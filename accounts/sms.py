"""
Sending text messages: OTPs (send_otp) and general messages such as status updates (send_message).

SMS_BACKEND (set in .env) chooses how:
  console   prints the message in the server console (testing only)
  fast2sms  needs FAST2SMS_API_KEY. OTPs use the OTP route (the SMS reads "Your OTP: 123456");
            other messages use the Quick SMS route.
  msg91     needs MSG91_AUTH_KEY. OTPs use the OTP API with MSG91_OTP_TEMPLATE_ID (template with ##OTP##);
            other messages use the Flow API with MSG91_SMS_TEMPLATE_ID, a DLT-approved template whose
            only variable is ##message##.
  twilio    Twilio Messages API; needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER

Phone numbers are passed as +91XXXXXXXXXX. Every failure is raised as SMSError.
"""
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

FAST2SMS_URL = 'https://www.fast2sms.com/dev/bulkV2'
MSG91_OTP_URL = 'https://control.msg91.com/api/v5/otp'
MSG91_FLOW_URL = 'https://control.msg91.com/api/v5/flow'
TIMEOUT_SECONDS = 10


class SMSError(Exception):
    """The message could not be sent."""


def uses_console():
    return settings.SMS_BACKEND == 'console'


def otp_message(code, minutes):
    return (f'{code} is your City Corporation verification code. '
            f'It expires in {minutes} minutes. Do not share it with anyone.')


def send_otp(phone, code, minutes):
    backend = settings.SMS_BACKEND
    if backend == 'fast2sms':
        _fast2sms(phone, code)
    elif backend == 'msg91':
        _msg91(phone, code, minutes)
    else:
        send_message(phone, otp_message(code, minutes))


def send_message(phone, message):
    """Send any text (not an OTP) to +91XXXXXXXXXX through the configured backend."""
    backend = settings.SMS_BACKEND
    if backend == 'console':
        print(f'[SMS to {phone}] {message}', flush=True)
    elif backend == 'fast2sms':
        _fast2sms_message(phone, message)
    elif backend == 'msg91':
        _msg91_message(phone, message)
    elif backend == 'twilio':
        _twilio(phone, message)
    else:
        raise SMSError(f'Unknown SMS_BACKEND "{backend}".')


def _post(url, body, headers):
    """POST and return the decoded JSON reply. Any failure becomes SMSError."""
    request = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            text = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        logger.error('SMS provider answered HTTP %s: %s', exc.code, exc.read()[:300])
        raise SMSError('The SMS provider rejected the request.') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.error('Could not reach the SMS provider: %s', exc)
        raise SMSError('Could not reach the SMS provider.') from exc
    try:
        return json.loads(text)
    except ValueError:
        return {'raw': text}


def _fast2sms(phone, code):
    if not settings.FAST2SMS_API_KEY:
        raise SMSError('FAST2SMS_API_KEY is not set.')
    reply = _post(
        FAST2SMS_URL,
        json.dumps({'route': 'otp', 'variables_values': code, 'numbers': phone[-10:]}).encode(),
        {
            'authorization': settings.FAST2SMS_API_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )
    if reply.get('return') is not True:
        logger.error('Fast2SMS did not send the OTP: %s', reply)
        raise SMSError('Fast2SMS did not send the OTP.')


def _fast2sms_message(phone, message):
    if not settings.FAST2SMS_API_KEY:
        raise SMSError('FAST2SMS_API_KEY is not set.')
    reply = _post(
        FAST2SMS_URL,
        json.dumps({'route': 'q', 'message': message, 'language': 'english', 'flash': 0,
                    'numbers': phone[-10:]}).encode(),
        {
            'authorization': settings.FAST2SMS_API_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )
    if reply.get('return') is not True:
        logger.error('Fast2SMS did not send the message: %s', reply)
        raise SMSError('Fast2SMS did not send the message.')


def _msg91(phone, code, minutes):
    if not (settings.MSG91_AUTH_KEY and settings.MSG91_OTP_TEMPLATE_ID):
        raise SMSError('MSG91_AUTH_KEY and MSG91_OTP_TEMPLATE_ID must both be set.')
    query = urllib.parse.urlencode({
        'template_id': settings.MSG91_OTP_TEMPLATE_ID,
        'mobile': phone.lstrip('+'),  # 91XXXXXXXXXX
        'otp': code,                  # our own code; we also verify it ourselves
        'otp_expiry': minutes,
    })
    reply = _post(f'{MSG91_OTP_URL}?{query}', b'{}', {
        'authkey': settings.MSG91_AUTH_KEY,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    })
    if reply.get('type') != 'success':
        logger.error('MSG91 did not send the OTP: %s', reply)
        raise SMSError('MSG91 did not send the OTP.')


def _msg91_message(phone, message):
    if not (settings.MSG91_AUTH_KEY and settings.MSG91_SMS_TEMPLATE_ID):
        raise SMSError('MSG91_AUTH_KEY and MSG91_SMS_TEMPLATE_ID must both be set.')
    reply = _post(
        MSG91_FLOW_URL,
        json.dumps({
            'template_id': settings.MSG91_SMS_TEMPLATE_ID,
            'short_url': '0',
            'recipients': [{'mobiles': phone.lstrip('+'), 'message': message}],
        }).encode(),
        {
            'authkey': settings.MSG91_AUTH_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )
    if reply.get('type') != 'success':
        logger.error('MSG91 did not send the message: %s', reply)
        raise SMSError('MSG91 did not send the message.')


def _twilio(phone, message):
    sid, token, sender = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_FROM_NUMBER
    if not (sid and token and sender):
        raise SMSError('Twilio settings are incomplete.')
    credentials = base64.b64encode(f'{sid}:{token}'.encode()).decode()
    reply = _post(
        f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json',
        urllib.parse.urlencode({'To': phone, 'From': sender, 'Body': message}).encode(),
        {'Authorization': f'Basic {credentials}'},
    )
    # A queued message has a SID; "failed"/"undelivered" (or no SID) means it won't arrive.
    if not reply.get('sid') or reply.get('status') in ('failed', 'undelivered'):
        logger.error('Twilio did not accept the message: %s', reply)
        raise SMSError('Twilio did not send the message.')
