# Antarctica Wind Farm - AEMET API Service

## 1. Executive Summary
This project provides a full-stack web service to retrieve, aggregate, and visualize historical weather data from the AEMET API for meteorological stations in Antarctica. Developed to support the Analytical team's feasibility study for a future Wind Farm, the solution features a backend cache (SQLite) to manage high-volume trader requests without overloading the source API, alongside a frontend interface for data visualization.

## 2. Key Features (Core API)
- **Clean Architecture & Modular Design:** The codebase is structured into logical, decoupled layers (`api/`, `core/`, `models/`, `services/`). This separation of concerns ensures high maintainability, readability, and readiness for future scalability.
- **HATEOAS Integration:** Seamlessly handles the AEMET two-step data retrieval process.
- **Smart Database Caching (SQLite):** Implements an interception layer using SQLAlchemy ORM. Caches historical data locally to prevent source API overload, delivering millisecond response times for intraday traders (Cache Hit/Miss logic).
- **Station Canonicalisation:** A station may be requested by its name or by its AEMET internal code. Both spellings resolve to a single cache key, so one physical station is stored once instead of once per spelling.
- **Time-Bounded Freshness (TTL):** Each station records when it was last refreshed. A covered window older than the TTL is re-read, so the cache spares the source API without freezing the most recent hours of data.
- **Upstream Resilience:** Every upstream call carries a timeout and bounded retries. A failing AEMET service is reported as `502 Bad Gateway` instead of being disguised as a station with no observations.
- **Professional Logging:** Centralized tracking of cache operations, data ingestion, and system behavior to facilitate robust troubleshooting and monitoring.
- **Data Transformation:** Uses `pandas` for highly efficient filtering and column mapping.
- **Timezone & DST Handling:** Converts all UTC timestamps to `Europe/Madrid` (CET/CEST) dynamically, ensuring strict Daylight-Saving Time (DST) compliance. See [section 7](#7-daylight-saving-time-verification).
- **Time Aggregation:** Supports `Hourly`, `Daily`, and `Monthly` data resampling (calculating the mean of numerical variables) directly at the Madrid local midnight boundaries.
- **Referential Integrity:** A `UniqueConstraint` on `(station_id, timestamp)` makes ingestion idempotent, so even concurrent requests for the same window cannot store the same reading twice.
- **Defensive Programming & Validation:** Strict input validation for ISO 8601 date formats and temporal coherence. Invalid requests are caught at the API boundary, returning descriptive 400 Bad Request errors to prevent unnecessary downstream processing.

## 3. Prerequisites
- Python 3.9 or higher.
- A personal AEMET OpenData API Key (Corporate emails might be blocked by the agency).

The `tzdata` package is pinned in `requirements.txt` so the `Europe/Madrid` conversions and DST boundaries resolve identically on Windows, macOS and Linux.

## 4. Setup & Installation

**1. Clone the repository:**
```bash
git clone https://github.com/CesaarFrade/aemet-antartica
cd aemet-antartica/backend
```

**2. Create and activate a virtual environment:**
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
```

**3. Install dependencies:**
```bash
pip install -r requirements.txt
```

**4. Environment Variables:**
Create a `.env` file in the `backend` directory and add your AEMET API key. This ensures credentials remain secure and are not committed to version control:
```env
AEMET_API_KEY=your_personal_api_key_here
```

**Optional cache tuning:**

| Variable | Default | Purpose |
|---|---|---|
| `AEMET_CACHE_TTL_MINUTES` | `60` | How long a station's cached window is served before it is refreshed. Set it high to effectively disable refreshes. Invalid values fall back to the default. |

**5. First run:**
The SQLite cache (`meteo_cache.db`) is created automatically in the `backend` folder on first run and repopulates itself as stations are requested. It is a disposable artefact: deleting it only costs the next request per station.

## 5. Running the Service

Start the FastAPI server using Uvicorn:
```bash
uvicorn main:app --reload
```

## 6. API Documentation & Usage

```
GET /api/antartida/datos/fechaini/{fechaIniStr}/fechafin/{fechaFinStr}/estacion/{identificacion}
```

**Path parameters**

| Parameter | Description |
|---|---|
| `fechaIniStr` | Inclusive range start, `YYYY-MM-DDTHH:MM:SS`. A trailing `UTC` or `Z` is tolerated but not required. |
| `fechaFinStr` | Inclusive range end, same format. Must be strictly later than the start. |
| `identificacion` | Station name (`Meteo Station Gabriel de Castilla`) or AEMET code (`89064`). |

**Query parameters**

| Parameter | Values | Default | Description |
|---|---|---|---|
| `aggregation` | `None`, `Hourly`, `Daily`, `Monthly` | `None` | `None` keeps the native 10-minute granularity; the others average the numeric variables on local Madrid boundaries. |
| `data_types` | `temperature`, `pressure`, `speed` | all | Repeat the parameter to select several. `Station` and `Datetime` are always returned. |
| `location` | any IANA zone or offset | — | Accepted per the challenge spec but not applied: the output is always `Europe/Madrid`, and the response echoes both `location_requested` and `location_resolved`. |

**Response shape**

```json
{
  "status": "success",
  "station_requested": "Meteo Station Gabriel de Castilla",
  "station_resolved": "89064",
  "location_requested": null,
  "location_resolved": "Europe/Madrid",
  "data_types_filtered": "All",
  "data": [
    {
      "Station": "JCI Estacion meteorologica",
      "Datetime": "2024-01-01T01:00:00+01:00",
      "Temperature (ºC)": 2.4,
      "Pressure (hpa)": 990.8,
      "Speed (m/s)": 1.3
    }
  ]
}
```

`station_requested` echoes exactly what the caller sent, while `station_resolved` shows the canonical identifier used as the cache key. A cache hit returns a payload byte-identical to the cache miss it replaces.

**Error contract**

| Status | When | Body |
|---|---|---|
| `400` | Malformed date, or `fechaIniStr` not strictly before `fechaFinStr` | `{"detail": "..."}` |
| `422` | `aggregation` outside the allowed values | FastAPI validation error |
| `502` | AEMET unreachable, erroring, or returning an unusable payload | `{"detail": "Upstream AEMET API unavailable: ..."}` |
| `200` | Upstream answered successfully but published no observations | `{"status": "success", "data": []}` |

The 200/502 split is deliberate: an unreachable source API must never look like a station that simply published nothing.

Once the server is running, navigate to [http://localhost:8000/docs](http://localhost:8000/docs) in your web browser.

FastAPI automatically generates an interactive Swagger UI documentation where you can test the endpoints, pass datetime parameters, filter required data types, and review the timezone-aware JSON responses.

## 7. Daylight-Saving Time Verification

The challenge asks to confirm the API's behaviour during Daylight-Saving Time, so both 2024 transitions are pinned down by tests in `tests/test_dst.py`. Europe/Madrid switches CET (+01:00) to CEST (+02:00) at 02:00 local on 31 March 2024, and back at 03:00 local on 27 October 2024.

| Transition | Verified behaviour |
|---|---|
| **31 Mar 2024, 02:00 CET → CEST** | The local 02:00 hour never happens. Samples run `01:00+01:00`, `01:10+01:00` … `01:50+01:00` and then jump straight to `03:00+02:00`. Hourly resampling does not invent a bucket for the missing hour. |
| **27 Oct 2024, 03:00 CEST → CET** | The local 02:00 hour happens **twice**. Both passages are kept and distinguished only by their offset: `2024-10-27T02:00:00+02:00` and `2024-10-27T02:00:00+01:00`. |
| **A UTC day is not a Madrid day** | The station is two hours ahead of UTC and the clock moves in between, so `2024-03-31T00:00→23:00` UTC spans **two** Madrid days and yields **two** daily buckets. |
| **Boundaries carry their own offset** | Consecutive daily buckets correctly report different offsets: `2024-03-31T00:00:00+01:00` and `2024-04-01T00:00:00+02:00`. |

This is precisely why the UTC offset is part of every timestamp: in autumn the wall-clock time alone is ambiguous, and dropping either passage would silently lose an hour of observations.

## 8. Cache Policy

A request is served from SQLite only when **both** conditions hold:

1. **Coverage.** The cached observations span the whole requested window, compared on the 10-minute grid AEMET publishes on and against both inclusive bounds.
2. **Freshness.** The station was refreshed within `AEMET_CACHE_TTL_MINUTES`.

On a miss, the fetched rows **replace** the previous window instead of appending, inside a single transaction, and only after the payload has parsed successfully — a failed or truncated upstream response can never purge data already held. The station's freshness stamp is written in that same transaction, and neither an outage nor an empty answer refreshes it.

Known limitations, deliberately left as follow-up work:

- The coverage check compares the earliest and latest stored timestamps, so a hole in the **middle** of a window still counts as a hit. Closing it needs either a row-count comparison against the expected number of 10-minute slots, or explicit per-station coverage intervals.
- The refill is **all-or-nothing**, so an expired wide window refetches its whole span. Fetching only the uncovered or expired tail would cut that cost.

## 9. Testing

The suite runs entirely against an in-memory SQLite database injected through FastAPI's dependency overrides, so it never reads or writes the real `meteo_cache.db` and never reaches the network.

```bash
cd backend
pytest -q
```

**55 tests across 6 files:**

| File | Focus |
|---|---|
| `tests/conftest.py` | Shared fixtures: in-memory database, `TestClient`, mocked AEMET client, cache inspection |
| `tests/test_main.py` | Endpoint contract: date validation, `Literal` enforcement, aggregation and filtering |
| `tests/test_cache.py` | Cache hit/miss, deduplication, wider ranges, partially cached days, station scoping, logging |
| `tests/test_dst.py` | Both 2024 DST transitions and the aggregation boundaries around them |
| `tests/test_upstream.py` | Upstream failure semantics: timeouts, retries, `502` versus an empty dataset |
| `tests/test_cache_hardening.py` | Station canonicalisation, TTL expiry, schema constraints |

Several tests are explicit regression guards for defects found during the build, including a cache hit that duplicated every row, a wider range that was silently truncated, and a cache hit whose `Station` value differed from the equivalent cache miss.

## 10. Project Structure

```
backend/
├── api/
│   └── routes.py            # Endpoint: validation, cache orchestration, response
├── core/
│   ├── config.py            # Runtime configuration (cache TTL)
│   ├── exceptions.py        # Domain exceptions (upstream failures)
│   ├── logger.py            # Centralized logging
│   └── stations.py          # Station registry and canonical identifiers
├── models/
│   └── database.py          # SQLAlchemy models, constraints and schema migration
├── services/
│   ├── aemet_client.py      # AEMET two-step client: timeouts, retries, error semantics
│   └── data_processor.py    # pandas transformations, aggregation, timezone handling
├── tests/                   # pytest suite
├── main.py                  # FastAPI application
└── requirements.txt
```