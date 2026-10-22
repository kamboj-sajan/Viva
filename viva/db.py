"""Postgres access: a connection pool, the schema, and three small query helpers."""

import atexit
import pathlib
import threading
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from viva import config

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def pool() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            p = ConnectionPool(
                config.DATABASE_URL,
                min_size=1,
                max_size=10,
                kwargs={"row_factory": dict_row, "autocommit": True},
                open=True,
            )
            with p.connection() as conn:
                conn.execute((pathlib.Path(__file__).parent / "schema.sql").read_text())
            _pool = p
            atexit.register(p.close)
    return _pool


def query(sql: str, params=None) -> list[dict]:
    with pool().connection() as conn:
        return conn.execute(sql, params).fetchall()


def one(sql: str, params=None) -> dict | None:
    with pool().connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params=None) -> None:
    with pool().connection() as conn:
        conn.execute(sql, params)


@contextmanager
def transaction():
    with pool().connection() as conn, conn.transaction():
        yield conn


def vector(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"
