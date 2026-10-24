import hmac
import json
import logging

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import DatabaseError
from django.http import JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import chat, ingest
from .ratelimit import allow

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 500
CHAT_PER_MINUTE = 30
INGEST_PER_HOUR = 120
BAD_KEYS_PER_HOUR = 20
MAX_INGEST_BYTES = 5 * 1024 * 1024


def _error(message, status, **extra):
    return JsonResponse({'error': message, **extra}, status=status)


def _client_ip(request):
    return request.META.get('REMOTE_ADDR', 'unknown')


# ---------- Chat (dashboard widget) ----------

@require_POST
def chat_message(request):
    user = request.user
    if not (user.is_authenticated and user.is_active and user.is_staff):
        return _error(_('Please sign in as a corporation admin to use the assistant.'), 401,
                      login_url=reverse('accounts:login'))
    if not allow(f'chat:{user.pk}', CHAT_PER_MINUTE, 60):
        return _error(_('Too many messages. Please wait a minute and try again.'), 429)
    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return _error(_('The message could not be read.'), 400)
    message = body.get('message') if isinstance(body, dict) else None
    if not isinstance(message, str):
        return _error(_('Type a message first.'), 400)
    message = ''.join(char for char in message if char.isprintable() or char in '\n\t').strip()
    if not message:
        return _error(_('Type a message first.'), 400)
    if len(message) > MAX_MESSAGE_LENGTH:
        return _error(_('Please keep the message under %(count)s characters.') % {'count': MAX_MESSAGE_LENGTH}, 400)
    try:
        return JsonResponse(chat.answer(request, message))
    except DatabaseError:
        logger.exception('Assistant: database error for message %r', message[:80])
        return _error(_('The database is not reachable right now. Please try again in a moment.'), 503)
    except Exception:  # noqa: BLE001  (never show a server error page in the chat)
        logger.exception('Assistant: unexpected error for message %r', message[:80])
        return _error(_('Something went wrong on our side. Please try again.'), 500)


# ---------- Ingestion (scraped data from another device) ----------

@csrf_exempt     # called by scripts with an API key, not by a browser session
@require_POST
def ingest_data(request):
    ip = _client_ip(request)
    expected = settings.INGEST_API_KEY
    if not expected:
        return _error('Ingestion is not set up on this server (INGEST_API_KEY is empty).', 503)
    supplied = request.headers.get('X-API-Key', '')
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        if not allow(f'ingest-bad-key:{ip}', BAD_KEYS_PER_HOUR, 3600):
            return _error('Too many requests with a wrong API key. Try again later.', 429)
        return _error('Missing or wrong API key. Send it in the X-API-Key header.', 401)
    if not allow(f'ingest:{ip}', INGEST_PER_HOUR, 3600):
        return _error(f'Too many uploads (more than {INGEST_PER_HOUR} an hour). Try again later.', 429)
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = 0
    if length > MAX_INGEST_BYTES:
        return _error('The upload is larger than 5 MB. Split it into smaller batches.', 413)

    content_type = request.content_type or ''
    try:
        if content_type == 'application/json':
            dataset, records = ingest.parse_json(request.body)
            dataset = dataset or request.GET.get('dataset')
        elif content_type in ('text/csv', 'application/csv'):
            dataset, records = request.GET.get('dataset'), ingest.parse_csv(request.body)
        elif content_type == 'multipart/form-data':
            upload = request.FILES.get('file')
            if upload is None:
                raise ingest.IngestError('Attach the data as a file field named "file".')
            if upload.size > MAX_INGEST_BYTES:
                return _error('The file is larger than 5 MB. Split it into smaller batches.', 413)
            raw = upload.read()
            if upload.name.lower().endswith('.json'):
                dataset, records = ingest.parse_json(raw)
                dataset = dataset or request.POST.get('dataset')
            elif upload.name.lower().endswith('.csv'):
                dataset, records = request.POST.get('dataset'), ingest.parse_csv(raw)
            else:
                raise ingest.IngestError('The file must end in .json or .csv.')
        else:
            return _error('Send Content-Type application/json, text/csv or multipart/form-data.', 415)
        summary = ingest.store(dataset, records)
    except RequestDataTooBig:
        return _error('The upload is larger than 5 MB. Split it into smaller batches.', 413)
    except ingest.IngestError as exc:
        return _error(str(exc), 400)
    except DatabaseError:
        logger.exception('Ingestion: database error; nothing was saved.')
        return _error('The database is not reachable right now; nothing was saved. Try again later.', 503)
    except Exception:  # noqa: BLE001
        logger.exception('Ingestion: unexpected error; nothing was saved.')
        return _error('Something went wrong on the server; nothing was saved.', 500)
    logger.info('Ingested into %s: %s', summary['dataset'], {k: v for k, v in summary.items() if k != 'errors'})
    return JsonResponse(summary, status=201 if summary['created'] else 200)
