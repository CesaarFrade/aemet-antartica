from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Literal, Optional
from datetime import datetime, timedelta, timezone

# Relative imports to the sibling layers
from services.aemet_client import fetch_aemet_data
from services.data_processor import process_weather_data
from models.database import SessionLocal, MeteoRecord, StationCacheState
from core.config import CACHE_TTL_MINUTES
from core.exceptions import UpstreamAEMETError
from core.logger import get_logger
from core.stations import canonical_station_id, get_all_stations

logger = get_logger("routes")
router = APIRouter()


def _utcnow() -> datetime:
    """
    Current UTC wall-clock as a naive datetime.

    The rest of the pipeline handles naive UTC timestamps, so freshness stamps are
    stored the same way to keep every comparison in a single frame of reference.
    `datetime.now(timezone.utc)` avoids the deprecated `utcnow()`.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_grid(dt: datetime) -> datetime:
    """
    Snaps a requested bound onto the 10-minute grid AEMET publishes on.

    Without this, a request ending at 23:59:59 never matches the last sample of
    the day (23:50) and the cache is refetched on every single call. Flooring is
    the right direction for the end bound; for the start bound it is marginally
    lenient, which is harmless while AEMET only emits grid-aligned timestamps.
    """
    return dt.replace(second=0, microsecond=0) - timedelta(minutes=dt.minute % 10)

# --- DATABASE DEPENDENCY ---
def get_db():
    """
    Yields a SQLAlchemy session scoped to a single request.

    FastAPI resolves this through `Depends`, so one session is opened per request
    and always released in the `finally` block, never leaking connections.
    The test suite overrides this dependency with an in-memory database.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/api/antartida/estaciones")
def list_stations():
    """
    Lists the Antarctic stations the service knows about.

    Supporting endpoint for the dashboard, so a client can populate a station picker
    instead of hardcoding identifiers. It reads the curated registry only and never
    touches the cache or AEMET, so it stays available even when the upstream is down.

    Returns:
        `{"status": "success", "data": [{"id": ..., "name": ...}, ...]}`.
    """
    return {
        "status": "success",
        "data": get_all_stations(),
    }


@router.get("/api/antartida/datos/fechaini/{fechaIniStr}/fechafin/{fechaFinStr}/estacion/{identificacion}")
def get_meteo_data(
    fechaIniStr: str,
    fechaFinStr: str,
    identificacion: str,
    # TODO: accepted for compatibility with the challenge spec but never applied.
    # Either convert the output to the requested zone, or state in the README that
    # the output is always Europe/Madrid and echo the resolved zone back.
    location: Optional[str] = Query(None, description="Time zone location, e.g., Europe/Berlin or +02:00"),
    # `Literal` is what actually enforces the allowed values and publishes them in
    # the OpenAPI schema; FastAPI's `Query(enum=...)` silently drops the kwarg.
    aggregation: Literal["None", "Hourly", "Daily", "Monthly"] = Query("None"),
    data_types: Optional[List[str]] = Query(None, description="List of required data types: temperature, pressure, speed"),
    db: Session = Depends(get_db)
):
    """
    Retrieves the time series of measurements for one station within a date range.

    Args:
        fechaIniStr: Inclusive range start, `YYYY-MM-DDTHH:MM:SS`, optional UTC suffix.
        fechaFinStr: Inclusive range end, same format.
        identificacion: Station name or AEMET station code.
        location: Accepted per the spec but currently ignored (see TODO above).
        aggregation: `None` keeps the native 10-minute granularity. `Hourly`,
            `Daily` and `Monthly` average the numeric variables on local
            Europe/Madrid boundaries.
        data_types: Any combination of `temperature`, `pressure` and `speed`.
            When omitted or empty, every available variable is returned.

    Returns:
        The requested station, the applied filter and the dataset, where `Datetime`
        is Europe/Madrid (CET/CEST) including its UTC offset.

    Raises:
        HTTPException: 400 when a date is malformed or the range is inverted.
            422 when `aggregation` or `data_types` are outside the allowed values.
            502 when the AEMET upstream fails; an upstream outage is never
            reported as an empty station.
    """
    # --- DATE VALIDATION ---
    # AEMET timestamps are UTC wall-clock strings, so the range is handled as naive
    # UTC throughout this layer and the CET/CEST conversion happens later, in the
    # data processor. A trailing `UTC` or `Z` is tolerated but not required.
    try:
        clean_ini = fechaIniStr.replace("UTC", "").replace("Z", "")
        clean_fin = fechaFinStr.replace("UTC", "").replace("Z", "")
        
        # Strict format: anything else is rejected rather than guessed
        dt_ini = datetime.strptime(clean_ini, "%Y-%m-%dT%H:%M:%S")
        dt_fin = datetime.strptime(clean_fin, "%Y-%m-%dT%H:%M:%S")
        
        if dt_ini >= dt_fin:
            raise HTTPException(
                status_code=400, 
                detail="Invalid date range: The start date must be strictly before the end date."
            )
            
    # NOTE: `HTTPException` does not inherit from `ValueError`, so the 400 raised
    # above propagates untouched instead of being rewritten as a format error.
    except ValueError:
        raise HTTPException(
            status_code=400, 
            detail="Invalid date format. Expected format: YYYY-MM-DDTHH:MM:SS (e.g., 2024-01-01T00:00:00UTC)"
        )
    # --- REQUEST LOGGING ---
    # The challenge accepts either the station name or its AEMET code, but both
    # spell the same physical station. Everything downstream (cache key, coverage
    # probe, AEMET request) uses the canonical code, so a station is stored once
    # instead of once per spelling. `station_requested` still echoes the raw input.
    station_key = canonical_station_id(identificacion)
    logger.info(
        f"Incoming request for station: '{identificacion}' (resolved: '{station_key}') "
        f"from {dt_ini} to {dt_fin}"
    )

    # --- CACHE COVERAGE AND FRESHNESS ---
    # A request is served from SQLite only when both conditions hold:
    #
    #   1. the stored observations span the whole requested window, and
    #   2. the station was refreshed recently enough.
    #
    # Coverage alone would freeze a window forever once filled, which is unusable
    # for the intraday traders this service targets: AEMET revises historical data
    # several times a day, and "the last 3 hours" must keep moving. The freshness
    # stamp bounds the upstream cost to one call per station per TTL window while
    # guaranteeing no hit is ever older than CACHE_TTL_MINUTES.
    #
    # TODO: min/max only proves the outer bounds. A hole in the middle of the
    # window (rows missing for a few hours) still counts as a hit and is never
    # refilled. Either compare the row count against the expected number of
    # 10-minute slots, or persist explicit coverage intervals per station.
    # TODO: the refill is all-or-nothing, so an expired wide window refetches its
    # whole span. Fetching only the uncovered or expired tail would cut that cost.
    cache_stats = db.query(
        func.min(MeteoRecord.timestamp),
        func.max(MeteoRecord.timestamp)
    ).filter(MeteoRecord.station_id == station_key).first()

    # Both bounds are None while the station has never been cached
    db_min, db_max = cache_stats
    is_cache_hit = False

    if db_min and db_max:
        # Coverage test on the grid-aligned bounds: the cache must start at or
        # before the requested start and end at or after the requested end, since
        # both range bounds are inclusive. Comparing whole dates instead would
        # wrongly accept a day that is only partially cached.
        covers_window = db_min <= _to_grid(dt_ini) and db_max >= _to_grid(dt_fin)

        # Freshness test: the station's last successful refresh must be recent.
        # A station with covered rows but no state row predates this table and is
        # treated as stale, so the first request after upgrading repopulates it.
        state = db.query(StationCacheState).filter(
            StationCacheState.station_id == station_key
        ).first()

        fresh_until = _utcnow() - timedelta(minutes=CACHE_TTL_MINUTES)
        is_fresh = state is not None and state.last_fetched_at > fresh_until

        if covers_window and is_fresh:
            is_cache_hit = True
            logger.info(
                f"CACHE HIT: '{station_key}' covers {dt_ini}..{dt_fin} and was refreshed at "
                f"{state.last_fetched_at} (TTL {CACHE_TTL_MINUTES} min)."
            )
        else:
            reason = "coverage incomplete" if not covers_window else "cache expired"
            logger.info(f"CACHE MISS for '{station_key}': {reason}. Fetching from AEMET API...")
    else:
        logger.info(f"CACHE MISS for '{station_key}': nothing cached. Fetching from AEMET API...")

    raw_data = []

    # --- CACHE HIT ---
    if is_cache_hit:
        cached_records = db.query(MeteoRecord).filter(
            MeteoRecord.station_id == station_key,
            MeteoRecord.timestamp >= dt_ini,
            MeteoRecord.timestamp <= dt_fin
        ).all()
        # Rows are mapped back to the AEMET field names so the cache path and the
        # source path feed the exact same shape into the data processor. `nombre`
        # is only replayed when a label was actually stored, because the source
        # path produces no `Station` column at all when the payload carries no
        # label. Emitting one here would make a cache hit differ from a cache
        # miss for the same request.
        for record in cached_records:
            restored = {
                "fhora": record.timestamp.strftime("%Y-%m-%dT%H:%M:%SUTC"),
                "temp": record.temperature,
                "pres": record.pressure,
                "vel": record.speed
            }
            if record.station_name:
                restored["nombre"] = record.station_name
            raw_data.append(restored)
            
    # --- CACHE MISS ---
    else:
        # AEMET expects the UTC suffix to be part of the path parameter
        aemet_ini = dt_ini.strftime("%Y-%m-%dT%H:%M:%SUTC")
        aemet_fin = dt_fin.strftime("%Y-%m-%dT%H:%M:%SUTC")

        # An unreachable or erroring upstream is a gateway failure, not an empty
        # station. Answering 502 keeps a source-API outage distinguishable from a
        # window that genuinely holds no observations, so callers do not read an
        # outage as "the station published nothing" and silently keep the cache.
        try:
            raw_data = fetch_aemet_data(aemet_ini, aemet_fin, station_key)
        except UpstreamAEMETError as error:
            logger.error(f"AEMET upstream failure for {station_key}: {error}")
            raise HTTPException(
                status_code=502,
                detail=f"Upstream AEMET API unavailable: {error}",
            )

        if not raw_data:
            # A valid but empty answer: keep whatever the cache already holds for
            # this window instead of treating it as an invalidation. The freshness
            # stamp is deliberately left untouched, because nothing new arrived.
            logger.warning(
                f"AEMET returned no observations for {station_key} "
                f"between {aemet_ini} and {aemet_fin}. Serving an empty dataset."
            )
        else:
            logger.info("Saving new AEMET data to SQLite cache...")
            db_records_to_insert = []
            # AEMET labels every sample of a station the same way, so the first
            # non-empty label is stored once and reused on every cache hit.
            station_label = next(
                (item.get("nombre") for item in raw_data if item.get("nombre")),
                None,
            )

            for item in raw_data:
                # Skip malformed rows rather than aborting the whole ingestion
                item_dt_str = item.get("fhora", "").replace("UTC", "").replace("Z", "")
                try:
                    item_dt = datetime.strptime(item_dt_str, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue 
                
                new_record = MeteoRecord(
                    station_id=station_key,
                    station_name=station_label,
                    timestamp=item_dt,
                    temperature=item.get("temp"),
                    pressure=item.get("pres"),
                    speed=item.get("vel")
                )
                db_records_to_insert.append(new_record)

            # Replace whatever we hold for this window instead of appending, so a
            # refresh cannot duplicate rows. The delete and the insert share a
            # single transaction, so the swap stays atomic, and both only run once
            # the payload has been parsed: a truncated or failed upstream response
            # must never wipe data we already had.
            #
            # The unique constraint on (station_id, timestamp) backs this up at the
            # schema level, so even two concurrent refreshes of the same window
            # cannot both insert the same sample.
            if db_records_to_insert:
                db.query(MeteoRecord).filter(
                    MeteoRecord.station_id == station_key,
                    MeteoRecord.timestamp >= dt_ini,
                    MeteoRecord.timestamp <= dt_fin
                ).delete()

                db.add_all(db_records_to_insert)

                # Only a successful ingestion marks the station fresh. Recording it
                # here, inside the same transaction as the rows, means the stamp can
                # never claim freshness for data that was not actually stored.
                state = db.query(StationCacheState).filter(
                    StationCacheState.station_id == station_key
                ).first()
                fetched_at = _utcnow()
                if state:
                    state.last_fetched_at = fetched_at
                else:
                    db.add(StationCacheState(station_id=station_key, last_fetched_at=fetched_at))

                db.commit()
                logger.info(
                    f"Successfully cached {len(db_records_to_insert)} records for '{station_key}' "
                    f"and marked it fresh at {fetched_at}."
                )
    # --- TRANSFORMATION & RESPONSE ---
    # Column renaming, the Europe/Madrid conversion, the time aggregation and
    # the data_types filter all live in the service layer, keeping this endpoint
    # thin and focused on transport concerns.
    processed_data = process_weather_data(raw_data, data_types, aggregation)

    # `location` is accepted because the challenge spec asks for it, but the
    # challenge also fixes the output to Europe/Madrid, so it is not applied.
    # Echoing both values keeps that decision explicit for API consumers.
    # TODO: apply it, or drop the parameter and document the fixed zone in the README.
    return {
        "status": "success",
        "station_requested": identificacion,
        "station_resolved": station_key,
        "location_requested": location,
        "location_resolved": "Europe/Madrid",
        "data_types_filtered": data_types if data_types else "All",
        "data": processed_data
    }
