# Sending data to the City Corporation portal (ingestion API)

This guide is for whoever sends scraped data from another device. Data you send is checked,
cleaned of duplicates, stored, and searchable in the portal's "Citizen help" chatbot straight away.

## 1. What you need from the portal owner

| Item | Example |
|---|---|
| The server address | `https://grievance.example.gov.in` (or `http://192.168.1.20:8000` on the same Wi-Fi) |
| The API key | a long random string, shared privately (not by email or chat) |

The key goes in the **`X-API-Key`** header of every request. Without it, or with a wrong key,
the server answers `401`, and too many wrong keys block your IP for an hour.

## 2. The endpoint

```
POST /api/ingest/
```

You can send data in three ways.

### a) JSON (recommended)

`Content-Type: application/json`, with this body:

```json
{
  "dataset": "ward-news",
  "records": [
    {"id": "news-101", "title": "Water supply cut in Ward 4", "body": "No water on Monday, 9 AM to 5 PM.", "ward": 4},
    {"id": "news-102", "title": "Road relaying on Lake Road", "body": "Expect delays this week."}
  ]
}
```

```bash
curl -X POST https://SERVER/api/ingest/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d @records.json
```

On Windows PowerShell, use `curl.exe` (not `curl`) and put the command on one line.

### b) CSV in the body

`Content-Type: text/csv`, with the dataset name in the address. The first line must be the column names.

```bash
curl -X POST "https://SERVER/api/ingest/?dataset=field-survey" \
  -H "Content-Type: text/csv" \
  -H "X-API-Key: YOUR_KEY" \
  --data-binary @survey.csv
```

```csv
id,title,area,reported_on
s-1,Drain blocked,Egmore,2026-10-20
s-2,Tree fallen on road,Adyar,2026-10-21
```

### c) File upload (CSV or JSON file)

`multipart/form-data` with a field named **`file`** (ending in `.csv` or `.json`) and, for CSV, a
field named **`dataset`**.

```bash
curl -X POST https://SERVER/api/ingest/ \
  -H "X-API-Key: YOUR_KEY" \
  -F "dataset=field-survey" \
  -F "file=@survey.csv"
```

### From Python (standard library only)

```python
import json, urllib.request

body = {"dataset": "ward-news", "records": [{"id": "news-101", "title": "Water supply cut in Ward 4"}]}
request = urllib.request.Request(
    "https://SERVER/api/ingest/", data=json.dumps(body).encode(), method="POST",
    headers={"Content-Type": "application/json", "X-API-Key": "YOUR_KEY"})
with urllib.request.urlopen(request, timeout=30) as response:
    print(response.status, json.loads(response.read()))
```

## 3. Rules for the data

| Rule | Detail |
|---|---|
| `dataset` | Required. 2–50 characters: lower-case letters, digits, `-` or `_`, starting with a letter or digit. One name per kind of data (`ward-news`, `field-survey`). |
| Records | A list of **flat** objects: field name → text, number or true/false. No nested objects or lists. |
| Text | Each record needs at least one non-empty text field. Use `title` (or `name`, `subject`, `headline`) for the line shown in the chat. |
| IDs | Give each record a stable `id` (or `external_id`, `url`) when you can. Sending the same `id` again **updates** that record instead of duplicating it. Without an ID, an identical record (ignoring case and extra spaces) is stored once. |
| Size | At most 5,000 records and 5 MB per request. Send bigger data in batches. |
| Field limits | At most 50 fields per record; field names up to 64 characters; values up to 10,000 characters. |
| Encoding | UTF-8 (Tamil, Hindi and other scripts are fine). |
| Rate | At most 120 requests an hour per device. |

## 4. Answers

**Success** (`201` when something new was stored, `200` when nothing was new):

```json
{"dataset": "ward-news", "received": 3, "created": 1, "updated": 0, "duplicates": 1, "rejected": 1,
 "errors": [{"record": 2, "error": "field \"title\" holds a nested object or list; send flat values only"}]}
```

Good records are stored even when some others are rejected. `errors` lists the rejected ones by position
(0 = first record), up to 50.

**Errors** always come back as `{"error": "what went wrong"}`:

| Status | Meaning | What to do |
|---|---|---|
| 400 | Bad JSON/CSV, missing or bad `dataset`, no valid records, too many records | Fix the data; the message says what's wrong |
| 401 | Missing or wrong `X-API-Key` | Check the key |
| 405 | Not a POST request | Use POST |
| 413 | Larger than 5 MB | Split into smaller batches |
| 415 | Unsupported `Content-Type` | Use JSON, CSV or a file upload |
| 429 | Too many requests (or too many wrong keys) | Wait and retry later |
| 503 | Ingestion switched off on the server, or the database is down | Retry later; nothing was saved |
| 500 | Unexpected server problem | Retry later; nothing was saved |

## 5. For the portal owner

- Set `INGEST_API_KEY` in `.env` (32+ random characters) and restart the server. Empty means the API is off.
- Give the key to your friend privately. To revoke it, change it in `.env` and restart.
- Uploaded data appears in Django admin under **Citizen help assistant → Knowledge records**.
- New kinds of data need no code changes: a new `dataset` name is enough. To make the chatbot search a
  completely different source (another table, documents), add a `Source` class in `assistant/sources.py`.
