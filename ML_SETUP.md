# Agent Work: dataset collection and TensorFlow training

The **Agent Work** page (`/agent-work/`, corporation admins only) shows one card per dataset. Each
card's **Collect data** button copies the latest records into the database; nothing else starts
collection. Training is a separate step that only runs when you start it on the server.

| Dataset | Source | Text | Label |
|---|---|---|---|
| `complaints` | Complaints in this portal that have a service category | title + description | category slug (`water`, `roads`, …) |
| `ingested` | Records sent to `POST /api/ingest/` (see INGEST_API.md) | title + content | the record's `category` or `label` field, if any |
| `news` | Polimer News YouTube videos, **after a reviewer approves each one** (see below) | video title + description | the category the reviewer chose |
| `external` | Only when `ML_HTTP_SOURCE_URL` is set | `text` (or `title`) | `label` |

Email addresses and phone numbers are replaced with `[email]` / `[phone]` before anything is saved.

## News agent (Polimer News on YouTube)

1. **Collect data** on the "Polimer News (YouTube)" card reads the channel's newest uploads
   (`YOUTUBE_MAX_VIDEOS`, default 200) through the YouTube Data API v3.
2. It keeps only stories that mention a municipal problem. `mlops/news.py` has Tamil and English
   keywords for the portal's four categories: water, roads, sewage and sanitation. A road *accident* is skipped.
3. Each kept story is pre-filled under **News to review**: title (a link to the video), summary, the
   agent's category guess, the words it matched, and the district if the text names one.
4. You check each one and press **Approve** (with the right category) or **Reject**. Approved stories go
   straight into the `news` dataset; rejected ones are never used. A story is never offered twice.
5. Once there are at least `ML_MIN_TRAIN_RECORDS` approved stories in 2 or more categories:

   ```bash
   .venv\Scripts\python.exe manage.py train_model news
   ```

The keyword filter is only a first pass and will miss some stories and catch some it shouldn't.
That is why nothing reaches training without a person approving it.

**Getting the API key** (free, about 5 minutes):
1. Open https://console.cloud.google.com/ and sign in with a Google account.
2. Top bar → project picker → **New project** → name it (e.g. "City Corporation") → **Create**.
3. Search bar → "YouTube Data API v3" → **Enable**.
4. Left menu → **APIs & Services → Credentials** → **Create credentials → API key**. Copy it.
5. Optional but wise: **Edit API key → API restrictions → Restrict key → YouTube Data API v3**.
6. Paste it into `.env` as `YOUTUBE_API_KEY=...` and restart the server. On Vercel, add it under
   Project → Settings → Environment Variables, then redeploy.

One run costs about 5 units of the free 10,000-unit daily quota. YouTube's developer policies limit how
long API data may be kept, so check them before you keep stories for months. Stories are used only
inside this staff-only page.

## 1. Install

The website itself needs nothing new. TensorFlow is needed only on the computer that trains:

```bash
.venv\Scripts\python.exe -m pip install -r requirements-ml.txt
```

TensorFlow 2.21 needs 64-bit Python 3.10–3.13 (this project uses 3.12) and about 1.5 GB of disk.
It is **not** in `requirements.txt`: it is too big for Vercel (250 MB limit), so the live site can
collect data but not train.

## 2. Database

The new tables come from the `mlops` app's migration:

```bash
.venv\Scripts\python.exe manage.py migrate
```

For the live site, run the same command once with `DATABASE_URL` set to the Supabase URL (DEPLOY.md).

## 3. Settings (`.env`, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `ML_JOB_RUNNER` | `thread` (`inline` on Vercel) | `thread`: background thread in the web process. `inline`: runs inside the request. `command`: left queued for `manage.py run_collection_jobs` |
| `ML_JOB_STALE_SECONDS` | `300` | A running job with no progress for this long is marked failed |
| `ML_JOB_QUEUE_TIMEOUT` | `900` | A job still queued after this long is marked failed |
| `ML_JOB_MAX_ATTEMPTS` | `3` | Tries when the database or source fails for a moment (waits 2 s, 4 s, …) |
| `ML_ARTIFACTS_DIR` | `ml_artifacts/` | Where trained models and metrics are saved (gitignored) |
| `ML_MIN_TRAIN_RECORDS` | `20` | Labelled records needed before training |
| `ML_TRAIN_EPOCHS` | `30` | Maximum epochs (training stops early when validation loss stops improving) |
| `ML_HTTP_SOURCE_URL` | empty | Outside source: answers GET with a JSON list (or `{"records": [...]}`) of `{"id", "text", "label"}` |
| `ML_HTTP_SOURCE_TOKEN` | empty | Sent as `Authorization: Bearer <token>`. Keep it in `.env` only |
| `ML_HTTP_SOURCE_NAME` | `External source` | Card title for the outside source |

## 4. Run and collect

```bash
.venv\Scripts\python.exe manage.py runserver
```

Sign in as a corporation admin, open http://127.0.0.1:8000/agent-work/ and press **Collect data** on a
card. The card shows progress and the record count, then what to do next. One job per dataset runs at a
time; pressing again while it runs just shows the running job.

Without the browser (for example from a timer when `ML_JOB_RUNNER=command`):

```bash
.venv\Scripts\python.exe manage.py run_collection_jobs
.venv\Scripts\python.exe manage.py run_collection_jobs --dataset complaints
```

The first runs every queued job. The second starts a new collection and runs it.

## 5. Train

```bash
.venv\Scripts\python.exe manage.py train_model complaints
.venv\Scripts\python.exe manage.py train_model complaints --epochs 50
```

It needs at least `ML_MIN_TRAIN_RECORDS` labelled records in two or more labels; otherwise it stops with
the reason. The model is a baseline: TF-IDF word features → Dense(64, relu) → Dropout → softmax,
trained on 80% of each label and checked on the other 20%. The result also shows the accuracy of always
guessing the most common label; a useful model must beat it.

From Python code: `from mlops.ml import train; train.train('complaints')` and
`train.predict(['Water pipe leaking near the school'])`.

## 6. Where results go

- `ml_artifacts/<dataset>/<YYYYmmdd-HHMMSS>-<run id>/`:
  - `model.keras`: the trained model; it takes raw text.
  - `labels.json`: which output is which label.
  - `metrics.json`: accuracy, loss, the most-common-label baseline, and per-label precision and recall.
- `ml_artifacts/<dataset>/latest.json`: names the newest folder (`predict()` uses it).
- The database: each run is a **TrainingRun** (Django admin → Agent Work), and the latest five show in
  the **Train the model** panel on the Agent Work page.

## API (used by the page)

| Method and path | Body | Answers |
|---|---|---|
| `POST /api/agent-work/collect/` | `{"dataset": "complaints"}` | 202 `{"job"}` started · 409 `{"error", "job"}` already running · 400 bad dataset · 401 not staff · 429 more than 10 a minute · 503 database down |
| `GET /api/agent-work/jobs/<id>/` | – | 200 `{"job"}` · 404 · 401 |

Both need a staff session; POST also needs the `X-CSRFToken` header. `job` has `status`
(`queued`/`running`/`succeeded`/`failed`), `progress` (0–100 or null), `records_total`,
`records_processed`, `records_new`, `error`, `message`, `next_action` and `status_url`.

## Connecting another source

Write a class in `mlops/providers.py` with `slug`, `name`, `description`, `source_label`, `count()` and
`batches()` (a generator of lists of `CollectedRecord(source_id, text, label)`), and decorate it with
`@register`. It appears as a new card. Raise `ProviderError(message, temporary=True)` for failures that
may pass on a retry.
