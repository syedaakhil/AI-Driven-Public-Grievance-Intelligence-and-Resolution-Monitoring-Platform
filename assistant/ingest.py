"""
Checking, cleaning and storing data sent to POST /api/ingest/ (see INGEST_API.md).

A batch is a dataset name plus a list of records (flat key -> value objects). Bad records are
reported one by one and skipped; the good ones are stored in one transaction and are searchable by
the chatbot straight away. Re-sending the same record updates it instead of duplicating it.
"""
import csv
import hashlib
import io
import json
import re

from django.db import transaction

from .models import KnowledgeRecord
from .search import normalize

DATASET_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{1,49}$')
MAX_RECORDS = 5000
MAX_FIELDS = 50
MAX_KEY_LENGTH = 64
MAX_VALUE_LENGTH = 10_000
MAX_REPORTED_ERRORS = 50
ID_KEYS = ('id', 'external_id', 'record_id', 'url', 'link')
TITLE_KEYS = ('title', 'name', 'subject', 'headline', 'heading')


class IngestError(Exception):
    """The whole request is unusable. The message is returned to the sender."""


def parse_json(raw):
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise IngestError(f'The body is not valid JSON: {exc}.') from None
    if isinstance(body, dict):
        dataset, records = body.get('dataset'), body.get('records')
    else:
        raise IngestError('Send a JSON object: {"dataset": "...", "records": [ {...}, ... ]}.')
    if not isinstance(records, list):
        raise IngestError('"records" must be a list of objects.')
    return dataset, records


def parse_csv(raw):
    try:
        text = raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw
    except UnicodeDecodeError:
        raise IngestError('The CSV file must be UTF-8 encoded.') from None
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or not any(name and name.strip() for name in reader.fieldnames):
            raise IngestError('The CSV file needs a header row with column names.')
        rows = []
        for row in reader:
            if None in row:   # more cells than headers
                raise IngestError(f'CSV line {reader.line_num} has more values than the header has columns.')
            rows.append(row)
            if len(rows) > MAX_RECORDS:
                break
        return rows
    except csv.Error as exc:
        raise IngestError(f'The CSV file could not be read: {exc}.') from None


def check_dataset(dataset):
    dataset = (dataset or '').strip().lower() if isinstance(dataset, str) else ''
    if not DATASET_RE.match(dataset):
        raise IngestError('"dataset" is required: 2-50 lower-case letters, digits, "-" or "_", '
                          'starting with a letter or digit (e.g. "ward-news").')
    return dataset


def clean_record(record):
    """Return (cleaned dict, None) or (None, reason)."""
    if not isinstance(record, dict):
        return None, 'must be an object of field names and values'
    if len(record) > MAX_FIELDS:
        return None, f'has more than {MAX_FIELDS} fields'
    cleaned = {}
    for key, value in record.items():
        key = str(key).strip() if key is not None else ''
        if not key:
            continue
        if len(key) > MAX_KEY_LENGTH:
            return None, f'field name "{key[:20]}…" is longer than {MAX_KEY_LENGTH} characters'
        if isinstance(value, (dict, list)):
            return None, f'field "{key}" holds a nested object or list; send flat values only'
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            cleaned[key] = value
            continue
        value = ' '.join(str(value).split())   # trim and collapse whitespace
        if len(value) > MAX_VALUE_LENGTH:
            return None, f'field "{key}" is longer than {MAX_VALUE_LENGTH} characters'
        if value:
            cleaned[key] = value
    if not any(isinstance(value, str) for value in cleaned.values()):
        return None, 'has no text to search (at least one non-empty text field is needed)'
    return cleaned, None


def _pick(record, keys):
    lowered = {key.lower(): value for key, value in record.items()}
    for key in keys:
        if lowered.get(key) not in (None, ''):
            return str(lowered[key])
    return ''


def fingerprint(record):
    external_id = _pick(record, ID_KEYS)
    basis = 'id:' + external_id if external_id else 'data:' + json.dumps(
        {key.lower(): normalize(value) if isinstance(value, str) else value for key, value in record.items()},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(basis.encode('utf-8')).hexdigest()


def store(dataset, records):
    """Validate and save. Returns a summary dict; raises IngestError when nothing is usable."""
    dataset = check_dataset(dataset)
    if not records:
        raise IngestError('"records" is empty: send at least one record.')
    if len(records) > MAX_RECORDS:
        raise IngestError(f'Too many records in one request ({len(records)}); send at most {MAX_RECORDS}.')

    errors, prepared = [], {}
    duplicates_in_batch = 0
    for index, record in enumerate(records):
        cleaned, reason = clean_record(record)
        if reason:
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append({'record': index, 'error': reason})
            continue
        key = fingerprint(cleaned)
        if key in prepared:
            duplicates_in_batch += 1
        prepared[key] = cleaned          # the last copy in the batch wins
    rejected = len(records) - len(prepared) - duplicates_in_batch
    if not prepared:
        raise IngestError('No valid records: ' + '; '.join(
            f'record {item["record"]} {item["error"]}' for item in errors[:5]))

    created = updated = unchanged = 0
    with transaction.atomic():
        existing = {row.fingerprint: row for row in
                    KnowledgeRecord.objects.filter(dataset=dataset, fingerprint__in=list(prepared))}
        for key, record in prepared.items():
            title = _pick(record, TITLE_KEYS)[:200]
            content = ' · '.join(f'{name}: {value}' for name, value in record.items() if isinstance(value, str))
            values = {
                'external_id': _pick(record, ID_KEYS)[:100], 'title': title, 'content': content,
                'data': record, 'search_text': normalize(' '.join(str(value) for value in record.values())),
            }
            row = existing.get(key)
            if row is None:
                KnowledgeRecord.objects.create(dataset=dataset, fingerprint=key, **values)
                created += 1
            elif row.data != record:
                for name, value in values.items():
                    setattr(row, name, value)
                row.save()
                updated += 1
            else:
                unchanged += 1
    return {
        'dataset': dataset, 'received': len(records), 'created': created, 'updated': updated,
        'duplicates': duplicates_in_batch + unchanged, 'rejected': rejected, 'errors': errors,
    }
