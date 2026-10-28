"""Lazy database engine/session factory helpers."""

from __future__ import annotations

from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_database_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _connect_args_for_url(database_url: str) -> dict[str, object]:
    settings = get_database_settings()
    if database_url.startswith("postgresql"):
        return {"connect_timeout": settings.connect_timeout}
    return {}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_database_settings()
        _engine = create_engine(
            settings.database_url,
            echo=settings.echo_sql,
            pool_pre_ping=True,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout,
            future=True,
            connect_args=_connect_args_for_url(settings.database_url),
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _session_factory


def get_session() -> Iterator[Session]:
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


__all__ = ["get_engine", "get_session_factory", "get_session"]
