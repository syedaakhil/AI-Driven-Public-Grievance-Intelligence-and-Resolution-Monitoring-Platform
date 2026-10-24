"""Warnings shown by "manage.py check --deploy" before the portal goes live."""
from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=True)
def citizen_sign_in_settings(app_configs, **kwargs):
    errors = []
    if settings.SMS_BACKEND == 'console':
        errors.append(checks.Warning(
            'SMS_BACKEND is "console", so OTPs are only printed in the server log and citizens cannot sign in.',
            hint='Set SMS_BACKEND to fast2sms, msg91 or twilio and add its keys to .env.',
            id='accounts.W001',
        ))
    if settings.CACHES['default']['BACKEND'].endswith('LocMemCache'):
        errors.append(checks.Warning(
            'The cache is per-process memory, so OTP and admin-login rate limits are not shared between '
            'gunicorn workers and reset on every restart.',
            hint='Set REDIS_URL in .env (for example redis://localhost:6379/0).',
            id='accounts.W002',
        ))
    if settings.SECRET_KEY == settings.DEV_SECRET_KEY:
        errors.append(checks.Error(
            'SECRET_KEY is the development key that is published with the code.',
            hint='Set DJANGO_SECRET_KEY in .env to a long random value.',
            id='accounts.E001',
        ))
    return errors
