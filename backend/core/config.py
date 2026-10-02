"""Runtime configuration, read once from the environment."""

import os


def _int_env(name: str, default: int) -> int:
    """Reads a positive integer from the environment, falling back on bad input."""
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# How long a station's cached window is considered fresh, in minutes.
#
# AEMET republishes and revises historical observations several times a day, so a
# cache that never expires would freeze the most recent hours of data forever.
# Expiring the whole station bounds the upstream cost to one call per station per
# TTL window while guaranteeing that every hit is at most this old.
#
# Override with AEMET_CACHE_TTL_MINUTES; set it high to effectively disable refresh.
CACHE_TTL_MINUTES = _int_env("AEMET_CACHE_TTL_MINUTES", 60)