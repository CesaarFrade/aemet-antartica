"""Domain-level exceptions shared across the layers."""


class UpstreamAEMETError(Exception):
    """
    Raised when the AEMET OpenData service cannot be reached or answers with an
    error, as opposed to answering successfully with an empty dataset.

    The distinction matters for Part 2 of the challenge: an unreachable source API
    is a `502 Bad Gateway`, while a station that genuinely published no
    observations for a window is a `200 OK` with an empty `data` array. Collapsing
    both into an empty list hides outages behind an apparently healthy response and
    would let the cache silently keep serving stale data.
    """

    def __init__(self, message: str, attempts: int = 1):
        super().__init__(message)
        self.attempts = attempts