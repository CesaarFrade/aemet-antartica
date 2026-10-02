"""
Cache hardening: station canonicalisation, freshness TTL and schema constraints.

These cover the three correctness gaps that survived the first pass of Part 2:
one physical station was stored under several spellings, a covered window was
never refreshed, and the schema allowed duplicate rows.
"""

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from api import routes
from core.exceptions import UpstreamAEMETError
from core.stations import canonical_station_id
from models.database import (
    COMPOSITE_INDEX_NAME,
    MeteoRecord,
    StationCacheState,
)

STATION_NAME = "Meteo Station Gabriel de Castilla"
STATION_CODE = "89064"
OTHER_NAME = "Meteo Station Juan Carlos I"

START = "2024-01-01T00:00:00"
END = "2024-01-01T01:00:00"


def call(client, start=START, end=END, station=STATION_NAME, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"/api/antartida/datos/fechaini/{start}/fechafin/{end}/estacion/{station}"
    return client.get(f"{url}?{query}" if query else url)


# --- Risk 2: station canonicalisation ---


def test_name_and_code_address_the_same_cache_entry(mock_aemet, client, cache):
    """
    Regression guard: the station name and its AEMET code were separate cache keys,
    so the same physical station was stored twice and every spelling caused its own
    upstream call.
    """
    call(client, station=STATION_NAME)
    call(client, station=STATION_CODE)

    assert len(mock_aemet.calls) == 1  # the second spelling hit the same entry
    assert len(cache.rows(STATION_CODE)) == 7
    # Both spellings resolve to the same rows, not two copies of the same station
    assert [row.timestamp for row in cache.rows(STATION_NAME)] == [
        row.timestamp for row in cache.rows(STATION_CODE)
    ]


def test_response_echoes_the_requested_and_resolved_station(mock_aemet, client):
    """Both spellings stay visible to the caller, with the resolution explicit."""
    by_name = call(client, station=STATION_NAME).json()
    by_code = call(client, station="Meteo Station Juan Carlos I").json()

    assert by_name["station_requested"] == STATION_NAME
    assert by_name["station_resolved"] == STATION_CODE
    assert by_code["station_requested"] == OTHER_NAME
    assert by_code["station_resolved"] == "89070"


def test_unknown_station_passes_through_unchanged():
    """Arbitrary AEMET codes keep working without being registered here."""
    assert canonical_station_id("B99999") == "B99999"
    assert canonical_station_id("  B99999  ") == "B99999"
    assert canonical_station_id(STATION_NAME) == STATION_CODE


def test_canonical_code_is_what_reaches_aemet(mock_aemet, client):
    """The outgoing request must use the same identifier as the cache key."""
    call(client, station=STATION_NAME)

    _, _, station_sent = mock_aemet.calls[0]
    assert station_sent == STATION_CODE


def test_cache_hit_reproduces_the_station_label_from_the_source(client, monkeypatch):
    """
    Regression guard: the cache path used to rebuild the station label from the id,
    so a cache hit answered with the AEMET code while a cache miss answered with
    the label AEMET published. Same request, two different responses.
    """
    # AEMET's own label is unrelated to both the station name and its code
    monkeypatch.setattr(
        "api.routes.fetch_aemet_data",
        lambda *_: [
            {"nombre": "JCI Estacion meteorologica",
             "fhora": f"2024-01-01T{hour:02d}:00:00UTC",
             "temp": 1.0, "pres": 1010.0, "vel": 5.0}
            for hour in range(2)
        ],
    )

    from_source = call(client, station=STATION_NAME).json()
    from_cache = call(client, station=STATION_NAME).json()

    assert from_cache["data"] == from_source["data"]
    assert from_cache["data"][0]["Station"] == "JCI Estacion meteorologica"


def test_station_label_is_omitted_when_the_source_omits_it(client, monkeypatch):
    """
    The source path produces no `Station` column when the payload carries no
    label, so the cache path must not invent one either.
    """
    monkeypatch.setattr(
        "api.routes.fetch_aemet_data",
        lambda *_: [
            {"fhora": "2024-01-01T00:00:00UTC", "temp": 1.0, "pres": 1010.0, "vel": 5.0},
            {"fhora": "2024-01-01T01:00:00UTC", "temp": 2.0, "pres": 1010.0, "vel": 5.0},
        ],
    )

    from_source = call(client, station=STATION_NAME).json()
    from_cache = call(client, station=STATION_NAME).json()

    assert from_cache["data"] == from_source["data"]
    assert "Station" not in from_cache["data"][0]


# --- Risk 1: freshness TTL ---


def test_successful_fetch_marks_the_station_fresh(mock_aemet, client, cache):
    """The stamp only exists once data has actually been stored."""
    assert cache.freshness(STATION_NAME) is None

    call(client)

    assert cache.freshness(STATION_NAME) is not None


def test_within_the_ttl_window_the_cache_is_reused(mock_aemet, client):
    """Repeated calls inside the TTL must not reach the upstream at all."""
    call(client)
    call(client)
    call(client)

    assert len(mock_aemet.calls) == 1


def test_expired_cache_is_refreshed_from_aemet(mock_aemet, client, cache):
    """
    Regression guard: coverage alone froze a window forever. AEMET revises
    historical data, so an expired station must be read again.
    """
    call(client)
    cache.backdate(STATION_NAME, minutes=routes.CACHE_TTL_MINUTES + 5)

    call(client)

    assert len(mock_aemet.calls) == 2


def test_expiry_triggers_exactly_one_refresh_then_reuses_again(mock_aemet, client, cache):
    """The TTL bounds upstream traffic rather than disabling the cache."""
    call(client)
    cache.backdate(STATION_NAME, minutes=routes.CACHE_TTL_MINUTES + 5)

    call(client)  # expires -> refetch
    call(client)  # fresh again -> cache
    call(client)  # still fresh -> cache

    assert len(mock_aemet.calls) == 2


def test_refresh_does_not_duplicate_rows(mock_aemet, client, cache):
    """Re-reading an expired window replaces its rows instead of appending."""
    call(client)
    cache.backdate(STATION_NAME, minutes=routes.CACHE_TTL_MINUTES + 5)

    call(client)

    assert len(cache.rows(STATION_NAME)) == 7


def test_coverage_still_beats_the_ttl_for_older_data(mock_aemet, client, cache):
    """
    A station cached long ago is refreshed, but a station refreshed moments ago
    keeps serving even for a much wider window it already covers.
    """
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T02:00:00")
    assert len(mock_aemet.calls) == 1

    call(client, start="2024-01-01T00:00:00", end="2024-01-01T02:00:00")

    assert len(mock_aemet.calls) == 1  # fresh and covered, no refetch


def test_outage_does_not_refresh_the_freshness_stamp(client, cache, monkeypatch):
    """
    A failed read must not claim the data is current; otherwise a short outage
    would push the next legitimate refresh out by a full TTL.
    """
    def boom(*_, **__):
        raise UpstreamAEMETError("AEMET unreachable")

    monkeypatch.setattr("api.routes.fetch_aemet_data", boom)

    assert call(client).status_code == 502
    assert cache.freshness(STATION_NAME) is None


def test_empty_upstream_answer_does_not_refresh_the_stamp(mock_aemet, client, cache, monkeypatch):
    """An empty but valid answer carries no new data, so freshness is unchanged."""
    monkeypatch.setattr("api.routes.fetch_aemet_data", lambda *_: [])

    call(client)

    assert cache.freshness(STATION_NAME) is None


def test_ttl_is_configurable(monkeypatch):
    """The window is an operational knob, so it must be overridable."""
    monkeypatch.setenv("AEMET_CACHE_TTL_MINUTES", "5")

    import importlib

    from core import config

    reloaded = importlib.reload(config)
    try:
        assert reloaded.CACHE_TTL_MINUTES == 5
    finally:
        monkeypatch.delenv("AEMET_CACHE_TTL_MINUTES")
        importlib.reload(config)


def test_invalid_ttl_falls_back_to_the_default(monkeypatch):
    """A typo in the environment must not take the cache policy down."""
    monkeypatch.setenv("AEMET_CACHE_TTL_MINUTES", "not-a-number")

    import importlib

    from core import config

    reloaded = importlib.reload(config)
    try:
        assert reloaded.CACHE_TTL_MINUTES == 60
    finally:
        monkeypatch.delenv("AEMET_CACHE_TTL_MINUTES")
        importlib.reload(config)


# --- Risk 3: schema constraints ---


def test_duplicate_instant_for_a_station_is_rejected(engine):
    """One reading per station per instant, enforced by the schema itself."""
    with sessionmaker(bind=engine)() as session:
        session.add(MeteoRecord(station_id=STATION_CODE, timestamp=datetime(2024, 1, 1), temperature=1.0))
        session.commit()

        session.add(MeteoRecord(station_id=STATION_CODE, timestamp=datetime(2024, 1, 1), temperature=2.0))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_same_instant_for_different_stations_is_allowed(engine):
    """The constraint is scoped per station, so stations never block each other."""
    with sessionmaker(bind=engine)() as session:
        moment = datetime(2024, 1, 1)
        session.add(MeteoRecord(station_id=STATION_CODE, timestamp=moment, temperature=1.0))
        session.add(MeteoRecord(station_id="89070", timestamp=moment, temperature=2.0))
        session.commit()

        assert session.query(MeteoRecord).count() == 2


def test_composite_index_exists(engine):
    """Coverage and window queries share one index instead of two single columns."""
    with engine.connect() as connection:
        names = {
            row[0] for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert COMPOSITE_INDEX_NAME in names


def test_freshness_table_holds_one_row_per_station(engine):
    """The stamp is keyed by station, so it cannot accumulate duplicates."""
    with sessionmaker(bind=engine)() as session:
        session.add(StationCacheState(station_id=STATION_CODE, last_fetched_at=datetime(2024, 1, 1)))
        session.commit()

        session.add(StationCacheState(station_id=STATION_CODE, last_fetched_at=datetime(2024, 1, 2)))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()