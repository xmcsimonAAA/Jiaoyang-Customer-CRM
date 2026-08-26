#!/usr/bin/env python3
"""Validate and migrate the authoritative customer workbook into the CRM database.

The script is intentionally explicit about the source layout and refuses to mutate the
database unless the expected 1,292-row workbook and the existing 51-row seed data line up.
Run without --apply for a dry run; use --apply only after reviewing the report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = Path("/Users/simon/Desktop/测试表格完整版.xlsx")
MIGRATION_USER = {
    "id": "system-migration",
    "name": "系统迁移",
    "role": "developer",
    "rolePermission": "developer",
    "team": "系统后台",
}
UNASSIGNED_OWNER = {
    "id": "unassigned",
    "username": "unassigned",
    "name": "待分配",
    "role": "system",
    "roleLabel": "待分配",
    "rolePermission": "system",
    "team": "待分配池",
    "active": True,
}
EXPECTED_HEADERS = [
    "序号", "微信昵称", "真实姓名", "港安顾问", "骄阳顾问", "开户证券", "开户方式", "注册日期",
    "开户状态", "入金金额/USD", "买入数量", "账面市值", "资金流向", "备注(最后更新日期：2026.05.22)",
    "5.29持仓数量", "5.29持仓市值", "6.30持仓数量", "6.30持仓市值", "7.31持仓数量", "7.31持仓市值",
]
PLACEHOLDERS = {"", "/", "-", "--", "#NAME?", "#VALUE!", "#REF!"}
ACCOUNT_STATUS_MAP = {
    "未开户": "未启动", "提交中": "资料准备", "处理中": "开户审核", "开户失败": "开户失败",
    "已开户未入金": "已开户", "已开户入金": "已开户", "已开户": "已开户",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--database", type=Path, default=ROOT_DIR / "customer_data.db")
    parser.add_argument("--apply", action="store_true", help="write the migration after validation")
    return parser.parse_args()


def clean(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.upper() in PLACEHOLDERS else text


def number(value: object) -> float:
    text = clean(value).replace(",", "").replace("$", "").replace("￥", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"无法识别数字: {value!r}") from exc


def date_value(value: object) -> str | None:
    text = clean(value).replace("/", "-").replace(".", "-")
    return text if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text) else None


def load_workbook(source: Path) -> tuple[list[str], list[list[object]], str]:
    if not source.exists():
        raise FileNotFoundError(f"找不到源文件: {source}")
    from backend.main import parse_import_file

    headers, rows, sheet_name = parse_import_file(source.name, source.read_bytes())
    if len(rows) != 1292:
        raise ValueError(f"源表应有 1,292 条数据，实际识别到 {len(rows)} 条。")
    if len(headers) < len(EXPECTED_HEADERS):
        raise ValueError(f"源表应至少有 {len(EXPECTED_HEADERS)} 列，实际识别到 {len(headers)} 列。")
    for index, expected in enumerate(EXPECTED_HEADERS):
        actual = headers[index]
        if index == 13:
            if not actual.startswith("备注("):
                raise ValueError(f"第 14 列应为动态备注表头，实际为 {actual!r}。")
        elif actual != expected:
            raise ValueError(f"第 {index + 1} 列应为 {expected!r}，实际为 {actual!r}。")
    return headers, rows, sheet_name


def identity_tuple(row: sqlite3.Row | list[object]) -> tuple[str, str]:
    if isinstance(row, sqlite3.Row):
        return clean(row["name"]), clean(row["wechat_nickname"])
    return clean(row[2]), clean(row[1])


def validate_seed(conn: sqlite3.Connection, rows: list[list[object]]) -> dict[str, object]:
    active = conn.execute("SELECT * FROM customers WHERE archived_at IS NULL ORDER BY customer_code").fetchall()
    if len(active) != 51:
        raise ValueError(f"正式迁移前要求当前活动客户正好为 51 条，实际为 {len(active)} 条。")
    code_prefix = str(active[0]["customer_code"]).rsplit("-", 1)[0]
    expected_codes = [f"{code_prefix}-{index:05d}" for index in range(1, 52)]
    actual_codes = [row["customer_code"] for row in active]
    if actual_codes != expected_codes:
        raise ValueError("当前 51 条测试数据的客户编号不是连续的 KH-当前年份-00001 至 00051，已停止迁移。")
    expected = [identity_tuple(row) for row in rows[:51]]
    actual = [identity_tuple(row) for row in active]
    if actual != expected:
        raise ValueError("当前 51 条测试数据与正式表前 51 行的姓名/微信昵称顺序不一致，已停止迁移。")
    return {"activeSeedCount": len(active), "seedCodes": actual_codes}


def ensure_field(conn: sqlite3.Connection, label: str, field_type: str) -> str:
    row = conn.execute("SELECT id FROM customer_fields WHERE label = ?", (label,)).fetchone()
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    if row:
        conn.execute(
            "UPDATE customer_fields SET field_type=?, active=1, updated_by=?, updated_at=? WHERE id=?",
            (field_type, MIGRATION_USER["id"], timestamp, row["id"]),
        )
        return row["id"]
    field_id = str(uuid4())
    field_key = f"custom_{field_id.replace('-', '')[:12]}"
    display_order = conn.execute("SELECT COALESCE(MAX(display_order), -1) + 1 FROM customer_fields").fetchone()[0]
    conn.execute(
        "INSERT INTO customer_fields VALUES (?, ?, ?, ?, '[]', 1, ?, ?, ?, ?, ?)",
        (field_id, field_key, label, field_type, display_order, MIGRATION_USER["id"], timestamp, MIGRATION_USER["id"], timestamp),
    )
    return field_id


def field_values(row: list[object], field_ids: dict[str, str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for label, column in (("开户方式", 6), ("买入数量", 10), ("账面市值", 11)):
        raw = clean(row[column])
        if not raw:
            continue
        values[field_ids[label]] = number(raw) if label != "开户方式" else raw
    return values


def row_payload(row: list[object]) -> tuple[dict[str, object], list[dict[str, object]]]:
    nickname, name = clean(row[1]), clean(row[2])
    if not name and not nickname:
        raise ValueError("存在姓名和微信昵称都为空的记录。")
    source_advisor = clean(row[4])
    if source_advisor == "0":
        source_advisor = ""
    raw_account_status = clean(row[8])
    account_status = ACCOUNT_STATUS_MAP.get(raw_account_status, raw_account_status or "未启动")
    capital_destination = clean(row[12])
    participating = capital_destination == "参与定增"
    if participating:
        stage, intent_status, placement_status = "已参与定增", "已锁定", "已参与"
    elif account_status == "已开户":
        stage, intent_status, placement_status = "开户推进", "未确认", "未进入"
    elif account_status in {"资料准备", "开户审核"}:
        stage, intent_status, placement_status = "开户推进", "未确认", "未进入"
    else:
        stage, intent_status, placement_status = "新客户", "未确认", "未进入"
    payload: dict[str, object] = {
        "name": name,
        "wechatNickname": nickname,
        "source": "历史存量",
        "sourceDetail": "港安客户总表 · 2026.05.22",
        "stage": stage,
        "priority": "普通",
        "accountStatus": account_status,
        "accountBroker": clean(row[5]),
        "accountOpenedAt": date_value(row[7]),
        "brokerDepositAmount": number(row[9]),
        "capitalDestination": capital_destination,
        "intentStatus": intent_status,
        "placementStatus": placement_status,
        "hkAdvisor": clean(row[3]),
        "sourceAdvisorLabel": source_advisor,
        "notes": clean(row[13]),
        "collaboratorIds": [],
        "holdingSnapshots": [],
    }
    snapshots: list[dict[str, object]] = []
    for snapshot_date, quantity_index, market_index in (
        ("2026-05-29", 14, 15), ("2026-06-30", 16, 17), ("2026-07-31", 18, 19),
    ):
        quantity, market_value = number(row[quantity_index]), number(row[market_index])
        if quantity or market_value:
            snapshots.append({
                "snapshotDate": snapshot_date,
                "securityName": "二级市场持仓",
                "quantity": quantity,
                "marketValue": market_value,
                "sourceLabel": "港安历史持仓",
            })
    payload["holdingSnapshots"] = snapshots
    return payload, snapshots


def backup_database(database: Path) -> Path:
    checkpoint = sqlite3.connect(database)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        checkpoint.close()
    backup_dir = ROOT_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"customer_data-before-master-migration-{timestamp}.db"
    shutil.copy2(database, backup_path)
    return backup_path


def main() -> int:
    args = parse_args()
    database = args.database.expanduser().resolve()
    source = args.source.expanduser().resolve()
    os.environ["CUSTOMER_DB_PATH"] = str(database)
    sys.path.insert(0, str(ROOT_DIR))

    from backend.main import audit, create_customer_record, init_db, save_custom_values

    init_db()
    headers, rows, sheet_name = load_workbook(source)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        seed_report = validate_seed(conn, rows)
        field_report: dict[str, str] = {}
        for label, field_type in (("开户方式", "text"), ("买入数量", "number"), ("账面市值", "number")):
            existing = conn.execute("SELECT id FROM customer_fields WHERE label=? AND active=1", (label,)).fetchone()
            field_report[label] = existing["id"] if existing else "待创建"
        owner_report = {"待分配": len(rows)}
        advisor_labels = sorted({clean(row[4]) for row in rows if clean(row[4]) and clean(row[4]) != "0"})
        identity_report = {
            "nameAndNickname": sum(bool(clean(row[1])) and bool(clean(row[2])) for row in rows),
            "nameOnly": sum(bool(clean(row[2])) and not clean(row[1]) for row in rows),
            "nicknameOnly": sum(bool(clean(row[1])) and not clean(row[2]) for row in rows),
            "withoutIdentity": sum(not clean(row[1]) and not clean(row[2]) for row in rows),
            "withoutPhoneOrEmail": len(rows),
        }
        snapshot_count = 0
        for row in rows:
            _, snapshots = row_payload(row)
            snapshot_count += len(snapshots)
        report: dict[str, object] = {
            "mode": "apply" if args.apply else "dry-run",
            "source": str(source),
            "sheet": sheet_name,
            "headers": headers[:20],
            "sourceRows": len(rows),
            "seed": seed_report,
            "identity": identity_report,
            "ownerCounts": owner_report,
            "historicalAdvisorLabels": advisor_labels,
            "customFields": field_report,
            "holdingSnapshotRows": snapshot_count,
            "decision": "旧 51 条归档，正式表 1,292 条作为活动客户导入；所有历史顾问先进入待分配池并保留原始顾问字段。",
        }
        if not args.apply:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        backup_path = backup_database(database)
        migration_id = str(uuid4())
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        created_ids: list[str] = []
        conn.execute("BEGIN")
        try:
            field_ids = {label: ensure_field(conn, label, field_type) for label, field_type in (("开户方式", "text"), ("买入数量", "number"), ("账面市值", "number"))}
            conn.execute(
                "UPDATE customers SET archived_at=?, updated_at=?, version=version+1 WHERE archived_at IS NULL",
                (timestamp, timestamp),
            )
            archived_codes = [row["customer_code"] for row in conn.execute("SELECT customer_code FROM customers WHERE archived_at=?", (timestamp,)).fetchall()]
            audit(conn, MIGRATION_USER, "migration.legacy_seed_archived", "migration", migration_id, {"count": len(archived_codes), "codes": archived_codes})
            total_snapshots = 0
            for row in rows:
                payload, snapshots = row_payload(row)
                created = create_customer_record(conn, payload, UNASSIGNED_OWNER, MIGRATION_USER)
                save_custom_values(conn, created["id"], field_values(row, field_ids), MIGRATION_USER)
                created_ids.append(created["id"])
                total_snapshots += len(snapshots)
            quality = {
                "profile": "hongan_master",
                "identity": identity_report,
                "historicalAdvisorLabels": advisor_labels,
                "holdingSnapshotRows": total_snapshots,
                "archivedSeedCount": len(archived_codes),
                "unidentifiedRowsImported": len(rows),
            }
            conn.execute(
                "INSERT INTO import_jobs(id, filename, owner_id, owner_name, total_rows, created_count, conflict_count, error_count, imported_by, imported_by_name, created_at, data_quality_json, created_customer_ids_json) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)",
                (migration_id, source.name, UNASSIGNED_OWNER["id"], UNASSIGNED_OWNER["name"], len(rows), len(created_ids), MIGRATION_USER["id"], MIGRATION_USER["name"], timestamp, json.dumps(quality, ensure_ascii=False), json.dumps(created_ids, ensure_ascii=False)),
            )
            audit(conn, MIGRATION_USER, "migration.master_import_completed", "migration", migration_id, {"createdCount": len(created_ids), "archivedSeedCount": len(archived_codes), "holdingSnapshotRows": total_snapshots})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        report.update({
            "backup": str(backup_path),
            "migrationId": migration_id,
            "archivedSeedCount": len(archived_codes),
            "createdCount": len(created_ids),
            "customFields": field_ids,
            "holdingSnapshotRows": total_snapshots,
        })
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
