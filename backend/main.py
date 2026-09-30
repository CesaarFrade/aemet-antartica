from fastapi import FastAPI, Query, HTTPException
from typing import List, Optional
from datetime import datetime
from aemet_client import fetch_aemet_data
from data_processor import process_weather_data

app = FastAPI(title="Antarctica Wind Farm AEMET API")

# The endpoint route required by the challenge
@app.get("/api/antartida/datos/fechaini/{fechaIniStr}/fechafin/{fechaFinStr}/estacion/{identificacion}")
def get_meteo_data(
    fechaIniStr: str,
    fechaFinStr: str,
    identificacion: str,
    location: Optional[str] = Query(None, description="Time zone location, e.g., Europe/Berlin or +02:00"),
    aggregation: Optional[str] = Query("None", enum=["None", "Hourly", "Daily", "Monthly"]),
    data_types: Optional[List[str]] = Query(None, description="List of required data types: temperature, pressure, speed")
):
    """
    Retrieves meteorological data for the specified station within the given date range.
    """

    # --- 1. DATE VALIDATION ---
    try:
        # Strip "UTC" or "Z" from the end if provided by the user to validate the base format
        clean_ini = fechaIniStr.replace("UTC", "").replace("Z", "")
        clean_fin = fechaFinStr.replace("UTC", "").replace("Z", "")
        
        # Attempt to convert the string into actual datetime objects
        dt_ini = datetime.strptime(clean_ini, "%Y-%m-%dT%H:%M:%S")
        dt_fin = datetime.strptime(clean_fin, "%Y-%m-%dT%H:%M:%S")
        
        # Validate temporal coherence (start date must be before end date)
        if dt_ini >= dt_fin:
            raise HTTPException(
                status_code=400, 
                detail="Invalid date range: The start date (fechaIniStr) must be strictly before the end date (fechaFinStr)."
            )
            
    except ValueError:
        # If strptime fails, it means the format was not YYYY-MM-DDTHH:MM:SS
        raise HTTPException(
            status_code=400, 
            detail="Invalid date format. Expected format: YYYY-MM-DDTHH:MM:SS (e.g., 2024-01-01T00:00:00UTC)"
        )
    # --------------------------
    
    # 2. Fetch data from AEMET
    raw_data = fetch_aemet_data(fechaIniStr, fechaFinStr, identificacion)

    # 3. Process data using Pandas
    processed_data = process_weather_data(raw_data, data_types, aggregation)
    
    # Return the processed payload to the client
    return {
        "status": "success",
        "station_requested": identificacion,
        "data_types_filtered": data_types if data_types else "All",
        "data": processed_data
    }