"""Create the corporation admin account.

Username and password come from DJANGO_ADMIN_USERNAME and DJANGO_ADMIN_PASSWORD, defaulting to
admin / 1234 for the college demo. Change the password before deploying anywhere public.
"""
import os

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations


def create_admin(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    username = os.environ.get('DJANGO_ADMIN_USERNAME', 'admin')
    if User.objects.filter(username=username).exists():
        return
    User.objects.create(
        username=username,
        password=make_password(os.environ.get('DJANGO_ADMIN_PASSWORD', '1234')),
        first_name='Corporation',
        last_name='Admin',
        is_staff=True,
        is_superuser=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(create_admin, migrations.RunPython.noop),
    ]
