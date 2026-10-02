import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch
from sqlalchemy.pool import StaticPool
from main import app
from api.routes import get_db
from models.database import Base

# --- TEST DATABASE SETUP ---
# Use an in-memory SQLite database exclusively for tests to prevent modifying the real DB (Fixes A3)
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables in the in-memory database
Base.metadata.create_all(bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

# Inject the fake database session into the real FastAPI app
app.dependency_overrides[get_db] = override_get_db

# Create a test client that simulates a browser/user
client = TestClient(app)
# ---------------------------

def test_invalid_date_format_returns_400():
    """
    Test that sending a malformed date string triggers our custom 400 error.
    """
    response = client.get(
        "/api/antartida/datos/fechaini/hola/fechafin/2024-01-05T23:59:59/estacion/Meteo Station Gabriel de Castilla"
    )
    assert response.status_code == 400
    assert "Invalid date format" in response.json()["detail"]

def test_time_travel_dates_returns_400():
    """
    Test that start dates occurring after end dates trigger a 400 error.
    """
    response = client.get(
        "/api/antartida/datos/fechaini/2024-12-31T00:00:00/fechafin/2024-01-01T00:00:00/estacion/Meteo Station Gabriel de Castilla"
    )
    assert response.status_code == 400
    assert "Invalid date range" in response.json()["detail"]

# Patch decorator replaces the actual fetch_aemet_data function used in api.routes (Fixes A1)
@patch("api.routes.fetch_aemet_data")
def test_valid_request_processes_data_correctly(mock_fetch):
    """
    Tests the 'Happy Path' of the API's business logic using mock data.
    Verifies that Pandas correctly filters by data types and performs 
    time-based aggregations (e.g., calculating the mean) without hitting the real AEMET API.
    """
    # 1. Provide fake AEMET JSON response data to bypass external network calls
    mock_fetch.return_value = [
        {"nombre": "Test Station", "fhora": "2024-01-01T00:00:00UTC", "temp": 10.0, "vel": 5.0},
        {"nombre": "Test Station", "fhora": "2024-01-01T01:00:00UTC", "temp": 20.0, "vel": 15.0}
    ]

    # 2. Simulate a client requesting 'Daily' aggregation and filtering only by 'temperature'
    response = client.get(
        "/api/antartida/datos/fechaini/2024-01-01T00:00:00/fechafin/2024-01-02T00:00:00/estacion/1234?aggregation=Daily&data_types=temperature"
    )

    # 3. Assertions to guarantee logic integrity
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "success"
    
    # Since we requested 'Daily' aggregation, the two hourly records should merge into 1
    assert len(data["data"]) == 1 
    
    # Pandas should calculate the mathematical mean of 10.0 and 20.0 (which is 15.0)
    assert data["data"][0]["Temperature (ºC)"] == 15.0
    
    # Since we filtered by 'temperature', the 'Speed (m/s)' column must be removed
    assert "Speed (m/s)" not in data["data"][0]