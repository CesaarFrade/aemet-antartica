from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

# Importaciones relativas a la nueva estructura de carpetas
from services.aemet_client import fetch_aemet_data
from services.data_processor import process_weather_data
from models.database import SessionLocal, MeteoRecord
from core.logger import get_logger

logger = get_logger("routes")
router = APIRouter()

# Dependencia de la base de datos
def get_db():
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
    location: Optional[str] = Query(None, description="Time zone location, e.g., Europe/Berlin or +02:00"),
    aggregation: Optional[str] = Query("None", enum=["None", "Hourly", "Daily", "Monthly"]),
    data_types: Optional[List[str]] = Query(None, description="List of required data types: temperature, pressure, speed"),
    db: Session = Depends(get_db)
):
    """
    Retrieves meteorological data for the specified station within the given date range.
    """
    # DATE VALIDATION 
    try:
        clean_ini = fechaIniStr.replace("UTC", "").replace("Z", "")
        clean_fin = fechaFinStr.replace("UTC", "").replace("Z", "")
        
        dt_ini = datetime.strptime(clean_ini, "%Y-%m-%dT%H:%M:%S")
        dt_fin = datetime.strptime(clean_fin, "%Y-%m-%dT%H:%M:%S")
        
        if dt_ini >= dt_fin:
            raise HTTPException(
                status_code=400, 
                detail="Invalid date range: The start date must be strictly before the end date."
            )
            
    except ValueError:
        raise HTTPException(
            status_code=400, 
            detail="Invalid date format. Expected format: YYYY-MM-DDTHH:MM:SS (e.g., 2024-01-01T00:00:00UTC)"
        )
    # --------------------------
    
    logger.info(f"Incoming request for station: '{identificacion}' from {dt_ini} to {dt_fin}")

    # Check if we already have the data in our local database
    cached_records = db.query(MeteoRecord).filter(
        MeteoRecord.station_id == identificacion,
        MeteoRecord.timestamp >= dt_ini,
        MeteoRecord.timestamp <= dt_fin
    ).all()

    raw_data = []

    if cached_records:
        logger.info("CACHE HIT: Retrieving data from SQLite database to avoid AEMET overload.")
        for record in cached_records:
            raw_data.append({
                "nombre": record.station_id,
                "fhora": record.timestamp.strftime("%Y-%m-%dT%H:%M:%SUTC"),
                "temp": record.temperature,
                "pres": record.pressure,
                "vel": record.speed
            })
    else:
        logger.info("CACHE MISS: No local data found. Fetching from AEMET API...")
        
        aemet_ini = dt_ini.strftime("%Y-%m-%dT%H:%M:%SUTC")
        aemet_fin = dt_fin.strftime("%Y-%m-%dT%H:%M:%SUTC")
        
        raw_data = fetch_aemet_data(aemet_ini, aemet_fin, identificacion)
        
        if raw_data:
            logger.info("Saving new AEMET data to SQLite cache...")
            db_records_to_insert = []
            
            for item in raw_data:
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
            
            db.add_all(db_records_to_insert)
            db.commit()
            logger.info(f"Successfully cached {len(db_records_to_insert)} records to DB.")
    # --------------------------------------

    processed_data = process_weather_data(raw_data, data_types, aggregation)
    
    return {
        "status": "success",
        "station_requested": identificacion,
        "data_types_filtered": data_types if data_types else "All",
        "data": processed_data
    }