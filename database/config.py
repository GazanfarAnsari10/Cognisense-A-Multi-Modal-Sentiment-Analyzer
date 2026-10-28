"""Database configuration for the Phase 6A platform foundation."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseSettings:
    database_url: str
    echo_sql: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    connect_timeout: int = 10

    @classmethod
    def from_environment(cls) -> "DatabaseSettings":
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError("DATABASE_URL is not set. Configure PostgreSQL before initializing the platform database.")

        return cls(
            database_url=url,
            echo_sql=os.getenv("DATABASE_ECHO", "false").strip().lower() in {"1", "true", "yes", "on"},
            pool_size=max(1, int(os.getenv("DATABASE_POOL_SIZE", "5"))),
            max_overflow=max(0, int(os.getenv("DATABASE_MAX_OVERFLOW", "10"))),
            pool_timeout=max(1, int(os.getenv("DATABASE_POOL_TIMEOUT", "30"))),
            connect_timeout=max(1, int(os.getenv("DATABASE_CONNECT_TIMEOUT", "10"))),
        )


def get_database_url() -> str:
    """Return the configured PostgreSQL database URL.

    The value must be supplied through an environment variable. This function is
    intentionally strict so the application does not silently fall back to a
    local in-memory database when PostgreSQL is not configured.
    """

    return DatabaseSettings.from_environment().database_url


def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings.from_environment()
