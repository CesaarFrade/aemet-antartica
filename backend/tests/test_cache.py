"""
SQLite cache behaviour — challenge Part 2.

The brief asks for an SQLite store that avoids overloading the source API, plus
proper logging, so these tests pin down three things: the upstream is called once
per covered range, the persisted rows are refreshed instead of duplicated, and a
truncated upstream response never destroys data we already hold.
"""

from datetime import datetime

import pytest

STATION = "Meteo Station Gabriel de Castilla"


def call(client, start, end, station=STATION, **params):
    """Calls the challenge endpoint with the given inclusive UTC bounds."""
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"/api/antartida/datos/fechaini/{start}/fechafin/{end}/estacion/{station}"
    return client.get(f"{url}?{query}" if query else url)


def test_first_request_misses_the_cache_and_persists_the_rows(mock_aemet, client, cache):
    """An empty cache must trigger exactly one upstream call and fill SQLite."""
    response = call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")

    assert response.status_code == 200
    assert len(mock_aemet.calls) == 1
    assert len(response.json()["data"]) == 19  # 3 hours on a 10-minute grid
    assert len(cache.rows(STATION)) == 19


def test_repeated_request_is_served_without_calling_aemet(mock_aemet, client):
    """The whole point of Part 2: the second identical request never leaves the box."""
    call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")
    response = call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")

    assert len(mock_aemet.calls) == 1  # still one, the second request hit SQLite
    assert len(response.json()["data"]) == 19


def test_cache_hit_returns_each_measurement_exactly_once(mock_aemet, client):
    """
    Regression guard: both cache paths feed the same processor, so a request
    served from SQLite must be indistinguishable from a fresh upstream read.
    A duplicated append used to double every row on a cache hit.
    """
    first = call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")
    second = call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")

    assert first.json()["data"] == second.json()["data"]


def test_cache_hit_applies_the_same_aggregation_as_a_miss(mock_aemet, client):
    """Daily aggregation must give the same answer from cache and from upstream."""
    fresh = call(client, "2024-03-01T00:00:00", "2024-03-02T23:50:00", aggregation="Daily")
    cached = call(client, "2024-03-01T00:00:00", "2024-03-02T23:50:00", aggregation="Daily")

    assert len(mock_aemet.calls) == 1
    assert cached.json()["data"] == fresh.json()["data"]


def test_wider_range_refetches_the_days_that_are_missing(mock_aemet, client, cache):
    """
    Regression guard: coverage used to mean "at least one row inside the range",
    so widening the window returned a truncated dataset and never refreshed.
    """
    call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")
    assert len(mock_aemet.calls) == 1

    wider = call(client, "2024-01-01T00:00:00", "2024-01-02T03:00:00")

    assert len(mock_aemet.calls) == 2  # the extra day had to be fetched
    assert len(wider.json()["data"]) == 163  # 27 hours on a 10-minute grid, inclusive
    assert len(cache.rows(STATION)) == 163


def test_partially_cached_day_is_not_a_cache_hit(mock_aemet, client, cache):
    """
    Regression guard: comparing whole dates instead of instants accepted a day that
    only held two samples, answering a full-day request with two rows.
    """
    cache.seed(
        STATION,
        [datetime(2024, 2, 1, 0, 0), datetime(2024, 2, 1, 0, 10)],
    )

    response = call(client, "2024-02-01T00:00:00", "2024-02-01T23:00:00")

    assert len(mock_aemet.calls) == 1  # the partial day was correctly refetched
    assert len(response.json()["data"]) == 139  # a full day on a 10-minute grid


def test_grid_aligned_end_of_day_is_served_from_cache(mock_aemet, client):
    """
    A request ending at 23:59:59 covers the last sample of the day (23:50), so it
    must hit the cache. Comparing instants naively refetched this on every call.
    """
    call(client, "2024-01-01T00:00:00", "2024-01-01T23:59:59")
    response = call(client, "2024-01-01T00:00:00", "2024-01-01T23:59:59")

    assert len(mock_aemet.calls) == 1
    assert len(response.json()["data"]) == 144  # midnight to 23:50 inclusive


def test_refresh_replaces_rows_instead_of_duplicating_them(mock_aemet, client, cache):
    """Re-reading a range must swap its rows, never append a second copy."""
    call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")
    first = len(cache.rows(STATION))

    call(client, "2024-01-01T00:00:00", "2024-01-01T03:00:00")
    second = len(cache.rows(STATION))

    assert first == 19
    assert second == first


def test_empty_upstream_response_keeps_the_cache_intact(mock_aemet, client, cache):
    """
    Regression guard: purging the requested window used to run even when the
    upstream payload was empty, so a transient outage erased cached data.
    """
    call(client, "2024-02-01T00:00:00", "2024-02-01T23:00:00")
    assert len(cache.rows(STATION)) == 139

    monkey = pytest.MonkeyPatch()
    monkey.setattr("api.routes.fetch_aemet_data", lambda *_: [])
    try:
        # Wider than what we hold, so it takes the cache-miss branch
        response = call(client, "2024-02-01T00:00:00", "2024-02-10T00:00:00")
    finally:
        monkey.undo()

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert len(cache.rows(STATION)) == 139  # untouched


def test_cache_is_scoped_per_station(mock_aemet, client, cache):
    """Two stations must not share rows, which is what the station_id index is for."""
    call(client, "2024-01-01T00:00:00", "2024-01-01T01:00:00", station=STATION)
    call(client, "2024-01-01T00:00:00", "2024-01-01T01:00:00", station="Meteo Station Juan Carlos I")

    assert len(mock_aemet.calls) == 2
    assert len(cache.rows(STATION)) == 7
    assert len(cache.rows("Meteo Station Juan Carlos I")) == 7


def test_cache_hit_and_miss_are_logged(mock_aemet, client, caplog):
    """Part 2 also asks for proper logging, so both outcomes must be traceable."""
    with caplog.at_level("INFO"):
        call(client, "2024-01-01T00:00:00", "2024-01-01T01:00:00")
        call(client, "2024-01-01T00:00:00", "2024-01-01T01:00:00")

    messages = [record.getMessage() for record in caplog.records]

    assert any("CACHE MISS" in message for message in messages)
    assert any("CACHE HIT" in message for message in messages)