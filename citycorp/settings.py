"""
Settings for the City Corporation public grievance portal.

Values that change between computers (secret key, debug, database, cache, SMS keys) are read
from the environment or from a ".env" file next to manage.py. See .env.example and DEPLOY.md.
"""
import os
import warnings
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path):
    """Read KEY=VALUE lines into os.environ. Real environment variables take priority."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_flag(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [item.strip() for item in os.environ.get(name, default).split(',') if item.strip()]


def env_int(name, default):
    value = os.environ.get(name, '').strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ImproperlyConfigured(f'{name} must be a whole number, not {value!r}.') from None


PYMYSQL_PREFIX = 'mysql+pymysql://'


def use_pymysql():
    """
    Let PyMySQL (pure Python) stand in for mysqlclient. Django 6 refuses drivers that report a
    version below 2.2.1, and PyMySQL reports 1.4.6, so it is told to report 2.2.1.
    """
    import pymysql
    pymysql.install_as_MySQLdb()
    pymysql.version_info = (2, 2, 1, 'final', 0)


def database_settings(url):
    """
    DATABASE_URL, or SQLite in db.sqlite3 when empty. Examples:
      postgres://user:pass@host:5432/citycorp
      mysql://user:pass@host:3306/citycorp_db          (driver: mysqlclient)
      mysql+pymysql://user:pass@host:3306/citycorp_db  (driver: PyMySQL)
    """
    if not url:
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}
    if url.startswith(PYMYSQL_PREFIX):
        use_pymysql()
        url = 'mysql://' + url[len(PYMYSQL_PREFIX):]
    # Keep connections open between requests, and check them before reuse.
    config = dj_database_url.parse(url, conn_max_age=600, conn_health_checks=True)
    if config['ENGINE'] == 'django.db.backends.mysql':
        # utf8mb4 stores every character (Tamil, Hindi, emoji); strict mode rejects bad data instead
        # of silently truncating it (and silences Django's mysql.W002 check).
        config.setdefault('OPTIONS', {}).update({
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        })
    return config


def media_root(environ=os.environ):
    """
    Where uploads are saved: DJANGO_MEDIA_ROOT if set, /tmp/media on Vercel (whose app folder is
    read-only, and whose /tmp is wiped from time to time), otherwise media/ next to manage.py.
    """
    if environ.get('DJANGO_MEDIA_ROOT', '').strip():
        return Path(environ['DJANGO_MEDIA_ROOT'].strip())
    if environ.get('VERCEL'):
        return Path('/tmp/media')
    return BASE_DIR / 'media'


def cache_settings(url):
    """
    Redis at REDIS_URL, or per-process memory when empty. OTP and admin-login rate limits live in
    the cache, so a site with more than one gunicorn worker needs Redis for them to hold.
    """
    if not url:
        return {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
    return {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': url,
            'KEY_PREFIX': 'citycorp'}


DEV_SECRET_KEY = 'dev-only-insecure-key-change-before-deploying'


def secret_key(debug):
    """DJANGO_SECRET_KEY, or the development key when DEBUG is on. A live site must set its own."""
    key = os.environ.get('DJANGO_SECRET_KEY', '').strip()
    if key:
        return key
    if debug:
        return DEV_SECRET_KEY
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is not set. Add it to .env, or set DJANGO_DEBUG=1 for local development.')


load_env_file(BASE_DIR / '.env')

# Off unless .env turns it on: a copy deployed without settings must not run in debug mode
# (debug mode also shows the citizen OTP on the login page when SMS_BACKEND is "console").
DEBUG = env_flag('DJANGO_DEBUG', False)
# True on a live site. Unlike DEBUG, the test runner doesn't switch it, so it can guard
# production-only behaviour such as accounts/migrations/0002.
PRODUCTION = not DEBUG
SECRET_KEY = secret_key(DEBUG)
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')
# Full origins (https://grievance.example.gov.in) when the site is reached through a proxy or more
# than one domain.
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
    'accounts',
    'grievances',
    'assistant',
    'mlops',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves /static/ under gunicorn
    'django.contrib.sessions.middleware.SessionMiddleware',
    'core.middleware.CookieLanguageMiddleware',  # English or Hindi, from the language cookie
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'citycorp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # nagar-seva/: the standalone citizen form (filed-complaint.html), served by Django
        'DIRS': [BASE_DIR / 'templates', BASE_DIR / 'nagar-seva'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.i18n',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.layout',
            ],
        },
    },
]

WSGI_APPLICATION = 'citycorp.wsgi.application'

DATABASES = {'default': database_settings(os.environ.get('DATABASE_URL', '').strip())}

CACHES = {'default': cache_settings(os.environ.get('REDIS_URL', '').strip())}

# Outside debug mode, accounts with a weak password (such as the demo admin/1234) can't sign in.
AUTHENTICATION_BACKENDS = ['accounts.backends.StrongPasswordBackend']

# --- HTTPS (on whenever DEBUG is off) -------------------------------------------------------------
# DJANGO_HTTPS=0 turns all of this off, only to try DEBUG=0 on your own computer over plain http.
HTTPS = env_flag('DJANGO_HTTPS', not DEBUG)
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = HTTPS
SESSION_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = HTTPS
# Browsers remember to use HTTPS only. Start lower (e.g. 3600) on a new domain if unsure; see DEPLOY.md.
SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', 31536000)) if HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = HTTPS and env_flag('DJANGO_HSTS_INCLUDE_SUBDOMAINS', True)
SECURE_HSTS_PRELOAD = HTTPS and env_flag('DJANGO_HSTS_PRELOAD', True)
# Behind nginx or a hosting platform that ends TLS and forwards X-Forwarded-Proto. Without it
# SECURE_SSL_REDIRECT loops forever. Only turn it on when the proxy always sets this header.
if env_flag('DJANGO_BEHIND_PROXY', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# The site is in English and Hindi. Visitors start in English and switch with the language menu
# (django.views.i18n.set_language, remembered in a cookie; see core/middleware.py).
# Translations: locale/hi/LC_MESSAGES/*.po. SMS and emails always stay in English.
LANGUAGE_CODE = 'en'
LANGUAGES = [('en', 'English'), ('hi', 'हिंदी')]
LOCALE_PATHS = [BASE_DIR / 'locale']
LANGUAGE_COOKIE_AGE = 365 * 24 * 60 * 60
LANGUAGE_COOKIE_SAMESITE = 'Lax'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
    # The nagar-seva page's own assets, at /static/nagar-seva/... (not the HTML itself)
    ('nagar-seva/css', BASE_DIR / 'nagar-seva' / 'css'),
    ('nagar-seva/js', BASE_DIR / 'nagar-seva' / 'js'),
    ('nagar-seva/assets', BASE_DIR / 'nagar-seva' / 'assets'),
]
STATICFILES_FINDERS = [
    'core.finders.FileSystemFinder',  # so the "nagar-seva/..." prefixes above work on Windows too
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'  # filled by "manage.py collectstatic" on the server
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # Live site: hashed, compressed copies from collectstatic, served by whitenoise with long caching.
    # Development and tests: the files as they are (no collectstatic needed).
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG
                    else 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# Complaint photos, earlier-complaint copies and employee photos. They hold personal details, so they
# are never served from MEDIA_URL; staff open them through grievances:complaint_file / officer_photo.
MEDIA_URL = 'media/'
MEDIA_ROOT = media_root()

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Dashboard pages need a corporation admin (staff) login.
LOGIN_URL = 'accounts:login'

# Citizen SMS (OTPs and complaint status updates): "console" (testing), "fast2sms", "msg91" or
# "twilio". See accounts/sms.py.
SMS_BACKEND = os.environ.get('SMS_BACKEND', 'console').strip().lower()
# Demo switch for a live site without an SMS provider: with the console backend, show the citizen
# OTP on the login page (debug mode always does). Anyone can then sign in as any mobile number,
# so turn it off once a real SMS provider is set up.
CITIZEN_OTP_ON_SCREEN = env_flag('CITIZEN_OTP_ON_SCREEN', False)
FAST2SMS_API_KEY = os.environ.get('FAST2SMS_API_KEY', '')
MSG91_AUTH_KEY = os.environ.get('MSG91_AUTH_KEY', '')
MSG91_OTP_TEMPLATE_ID = os.environ.get('MSG91_OTP_TEMPLATE_ID', '')
MSG91_SMS_TEMPLATE_ID = os.environ.get('MSG91_SMS_TEMPLATE_ID', '')  # status-update SMS (Flow API)
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER', '')

# --- Assistant: email through Brevo, and the data-ingestion API (see assistant/, INGEST_API.md) ---
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '').strip()
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', '').strip()     # a sender verified in Brevo
SENDER_NAME = os.environ.get('SENDER_NAME', 'City Corporation').strip()
# Emails only ever go to assistant.brevo.ONLY_RECIPIENT; ADMIN_EMAIL must match it or nothing is sent.
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'govermentowncorporation@gmail.com').strip()
INGEST_API_KEY = os.environ.get('INGEST_API_KEY', '').strip()
# Request bodies up to 5 MB (the ingestion API's limit; Django's default is 2.5 MB).
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Agent Work: dataset collection and TensorFlow training (see mlops/, ML_SETUP.md) ---
# Where "Collect data" jobs run: "thread" (a background thread in the web process), "inline" (inside
# the request; Vercel freezes background threads once the response is sent) or "command" (left queued
# for "manage.py run_collection_jobs", e.g. from a systemd timer or Task Scheduler).
ML_JOB_RUNNER = os.environ.get('ML_JOB_RUNNER', 'inline' if os.environ.get('VERCEL') else 'thread').strip().lower()
if ML_JOB_RUNNER not in ('thread', 'inline', 'command'):
    raise ImproperlyConfigured('ML_JOB_RUNNER must be "thread", "inline" or "command".')
# A running job that hasn't reported progress for this long (or a job queued this long) is marked failed.
ML_JOB_STALE_SECONDS = env_int('ML_JOB_STALE_SECONDS', 300)
ML_JOB_QUEUE_TIMEOUT = env_int('ML_JOB_QUEUE_TIMEOUT', 900)
# Tries per job when the database or the data source fails for a moment.
ML_JOB_MAX_ATTEMPTS = env_int('ML_JOB_MAX_ATTEMPTS', 3)
# Trained models and their metrics: <ML_ARTIFACTS_DIR>/<dataset>/<run time>/ (gitignored).
ML_ARTIFACTS_DIR = Path(os.environ.get('ML_ARTIFACTS_DIR', '').strip() or BASE_DIR / 'ml_artifacts')
ML_MIN_TRAIN_RECORDS = env_int('ML_MIN_TRAIN_RECORDS', 20)
ML_TRAIN_EPOCHS = env_int('ML_TRAIN_EPOCHS', 30)
# Optional outside data source (JSON over HTTPS), shown as one more dataset card. See ML_SETUP.md.
ML_HTTP_SOURCE_URL = os.environ.get('ML_HTTP_SOURCE_URL', '').strip()
ML_HTTP_SOURCE_TOKEN = os.environ.get('ML_HTTP_SOURCE_TOKEN', '').strip()
ML_HTTP_SOURCE_NAME = os.environ.get('ML_HTTP_SOURCE_NAME', 'External source').strip()

# Warnings and errors (failed SMS, server errors) go to the console, which gunicorn/systemd keep.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'plain': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
    'root': {'handlers': ['console'], 'level': os.environ.get('DJANGO_LOG_LEVEL', 'WARNING')},
    # Every 404/405 is a warning; keep server errors (5xx). CSRF failures still come from django.security.
    'loggers': {'django.request': {'level': 'ERROR'}},
}

if DEBUG:
    # No collectstatic in development, so whitenoise has no STATIC_ROOT to scan; runserver serves
    # /static/ itself. On a live site the warning stays: it means collectstatic wasn't run.
    warnings.filterwarnings('ignore', message='No directory at', category=UserWarning)
