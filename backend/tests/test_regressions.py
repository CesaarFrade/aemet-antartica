"""
Regression guards for defects that shipped with a green test suite.

Every case here failed at some point while the 69 other tests kept passing, which
is the reason they exist. They fall into four groups:

* the schema bootstrap, which used to leave a fresh checkout with no tables at all;
* the SQLite configuration that Part 2 asks for;
* input validation on `location` and `data_types`;
* cache-write integrity when the upstream over-delivers or a writer races.
"""

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api import routes
from api.routes import _resolve_input_timezone
from core.exceptions import ConfigurationError
from models import database as db
from models.database import Base, MeteoRecord
from services import aemet_client

STATION_CODE = "89064"
START = "2024-01-01T00:00:00"
END = "2024-01-01T01:00:00"
ENDPOINT = f"/api/antartida/datos/fechaini/{START}/fechafin/{END}/estacion/{STATION_CODE}"


def call(client, start=START, end=END, station=STATION_CODE, **params):
    """
    Calls the challenge endpoint with inclusive bounds and query parameters.

    A parameter whose value is None is left out entirely, so an optional argument
    can be exercised as "not supplied" rather than as the literal string "None".
    """
    from urllib.parse import quote

    supplied = {k: v for k, v in params.items() if v is not None}
    query = "&".join(f"{key}={quote(str(value))}" for key, value in supplied.items())
    url = f"/api/antartida/datos/fechaini/{start}/fechafin/{end}/estacion/{station}"
    return client.get(f"{url}?{query}" if query else url)


def observations(hours=(0, 1), label="S", **overrides):
    """Builds an AEMET-shaped payload for the given UTC hours."""
    rows = []
    for hour in hours:
        row = {
            "nombre": label,
            "fhora": f"2024-01-01T{hour:02d}:00:00UTC",
            "temp": 1.0,
            "pres": 1010.0,
            "vel": 5.0,
        }
        row.update(overrides)
        rows.append(row)
    return rows


@pytest.fixture
def payload(monkeypatch):
    """Serves a fixed payload through the mocked upstream, bypassing the cache."""
    def install(rows):
        monkeypatch.setattr("api.routes.fetch_aemet_data", lambda *_: list(rows))
    return install


# --- Schema bootstrap: the defect that made a fresh checkout unusable ---


def test_a_brand_new_cache_file_gets_the_tables_created(tmp_path):
    """
    The single most damaging defect found: on a checkout with no cache file the
    bootstrap decided there was "nothing to rebuild", returned early, and never
    reached `create_all`. Every data endpoint then answered 500 with
    "no such table: meteo_records".

    The suite missed it because `conftest` builds its own in-memory schema, so the
    application bootstrap was never exercised.
    """
    fresh = create_engine(f"sqlite:///{tmp_path / 'meteo_cache.db'}")
    assert "meteo_records" not in inspect(fresh).get_table_names()

    db._prepare_schema(fresh)

    assert set(inspect(fresh).get_table_names()) == {
        "meteo_records",
        "station_cache_state",
        "station_coverage",
    }
    fresh.dispose()


def test_the_bootstrap_is_idempotent(tmp_path):
    """Running it against a correct file must not drop or damage anything."""
    fresh = create_engine(f"sqlite:///{tmp_path / 'meteo_cache.db'}")
    db._prepare_schema(fresh)

    session = sessionmaker(bind=fresh)()
    session.add(MeteoRecord(station_id=STATION_CODE, timestamp=dt.datetime(2024, 1, 1), temperature=3.0))
    session.commit()
    session.close()

    db._prepare_schema(fresh)

    with fresh.connect() as connection:
        rows = connection.execute(text("SELECT temperature FROM meteo_records")).fetchall()
    assert rows == [(3.0,)]  # still there: a correct file is left alone
    fresh.dispose()


def test_a_legacy_file_is_rebuilt(tmp_path):
    """A file without `station_name` predates the model and must be replaced."""
    legacy = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{legacy}")
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE meteo_records (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO meteo_records DEFAULT VALUES"))
        connection.commit()

    db._prepare_schema(engine)

    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(meteo_records)"))}
    assert "station_name" in columns and "timestamp" in columns
    engine.dispose()


def test_the_cache_path_does_not_depend_on_the_working_directory():
    """
    The URL used to be `sqlite:///./meteo_cache.db`, which SQLite resolves against
    the process CWD. Starting the app from the repository root and from `backend/`
    therefore opened two different databases. The path has to belong to the
    checkout, not to how the process was launched.
    """
    assert Path(db.DB_PATH).is_absolute()
    assert "meteo_cache.db" in db.SQLALCHEMY_DATABASE_URL
    assert db.DB_PATH == Path(__file__).resolve().parent.parent / "meteo_cache.db"


def test_the_cache_path_can_be_overridden(monkeypatch, tmp_path):
    """Deployments need to point the cache somewhere else."""
    monkeypatch.setenv("AEMET_DB_PATH", str(tmp_path / "elsewhere.db"))
    import importlib

    reloaded = importlib.reload(db)
    try:
        assert reloaded.DB_PATH == tmp_path / "elsewhere.db"
    finally:
        monkeypatch.delenv("AEMET_DB_PATH")
        importlib.reload(db)


# --- SQLite configuration: Part 2 asks for DB best practices ---


def test_wal_is_enabled_on_every_connection():
    """
    WAL is persisted in the database header, so it survives restarts. Readers
    stopping the writer is exactly the contention this service would hit, since
    Part 2 expects many concurrent readers and one writer.
    """
    with db.engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_synchronous_normal_reaches_new_connections():
    """
    `synchronous` is a per-connection setting and is NOT stored in the file, unlike
    `journal_mode`. Setting it once at import time left every later request on the
    default FULL, so the pragma had no effect at all. It has to hang off the
    `connect` event, which fires for each connection the pool opens.
    """
    db.engine.dispose()  # drop the pooled connection, so the next one is brand new
    try:
        with db.engine.connect() as connection:
            # 1 is NORMAL; 2 would be the untouched default.
            assert connection.execute(text("PRAGMA synchronous")).scalar() == 1
    finally:
        db.engine.dispose()


def test_the_engine_is_not_in_autocommit():
    """
    `isolation_level=AUTOCOMMIT` made every statement commit on its own, so the
    delete-then-insert swap lost its atomicity: a failure between the two left the
    window permanently empty, which is the exact scenario the surrounding comments
    claim to prevent.
    """
    assert db.engine.dialect.isolation_level is None


def test_the_swap_is_atomic_under_failure(tmp_path):
    """The delete must be undone when the insert half of the swap never lands."""
    engine = create_engine(f"sqlite:///{tmp_path / 'atomic.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)

    with session() as setup:
        setup.add_all([
            MeteoRecord(station_id=STATION_CODE, timestamp=dt.datetime(2024, 1, 1, 0), temperature=1.0),
            MeteoRecord(station_id=STATION_CODE, timestamp=dt.datetime(2024, 1, 1, 10), temperature=1.0),
        ])
        setup.commit()

    broken = session()
    broken.query(MeteoRecord).filter(MeteoRecord.station_id == STATION_CODE).delete()
    broken.add(MeteoRecord(station_id=STATION_CODE, timestamp=dt.datetime(2024, 1, 1, 0), temperature=99.0))
    broken.rollback()
    broken.close()

    with session() as verify:
        survivors = verify.query(MeteoRecord).filter(MeteoRecord.station_id == STATION_CODE).all()
    assert len(survivors) == 2
    assert all(row.temperature == 1.0 for row in survivors)
    engine.dispose()


# --- Input validation ---


def test_an_unknown_data_type_is_rejected(client):
    """
    `data_types` acts as a whitelist further down, so an unrecognised name is not
    an error there: it simply contributes no column. Validating at the edge turns
    a typo into 422 instead of a 200 that silently dropped every measurement.
    """
    response = call(client, data_types="temperatura")

    assert response.status_code == 422
    assert "temperatura" in response.json()["detail"]


def test_a_known_data_type_is_accepted(client, payload):
    """The three documented names must keep working."""
    payload(observations())

    assert call(client, data_types="temperature").status_code == 200


def test_the_payload_without_a_station_label_still_filters(client, payload):
    """
    The processor built its base column list unconditionally, so a payload with no
    `nombre` produced a KeyError and a 500 the moment `data_types` was used. AEMET
    omitting the label is a valid answer, not a broken request.
    """
    payload([{k: v for k, v in row.items() if k != "nombre"} for row in observations()])

    response = call(client, data_types="temperature")

    assert response.status_code == 200
    assert list(response.json()["data"][0].keys()) == ["Datetime", "Temperature (ºC)"]


def test_requesting_one_variable_keeps_buckets_missing_another(client, payload):
    """
    Empty time bins were dropped with `how='any'` across every numeric column
    before the requested columns were selected. Asking only for temperature
    therefore discarded whole hours in which pressure happened to be absent, even
    though temperature was present and requested.
    """
    payload([
        {"nombre": "S", "fhora": "2024-01-01T00:00:00UTC", "temp": 1.0, "pres": None, "vel": 5.0},
        {"nombre": "S", "fhora": "2024-01-01T00:10:00UTC", "temp": 2.0, "pres": None, "vel": 5.0},
        {"nombre": "S", "fhora": "2024-01-01T01:00:00UTC", "temp": 9.0, "pres": 1010.0, "vel": 5.0},
    ])

    response = call(
        client, end="2024-01-01T02:00:00", data_types="temperature", aggregation="Hourly"
    )

    assert response.status_code == 200
    assert len(response.json()["data"]) == 2  # the hour with no pressure survives


@pytest.mark.parametrize("location", ["garbage", "Europe/Nowhere", "abc", "+2", "+99:00", "+05:99", "+"])
def test_an_unusable_location_is_refused(client, location):
    """
    Anything that is neither a known IANA zone nor a `+/-HH:MM` offset used to fall
    through to UTC and be applied silently, shifting the request by hours.
    """
    assert _resolve_input_timezone_error(location) is True


def _resolve_input_timezone_error(location):
    from fastapi import HTTPException

    try:
        _resolve_input_timezone(location)
    except HTTPException as error:
        return error.status_code == 400
    return False


def test_a_legacy_iana_zone_is_honoured_rather_than_ignored():
    """
    `CET` and `EST` are real IANA identifiers. They used to be treated as UTC,
    which was wrong by one or two hours; they must now resolve to their true
    offset instead of being rejected or silently ignored.
    """
    reference = dt.datetime(2024, 1, 1, 12, 0, tzinfo=_resolve_input_timezone("CET"))

    assert reference.utcoffset() == dt.timedelta(hours=1)


def test_the_request_window_shifts_with_the_input_location(client, monkeypatch):
    """
    `location` states the zone the submitted instants are written in, so it has to
    be applied before the upstream call. Previously it was accepted and discarded,
    leaving the whole requirement of Part 1 unimplemented.
    """
    seen = {}

    def capture(start, end, station):
        seen["start"] = start
        seen["end"] = end
        return observations()

    monkeypatch.setattr("api.routes.fetch_aemet_data", capture)
    call(client, location="+02:00")

    # 00:00 at +02:00 is 22:00 UTC on the previous day.
    assert seen["start"] == "2023-12-31T22:00:00UTC"
    assert seen["end"] == "2023-12-31T23:00:00UTC"


@pytest.mark.parametrize(
    "location,expected_start",
    [
        ("Europe/Berlin", "2023-12-31T23:00:00UTC"),
        ("America/New_York", "2024-01-01T05:00:00UTC"),
        ("Asia/Tokyo", "2023-12-31T15:00:00UTC"),
        ("+05:30", "2023-12-31T18:30:00UTC"),
        ("-05:30", "2024-01-01T05:30:00UTC"),
        (None, "2024-01-01T00:00:00UTC"),
    ],
)
def test_every_accepted_location_form_converts_correctly(
    client, monkeypatch, location, expected_start
):
    """Zones, positive offsets and negative offsets all have to reach AEMET as UTC."""
    seen = {}

    def capture(start, end, station):
        seen["start"] = start
        # The window moved, so the payload has to move with it or nothing survives
        # the trim; only the outgoing bound is under assertion here.
        return [
            {"nombre": "S", "fhora": f"{start[:19]}", "temp": 1.0, "pres": 1010.0, "vel": 5.0},
            {"nombre": "S", "fhora": f"{end[:19]}", "temp": 2.0, "pres": 1010.0, "vel": 5.0},
        ]

    monkeypatch.setattr("api.routes.fetch_aemet_data", capture)
    response = call(client, location=location)

    assert seen["start"] == expected_start
    assert response.status_code == 200


def test_the_output_zone_is_still_madrid(client, monkeypatch):
    """
    The brief fixes the output to Europe/Madrid whatever the input zone was, so
    applying `location` must not have changed the rendering.
    """
    def capture(start, end, station):
        return [
            {"nombre": "S", "fhora": start[:19], "temp": 1.0, "pres": 1010.0, "vel": 5.0},
        ]

    monkeypatch.setattr("api.routes.fetch_aemet_data", capture)
    response = call(client, location="Asia/Tokyo")
    body = response.json()

    assert body["data"][0]["Datetime"].endswith("+01:00")  # Madrid in January
    assert body["location_resolved"] == "Europe/Madrid"
    assert body["location_requested"] == "Asia/Tokyo"


def test_the_response_stays_valid_json_without_measurements(client, monkeypatch):
    """A missing measurement must serialise as null, never as a bare NaN token."""
    monkeypatch.setattr("api.routes.fetch_aemet_data", lambda *_: [
        {"nombre": "S", "fhora": "2024-01-01T00:00:00UTC", "temp": None, "pres": 1010.0, "vel": 5.0}
    ])

    response = call(client)

    assert response.status_code == 200
    json.loads(response.text, parse_constant=lambda c: pytest.fail(f"invalid JSON literal {c}"))
    assert response.json()["data"][0]["Temperature (ºC)"] is None


# --- Cache-write integrity ---


def test_rows_outside_the_requested_window_are_not_stored(client, payload, engine):
    """
    AEMET answers with whole days whatever window was asked for. Those extra rows
    were stored even though the delete is scoped to the request, so they outlived
    the next refresh and then collided with it on the unique constraint, turning a
    routine refresh into a 500. Two requests differing only in `location` were
    enough to trigger it.
    """
    payload(observations(hours=range(6)))

    response = call(client, start="2024-01-01T05:00:00", end="2024-01-01T06:00:00")

    assert response.status_code == 200
    with sessionmaker(bind=engine)() as session:
        stored = session.query(MeteoRecord).filter(
            MeteoRecord.station_id == STATION_CODE
        ).all()
    assert [row.timestamp for row in stored] == [dt.datetime(2024, 1, 1, 5, 0)]
    assert len(response.json()["data"]) == 1


def test_a_lost_write_race_is_not_an_error(client, monkeypatch, cache):
    """
    Two writers refreshing one window is normal under load, not a client fault. The
    unique constraint turned the loser into a 500, and because the delete had
    already run it could also empty the window. The loser must roll back and answer.
    """
    # The upstream is stubbed for the whole test, including the seeding call, so no
    # code path here can reach the network.
    monkeypatch.setattr("api.routes.fetch_aemet_data", lambda *_: observations())
    call(client)  # populate the window
    before = len(cache.rows(STATION_CODE))
    assert before == 2

    def losing_commit(self):
        self.flush()
        raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    with monkeypatch.context() as patcher:
        patcher.setattr(Session, "commit", losing_commit)
        response = call(client)

    assert response.status_code == 200
    assert len(response.json()["data"]) == 2
    assert len(cache.rows(STATION_CODE)) == before  # the delete was rolled back


def test_a_real_race_does_not_corrupt_the_window(engine):
    """
    Two sessions replaying the same window, committing out of order. SQLite
    serialises them, and the loser has to end up with the window intact rather than
    half-written.
    """
    first = sessionmaker(bind=engine)()
    second = sessionmaker(bind=engine)()
    moments = [dt.datetime(2024, 1, 1, 0), dt.datetime(2024, 1, 1, 1)]

    first.query(MeteoRecord).filter(MeteoRecord.station_id == STATION_CODE).delete()
    first.add_all([MeteoRecord(station_id=STATION_CODE, timestamp=m, temperature=1.0) for m in moments])
    first.flush()

    second.query(MeteoRecord).filter(MeteoRecord.station_id == STATION_CODE).delete()
    second.add_all([MeteoRecord(station_id=STATION_CODE, timestamp=m, temperature=2.0) for m in moments])
    second.commit()

    try:
        first.commit()
    except IntegrityError:
        first.rollback()

    with sessionmaker(bind=engine)() as verify:
        rows = verify.query(MeteoRecord).filter(MeteoRecord.station_id == STATION_CODE).all()
    assert len(rows) == 2
    assert all(row.temperature == 2.0 for row in rows)
    first.close()
    second.close()


# --- Operational surface ---


def test_a_missing_api_key_raises_a_dedicated_configuration_error():
    """
    A bare ValueError escaped the endpoint's `except UpstreamAEMETError`, so a
    missing key surfaced as an opaque 500 that looked like an AEMET problem. It is
    a local misconfiguration and has its own type, kept distinct from the upstream
    failure so the two map to different status codes.
    """
    from fastapi import HTTPException

    from core.exceptions import UpstreamAEMETError

    assert issubclass(ConfigurationError, Exception)
    assert not issubclass(ConfigurationError, UpstreamAEMETError)
    # Must not be swallowed by a bare `except ValueError` on the request path.
    assert not issubclass(ConfigurationError, ValueError)
    assert not issubclass(ConfigurationError, HTTPException)


def test_the_client_reports_a_missing_key_as_a_configuration_error(monkeypatch):
    """The client itself has to raise the typed error, not a plain ValueError."""
    monkeypatch.setattr(aemet_client, "AEMET_API_KEY", None)

    with pytest.raises(ConfigurationError, match="AEMET_API_KEY"):
        aemet_client.fetch_aemet_data("2024-01-01T00:00:00UTC", "2024-01-01T01:00:00UTC", STATION_CODE)


def test_the_endpoint_answers_503_when_the_client_is_unconfigured(client, monkeypatch):
    def unconfigured(*_args, **_kwargs):
        raise ConfigurationError("AEMET_API_KEY is not set.")

    monkeypatch.setattr("api.routes.fetch_aemet_data", unconfigured)

    response = call(client)

    assert response.status_code == 503
    assert "AEMET_API_KEY" in response.json()["detail"]


def test_the_station_list_stays_available_while_data_is_unconfigured(client, monkeypatch):
    """Discovery must keep working when the data route cannot, so the UI can render."""
    def unconfigured(*_args, **_kwargs):
        raise ConfigurationError("AEMET_API_KEY is not set.")

    monkeypatch.setattr("api.routes.fetch_aemet_data", unconfigured)

    assert call(client).status_code == 503
    assert client.get("/api/antartida/estaciones").status_code == 200


def test_cors_does_not_combine_a_wildcard_with_credentials(client):
    """
    `allow_origins=["*"]` together with `allow_credentials=True` is rejected by
    browsers, which breaks every call from the dashboard. The origins are now
    listed explicitly and credentials are off.
    """
    response = client.get("/api/antartida/estaciones", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in response.headers


def test_the_health_probe_never_touches_aemet(client, monkeypatch):
    """
    Part 2 expects high availability during business hours, so a load balancer
    needs to tell "this instance is broken" apart from "AEMET is down". A probe
    that called AEMET would go red for both.
    """
    def unreachable(*_args, **_kwargs):
        raise AssertionError("the health probe must not call the upstream")

    monkeypatch.setattr("api.routes.fetch_aemet_data", unreachable)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

# --- Coverage spans and gap-aware refill ---


def _edges_only(start, end, station):
    """A payload with both edges present and nothing in between."""
    from datetime import datetime as _dt

    def stamp(text):
        return _dt.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")

    return [
        {"nombre": station, "fhora": stamp(start).strftime("%Y-%m-%dT%H:%M:%SUTC"),
         "temp": 1.0, "pres": 1010.0, "vel": 5.0},
        {"nombre": station, "fhora": stamp(end).strftime("%Y-%m-%dT%H:%M:%SUTC"),
         "temp": 2.0, "pres": 1010.0, "vel": 5.0},
    ]


def test_a_gap_between_two_cached_windows_is_not_served_as_a_hit(client, mock_aemet, cache):
    """
    The blind spot that `station_coverage` exists to close.

    Two windows a few hours apart both got cached. MIN/MAX over the station then
    reports [00:00 .. 18:00], which looks like it covers everything, and because
    freshness is tracked per station the stamp written by the second fetch also made
    the wider window look freshly fetched. A request spanning the space between them
    was answered from the cache with the middle missing and was never refilled.
    """
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")
    call(client, start="2024-01-01T12:00:00", end="2024-01-01T18:00:00")
    assert len(cache.spans(STATION_CODE)) == 2  # two genuinely separate windows

    response = call(client, start="2024-01-01T00:00:00", end="2024-01-01T18:00:00")

    assert len(mock_aemet.calls) == 3  # the hole was detected and refilled
    assert mock_aemet.calls[-1][0] == "2024-01-01T06:10:00UTC"  # from just after the first window
    assert len(cache.spans(STATION_CODE)) == 1  # now one continuous span
    # And the caller gets the whole window, not just the part that was refetched.
    assert len(response.json()["data"]) == 109  # 00:00..18:00 inclusive, 10-minute grid


def test_only_the_uncovered_tail_is_requested_upstream(client, mock_aemet, cache):
    """
    A rolling "last N hours" query keeps most of its window cached and valid, so
    refetching the entire span from the AEMET API wastes the one request that is
    actually expensive here.
    """
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")

    call(client, start="2024-01-01T04:00:00", end="2024-01-01T10:00:00")

    assert [recorded[0] for recorded in mock_aemet.calls] == [
        "2024-01-01T00:00:00UTC",
        "2024-01-01T06:10:00UTC",  # the cached head is reused, not refetched
    ]


def test_a_partially_refilled_window_still_answers_whole(client, mock_aemet):
    """
    The response has to describe the window the caller asked for. Returning only the
    freshly fetched tail would silently drop the cached hours they also requested.
    """
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")

    body = call(client, start="2024-01-01T04:00:00", end="2024-01-01T10:00:00").json()

    stamps = [row["Datetime"] for row in body["data"]]
    assert len(stamps) == 37  # 04:00..10:00 inclusive on a 10-minute grid
    assert stamps[0] == "2024-01-01T05:00:00+01:00"  # cached head, Madrid in January
    assert stamps[-1] == "2024-01-01T11:00:00+01:00"  # newly fetched tail


def test_adjacent_windows_fuse_into_one_span(client, mock_aemet, cache):
    """Day-by-day ingestion must not leave one row per day forever."""
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")
    call(client, start="2024-01-01T06:00:00", end="2024-01-01T12:00:00")
    call(client, start="2024-01-01T12:00:00", end="2024-01-01T18:00:00")

    assert cache.spans(STATION_CODE) == [(dt.datetime(2024, 1, 1, 0), dt.datetime(2024, 1, 1, 18))]


def test_a_genuine_upstream_gap_does_not_refetch_on_every_request(client, monkeypatch, cache):
    """
    The reason coverage records the window that was asked for rather than the rows
    that came back.

    Checking the row count against the theoretical grid looks stricter, but AEMET
    stations really do have sensor gaps. Every such gap would look like a permanent
    cache miss, so the endpoint would hammer the source API on every single request
    for that window forever. Recording the request instead treats an upstream gap as
    the upstream's answer.
    """
    calls = []

    def gappy(start, end, station):
        calls.append((start, end))
        return _edges_only(start, end, station)

    monkeypatch.setattr("api.routes.fetch_aemet_data", gappy)

    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")

    assert len(calls) == 1  # the gap does not turn the next request into a miss


def test_an_empty_answer_never_claims_a_window_is_covered(client, monkeypatch, cache):
    """
    Recording coverage for a window upstream did not fill would turn an empty answer
    into a permanent cache hit over nothing.
    """
    monkeypatch.setattr("api.routes.fetch_aemet_data", lambda *_: [])

    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")

    assert cache.spans(STATION_CODE) == []
    assert cache.rows(STATION_CODE) == []


def test_an_expired_window_is_refetched_end_to_end(client, mock_aemet, cache):
    """
    Gap-aware refill must not extend to expired windows: AEMET revises historical
    values, so refreshing only the missing tail would leave the cached head pinned
    to a superseded revision indefinitely.
    """
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")
    cache.backdate(STATION_CODE, minutes=routes.CACHE_TTL_MINUTES + 5)

    call(client, start="2024-01-01T04:00:00", end="2024-01-01T10:00:00")

    assert mock_aemet.calls[-1][0] == "2024-01-01T04:00:00UTC"  # whole window, not the tail

def test_restarting_the_app_keeps_the_coverage(client, mock_aemet, engine, cache):
    """
    Bootstrap runs on every startup, so anything it clears has to be cleared only
    when it rebuilds. Wiping the coverage table unconditionally would throw away
    the whole cache's memory of what it has seen and refetch the world on each
    restart, while looking perfectly healthy in the logs.
    """
    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")
    before = cache.spans(STATION_CODE)

    db._prepare_schema(target_engine=engine)  # what an app restart does

    assert cache.spans(STATION_CODE) == before

    call(client, start="2024-01-01T00:00:00", end="2024-01-01T06:00:00")
    assert len(mock_aemet.calls) == 1  # still a hit, nothing was invalidated


def test_a_legacy_rebuild_drops_the_coverage_that_described_the_old_rows(tmp_path):
    """
    The opposite guarantee: coverage that outlives the rows it describes would be
    served as a hit over a table that no longer holds them.
    """
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with legacy.connect() as connection:
        connection.execute(text(
            "CREATE TABLE meteo_records (id INTEGER NOT NULL, station_id VARCHAR, "
            "timestamp DATETIME, PRIMARY KEY (id))"
        ))
        connection.commit()

    db._prepare_schema(target_engine=legacy)
    with legacy.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM station_coverage")).scalar() == 0
    legacy.dispose()

