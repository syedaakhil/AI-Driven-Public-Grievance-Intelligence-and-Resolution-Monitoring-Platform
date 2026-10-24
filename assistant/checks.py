"""Warnings shown by "manage.py check --deploy" for the assistant's email and ingestion settings."""
from django.conf import settings
from django.core import checks

from .brevo import ONLY_RECIPIENT


@checks.register(checks.Tags.security, deploy=True)
def assistant_settings(app_configs, **kwargs):
    problems = []
    if not (settings.BREVO_API_KEY and settings.SENDER_EMAIL):
        problems.append(checks.Warning(
            'BREVO_API_KEY or SENDER_EMAIL is empty, so the daily report and chat emails cannot be sent.',
            hint='Add both to .env (SENDER_EMAIL must be a sender verified in Brevo).', id='assistant.W001'))
    if (settings.ADMIN_EMAIL or '').strip().casefold() != ONLY_RECIPIENT:
        problems.append(checks.Error(
            f'ADMIN_EMAIL must be {ONLY_RECIPIENT}; the assistant refuses to email anyone else.',
            hint=f'Set ADMIN_EMAIL={ONLY_RECIPIENT} in .env (or remove the line).', id='assistant.E001'))
    if len(settings.INGEST_API_KEY or '') < 32:
        problems.append(checks.Warning(
            'INGEST_API_KEY is empty or shorter than 32 characters.',
            hint='python -c "import secrets; print(secrets.token_urlsafe(32))"', id='assistant.W002'))
    return problems
