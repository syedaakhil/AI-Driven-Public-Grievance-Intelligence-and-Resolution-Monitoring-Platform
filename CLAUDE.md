# CLAUDE.md

This file guides Claude Code (claude.ai/code) when working in this repository.

## Project rules

- This is a Django 6 project (`citycorp` settings, apps: `accounts`, `grievances`, `core`) for a City Corporation grievance portal.
- Citizens sign in with mobile OTP (`accounts/otp.py`, session key `"citizen_phone"`); admins are Django staff users.
- Always run `python manage.py test` after every change and fix failures before finishing.
- Never edit existing migrations; create new ones with `makemigrations`.
- Reuse existing form validation in `grievances/forms.py` instead of duplicating rules.
- Keep secrets in `.env` (see `.env.example`); never hard-code keys.
- Write tests for every new feature.

## Commands

The project uses a local venv at `.venv` (Windows). Call its python directly; there's no need to activate it.

```bash
.venv\Scripts\python.exe manage.py runserver          # http://127.0.0.1:8000/
.venv\Scripts\python.exe manage.py test               # all tests
.venv\Scripts\python.exe manage.py test accounts      # one app
.venv\Scripts\python.exe manage.py test grievances.tests.CitizenRegistrationTests   # one class
.venv\Scripts\python.exe manage.py makemigrations
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe -m coverage run manage.py test    # then: -m coverage report  (needs requirements-dev.txt)
```

Coverage is configured in `.coveragerc`, with branch coverage on. Keep it at **85% or more**.

Open pages through `runserver` on port 8000. VS Code Live Server (port 5500) can't render Django templates; it shows raw `{% block %}` text and no CSS.

## Architecture

- **`citycorp/`**: settings, root URLs, and WSGI/ASGI. `settings.py` loads `.env` by itself (`load_env_file`, where real env vars win) and reads `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_HTTPS`, `SMS_BACKEND` and the SMS provider keys. The database is SQLite (`db.sqlite3`), and `LOGIN_URL` is `accounts:login`.
  - **`DEBUG` defaults to off.** Local development needs `DJANGO_DEBUG=1` in `.env`. With DEBUG off:
    - `DJANGO_SECRET_KEY` is required.
    - HTTPS protection is on: secure cookies, SSL redirect and HSTS. It's controlled by `DJANGO_HTTPS` and `DJANGO_BEHIND_PROXY`.
    - Weak passwords such as the demo `admin`/`1234` are refused at sign-in (`accounts/backends.py`).
    - `accounts/migrations/0002` stops `migrate` while the admin still has `1234`. It reads `settings.PRODUCTION`, which the test runner doesn't change, unlike `DEBUG`.
  - `DATABASE_URL` selects PostgreSQL or MySQL, through `database_settings()`. Without it, SQLite is used.
    - `mysql://` uses mysqlclient. `mysql+pymysql://` uses PyMySQL through `use_pymysql()`, which also makes PyMySQL report version 2.2.1, because Django 6 refuses older drivers.
    - MySQL connections always get `charset=utf8mb4` and `STRICT_TRANS_TABLES`.
    - **The local `.env` points at MySQL.** To run the tests on SQLite without MySQL, set `DATABASE_URL=sqlite:///db.sqlite3` in the environment first; a real environment variable wins over `.env`.
  - `setup.bat` (Windows) and `setup.sh` (Linux/macOS) do first-time setup: venv, packages, `.env` from `.env.example`, and migrate. `.gitattributes` keeps them CRLF and LF respectively.
  - `REDIS_URL` selects the Redis cache, through `cache_settings()`. Without it, the cache is per-process memory, so the OTP and login rate limits aren't shared between workers.
  - whitenoise serves `/static/`. With DEBUG off it uses hashed, compressed files, so `collectstatic` is needed. In development and tests it uses plain files.
  - All dependencies are pinned in `requirements.txt`. Deployment steps are in `DEPLOY.md`.
  - `manage.py check --deploy` also runs `accounts/checks.py`, which flags the console SMS backend (W001), a per-process cache (W002) and the dev secret key (E001). `ProductionSettingsTests` runs the check in a subprocess with live-site variables and expects no warnings.
  - Uploads go to `media/` but are **never served from `/media/`**. They're opened through `grievances:complaint_file` (`/complaints/<ref>/files/photo|previous-copy/`). Link to that view, never to `FileField.url`.
    - Staff can open any complaint's files. A verified citizen can open only their own (via `_citizen_complaints`); another citizen gets a 404, and anyone else is sent to login.
- **`accounts/`**: authentication.
  - `views.py`: the `/login/` page with Citizen and Corporation admin tabs, `/login/admin/`, OTP send/verify/reset, and `/logout/`. Admin login rejects non-staff users.
  - `otp.py`: OTP generation and verification. The session stores only an HMAC of (phone, code) under `pending_otp`. It also handles expiry, wrong-try limits, resend wait and per-phone hourly rate limit (cache). `normalize_phone()` returns `+91XXXXXXXXXX`.
  - `sms.py`: SMS backends `console` (the default, which prints the message), `fast2sms`, `msg91` and `twilio`.
    - `send_otp()` sends sign-in codes. `send_message(phone, text)` sends any other text.
    - Every failure raises `SMSError`. Tests use the console backend and `redirect_stdout`, and mock `sms._post` for providers.
  - `decorators.py`: `CITIZEN_SESSION_KEY = 'citizen_phone'` (set after a successful OTP), `citizen_required`, and `staff_required` (`is_active and is_staff`).
  - The migration `0001_corporation_admin` creates the admin user from `DJANGO_ADMIN_USERNAME`/`DJANGO_ADMIN_PASSWORD`.
- **`grievances/`**: the domain.
  - Models: `Category`, `Ward`, `Officer`, `Complaint` (with `Status`, `Priority` and `Channel` choices), `ComplaintUpdate`, `Notice` and `SupportEnquiry`.
  - `forms.py` holds all validation:
    - `CitizenComplaintForm`: the only form that creates complaints, used by `/complaint-registration/` and `POST /api/complaints/`. Its template writes its own inputs, and the form only validates. Staff don't enter complaints in the dashboard; they use Django `/admin/` if needed.
    - `StatusUpdateForm`.
    - Filter forms: `ComplaintFilterForm`, `FiledComplaintFilterForm` and `OfficerFilterForm`.
    - `SupportEnquiryForm`.
    - Shared helpers: `NAME_RE`, the upload limits/types (`PHOTO_TYPES`, `PREVIOUS_COPY_TYPES`, 5 MB), `_check_upload` and `style_fields`. Phone numbers are validated by `accounts.otp.normalize_phone`.
  - `services.py` aggregates the dashboard stats (`dashboard(range_months)`, month/week periods and `percent_change`).
  - In `views.py`, dashboard pages are `@staff_required`. Citizen pages (`complaint_registration`, `track_list` at `/track/`, `track_detail` at `/track/<ref>/`) are `@citizen_required`.
    - **Citizen pages must look complaints up through `_citizen_complaints(request)`**, which filters `citizen_contact` to the verified phone. Another citizen's reference gives a 404.
    - `ComplaintUpdate.is_public` (staff tick "Show this note to the citizen") decides whether a note's text appears on `/track/<ref>/`. Every status step is shown either way.
    - The citizen header is shared: `templates/grievances/_citizen_header.html`.
  - `complaint_delete` (`POST /complaints/<ref>/delete/`) permanently deletes a complaint.
    - It needs a staff login, the `grievances.delete_complaint` permission (superusers have it) and `confirm=yes` (the tick box).
    - The `post_delete` signal in `models.py` removes the uploaded files once the delete commits. This also covers deletes in Django `/admin/`.
    - Every delete is logged with the reference and username.
  - When `complaint_detail` changes a status, `grievances/notify.py` texts the citizen ("Your complaint CC-… is now In progress. - City Corporation").
    - It only fires for `filed_by_citizen` complaints whose `citizen_contact` is a valid mobile number.
    - It runs after the save. A failure is logged (reference only, no phone number) and shown as a warning, and never undoes the update. The CSV export escapes formula-leading cells (`_csv_safe`).
  - Migrations `0002_reference_data` seeds categories and wards. That's reference data, not demo data.
- **`assistant/`**: the "Citizen help" chatbot (staff only), Brevo email, the daily report and the ingestion API.
  - Widget: `templates/assistant/_widget.html`, `static/js/assistant.js` and `static/css/assistant.css`. `base.html` includes them only when `request.user.is_staff`.
  - `POST /api/assistant/chat/` (`views.chat_message`): staff session plus CSRF, 30 messages a minute, JSON `{"message"}`. It answers `{"reply", "results", "total", "email"?}`, and errors as `{"error"}` (400/401/429/500/503).
  - **Knowledge layer:** `sources.py` keeps a registry of `Source` classes. `ComplaintSource` reads `grievances.Complaint` directly (no copy); `IngestedDataSource` reads `KnowledgeRecord`. To search something new, add a `@register` class.
  - `search.py`: `parse_query` (reference, status/today/recent filters, stopwords; email addresses removed) and `score`, which is case-insensitive, matches parts of words, and accepts spelling within `difflib` 0.8.
  - `intents.py`: rule-based "email me…" detection with an exact typo list. There's no fuzzy matching there, so "main" never becomes "mail".
  - **Email:** `brevo.send_email(subject, html, text, kind)` has **no recipient parameter**.
    - It sends only to `brevo.ONLY_RECIPIENT` (`govermentowncoporation@gmail.com`).
    - `ADMIN_EMAIL` must equal that address, and `check_only_recipient()` guards the payload.
    - Every attempt is written to `EmailLog`. Never add a way to choose recipients.
  - **Daily report:** `reports.py` plus `manage.py send_daily_report [--force] [--dry-run]`.
    - It's due from 09:00 Asia/Kolkata.
    - `ReportRun` (one row per date) stops duplicates; a failed run is retried by the next run.
    - It's started from outside: a systemd timer (DEPLOY.md), or `run_daily_report.bat` from Windows Task Scheduler.
  - **Ingestion:** `POST /api/ingest/` (`ingest.py`), documented in `INGEST_API.md`.
    - It needs the `X-API-Key` header, which must equal `INGEST_API_KEY`.
    - It accepts JSON, CSV or file uploads, up to 5 MB and 5000 records.
    - Duplicates are removed by a fingerprint (`dataset` plus ID or cleaned data).
  - Tests mock `urllib.request.urlopen`; nothing is really emailed.
- **`core/`**: shared UI.
  - `context_processors.layout`: the sidebar, notifications, and a count of citizen complaints waiting for review.
  - `templatetags/ui.py`: `icon`, `static_v` (static URL plus `?v=<mtime>` cache-busting; use it for every CSS/JS link), `absolute` and `query`.
  - `templatetags/ui.py` also has `|tr`, which translates database reference text (seeded category, department and ward names).
  - `middleware.CookieLanguageMiddleware` picks the language from the `django_language` cookie only (the browser's Accept-Language is ignored), so first-time visitors get English.
- **Languages (English and Hindi)**: `LANGUAGES = en, hi` in settings, and `locale/hi/LC_MESSAGES/` holds `django.po`/`djangojs.po` plus the compiled `.mo` files, which are committed.
  - The switch is `templates/partials/language_switch.html` (POSTs to `set_language` at `/i18n/setlang/`, which sets the cookie). `compact=True` gives the pill style used on the login and citizen pages; without it the buttons are topbar menu items.
  - New text: wrap it in `{% translate %}`/`{% blocktranslate %}` in templates, `gettext`/`gettext_lazy`/`ngettext` in Python, and `gettext()`/`ngettext()`/`interpolate()` in JS (served by `/jsi18n/`). Inside `{% translate %}` write `&amp;`, not `&`: its output isn't escaped.
  - New seeded reference text goes in `grievances/reference_i18n.py` (`gettext_noop`) and is shown with `|tr` or `gettext()`.
  - **Citizen SMS, admin emails (chat email and 9 AM report) and the ingest API stay English**, via `translation.override('en')` in `grievances/notify.py`, `assistant/chat.py` and `assistant/reports.py`.
  - Tests run in English (no cookie). `core/tests.py` covers the Hindi pages, the cookie, `/jsi18n/`, English-only SMS/email, and fails if any `.po` entry is empty or fuzzy.
  - After changing text, update and compile the translations (needs GNU gettext; on this PC it's in `%LOCALAPPDATA%\Programs\gettext-iconv\bin`, which must be on `PATH`), fill the new `msgstr` lines, then compile:

    ```bash
    .venv\Scripts\python.exe manage.py makemessages -l hi --no-wrap --no-location --ignore=.venv --ignore=staticfiles --ignore=htmlcov --ignore=media --ignore=*/tests.py --ignore=locale
    .venv\Scripts\python.exe manage.py makemessages -d djangojs -l hi --no-wrap --no-location --ignore=.venv --ignore=staticfiles --ignore=htmlcov --ignore=media --ignore=locale
    .venv\Scripts\python.exe manage.py compilemessages -l hi --ignore=.venv
    ```
- **`templates/`**:
  - `base.html`, `partials/` (sidebar, topbar, field, messages, pagination), `accounts/login.html`, and `grievances/*.html`.
  - **`static/`**: `css/main.css` and `css/citizen.css`, plus `js/` and `img/`.
- **`nagar-seva/`**: a second, standalone citizen complaint form with its own design, served by Django.
  - `filed-complaint.html` is a Django template (`TEMPLATES` `DIRS`), rendered by `citizen_complaint_page` at `/citizen/complaint/` (`@citizen_required`). It no longer works with Live Server.
  - Its `css/`, `js/` and `assets/` are static files at `/static/nagar-seva/...` through prefixed `STATICFILES_DIRS`. `core/finders.py` makes those prefixes work on Windows.
  - `js/complaint-form.js` posts multipart `FormData` to `api_complaints` (`POST /api/complaints/`), using the `CitizenComplaintForm` field names and the `X-CSRFToken` header.
    - The API answers 201 `{"complaint": {...}}`, 400 `{"errors": {field: [messages]}}`, or 401 `{"login_url"}`.
    - It saves through the same `_save_citizen_complaint` helper as `/complaint-registration/`. Nothing is kept in localStorage.

## Conventions

- Brand: "City Corporation", with the tagline "Your city, our responsibility." Don't invent demo statistics, complaints, officers or notices.
- Tests use Django's `TestCase` (`accounts/tests.py`, `grievances/tests.py`). OTP tests rely on the `console` SMS backend.
  - The test runner runs with `DEBUG=False`, so `client.login('admin', '1234')` fails. Sign staff in with `force_login` (`sign_in_admin` in `grievances/tests.py`), or use `@override_settings(DEBUG=True)` when testing the demo login itself.
- Pages send `X-Frame-Options: DENY`, so open them directly instead of in iframes.
