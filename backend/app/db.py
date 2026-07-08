"""Database engine, session factory, and declarative base.

The engine and session factory are created lazily on first use rather than at import, so the
application (and its models/routers) can be imported without a live database or AWS access.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """The process-wide SQLAlchemy engine, created on first use. pool_pre_ping guards against
    RDS dropping idle connections."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
    return _engine


def SessionLocal() -> Session:  # noqa: N802 - kept as a callable so existing call sites are unchanged
    """Create a new Session. Callable form preserves the previous `SessionLocal()` usage while
    deferring engine construction until first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _session_factory()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
