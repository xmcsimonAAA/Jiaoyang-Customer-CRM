"""Backfill identity fields for the first Hong Kong master-sheet import."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree as ET

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EMPTY_MARKERS = {"/", "-", "—"}


def read_shared_strings(archive: ZipFile) -> list[str]:
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.itertext()) for node in root.findall("x:si", NS)]


def cell_value(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find("x:v", NS)
    if cell.get("t") == "s" and value is not None:
        return shared[int(value.text)]
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext())
    return value.text if value is not None else ""


def read_source_rows(source: Path) -> list[dict[str, str]]:
    with ZipFile(source) as archive:
        shared = read_shared_strings(archive)
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    result = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        row_number = int(row.get("r", "0"))
        if row_number < 5:
            continue
        if row_number > 55:
            break
        cells = {cell.get("r"): cell_value(cell, shared).strip() for cell in row.findall("x:c", NS)}
        result.append({"row": row_number, "name": cells.get(f"C{row_number}", ""), "nickname": cells.get(f"B{row_number}", "")})
    return result


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def build_plan(source: Path, database: Path) -> tuple[sqlite3.Connection, list[dict[str, str]], list[dict[str, str]]]:
    source_rows = read_source_rows(source)
    if len(source_rows) != 51:
        raise RuntimeError(f"原表预期 51 行，实际读取 {len(source_rows)} 行，未执行写入。")
    conn = sqlite3.connect(database, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    records = conn.execute(
        "SELECT id, customer_code, name, wechat_nickname FROM customers WHERE archived_at IS NULL ORDER BY customer_code"
    ).fetchall()
    if len(records) != 51:
        raise RuntimeError(f"系统预期 51 条历史记录，实际为 {len(records)} 条，未执行写入。")
    changes = []
    for index, (source_row, record) in enumerate(zip(source_rows, records), start=1):
        expected_suffix = f"-{index:05d}"
        normalized_source_name = "" if source_row["name"] in EMPTY_MARKERS else source_row["name"]
        if not record["customer_code"].endswith(expected_suffix) or record["name"].strip() != normalized_source_name:
            raise RuntimeError(
                f"第 {index} 条不匹配：原表姓名={source_row['name']!r}，系统={record['customer_code']} / {record['name']!r}，未执行写入。"
            )
        new_name = "" if source_row["name"] in EMPTY_MARKERS else record["name"]
        if record["wechat_nickname"].strip() != source_row["nickname"] or record["name"] != new_name:
            changes.append({"id": record["id"], "code": record["customer_code"], "name": new_name, "nickname": source_row["nickname"]})
    return conn, source_rows, changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill nickname and blank-name values from 港安客户总表")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="apply the verified plan in one transaction")
    args = parser.parse_args()
    conn, source_rows, changes = build_plan(args.source, args.database)
    try:
        report = {
            "matchedRows": len(source_rows),
            "nicknameBackfills": sum(bool(row["nickname"]) for row in changes),
            "placeholderNamesCleared": sum(not row["name"] for row in changes),
            "totalChanges": len(changes),
            "sample": [{key: row[key] for key in ("code", "name", "nickname")} for row in changes[:8]],
        }
        if args.apply:
            timestamp = now_iso()
            with conn:
                for row in changes:
                    conn.execute(
                        "UPDATE customers SET name=?, wechat_nickname=?, updated_at=?, version=version+1 WHERE id=?",
                        (row["name"], row["nickname"], timestamp, row["id"]),
                    )
                conn.execute(
                    "INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid4()),
                        "system",
                        "系统数据修复",
                        "customer.legacy_identity_backfilled",
                        "import_batch",
                        "hongan-master-20260814",
                        json.dumps(report, ensure_ascii=False),
                        timestamp,
                    ),
                )
            report["applied"] = True
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
