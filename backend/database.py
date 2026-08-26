"""Small database adapter used while the CRM moves from local SQLite to PostgreSQL.

The application started with SQLite and its SQL uses qmark parameters.  Keeping that
surface stable makes the production migration less risky: SQLite remains the default
for local development and tests, while PostgreSQL is selected only with DATABASE_URL.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


def uses_postgres(database_url: str) -> bool:
    return database_url.startswith(("postgres://", "postgresql://"))


class PostgresRow(dict[str, Any]):
    """Mapping row with SQLite Row's small positional-access convenience."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class PostgresCursor:
    def __init__(self, cursor: Any | None = None) -> None:
        self._cursor = cursor

    def fetchone(self) -> PostgresRow | None:
        if not self._cursor:
            return None
        try:
            row = self._cursor.fetchone()
            return PostgresRow(row) if row is not None else None
        finally:
            self._cursor.close()
            self._cursor = None

    def fetchall(self) -> list[PostgresRow]:
        if not self._cursor:
            return []
        try:
            return [PostgresRow(row) for row in self._cursor.fetchall()]
        finally:
            self._cursor.close()
            self._cursor = None


def _translate_sql(sql: str) -> str:
    """Translate the limited SQLite dialect used by this service to PostgreSQL."""
    translated = sql
    translated = re.sub(
        r"date\('now','-(\d+) days'\)",
        lambda match: f"(CURRENT_DATE - INTERVAL '{match.group(1)} days')::date",
        translated,
    )
    translated = re.sub(
        r"datetime\('now','-(\d+) days'\)",
        lambda match: f"(CURRENT_TIMESTAMP - INTERVAL '{match.group(1)} days')",
        translated,
    )
    translated = translated.replace("datetime('now')", "CURRENT_TIMESTAMP")
    translated = translated.replace("date('now')", "CURRENT_DATE")
    translated = re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", translated)
    return translated.replace("?", "%s")


class PostgresConnection:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - only reached in misconfigured production.
            raise RuntimeError("PostgreSQL 已启用，但未安装 psycopg。请重新安装 requirements.txt。") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self._connection = psycopg.connect(database_url, row_factory=dict_row)

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> PostgresCursor:
        cursor = self._connection.cursor(row_factory=self._dict_row)
        try:
            cursor.execute(_translate_sql(sql), params)
        except self._psycopg.IntegrityError as exc:
            cursor.close()
            # Existing endpoint handlers intentionally catch sqlite3.IntegrityError.
            raise sqlite3.IntegrityError(str(exc)) from exc
        except Exception:
            cursor.close()
            raise
        if cursor.description is None:
            cursor.close()
            return PostgresCursor()
        return PostgresCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


@contextmanager
def connection(database_url: str, sqlite_path: Path) -> Iterator[Any]:
    if uses_postgres(database_url):
        conn: Any = PostgresConnection(database_url)
    else:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(sqlite_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
