import os
import time

import requests
from dotenv import load_dotenv

from core.exceptions import ConfigurationError, UpstreamAEMETError
from core.logger import get_logger
from core.stations import station_code_for_aemet


logger = get_logger("aemet_client")

# Load environment variables
load_dotenv()
AEMET_API_KEY = os.getenv("AEMET_API_KEY")

# The name-to-code registry lives in `core.stations` so the cache key and the
# outgoing request always resolve a station the same way.

# Seconds allowed for each HTTP round trip. Without an explicit timeout `requests`
# blocks indefinitely, so a stalled AEMET connection would pin the API worker
# instead of letting it answer 502.
REQUEST_TIMEOUT = 10

# AEMET occasionally rate-limits or drops a connection under load, so a transient
# transport failure is retried before the request is declared failed.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1


def _get_with_retry(url: str, **kwargs):
    """
    Performs a GET with a timeout, retrying transient transport failures.

    Only connection and timeout problems are retried: a 4xx or 5xx response comes
    back immediately because repeating it would not change the outcome.
    """
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return requests.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as error:
            last_error = error
            logger.warning(f"AEMET request attempt {attempt}/{MAX_ATTEMPTS} failed: {error}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise UpstreamAEMETError(
        f"AEMET unreachable after {MAX_ATTEMPTS} attempts: {last_error}",
        attempts=MAX_ATTEMPTS,
    ) from last_error


def fetch_aemet_data(start_date: str, end_date: str, station_name: str):
    """
    Fetches historical data from AEMET OpenData API using the two-step request flow.

    Returns the list of observations for the requested window, which may legitimately
    be empty. Any transport or API-level failure raises `UpstreamAEMETError` so the
    caller can tell an outage apart from a station that published no data.
    """
    if not AEMET_API_KEY:
        # A distinct exception type, not a bare ValueError: the caller maps this
        # to 503 so the failure reads as "this deployment is misconfigured" rather
        # than "the source API is down", which would send the caller to the wrong
        # party. It also means the missing key no longer surfaces as an opaque 500.
        raise ConfigurationError(
            "AEMET_API_KEY is not set. Add it to the .env file at the repository "
            "root; a personal e-mail account can register at "
            "https://opendata.aemet.es/centrodedescargas/inicio."
        )

    # If the user passes the full name, we convert it to the ID. If not, we use the input directly.
    station_id = station_code_for_aemet(station_name)

    # AEMET OpenData Antarctica endpoint
    url = f"https://opendata.aemet.es/opendata/api/antartida/datos/fechaini/{start_date}/fechafin/{end_date}/estacion/{station_id}"

    headers = {
        "cache-control": "no-cache"
    }

    querystring = {"api_key": AEMET_API_KEY}

    # Step 1: Request the data URL
    response = _get_with_retry(url, headers=headers, params=querystring)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as error:
        raise UpstreamAEMETError(f"AEMET metadata request failed: {error}") from error

    try:
        meta_data = response.json()
    except ValueError as error:
        raise UpstreamAEMETError(f"AEMET metadata response was not valid JSON: {error}") from error

    if meta_data.get("estado") != 200:
        raise UpstreamAEMETError(
            f"AEMET API error {meta_data.get('estado')}: {meta_data.get('descripcion')}"
        )

    # `datos` is the temporary URL holding the payload. A successful response with
    # no URL means the station published nothing for this window, which is a valid
    # answer and not a failure.
    datos_url = meta_data.get("datos")
    if not datos_url:
        logger.info(f"AEMET reported no data for station {station_id} in {start_date}..{end_date}")
        return []

    # Step 2: Fetch the actual data from the provided URL
    datos_response = _get_with_retry(datos_url)

    try:
        datos_response.raise_for_status()
    except requests.exceptions.HTTPError as error:
        raise UpstreamAEMETError(f"AEMET payload request failed: {error}") from error

    try:
        return datos_response.json()
    except ValueError as error:
        # A truncated or non-JSON payload would otherwise be ingested as an empty
        # dataset and, worse, treated as a valid empty upstream answer.
        raise UpstreamAEMETError(f"AEMET payload was not valid JSON: {error}") from error