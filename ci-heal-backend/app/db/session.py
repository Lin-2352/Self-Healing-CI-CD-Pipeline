"""Engine and session construction.

`create_all` runs from the application lifespan, not at import time, so importing the app
does not create a database file.
"""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base


def make_engine(database_url: str) -> Engine:
    """Build an engine for `database_url`.

    In-memory SQLite uses one shared connection. Without that, each new connection would see
    an empty database and the tables created at startup would disappear.

    Args:
        database_url: A SQLAlchemy URL, for example `sqlite:///./ci_heal.db`.

    Returns:
        A configured engine. Tables are not created yet; call `init_db`.
    """
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        # `sqlite://` and `sqlite:///:memory:` both mean a private in-memory database.
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):
        # SQLite ignores foreign keys unless this is set on every connection.
        event.listen(engine, "connect", _configure_sqlite)
    return engine


# How long SQLite waits for a lock before returning SQLITE_BUSY. Five seconds covers a
# writer committing a pipeline result while the dashboard reads the same file.
_SQLITE_BUSY_TIMEOUT_MS = 5000


def _configure_sqlite(dbapi_connection: Any, _connection_record: object) -> None:
    """Enable foreign keys, WAL, and a busy timeout on one new SQLite connection.

    WAL lets a reader and a writer share a file database. An in-memory database stays
    on its own journal; the pragma is a no-op there and does not raise.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to `engine`.

    `expire_on_commit` is off so objects can be serialised into API responses after the
    request session commits and closes.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create any tables that do not exist yet."""
    Base.metadata.create_all(engine)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session, committing if the caller finishes and rolling back if it raises."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
