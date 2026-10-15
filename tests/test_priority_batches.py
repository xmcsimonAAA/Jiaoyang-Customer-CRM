"""Focused tests for real priority-inferior participation-batch rollups."""
from datetime import datetime, timezone
import sqlite3

from backend.priority_batches import SCHEMA, rollup_activity, summarize_batches


def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for statement in SCHEMA:
        conn.execute(statement)
    return conn


def heads(*datasets):
    return {dataset["dataset_key"]: dataset for dataset in datasets}


def activity(dataset_key, date, rows):
    return {"dataset_key": dataset_key, "kind": "icc", "business_date": date,
            "head_id": "revision-" + date, "rows": rows,
            "created_at": datetime.now(timezone.utc).isoformat()}


def test_same_tw_in_one_real_batch_uses_latest_amount_and_keeps_activity_dates():
    conn = db()
    conn.execute("INSERT INTO priority_product_batches VALUES (?,?,?,?,?,?,?,?)",
                 ("batch-1", "九月优先劣后", "2026-09-20", "进行中", "", "u", "now", "now"))
    for date in ("2026-09-09", "2026-09-16"):
        conn.execute("INSERT INTO priority_batch_activities VALUES (?,?,?,?,?,?)",
                     ("a-" + date, "batch-1", date, "icc:" + date, "u", "now"))
    source = heads(
        activity("icc:2026-09-09", "2026-09-09", [{"recordKey": "row:2", "sourceRow": 2, "twCode": "TW1", "customerName": "甲", "agreementAmountUsd": "100"}]),
        activity("icc:2026-09-16", "2026-09-16", [{"recordKey": "row:2", "sourceRow": 2, "twCode": "TW1", "customerName": "甲", "agreementAmountUsd": "250"}]),
    )
    rolled = rollup_activity(conn, source)["rows"]
    assert len(rolled) == 1
    assert rolled[0]["twCode"] == "TW1"
    assert rolled[0]["agreementAmountUsd"] == "250"
    assert rolled[0]["activityDates"] == ["2026-09-09", "2026-09-16"]
    assert [source["sourceRow"] for source in rolled[0]["activitySources"]] == [2, 2]
    summary = summarize_batches(conn, source)[0]
    assert summary["participantCount"] == 1
    assert summary["recordCount"] == 1
    assert summary["agreementUsd"] == "250"


def test_unmatched_same_name_is_never_merged_and_unmapped_activity_is_preserved():
    conn = db()
    conn.execute("INSERT INTO priority_product_batches VALUES (?,?,?,?,?,?,?,?)",
                 ("batch-1", "九月优先劣后", "2026-09-20", "筹备中", "", "u", "now", "now"))
    conn.execute("INSERT INTO priority_batch_activities VALUES (?,?,?,?,?,?)",
                 ("a-1", "batch-1", "2026-09-09", "icc:2026-09-09", "u", "now"))
    source = heads(
        activity("icc:2026-09-09", "2026-09-09", [
            {"recordKey": "row:2", "twCode": "", "customerName": "张三", "agreementAmountUsd": "100"},
        ]),
        activity("icc:2026-09-16", "2026-09-16", [
            {"recordKey": "row:2", "twCode": "", "customerName": "张三", "agreementAmountUsd": "200"},
        ]),
    )
    rolled = rollup_activity(conn, source)["rows"]
    assert len(rolled) == 2
    assert {row["subjectKey"] for row in rolled} == {"icc:2026-09-09/row:2", "icc:2026-09-16/row:2"}
    assert sorted(row["agreementAmountUsd"] for row in rolled) == ["100", "200"]
