#!/usr/bin/env python3
"""Copy one verified SQLite customer-data snapshot into an empty PostgreSQL database.

Run a dry check first, then stop local writes, make a SQLite backup, and run with
``--apply`` against that backup. The script never clears a populated PostgreSQL target.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.postgres_schema import POSTGRES_SCHEMA_STATEMENTS


TABLE_ORDER = [
    "counters",
    "user_permissions",
    "customers",
    "customer_identifiers",
    "assignments",
    "customer_collaborators",
    "advisor_alias_mappings",
    "advisor_bindings",
    "followups",
    "merge_events",
    "import_jobs",
    "audit_logs",
    "placement_batches",
    "customer_fields",
    "customer_field_values",
    "customer_holding_snapshots",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT_DIR / "customer_data.db", help="SQLite snapshot to migrate")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL URL; prefer DATABASE_URL environment variable")
    parser.add_argument("--apply", action="store_true", help="Create the PostgreSQL schema and copy the data")
    return parser.parse_args()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def source_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()]


def source_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0])
        for table in TABLE_ORDER
    }


def validate_source(conn: sqlite3.Connection) -> dict[str, int]:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"SQLite 完整性检查失败: {integrity}")
    existing = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing = [table for table in TABLE_ORDER if table not in existing]
    if missing:
        raise RuntimeError(f"SQLite 缺少必要数据表: {', '.join(missing)}")
    return source_counts(conn)


def ensure_schema(conn: Any) -> None:
    with conn.cursor() as cursor:
        for statement in POSTGRES_SCHEMA_STATEMENTS:
            cursor.execute(statement)


def target_columns(conn: Any, table: str) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position""",
            (table,),
        )
        return [row[0] for row in cursor.fetchall()]


def target_counts(conn: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cursor:
        for table in TABLE_ORDER:
            cursor.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}")
            counts[table] = int(cursor.fetchone()[0])
    return counts


def copy_table(source: sqlite3.Connection, target: Any, table: str) -> int:
    columns = source_table_columns(source, table)
    allowed = set(target_columns(target, table))
    unsupported = [column for column in columns if column not in allowed]
    if unsupported:
        raise RuntimeError(f"目标 PostgreSQL 表 {table} 缺少字段: {', '.join(unsupported)}")
    rows = source.execute(f"SELECT {', '.join(quote_identifier(column) for column in columns)} FROM {quote_identifier(table)}").fetchall()
    if not rows:
        return 0
    insert_sql = (
        f"INSERT INTO {quote_identifier(table)} ({', '.join(quote_identifier(column) for column in columns)}) "
        f"VALUES ({', '.join('%s' for _ in columns)})"
    )
    values = [tuple(row[column] for column in columns) for row in rows]
    with target.cursor() as cursor:
        cursor.executemany(insert_sql, values)
    return len(values)


def reset_identity_sequence(conn: Any) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT setval(
                pg_get_serial_sequence('customer_identifiers', 'id'),
                COALESCE((SELECT MAX(id) FROM customer_identifiers), 1),
                (SELECT COUNT(*) > 0 FROM customer_identifiers)
            )"""
        )


def main() -> int:
    args = parse_args()
    source_path = args.source.expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"找不到 SQLite 数据库快照: {source_path}")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        counts = validate_source(source)
        print(f"SQLite snapshot: {source_path}")
        print("Source rows:", counts)
        if not args.apply:
            print("Dry check completed. No PostgreSQL data was changed. Re-run with --apply after taking a final backup.")
            return 0
        if not args.database_url.startswith(("postgres://", "postgresql://")):
            raise SystemExit("请通过 DATABASE_URL 或 --database-url 提供 PostgreSQL 连接地址。")

        try:
            import psycopg
        except ImportError as exc:
            raise SystemExit("缺少 psycopg。请安装 requirements.txt。") from exc
        with psycopg.connect(args.database_url) as target:
            ensure_schema(target)
            existing = target_counts(target)
            nonempty = {table: count for table, count in existing.items() if count}
            if nonempty:
                raise RuntimeError(f"目标 PostgreSQL 已有数据，已停止迁移: {nonempty}")
            with target.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            copied = {table: copy_table(source, target, table) for table in TABLE_ORDER}
            reset_identity_sequence(target)
            verified = target_counts(target)
            if copied != counts or verified != counts:
                raise RuntimeError(f"迁移校验失败。source={counts}, copied={copied}, target={verified}")
            target.commit()
        print("Migration completed and row counts match:", counts)
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
