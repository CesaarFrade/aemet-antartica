from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Literal, Optional
from datetime import datetime, timedelta

# Relative imports to the sibling layers
from services.aemet_client import fetch_aemet_data
from services.data_processor import process_weather_data
from models.database import SessionLocal, MeteoRecord
from core.logger import get_logger

logger = get_logger("routes")
router = APIRouter()

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
    logger.info(f"Incoming request for station: '{identificacion}' from {dt_ini} to {dt_fin}")

    # --- CACHE COVERAGE LOOKUP ---
    # A request is only served from SQLite when the stored observations already
    # span the whole requested window; anything narrower goes back to AEMET for
    # the full range. The first and last cached timestamps are enough to decide
    # that, and they come from a cheap aggregate over the per-station index.
    #
    # TODO: min/max only proves the outer bounds. A hole in the middle of the
    # window (rows missing for a few hours) still counts as a hit and is never
    # refilled. Either compare the row count against the expected number of
    # 10-minute slots, or persist explicit coverage intervals per station.
    # TODO: the refill is still all-or-nothing. Fetching only the uncovered gaps,
    # plus a TTL per station, would also serve the intraday refreshes the traders
    # need, since AEMET revises historical data a few times per day.
    cache_stats = db.query(
        func.min(MeteoRecord.timestamp),
        func.max(MeteoRecord.timestamp)
    ).filter(MeteoRecord.station_id == identificacion).first()

    # Both bounds are None while the station has never been cached
    db_min, db_max = cache_stats
    is_cache_hit = False

    if db_min and db_max:
        # Coverage test on the grid-aligned bounds: the cache must start at or
        # before the requested start and end at or after the requested end, since
        # both range bounds are inclusive. Comparing whole dates instead would
        # wrongly accept a day that is only partially cached.
        if db_min <= _to_grid(dt_ini) and db_max >= _to_grid(dt_fin):
            is_cache_hit = True

    raw_data = []

    # --- CACHE HIT ---
    if is_cache_hit:
        logger.info("CACHE HIT: Full date range found in SQLite.")
        cached_records = db.query(MeteoRecord).filter(
            MeteoRecord.station_id == identificacion,
            MeteoRecord.timestamp >= dt_ini,
            MeteoRecord.timestamp <= dt_fin
        ).all()
        # Rows are mapped back to the AEMET field names so the cache path and the
        # source path feed the exact same shape into the data processor.
        for record in cached_records:
            raw_data.append({
                "nombre": record.station_id,
                "fhora": record.timestamp.strftime("%Y-%m-%dT%H:%M:%SUTC"),
                "temp": record.temperature,
                "pres": record.pressure,
                "vel": record.speed
            })
            
    # --- CACHE MISS ---
    else:
        logger.info("CACHE MISS OR PARTIAL DATA: Fetching from AEMET API...")
        # AEMET expects the UTC suffix to be part of the path parameter
        aemet_ini = dt_ini.strftime("%Y-%m-%dT%H:%M:%SUTC")
        aemet_fin = dt_fin.strftime("%Y-%m-%dT%H:%M:%SUTC")
        
        raw_data = fetch_aemet_data(aemet_ini, aemet_fin, identificacion)
        
        # TODO: `fetch_aemet_data` maps every AEMET outcome to `[]`, so an upstream
        # outage and a legitimately empty range are indistinguishable here. Have
        # the client return a status (or raise a custom exception) and answer 502
        # for the former, 200 with an empty dataset for the latter.
        if not raw_data:
            logger.warning(f"AEMET returned no data for {identificacion} (could be empty range or upstream failure). Serving empty dataset.")
        else:
            logger.info("Saving new AEMET data to SQLite cache...")
            db_records_to_insert = []
            
            for item in raw_data:
                # Skip malformed rows rather than aborting the whole ingestion
                item_dt_str = item.get("fhora", "").replace("UTC", "").replace("Z", "")
                try:
                    item_dt = datetime.strptime(item_dt_str, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue 
                
                new_record = MeteoRecord(
                    station_id=identificacion,
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
            # TODO: it is still keyed on the station string, so one physical station
            # is stored twice under its name and under its AEMET code. Canonicalise
            # the station id and add a UniqueConstraint on (station_id, timestamp).
            if db_records_to_insert:
                db.query(MeteoRecord).filter(
                    MeteoRecord.station_id == identificacion,
                    MeteoRecord.timestamp >= dt_ini,
                    MeteoRecord.timestamp <= dt_fin
                ).delete()
                
                db.add_all(db_records_to_insert)
                db.commit()
                logger.info(f"Successfully cached {len(db_records_to_insert)} records to DB.")
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
        "location_requested": location,
        "location_resolved": "Europe/Madrid",
        "data_types_filtered": data_types if data_types else "All",
        "data": processed_data
    }
