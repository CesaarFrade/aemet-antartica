"""
Endpoint contract tests — challenge Parts 1 and 2.

Test database, fixtures and helpers live in `conftest.py`.
"""

from unittest.mock import patch

STATION = "Meteo Station Gabriel de Castilla"


def test_invalid_date_format_returns_400(client):
    """A malformed date string must trigger the custom 400 error."""
    response = client.get(
        f"/api/antartida/datos/fechaini/hola/fechafin/2024-01-05T23:59:59/estacion/{STATION}"
    )

    assert response.status_code == 400
    assert "Invalid date format" in response.json()["detail"]


def test_time_travel_dates_returns_400(client):
    """A start date after the end date must be rejected before touching the cache."""
    response = client.get(
        f"/api/antartida/datos/fechaini/2024-12-31T00:00:00/fechafin/2024-01-01T00:00:00/estacion/{STATION}"
    )

    assert response.status_code == 400
    assert "Invalid date range" in response.json()["detail"]


@patch("api.routes.fetch_aemet_data")
def test_valid_request_processes_data_correctly(mock_fetch, client):
    """
    Happy path of the business logic with mocked upstream data: verifies that the
    requested aggregation merges the samples and that `data_types` filters the
    output columns, without reaching the real AEMET API.
    """
    mock_fetch.return_value = [
        {"nombre": "Test Station", "fhora": "2024-01-01T00:00:00UTC", "temp": 10.0, "vel": 5.0},
        {"nombre": "Test Station", "fhora": "2024-01-01T01:00:00UTC", "temp": 20.0, "vel": 15.0},
    ]

    response = client.get(
        "/api/antartida/datos/fechaini/2024-01-01T00:00:00/fechafin/2024-01-02T00:00:00"
        "/estacion/1234?aggregation=Daily&data_types=temperature"
    )

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"

    # Two hourly samples merged into a single daily bucket
    assert len(data["data"]) == 1

    # Pandas must average 10.0 and 20.0
    assert data["data"][0]["Temperature (ºC)"] == 15.0

    # Asking only for 'temperature' must drop the wind column
    assert "Speed (m/s)" not in data["data"][0]


def test_invalid_aggregation_returns_422(client):
    """`aggregation` is a Literal, so FastAPI rejects unknown values itself."""
    response = client.get(
        f"/api/antartida/datos/fechaini/2024-01-01T00:00:00/fechafin/2024-01-02T00:00:00"
        f"/estacion/{STATION}?aggregation=Yearly"
    )

    assert response.status_code == 422