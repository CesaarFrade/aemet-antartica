"""
Upstream failure semantics — challenge Part 2.

The cache only pays off if a source-API outage is visible. These tests pin down
the contract the endpoint now exposes:

* the AEMET service failing  -> `502 Bad Gateway`
* AEMET answering with no observations -> `200 OK` with an empty `data` array

Everything is mocked at the `requests` level, so no network access is involved.
"""

import pytest
import requests

from core.exceptions import UpstreamAEMETError
from services import aemet_client

STATION = "Meteo Station Gabriel de Castilla"
STATION_CODE = "89064"


class FakeResponse:
    """Minimal stand-in for `requests.Response`."""

    def __init__(self, json_data=None, status=200, invalid_json=False):
        self._json = json_data
        self._status = status
        self._invalid_json = invalid_json

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self._status}")

    def json(self):
        if self._invalid_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    """The client reads the key at import time, so it has to be injected here."""
    monkeypatch.setattr(aemet_client, "AEMET_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Removes the retry backoff delay while keeping the retry logic in place."""
    monkeypatch.setattr(aemet_client.time, "sleep", lambda *_: None)


def metadata(data_url="https://example.test/payload"):
    return {"estado": 200, "descripcion": "datos OK", "datos": data_url}


@pytest.fixture
def observations():
    return [{"nombre": "S", "fhora": "2024-01-01T00:00:00UTC", "temp": 1.0, "pres": 1010.0, "vel": 5.0}]


def test_network_failure_raises_upstream_error(monkeypatch):
    """A dead connection must raise, not silently return an empty dataset."""
    monkeypatch.setattr(
        aemet_client.requests, "get",
        lambda *_, **__: (_ for _ in ()).throw(requests.exceptions.ConnectionError("boom")),
    )

    with pytest.raises(UpstreamAEMETError):
        aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION)


def test_transient_failure_is_retried_then_succeeds(monkeypatch, observations):
    """A single dropped connection must not fail the whole request."""
    responses = [
        requests.exceptions.ConnectionError("dropped"),
        FakeResponse(metadata()),
        FakeResponse(observations),
    ]
    calls = []

    def fake_get(*_, **__):
        calls.append(1)
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(aemet_client.requests, "get", fake_get)

    result = aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION)

    assert result == observations
    assert len(calls) == 3  # one failed attempt, then the two-step flow


def test_retries_are_bounded_and_report_the_attempt_count(monkeypatch):
    """Repeated failures stop at MAX_ATTEMPTS rather than looping forever."""
    attempts = []

    def fake_get(*_, **__):
        attempts.append(1)
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(aemet_client.requests, "get", fake_get)

    with pytest.raises(UpstreamAEMETError) as excinfo:
        aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION)

    assert len(attempts) == aemet_client.MAX_ATTEMPTS
    assert excinfo.value.attempts == aemet_client.MAX_ATTEMPTS


def test_every_request_carries_a_timeout(monkeypatch, observations):
    """No timeout means a stalled connection pins the worker forever."""
    payload_url = "https://example.test/payload"
    timeouts = []

    def fake_get(url, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return FakeResponse(observations) if url == payload_url else FakeResponse(metadata())

    monkeypatch.setattr(aemet_client.requests, "get", fake_get)
    aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION)

    assert len(timeouts) == 2  # both steps of the flow
    assert all(t == aemet_client.REQUEST_TIMEOUT for t in timeouts)


def test_http_error_status_raises_upstream_error(monkeypatch):
    """An API-level HTTP failure must not be read as 'no data'."""
    monkeypatch.setattr(aemet_client.requests, "get", lambda *_, **__: FakeResponse(status=500))

    with pytest.raises(UpstreamAEMETError):
        aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION)


def test_error_state_in_metadata_raises_upstream_error(monkeypatch):
    """AEMET's own `estado` code must be honoured."""
    monkeypatch.setattr(
        aemet_client.requests, "get",
        lambda *_, **__: FakeResponse({"estado": 401, "descripcion": "API key invalid", "datos": None}),
    )

    with pytest.raises(UpstreamAEMETError, match="401"):
        aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION)


def test_truncated_payload_raises_instead_of_returning_empty(monkeypatch):
    """
    A truncated payload is a failure, not an empty dataset. Returning `[]` here
    would let the caller purge the cached window for the range.
    """
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(metadata()) if len(calls) == 1 else FakeResponse(invalid_json=True)

    monkeypatch.setattr(aemet_client.requests, "get", fake_get)

    with pytest.raises(UpstreamAEMETError):
        aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION)


def test_successful_response_without_data_url_returns_empty_list(monkeypatch):
    """A valid answer with no payload URL means the station published nothing."""
    monkeypatch.setattr(aemet_client.requests, "get", lambda *_, **__: FakeResponse(metadata(data_url=None)))

    assert aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION) == []


# --- Endpoint-level contract ---


def test_endpoint_answers_502_on_upstream_outage(client, monkeypatch):
    """The outage must reach the caller instead of looking like an empty station."""
    def boom(*_, **__):
        raise UpstreamAEMETError("AEMET unreachable", attempts=3)

    monkeypatch.setattr("api.routes.fetch_aemet_data", boom)

    response = client.get(
        "/api/antartida/datos/fechaini/2024-01-01T00:00:00/fechafin/2024-01-01T01:00:00"
        f"/estacion/{STATION}"
    )

    assert response.status_code == 502
    assert "AEMET" in response.json()["detail"]


def test_endpoint_answers_200_with_empty_data_on_a_genuinely_empty_range(client, monkeypatch):
    """An empty but valid upstream answer stays a successful, empty response."""
    monkeypatch.setattr("api.routes.fetch_aemet_data", lambda *_: [])

    response = client.get(
        "/api/antartida/datos/fechaini/2024-01-01T00:00:00/fechafin/2024-01-01T01:00:00"
        f"/estacion/{STATION}"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["data"] == []


def test_outage_does_not_purge_the_cache(client, monkeypatch, cache):
    """A 502 must leave previously cached observations untouched."""
    url = (
        "/api/antartida/datos/fechaini/2024-02-01T00:00:00/fechafin/2024-02-01T23:00:00"
        f"/estacion/{STATION}"
    )
    monkeypatch.setattr("api.routes.fetch_aemet_data", lambda *_: [
        {"nombre": STATION, "fhora": f"2024-02-01T{hour:02d}:00:00UTC", "temp": 1.0, "pres": 1010.0, "vel": 5.0}
        for hour in range(24)
    ])
    assert client.get(url).status_code == 200
    assert len(cache.rows(STATION_CODE)) == 24

    def boom(*_, **__):
        raise UpstreamAEMETError("AEMET unreachable")

    monkeypatch.setattr("api.routes.fetch_aemet_data", boom)

    # Wider than what we hold, so it takes the cache-miss branch and reaches the client
    wider = url.replace("2024-02-01T23:00:00", "2024-02-05T00:00:00")
    assert client.get(wider).status_code == 502
    assert len(cache.rows(STATION_CODE)) == 24  # cache survived the outage