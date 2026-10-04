# Antarctica Weather API

Full-stack weather data platform for Antarctic wind-farm feasibility analysis. It
retrieves, caches, processes and visualises historical meteorological data from the
**AEMET OpenData API** through a FastAPI backend and a React dashboard.

---

## Table of contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Requirements](#3-requirements)
4. [Technologies](#4-technologies)
5. [Project structure](#5-project-structure)
6. [Configuration](#6-configuration)
7. [Running the application](#7-running-the-application)
8. [API endpoints](#8-api-endpoints)
9. [AEMET integration](#9-aemet-integration)
10. [Data aggregation](#10-data-aggregation)
11. [Timezones and DST](#11-timezones-and-dst)
12. [Database and caching strategy](#12-database-and-caching-strategy)
13. [Testing](#13-testing)
14. [Logging](#14-logging)
15. [Frontend](#15-frontend)
16. [Design decisions](#16-design-decisions)
17. [Limitations](#17-limitations)
18. [Future improvements](#18-future-improvements)
19. [TODO](#19-todo)

---

## 1. Overview

The service exposes historical observations from the AEMET OpenData API for the
Antarctic stations named in the challenge (**Gabriel de Castilla**, `89064`, and
**Juan Carlos I**, `89070`) and serves them to an analytics dashboard.

It combines:

- a **FastAPI** backend that validates, caches, aggregates and converts timezones;
- a **SQLite** cache that keeps the source API from being queried once per request;
- a **React + TypeScript** single-page dashboard for exploring the series;
- **Pandas** for filtering, resampling and timezone conversion;
- a **117-test** suite covering the caching, timezone, validation and resilience
  constraints of the brief.

Requests are served from the cache whenever the requested window is already covered
and still fresh, which bounds the upstream cost to roughly one AEMET call per station
per TTL window.

---

## 2. Architecture

Decoupled client-server layout. The backend owns all business rules; the frontend
only renders what the API returns.

```text
                    +-----------------+
                    |    React SPA    |
                    |    Port 5173    |
                    +--------+--------+
                             |
                            HTTP
                             |
                             v
                    +-----------------+
                    |     FastAPI     |
                    |    Port 8000    |
                    +--------+--------+
                             | |
                    +--------+ +--------+
                    v                   v
             +--------------+    +--------------+
             |    SQLite    |    |    AEMET     |
             |    Cache     |    |  OpenData    |
             +--------------+    +--------------+
```

Layering inside `backend/`:

```text
routes.py  ->  services/  ->  AEMET / Pandas
     |
     +------->  models/database.py  ->  SQLite
     |
     +------->  core/  (config, errors, logging, stations)
```

`api/routes.py` stays thin: transport concerns and cache decisions only. Everything
that reshapes data lives in `services/`.

### Request flow

1. The dashboard requests a station, a date range and optional filters.
2. FastAPI validates the path and query parameters (`400` / `422` on bad input).
3. The backend resolves the timezone and converts the window to UTC.
4. The cache is consulted: a hit requires full window coverage **and** a fresh
   station.
5. On a miss, AEMET is queried, the payload is trimmed to the window, and the rows
   are written inside one transaction together with their coverage span.
6. Pandas applies the `data_types` filter, the aggregation and the conversion to
   `Europe/Madrid`.
7. The dataset is returned to the dashboard.

---

## 3. Requirements

| Requirement               | Version  |
| ------------------------- | -------- |
| Python                    | `3.11+`  |
| Node.js                   | `18+`    |
| AEMET OpenData API key    | Required |

### AEMET API key

A personal e-mail account is recommended when registering at
<https://opendata.aemet.es/centrodedescargas/inicio>; corporate addresses are
sometimes rejected by the agency.

### Timezone data

`tzdata` is pinned in `requirements.txt` so `Europe/Madrid` conversion and DST
boundaries behave identically on Windows, macOS and Linux.

---

## 4. Technologies

### Backend

| Technology    | Purpose                                      |
| ------------- | -------------------------------------------- |
| Python        | Core language                                 |
| FastAPI       | REST framework, automatic OpenAPI docs        |
| Uvicorn       | ASGI server                                   |
| Pandas        | Filtering, aggregation, timezone conversion  |
| SQLite        | Local cache database                          |
| SQLAlchemy    | ORM and engine configuration                  |
| Pytest        | Automated testing                             |
| Pydantic      | Request validation, brought in by FastAPI     |

### Frontend

| Technology    | Purpose                       |
| ------------- | ----------------------------- |
| React 18      | UI framework                  |
| TypeScript    | Type-safe development         |
| Vite          | Build tooling and dev server  |
| Tailwind CSS  | Styling                       |
| Recharts      | Data visualisation            |
| lucide-react  | Icon set                      |
| ESLint        | Linting                       |
| Vitest + Testing Library | Frontend component testing |

> The API does not declare Pydantic response models: endpoints return plain
> dictionaries and the OpenAPI contract documents the shape by hand. Pydantic is
> only present because FastAPI depends on it for request validation.

---

## 5. Project structure

```text
.
├── .env                          # Credentials. Not versioned.
├── .gitignore
├── pyproject.toml                # pytest config (pythonpath, testpaths).
├── README.md
├── venv/                         # Virtual environment. Not versioned.
│
├── backend/
│   ├── main.py                   # FastAPI app: CORS, metadata, /health.
│   ├── requirements.txt
│   │
│   ├── api/
│   │   └── routes.py             # Endpoints and cache decisions
│   │                             # (coverage, freshness, refill, validation).
│   │
│   ├── core/
│   │   ├── config.py             # AEMET_CACHE_TTL_MINUTES.
│   │   ├── exceptions.py         # ConfigurationError, UpstreamAEMETError.
│   │   ├── logger.py             # Centralised logging.
│   │   └── stations.py           # Station registry and canonical ids.
│   │
│   ├── models/
│   │   └── database.py           # Engine, WAL pragmas, ORM models and the
│   │                             # schema bootstrap run on every startup.
│   │
│   ├── services/
│   │   ├── aemet_client.py       # AEMET two-step HATEOAS client, timeouts
│   │   │                         # and bounded retries.
│   │   └── data_processor.py     # Pandas: data_types, aggregation, DST.
│   │
│   ├── tests/                    # 117 tests, in-memory SQLite.
│   │   ├── conftest.py           # Fixtures + network guard.
│   │   ├── test_cache.py         # Window filling and row replacement.
│   │   ├── test_cache_hardening.py  # TTL, concurrency, purity.
│   │   ├── test_dst.py           # 2024 DST transitions.
│   │   ├── test_main.py          # App wiring, CORS, /health.
│   │   ├── test_regressions.py   # Guards for the regressions that were fixed.
│   │   ├── test_stations.py      # Registry and canonicalisation.
│   │   └── test_upstream.py      # AEMET failure semantics.
│   │
│   └── meteo_cache.db            # SQLite cache. Generated, not versioned.
│
└── frontend/
    ├── index.html
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js        # Tailwind + lucide-react icon paths.
    ├── postcss.config.js
    ├── eslint.config.js
    ├── tsconfig.json             # References tsconfig.app.json / tsconfig.node.json.
    │
    ├── public/                   # favicon.svg, icons.svg.
    │
    └── src/
        ├── main.tsx              # Entry point.
        ├── App.tsx               # Layout and filter state.
        ├── index.css             # Tailwind layers and base styles.
        ├── components/
        │   ├── Dashboard.tsx     # Station picker, aggregation, date range.
        │   └── WeatherChart.tsx  # Recharts series + raw data table.
        ├── services/
        │   └── apiService.ts     # HTTP client for the backend.
        └── types/
            └── api.ts            # Response typings.
```

`frontend/dist/`, `node_modules/`, `__pycache__/` and `.pytest_cache/` are generated
and excluded from version control.

`frontend/src/assets/` still holds the Vite boilerplate (`hero.png`, `react.svg`,
`vite.svg`) and `frontend/src/App.css` is no longer imported; both are dead weight
from the template.

---

## 6. Configuration

Create a `.env` file **at the repository root**:

```env
AEMET_API_KEY=your_personal_api_key_here
```

Keeping it there rather than inside `backend/` matches what the error message on a
missing key tells the operator, and keeps the credential out of version control.

### Environment variables

| Variable                     | Default                                              | Purpose                                              |
| ---------------------------- | ---------------------------------------------------- | ---------------------------------------------------- |
| `AEMET_API_KEY`              | *(none)*                                            | Credential for the AEMET OpenData API.              |
| `AEMET_CACHE_TTL_MINUTES`    | `60`                                                | How long a station is considered fresh.             |
| `CORS_ORIGINS`               | `http://localhost:5173,http://127.0.0.1:5173`        | Comma-separated dashboard origins.                   |
| `AEMET_DB_PATH`              | `backend/meteo_cache.db`                            | Overrides the cache file location.                   |

Invalid or non-positive `AEMET_CACHE_TTL_MINUTES` falls back to `60` rather than
failing the process.

The cache path is resolved from the module location, never from the working
directory, so launching the app from the repository root or from `backend/` opens
the same file instead of silently creating two databases.

---

## 7. Running the application

Two independent services must run concurrently.

### Backend

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

pip install -r backend/requirements.txt
```

```bash
cd backend
uvicorn main:app --reload
```

The API is then available at `http://localhost:8000`, with interactive docs at
`http://localhost:8000/docs`.

The SQLite cache file is created automatically on first run, together with its
tables.

### Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The dashboard is then available at `http://localhost:5173`.

### Tests

```bash
pytest
```

`pyproject.toml` puts `backend` on the path and points `testpaths` at
`backend/tests`, so the suite is meant to be run from the repository root. Running
`pytest` from inside `backend/` works too.

---

## 8. API endpoints

| Method | Path                                      | Purpose                     |
| ------ | ----------------------------------------- | --------------------------- |
| GET    | `/health`                                 | Liveness probe.             |
| GET    | `/api/antartida/estaciones`               | Station registry.           |
| GET    | `/api/antartida/datos/fechaini/...`       | Observation time series.    |

### 8.1 `GET /health`

```json
{ "status": "ok" }
```

Deliberately touches neither AEMET nor SQLite. A load balancer has to be able to
tell "this instance is broken" apart from "AEMET is down", and a probe that
consulted either would go red for both.

### 8.2 `GET /api/antartida/estaciones`

```json
{
  "status": "success",
  "data": [
    { "id": "89064", "name": "Meteo Station Gabriel de Castilla" },
    { "id": "89070", "name": "Meteo Station Juan Carlos I" }
  ]
}
```

Reads the curated registry only, so it stays available during an upstream outage.
The data endpoint additionally accepts any other AEMET station code, which resolves
untouched.

### 8.3 `GET /api/antartida/datos/fechaini/{ini}/fechafin/{fin}/estacion/{id}`

```http
GET /api/antartida/datos/fechaini/2024-01-01T00:00:00/fechafin/2024-01-02T00:00:00/estacion/89064
```

#### Path parameters

| Parameter | Description                                                |
| --------- | ---------------------------------------------------------- |
| `ini`     | Inclusive start, `YYYY-MM-DDTHH:MM:SS` (UTC suffix allowed). |
| `fin`     | Inclusive end, same format.                               |
| `id`      | Station code or name, case-insensitive.                    |

#### Query parameters

| Parameter     | Description                                                              |
| ------------- | ------------------------------------------------------------------------ |
| `aggregation` | `None` (native 10-minute), `Hourly`, `Daily` or `Monthly`. Default `None`. |
| `data_types`  | Repeatable: `temperature`, `pressure`, `speed`. Omit for all.           |
| `location`    | IANA zone (`Europe/Berlin`) or fixed offset (`+02:00`). Default `UTC`.   |

#### Response

```json
{
  "status": "success",
  "station_requested": "Gabriel de Castilla",
  "station_resolved": "89064",
  "location_requested": "Europe/Berlin",
  "location_resolved": "Europe/Madrid",
  "data_types_filtered": ["temperature"],
  "data": [
    {
      "Station": "Meteo Station Gabriel de Castilla",
      "Datetime": "2024-01-01T01:00:00+01:00",
      "Temperature (ºC)": 2.4
    }
  ]
}
```

Column names come from the AEMET field names: `Station`, `Datetime`,
`Temperature (ºC)`, `Pressure (hpa)` and `Speed (m/s)`.

A station may be addressed by name or by code. Both spellings resolve to the same
canonical code, so they share one cache entry, and both are echoed in the response
so the resolution stays auditable from outside.

#### Error contract

| Status  | Cause                                                                |
| ------- | -------------------------------------------------------------------- |
| `400`   | Malformed dates, `fin` before `ini`, or an unknown `location`.       |
| `422`   | Invalid `aggregation` or `data_types`.                               |
| `502`   | AEMET unreachable or failing.                                        |
| `503`   | `AEMET_API_KEY` not configured on this deployment.                   |

`503` is kept separate from `502` on purpose: a missing credential is a local
misconfiguration, and reporting it as a gateway failure would send an operator to
investigate the wrong party.

---

## 9. AEMET integration

The API is consumed through its two-step HATEOAS flow:

1. `GET /opendata/api/antartida/datos/fechaini/{ini}/fechafin/{fin}/estacion/{code}`
   returns a JSON envelope with `estado`, `descripcion` and a temporary `datos` URL.
2. That `datos` URL is fetched to obtain the observations.

Envelope handling:

- `estado != 200` raises `UpstreamAEMETError`;
- a missing `datos` URL is a **valid empty answer**, not a failure;
- a payload that is not valid JSON raises, instead of being ingested as an empty
  dataset and mistaken for "the station published nothing".

### Resilience

- `REQUEST_TIMEOUT = 10` seconds per round trip. Without it a stalled AEMET
  connection would pin the API worker instead of letting it answer `502`.
- `MAX_ATTEMPTS = 3` with linear backoff, applied only to connection and timeout
  failures. A `4xx`/`5xx` returns immediately, since repeating it changes nothing.
- Every upstream failure surfaces as `502` rather than as `200` with an empty
  dataset, so an outage cannot be misread as a period with no measurements.

---

## 10. Data aggregation

AEMET publishes at a **10-minute** native granularity. `aggregation` resamples with
Pandas:

| Value     | Bucket                     |
| --------- | -------------------------- |
| `None`    | Native 10-minute samples.  |
| `Hourly`  | Europe/Madrid calendar hour. |
| `Daily`   | Europe/Madrid calendar day.  |
| `Monthly` | Europe/Madrid calendar month. |

Numeric variables (`Temperature (ºC)`, `Pressure (hpa)`, `Speed (m/s)`) are averaged;
`Station` takes the first value of the bucket. Buckets are aligned on
`Europe/Madrid` boundaries, not on UTC ones, so a daily aggregate matches the
analysts' working day.

### Data integrity

Completely empty buckets are dropped to keep the payload small. **Partial buckets
are kept**: if temperature and speed exist but pressure does not, the row is still
returned, because it carries valid measurements.

---

## 11. Timezones and DST

### Input

`location` sets the zone the submitted datetimes are expressed in:

```text
Europe/Berlin
Europe/Madrid
+02:00
-05:30
```

The zone is resolved to an offset and the window is converted to **UTC** before
touching AEMET. Anything that is neither a known IANA name nor a valid `±HH:MM`
offset is rejected with `400`.

Note that `CET` and `EST` are legitimate IANA names, so they are resolved as fixed
offsets rather than rejected.

### Output

The brief fixes the output to `Europe/Madrid` regardless of the input zone, so
`Datetime` always carries the correct CET/CEST offset for the instant it describes.
The response echoes `location_requested` and `location_resolved` separately, which
keeps the conversion auditable from outside.

### Daylight saving time

Converting a local window to absolute instants across a DST transition is genuinely
ambiguous, and the behaviour is pinned by tests:

- **Spring forward** — the non-existent local hour is removed from the window;
- **Autumn back** — the repeated local hour is honoured in both passes, so the
  duplicated instants are preserved.

---

## 12. Database and caching strategy

```text
SQLite + SQLAlchemy ORM
```

Three tables, all created by the bootstrap that runs on every startup:

| Table                 | Purpose                                                              |
| --------------------- | -------------------------------------------------------------------- |
| `meteo_records`       | The observations. Unique on `(station_id, timestamp)`.                |
| `station_cache_state` | Per-station `last_fetched_at`, i.e. the freshness stamp.             |
| `station_coverage`    | Per-station verified windows, so coverage gaps stay visible.          |

### Cache coverage and freshness

A request is served from SQLite only when both hold:

1. a single coverage span contains the whole requested window, **and**
2. the station's freshness stamp is within `AEMET_CACHE_TTL_MINUTES`.

Coverage alone would freeze a window forever once filled, which is unusable for
intraday work: AEMET revises historical data several times a day, and "the last 3
hours" must keep moving. The freshness stamp bounds upstream cost to one call per
station per TTL window while guaranteeing no hit is ever older than the TTL.

### Why coverage is stored, not derived

Coverage used to be inferred from `MIN(timestamp)`/`MAX(timestamp)`, which only
proves the outer bounds. Two windows cached a few hours apart collapse into a
single range, so a request spanning the space between them looked fully covered,
and because freshness is tracked per station the stamp from the first window also
made the wider one look freshly fetched. The hole was served as a hit and never
refilled.

`station_coverage` keeps each verified window as its own row, which makes
"one row contains the window" a precise test. Spans are stored disjoint and never
grid-adjacent, so day-by-day ingestion collapses into a single span instead of
accumulating a row per day.

### Gap-aware refill

Only the missing part of a window is requested upstream. Everything from the first
hole to the end of the window is taken in one call, so several holes close at once.
This is the common case for a rolling "last N hours" query, where most of the
window is already cached and valid.

An **expired** station refetches end to end regardless. AEMET revises historical
values, so refreshing only the tail would leave the cached head pinned to a
superseded revision indefinitely.

### Idempotent ingestion

`DELETE` and `INSERT` share a single transaction, so a window is replaced atomically
and a refresh can never duplicate rows or leave a window half-written. Rows outside
the refill range are preserved. A unique constraint on `(station_id, timestamp)`
backs this at the schema level, and a writer that loses the race rolls back and
serves what the winner stored instead of returning `500`.

### SQLite pragmas

```text
journal_mode = WAL
synchronous  = NORMAL
busy_timeout = 5000
```

WAL is what removes writer contention: readers no longer block the writer, which
matters because the service is read-heavy.

The pragmas are applied on the engine's `connect` event, not once at import.
`journal_mode` is stored in the database header and survives, but `synchronous` is a
per-connection setting: running it against a single connection would leave every
later request on the default `FULL`, silently negating the pragma.

---

## 13. Testing

### Backend

```bash
pytest
```

117 tests covering cache hardening, coverage spans, DST boundaries, endpoint
contracts, parameter validation, timezone resolution and upstream failure
semantics.

Two properties are enforced by the suite rather than by convention:

- **The real cache is never touched.** Tests run against an in-memory SQLite
  database injected through FastAPI's dependency overrides, so `meteo_cache.db`
  cannot be modified by a test run.
- **No test reaches the network.** An autouse fixture blocks outbound HTTP at the
  `requests` transport and fails the test that tries. Without it a test that forgets
  to stub `fetch_aemet_data` does not fail loudly, it quietly succeeds against the
  live API using whatever key happens to be in the developer's `.env`.

`backend/tests/test_regressions.py` holds a guard per regression that was found and
fixed, so none of them can come back silently.

### Frontend

The frontend includes isolated component tests using Vitest and React Testing Library to ensure correct rendering, loading states, and error handling without reaching the real network.

```bash
cd frontend
npm run test
```

---

## 14. Logging

Centralised through `core.logger`, one named logger per layer (`routes`,
`database`, `aemet_client`). Recorded events include:

- cache `HIT` / `MISS` with the reason, the station and the affected window;
- the coverage spans considered and the instant a refill started from;
- freshness stamps;
- upstream request status and retry attempts;
- rows ingested, rows discarded as out of window, and short upstream answers.

Coverage gaps and short payloads are logged as warnings rather than swallowed,
because a silently under-reported gap is the kind of defect that stays invisible
until someone trusts the data.

---

## 15. Frontend

A React 18 + TypeScript single-page app built with Vite and Tailwind CSS.

- The station selector is populated from `GET /api/antartida/estaciones`, so no
  identifier is hardcoded and an invalid selection is not possible.
- Analysts switch between an interactive Recharts time series and a raw data table,
  for pattern recognition and precise inspection respectively.
- Empty datasets, including periods with a station transmission blackout, render a
  clear "No data" state instead of breaking.
- `data_types` and `aggregation` map directly to the endpoint's query parameters.

### Known frontend issues

- `src/services/apiService.ts` hardcodes `http://localhost:8000` instead of reading
  a `VITE_API_URL` environment variable, so a deployed build cannot be repointed
  without editing source.
- Requests are not cancelled. Rapidly changing filters can let a slower earlier
  response overwrite a newer one.
- The table renders every row without virtualisation, and computes its extremes with
  `Math.max(...rows)`. A full year of native 10-minute data is ~52,600 rows, which
  is survivable but heavy; the spread would only overflow past ~125,000 rows
  (roughly 2.4 years), at which point it throws instead of degrading.
- `Station` is declared as required in `src/types/api.ts`, but the backend omits
  the column when the upstream payload carries no label.

---

## 16. Design decisions

### Pandas for data manipulation

Chosen over hand-written loops because its vectorised operations handle filtering,
resampling and timezone conversion directly, including the DST edge cases that are
awkward to get right by hand.

### SQLite with WAL

Zero configuration, excellent portability, and enough performance for a
single-instance analytical cache. WAL is enabled at the connection level to allow
concurrent readers during writes.

### Coverage as explicit intervals

Recording verified windows per station, rather than deriving coverage from the row
extremes, is what makes a hole in the middle of a cached range detectable at all.
The alternative — comparing the stored row count against the theoretical number of
grid instants — looks stricter but is unsafe: AEMET stations have genuine sensor
gaps, and every one of them would read as a permanent cache miss, turning the
endpoint into a source of unbounded upstream traffic. Recording the window that was
*asked for* treats an upstream gap as the upstream's answer, and anything genuinely
missing is still repaired within one TTL because an expired span is always refetched
end to end.

### Dictionary responses

Endpoints return plain dictionaries rather than Pydantic response models. The
payload shape is small, stable and already covered by contract tests, and the
processor can legitimately omit a column depending on the data, which a strict
response model would reject.

### Stations keyed by canonical code

A station may be addressed by name or by code, and both spellings reach the cache
through `canonical_station_id`. Without it the same physical station would be
stored once per spelling and every cache hit would depend on how the caller
spelled it.

---

## 17. Limitations

### 1. No pagination

`aggregation=None` over a long window returns every 10-minute sample in one
response: roughly 52,600 rows for a full year. The frontend renders them all. This
is the largest remaining performance risk.

### 2. Thundering herd on a cold cache

Concurrent requests for the same uncached station all reach AEMET before the first
transaction commits. The unique constraint prevents duplicate rows, but not
duplicate upstream calls. Rows stay correct; the cost is wasted requests.

### 3. Coverage is verified per window, not per slot

A span records the window that was requested, not the individual samples received,
by design (see section 16). A transient upstream gap inside a single fetch is
therefore accepted until the TTL expires, at which point the window is refetched end
to end. Anomalies are logged and the sample count is stored on the span, but nothing
rejects a hit on that basis.

### 4. Single instance

The cache is a local SQLite file, so it only works behind one process or one
shared volume. Horizontal scaling needs a shared cache.

### 5. Cache growth

There is no retention policy. `meteo_records` only grows, which over time makes the
per-station coverage queries and the index slower.

---

## 18. Future improvements

- **Pagination** on the data endpoint, with a documented default page size, so a
  wide unaggregated query cannot return an unbounded payload.
- **Single-flight** per station, using an in-process lock or a Redis lock, to
  collapse concurrent cold requests into a single upstream call.
- **Retention policy** for `meteo_records`, keeping recent data hot and pruning the
  rest.
- **Distributed cache** if the backend is ever scaled beyond one instance.
- **Dockerisation** of both services plus a `docker-compose.yml` for one-command
  startup.
- **CI** running `pytest`, `npm run lint` and `npm run build` on every push.

---

## 19. TODO

### Backend

- [ ] Add pagination to the data endpoint.
- [ ] Add a per-station lock to collapse concurrent cold-start requests.
- [ ] Add retention/pruning for `meteo_records`.
- [ ] Replace hardcoded mock data in the test suite with generated fixtures.
- [ ] Introduce property-based testing for the timezone and aggregation logic.
- [ ] Document the `station_coverage` table in the schema bootstrap docstring.

### Frontend

- [ ] Add `AbortController` to cancel stale requests when filters change quickly.
- [ ] Paginate or virtualise the raw data table.
- [ ] Make `Station` optional in `src/types/api.ts`, matching the backend.
- [ ] Remove the Vite boilerplate: `src/assets/`, `src/App.css`, `frontend/README.md`.