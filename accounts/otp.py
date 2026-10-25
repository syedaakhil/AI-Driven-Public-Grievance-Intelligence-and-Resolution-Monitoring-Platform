"""
One-time passwords for citizen sign-in.

The session keeps only an HMAC of (phone, code), never the code itself, plus its expiry and
the number of wrong tries. Sends per phone number are rate-limited through the cache.
"""
import hashlib
import hmac
import re
import secrets
import time

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _, ngettext

from . import sms

SESSION_KEY = 'pending_otp'
DEV_CODE_KEY = 'dev_otp_code'
LENGTH = 6
LIFETIME_SECONDS = 5 * 60
MAX_TRIES = 5
RESEND_WAIT_SECONDS = 30
MAX_SENDS_PER_HOUR = 5


class OTPError(Exception):
    """A message to show the citizen."""


def normalize_phone(raw):
    """Return +91XXXXXXXXXX for a valid Indian mobile number, otherwise None."""
    digits = re.sub(r'[\s-]', '', raw or '')
    match = re.fullmatch(r'(?:\+91|91|0)?([6-9]\d{9})', digits)
    return f'+91{match.group(1)}' if match else None


def mask(phone):
    return f'+91 {phone[3:5]}XXXXXX{phone[-2:]}'


def _digest(phone, code):
    return hmac.new(settings.SECRET_KEY.encode(), f'{phone}:{code}'.encode(), hashlib.sha256).hexdigest()


def pending(request):
    """The OTP waiting for verification in this session, if it hasn't expired."""
    data = request.session.get(SESSION_KEY)
    return data if data and data['expires'] > time.time() else None


def send(request, phone):
    """Create a code, text it to the phone and remember it in the session."""
    now = time.time()
    current = request.session.get(SESSION_KEY)
    if current and current['phone'] == phone:
        wait = int(RESEND_WAIT_SECONDS - (now - current['sent_at']))
        if wait > 0:
            raise OTPError(_('Please wait %(seconds)s seconds before asking for another OTP.') % {'seconds': wait})

    counter = f'otp-sends:{phone}'
    if cache.get(counter, 0) >= MAX_SENDS_PER_HOUR:
        raise OTPError(_('Too many OTP requests for this number. Please try again in an hour.'))

    code = f'{secrets.randbelow(10 ** LENGTH):0{LENGTH}d}'
    try:
        sms.send_otp(phone, code, LIFETIME_SECONDS // 60)
    except sms.SMSError as exc:
        raise OTPError(_('We could not send the SMS right now. Please try again later.')) from exc

    if not cache.add(counter, 1, 60 * 60):
        cache.incr(counter)
    request.session[SESSION_KEY] = {
        'phone': phone,
        'digest': _digest(phone, code),
        'expires': now + LIFETIME_SECONDS,
        'sent_at': now,
        'tries': 0,
    }
    if shows_code_on_screen():
        request.session[DEV_CODE_KEY] = code


def shows_code_on_screen():
    """Without a real SMS provider, the login page shows the OTP while testing (debug mode) or on a
    demo site with CITIZEN_OTP_ON_SCREEN turned on."""
    return sms.uses_console() and (settings.DEBUG or settings.CITIZEN_OTP_ON_SCREEN)


def verify(request, code):
    """Return the verified phone number, or raise OTPError."""
    data = request.session.get(SESSION_KEY)
    if not data:
        raise OTPError(_('Please request an OTP first.'))
    if data['expires'] <= time.time():
        clear(request)
        raise OTPError(_('This OTP has expired. Please request a new one.'))

    code = (code or '').strip()
    if not re.fullmatch(rf'\d{{{LENGTH}}}', code):
        raise OTPError(_('Enter the %(length)s-digit OTP.') % {'length': LENGTH})

    if not hmac.compare_digest(data['digest'], _digest(data['phone'], code)):
        data['tries'] += 1
        if data['tries'] >= MAX_TRIES:
            clear(request)
            raise OTPError(_('Too many incorrect attempts. Please request a new OTP.'))
        request.session[SESSION_KEY] = data
        left = MAX_TRIES - data['tries']
        raise OTPError(ngettext('Incorrect OTP. %(left)s attempt left.', 'Incorrect OTP. %(left)s attempts left.',
                                 left) % {'left': left})

    clear(request)
    return data['phone']


def clear(request):
    request.session.pop(SESSION_KEY, None)
    request.session.pop(DEV_CODE_KEY, None)
