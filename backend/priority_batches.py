"""Real participation batches for the priority-inferior activity stream.

ICC uploads are weekly activity sources.  This module adds a small, explicit
grouping layer on top of those immutable sources.  Grouping never edits an
activity revision: within one real batch a matched TW is counted once, using
the latest activity amount; an unresolved activity row keeps its own stable
``icc:<date>/<recordKey>`` anchor and is never merged by name.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Callable
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from backend.priority_import import iso, normalized
from backend.followup_access import batch_read_user, can_followup, disabled_reason
from backend.shared_funding import attach as attach_funding


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS priority_product_batches (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        participation_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '筹备中',
        notes TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS priority_batch_activities (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        activity_date TEXT NOT NULL UNIQUE,
        activity_key TEXT NOT NULL UNIQUE,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(batch_id) REFERENCES priority_product_batches(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_priority_batch_activity_batch ON priority_batch_activities(batch_id, activity_date)",
    """CREATE TABLE IF NOT EXISTS priority_batch_roster (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        subject_key TEXT NOT NULL,
        activity_key TEXT NOT NULL DEFAULT '',
        tw_code TEXT NOT NULL DEFAULT '',
        customer_name TEXT NOT NULL DEFAULT '',
        priority_amount TEXT NOT NULL DEFAULT '0',
        block_amount TEXT NOT NULL DEFAULT '0',
        priority_manual INTEGER NOT NULL DEFAULT 0,
        block_manual INTEGER NOT NULL DEFAULT 0,
        priority_status TEXT NOT NULL DEFAULT 'none',
        block_status TEXT NOT NULL DEFAULT 'none',
        priority_actual_amount TEXT NOT NULL DEFAULT '',
        block_actual_amount TEXT NOT NULL DEFAULT '',
        block_buy_price TEXT NOT NULL DEFAULT '',
        block_share_quantity TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(batch_id, subject_key),
        FOREIGN KEY(batch_id) REFERENCES priority_product_batches(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_priority_batch_roster_batch_status ON priority_batch_roster(batch_id, priority_status, block_status)",
]

STATUSES = {"筹备中", "进行中", "已完成", "已取消"}


class ProductBatchPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    participationDate: str = Field(min_length=8, max_length=20)
    status: str = "筹备中"
    notes: str = Field(default="", max_length=4000)
    activityDates: list[str] = Field(default_factory=list, max_length=100)


class ProductBatchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    participationDate: str | None = Field(default=None, min_length=8, max_length=20)
    status: str | None = None
    notes: str | None = Field(default=None, max_length=4000)
    activityDates: list[str] | None = Field(default=None, max_length=100)


class RosterPayload(BaseModel):
    subjectKey: str = Field(min_length=1, max_length=250)
    board: str = "priority"
    status: str | None = None
    priorityAmount: str | float | int | None = None
    blockAmount: str | float | int | None = None
    priorityActualAmount: str | float | int | None = None
    blockActualAmount: str | float | int | None = None
    blockBuyPrice: str | float | int | None = None
    blockShareQuantity: str | float | int | None = None
    customerName: str = Field(default="", max_length=250)
    twCode: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=4000)


def _clean_date(value: str, label: str = "参与日期") -> str:
    try:
        return iso(value)
    except HTTPException as exc:
        raise HTTPException(422, f"{label}无效，请使用 YYYY-MM-DD。") from exc


def _date_key(value: str) -> str:
    return "icc:" + value


def _source_row(row: dict[str, Any]) -> int | None:
    value = row.get("sourceRow")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    record_key = str(row.get("recordKey") or "")
    if record_key.startswith("row:"):
        try:
            return int(record_key.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
    return None


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def _activity_rows(all_heads: dict[str, dict[str, Any]], dates: set[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset_key, dataset in all_heads.items():
        if dataset.get("kind") != "icc" or not dataset.get("head_id"):
            continue
        date = dataset.get("business_date")
        if dates is not None and date not in dates:
            continue
        for row in dataset.get("rows", []):
            item = dict(row)
            item["activityDate"] = date
            item["activityKey"] = dataset_key + "/" + str(row.get("recordKey", ""))
            item["subjectKey"] = "tw:" + row["twCode"] if row.get("twCode") else "icc:" + date + "/" + str(row.get("recordKey", ""))
            rows.append(item)
    return rows


def rollup_activity(conn: Any, all_heads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return activity rows collapsed only within explicitly mapped batches.

    Unmapped activity rows are retained one-for-one, preserving the historic
    weekly totals until an operator chooses a real participation batch.
    """
    mappings = {
        row["activity_date"]: dict(row)
        for row in conn.execute("SELECT * FROM priority_batch_activities").fetchall()
    }
    source = _activity_rows(all_heads)
    mapped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ungrouped: list[dict[str, Any]] = []
    for row in source:
        mapping = mappings.get(row["activityDate"])
        if mapping:
            row["productBatchId"] = mapping["batch_id"]
            mapped[mapping["batch_id"]].append(row)
        else:
            ungrouped.append(row)

    result: list[dict[str, Any]] = list(ungrouped)
    for batch_id, rows in mapped.items():
        by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_subject[row["subjectKey"]].append(row)
        for subject, candidates in by_subject.items():
            # Latest activity wins.  sourceRow is only a deterministic tie-breaker.
            latest = max(candidates, key=lambda row: (row.get("activityDate", ""), _source_row(row) or 0))
            result.append(dict(
                latest,
                productBatchId=batch_id,
                activityCount=len(candidates),
                activityDates=sorted({r["activityDate"] for r in candidates}),
                activitySources=[
                    {"date": r["activityDate"], "recordKey": r.get("recordKey", ""),
                     "sourceRow": _source_row(r), "activityKey": r.get("activityKey", "")}
                    for r in sorted(candidates, key=lambda value: (value.get("activityDate", ""), _source_row(value) or 0))
                ],
            ))
    return {"rows": result, "mappings": mappings, "sourceRows": source}


def summarize_batches(conn: Any, all_heads: dict[str, dict[str, Any]], user_scope: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    rows = rollup_activity(conn, all_heads)["rows"]
    batches = [dict(row) for row in conn.execute("SELECT * FROM priority_product_batches ORDER BY participation_date DESC, created_at DESC").fetchall()]
    activities = defaultdict(list)
    for row in conn.execute("SELECT * FROM priority_batch_activities ORDER BY activity_date").fetchall():
        activities[row["batch_id"]].append(dict(row))
    result = []
    for batch in batches:
        selected = [row for row in rows if row.get("productBatchId") == batch["id"] and (user_scope is None or user_scope(row))]
        positive = [row for row in selected if _decimal(row.get("agreementAmountUsd")) > 0]
        block_positive = [row for row in selected if _decimal(row.get("blockAgreementAmountUsd")) > 0]
        matched = {row["twCode"] for row in positive if row.get("twCode")}
        pending = sum(1 for row in positive if not row.get("twCode"))
        result.append(dict(batch,
                           participationDate=batch["participation_date"],
                           activityDates=[item["activity_date"] for item in activities.get(batch["id"], [])],
                           activityCount=len(activities.get(batch["id"], [])),
                           participantCount=len(matched),
                           pendingCount=pending,
                           recordCount=len(positive),
                           agreementUsd=str(sum((_decimal(row.get("agreementAmountUsd")) for row in positive), Decimal(0))),
                           blockParticipantCount=len({row.get("twCode") or row.get("subjectKey") for row in block_positive}),
                           blockRecordCount=len(block_positive),
                           blockAgreementUsd=str(sum((_decimal(row.get("blockAgreementAmountUsd")) for row in block_positive), Decimal(0)))))
    return result


def install(app: Any, db: Callable, current_user: Callable, now_iso: Callable,
            audit: Callable, heads: Callable, scoped: Callable, require_edit: Callable) -> None:
    with db() as conn:
        for statement in SCHEMA:
            conn.execute(statement)

    def check_status(status: str | None) -> str:
        value = str(status or "筹备中").strip()
        if value not in STATUSES:
            raise HTTPException(422, "参与批次状态无效。")
        return value

    def existing_activity_dates(conn: Any) -> set[str]:
        return {row["business_date"] for row in heads(conn).values() if row.get("kind") == "icc" and row.get("head_id")}

    def normalize_dates(conn: Any, values: list[str]) -> list[str]:
        dates = [_clean_date(value, "活动日期") for value in values if str(value or "").strip()]
        if not dates:
            raise HTTPException(422, "参与批次至少要导入一个完整的 ICC 活动批次；客户不能单独添加或移除。")
        if len(dates) != len(set(dates)):
            raise HTTPException(422, "活动日期不能重复。")
        missing = [value for value in dates if value not in existing_activity_dates(conn)]
        if missing:
            raise HTTPException(422, f"找不到这些 ICC 活动：{'、'.join(missing)}。请先上传对应活动资料。")
        return dates

    def write_activities(conn: Any, batch_id: str, dates: list[str], user: dict[str, Any]) -> None:
        collisions = conn.execute("SELECT activity_date,batch_id FROM priority_batch_activities WHERE activity_date IN (" + ",".join("?" for _ in dates) + ")", dates).fetchall() if dates else []
        conflicts = [row["activity_date"] for row in collisions if row["batch_id"] != batch_id]
        if conflicts:
            raise HTTPException(409, f"这些活动已经归入其他参与批次：{'、'.join(conflicts)}。一个活动只能归一个批次。")
        conn.execute("DELETE FROM priority_batch_activities WHERE batch_id=?", (batch_id,))
        for date in dates:
            conn.execute("INSERT INTO priority_batch_activities(id,batch_id,activity_date,activity_key,created_by,created_at) VALUES(?,?,?,?,?,?)",
                         (str(uuid4()), batch_id, date, _date_key(date), user["id"], now_iso()))

    def sync_roster(conn: Any, batch_id: str, all_heads: dict[str, dict[str, Any]], user: dict[str, Any]) -> None:
        for row in rollup_activity(conn, all_heads)["rows"]:
            if row.get("productBatchId") != batch_id:
                continue
            priority_amount = str(row.get("agreementAmountUsd") or "0")
            block_amount = str(row.get("blockAgreementAmountUsd") or "0")
            priority_status = "followup" if _decimal(priority_amount) > 0 else "none"
            block_status = "followup" if _decimal(block_amount) > 0 else "none"
            conn.execute('''INSERT INTO priority_batch_roster
                (id,batch_id,subject_key,activity_key,tw_code,customer_name,priority_amount,block_amount,
                 priority_status,block_status,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(batch_id,subject_key) DO UPDATE SET
                activity_key=excluded.activity_key,tw_code=excluded.tw_code,customer_name=excluded.customer_name,
                priority_amount=CASE WHEN priority_batch_roster.priority_manual=1 THEN priority_batch_roster.priority_amount ELSE excluded.priority_amount END,
                block_amount=CASE WHEN priority_batch_roster.block_manual=1 THEN priority_batch_roster.block_amount ELSE excluded.block_amount END,
                priority_status=CASE WHEN ((priority_batch_roster.priority_manual=1 AND CAST(priority_batch_roster.priority_amount AS DECIMAL)<=0) OR (priority_batch_roster.priority_manual=0 AND CAST(excluded.priority_amount AS DECIMAL)<=0)) AND priority_batch_roster.priority_status='followup' THEN 'none' ELSE priority_batch_roster.priority_status END,
                block_status=CASE WHEN ((priority_batch_roster.block_manual=1 AND CAST(priority_batch_roster.block_amount AS DECIMAL)<=0) OR (priority_batch_roster.block_manual=0 AND CAST(excluded.block_amount AS DECIMAL)<=0)) AND priority_batch_roster.block_status='followup' THEN 'none' ELSE priority_batch_roster.block_status END,
                updated_at=excluded.updated_at''',
                (str(uuid4()), batch_id, row["subjectKey"], row.get("activityKey", ""), row.get("twCode", ""),
                 row.get("canonicalName") or row.get("customerName", ""), priority_amount, block_amount,
                 priority_status, block_status, user["id"], now_iso(), now_iso()))

    def roster_items(conn: Any, batch_id: str, board: str, user: dict[str, Any], owners: dict, allowed: set[str]):
        status_col = "priority_status" if board == "priority" else "block_status"
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM priority_batch_roster WHERE batch_id=? AND {status_col}!='none' ORDER BY customer_name", (batch_id,)).fetchall()]
        result = []
        for row in rows:
            item = dict(row, id=row["id"], subjectKey=row["subject_key"], twCode=row["tw_code"],
                        name=row["customer_name"], agreementAmountUsd=row["priority_amount"],
                        blockAgreementAmountUsd=row["block_amount"], status=row[status_col], batchKey=batch_id)
            item.update(roster_row(item, batch_id, user, owners, allowed))
            result.append(item)
        return result

    def save_roster(conn: Any, batch_id: str, payload: RosterPayload, user: dict[str, Any]):
        if payload.board not in {"priority", "block"}:
            raise HTTPException(422, "业务板块无效。")
        batch = conn.execute("SELECT id FROM priority_product_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "参与批次不存在。")
        status_col = "priority_status" if payload.board == "priority" else "block_status"
        amount_col = "priority_amount" if payload.board == "priority" else "block_amount"
        status = payload.status or "followup"
        if status not in {"followup", "completed", "abandoned", "none"}:
            raise HTTPException(422, "客户状态无效。")
        current = conn.execute("SELECT * FROM priority_batch_roster WHERE batch_id=? AND subject_key=?", (batch_id, payload.subjectKey)).fetchone()
        if not current:
            amount = str(payload.priorityAmount if payload.board == "priority" else payload.blockAmount or "0")
            if _decimal(amount) <= 0:
                raise HTTPException(422, "意向金额必须大于 0。")
            conn.execute('''INSERT INTO priority_batch_roster(id,batch_id,subject_key,tw_code,customer_name,priority_amount,block_amount,
                priority_status,block_status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (str(uuid4()), batch_id, payload.subjectKey, payload.twCode, payload.customerName,
                 str(payload.priorityAmount or "0"), str(payload.blockAmount or "0"),
                 status if payload.board == "priority" else "none", status if payload.board == "block" else "none",
                 user["id"], now_iso(), now_iso()))
            current = conn.execute("SELECT * FROM priority_batch_roster WHERE batch_id=? AND subject_key=?", (batch_id, payload.subjectKey)).fetchone()
        updates = {status_col: status, "updated_at": now_iso()}
        amount = payload.priorityAmount if payload.board == "priority" else payload.blockAmount
        if amount is not None:
            if _decimal(amount) < 0:
                raise HTTPException(422, "意向金额不能小于 0。")
            updates[amount_col] = str(amount)
            updates['priority_manual' if payload.board == 'priority' else 'block_manual'] = 1
        if payload.priorityActualAmount is not None: updates["priority_actual_amount"] = str(payload.priorityActualAmount)
        if payload.blockActualAmount is not None: updates["block_actual_amount"] = str(payload.blockActualAmount)
        if payload.blockBuyPrice is not None: updates["block_buy_price"] = str(payload.blockBuyPrice)
        if payload.blockShareQuantity is not None: updates["block_share_quantity"] = str(payload.blockShareQuantity)
        if payload.notes: updates["notes"] = payload.notes
        if payload.board == "block":
            actual = _decimal(payload.blockActualAmount if payload.blockActualAmount is not None else current["block_actual_amount"])
            price = _decimal(payload.blockBuyPrice if payload.blockBuyPrice is not None else current["block_buy_price"])
            if status == "completed" and (actual <= 0 or price <= 0):
                raise HTTPException(422, "请先填写实际参与金额和买入股价，再完成客户。")
            if actual > 0 and price > 0:
                updates["block_share_quantity"] = str((actual / price).quantize(Decimal("0.0001")))
        conn.execute("UPDATE priority_batch_roster SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=?", (*updates.values(), current["id"]))
        return conn.execute("SELECT * FROM priority_batch_roster WHERE id=?", (current["id"],)).fetchone()

    def roster_context(conn: Any, user: dict[str, Any]):
        all_heads, _ = scoped(conn, batch_read_user(user))
        own_heads, visible = scoped(conn, user)
        allowed = {row['subjectKey'] for row in _activity_rows(own_heads)}
        allowed.update('tw:' + code for code in visible if str(code).startswith('TW'))
        owner_rows = conn.execute('''SELECT c.id,c.name,c.wechat_nickname,c.owner_id,c.owner_name,c.customer_code,c.funded_amount,i.normalized_value tw_code
            FROM customers c LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'
            WHERE c.archived_at IS NULL''').fetchall()
        owner_rows = attach_funding(conn, [dict(row) for row in owner_rows])
        owners = {row['tw_code'] or row['customer_code']: row for row in owner_rows
                  if row['tw_code'] or row['customer_code']}
        return all_heads, owners, allowed

    def roster_row(row, batch_id, user, owners, allowed):
        owner = owners.get(row.get('twCode'), {})
        owner_id = owner.get('owner_id') if owner else row.get('serviceOwnerId') or ''
        owner_name = owner.get('owner_name') if owner else row.get('serviceOwner') or '待分配'
        subject = row['subjectKey']
        readable = subject in allowed
        display_name = (row.get('canonicalName') or row.get('customerName') or owner.get('name')
                        or owner.get('wechat_nickname') or row.get('twCode') or '身份待确认')
        return dict(row, name=display_name,
                    subjectKey=subject, batchKey=batch_id, ownerId=owner_id, ownerName=owner_name,
                    funded_amount=owner.get('funded_amount'),
                    funding_source_kind=owner.get('funding_source_kind', ''),
                    funding_source_date=owner.get('funding_source_date', ''),
                    funding_source_label=owner.get('funding_source_label', ''),
                    funding_updated_at=owner.get('funding_updated_at', ''),
                    canOpenCard=readable, canFollowup=can_followup(user, owner_id, readable),
                    followupDisabledReason=disabled_reason(user, owner_id, readable))

    @app.get('/api/priority-inferior/product-batches')
    def product_batches(user=Depends(current_user)):
        with db() as conn:
            all_heads, owners, allowed = roster_context(conn, user)
            for batch in conn.execute("SELECT id FROM priority_product_batches").fetchall():
                sync_roster(conn, batch["id"], all_heads, user)
            items = summarize_batches(conn, all_heads)
            rolled = rollup_activity(conn, all_heads)['rows']
            for item in items:
                item['rows'] = [roster_row(row, item['id'], user, owners, allowed) for row in rolled
                                if row.get('productBatchId') == item['id']]
                item['rosterRows'] = roster_items(conn, item['id'], 'priority', user, owners, allowed)
                item['blockRows'] = roster_items(conn, item['id'], 'block', user, owners, allowed)
            mapped = {row["activity_date"]: row["batch_id"] for row in conn.execute("SELECT activity_date,batch_id FROM priority_batch_activities").fetchall()}
            activity_options = [{"date": dataset['business_date'], "batchId": mapped.get(dataset['business_date']),
                                 "records": len(dataset.get("rows", []))}
                                for dataset in sorted(all_heads.values(), key=lambda item: item.get("business_date", ""), reverse=True)
                                if dataset.get("kind") == "icc" and dataset.get("head_id")]
        return {"items": items, "activityOptions": activity_options, "statuses": sorted(STATUSES)}

    @app.post('/api/priority-inferior/product-batches', status_code=201)
    def create_product_batch(payload: ProductBatchPayload, user=Depends(current_user)):
        require_edit(user)
        name = payload.name.strip()
        participation_date = _clean_date(payload.participationDate)
        with db() as conn:
            dates = normalize_dates(conn, payload.activityDates)
            batch_id = str(uuid4())
            timestamp = now_iso()
            conn.execute("INSERT INTO priority_product_batches(id,name,participation_date,status,notes,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (batch_id, name, participation_date, check_status(payload.status), payload.notes.strip(), user["id"], timestamp, timestamp))
            write_activities(conn, batch_id, dates, user)
            all_heads, _ = scoped(conn, batch_read_user(user))
            sync_roster(conn, batch_id, all_heads, user)
            audit(conn, user, "priority.product_batch.created", "priority_product_batch", batch_id,
                  {"name": name, "participationDate": participation_date, "activityDates": dates})
        return {"id": batch_id}

    @app.patch('/api/priority-inferior/product-batches/{batch_id}')
    def update_product_batch(batch_id: str, payload: ProductBatchPatch, user=Depends(current_user)):
        require_edit(user)
        values = payload.model_dump(exclude_none=True)
        with db() as conn:
            current = conn.execute("SELECT * FROM priority_product_batches WHERE id=?", (batch_id,)).fetchone()
            if not current:
                raise HTTPException(404, "参与批次不存在。")
            before = dict(current)
            updates = {}
            if "name" in values:
                updates["name"] = str(values["name"]).strip()
            if "participationDate" in values:
                updates["participation_date"] = _clean_date(values["participationDate"])
            if "status" in values:
                updates["status"] = check_status(values["status"])
            if "notes" in values:
                updates["notes"] = str(values["notes"] or "").strip()
            dates = normalize_dates(conn, values["activityDates"]) if "activityDates" in values else None
            if updates:
                setters = ",".join(f"{key}=?" for key in updates)
                conn.execute(f"UPDATE priority_product_batches SET {setters},updated_at=? WHERE id=?", (*updates.values(), now_iso(), batch_id))
            if dates is not None:
                write_activities(conn, batch_id, dates, user)
            all_heads, _ = scoped(conn, batch_read_user(user))
            sync_roster(conn, batch_id, all_heads, user)
            audit(conn, user, "priority.product_batch.updated", "priority_product_batch", batch_id,
                  {"before": before, "changes": updates, "activityDates": dates})
            row = conn.execute("SELECT * FROM priority_product_batches WHERE id=?", (batch_id,)).fetchone()
        return {"item": dict(row)}

    @app.get('/api/priority-inferior/product-batches/{batch_id}')
    def product_batch_detail(batch_id: str, user=Depends(current_user)):
        with db() as conn:
            all_heads, owners, allowed = roster_context(conn, user)
            batch = conn.execute("SELECT * FROM priority_product_batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(404, "参与批次不存在。")
            dates = {row["activity_date"] for row in conn.execute("SELECT activity_date FROM priority_batch_activities WHERE batch_id=?", (batch_id,)).fetchall()}
            sync_roster(conn, batch_id, all_heads, user)
            rows = [row for row in rollup_activity(conn, all_heads)["rows"] if row.get("productBatchId") == batch_id]
            rows = [roster_row(row, batch_id, user, owners, allowed) for row in rows]
            followup_rows = roster_items(conn, batch_id, 'priority', user, owners, allowed)
            block_rows = roster_items(conn, batch_id, 'block', user, owners, allowed)
            summary = summarize_batches(conn, all_heads)
            item = next((value for value in summary if value["id"] == batch_id), None)
        return {"item": item or dict(batch), "activityDates": sorted(dates), "rows": rows,
                "rosterRows": followup_rows, "blockRows": block_rows}

    @app.get('/api/priority-inferior/product-batches/{batch_id}/roster')
    def product_batch_roster(batch_id: str, board: str = 'priority', user=Depends(current_user)):
        if board not in {'priority', 'block'}:
            raise HTTPException(422, '业务板块无效。')
        with db() as conn:
            all_heads, owners, allowed = roster_context(conn, user)
            if not conn.execute('SELECT 1 FROM priority_product_batches WHERE id=?', (batch_id,)).fetchone():
                raise HTTPException(404, '参与批次不存在。')
            sync_roster(conn, batch_id, all_heads, user)
            rows = roster_items(conn, batch_id, board, user, owners, allowed)
        return {'items': rows, 'board': board}

    @app.post('/api/priority-inferior/product-batches/{batch_id}/roster', status_code=201)
    def add_product_batch_roster(batch_id: str, payload: RosterPayload, user=Depends(current_user)):
        require_edit(user)
        with db() as conn:
            row = save_roster(conn, batch_id, payload, user)
            audit(conn, user, 'priority.product_batch.roster.updated', 'priority_batch_roster', row['id'],
                  {'batchId': batch_id, 'board': payload.board, 'status': payload.status or 'followup'})
        return {'item': dict(row)}

    @app.patch('/api/priority-inferior/product-batches/{batch_id}/roster/{roster_id}')
    def update_product_batch_roster(batch_id: str, roster_id: str, payload: RosterPayload, user=Depends(current_user)):
        require_edit(user)
        with db() as conn:
            current = conn.execute('SELECT * FROM priority_batch_roster WHERE id=? AND batch_id=?', (roster_id, batch_id)).fetchone()
            if not current:
                raise HTTPException(404, '批次客户不存在。')
            payload.subjectKey = current['subject_key']
            row = save_roster(conn, batch_id, payload, user)
        return {'item': dict(row)}

    @app.post('/api/priority-inferior/product-batches/{batch_id}/roster/{roster_id}/resolve')
    def resolve_product_batch_roster(batch_id: str, roster_id: str, payload: RosterPayload, user=Depends(current_user)):
        require_edit(user)
        if payload.status not in {'completed', 'abandoned', 'followup', 'none'}:
            raise HTTPException(422, '状态无效。')
        with db() as conn:
            current = conn.execute('SELECT * FROM priority_batch_roster WHERE id=? AND batch_id=?', (roster_id, batch_id)).fetchone()
            if not current:
                raise HTTPException(404, '批次客户不存在。')
            payload.subjectKey = current['subject_key']
            row = save_roster(conn, batch_id, payload, user)
        return {'item': dict(row)}

