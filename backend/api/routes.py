from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import List, Literal, Optional
from datetime import datetime

# Relative imports to the sibling layers of the new folder structure
from services.aemet_client import fetch_aemet_data
from services.data_processor import process_weather_data
from models.database import SessionLocal, MeteoRecord
from core.logger import get_logger

logger = get_logger("routes")
router = APIRouter()

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

    # --- CACHE LOOKUP ---
    # Query SQLite first so the source API is only called when we have nothing
    # stored for this station and window.
    #
    # TODO: this treats "at least one row inside the range" as "the whole range is
    # cached". A wider request served from a narrower cache therefore returns a
    # truncated dataset and is never refreshed, which also defeats the intraday
    # updates the traders expect. Compare the cached min/max timestamps against the
    # requested window, fetch only the gaps, and consider a TTL per station because
    # AEMET revises historical data a few times per day.
    cached_records = db.query(MeteoRecord).filter(
        MeteoRecord.station_id == identificacion,
        MeteoRecord.timestamp >= dt_ini,
        MeteoRecord.timestamp <= dt_fin
    ).all()

    raw_data = []

    # --- CACHE HIT ---
    if cached_records:
        logger.info("CACHE HIT: Retrieving data from SQLite database to avoid AEMET overload.")
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
        logger.info("CACHE MISS: No local data found. Fetching from AEMET API...")
        
        # AEMET expects the UTC suffix to be part of the path parameter
        aemet_ini = dt_ini.strftime("%Y-%m-%dT%H:%M:%SUTC")
        aemet_fin = dt_fin.strftime("%Y-%m-%dT%H:%M:%SUTC")
        
        raw_data = fetch_aemet_data(aemet_ini, aemet_fin, identificacion)
        
        # TODO: `fetch_aemet_data` swallows upstream failures and returns an empty
        # list, so a dead AEMET response is logged as a success and the client gets
        # 200 with an empty dataset. Raise a 502/503 instead, or at least log the
        # upstream error at ERROR level.
        if raw_data:
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
            
            # One bulk transaction for the whole batch. TODO: add a UniqueConstraint on
            # (station_id, timestamp) and upsert, so overlapping requests cannot
            # store the same measurement twice under different station aliases.
            db.add_all(db_records_to_insert)
            db.commit()
            logger.info(f"Successfully cached {len(db_records_to_insert)} records to DB.")

    # --- TRANSFORMATION & RESPONSE ---
    # Column renaming, the Europe/Madrid conversion, the time aggregation and the
    # data_types filter all live in the service layer to keep this endpoint thin.
    processed_data = process_weather_data(raw_data, data_types, aggregation)

    # Response envelope; the dataset itself uses the field names from the
    # challenge table (Station, Datetime, Temperature, Pressure, Speed)
    return {
        "status": "success",
        "station_requested": identificacion,
        "data_types_filtered": data_types if data_types else "All",
        "data": processed_data
    }
