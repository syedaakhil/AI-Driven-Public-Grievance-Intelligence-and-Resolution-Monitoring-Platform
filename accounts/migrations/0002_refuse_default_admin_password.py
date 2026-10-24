"""Refuse the demo admin password on a live site.

0001_corporation_admin creates the admin with DJANGO_ADMIN_PASSWORD, defaulting to "1234" for the
local demo. Existing migrations are never edited, so this one runs straight after it. On a live
site (DJANGO_DEBUG off) an admin still on "1234":
  - gets DJANGO_ADMIN_PASSWORD instead, if that is set and passes AUTH_PASSWORD_VALIDATORS;
  - otherwise "migrate" stops here with instructions. Set the variable and run migrate again.
With DJANGO_DEBUG=1 (your own computer) nothing changes.
"""
import os

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import migrations

DEMO_PASSWORD = '1234'


def refuse_default_admin_password(apps, schema_editor):
    if not settings.PRODUCTION:
        return
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    username = os.environ.get('DJANGO_ADMIN_USERNAME', 'admin')
    admin = User.objects.filter(username=username).first()
    if admin is None or not check_password(DEMO_PASSWORD, admin.password):
        return

    password = os.environ.get('DJANGO_ADMIN_PASSWORD', '')
    try:
        validate_password(password)
    except ValidationError as error:
        raise ImproperlyConfigured(
            f'The "{username}" account still has the demo password "{DEMO_PASSWORD}", which is not allowed '
            'with DJANGO_DEBUG off. Set DJANGO_ADMIN_PASSWORD to a strong password (at least 8 characters, '
            'not common, not only digits) and run "manage.py migrate" again. Problem: '
            + ' '.join(error.messages)
        ) from None
    admin.password = make_password(password)
    admin.save(update_fields=['password'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_corporation_admin'),
    ]

    operations = [
        migrations.RunPython(refuse_default_admin_password, migrations.RunPython.noop),
    ]
