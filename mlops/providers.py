"""
Where "Collect data" gets its records. Each provider is one dataset card on the Agent Work page.

A provider yields batches of CollectedRecord(source_id, text, label). To add a real source, write a
class with a slug, name, description, count() and batches(), and decorate it with @register (or add
it in providers()). Nothing here invents data: the built-in providers read this portal's own
complaints and the records sent to POST /api/ingest/.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

BATCH_SIZE = 200
MAX_TEXT_LENGTH = 2000
HTTP_TIMEOUT_SECONDS = 20
HTTP_MAX_BYTES = 5 * 1024 * 1024
HTTP_MAX_RECORDS = 5000

EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')
# 10+ digits, allowing +, spaces and dashes between them (Indian mobiles, landlines with STD codes).
PHONE_RE = re.compile(r'\+?\d(?:[\s-]?\d){9,}')


class ProviderError(Exception):
    """A message that is safe to show staff. temporary=True means trying again may work."""

    def __init__(self, message, temporary=False):
        super().__init__(message)
        self.temporary = temporary


@dataclass(frozen=True)
class CollectedRecord:
    source_id: str
    text: str
    label: str = ''


def clean_text(*parts):
    """Join the parts, hide email addresses and phone numbers, and squash whitespace."""
    text = ' '.join(str(part) for part in parts if part)
    text = EMAIL_RE.sub('[email]', text)
    text = PHONE_RE.sub('[phone]', text)
    return ' '.join(text.split())[:MAX_TEXT_LENGTH]


class Provider:
    slug = ''
    name = ''
    description = ''
    source_label = ''
    label_name = _('Label')
    icon = 'table'

    def count(self):
        """How many records batches() will yield, or None if the source can't tell in advance."""
        return None

    def batches(self):
        raise NotImplementedError


_REGISTRY = {}


def register(cls):
    _REGISTRY[cls.slug] = cls
    return cls


@register
class ComplaintCategoryProvider(Provider):
    """Complaint title + description, labelled with the complaint's service category."""
    slug = 'complaints'
    name = _('Complaint texts')
    description = _('Title and description of every complaint that has a service category.')
    source_label = _('Complaints in this portal')
    label_name = _('Service category')
    icon = 'file'

    def queryset(self):
        from grievances.models import Complaint
        return Complaint.objects.filter(category__isnull=False).select_related('category').order_by('pk')

    def count(self):
        return self.queryset().count()

    def batches(self):
        last_pk = 0
        while True:
            # Keyset paging, so complaints filed during the job can't shift a page.
            rows = list(self.queryset().filter(pk__gt=last_pk)[:BATCH_SIZE])
            if not rows:
                return
            last_pk = rows[-1].pk
            yield [CollectedRecord(complaint.reference, clean_text(complaint.title, complaint.description),
                                   complaint.category.slug) for complaint in rows]


@register
class IngestedRecordsProvider(Provider):
    """Records received through POST /api/ingest/, labelled by their "category" or "label" field if any."""
    slug = 'ingested'
    name = _('Ingested records')
    description = _('Records sent to the data-ingestion API, with their category when they have one.')
    source_label = _('Data-ingestion API')
    label_name = _('Category')
    icon = 'upload'

    def queryset(self):
        from assistant.models import KnowledgeRecord
        return KnowledgeRecord.objects.order_by('pk')

    def count(self):
        return self.queryset().count()

    def batches(self):
        last_pk = 0
        while True:
            rows = list(self.queryset().filter(pk__gt=last_pk)[:BATCH_SIZE])
            if not rows:
                return
            last_pk = rows[-1].pk
            yield [CollectedRecord(f'{record.dataset}:{record.pk}', clean_text(record.title, record.content),
                                   _label_from(record.data)) for record in rows]


def _label_from(data):
    if not isinstance(data, dict):
        return ''
    for key in ('category', 'label', 'Category', 'Label'):
        value = data.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()[:100]
    return ''


class HttpJsonProvider(Provider):
    """
    An outside source set in .env: ML_HTTP_SOURCE_URL answers GET with a JSON list (or {"records": [...]})
    of {"id", "text", "label"} objects. ML_HTTP_SOURCE_TOKEN, if set, is sent as a Bearer token.
    """
    slug = 'external'
    description = _('Records fetched from the outside source set in ML_HTTP_SOURCE_URL.')
    source_label = _('Outside source (HTTPS)')
    icon = 'globe'

    @property
    def name(self):
        return settings.ML_HTTP_SOURCE_NAME or 'External source'

    def batches(self):
        records = self._fetch()
        for start in range(0, len(records), BATCH_SIZE):
            yield records[start:start + BATCH_SIZE]

    def _fetch(self):
        url = settings.ML_HTTP_SOURCE_URL
        if not url.lower().startswith(('https://', 'http://')):
            raise ProviderError(_('ML_HTTP_SOURCE_URL must start with https://.'))
        headers = {'Accept': 'application/json', 'User-Agent': 'CityCorporation-AgentWork/1.0'}
        if settings.ML_HTTP_SOURCE_TOKEN:
            headers['Authorization'] = f'Bearer {settings.ML_HTTP_SOURCE_TOKEN}'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read(HTTP_MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # 5xx and 429 may pass; 4xx (wrong token, wrong URL) won't fix themselves.
            raise ProviderError(_('The outside source answered with HTTP %(code)s.') % {'code': exc.code},
                                temporary=exc.code >= 500 or exc.code == 429) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(_('The outside source could not be reached.'), temporary=True) from exc
        if len(body) > HTTP_MAX_BYTES:
            raise ProviderError(_('The outside source sent more than 5 MB.'))
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(_('The outside source did not send valid JSON.')) from exc
        items = payload.get('records') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ProviderError(_('The outside source must send a JSON list of records.'))
        if len(items) > HTTP_MAX_RECORDS:
            raise ProviderError(_('The outside source sent more than %(count)s records.') % {'count': HTTP_MAX_RECORDS})
        records = []
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = clean_text(item.get('text') or item.get('title') or '')
            if not text:
                continue
            source_id = str(item.get('id') or position)[:100]
            records.append(CollectedRecord(source_id, text, _label_from(item)))
        return records


def providers():
    """Every dataset that can be collected, in the order the Agent Work page shows them."""
    found = [cls() for cls in _REGISTRY.values()]
    if settings.ML_HTTP_SOURCE_URL:
        found.append(HttpJsonProvider())
    return found


def get_provider(slug):
    """The provider for `slug`, or None."""
    return next((provider for provider in providers() if provider.slug == slug), None)
