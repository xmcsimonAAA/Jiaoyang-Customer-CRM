#!/usr/bin/env python3
"""Backfill the external Hongan advisor relation after the master workbook migration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = Path("/Users/simon/Desktop/测试表格完整版.xlsx")
PLACEHOLDERS = {"", "/", "-", "--", "#NAME?", "#VALUE!", "#REF!", "0"}
MIGRATION_USER = {"id": "system-migration", "name": "系统迁移", "rolePermission": "developer", "team": "系统后台"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--database", type=Path, default=ROOT_DIR / "customer_data.db")
    parser.add_argument("--apply", action="store_true", help="write the backfill after validation")
    return parser.parse_args()


def clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.upper() in PLACEHOLDERS else text


def identity_from_source(row: list[object]) -> tuple[str, str]:
    return clean(row[2]), clean(row[1])


def identity_from_customer(row: sqlite3.Row) -> tuple[str, str]:
    return clean(row["name"]), clean(row["wechat_nickname"])


def backup_database(database: Path) -> Path:
    checkpoint = sqlite3.connect(database)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        checkpoint.close()
    backup_dir = ROOT_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"customer_data-before-hongan-advisor-repair-{timestamp}.db"
    shutil.copy2(database, destination)
    return destination


def main() -> int:
    args = parse_args()
    source = args.source.expanduser().resolve()
    database = args.database.expanduser().resolve()
    os.environ["CUSTOMER_DB_PATH"] = str(database)
    sys.path.insert(0, str(ROOT_DIR))
    from backend.main import audit, init_db, now_iso, parse_import_file

    init_db()
    headers, source_rows, sheet_name = parse_import_file(source.name, source.read_bytes())
    if len(source_rows) != 1292 or len(headers) < 4 or headers[3] != "港安顾问":
        raise ValueError("源表不是预期的 1,292 条港安客户总表，已停止回填。")
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        customers = conn.execute(
            "SELECT id, customer_code, name, wechat_nickname, hongan_advisor FROM customers WHERE archived_at IS NULL ORDER BY CAST(SUBSTR(customer_code, 9) AS INTEGER)"
        ).fetchall()
        if len(customers) != len(source_rows):
            raise ValueError(f"活动客户应为 {len(source_rows)} 条，实际为 {len(customers)} 条。")
        code_numbers = [int(row["customer_code"].rsplit("-", 1)[-1]) for row in customers]
        if code_numbers != list(range(52, 52 + len(source_rows))):
            raise ValueError("活动客户编号不是正式迁移预期的连续区间，已停止回填。")
        for index, (customer, source_row) in enumerate(zip(customers, source_rows), start=1):
            if identity_from_customer(customer) != identity_from_source(source_row):
                raise ValueError(f"第 {index} 条客户的姓名/微信昵称与源表不一致，已停止回填。")
        changes = [
            (clean(source_row[3]), customer["id"], customer["customer_code"])
            for customer, source_row in zip(customers, source_rows)
            if clean(customer["hongan_advisor"]) != clean(source_row[3])
        ]
        report: dict[str, object] = {
            "mode": "apply" if args.apply else "dry-run",
            "source": str(source),
            "sheet": sheet_name,
            "sourceRows": len(source_rows),
            "activeCustomers": len(customers),
            "changedCustomers": len(changes),
            "nonEmptyHonganAdvisors": sum(bool(clean(row[3])) for row in source_rows),
            "uniqueHonganAdvisors": len({clean(row[3]) for row in source_rows if clean(row[3])}),
        }
        if not args.apply:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        backup = backup_database(database)
        migration_id = str(uuid4())
        timestamp = now_iso()
        conn.execute("BEGIN")
        try:
            for advisor, customer_id, _ in changes:
                conn.execute(
                    "UPDATE customers SET hongan_advisor=?, updated_at=?, version=version+1 WHERE id=? AND archived_at IS NULL",
                    (advisor, timestamp, customer_id),
                )
            audit(conn, MIGRATION_USER, "migration.hongan_advisors_backfilled", "migration", migration_id, {
                "changedCustomers": len(changes),
                "nonEmptyHonganAdvisors": report["nonEmptyHonganAdvisors"],
                "source": source.name,
            })
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        report.update({"backup": str(backup), "migrationId": migration_id})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
