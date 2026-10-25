import os
import re
from typing import Any, Dict, Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Default is PostgreSQL production target. Set DATABASE_URL or AEGIS_DATABASE_URL to override.
# SQLite remains supported as an optional test and development fallback.
DEFAULT_POSTGRES_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/aegis_rx"

RAW_DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("AEGIS_DATABASE_URL")
    or DEFAULT_POSTGRES_URL
)


def normalize_database_url(url: str) -> str:
    """
    Ensure PostgreSQL URLs use an installed driver (psycopg2 or psycopg).
    Normalizes legacy postgres:// and bare postgresql:// prefixes.
    """
    if url.startswith("postgres://"):
        return re.sub(r"^postgres://", "postgresql+psycopg2://", url, count=1)
    if url.startswith("postgresql://") and not (
        url.startswith("postgresql+psycopg2://")
        or url.startswith("postgresql+psycopg://")
        or url.startswith("postgresql+asyncpg://")
    ):
        return re.sub(r"^postgresql://", "postgresql+psycopg2://", url, count=1)
    return url


DATABASE_URL = normalize_database_url(RAW_DATABASE_URL)


class Base(DeclarativeBase):
    pass


def get_engine_args(url: str) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    if url.startswith("sqlite"):
        args["connect_args"] = {"check_same_thread": False}
    else:
        # Production-grade PostgreSQL pooling & connection health verification
        args["pool_pre_ping"] = True
        args["pool_recycle"] = int(os.getenv("DB_POOL_RECYCLE", "300"))
        args["pool_size"] = int(os.getenv("DB_POOL_SIZE", "10"))
        args["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    return args


engine = create_engine(
    DATABASE_URL,
    **get_engine_args(DATABASE_URL),
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable SQLite WAL journal mode and foreign key enforcement when running against SQLite."""
    if engine.dialect.name == "sqlite":
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        except Exception:
            pass


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
