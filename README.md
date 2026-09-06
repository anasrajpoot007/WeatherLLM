# WeatherLLM

An end-to-end live-data LLMOps pipeline: pull weather data from the US National
Weather Service API on a schedule, model it as a relational database, prepare the
narrative text for retrieval, embed it, and serve both a dashboard and a semantic
search interface.

**Source:** [api.weather.gov](https://www.weather.gov/documentation/services-web-api) —
free, no API key, updated continuously.

---

## Architecture

```
                    ┌──────────────────────────┐
                    │   api.weather.gov (NWS)   │
                    │  points · stations ·      │
                    │  observations · forecasts │
                    │  · alerts                 │
                    └───────────┬───────────────┘
                                │  src/nws_client.py
                                │  bounded retries, backoff, 429 handling
                                ▼
       ┌────────────────────────────────────────────────┐
       │  INGEST            src/ingest.py                │
       │  timestamped, immutable raw captures            │
       │  data/raw/{locations,observations,              │
       │            forecasts,alerts}_<ts>.json          │
       └───────────┬────────────────────────────────────┘
                   │  newest non-empty capture wins
                   ▼
       ┌────────────────────────────────────────────────┐
       │  ETL               src/load_data.py             │
       │  flatten · dedupe · upsert vs append            │
       │  ▶ weather.db  (SQLite, 7 tables, FK enforced)  │
       │  VALIDATE          src/validate.py              │
       └──────┬──────────────────────────┬──────────────┘
              │                          │
   numeric ───┘                          └─── narrative text
              │                                    │
              │                    ┌───────────────▼───────────────┐
              │                    │ CHUNK    src/prepare_text.py  │
              │                    │ type-aware, overlap, metadata │
              │                    ├───────────────────────────────┤
              │                    │ EMBED    src/embed.py         │
              │                    │ all-MiniLM-L6-v2, normalised  │
              │                    ├───────────────────────────────┤
              │                    │ INDEX    src/vector_search.py │
              │                    │ FAISS IndexFlatIP (cosine)    │
              │                    └───────────────┬───────────────┘
              │                                    │
              ▼                                    ▼
       ┌────────────────────────────────────────────────┐
       │  SERVE             src/api.py  (FastAPI)        │
       │  /api/locations /forecasts /observations        │
       │  /api/alerts   /stats   /ask                    │
       └───────────────────┬────────────────────────────┘
                           ▼
                 frontend/index.html
             dashboard + semantic search UI
```

---

## Quick start

```bash
git clone <this repo> && cd weatherLLM

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

# Identify yourself to NWS (required — it has no API key)
set NWS_CONTACT_EMAIL=you@example.com     # Windows
# export NWS_CONTACT_EMAIL=you@example.com

python src/pipeline.py           # full run: ingest → load → validate → embed → index
uvicorn src.api:app --reload     # then open http://127.0.0.1:8000
```

Individual stages:

```bash
python src/reconnaissance.py      # Step 1: inspect real API payloads
python src/ingest.py              # Step 2: fetch + write raw captures
python src/create_db.py           # Step 3: build the schema
python src/load_data.py           #         load newest captures
python src/validate.py            #         integrity checks
python src/prepare_text.py        # Step 4: chunk + tag
python src/embed.py               #         embed
python src/vector_search.py       # Step 5: build FAISS index
python src/search.py --eval       #         run evaluation queries
```

Useful flags:

| Flag | Effect |
|---|---|
| `python src/pipeline.py --skip-ingest` | Rebuild DB + index from existing captures, no API calls |
| `python src/pipeline.py --refresh-reference` | Re-resolve city → office/grid/station |
| `python src/create_db.py --reset` | Drop and rebuild all tables |
| `python src/search.py --verify` | Join every hit back to `weather.db` |

---

## Step 1 — Data contract

### Endpoints used

| Endpoint | Returns | Role |
|---|---|---|
| `/points/{lat},{lon}` | forecast office (`gridId`), `gridX`/`gridY`, `relativeLocation` | resolves a city to a grid cell |
| `/gridpoints/{office}/{x},{y}/stations` | array of observation stations, nearest first | picks a station per city |
| `/stations/{id}/observations/latest` | current reading | time-series source |
| `/gridpoints/{office}/{x},{y}/forecast` | 14 periods with `detailedForecast` | **narrative text** |
| `/alerts/active?area={state}` | active CAP alerts | **narrative text** |

**Auth:** none. NWS requires a `User-Agent` header with contact details instead
of an API key (`src/config.py` → `USER_AGENT`).
**Rate limit:** not published; the documented guidance is "reasonable limits,"
retry after ~5 seconds. `src/nws_client.py` handles 429 explicitly.

### Annotated sample — `/points/47.6062,-122.3321`

```jsonc
{
  "properties": {
    "gridId": "SEW",              // scalar — NATURAL KEY (forecast office)
    "gridX": 125,                 // scalar — NATURAL KEY (with gridY)
    "gridY": 68,
    "forecast": "https://...",    // scalar — URL to follow
    "observationStations": "https://...",
    "relativeLocation": {          // NESTED — city/state live one level down
      "properties": { "city": "Seattle", "state": "WA" }
    }
  }
}
```

### Annotated sample — observation (the messy one)

```jsonc
{
  "properties": {
    "timestamp": "2026-09-06T14:53:00+00:00",   // scalar — half the natural key
    "textDescription": "Mostly Cloudy",         // short prose
    "temperature": {                            // NESTED measurement wrapper
      "value": 12.2,                            // ← frequently null
      "unitCode": "wmoUnit:degC",
      "qualityControl": "V"
    },
    "relativeHumidity": { "value": null, ... }  // null is normal, not an error
  }
}
```

Every measurement is wrapped in `{value, unitCode, qualityControl}` and `value`
is null far more often than the docs suggest. `ingest.measurement()` flattens
this to a plain float or `None`.

### Annotated sample — alert (the text source)

```jsonc
{
  "properties": {
    "id": "urn:oid:2.49.0.1.840.0.abc123",  // NATURAL KEY (CAP identifier)
    "event": "Flood Warning",
    "severity": "Severe",
    "areaDesc": "Bell, Coryell, Falls...",
    "description": "* WHAT...Flooding caused by excessive rainfall...\n\n
                    * WHERE...A portion of central Texas...",  // ← 300–5000+ chars
    "instruction": "Turn around, don't drown when encountering flooded roads..."
  }
}
```

Run `python src/reconnaissance.py` to regenerate `docs/sample_responses.json`
with live payloads.

### Entity-relationship diagram

```
        ┌─────────────┐
        │   states    │  code PK, name
        └──┬───┬───┬──┘
           │   │   └──────────────────────────┐
           │   │                              │
    ┌──────▼───┐  ┌────────▼────────┐   ┌─────▼──────┐
    │ offices  │  │    locations    │   │   alerts   │
    │ idOffice │◄─┤ idLocation  PK  │   │ idAlert PK │
    │ PK       │  │ idOffice    FK  │   │ stateCode  │
    │ stateCode│  │ stateCode   FK  │   │ FK         │
    │ FK       │  │ gridX, gridY    │   │ description│  ← TEXT
    └──────────┘  └────────┬────────┘   │ instruction│  ← TEXT
                           │            └────────────┘
              ┌────────────┴────────────┐
              │                         │
       ┌──────▼───────┐        ┌────────▼─────────┐
       │   stations   │        │    forecasts     │
       │ idStation PK │        │ idForecast    PK │
       │ idLocation FK│        │ idLocation    FK │
       └──────┬───────┘        │ detailedForecast │  ← TEXT
              │                └──────────────────┘
       ┌──────▼─────────┐
       │  observations  │
       │ idObservation PK│
       │ idStation    FK │
       │ temperatureC…   │
       └────────────────┘
```

**Slow-changing reference data:** `states`, `offices`, `locations`, `stations`.
Grid cells and station assignments change rarely, so they are cached in
`data/reference_cache.json` and only re-fetched with `--refresh-reference`.

**Grows every fetch:** `observations` (one row per station per reading),
`forecasts` (14 periods per city per run), `alerts` (new CAP ids as weather
develops).

### Natural keys

| Table | Key | Source |
|---|---|---|
| `states` | `code` | NWS state abbreviation |
| `offices` | `idOffice` | `properties.gridId` |
| `locations` | `idLocation` | our slug (`seattle-wa`) — we choose the cities |
| `stations` | `idStation` | `stationIdentifier` |
| `observations` | `idObservation` | **derived**: `<station>@<timestamp>` |
| `forecasts` | `idForecast` | **derived**: `<location>@<startTime>` |
| `alerts` | `idAlert` | `properties.id` (CAP) |

Observations and forecasts have no id in the payload, so a deterministic
composite key is derived. Re-fetching the same reading produces the same key and
collides instead of duplicating — this is what makes the load idempotent.

### Known messiness, and the plan for it

| Problem | Handling |
|---|---|
| Measurements wrapped in `{value, unitCode, …}` | flattened by `measurement()` |
| `value` is frequently `null` | column stays nullable; `validate.py` reports coverage |
| Station may have no recent observation (404) | `nws_client` returns `None`, not a retry |
| Alerts array is often empty (calm weather) | legitimate — warning, never a failure |
| Alert text hard-wrapped at ~68 columns | `clean()` collapses intra-paragraph newlines |
| One alert spans several states | deduped on CAP id at ingest |
| Alert names an untracked state | state registered on the fly so the FK holds |

---

## Step 2 — Ingestion

**Retry strategy.** Bounded: 5 attempts, exponential backoff `2 → 4 → 8 → 16 →
30s` (capped). An unbounded `while True` retry — the obvious first implementation
— turns one dead endpoint into a scheduler that hangs forever with no output.

**Rate limits.** HTTP 429 sleeps 10s and retries *without consuming the attempt
budget*: the server is asking us to slow down, not reporting a failure. Counting
it as an attempt would abandon work the API was willing to serve.

**404 is not an error.** A station with no recent observation returns 404. It is
a legitimate answer, so it returns `None` immediately rather than burning five
retries on a resource that will never exist.

**Raw captures are immutable.** Every response is written to
`data/raw/<kind>_<timestamp>.json` before anything transforms it. When something
breaks downstream, the run can be replayed offline against the exact bytes that
caused it (`python src/pipeline.py --skip-ingest`). Cleaning happens on the way
into SQLite, never in place.

**Empty captures are never written.** A failed run that wrote `[]` would become
the "newest" file and silently blank the next load. `save_capture()` refuses to
write zero records, and the loader independently skips empty files — two
defences, because this bug is invisible until the database is already wrong.

**Call budget (15 cities):** ~30 reference calls (cached), then ~43 per run —
15 observations + 15 forecasts + ~13 alerts. A full run takes well under a
minute.

---

## Step 3 — ETL

**Two write strategies, chosen per table:**

- **UPSERT** (`ON CONFLICT DO UPDATE`) for reference tables. A station can be
  renamed or a city reassigned to another grid cell; we want current truth.
- **APPEND-IF-NEW** (`INSERT OR IGNORE`) for event tables. These are historical
  facts. A reading already recorded must never be rewritten, so the table
  accumulates history across runs.

Either way, running the load twice changes nothing — verified below.

**Database choice: SQLite.** The brief suggests comparing an embedded file-based
database against a client-server one. SQLite wins here because the whole dataset
is a few MB, the pipeline is single-writer, and a zero-configuration file makes
the repo genuinely runnable by a stranger — `git clone` and go, no service to
install. PostgreSQL would be the right call if this became multi-writer, needed
concurrent readers during a load, or if the corpus grew enough to want `pgvector`
keeping embeddings beside their rows. Documented as the first thing to revisit.

**Validation** (`src/validate.py`) reports row counts, orphaned FKs across all
seven relationships, `PRAGMA foreign_key_check`, duplicate natural keys, null
coverage, and how much embeddable text exists. Exits non-zero on a hard failure.
An empty `alerts` table is a warning, not a failure — calm weather is a valid
state of the world.

---

## Step 4 — Chunking, metadata, embeddings

**What gets embedded, and what deliberately does not.**

Embedded: `forecasts.detailedForecast`, `alerts.description`,
`alerts.instruction` — real forecaster and advisory prose.

**Not** embedded: every numeric column. Embedding a stringified number produces
a vector that encodes the *words*, so `"72 degrees"` and `"27 degrees"` land near
each other while meaning opposite things. Numbers belong in SQL where they can be
filtered and compared; the dashboard queries them directly. The vector index
answers questions about language.

**Chunking strategy — chosen per document type**, because the two text sources
have different shapes:

| Type | Length | Strategy | Why |
|---|---|---|---|
| Forecast narrative | 150–400 chars | keep whole | 1–3 sentences, already one coherent thought; splitting separates "high near 75" from the day it belongs to |
| Alert body | 300–5000+ chars | paragraph-first, then sliding window (120 words, 30 overlap) | multi-paragraph and covers several distinct instructions; overlap means a sentence crossing a boundary still appears intact in one chunk |

Rejected: fixed character windows (split mid-word, ignore structure) and
sentence-level chunks (too small — a lone forecast sentence lacks the location
context that makes it retrievable).

**Contextual prefix.** Every chunk is prefixed with
`"Seattle, WA — Tonight forecast:"`. Without it, *"Sunny with a high near 75"*
appears verbatim for a dozen cities and retrieval cannot tell them apart. The
prefix is embedded *and* recorded in metadata.

**Metadata for traceability.** Every chunk carries `source_table` and
`source_id` — the actual primary key of its source row — plus
`chunk_index`/`chunk_count`. Any result joins straight back:

```sql
SELECT * FROM forecasts WHERE idForecast = '<source_id>';
```

`python src/search.py --verify` does exactly this for every hit.

**Embedding model: `all-MiniLM-L6-v2`.** Runs locally on CPU — no key, no
per-run cost, nothing leaves the machine, which matters for something on a
schedule. 384 dimensions and ~90 MB; strong quality-per-byte on short-passage
retrieval, which is this corpus's shape. Rejected: OpenAI `text-embedding-3-small`
(bills every scheduled run and needs a key in the scheduler), `bge-large` (5× the
size, slower on CPU, marginal gain at this scale).

**Vectors are L2-normalised** so FAISS inner product *is* cosine similarity —
the metric MiniLM was trained for. Raw L2 distance on un-normalised vectors, the
naive default, lets magnitude leak into ranking.

Model name and revision are pinned in `src/config.py` and recorded in
`data/run_manifest.json` on every build, so an upgrade cannot silently change
results.

---

## Step 5 — Retrieval

**Index: FAISS `IndexFlatIP`** — exact search over normalised vectors. For a few
thousand chunks this is microseconds per query and returns exact nearest
neighbours. Approximate indexes (IVF, HNSW) only pay off in the hundreds of
thousands of vectors and would cost recall plus a training step for no measurable
gain here.

**Where vectors live: a local FAISS file.** The corpus rebuilds from the
relational tables in seconds, so a hosted vector database would add a process to
run, secure and pay for, with nothing gained. `sqlite-vec` and `pgvector` were
the alternatives — both keep vectors beside their rows, which is appealing, but
FAISS keeps the retrieval layer swappable without touching the schema. Revisit if
the corpus outgrows memory.

**Relevance threshold** is cosine ≥ 0.25 (`config.SIMILARITY_THRESHOLD`), tuned
against the evaluation queries in `src/search.py`. Below it, `/api/ask` reports
that nothing was relevant rather than returning confident noise.

**Graceful degradation.** If the index is missing or was built with a different
model, `/api/ask` falls back to SQL keyword search and says so in its `note`
field rather than failing.

---

## Step 6 — Interface

`frontend/index.html` is a single self-contained file served by FastAPI:

- **Cities** — card per location, colour-banded by temperature, click for the
  full 7-period forecast
- **Forecasts / Observations** — filterable tables over the relational data
- **Alerts** — severity-coded cards with expandable advisory text
- **Ask** — semantic search; every hit shows its cosine score and its
  `source_table → source_id` trace

Light and dark themes, responsive, no external dependencies.

---

## Verification

Verified against mock NWS payloads matching the real response shapes (15 cities,
45 observations, 105 forecast periods, 4 alerts with full-length CAP text):

| Check | Result |
|---|---|
| All 13 modules compile | pass |
| Schema creates, 7 tables, FKs enforced | pass |
| Load: 13 states, 13 offices, 15 locations, 15 stations, 45 obs, 105 forecasts, 4 alerts | pass |
| **Idempotency** — second load adds 0 rows | pass |
| `validate.py` — 0 orphans, 0 FK violations, 0 duplicate keys | pass |
| Chunking — 350-word doc → 4 chunks, 30-word overlap, 350/350 words covered | pass |
| Index build — 113 vectors, 384 dims, IndexFlatIP | pass |
| Retrieval — alert queries return alerts, forecast queries return forecasts | pass |
| **Traceability** — 9/9 hits joined back to `weather.db` | pass |
| All 9 API endpoints + 404/400 error paths | pass |
| Keyword fallback when index unavailable | pass |

Reproduce with `python src/pipeline.py` against live data.

---

## Scheduling

**Windows Task Scheduler** (see `run_pipeline.bat`):

1. Verify manually first: `run_pipeline.bat` → expect `PIPELINE RUN COMPLETED`.
2. Task Scheduler → **Create Task** (not Basic Task).
   - *General*: run whether logged on or not.
   - *Triggers*: daily, 06:00. Forecasts update a few times a day; hourly would
     re-fetch mostly-identical data.
   - *Actions*: program `run_pipeline.bat`; **Start in** = the project folder
     (leaving this blank is the usual cause of "works manually, fails scheduled").
   - *Settings*: run as soon as possible after a missed start.
3. Export it into the repo:
   `schtasks /query /tn "WeatherLLM Daily" /xml > docs/scheduler_task.xml`

Each run appends to `logs/pipeline_<date>.log`.

---

## Repo layout

```
weatherLLM/
├── src/
│   ├── config.py           paths, city list, pinned model, HTTP policy
│   ├── nws_client.py       retry / backoff / rate-limit policy
│   ├── reconnaissance.py   Step 1 — inspect live payloads
│   ├── ingest.py           Step 2 — raw captures
│   ├── create_db.py        Step 3 — schema
│   ├── load_data.py        Step 3 — ETL
│   ├── validate.py         Step 3 — integrity checks
│   ├── prepare_text.py     Step 4 — chunking + metadata
│   ├── embed.py            Step 4 — embeddings
│   ├── vector_search.py    Step 5 — FAISS index + run manifest
│   ├── search.py           Step 5 — CLI retrieval
│   ├── api.py              Step 6 — FastAPI
│   └── pipeline.py         orchestrator (streams + logs)
├── sql/schema.sql          7 tables, PK/FK
├── frontend/index.html     dashboard + search UI
├── data/raw/               immutable timestamped captures
├── docs/                   sample payloads, scheduler export
├── requirements.txt        pinned
└── run_pipeline.bat        scheduler entry point
```

---

## What I would do differently with more time

- **Postgres + pgvector.** Keeping embeddings beside their source rows removes
  the index/database sync problem entirely and makes hybrid filtered search
  (`WHERE state = 'TX'` *and* vector similarity) a single query.
- **Retrieval evaluation with labelled data.** `search.py --eval` is a smoke
  test, not a metric. Twenty labelled query→chunk pairs and recall@5 would turn
  chunking decisions into measurements instead of arguments.
- **Hourly observations, daily forecasts.** The two have different natural
  cadences and currently share one schedule.
- **An LLM answer layer.** `build_answer()` stitches retrieved chunks together.
  Feeding them to a small local model with a grounded prompt would produce real
  answers while keeping citations.
- **Backfill history.** `/products` exposes archived text products; the current
  pipeline only ever sees the present.
- **Unit tests.** `tests/` is a placeholder. `measurement()`, `split_document()`
  and `latest_capture()` are pure functions with clear edge cases.
