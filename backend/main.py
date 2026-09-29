from fastapi import FastAPI, Query
from typing import List, Optional

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
    
    # Return a test message with the received parameters to ensure correct parsing
    return {
        "status": "success",
        "message": "Endpoint is working correctly",
        "parameters": {
            "start_date": fechaIniStr,
            "end_date": fechaFinStr,
            "station": identificacion,
            "location": location,
            "aggregation": aggregation,
            "data_types": data_types
        }
    }