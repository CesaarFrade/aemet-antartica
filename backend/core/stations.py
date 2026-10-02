"""Station registry and canonical identifiers."""

# The challenge accepts either the station name or its AEMET internal code, but both
# spellings address the same physical station. Every cache key goes through
# `canonical_station_id` so the station is stored once instead of once per spelling.
#
# Unknown identifiers are passed through untouched, which keeps arbitrary AEMET
# station codes working without this layer having to know about them.
STATION_ALIASES = {
    "Meteo Station Gabriel de Castilla": "89064",
    "89064": "89064",
    "Meteo Station Juan Carlos I": "89070",
    "89070": "89070",
}


def canonical_station_id(identificacion: str) -> str:
    """
    Resolves any accepted spelling of a station to its canonical AEMET code.

    Args:
        identificacion: Station name or AEMET code, as supplied by the caller.

    Returns:
        The canonical code for a known station, or the trimmed input for an
        unknown one, so unknown identifiers keep their existing behaviour.
    """
    return STATION_ALIASES.get(identificacion.strip(), identificacion.strip())


def station_code_for_aemet(identificacion: str) -> str:
    """
    Returns the value AEMET expects in the URL path.

    AEMET is lenient here and accepts either form, so the canonical code is used
    for both the outgoing request and the cache key, keeping the two aligned.
    """
    return canonical_station_id(identificacion)