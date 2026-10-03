"""Station registry, canonical identifiers and station discovery."""

from typing import Dict, List

# The challenge names two Antarctic stations. A station may be addressed either by its
# AEMET internal code or by its display name, but both spell the same physical
# station, so every cache key goes through `canonical_station_id` and the station is
# stored once instead of once per spelling.
#
# Keyed by code and valued by name, because that is the direction callers need for
# discovery: the registry is exposed as an endpoint so a client can enumerate what
# is available instead of hardcoding the identifiers.
ANTARCTIC_STATIONS: Dict[str, str] = {
    "89064": "Meteo Station Gabriel de Castilla",
    "89070": "Meteo Station Juan Carlos I",
}

# Reverse lookup for the name -> code direction, built once at import time so
# `canonical_station_id` stays a constant-time lookup instead of scanning the
# registry on every request. Keys are lowercased so station names resolve
# case-insensitively.
_NAME_TO_CODE: Dict[str, str] = {
    name.lower(): code for code, name in ANTARCTIC_STATIONS.items()
}


def get_all_stations() -> List[Dict[str, str]]:
    """
    Lists every station the registry knows about, ready for API consumption.

    Returns:
        One `{"id": code, "name": name}` entry per registered station. This is the
        curated set from the challenge spec; the data endpoint additionally accepts
        any AEMET station code, which resolves through untouched so the service
        keeps working if AEMET widens its catalogue.
    """
    return [{"id": code, "name": name} for code, name in ANTARCTIC_STATIONS.items()]


def canonical_station_id(identificacion: str) -> str:
    """
    Resolves any accepted spelling of a station to its canonical AEMET code.

    Surrounding whitespace is ignored, so a code copied with a trailing space still
    hits the cache entry instead of creating a second one. Names match
    case-insensitively.

    Args:
        identificacion: Station name or AEMET code, as supplied by the caller.

    Returns:
        The canonical code for a known station, or the trimmed input for an unknown
        one, so unregistered AEMET station codes keep working without this layer
        having to know about them.
    """
    candidate = identificacion.strip()
    if candidate in ANTARCTIC_STATIONS:
        return candidate
    return _NAME_TO_CODE.get(candidate.lower(), candidate)


def station_code_for_aemet(identificacion: str) -> str:
    """
    Returns the value AEMET expects in the URL path.

    AEMET is lenient here and accepts either form, so the canonical code is used
    for both the outgoing request and the cache key, keeping the two aligned.
    """
    return canonical_station_id(identificacion)