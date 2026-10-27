"""Focused tests for real priority-inferior participation-batch rollups."""
from datetime import datetime, timezone
import sqlite3
import pytest

from backend.database import PostgresRow
from backend.priority_batches import SCHEMA, migrate_legacy_participation_batches, rollup_activity, summarize_batches


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
    conn.execute("INSERT INTO priority_product_batches(id,name,participation_date,status,notes,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
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
    conn.execute("INSERT INTO priority_product_batches(id,name,participation_date,status,notes,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
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


def legacy_batch(conn):
    conn.execute('''INSERT INTO priority_product_batches
        (id,name,participation_date,priority_participation_date,block_participation_date,created_by,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)''',
        ('source-1', '10.22发车批次', '2026-10-22', '2026-10-22', '2026-11-01', 'manager', 'before', 'before'))


def legacy_customer(conn, key, status='followup', priority_card='', block_status='none'):
    conn.execute('''INSERT INTO priority_batch_roster
        (id,batch_id,subject_key,customer_name,priority_amount,block_amount,priority_status,block_status,
         priority_completion_batch_id,priority_actual_amount,block_actual_amount,created_by,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (key, 'source-1', 'tw:' + key, '合成客户', '100', '200', status, block_status, priority_card, '90', '180', 'manager', 'before', 'before'))


def migrated_db(mapping_rows):
    conn = db()
    if mapping_rows:
        conn.row_factory = lambda cursor, values: PostgresRow(zip((column[0] for column in cursor.description), values))
    return conn


@pytest.mark.parametrize('mapping_rows', [False, True])
def test_existing_operator_batch_becomes_named_card_without_inventing_completed_customers(mapping_rows):
    conn = migrated_db(mapping_rows)
    legacy_batch(conn)
    legacy_customer(conn, 'following')
    legacy_customer(conn, 'declined', status='abandoned')
    for card_id in ('bad-1', 'bad-2'):
        conn.execute('''INSERT INTO priority_completion_batches VALUES(?,?,?,?,?,?,?,?)''',
                     (card_id, 'priority', '历史完成 · name', 'priority_participation_date',
                      '系统迁移的历史完成客户，可编辑后重新归入发车批次。', 'system', 'before', 'before'))
    migrate_legacy_participation_batches(conn, 'now')
    cards = [dict(row) for row in conn.execute('SELECT * FROM priority_completion_batches').fetchall()]
    assert len(cards) == 1
    assert (cards[0]['board'], cards[0]['name'], cards[0]['participation_date']) == ('priority', '10.22发车批次', '2026-10-22')
    rows = [dict(row) for row in conn.execute('SELECT * FROM priority_batch_roster ORDER BY id').fetchall()]
    assert {row['priority_status'] for row in rows} == {'followup', 'abandoned'}
    assert all(not row['priority_completion_batch_id'] for row in rows)
    migrate_legacy_participation_batches(conn, 'later')
    assert [dict(row) for row in conn.execute('SELECT * FROM priority_completion_batches').fetchall()] == cards
    assert [dict(row) for row in conn.execute('SELECT * FROM priority_batch_roster ORDER BY id').fetchall()] == rows


@pytest.mark.parametrize('mapping_rows', [False, True])
def test_completed_rows_keep_amounts_and_explicit_assignments_during_card_repair(mapping_rows):
    conn = migrated_db(mapping_rows)
    legacy_batch(conn)
    conn.execute('INSERT INTO priority_completion_batches VALUES(?,?,?,?,?,?,?,?)',
                 ('manual', 'priority', '同事新建的第二车', '2026-11-10', '', 'manager', 'before', 'before'))
    conn.execute('INSERT INTO priority_completion_batches VALUES(?,?,?,?,?,?,?,?)',
                 ('bad', 'priority', '历史完成 · name', 'priority_participation_date',
                  '系统迁移的历史完成客户，可编辑后重新归入发车批次。', 'system', 'before', 'before'))
    legacy_customer(conn, 'completed', status='completed', block_status='completed')
    legacy_customer(conn, 'explicit', status='completed', priority_card='manual')
    legacy_customer(conn, 'broken', status='completed', priority_card='bad')
    before = {row['id']: dict(row) for row in conn.execute('SELECT * FROM priority_batch_roster').fetchall()}
    migrate_legacy_participation_batches(conn, 'now')
    cards = {row['id']: dict(row) for row in conn.execute('SELECT * FROM priority_completion_batches').fetchall()}
    assert 'bad' not in cards
    assert cards['manual']['name'] == '同事新建的第二车'
    for raw in conn.execute('SELECT * FROM priority_batch_roster').fetchall():
        row = dict(raw)
        for field in set(row) - {'priority_completion_batch_id', 'block_completion_batch_id'}:
            assert row[field] == before[row['id']][field]
        if row['id'] == 'explicit':
            assert row['priority_completion_batch_id'] == 'manual'
        else:
            card = cards[row['priority_completion_batch_id']]
            assert (card['name'], card['participation_date'], card['board']) == ('10.22发车批次', '2026-10-22', 'priority')
        if row['block_status'] == 'completed':
            card = cards[row['block_completion_batch_id']]
            assert (card['name'], card['participation_date'], card['board']) == ('10.22发车批次', '2026-11-01', 'block')


def test_valid_legacy_card_is_renamed_and_reused_without_duplicating_it():
    conn = db()
    legacy_batch(conn)
    conn.execute('INSERT INTO priority_completion_batches VALUES(?,?,?,?,?,?,?,?)',
                 ('legacy', 'priority', '历史完成 · 10.22发车批次', '2026-10-22',
                  '系统迁移的历史完成客户，可编辑后重新归入发车批次。', 'system', 'before', 'before'))
    legacy_customer(conn, 'completed', status='completed', priority_card='legacy')
    migrate_legacy_participation_batches(conn, 'now')
    cards = conn.execute('SELECT * FROM priority_completion_batches').fetchall()
    assert len(cards) == 1
    assert cards[0]['id'] == 'legacy' and cards[0]['name'] == '10.22发车批次'


def test_no_operator_batches_means_no_invented_cards():
    conn = db()
    migrate_legacy_participation_batches(conn, 'now')
    assert not conn.execute('SELECT * FROM priority_completion_batches').fetchall()
