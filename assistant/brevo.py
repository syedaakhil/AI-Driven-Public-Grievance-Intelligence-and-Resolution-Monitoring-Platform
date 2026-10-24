"""
Sending email through Brevo's transactional API (https://api.brevo.com/v3/smtp/email).

Every email goes to ONE address only: ONLY_RECIPIENT below. It is fixed in the code on purpose:
send_email() has no "to" parameter, a chat message can't change it, and .env can't redirect it
(ADMIN_EMAIL must equal it, or nothing is sent). Every attempt is stored in EmailLog.
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from .models import EmailLog

logger = logging.getLogger(__name__)

BREVO_URL = 'https://api.brevo.com/v3/smtp/email'
ONLY_RECIPIENT = 'govermentowncorporation@gmail.com'
TIMEOUT_SECONDS = 15


class EmailError(Exception):
    """The email was not sent. The message is safe to show to the admin."""


def recipient():
    """The one allowed address, after checking that ADMIN_EMAIL in .env agrees with it."""
    configured = (settings.ADMIN_EMAIL or '').strip().casefold()
    if configured != ONLY_RECIPIENT:
        raise EmailError(f'ADMIN_EMAIL in .env must be {ONLY_RECIPIENT}; emails are only sent to that address.')
    return ONLY_RECIPIENT


def build_payload(subject, html, text):
    if not settings.BREVO_API_KEY:
        raise EmailError('Email is not set up yet: add BREVO_API_KEY to .env.')
    if not settings.SENDER_EMAIL:
        raise EmailError('Email is not set up yet: add SENDER_EMAIL (a sender verified in Brevo) to .env.')
    return {
        'sender': {'name': settings.SENDER_NAME, 'email': settings.SENDER_EMAIL},
        'to': [{'email': recipient()}],
        'subject': subject,
        'htmlContent': html,
        'textContent': text,
    }


def check_only_recipient(payload):
    """Last guard before anything leaves the server: exactly one recipient, the allowed one."""
    addresses = [entry.get('email', '').casefold() for entry in payload.get('to', [])]
    if addresses != [ONLY_RECIPIENT] or any(key in payload for key in ('cc', 'bcc', 'replyTo', 'messageVersions')):
        raise EmailError('Refused: emails may only be sent to ' + ONLY_RECIPIENT + '.')


def _post(payload):
    request = urllib.request.Request(
        BREVO_URL, data=json.dumps(payload).encode(), method='POST',
        headers={'api-key': settings.BREVO_API_KEY, 'Content-Type': 'application/json',
                 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode('utf-8', 'replace')
        logger.error('Brevo answered HTTP %s: %s', exc.code, detail)
        if exc.code == 401:
            raise EmailError('Brevo refused the API key (HTTP 401). Check BREVO_API_KEY in .env.') from exc
        if exc.code == 400:
            raise EmailError('Brevo rejected the email (HTTP 400). Is SENDER_EMAIL verified in Brevo?') from exc
        raise EmailError(f'Brevo could not send the email (HTTP {exc.code}). Try again later.') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.error('Could not reach Brevo: %s', exc)
        raise EmailError('Could not reach Brevo. Check the internet connection and try again.') from exc
    try:
        return json.loads(body or '{}')
    except ValueError:
        return {}


def send_email(subject, html, text, kind, record_count=0):
    """Send to the one allowed address. Returns the EmailLog; raises EmailError when not sent."""
    log = EmailLog(kind=kind, recipient=ONLY_RECIPIENT, subject=subject[:200], record_count=record_count)
    try:
        payload = build_payload(subject, html, text)
        check_only_recipient(payload)
        reply = _post(payload)
    except EmailError as exc:
        log.status, log.error = EmailLog.Status.FAILED, str(exc)
        log.save()
        logger.error('Email "%s" not sent: %s', subject, exc)
        raise
    log.status = EmailLog.Status.SENT
    log.provider_message_id = str(reply.get('messageId', ''))[:200]
    log.save()
    logger.info('Email "%s" sent (%s records).', subject, record_count)
    return log
