"""psycopg3 connection helpers (pool + one-off conn)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection (dict rows)."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def get_direct_conn() -> psycopg.Connection:
    """One-off (non-pooled) connection — handy for scripts / Lambda cold paths."""
    settings = get_settings()
    return psycopg.connect(settings.database_url, row_factory=dict_row)
