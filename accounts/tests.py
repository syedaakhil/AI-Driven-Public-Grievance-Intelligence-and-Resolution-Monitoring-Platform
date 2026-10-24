import importlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from contextlib import redirect_stdout
from datetime import timedelta
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from citycorp import settings as project_settings

from . import checks, otp, sms


@override_settings(DEBUG=True)  # the local demo, where admin/1234 still works
class AdminLoginTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_admin_account_is_created_by_migration(self):
        user = get_user_model().objects.get(username='admin')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password('1234'))

    def test_dashboard_redirects_to_login(self):
        response = self.client.get(reverse('grievances:home'))
        self.assertRedirects(response, reverse('accounts:login') + '?next=/')

    def test_wrong_password_shows_error(self):
        response = self.client.post(reverse('accounts:admin_login'),
                                    {'username': 'admin', 'password': 'wrong'}, follow=True)
        self.assertContains(response, 'Invalid username or password.')

    def test_admin_signs_in_and_follows_next(self):
        response = self.client.post(reverse('accounts:admin_login'),
                                    {'username': 'admin', 'password': '1234', 'next': '/officers/'})
        self.assertRedirects(response, '/officers/')

    def test_unsafe_next_is_ignored(self):
        response = self.client.post(reverse('accounts:admin_login'),
                                    {'username': 'admin', 'password': '1234', 'next': 'https://evil.example/'})
        self.assertRedirects(response, reverse('grievances:home'))

    def test_lockout_after_repeated_failures(self):
        for _ in range(10):
            self.client.post(reverse('accounts:admin_login'), {'username': 'admin', 'password': 'x'})
        response = self.client.post(reverse('accounts:admin_login'),
                                    {'username': 'admin', 'password': '1234'}, follow=True)
        self.assertContains(response, 'Too many failed attempts')

    def test_logout_requires_post(self):
        self.client.login(username='admin', password='1234')
        self.assertEqual(self.client.get(reverse('accounts:logout')).status_code, 405)
        self.client.post(reverse('accounts:logout'))
        self.assertEqual(self.client.get(reverse('grievances:home')).status_code, 302)


STRONG_PASSWORD = 'Ward-Office-Lamp-2026'


@override_settings(DEBUG=False)
class LiveSiteLoginTests(TestCase):
    """With DEBUG off, the demo admin/1234 account must not open the dashboard or Django admin."""

    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.get(username='admin')

    def test_demo_password_is_refused_with_a_clear_message(self):
        response = self.client.post(reverse('accounts:admin_login'),
                                    {'username': 'admin', 'password': '1234'}, follow=True)
        self.assertContains(response, 'still has a weak password')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get(reverse('grievances:home')).status_code, 302)

    def test_demo_password_is_refused_on_django_admin(self):
        self.assertFalse(self.client.login(username='admin', password='1234'))
        response = self.client.post(reverse('admin:login'), {'username': 'admin', 'password': '1234'})
        self.assertEqual(response.status_code, 200)  # the form again, not a redirect into admin
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_wrong_password_still_says_invalid(self):
        response = self.client.post(reverse('accounts:admin_login'),
                                    {'username': 'admin', 'password': 'Wrong-Password-99'}, follow=True)
        self.assertContains(response, 'Invalid username or password.')

    def test_strong_password_signs_in(self):
        self.admin.set_password(STRONG_PASSWORD)
        self.admin.save()
        response = self.client.post(reverse('accounts:admin_login'),
                                    {'username': 'admin', 'password': STRONG_PASSWORD})
        self.assertRedirects(response, reverse('grievances:home'))

    @override_settings(SMS_BACKEND='console')
    def test_otp_is_not_shown_on_the_login_page(self):
        self.client.post(reverse('accounts:send_otp'), {'phone': '9876543210'})
        self.assertNotIn(otp.DEV_CODE_KEY, self.client.session)
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.context['dev_code'], '')


class SettingsTests(SimpleTestCase):
    def test_secret_key_is_required_outside_debug(self):
        with mock.patch.dict(os.environ, {'DJANGO_SECRET_KEY': ''}):
            with self.assertRaisesMessage(ImproperlyConfigured, 'DJANGO_SECRET_KEY is not set'):
                project_settings.secret_key(debug=False)
            self.assertEqual(project_settings.secret_key(debug=True), project_settings.DEV_SECRET_KEY)
        with mock.patch.dict(os.environ, {'DJANGO_SECRET_KEY': 'a-real-key'}):
            self.assertEqual(project_settings.secret_key(debug=False), 'a-real-key')

    def test_debug_is_off_unless_turned_on(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('DJANGO_DEBUG', None)
            self.assertFalse(project_settings.env_flag('DJANGO_DEBUG', False))

    @override_settings(SMS_BACKEND='console', SECRET_KEY=project_settings.DEV_SECRET_KEY,
                       CACHES={'default': project_settings.cache_settings('')})
    def test_deploy_check_flags_local_settings(self):
        ids = {message.id for message in checks.citizen_sign_in_settings(None)}
        self.assertEqual(ids, {'accounts.W001', 'accounts.W002', 'accounts.E001'})

    @override_settings(SMS_BACKEND='msg91', SECRET_KEY='a-real-key',
                       CACHES={'default': project_settings.cache_settings('redis://localhost:6379/0')})
    def test_deploy_check_passes_with_live_settings(self):
        self.assertEqual(checks.citizen_sign_in_settings(None), [])

    @override_settings(SMS_BACKEND='msg91', SECRET_KEY='a-real-key',
                       CACHES={'default': project_settings.cache_settings('')})
    def test_deploy_check_flags_a_per_process_cache(self):
        self.assertEqual([m.id for m in checks.citizen_sign_in_settings(None)], ['accounts.W002'])

    def test_database_defaults_to_sqlite(self):
        db = project_settings.database_settings('')
        self.assertEqual(db['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(db['NAME'], project_settings.BASE_DIR / 'db.sqlite3')

    def test_database_url_selects_postgresql(self):
        db = project_settings.database_settings('postgres://citycorp:s3cret@db.internal:5432/citycorp?sslmode=require')
        self.assertEqual(db['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual((db['NAME'], db['USER'], db['PASSWORD'], db['HOST'], db['PORT']),
                         ('citycorp', 'citycorp', 's3cret', 'db.internal', 5432))
        self.assertEqual(db['OPTIONS'], {'sslmode': 'require'})
        self.assertEqual(db['CONN_MAX_AGE'], 600)

    def test_mysql_url_gets_utf8mb4_and_strict_mode(self):
        db = project_settings.database_settings('mysql://root:s3cret@127.0.0.1:3306/citycorp_db')
        self.assertEqual(db['ENGINE'], 'django.db.backends.mysql')
        self.assertEqual((db['NAME'], db['USER'], db['PASSWORD'], db['HOST'], db['PORT']),
                         ('citycorp_db', 'root', 's3cret', '127.0.0.1', 3306))
        self.assertEqual(db['OPTIONS'], {'charset': 'utf8mb4', 'init_command': "SET sql_mode='STRICT_TRANS_TABLES'"})

    def test_mysql_url_options_are_kept(self):
        db = project_settings.database_settings('mysql://u:p@db.internal:3306/citycorp_db?ssl_mode=REQUIRED')
        self.assertEqual(db['OPTIONS']['ssl_mode'], 'REQUIRED')
        self.assertEqual(db['OPTIONS']['charset'], 'utf8mb4')

    def test_pymysql_url_switches_driver(self):
        with mock.patch.object(project_settings, 'use_pymysql') as use_pymysql:
            db = project_settings.database_settings('mysql+pymysql://root:s3cret@127.0.0.1:3306/citycorp_db')
        use_pymysql.assert_called_once_with()
        self.assertEqual(db['ENGINE'], 'django.db.backends.mysql')
        self.assertEqual((db['NAME'], db['HOST'], db['PORT']), ('citycorp_db', '127.0.0.1', 3306))
        self.assertEqual(db['OPTIONS']['charset'], 'utf8mb4')

    def test_postgres_gets_no_mysql_options(self):
        with mock.patch.object(project_settings, 'use_pymysql') as use_pymysql:
            db = project_settings.database_settings('postgres://u:p@localhost:5432/citycorp')
        use_pymysql.assert_not_called()
        self.assertNotIn('charset', db.get('OPTIONS', {}))

    def test_redis_url_selects_redis_cache(self):
        self.assertTrue(project_settings.cache_settings('')['BACKEND'].endswith('LocMemCache'))
        cache_config = project_settings.cache_settings('redis://localhost:6379/1')
        self.assertEqual(cache_config['BACKEND'], 'django.core.cache.backends.redis.RedisCache')
        self.assertEqual(cache_config['LOCATION'], 'redis://localhost:6379/1')


# Every variable a live site sets (throwaway values; nothing connects to them).
LIVE_ENV = {
    'DJANGO_DEBUG': '0',
    'DJANGO_SECRET_KEY': 'test-only-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJKLMNOP',
    'DJANGO_ALLOWED_HOSTS': 'grievance.example.gov.in',
    'DATABASE_URL': 'postgres://citycorp:pw@localhost:5432/citycorp',
    'REDIS_URL': 'redis://localhost:6379/0',
    'SMS_BACKEND': 'fast2sms',
    'FAST2SMS_API_KEY': 'dummy',
    'BREVO_API_KEY': 'dummy-brevo-key',
    'SENDER_EMAIL': 'sender@example.com',
    'ADMIN_EMAIL': 'govermentowncorporation@gmail.com',
    'INGEST_API_KEY': 'test-only-ingest-key-0123456789-abcdefghij',
}
SETTINGS_KEYS = ['DEBUG', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE', 'SECURE_SSL_REDIRECT',
                 'SECURE_HSTS_SECONDS', 'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'SECURE_HSTS_PRELOAD']


class ProductionSettingsTests(SimpleTestCase):
    """Loads the settings in a fresh Python process with live-site environment variables."""

    def run_django(self, env, *args):
        full_env = {key: value for key, value in os.environ.items() if key not in LIVE_ENV}
        full_env.update(env, DJANGO_SETTINGS_MODULE='citycorp.settings', PYTHONIOENCODING='utf-8')
        return subprocess.run([sys.executable, *args], env=full_env, cwd=project_settings.BASE_DIR,
                              capture_output=True, text=True, timeout=120)

    def load_settings(self, env):
        script = ('import json, django; django.setup(); from django.conf import settings as s; '
                  f'print(json.dumps({{k: getattr(s, k) for k in {SETTINGS_KEYS!r}}}))')
        result = self.run_django(env, '-c', script)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_debug_off_turns_on_https_protection(self):
        values = self.load_settings(LIVE_ENV)
        self.assertEqual(values, {
            'DEBUG': False, 'SESSION_COOKIE_SECURE': True, 'CSRF_COOKIE_SECURE': True,
            'SECURE_SSL_REDIRECT': True, 'SECURE_HSTS_SECONDS': 31536000,
            'SECURE_HSTS_INCLUDE_SUBDOMAINS': True, 'SECURE_HSTS_PRELOAD': True,
        })

    def test_debug_on_leaves_https_off(self):
        values = self.load_settings({'DJANGO_DEBUG': '1'})
        self.assertFalse(any(values[key] for key in SETTINGS_KEYS[1:]))

    def test_django_loads_its_mysql_backend_with_either_driver(self):
        # Importing the backend runs Django's "mysqlclient 2.2.1 or newer" check; no server is needed.
        script = ('import django; django.setup(); from django.db import connections; '
                  'c = connections["default"]; print(c.vendor, c.Database.__name__, c.Database.version_info[:3])')
        for url, driver in (('mysql://root:pw@127.0.0.1:3306/citycorp_db', 'MySQLdb'),
                            ('mysql+pymysql://root:pw@127.0.0.1:3306/citycorp_db', 'pymysql')):
            with self.subTest(url=url.split(':')[0]):
                result = self.run_django({'DJANGO_DEBUG': '1', 'DATABASE_URL': url}, '-c', script)
                self.assertEqual(result.returncode, 0, result.stderr)
                vendor, module, version = result.stdout.strip().splitlines()[-1].split(' ', 2)
                self.assertEqual((vendor, module), ('mysql', driver))
                self.assertGreaterEqual(eval(version), (2, 2, 1))

    def test_check_deploy_has_no_warnings_with_live_settings(self):
        result = self.run_django(LIVE_ENV, 'manage.py', 'check', '--deploy', '--fail-level', 'WARNING')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no issues', result.stdout)


class RefuseDefaultAdminPasswordTests(TestCase):
    """accounts/migrations/0002: on a live site the demo admin password 1234 is replaced or refused."""

    migration = importlib.import_module('accounts.migrations.0002_refuse_default_admin_password')

    def run_migration(self, **env):
        with mock.patch.dict(os.environ, env):
            if 'DJANGO_ADMIN_PASSWORD' not in env:
                os.environ.pop('DJANGO_ADMIN_PASSWORD', None)
            self.migration.refuse_default_admin_password(django_apps, None)

    def admin(self):
        return get_user_model().objects.get(username='admin')

    @override_settings(PRODUCTION=True)
    def test_live_site_refuses_to_keep_1234(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'Set DJANGO_ADMIN_PASSWORD'):
            self.run_migration()
        with self.assertRaisesMessage(ImproperlyConfigured, 'demo password'):
            self.run_migration(DJANGO_ADMIN_PASSWORD='1234')
        self.assertTrue(self.admin().check_password('1234'))  # unchanged; migrate stopped

    @override_settings(PRODUCTION=True)
    def test_live_site_uses_a_strong_admin_password(self):
        self.run_migration(DJANGO_ADMIN_PASSWORD=STRONG_PASSWORD)
        self.assertTrue(self.admin().check_password(STRONG_PASSWORD))
        self.assertFalse(self.admin().check_password('1234'))

    @override_settings(PRODUCTION=True)
    def test_admin_with_a_changed_password_is_left_alone(self):
        admin = self.admin()
        admin.set_password(STRONG_PASSWORD)
        admin.save()
        self.run_migration()  # no DJANGO_ADMIN_PASSWORD needed
        self.assertTrue(self.admin().check_password(STRONG_PASSWORD))

    @override_settings(PRODUCTION=False)
    def test_local_development_keeps_the_demo_password(self):
        self.run_migration()
        self.assertTrue(self.admin().check_password('1234'))


class SendMessageTests(SimpleTestCase):
    """sms.send_message: general texts (status updates), not only OTPs."""

    PHONE = '+919876543210'

    @override_settings(SMS_BACKEND='console')
    def test_console_prints_the_message(self):
        with redirect_stdout(io.StringIO()) as out:
            sms.send_message(self.PHONE, 'Your complaint CC-2026-00042 is now In progress. - City Corporation')
        self.assertEqual(out.getvalue().strip(),
                         '[SMS to +919876543210] Your complaint CC-2026-00042 is now In progress. - City Corporation')

    @override_settings(SMS_BACKEND='console')
    def test_otp_still_goes_through_the_console(self):
        with redirect_stdout(io.StringIO()) as out:
            sms.send_otp(self.PHONE, '123456', 5)
        self.assertIn('123456 is your City Corporation verification code', out.getvalue())

    @override_settings(SMS_BACKEND='carrier-pigeon')
    def test_unknown_backend_raises(self):
        with self.assertRaisesMessage(sms.SMSError, 'Unknown SMS_BACKEND'):
            sms.send_message(self.PHONE, 'Hello')

    @override_settings(SMS_BACKEND='fast2sms', FAST2SMS_API_KEY='test-key')
    def test_fast2sms_uses_the_quick_sms_route(self):
        with mock.patch.object(sms, '_post', return_value={'return': True}) as post:
            sms.send_message(self.PHONE, 'Hello')
        url, body, headers = post.call_args.args
        payload = json.loads(body)
        self.assertEqual((payload['route'], payload['message'], payload['numbers']), ('q', 'Hello', '9876543210'))
        self.assertEqual(headers['authorization'], 'test-key')
        with mock.patch.object(sms, '_post', return_value={'return': False}), self.assertLogs('accounts.sms', 'ERROR'):
            with self.assertRaises(sms.SMSError):
                sms.send_message(self.PHONE, 'Hello')

    @override_settings(SMS_BACKEND='msg91', MSG91_AUTH_KEY='test-key', MSG91_SMS_TEMPLATE_ID='tmpl-1')
    def test_msg91_uses_the_flow_api(self):
        with mock.patch.object(sms, '_post', return_value={'type': 'success'}) as post:
            sms.send_message(self.PHONE, 'Hello')
        url, body, headers = post.call_args.args
        self.assertEqual(url, sms.MSG91_FLOW_URL)
        payload = json.loads(body)
        self.assertEqual(payload['template_id'], 'tmpl-1')
        self.assertEqual(payload['recipients'], [{'mobiles': '919876543210', 'message': 'Hello'}])
        with mock.patch.object(sms, '_post', return_value={'type': 'error'}), self.assertLogs('accounts.sms', 'ERROR'):
            with self.assertRaises(sms.SMSError):
                sms.send_message(self.PHONE, 'Hello')

    @override_settings(SMS_BACKEND='twilio', TWILIO_ACCOUNT_SID='AC1', TWILIO_AUTH_TOKEN='t', TWILIO_FROM_NUMBER='+15550001111')
    def test_twilio_checks_the_reply(self):
        with mock.patch.object(sms, '_post', return_value={'sid': 'SM1', 'status': 'queued'}):
            sms.send_message(self.PHONE, 'Hello')
        for reply in ({'code': 21211, 'message': 'Invalid To number'}, {'sid': 'SM2', 'status': 'failed'}):
            with self.subTest(reply=reply), mock.patch.object(sms, '_post', return_value=reply), \
                    self.assertLogs('accounts.sms', 'ERROR'):
                with self.assertRaises(sms.SMSError):
                    sms.send_message(self.PHONE, 'Hello')

    @override_settings(SMS_BACKEND='fast2sms', FAST2SMS_API_KEY='test-key')
    def test_fast2sms_otp_route(self):
        with mock.patch.object(sms, '_post', return_value={'return': True}) as post:
            sms.send_otp(self.PHONE, '123456', 5)
        payload = json.loads(post.call_args.args[1])
        self.assertEqual((payload['route'], payload['variables_values'], payload['numbers']),
                         ('otp', '123456', '9876543210'))
        with mock.patch.object(sms, '_post', return_value={'return': False}), self.assertLogs('accounts.sms', 'ERROR'):
            with self.assertRaises(sms.SMSError):
                sms.send_otp(self.PHONE, '123456', 5)

    @override_settings(SMS_BACKEND='msg91', MSG91_AUTH_KEY='test-key', MSG91_OTP_TEMPLATE_ID='otp-tmpl')
    def test_msg91_otp_api(self):
        with mock.patch.object(sms, '_post', return_value={'type': 'success'}) as post:
            sms.send_otp(self.PHONE, '123456', 5)
        url = post.call_args.args[0]
        self.assertTrue(url.startswith(sms.MSG91_OTP_URL + '?'))
        self.assertIn('otp=123456', url)
        self.assertIn('mobile=919876543210', url)
        with mock.patch.object(sms, '_post', return_value={'type': 'error'}), self.assertLogs('accounts.sms', 'ERROR'):
            with self.assertRaises(sms.SMSError):
                sms.send_otp(self.PHONE, '123456', 5)
        with override_settings(MSG91_OTP_TEMPLATE_ID=''), self.assertRaises(sms.SMSError):
            sms.send_otp(self.PHONE, '123456', 5)

    def test_post_turns_network_problems_into_sms_errors(self):
        http_error = urllib.error.HTTPError('https://sms.example', 401, 'Unauthorized', {}, io.BytesIO(b'bad key'))
        for problem in (http_error, urllib.error.URLError('no route to host'), TimeoutError()):
            with self.subTest(problem=type(problem).__name__), \
                    mock.patch('urllib.request.urlopen', side_effect=problem), \
                    self.assertLogs('accounts.sms', 'ERROR'), self.assertRaises(sms.SMSError):
                sms._post('https://sms.example', b'{}', {})

    def test_post_reads_json_or_raw_text(self):
        def reply(body):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = body
            return response
        with mock.patch('urllib.request.urlopen', return_value=reply(b'{"return": true}')):
            self.assertEqual(sms._post('https://sms.example', b'{}', {}), {'return': True})
        with mock.patch('urllib.request.urlopen', return_value=reply(b'OK')):
            self.assertEqual(sms._post('https://sms.example', b'{}', {}), {'raw': 'OK'})

    def test_missing_keys_raise_sms_error(self):
        for backend in ('fast2sms', 'msg91', 'twilio'):
            with self.subTest(backend=backend), override_settings(
                    SMS_BACKEND=backend, FAST2SMS_API_KEY='', MSG91_AUTH_KEY='', MSG91_SMS_TEMPLATE_ID='',
                    TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='', TWILIO_FROM_NUMBER=''):
                with self.assertRaises(sms.SMSError):
                    sms.send_message(self.PHONE, 'Hello')


@override_settings(SMS_BACKEND='console', DEBUG=True)
class CitizenOtpTests(TestCase):
    def setUp(self):
        cache.clear()

    def send(self, phone='9876543210', client=None):
        with redirect_stdout(io.StringIO()):  # the console backend prints each SMS
            return (client or self.client).post(reverse('accounts:send_otp'), {'phone': phone}, follow=True)

    def current_code(self):
        return self.client.session[otp.DEV_CODE_KEY]

    def test_phone_numbers_are_normalised(self):
        self.assertEqual(otp.normalize_phone('98765 43210'), '+919876543210')
        self.assertEqual(otp.normalize_phone('+91-9876543210'), '+919876543210')
        self.assertIsNone(otp.normalize_phone('12345'))
        self.assertIsNone(otp.normalize_phone('5876543210'))

    def test_invalid_phone_is_rejected(self):
        self.assertContains(self.send('12345'), 'Enter a valid 10-digit mobile number')

    def test_correct_otp_signs_citizen_in(self):
        page = self.send()
        self.assertContains(page, 'OTP sent to +91 98XXXXXX10')
        code = self.current_code()
        self.assertTrue(re.fullmatch(r'\d{6}', code))
        self.assertNotIn(code, str(self.client.session[otp.SESSION_KEY]))  # only a hash is kept
        response = self.client.post(reverse('accounts:verify_otp'), {'otp': code})
        self.assertRedirects(response, reverse('grievances:complaint_registration'))
        self.assertEqual(self.client.session['citizen_phone'], '+919876543210')

    def wrong_code(self):
        """A 6-digit code that is certainly not the one just sent."""
        return f'{(int(self.current_code()) + 1) % 1000000:06d}'

    def verify(self, code, client=None):
        return (client or self.client).post(reverse('accounts:verify_otp'), {'otp': code}, follow=True)

    def test_wrong_otp_counts_attempts(self):
        self.send()
        self.assertContains(self.verify(self.wrong_code()), '4 attempts left')
        self.assertContains(self.verify(self.wrong_code()), '3 attempts left')

    def test_fifth_wrong_otp_cancels_the_code(self):
        self.send()
        code, wrong = self.current_code(), self.wrong_code()
        for _ in range(4):
            self.verify(wrong)
        self.assertContains(self.verify(wrong), 'Too many incorrect attempts')
        self.assertNotIn(otp.SESSION_KEY, self.client.session)
        # Even the right code no longer works; a new OTP is needed.
        self.assertContains(self.verify(code), 'Please request an OTP first.')
        self.assertNotIn('citizen_phone', self.client.session)

    def test_otp_must_be_six_digits(self):
        self.send()
        for bad in ('12ab56', '12345', '1234567', ''):
            with self.subTest(code=bad):
                self.assertContains(self.verify(bad), 'Enter the 6-digit OTP.')

    def test_verify_without_requesting_an_otp(self):
        self.assertContains(self.verify('123456'), 'Please request an OTP first.')

    def test_resend_is_allowed_after_the_wait(self):
        self.send()
        first = self.current_code()
        later = time.time() + otp.RESEND_WAIT_SECONDS + 1
        with mock.patch('accounts.otp.time.time', return_value=later):
            self.assertContains(self.send(), 'OTP sent to')
            self.assertContains(self.verify(first), 'Incorrect OTP')  # the old code is replaced

    def test_hourly_limit_per_number_across_sessions(self):
        # Fresh browsers each time, so the per-session resend wait doesn't apply; only the shared cache does.
        for _ in range(otp.MAX_SENDS_PER_HOUR):
            self.assertContains(self.send(client=Client()), 'OTP sent to')
        refused = self.send(client=Client())
        self.assertContains(refused, 'Too many OTP requests for this number')
        self.assertNotIn(otp.SESSION_KEY, refused.client.session)
        # Other numbers are not affected.
        self.assertContains(self.send('9123456789', client=Client()), 'OTP sent to')

    @override_settings(SMS_BACKEND='fast2sms', FAST2SMS_API_KEY='')
    def test_sms_failure_shows_a_message_and_does_not_use_up_the_limit(self):
        response = self.send()
        self.assertContains(response, 'We could not send the SMS right now')
        self.assertNotIn(otp.SESSION_KEY, self.client.session)
        self.assertIsNone(cache.get('otp-sends:+919876543210'))

    def test_change_number_clears_the_pending_otp(self):
        self.send()
        self.client.post(reverse('accounts:reset_otp'))
        self.assertNotIn(otp.SESSION_KEY, self.client.session)
        self.assertNotIn(otp.DEV_CODE_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse('accounts:reset_otp')).status_code, 405)

    def test_sign_in_rotates_the_session_key(self):
        self.send()
        before = self.client.session.session_key
        self.verify(self.current_code())
        self.assertNotEqual(self.client.session.session_key, before)  # no session fixation

    def test_expired_citizen_session_must_sign_in_again(self):
        self.send()
        self.verify(self.current_code())
        self.assertEqual(self.client.get(reverse('grievances:track_list')).status_code, 200)
        Session.objects.filter(session_key=self.client.session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1))
        for url in (reverse('grievances:track_list'), reverse('grievances:complaint_registration'),
                    reverse('grievances:citizen_complaint_page')):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse('accounts:login'))
        api = self.client.post(reverse('grievances:api_complaints'), {})
        self.assertEqual(api.status_code, 401)

    def test_sign_out_ends_the_citizen_session(self):
        self.send()
        self.verify(self.current_code())
        self.client.post(reverse('accounts:logout'))
        self.assertRedirects(self.client.get(reverse('grievances:track_list')), reverse('accounts:login'))

    def test_expired_otp_is_refused(self):
        self.send()
        session = self.client.session
        data = session[otp.SESSION_KEY]
        data['expires'] = time.time() - 1
        session[otp.SESSION_KEY] = data
        session.save()
        response = self.client.post(reverse('accounts:verify_otp'), {'otp': self.current_code()}, follow=True)
        self.assertContains(response, 'expired')

    def test_resend_needs_a_short_wait(self):
        self.send()
        self.assertContains(self.send(), 'Please wait')

    def test_registration_page_needs_verified_citizen(self):
        response = self.client.get(reverse('grievances:complaint_registration'))
        self.assertRedirects(response, reverse('accounts:login'))

    def test_citizen_cannot_open_dashboard(self):
        self.send()
        self.client.post(reverse('accounts:verify_otp'), {'otp': self.current_code()})
        self.assertEqual(self.client.get(reverse('grievances:home')).status_code, 302)
