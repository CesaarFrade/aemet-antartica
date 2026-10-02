import os
import requests
from dotenv import load_dotenv
from core.logger import get_logger


logger = get_logger("aemet_client")
# Load environment variables
load_dotenv()
AEMET_API_KEY = os.getenv("AEMET_API_KEY")

# Mapping the requested station names to their potential AEMET internal IDs
# (Assuming standard AEMET ID mapping for Antarctica stations)
STATION_MAPPING = {
    "Meteo Station Gabriel de Castilla": "89064",
    "Meteo Station Juan Carlos I": "89070"
}

def fetch_aemet_data(start_date: str, end_date: str, station_name: str):
    """
    Fetches historical data from AEMET OpenData API using the two-step request flow.
    """
    if not AEMET_API_KEY:
        raise ValueError("AEMET_API_KEY is not set in the .env file")

    # If the user passes the full name, we convert it to the ID. If not, we use the input directly.
    station_id = STATION_MAPPING.get(station_name, station_name)

    # AEMET OpenData Antarctica endpoint
    url = f"https://opendata.aemet.es/opendata/api/antartida/datos/fechaini/{start_date}/fechafin/{end_date}/estacion/{station_id}"
    
    headers = {
        "cache-control": "no-cache"
    }
    
    querystring = {"api_key": AEMET_API_KEY}

    try:
        # Step 1: Request the data URL
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        meta_data = response.json()

        if meta_data.get("estado") == 200:
            datos_url = meta_data.get("datos")
            
            # Step 2: Fetch the actual data from the provided URL
            datos_response = requests.get(datos_url)
            datos_response.raise_for_status()
            
            return datos_response.json()
        else:
            logger.error(f"AEMET API Error: {meta_data.get('descripcion')}")
            return []

    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error with AEMET: {e}")
        return []