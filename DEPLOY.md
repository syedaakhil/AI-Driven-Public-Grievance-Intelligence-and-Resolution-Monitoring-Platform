# Deploying the City Corporation grievance portal

This guide puts the portal on one Linux server (Ubuntu 24.04 LTS) with **PostgreSQL**, **Redis**,
**gunicorn** behind **nginx**, and HTTPS from Let's Encrypt. On a hosting platform (Render, Railway,
a cloud VM image, …) the steps are the same ideas: set the environment variables from step 4, run
the commands from steps 5–6 at release time, and start gunicorn as in step 7.

> This is a college project. It is not an official government service, and the footer says so. If
> real citizens' data will be stored, check the applicable data-protection rules first.

Throughout, replace `grievance.example.gov.in` with your domain.

---

## 1. What you need

- A server with Ubuntu 24.04, 1–2 GB RAM, and a sudo user.
- A domain whose DNS **A record** points to the server's IP.
- For real SMS: an account with Fast2SMS, MSG91 or Twilio. For Indian numbers you also need a
  DLT-registered sender ID and approved templates (see step 4).

## 2. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql redis-server nginx certbot python3-certbot-nginx
sudo systemctl enable --now postgresql redis-server nginx
```

Check that Python is 3.12 or newer, which Django 6 needs:

```bash
python3 --version
```

## 3. Create the database and the app user

```bash
sudo -u postgres psql -c "CREATE USER citycorp WITH PASSWORD 'CHOOSE-A-STRONG-DB-PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE citycorp OWNER citycorp;"

sudo adduser --system --group --home /srv/citycorp citycorp
```

Copy the project to `/srv/citycorp/app`, using `git clone` or `scp`. **Don't** copy `.venv/`,
`db.sqlite3`, `media/` or your local `.env`. Then create the virtual environment:

```bash
sudo -u citycorp python3 -m venv /srv/citycorp/venv
sudo -u citycorp /srv/citycorp/venv/bin/pip install -r /srv/citycorp/app/requirements.txt
```

## 4. Create `/srv/citycorp/app/.env`

Start from `.env.example`. A live site needs at least the following:

```ini
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_ALLOWED_HOSTS=grievance.example.gov.in
DJANGO_CSRF_TRUSTED_ORIGINS=https://grievance.example.gov.in
DJANGO_BEHIND_PROXY=1

DATABASE_URL=postgres://citycorp:CHOOSE-A-STRONG-DB-PASSWORD@localhost:5432/citycorp
REDIS_URL=redis://localhost:6379/0

DJANGO_ADMIN_USERNAME=admin
DJANGO_ADMIN_PASSWORD=<a strong password: 8+ characters, not common, not only digits>

SMS_BACKEND=msg91            # or fast2sms / twilio, with that provider's keys below
MSG91_AUTH_KEY=...
MSG91_OTP_TEMPLATE_ID=...
MSG91_SMS_TEMPLATE_ID=...
```

Lock it down. It holds every secret:

```bash
sudo chown citycorp:citycorp /srv/citycorp/app/.env
sudo chmod 600 /srv/citycorp/app/.env
```

What these settings do:

- **`DJANGO_DEBUG=0`** turns on:
  - secure cookies;
  - the http → https redirect;
  - HSTS (one year, subdomains, preload-ready);
  - hashed static files;
  - the strong-password rule for admin sign-in.
- **`DJANGO_BEHIND_PROXY=1`** is required here, because nginx ends HTTPS and forwards
  `X-Forwarded-Proto`. Without it, the https redirect loops forever.
- **HSTS:** browsers will refuse plain http for this domain for a year. On a brand-new domain you
  can start with `DJANGO_HSTS_SECONDS=3600` and raise it once HTTPS works. Submitting the domain to
  hstspreload.org is a separate, hard-to-undo step. Don't do it unless you mean to.
- **SMS:**
  - **MSG91** needs two templates: an OTP one containing `##OTP##`, and a status-update one whose
    only variable is `##message##`.
  - **Fast2SMS** uses its OTP route for codes and Quick SMS for status updates.
  - With `SMS_BACKEND=console`, citizens can't sign in, because codes are only written to the log.

## 5. Set up the database, admin and static files

Run these as the app user, from the project folder:

```bash
cd /srv/citycorp/app
sudo -u citycorp /srv/citycorp/venv/bin/python manage.py migrate
sudo -u citycorp /srv/citycorp/venv/bin/python manage.py collectstatic --noinput
sudo -u citycorp mkdir -p /srv/citycorp/app/media
```

- `migrate` creates the tables, the service categories and wards, and the corporation admin account.
- **If `migrate` stops with "still has the demo password 1234"**, `DJANGO_ADMIN_PASSWORD` is missing
  or too weak. Fix it in `.env` and run `migrate` again: it sets that password and continues.
- `collectstatic` copies CSS, JS and images into `staticfiles/` with hashed names. Run it again after
  every update. gunicorn serves them through whitenoise.

## 6. Check the configuration

```bash
sudo -u citycorp /srv/citycorp/venv/bin/python manage.py check --deploy
```

It must say **"System check identified no issues"**. The project adds its own checks:

| ID | Meaning | Fix |
|---|---|---|
| `accounts.E001` | The secret key is the development key | Set `DJANGO_SECRET_KEY` |
| `accounts.W001` | The SMS backend is still `console` | Set a real `SMS_BACKEND` and its keys |
| `accounts.W002` | No Redis, so OTP and login rate limits aren't shared between workers | Set `REDIS_URL` |

Django's own warnings (`security.W…`) disappear once `DJANGO_DEBUG=0` and the variables above are set.

## 7. Run gunicorn with systemd

Create `/etc/systemd/system/citycorp.service`:

```ini
[Unit]
Description=City Corporation grievance portal (gunicorn)
After=network.target postgresql.service redis-server.service

[Service]
User=citycorp
Group=citycorp
WorkingDirectory=/srv/citycorp/app
ExecStart=/srv/citycorp/venv/bin/gunicorn citycorp.wsgi:application \
    --bind 127.0.0.1:8000 --workers 3 --timeout 60 \
    --access-logfile - --error-logfile -
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now citycorp
sudo systemctl status citycorp
```

Notes:

- `--workers 3` suits 1–2 CPUs; a common rule is 2 × CPUs + 1. Because of Redis, OTP limits hold
  across all workers.
- `--timeout 60` leaves room for the SMS provider. Each SMS call waits up to 10 seconds.
- To follow the logs (requests, failed SMS, server errors):

```bash
sudo journalctl -u citycorp -f
```

## 8. Put nginx and HTTPS in front

Create `/etc/nginx/sites-available/citycorp`:

```nginx
server {
    listen 80;
    server_name grievance.example.gov.in;

    # Photo (5 MB) + earlier complaint copy (5 MB) + form fields
    client_max_body_size 12M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Do not add a `location /media/` block.** Complaint photos and copies hold personal details. Django
serves them only to staff and to the citizen who filed the complaint.

Enable the site and get a certificate. certbot adds the HTTPS server block and keeps renewing it:

```bash
sudo ln -s /etc/nginx/sites-available/citycorp /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d grievance.example.gov.in
```

## 9. Check it works

1. Open `http://grievance.example.gov.in/`. It should redirect to **https://**.
2. Sign in as the admin with `DJANGO_ADMIN_PASSWORD`.
3. Sign in as a citizen with a real mobile number. The OTP should arrive by SMS and must **not**
   appear on the page.
4. Register a test complaint with a photo, open it as the admin, and change its status. The citizen
   should get the status SMS.
5. Copy the photo's `/media/...` path, if you know it, and open it while signed out. It must
   **not** load.
6. Delete the test complaint in `/admin/`.

## 10. Backups

These two things hold all the data. Back both up regularly and keep a copy off the server:

```bash
# Database
sudo -u postgres pg_dump -Fc citycorp > citycorp-$(date +%F).dump
# Uploaded photos and copies
sudo tar czf citycorp-media-$(date +%F).tgz -C /srv/citycorp/app media
```

To restore the database:

```bash
sudo -u postgres pg_restore -d citycorp --clean citycorp-YYYY-MM-DD.dump
```

On a hosting platform without a persistent disk, `media/` is lost on every deploy. Attach a
persistent volume at `/srv/citycorp/app/media` (or move uploads to object storage) before going live.

## 11. Updating the site

Copy in the new code (for example `git pull`), then:

```bash
cd /srv/citycorp/app
sudo -u citycorp /srv/citycorp/venv/bin/pip install -r requirements.txt
sudo -u citycorp /srv/citycorp/venv/bin/python manage.py migrate
sudo -u citycorp /srv/citycorp/venv/bin/python manage.py collectstatic --noinput
sudo -u citycorp /srv/citycorp/venv/bin/python manage.py check --deploy
sudo systemctl restart citycorp
```

Run `python manage.py test` on your own computer before every update.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Too many redirects" | `DJANGO_BEHIND_PROXY=1` missing, or nginx not sending `X-Forwarded-Proto` |
| 400 Bad Request on every page | The domain isn't in `DJANGO_ALLOWED_HOSTS` |
| 403 CSRF error when submitting forms | `DJANGO_CSRF_TRUSTED_ORIGINS` missing the `https://` origin |
| Pages without CSS, or a 500 mentioning "manifest" | `collectstatic` wasn't run after the update |
| Server won't start: "DJANGO_SECRET_KEY is not set" | `.env` missing, unreadable by `citycorp`, or `DJANGO_DEBUG` not set |
| `migrate` stops: "still has the demo password 1234" | Set a strong `DJANGO_ADMIN_PASSWORD` and run `migrate` again |
| Admin sign-in: "still has a weak password" | Run `manage.py changepassword admin` and choose a strong password |
| Citizens get no OTP | `SMS_BACKEND` still `console`, wrong keys, or the DLT template isn't approved. Check `journalctl -u citycorp` |
| "413 Request Entity Too Large" on upload | `client_max_body_size` missing in nginx |
