"""
Shared pytest fixtures for the AEMET backend test suite.

Every test runs against an in-memory SQLite database injected through FastAPI's
dependency overrides, so the suite never reads or writes the real `meteo_cache.db`.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes import get_db
from main import app
from models.database import Base, MeteoRecord, StationCacheState
from core.stations import canonical_station_id

# Shape of the endpoint under test, as required by the challenge
ENDPOINT = "/api/antartida/datos/fechaini/{start}/fechafin/{end}/estacion/{station}"

# AEMET publishes on a 10-minute grid; mirrors routes._to_grid
GRID_MINUTES = 10


@pytest.fixture(scope="session")
def engine():
    """
    In-memory SQLite shared by the whole test session.

    `StaticPool` keeps a single connection alive, otherwise every new session
    would open a brand new empty `:memory:` database.
    """
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture(autouse=True)
def clean_cache(engine):
    """Empties the cache before every test so cases stay independent."""
    with sessionmaker(bind=engine)() as session:
        session.query(MeteoRecord).delete()
        session.query(StationCacheState).delete()
        session.commit()
    yield


@pytest.fixture
def client(engine):
    """A `TestClient` whose database session points at the in-memory database."""
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def aemet():
    """
    Builds AEMET-shaped payloads and serves them through a mocked client.

    Yields a `fetch` callable that mimics the upstream two-step endpoint: it
    receives the formatted UTC bounds and returns the 10-minute grid that a real
    station would publish for them. Records every call so cache behaviour can be
    asserted on the number of upstream round trips.
    """
    calls = []

    def payload(station, start, end, value=None):
        """`value` receives each timestamp, or defaults to its UTC hour."""
        current = datetime.strptime(start[:19], "%Y-%m-%dT%H:%M:%S")
        last = datetime.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")
        rows = []
        while current <= last:
            rows.append({
                "nombre": station,
                "fhora": current.strftime("%Y-%m-%dT%H:%M:%SUTC"),
                "temp": float(current.hour) if value is None else value,
                "pres": 1010.0,
                "vel": 5.0,
            })
            current += timedelta(minutes=GRID_MINUTES)
        return rows

    def fetch(start, end, station):
        calls.append((start, end, station))
        return payload(station, start, end)

    fetch.calls = calls
    fetch.payload = payload
    return fetch


@pytest.fixture
def mock_aemet(monkeypatch, aemet):
    """Replaces `api.routes.fetch_aemet_data` so no test touches the network."""
    monkeypatch.setattr("api.routes.fetch_aemet_data", aemet)
    return aemet


@pytest.fixture
def cache(engine):
    """Read/write access to the test cache, to seed and inspect persisted rows."""

    def rows(station):
        with sessionmaker(bind=engine)() as session:
            return session.query(MeteoRecord).filter(
                MeteoRecord.station_id == canonical_station_id(station)
            ).order_by(MeteoRecord.timestamp).all()

    def seed(station, timestamps, temperature=1.0):
        with sessionmaker(bind=engine)() as session:
            session.add_all([
                MeteoRecord(station_id=canonical_station_id(station), timestamp=ts, temperature=temperature)
                for ts in timestamps
            ])
            session.commit()

    def freshness(station):
        """Returns the station's `last_fetched_at`, or None when never refreshed."""
        with sessionmaker(bind=engine)() as session:
            state = session.query(StationCacheState).filter(
                StationCacheState.station_id == canonical_station_id(station)
            ).first()
            return state.last_fetched_at if state else None

    def backdate(station, minutes):
        """Ages a station's freshness stamp to simulate an expired cache entry."""
        with sessionmaker(bind=engine)() as session:
            state = session.query(StationCacheState).filter(
                StationCacheState.station_id == canonical_station_id(station)
            ).first()
            state.last_fetched_at -= timedelta(minutes=minutes)
            session.commit()

    return type("Cache", (), {
        "rows": staticmethod(rows),
        "seed": staticmethod(seed),
        "freshness": staticmethod(freshness),
        "backdate": staticmethod(backdate),
    })()