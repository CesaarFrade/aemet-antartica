from fastapi import FastAPI, Query
from typing import List, Optional
from aemet_client import fetch_aemet_data

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

    raw_data = fetch_aemet_data(fechaIniStr, fechaFinStr, identificacion)
    
    # Return a test message with the data to check the APY Key functionality
    return {
        "status": "success",
        "station_requested": identificacion,
        "data": raw_data
    }