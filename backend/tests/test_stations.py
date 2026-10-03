"""
Tests for the station registry and the station discovery endpoint.

The registry is the one place that decides what a station *is*, so these cases pin
down the resolution rules and the shape of the payload the dashboard consumes.
"""

import pytest

from core.exceptions import UpstreamAEMETError
from core.stations import (
    ANTARCTIC_STATIONS,
    canonical_station_id,
    get_all_stations,
    station_code_for_aemet,
)

STATIONS_ENDPOINT = "/api/antartida/estaciones"

# The two stations the challenge names, which the registry must always expose.
CHALLENGE_STATIONS = {
    "89064": "Meteo Station Gabriel de Castilla",
    "89070": "Meteo Station Juan Carlos I",
}


def test_registry_exposes_both_challenge_stations():
    """Part 1 of the brief names exactly two stations; both must be listed."""
    assert ANTARCTIC_STATIONS == CHALLENGE_STATIONS


def test_station_endpoint_lists_the_registry(client):
    """The dashboard populates its picker from this, so the payload must be complete."""
    response = client.get(STATIONS_ENDPOINT)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert {entry["id"]: entry["name"] for entry in body["data"]} == CHALLENGE_STATIONS


def test_station_endpoint_survives_an_upstream_outage(client, monkeypatch):
    """
    Discovery must stay available when the data route cannot.

    The endpoint reads the curated registry only, so during an AEMET outage the
    dashboard can still render its station picker while the data endpoint correctly
    reports `502`. That contrast is the property worth pinning.
    """

    def outage(*_args, **_kwargs):
        raise UpstreamAEMETError("AEMET is unreachable")

    monkeypatch.setattr("api.routes.fetch_aemet_data", outage)

    data_response = client.get(
        "/api/antartida/datos/fechaini/2024-01-01T00:00:00"
        "/fechafin/2024-01-01T01:00:00/estacion/89064"
    )
    stations_response = client.get(STATIONS_ENDPOINT)

    assert data_response.status_code == 502
    assert stations_response.status_code == 200
    assert len(stations_response.json()["data"]) == len(ANTARCTIC_STATIONS)


@pytest.mark.parametrize("code,name", CHALLENGE_STATIONS.items())
def test_a_registered_station_resolves_from_both_spellings(code, name):
    """
    The whole point of canonicalisation: one station, two accepted spellings.

    Both must collapse to the same key, otherwise the station is cached twice.
    """
    assert canonical_station_id(code) == code
    assert canonical_station_id(name) == code


@pytest.mark.parametrize("code,name", CHALLENGE_STATIONS.items())
def test_a_registered_name_resolves_regardless_of_case(code, name):
    """Names arrive from URLs and copy-paste, so casing must not create a new key."""
    assert canonical_station_id(name.upper()) == code
    assert canonical_station_id(name.lower()) == code


@pytest.mark.parametrize("code,_name", CHALLENGE_STATIONS.items())
def test_surrounding_whitespace_does_not_create_a_second_entry(code, _name):
    """
    A code pasted with a stray space must not be cached under its own key.

    Without trimming, `" 89064 "` would miss the registry, fall through to the
    pass-through branch and be stored as a distinct station.
    """
    assert canonical_station_id(f"  {code}  ") == code


def test_an_unregistered_code_passes_through_untouched():
    """
    The service must keep working for stations the registry does not know about.

    AEMET publishes more than the two stations in the brief, so an unknown code is
    forwarded verbatim instead of being rejected.
    """
    assert canonical_station_id("B99999") == "B99999"


def test_an_unregistered_code_is_still_trimmed():
    """Unknown identifiers get the same whitespace tolerance as known ones."""
    assert canonical_station_id("  B99999  ") == "B99999"


def test_aemet_request_path_and_cache_key_agree():
    """
    The outgoing URL and the cache key must never disagree.

    AEMET is sent the canonical code, so resolving it a second time has to be a
    no-op; otherwise the request and the cache would use different identifiers.
    """
    name = CHALLENGE_STATIONS["89064"]
    assert station_code_for_aemet(name) == canonical_station_id(name) == "89064"


def test_get_all_stations_returns_independent_dicts():
    """
    Callers must not be able to corrupt the registry through the returned list.

    Mutating one entry has to leave the next call untouched.
    """
    first = get_all_stations()
    first[0]["name"] = "tampered"

    assert get_all_stations()[0]["name"] != "tampered"
    assert ANTARCTIC_STATIONS["89064"] == CHALLENGE_STATIONS["89064"]


def test_every_listed_station_is_usable_as_a_path_parameter(client):
    """
    Anything the endpoint advertises must actually work on the data endpoint.

    This closes the loop between discovery and querying, so the dashboard can never
    offer a station that the data route then rejects.
    """
    for entry in client.get(STATIONS_ENDPOINT).json()["data"]:
        code, name = entry["id"], entry["name"]
        assert canonical_station_id(code) == code
        assert canonical_station_id(name) == code