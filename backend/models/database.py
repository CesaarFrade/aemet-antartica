from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

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

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    temperature = Column(Float, nullable=True)
    pressure = Column(Float, nullable=True)
    speed = Column(Float, nullable=True)

# Create the tables in the SQLite database automatically
Base.metadata.create_all(bind=engine)