from pathlib import Path
import os

from sqlalchemy import (
    create_engine,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from core.logger import get_logger

logger = get_logger("database")

# The cache lives next to this module, never in the current working directory.
#
# A relative `sqlite:///./meteo_cache.db` is resolved by SQLite against the CWD of
# the process, so launching the app from the repository root and from `backend/`
# silently opened two different databases. Resolving from `__file__` makes the
# cache a property of the checkout instead of of how the app was started.
# Override with AEMET_DB_PATH when the cache has to live somewhere else.
DB_PATH = Path(
    os.getenv("AEMET_DB_PATH") or Path(__file__).resolve().parent.parent / "meteo_cache.db"
)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

# engine is the core interface to the database
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},  # Needed for SQLite in FastAPI
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record):
    """
    Applies the SQLite pragmas every pooled connection needs.

    This has to hang off the `connect` event rather than being executed once at
    import time. `journal_mode` is stored in the database header so it survives,
    but `synchronous` is a per-connection setting: running it against a single
    connection would leave every later request on the default FULL, which is what
    the pragma was meant to avoid.

    WAL is what actually removes the writer contention of SQLite: readers no
    longer block the writer, which matters because this service is read-heavy and
    Part 2 of the brief expects many concurrent readers.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()

# SessionLocal will be used to create individual database sessions for each request
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for our database models
Base = declarative_base()


# --- DATABASE MODELS ---
class MeteoRecord(Base):
    """
    SQLAlchemy model representing the cached meteorological data.
    """
    __tablename__ = "meteo_records"
    __table_args__ = (
        # A station can only hold one reading per instant. The unique constraint is
        # what makes the cache idempotent: two concurrent requests for the same
        # window can no longer insert the same sample twice, and a re-read replaces
        # rows instead of growing the table.
        UniqueConstraint("station_id", "timestamp", name="uq_meteo_station_timestamp"),
        # Serves both cache queries: the per-station MIN/MAX coverage probe and the
        # windowed `timestamp BETWEEN ...` fetch. Having station_id as the leading
        # column makes this a superset of a standalone index on either column, so
        # the single-column indexes are redundant and are no longer declared.
        Index("ix_meteo_station_timestamp", "station_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(String)
    # The label AEMET itself publishes for the station. It has to be stored rather
    # than rebuilt from `station_id`, because the upstream name ("JCI Estacion
    # meteorologica") differs from both the station name and the AEMET code, and a
    # cache hit must return exactly what a cache miss would have returned.
    station_name = Column(String, nullable=True)
    timestamp = Column(DateTime)
    temperature = Column(Float, nullable=True)
    pressure = Column(Float, nullable=True)
    speed = Column(Float, nullable=True)


class StationCacheState(Base):
    """
    Per-station cache freshness.

    Part 2 of the challenge asks the cache to spare the source API, which implies
    the data must not become stale. Coverage alone cannot express that, because a
    fully covered range looks identical whether it was filled seconds ago or weeks
    ago. This table records when each station was last refreshed successfully.
    """
    __tablename__ = "station_cache_state"

    station_id = Column(String, primary_key=True)
    last_fetched_at = Column(DateTime, nullable=False)


class StationCoverage(Base):
    """
    Windows of a station that have been checked against the source API.

    Coverage used to be derived from `MIN(timestamp)`/`MAX(timestamp)` over
    `meteo_records`, which only proves the outer bounds: two spans recorded a day
    apart collapse into a single range, so a window straddling the space between
    them looked fully covered while the middle was empty. Worse, freshness is
    tracked per station, so a stamp written for the first span made the wider,
    hole-ridden window look freshly fetched too, and nothing refilled it.

    Holding each verified window as its own row keeps the gaps visible. Spans are
    stored disjoint and never grid-adjacent, so "one row contains the window" is a
    precise test rather than an optimistic guess.

    A row records the window that was *asked for*, not the rows that came back, and
    that is deliberate. A slot count check against the theoretical grid would look
    stricter, but AEMET stations have genuine sensor gaps, and any such gap would
    then look like a cache miss on every single request forever. Recording the
    request means an upstream gap is accepted as the upstream's answer. Anything
    genuinely missing is still repaired within one `CACHE_TTL_MINUTES`, because an
    expired span is always refetched end to end.
    """
    __tablename__ = "station_coverage"

    station_id = Column(String, primary_key=True)
    start_ts = Column(DateTime, primary_key=True)
    end_ts = Column(DateTime, nullable=False)
    # How many samples upstream actually sent for this window. Purely diagnostic:
    # it makes a short answer visible in the log without being used to reject a
    # hit, which would reintroduce the permanent-refetch problem above.
    slots = Column(Integer, nullable=True)

    Index("ix_station_coverage_span", "station_id", "start_ts", "end_ts")


COMPOSITE_INDEX_NAME = "ix_meteo_station_timestamp"


def _columns(connection, table):
    """Column names currently present in a SQLite table."""
    return {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}


def _is_current_schema(connection) -> bool:
    """
    Tells whether `meteo_records` already matches the declared model.

    `create_all` only ever runs CREATE TABLE IF NOT EXISTS, so an existing cache
    file silently keeps whatever schema it was first written with.
    """
    has_table = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meteo_records'")
    ).first()

    if not has_table:
        return True  # Nothing to rebuild; `create_all` below will build it

    has_index = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": COMPOSITE_INDEX_NAME},
    ).first()

    return bool(has_index) and "station_name" in _columns(connection, "meteo_records")


def _prepare_schema(target_engine=None) -> None:
    """
    Brings the cache file in line with the declared models, on every startup.

    Two cases have to be handled, and conflating them is what previously broke a
    fresh checkout:

    * a brand-new file has no `meteo_records` at all, so the models still need to
      be created;
    * a file written by an older build keeps its original schema, so the table has
      to be dropped and rebuilt.

    `create_all` therefore runs unconditionally at the end. It only issues CREATE
    TABLE IF NOT EXISTS, so it is a cheap no-op once the tables are already there.
    Deciding inside the `with` block and acting outside it also keeps the rebuild
    from running while another connection is still open on the same file.

    The cache is disposable by design, so dropping it is always safe: it repopulates
    from AEMET on the next request.

    Args:
        target_engine: Engine to prepare. Defaults to the application engine, and
            is injectable so the bootstrap can be exercised against a throwaway
            file instead of the real cache.
    """
    target_engine = target_engine or engine
    rebuilt = False

    with target_engine.connect() as connection:
        if _is_current_schema(connection):
            pass
        else:
            connection.execute(text("PRAGMA foreign_keys = OFF"))
            connection.execute(text("DROP TABLE meteo_records"))
            connection.commit()
            rebuilt = True
            logger.warning(
                "Legacy meteo_cache.db detected (schema predates the current model). "
                "Rebuilding the cache table; it repopulates on the next request."
            )

    Base.metadata.create_all(bind=target_engine)

    if rebuilt:
        # Coverage rows describe rows in `meteo_records`, so they cannot outlive a
        # rebuild of it: a span left behind would claim a window that no longer holds
        # data and would then be served as a hit. Clearing them here, and only here,
        # matters -- running this unconditionally would wipe the coverage of a
        # perfectly healthy cache on every restart and refetch the world each time.
        with target_engine.connect() as connection:
            connection.execute(text("DELETE FROM station_coverage"))
            connection.commit()


# Create the tables in the SQLite database automatically
_prepare_schema()