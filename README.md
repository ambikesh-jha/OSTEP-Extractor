# OSTEP Offline Builder — FastAPI

A production-ready REST + WebSocket API that downloads, compiles and merges
**Operating Systems: Three Easy Pieces** (OSTEP) into a single offline HTML
document and an optional combined PDF.

---

## Project Structure

```
ostep_api/
├── main.py                        # App factory + Uvicorn entry point
├── .env                           # Environment configuration
├── requirements.txt
├── README.md
├── index.html
│
├── core/
│   ├── config.py                  # Pydantic Settings (reads .env)
│   └── logging.py                 # Centralised logging setup
│
├── models/
│   └── schemas.py                 # All Pydantic request / response models
│
├── api/
│   ├── dependencies.py            # FastAPI Depends providers
│   └── routes/
│       ├── health.py              # GET  /health
│       ├── scrape.py              # POST /api/v1/scrape/start
│       │                          # GET  /api/v1/scrape/status
│       ├── merge.py               # POST /api/v1/merge/start
│       │                          # GET  /api/v1/merge/status
│       ├── chapters.py            # GET  /api/v1/chapters/
│       │                          # GET  /api/v1/chapters/assets
│       └── ws.py                  # WS   /api/v1/ws/status
│
├── services/
│   ├── scraper.py                 # Core scrape-and-compile pipeline
│   ├── merger.py                  # PDF merge pipeline
│   ├── chapter_parser.py          # Index page → chapter list
│   └── job_store.py               # Single-pipeline state + WS pub/sub
│
├── static/
│   ├── app.js                     # javascript 
│   └── style.css                  # style
│
└── utils/
    ├── http.py                    # RateLimitedSession + RobotsChecker
    └── file_helpers.py            # classify, slugify, escape, path utils
```

---

## Quick Start

```bash
# 1. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) edit .env to override defaults
#    e.g. DEBUG=true, REQUEST_DELAY=1.5

# 4. Start the server
uvicorn main:app --host 0.0.0.0 --port 8000

# Interactive docs → http://localhost:8000/docs
```

---

## Pipeline Design

There is **one** shared pipeline job with a fixed ID `"ostep-pipeline"`.
It moves through two ordered phases:

```
idle ──► scrape (pending → running → completed)
                                  └──► merge (pending → running → completed)
```

- `POST /scrape/start` — allowed only when the pipeline is **idle** (not
  pending/running). Returns `409 Conflict` otherwise.
- `POST /merge/start` — allowed only after the scrape phase has
  **completed**. Returns `412 Precondition Failed` if not.
- Both endpoints return the **same** `job_id = "ostep-pipeline"`.
- Connect to the WebSocket **at any time** to receive live push updates.

---

## API Reference

Interactive Swagger UI: **http://localhost:8000/docs**
ReDoc:                  **http://localhost:8000/redoc**

### Health

```
GET /health
```
```json
{
  "status": "ok",
  "version": "1.0.0",
  "output_dir_exists": true,
  "pdf_count": 57,
  "html_ready": true,
  "merged_pdf_ready": false
}
```

---

### Scrape Phase

#### Start scrape
```
POST /api/v1/scrape/start
Content-Type: application/json

{ "index_url": null }        ← omit to use default OSTEP URL
```

**202 Accepted** — job started:
```json
{
  "job_id":  "ostep-pipeline",
  "phase":   "scrape",
  "status":  "pending",
  "message": "Scrape started. Connect to ws://.../api/v1/ws/status for live updates."
}
```

**409 Conflict** — already running:
```json
{
  "detail": "Pipeline is already active (phase=scrape, status=running). Connect to WS /api/v1/ws/status to monitor progress."
}
```

#### Poll status (REST)
```
GET /api/v1/scrape/status
```

---

### Merge Phase

#### Start merge
```
POST /api/v1/merge/start
Content-Type: application/json

{ "include_extras": true }
```

**202 Accepted** — same `job_id` as scrape:
```json
{
  "job_id":  "ostep-pipeline",
  "phase":   "merge",
  "status":  "pending",
  "message": "Merge started using the same pipeline job."
}
```

**409 Conflict** — pipeline still active
**412 Precondition Failed** — scrape not yet completed

#### Poll status (REST)
```
GET /api/v1/merge/status
```

---

### WebSocket — Live Status Stream

```
ws://localhost:8000/api/v1/ws/status
```

**No authentication or parameters required.**

| Direction | Message | Meaning |
|-----------|---------|---------|
| Server → Client | JSON snapshot (below) | State changed |
| Server → Client | `{"ping": true}` | 30 s keepalive |
| Client → Server | `"ping"` | Keepalive from client |
| Server → Client | `"pong"` | Keepalive reply |

Connection closes automatically (`code 1000`) once `status` is
`completed` or `failed`.

#### Snapshot schema

```jsonc
{
  "job_id":            "ostep-pipeline",
  "phase":             "idle" | "scrape" | "merge",
  "status":            "pending" | "running" | "completed" | "failed",

  // Scrape progress — increments live after every chapter
  "chapters_found":    57,
  "chapters_fetched":  42,
  "assets_downloaded": 310,
  "scrape_output":     "ostep_offline/ostep_complete.html",

  // Merge progress — increments live after every PDF
  "chapters_merged":   30,
  "total_pages":       820,
  "file_size_mb":      0.0,
  "merge_output":      null,

  "missing_files":     [],
  "error":             null,
  "started_at":        "2024-01-01T10:00:00+00:00",
  "updated_at":        "2024-01-01T10:04:55+00:00"
}
```

#### JavaScript client example

```javascript
const ws = new WebSocket("ws://localhost:8000/api/v1/ws/status");

ws.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.ping) { ws.send("ping"); return; }

  console.log(
    `[${msg.phase}] ${msg.status} — ` +
    `chapters: ${msg.chapters_fetched}/${msg.chapters_found} | ` +
    `assets: ${msg.assets_downloaded}`
  );

  if (msg.status === "completed" || msg.status === "failed") {
    console.log("Pipeline finished:", msg);
    ws.close();
  }
};
```

---

### Chapters & Assets

```
GET /api/v1/chapters/                         list from live index
GET /api/v1/chapters/assets                   all downloaded assets
GET /api/v1/chapters/assets?category=pdfs     filter by category
```

Asset categories: `code` · `images` · `pdfs` · `homework`

---

## Typical Workflow

```bash
# 1. Start the server
uvicorn main:app --reload

# 2. Connect WebSocket for live updates (separate terminal, needs wscat)
#    npm install -g wscat
wscat -c ws://localhost:8000/api/v1/ws/status

# 3. Start scrape — one call only while idle
curl -s -X POST http://localhost:8000/api/v1/scrape/start \
     -H "Content-Type: application/json" -d '{}' | python -m json.tool

# 4. (Or) poll REST status
curl -s http://localhost:8000/api/v1/scrape/status | python -m json.tool

# 5. Open compiled book after scrape completes
open ostep_offline/ostep_complete.html

# 6. Start merge (same job_id, requires scrape completed)
curl -s -X POST http://localhost:8000/api/v1/merge/start \
     -H "Content-Type: application/json" \
     -d '{"include_extras": true}' | python -m json.tool

# 7. Open merged PDF
open ostep_offline/OSTEP_Complete_Book.pdf
```

---

## Configuration (`.env`)

| Key               | Default                                   | Description                       |
|-------------------|-------------------------------------------|-----------------------------------|
| `APP_NAME`        | `OSTEP Offline Builder API`               | Shown in docs title               |
| `APP_VERSION`     | `1.0.0`                                   |                                   |
| `DEBUG`           | `false`                                   | Verbose logging + auto-reload     |
| `HOST`            | `0.0.0.0`                                 | Bind address                      |
| `PORT`            | `8000`                                    | Listen port                       |
| `OSTEP_INDEX_URL` | `https://pages.cs.wisc.edu/~remzi/OSTEP/` | Book index URL                    |
| `REQUEST_DELAY`   | `1.2`                                     | Seconds between HTTP requests     |
| `REQUEST_TIMEOUT` | `25`                                      | Per-request timeout (seconds)     |
| `USER_AGENT`      | `OSTEP-OfflineBuilder/2.0 ...`            | HTTP User-Agent string            |
| `OUTPUT_DIR`      | `ostep_offline`                           | Root output directory             |
| `ASSETS_SUBDIR`   | `ostep_assets`                            | Asset subdirectory name           |

---

## Output Files

```
ostep_offline/
├── ostep_complete.html          ← open in any browser (offline ready)
├── OSTEP_Complete_Book.pdf      ← combined PDF (after merge)
├── scrape.log                   ← full run log
└── ostep_assets/
    ├── pdfs/                    ← individual chapter PDFs
    ├── images/                  ← embedded images
    ├── code/                    ← source code files
    └── homework/                ← homework archives
```

---

## Architecture Notes

**Single pipeline job** — `PIPELINE_JOB_ID = "ostep-pipeline"` is a fixed
constant. `is_active()` is the only gate needed to enforce the one-at-a-time
rule.

**Thread-safe WebSocket fanout** — `_fanout()` runs in background threads but
dispatches to each subscriber's asyncio event loop via
`loop.call_soon_threadsafe`, making it safe across the thread/asyncio boundary.

**Live progress** — both `OSTEPScraper` and `PDFMerger` accept an optional
`store` reference and call `store.update()` after every chapter, so WebSocket
clients see incrementing counters in real time rather than just start/end events.

**robots.txt compliance** — enforced per host before every download.

**Rate limiting** — configurable via `REQUEST_DELAY` in `.env` (default 1.2 s).

For production scale, replace `services/job_store.py` with a Redis-backed
Celery worker and `redis-py` pub/sub for WebSocket fanout.

---

## License

This tool is for **personal educational use only**.
OSTEP content is © Remzi H. Arpaci-Dusseau & Andrea C. Arpaci-Dusseau.
