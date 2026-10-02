from sqlalchemy import (
    create_engine,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from core.logger import get_logger

logger = get_logger("database")

# This creates a local file named 'meteo_cache.db' in the backend folder
SQLALCHEMY_DATABASE_URL = "sqlite:///./meteo_cache.db"

# engine is the core interface to the database
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}  # Needed for SQLite in FastAPI
)

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
        return True  # Fresh file: `create_all` will build it correctly

    has_index = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": COMPOSITE_INDEX_NAME},
    ).first()

    return bool(has_index) and "station_name" in _columns(connection, "meteo_records")


def _rebuild_legacy_cache() -> None:
    """
    Rebuilds `meteo_records` when it predates the current schema.

    Without this, a cache file written by an older build would keep running without
    the uniqueness constraint, the composite index or the stored station name,
    silently degrading correctness and query performance. The cache is disposable
    by design, so the table is rebuilt and repopulates on the next request.
    """
    with engine.connect() as connection:
        if _is_current_schema(connection):
            return

        connection.execute(text("PRAGMA foreign_keys = OFF"))
        connection.execute(text("DROP TABLE meteo_records"))
        connection.commit()
        logger.warning(
            "Legacy meteo_cache.db detected (schema predates the current model). "
            "Rebuilding the cache table; it repopulates on the next request."
        )

    Base.metadata.create_all(bind=engine)


# Create the tables in the SQLite database automatically
_rebuild_legacy_cache()