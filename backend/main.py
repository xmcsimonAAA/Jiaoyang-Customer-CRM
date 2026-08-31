from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from xml.etree import ElementTree as ET

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from opencc import OpenCC
from pydantic import BaseModel, Field

from backend.database import connection as database_connection
from backend.database import uses_postgres
from backend.postgres_schema import POSTGRES_SCHEMA_STATEMENTS


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("CUSTOMER_DB_PATH", ROOT_DIR / "customer_data.db")).expanduser()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DEFAULT_MUSKZOOM_DB = Path("/Users/simon/Documents/new project/sales_qa.db")
MUSKZOOM_DB_PATH = Path(os.getenv("MUSKZOOM_DB_PATH", DEFAULT_MUSKZOOM_DB)).expanduser()
SSO_SECRET = os.getenv("MUSKZOOM_SSO_SECRET", os.getenv("PARTNER_CRM_SSO_SECRET", "")).strip()
SSO_TTL = int(os.getenv("MUSKZOOM_SSO_TTL_SECONDS", "120"))
MUSKZOOM_IDENTITY_URL = os.getenv("MUSKZOOM_IDENTITY_URL", "").strip()
MUSKZOOM_IDENTITY_SECRET = os.getenv("MUSKZOOM_IDENTITY_SECRET", "").strip()
IDENTITY_CACHE_TTL = max(5, int(os.getenv("MUSKZOOM_IDENTITY_CACHE_SECONDS", "60")))
CRM_SESSION_COOKIE = os.getenv("CRM_SESSION_COOKIE", "jy_crm_session").strip() or "jy_crm_session"
CRM_COOKIE_SECURE = os.getenv("CRM_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}
CRM_ALLOW_PASSWORD_LOGIN = os.getenv("CRM_ALLOW_PASSWORD_LOGIN", "true" if not MUSKZOOM_IDENTITY_URL else "false").strip().lower() in {"1", "true", "yes"}
CRM_DEMO_MODE = os.getenv("CRM_DEMO_MODE", "false").strip().lower() in {"1", "true", "yes"}
SSO_AUDIENCE = os.getenv("MUSKZOOM_SSO_AUDIENCE", "jiaoyang-customer-crm").strip()
SSO_ISSUER = os.getenv("MUSKZOOM_SSO_ISSUER", "muskzoom").strip()
SSO_REQUIRE_STRONG_CLAIMS = os.getenv("MUSKZOOM_SSO_REQUIRE_STRONG_CLAIMS", "true" if MUSKZOOM_IDENTITY_URL else "false").strip().lower() in {"1", "true", "yes"}

ALLOWED_ROLE_KEYS = {"manager", "supervisor", "ceo", "developer"}
ROLE_LABELS = {"manager": "商务经理", "supervisor": "部门主管", "ceo": "管理员", "developer": "开发者"}
ROLE_PERMISSIONS = {"manager": "manager", "supervisor": "supervisor", "ceo": "admin", "developer": "developer"}
STAGES = ["新客户", "初步接洽", "开户推进", "定增意向", "批次推进", "资金准备", "已参与定增", "已流失", "暂缓"]
ACCOUNT_STATUSES = ["未启动", "资料准备", "开户审核", "已开户", "开户失败"]
INTENT_STATUSES = ["未确认", "有意向", "已锁定", "无意向"]
PLACEMENT_STATUSES = ["未进入", "意向跟进", "批次确认", "资金筹备", "资金到账", "已参与", "已流失"]
BATCH_STATUSES = ["筹备中", "开放中", "已截止", "已完成"]
SOURCES = ["线下沙龙", "线上活动", "渠道推荐", "客户转介绍", "自主拓展", "历史存量", "其他"]
FOLLOWUP_METHODS = ["电话", "微信", "面谈", "邮件", "活动", "其他"]
IMPORT_ROW_LIMIT = 5000
UNASSIGNED_OWNER_ID = "unassigned"
UNASSIGNED_OWNER = {
    "id": UNASSIGNED_OWNER_ID,
    "username": "unassigned",
    "name": "待分配",
    "role": "system",
    "roleLabel": "待分配",
    "rolePermission": "system",
    "team": "待分配池",
    "active": True,
}

DEMO_USERS = {
    "manager": {"id": "demo-manager", "username": "manager", "name": "演示顾问", "role": "manager", "team": "演示一组", "password": "manager123"},
    "manager2": {"id": "demo-manager-2", "username": "manager2", "name": "演示顾问二", "role": "manager", "team": "演示一组", "password": "manager2123"},
    "supervisor": {"id": "demo-supervisor", "username": "supervisor", "name": "演示主管", "role": "supervisor", "team": "演示一组", "password": "supervisor123"},
    "admin": {"id": "demo-admin", "username": "admin", "name": "演示管理员", "role": "developer", "team": "系统后台", "password": "admin123"},
}

_identity_user_cache: list[dict[str, Any]] = []
_identity_user_cache_expires_at = 0.0

app = FastAPI(title="骄阳客户数据系统", version="0.1.0")
OPENCC_T2S = OpenCC("t2s")
TRADITIONAL_VARIANT_OVERRIDES = str.maketrans({"暱": "昵"})


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def row_dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row else None


@contextmanager
def db() -> Iterator[Any]:
    with database_connection(DATABASE_URL, DB_PATH) as conn:
        yield conn


def init_sqlite_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS module_sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sso_handoffs (
                handoff_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                consumed_at TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_permissions (
                user_id TEXT PRIMARY KEY,
                can_import_customers INTEGER,
                can_export_all INTEGER,
                can_manage_customer_fields INTEGER,
                updated_by TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS counters (
                counter_key TEXT PRIMARY KEY,
                counter_value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                customer_code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                source_detail TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT '待联系',
                priority TEXT NOT NULL DEFAULT '普通',
                owner_id TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                owner_team TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                merged_into_id TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (merged_into_id) REFERENCES customers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_customers_owner ON customers(owner_id, archived_at);
            CREATE INDEX IF NOT EXISTS idx_customers_team ON customers(owner_team, archived_at);
            CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
            CREATE TABLE IF NOT EXISTS customer_identifiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                display_value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(kind, normalized_value),
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            CREATE TABLE IF NOT EXISTS assignments (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                from_owner_id TEXT,
                from_owner_name TEXT,
                from_team TEXT,
                to_owner_id TEXT NOT NULL,
                to_owner_name TEXT NOT NULL,
                to_team TEXT NOT NULL,
                reason TEXT NOT NULL,
                changed_by TEXT NOT NULL,
                changed_by_name TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            CREATE TABLE IF NOT EXISTS customer_collaborators (
                customer_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                user_team TEXT NOT NULL,
                collaborator_role TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (customer_id, user_id),
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_customer_collaborators_user ON customer_collaborators(user_id, customer_id);
            CREATE TABLE IF NOT EXISTS advisor_alias_mappings (
                alias TEXT PRIMARY KEY COLLATE NOCASE,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                user_team TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS advisor_bindings (
                id TEXT PRIMARY KEY,
                hongan_advisor TEXT NOT NULL COLLATE NOCASE,
                jiaoyang_advisor_label TEXT NOT NULL COLLATE NOCASE,
                jiaoyang_advisor_id TEXT,
                customer_type TEXT NOT NULL CHECK(customer_type IN ('non_placement', 'placement')),
                assignment_mode TEXT NOT NULL CHECK(assignment_mode IN ('default', 'manual')),
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(hongan_advisor, customer_type, assignment_mode)
            );
            CREATE INDEX IF NOT EXISTS idx_advisor_bindings_lookup ON advisor_bindings(hongan_advisor, customer_type, assignment_mode, active);
            CREATE TABLE IF NOT EXISTS followups (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                author_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                method TEXT NOT NULL,
                content TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT '',
                next_followup_at TEXT,
                stage_after TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_followups_customer ON followups(customer_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS merge_events (
                id TEXT PRIMARY KEY,
                source_customer_id TEXT NOT NULL,
                target_customer_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                merged_by TEXT NOT NULL,
                merged_by_name TEXT NOT NULL,
                merged_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS import_jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                total_rows INTEGER NOT NULL,
                created_count INTEGER NOT NULL,
                updated_count INTEGER NOT NULL DEFAULT 0,
                conflict_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL,
                imported_by TEXT NOT NULL,
                imported_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data_quality_json TEXT NOT NULL DEFAULT '{}',
                created_customer_ids_json TEXT NOT NULL DEFAULT '[]',
                rolled_back_at TEXT,
                rolled_back_by TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS placement_batches (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                product_name TEXT NOT NULL DEFAULT '定增项目',
                close_date TEXT,
                status TEXT NOT NULL DEFAULT '筹备中',
                target_amount REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customer_fields (
                id TEXT PRIMARY KEY,
                field_key TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL COLLATE NOCASE UNIQUE,
                field_type TEXT NOT NULL,
                options_json TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                display_order INTEGER NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customer_field_values (
                customer_id TEXT NOT NULL,
                field_id TEXT NOT NULL,
                value_text TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (customer_id, field_id),
                FOREIGN KEY (customer_id) REFERENCES customers(id),
                FOREIGN KEY (field_id) REFERENCES customer_fields(id)
            );
            CREATE INDEX IF NOT EXISTS idx_customer_field_values_field ON customer_field_values(field_id, customer_id);
            CREATE TABLE IF NOT EXISTS customer_holding_snapshots (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                security_name TEXT NOT NULL DEFAULT '二级市场持仓',
                quantity REAL NOT NULL DEFAULT 0,
                market_value REAL NOT NULL DEFAULT 0,
                source_label TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(customer_id, snapshot_date, security_name),
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_holding_snapshots_customer_date ON customer_holding_snapshots(customer_id, snapshot_date DESC);
            """
        )
        permission_columns = {row["name"] for row in conn.execute("PRAGMA table_info(user_permissions)").fetchall()}
        if "can_manage_customer_fields" not in permission_columns:
            conn.execute("ALTER TABLE user_permissions ADD COLUMN can_manage_customer_fields INTEGER")
        import_job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(import_jobs)").fetchall()}
        if "updated_count" not in import_job_columns:
            conn.execute("ALTER TABLE import_jobs ADD COLUMN updated_count INTEGER NOT NULL DEFAULT 0")
        if "data_quality_json" not in import_job_columns:
            conn.execute("ALTER TABLE import_jobs ADD COLUMN data_quality_json TEXT NOT NULL DEFAULT '{}'")
        if "created_customer_ids_json" not in import_job_columns:
            conn.execute("ALTER TABLE import_jobs ADD COLUMN created_customer_ids_json TEXT NOT NULL DEFAULT '[]'")
        if "rolled_back_at" not in import_job_columns:
            conn.execute("ALTER TABLE import_jobs ADD COLUMN rolled_back_at TEXT")
        if "rolled_back_by" not in import_job_columns:
            conn.execute("ALTER TABLE import_jobs ADD COLUMN rolled_back_by TEXT")
        customer_columns = {row["name"] for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
        migrations = {
            "account_status": "TEXT NOT NULL DEFAULT '未启动'",
            "account_broker": "TEXT NOT NULL DEFAULT ''",
            "account_opened_at": "TEXT",
            "intent_status": "TEXT NOT NULL DEFAULT '未确认'",
            "placement_status": "TEXT NOT NULL DEFAULT '未进入'",
            "target_batch_id": "TEXT",
            "intent_amount": "REAL NOT NULL DEFAULT 0",
            "funded_amount": "REAL NOT NULL DEFAULT 0",
            "actual_amount": "REAL NOT NULL DEFAULT 0",
            "lost_reason": "TEXT NOT NULL DEFAULT ''",
            "closed_at": "TEXT",
            "hongan_advisor": "TEXT NOT NULL DEFAULT ''",
            "source_advisor_label": "TEXT NOT NULL DEFAULT ''",
            "broker_deposit_amount": "REAL NOT NULL DEFAULT 0",
            "capital_destination": "TEXT NOT NULL DEFAULT ''",
            "wechat_nickname": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in migrations.items():
            if column not in customer_columns:
                conn.execute(f"ALTER TABLE customers ADD COLUMN {column} {definition}")


def init_postgres_db() -> None:
    with db() as conn:
        for statement in POSTGRES_SCHEMA_STATEMENTS:
            conn.execute(statement)


def init_db() -> None:
    if uses_postgres(DATABASE_URL):
        init_postgres_db()
        return
    init_sqlite_db()


init_db()


class LoginPayload(BaseModel):
    username: str
    password: str


class SsoPayload(BaseModel):
    token: str


class CustomerPayload(BaseModel):
    name: str = Field(default="", max_length=100)
    phone: str = ""
    email: str = ""
    wechatNickname: str = ""
    company: str = ""
    source: str = ""
    sourceDetail: str = ""
    stage: str = "新客户"
    priority: str = "普通"
    accountStatus: str = "未启动"
    accountBroker: str = ""
    brokerDepositAmount: float = 0
    capitalDestination: str = ""
    intentStatus: str = "未确认"
    placementStatus: str = "未进入"
    targetBatchId: str | None = None
    intentAmount: float = 0
    fundedAmount: float = 0
    actualAmount: float = 0
    lostReason: str = ""
    ownerId: str | None = None
    collaboratorIds: list[str] = Field(default_factory=list)
    hkAdvisor: str = ""
    sourceAdvisorLabel: str = ""
    holdingSnapshots: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""
    customValues: dict[str, Any] = Field(default_factory=dict)


class CustomerPatch(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    wechatNickname: str | None = None
    company: str | None = None
    source: str | None = None
    sourceDetail: str | None = None
    stage: str | None = None
    priority: str | None = None
    accountStatus: str | None = None
    accountBroker: str | None = None
    brokerDepositAmount: float | None = None
    capitalDestination: str | None = None
    accountOpenedAt: str | None = None
    intentStatus: str | None = None
    placementStatus: str | None = None
    targetBatchId: str | None = None
    intentAmount: float | None = None
    fundedAmount: float | None = None
    actualAmount: float | None = None
    lostReason: str | None = None
    hkAdvisor: str | None = None
    sourceAdvisorLabel: str | None = None
    closedAt: str | None = None
    notes: str | None = None
    customValues: dict[str, Any] | None = None
    changeReason: str | None = None
    version: int


class FollowupPayload(BaseModel):
    method: str
    content: str = Field(min_length=1)
    outcome: str = ""
    nextAction: str = ""
    nextFollowupAt: str | None = None
    stageAfter: str


class AssignPayload(BaseModel):
    ownerId: str
    reason: str = Field(min_length=1)


class CollaboratorPayload(BaseModel):
    userIds: list[str] = Field(default_factory=list)


class HoldingSnapshotPayload(BaseModel):
    snapshotDate: str
    securityName: str = "二级市场持仓"
    quantity: float = Field(default=0, ge=0)
    marketValue: float = Field(default=0, ge=0)
    sourceLabel: str = ""


class MergePayload(BaseModel):
    sourceCustomerId: str
    targetCustomerId: str
    reason: str = Field(min_length=1)


class ImportPreviewPayload(BaseModel):
    filename: str
    dataBase64: str


class ImportCommitPayload(BaseModel):
    filename: str
    ownerId: str | None = None
    rows: list[dict[str, Any]]
    mode: str = "append"
    advisorAliasMappings: dict[str, str] = Field(default_factory=dict)
    allowUnidentifiedRows: bool = False


class PermissionPayload(BaseModel):
    canImportCustomers: bool | None = None
    canExportAll: bool | None = None
    canManageCustomerFields: bool | None = None


class CustomerFieldPayload(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    fieldType: str = "text"
    options: list[str] = Field(default_factory=list)


class CustomerFieldPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=50)
    options: list[str] | None = None
    active: bool | None = None
    displayOrder: int | None = Field(default=None, ge=0)


class CustomerFieldValuePayload(BaseModel):
    value: Any = ""
    version: int


class BatchPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    closeDate: str | None = None
    status: str = "筹备中"
    targetAmount: float = Field(default=0, ge=0)
    notes: str = ""


class BatchPatch(BaseModel):
    name: str | None = None
    closeDate: str | None = None
    status: str | None = None
    targetAmount: float | None = Field(default=None, ge=0)
    notes: str | None = None


ADVISOR_CUSTOMER_TYPES = {"non_placement": "非定增", "placement": "定增"}
ADVISOR_ASSIGNMENT_MODES = {"default": "默认绑定", "manual": "手动分配"}


class AdvisorBindingPayload(BaseModel):
    honganAdvisor: str = Field(min_length=1, max_length=100)
    jiaoyangAdvisor: str = Field(min_length=1, max_length=100)
    jiaoyangAdvisorId: str | None = None
    customerType: str = "non_placement"
    assignmentMode: str = "default"
    active: bool = True
    notes: str = ""


class AdvisorBindingPatch(BaseModel):
    honganAdvisor: str | None = Field(default=None, min_length=1, max_length=100)
    jiaoyangAdvisor: str | None = Field(default=None, min_length=1, max_length=100)
    jiaoyangAdvisorId: str | None = None
    customerType: str | None = None
    assignmentMode: str | None = None
    active: bool | None = None
    notes: str | None = None
    changeReason: str | None = None


def hash_platform_password(password: str) -> str:
    return hashlib.sha256(f"sales-qa::{password}".encode()).hexdigest()


def identity_provider_enabled() -> bool:
    return bool(MUSKZOOM_IDENTITY_URL and MUSKZOOM_IDENTITY_SECRET)


def platform_available() -> bool:
    return identity_provider_enabled() or MUSKZOOM_DB_PATH.is_file()


def remote_platform_users() -> list[dict[str, Any]]:
    """Read the canonical active-account list from MuskZoom over a private API."""
    global _identity_user_cache, _identity_user_cache_expires_at
    now = time.monotonic()
    if _identity_user_cache and now < _identity_user_cache_expires_at:
        return _identity_user_cache
    request = urllib.request.Request(
        MUSKZOOM_IDENTITY_URL,
        headers={"X-Customer-Module-Secret": MUSKZOOM_IDENTITY_SECRET, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        if _identity_user_cache:
            return _identity_user_cache
        raise HTTPException(503, "暂时无法同步 MuskZoom 账号权限，请稍后重试。") from exc
    raw_users = body.get("users") if isinstance(body, dict) else None
    if not isinstance(raw_users, list):
        raise HTTPException(503, "MuskZoom 身份接口返回的数据格式无效。")
    users: list[dict[str, Any]] = []
    for raw in raw_users:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip()
        if role not in ALLOWED_ROLE_KEYS:
            continue
        user_id = str(raw.get("id") or "").strip()
        username = str(raw.get("username") or "").strip()
        if not user_id or not username:
            continue
        users.append(public_platform_user({
            "id": user_id,
            "username": username,
            "name": str(raw.get("name") or username).strip(),
            "role": role,
            "roleLabel": str(raw.get("roleLabel") or ROLE_LABELS.get(role, role)).strip(),
            "rolePermission": str(raw.get("rolePermission") or ROLE_PERMISSIONS.get(role, "manager")).strip(),
            "team": str(raw.get("team") or "").strip(),
            "active": bool(raw.get("active", True)),
        }))
    _identity_user_cache = users
    _identity_user_cache_expires_at = now + IDENTITY_CACHE_TTL
    return users


def platform_users(include_inactive: bool = False) -> list[dict[str, Any]]:
    if identity_provider_enabled():
        result = remote_platform_users()
    elif MUSKZOOM_DB_PATH.is_file():
        conn = sqlite3.connect(f"file:{MUSKZOOM_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            role_rows = conn.execute("SELECT role_key, label, permission FROM role_configs").fetchall()
            roles = {row["role_key"]: dict(row) for row in role_rows}
            query = "SELECT id, username, name, role, team, active FROM users"
            if not include_inactive:
                query += " WHERE active = 1"
            rows = conn.execute(query).fetchall()
            result = []
            for row in rows:
                role = roles.get(row["role"], {})
                if row["role"] not in ALLOWED_ROLE_KEYS:
                    continue
                item = dict(row)
                item["rolePermission"] = role.get("permission", ROLE_PERMISSIONS.get(row["role"], "manager"))
                item["roleLabel"] = role.get("label", ROLE_LABELS.get(row["role"], row["role"]))
                result.append(public_platform_user(item))
        finally:
            conn.close()
    elif CRM_DEMO_MODE:
        result = [public_platform_user(user) for user in DEMO_USERS.values()]
    else:
        result = []
    return result if include_inactive else [user for user in result if user["active"]]


def public_platform_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"], "username": user["username"], "name": user["name"],
        "role": user["role"], "roleLabel": user.get("roleLabel", ROLE_LABELS.get(user["role"], user["role"])),
        "rolePermission": user.get("rolePermission", ROLE_PERMISSIONS.get(user["role"], "manager")),
        "team": user["team"], "active": bool(user.get("active", 1)),
    }


def platform_user_by_id(user_id: str) -> dict[str, Any] | None:
    return next((item for item in platform_users(True) if item["id"] == user_id), None)


def platform_user_by_username(username: str) -> dict[str, Any] | None:
    return next((item for item in platform_users(True) if item["username"] == username), None)


def authenticate_platform(username: str, password: str) -> dict[str, Any] | None:
    if identity_provider_enabled():
        # Production must authenticate at MuskZoom; the customer module never receives passwords.
        return None
    if CRM_DEMO_MODE and not MUSKZOOM_DB_PATH.is_file():
        raw = DEMO_USERS.get(username)
        return public_platform_user(raw) if raw and secrets.compare_digest(raw["password"], password) else None
    conn = sqlite3.connect(f"file:{MUSKZOOM_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT username, password_hash, role, active FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not row["active"] or row["role"] not in ALLOWED_ROLE_KEYS:
            return None
        if not secrets.compare_digest(row["password_hash"], hash_platform_password(password)):
            return None
        return platform_user_by_username(username)
    finally:
        conn.close()


def decode_sso_claims(token: str) -> dict[str, Any]:
    try:
        if not SSO_SECRET:
            raise ValueError("missing SSO secret")
        payload_part, signature_part = token.split(".", 1)
        expected = base64.urlsafe_b64encode(hmac.new(SSO_SECRET.encode(), payload_part.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        if not secrets.compare_digest(signature_part, expected):
            raise ValueError("signature")
        padded = payload_part + "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, dict):
            raise ValueError("payload")
        issued_at = int(payload["iat"])
        expires_at = int(payload.get("exp", issued_at + SSO_TTL))
        now = int(time.time())
        if now < issued_at - 30 or now > expires_at or expires_at - issued_at > SSO_TTL + 30:
            raise ValueError("expired")
        if not str(payload.get("username") or "").strip():
            raise ValueError("username")
        if SSO_REQUIRE_STRONG_CLAIMS:
            audience = payload.get("aud")
            audiences = audience if isinstance(audience, list) else [audience]
            if payload.get("iss") != SSO_ISSUER or SSO_AUDIENCE not in audiences or not str(payload.get("jti") or "").strip():
                raise ValueError("claims")
        return payload
    except Exception as exc:
        raise HTTPException(401, "单点登录凭证无效或已过期。") from exc


def decode_sso(token: str) -> str:
    return str(decode_sso_claims(token)["username"])


def consume_sso_handoff(claims: dict[str, Any], user_id: str) -> None:
    handoff_id = str(claims.get("jti") or "").strip()
    if not handoff_id:
        return
    with db() as conn:
        conn.execute("DELETE FROM sso_handoffs WHERE expires_at < ?", (int(time.time()),))
        try:
            conn.execute(
                "INSERT INTO sso_handoffs(handoff_id, user_id, consumed_at, expires_at) VALUES (?, ?, ?, ?)",
                (handoff_id, user_id, now_iso(), int(claims["exp"])),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(401, "该单点登录凭证已被使用，请从 MuskZoom 重新进入。") from exc


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("DELETE FROM module_sessions WHERE expires_at < ?", (int(time.time()),))
        conn.execute("INSERT INTO module_sessions VALUES (?, ?, ?, ?)", (token, user_id, now_iso(), int(time.time()) + 86400 * 7))
    return token


def default_import_permission(user: dict[str, Any]) -> bool:
    return user["rolePermission"] in {"supervisor", "admin", "developer"}


def enrich_user(user: dict[str, Any]) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT can_import_customers, can_export_all, can_manage_customer_fields FROM user_permissions WHERE user_id = ?", (user["id"],)).fetchone()
    result = dict(user)
    result["canImportCustomers"] = bool(row["can_import_customers"]) if row and row["can_import_customers"] is not None else default_import_permission(user)
    result["canExportAll"] = bool(row["can_export_all"]) if row and row["can_export_all"] is not None else user["rolePermission"] in {"admin", "developer"}
    result["canManageCustomerFields"] = bool(row["can_manage_customer_fields"]) if row and row["can_manage_customer_fields"] is not None else user["rolePermission"] in {"admin", "developer"}
    result["customerScope"] = "self" if user["rolePermission"] == "manager" else "team" if user["rolePermission"] == "supervisor" else "all"
    return result


def current_user(
    authorization: str | None = Header(default=None),
    crm_session: str | None = Cookie(default=None, alias=CRM_SESSION_COOKIE),
) -> dict[str, Any]:
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else (crm_session or "")
    if not token:
        raise HTTPException(401, "请先登录。")
    with db() as conn:
        row = conn.execute("SELECT user_id, expires_at FROM module_sessions WHERE token = ?", (token,)).fetchone()
    if not row or row["expires_at"] < int(time.time()):
        raise HTTPException(401, "登录状态已过期，请重新登录。")
    user = platform_user_by_id(row["user_id"])
    if not user or not user["active"]:
        raise HTTPException(401, "账号不存在或已停用。")
    return enrich_user(user)


def require_supervisor(user: dict[str, Any]) -> None:
    if user["rolePermission"] not in {"supervisor", "admin", "developer"}:
        raise HTTPException(403, "需要部门主管或更高权限。")


def require_hongan_advisor_permission(user: dict[str, Any], value: Any) -> None:
    """港安顾问属于外部关系，只允许主管及以上写入。"""
    if str(value or "").strip() and user["rolePermission"] == "manager":
        raise HTTPException(403, "港安顾问由部门主管或更高权限维护。")


def sql_date(expression: str) -> str:
    """Return a date expression for ISO-8601 text stored in either database engine."""
    return f"CAST(LEFT(({expression}), 10) AS date)" if uses_postgres(DATABASE_URL) else f"date({expression})"


def sql_today() -> str:
    return "CURRENT_DATE" if uses_postgres(DATABASE_URL) else "date('now')"


def sql_days_ago(days: int) -> str:
    return f"(CURRENT_DATE - INTERVAL '{days} days')::date" if uses_postgres(DATABASE_URL) else f"date('now','-{days} days')"


def sql_recent_timestamp(expression: str, days: int) -> str:
    if uses_postgres(DATABASE_URL):
        return f"CAST(LEFT(({expression}), 19) AS timestamp) >= CURRENT_TIMESTAMP - INTERVAL '{days} days'"
    return f"datetime({expression}) >= datetime('now','-{days} days')"


def require_admin(user: dict[str, Any]) -> None:
    if user["rolePermission"] not in {"admin", "developer"}:
        raise HTTPException(403, "需要管理员权限。")


def require_field_manager(user: dict[str, Any]) -> None:
    if not user["canManageCustomerFields"]:
        raise HTTPException(403, "您的账号未开通表头管理权限。")


def validate_advisor_binding_values(customer_type: str, assignment_mode: str) -> None:
    if customer_type not in ADVISOR_CUSTOMER_TYPES:
        raise HTTPException(422, "客户类型必须是定增或非定增。")
    if assignment_mode not in ADVISOR_ASSIGNMENT_MODES:
        raise HTTPException(422, "分配方式必须是默认绑定或手动分配。")
    if customer_type == "placement" and assignment_mode == "default":
        raise HTTPException(422, "定增客户必须使用手动分配，不能配置为默认绑定。")


def advisor_binding_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["customerTypeLabel"] = ADVISOR_CUSTOMER_TYPES.get(item["customer_type"], item["customer_type"])
    item["assignmentModeLabel"] = ADVISOR_ASSIGNMENT_MODES.get(item["assignment_mode"], item["assignment_mode"])
    item["active"] = bool(item["active"])
    item["jiaoyangAdvisorId"] = item.pop("jiaoyang_advisor_id")
    item["jiaoyangAdvisor"] = item.pop("jiaoyang_advisor_label")
    item["honganAdvisor"] = item.pop("hongan_advisor")
    item["customerType"] = item.pop("customer_type")
    item["assignmentMode"] = item.pop("assignment_mode")
    return item


def validate_binding_owner(advisor_id: str | None, advisor_label: str, user: dict[str, Any]) -> dict[str, Any] | None:
    clean_id = str(advisor_id or "").strip()
    if not clean_id:
        return None
    advisor = platform_user_by_id(clean_id)
    if not advisor or advisor["rolePermission"] != "manager":
        raise HTTPException(422, "关联的骄阳顾问必须是有效的商务经理账号。")
    if user["rolePermission"] == "supervisor" and advisor["team"] != user["team"]:
        raise HTTPException(403, "部门主管只能关联本组商务经理。")
    if advisor_label.strip() and advisor_label.strip() != advisor["name"].strip():
        raise HTTPException(422, "骄阳顾问名称必须与所关联的系统账号一致。")
    return advisor


def customer_type_for_values(values: dict[str, Any]) -> str:
    placement_status = str(values.get("placementStatus", values.get("placement_status", "")) or "").strip()
    stage = str(values.get("stage", "") or "").strip()
    destination = str(values.get("capitalDestination", values.get("capital_destination", "")) or "").strip()
    intent = str(values.get("intentStatus", values.get("intent_status", "")) or "").strip()
    if destination == "参与定增" or placement_status in {"批次确认", "资金筹备", "资金到账", "已参与", "已流失"} or stage in {"定增意向", "批次推进", "资金准备", "已参与定增", "已流失"} or intent in {"有意向", "已锁定"}:
        return "placement"
    return "non_placement"


def default_advisor_binding(conn: sqlite3.Connection, hongan_advisor: str, customer_type: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    clean_hongan = str(hongan_advisor or "").strip()
    if not clean_hongan or customer_type != "non_placement":
        return None, None
    validate_advisor_binding_values(customer_type, "default")
    rows = conn.execute(
        "SELECT * FROM advisor_bindings WHERE active=1 AND customer_type=? AND assignment_mode='default' AND lower(trim(hongan_advisor))=lower(trim(?)) ORDER BY updated_at DESC",
        (customer_type, clean_hongan),
    ).fetchall()
    if len(rows) != 1:
        return None, None
    rule = rows[0]
    advisor = platform_user_by_id(rule["jiaoyang_advisor_id"]) if rule["jiaoyang_advisor_id"] else None
    if advisor and not advisor.get("active", False):
        advisor = None
    return advisor, dict(rule)


FIELD_TYPES = {"text": "文本", "number": "数字", "date": "日期", "select": "单选"}


def field_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["fieldType"] = item.pop("field_type")
    item["options"] = json.loads(item.pop("options_json") or "[]")
    item["active"] = bool(item["active"])
    item["displayOrder"] = item.pop("display_order")
    return item


def normalize_field_options(values: list[str]) -> list[str]:
    options: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in options:
            options.append(cleaned)
    return options[:50]


def normalize_custom_value(field: sqlite3.Row, value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if field["field_type"] == "number":
        try:
            return str(float(text)).removesuffix(".0")
        except ValueError as exc:
            raise HTTPException(422, f"“{field['label']}”必须填写数字。") from exc
    if field["field_type"] == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise HTTPException(422, f"“{field['label']}”必须使用 YYYY-MM-DD 日期格式。")
    if field["field_type"] == "select" and text not in json.loads(field["options_json"] or "[]"):
        raise HTTPException(422, f"“{field['label']}”的选项无效。")
    return text[:2000]


def save_custom_values(conn: sqlite3.Connection, customer_id: str, values: dict[str, Any], user: dict[str, Any]) -> None:
    if not values:
        return
    placeholders = ",".join("?" for _ in values)
    fields = conn.execute(f"SELECT * FROM customer_fields WHERE id IN ({placeholders})", tuple(values)).fetchall()
    field_map = {row["id"]: row for row in fields}
    unknown = [field_id for field_id in values if field_id not in field_map]
    if unknown:
        raise HTTPException(422, "包含不存在的自定义字段，请刷新后重试。")
    timestamp = now_iso()
    for field_id, raw_value in values.items():
        value = normalize_custom_value(field_map[field_id], raw_value)
        conn.execute(
            "INSERT INTO customer_field_values(customer_id, field_id, value_text, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(customer_id, field_id) DO UPDATE SET value_text=excluded.value_text, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (customer_id, field_id, value, user["id"], timestamp),
        )


def attach_custom_values(conn: sqlite3.Connection, customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not customers:
        return customers
    placeholders = ",".join("?" for _ in customers)
    customer_map = {item["id"]: item for item in customers}
    for item in customers:
        item["custom_values"] = {}
    rows = conn.execute(
        f"SELECT customer_id, field_id, value_text FROM customer_field_values WHERE customer_id IN ({placeholders})",
        tuple(customer_map),
    ).fetchall()
    for row in rows:
        customer_map[row["customer_id"]]["custom_values"][row["field_id"]] = row["value_text"]
    return customers


def attach_collaborators(conn: sqlite3.Connection, customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not customers:
        return customers
    customer_map = {item["id"]: item for item in customers}
    for item in customers:
        item["collaborators"] = []
    placeholders = ",".join("?" for _ in customer_map)
    rows = conn.execute(
        f"SELECT customer_id, user_id, user_name, user_team, collaborator_role FROM customer_collaborators WHERE customer_id IN ({placeholders}) ORDER BY created_at",
        tuple(customer_map),
    ).fetchall()
    for row in rows:
        customer_map[row["customer_id"]]["collaborators"].append({
            "id": row["user_id"], "name": row["user_name"], "team": row["user_team"], "role": row["collaborator_role"],
        })
    return customers


def attach_customer_relations(conn: sqlite3.Connection, customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return attach_collaborators(conn, attach_custom_values(conn, customers))


def access_clause(user: dict[str, Any], alias: str = "c") -> tuple[str, tuple[Any, ...]]:
    if user["customerScope"] == "self":
        return f"({alias}.owner_id = ? OR EXISTS (SELECT 1 FROM customer_collaborators cc WHERE cc.customer_id = {alias}.id AND cc.user_id = ?))", (user["id"], user["id"])
    if user["customerScope"] == "team":
        return f"({alias}.owner_team = ? OR EXISTS (SELECT 1 FROM customer_collaborators cc WHERE cc.customer_id = {alias}.id AND cc.user_team = ?))", (user["team"], user["team"])
    return "1 = 1", ()


def assert_customer_access(conn: sqlite3.Connection, customer_id: str, user: dict[str, Any], include_archived: bool = False) -> sqlite3.Row:
    clause, params = access_clause(user)
    archived = "" if include_archived else " AND c.archived_at IS NULL"
    row = conn.execute(f"SELECT c.* FROM customers c WHERE c.id = ? AND {clause}{archived}", (customer_id, *params)).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在，或您无权查看。")
    return row


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def duplicate_matches(conn: sqlite3.Connection, phone: str, email: str, exclude_id: str | None = None) -> list[dict[str, Any]]:
    conditions, params = [], []
    if normalize_phone(phone):
        conditions.append("(i.kind = 'phone' AND i.normalized_value = ?)")
        params.append(normalize_phone(phone))
    if normalize_email(email):
        conditions.append("(i.kind = 'email' AND i.normalized_value = ?)")
        params.append(normalize_email(email))
    if not conditions:
        return []
    exclude = " AND c.id != ?" if exclude_id else ""
    if exclude_id:
        params.append(exclude_id)
    rows = conn.execute(
        f"SELECT DISTINCT c.id, c.customer_code, c.name, c.phone, c.owner_name, c.owner_team FROM customer_identifiers i JOIN customers c ON c.id = i.customer_id WHERE ({' OR '.join(conditions)}) AND c.archived_at IS NULL{exclude}",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def potential_identity_matches(conn: sqlite3.Connection, name: str, nickname: str, exclude_id: str | None = None) -> list[dict[str, Any]]:
    clean_name = str(name or "").strip()
    clean_nickname = str(nickname or "").strip()
    conditions, params = [], []
    if clean_name:
        conditions.append("(TRIM(c.name) <> '' AND lower(TRIM(c.name)) = lower(?))")
        params.append(clean_name)
    if clean_nickname:
        conditions.append("(TRIM(c.wechat_nickname) <> '' AND lower(TRIM(c.wechat_nickname)) = lower(?))")
        params.append(clean_nickname)
    if not conditions:
        return []
    exclude = " AND c.id != ?" if exclude_id else ""
    if exclude_id:
        params.append(exclude_id)
    # The customers table is already one row per customer, so DISTINCT is
    # unnecessary and breaks PostgreSQL when ordering by a non-selected column.
    rows = conn.execute(
        f"SELECT c.id, c.customer_code, c.name, c.wechat_nickname, c.owner_name, c.owner_team FROM customers c WHERE c.archived_at IS NULL AND ({' OR '.join(conditions)}){exclude} ORDER BY c.updated_at DESC LIMIT 10",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def next_customer_code(conn: sqlite3.Connection) -> str:
    key = datetime.now().strftime("customer-%Y")
    conn.execute("INSERT INTO counters(counter_key, counter_value) VALUES (?, 0) ON CONFLICT(counter_key) DO NOTHING", (key,))
    conn.execute("UPDATE counters SET counter_value = counter_value + 1 WHERE counter_key = ?", (key,))
    value = conn.execute("SELECT counter_value FROM counters WHERE counter_key = ?", (key,)).fetchone()[0]
    return f"KH-{datetime.now().year}-{value:05d}"


def audit(conn: sqlite3.Connection, user: dict[str, Any], action: str, entity_type: str, entity_id: str, detail: dict[str, Any]) -> None:
    conn.execute("INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid4()), user["id"], user["name"], action, entity_type, entity_id, json.dumps(detail, ensure_ascii=False), now_iso()))


def owner_for_request(owner_id: str | None, user: dict[str, Any]) -> dict[str, Any]:
    if user["rolePermission"] == "manager":
        return user
    if not owner_id and user["rolePermission"] in {"admin", "developer"}:
        return dict(UNASSIGNED_OWNER)
    if owner_id == UNASSIGNED_OWNER_ID:
        if user["rolePermission"] not in {"admin", "developer"}:
            raise HTTPException(403, "待分配客户池仅限管理员或开发者使用。")
        return dict(UNASSIGNED_OWNER)
    owner = platform_user_by_id(owner_id or user["id"])
    if not owner or owner["rolePermission"] != "manager":
        raise HTTPException(422, "请选择有效的商务经理。")
    if user["rolePermission"] == "supervisor" and owner["team"] != user["team"]:
        raise HTTPException(403, "部门主管只能选择本组商务经理。")
    return owner


def collaborators_for_request(user_ids: list[str], owner: dict[str, Any], user: dict[str, Any]) -> list[dict[str, Any]]:
    requested_ids = list(dict.fromkeys(str(user_id).strip() for user_id in user_ids if str(user_id).strip()))
    if requested_ids and user["rolePermission"] == "manager":
        raise HTTPException(403, "协同负责人需要部门主管或更高权限设置。")
    collaborators: list[dict[str, Any]] = []
    for user_id in requested_ids:
        if user_id == owner["id"]:
            continue
        candidate = platform_user_by_id(user_id)
        if not candidate or candidate["rolePermission"] not in {"manager", "supervisor"}:
            raise HTTPException(422, "协同负责人必须是有效的商务经理或部门主管。")
        if user["rolePermission"] == "supervisor" and candidate["team"] != user["team"]:
            raise HTTPException(403, "部门主管只能设置本组协同负责人。")
        collaborators.append(candidate)
    return collaborators


def replace_collaborators(conn: sqlite3.Connection, customer_id: str, collaborators: list[dict[str, Any]], user: dict[str, Any]) -> None:
    conn.execute("DELETE FROM customer_collaborators WHERE customer_id = ?", (customer_id,))
    timestamp = now_iso()
    for collaborator in collaborators:
        role = "协同主管" if collaborator["rolePermission"] == "supervisor" else "协同商务经理"
        conn.execute(
            "INSERT INTO customer_collaborators(customer_id, user_id, user_name, user_team, collaborator_role, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (customer_id, collaborator["id"], collaborator["name"], collaborator["team"], role, user["id"], timestamp),
        )


def normalize_snapshot_date(value: Any) -> str:
    date_value = str(value or "").strip().replace("/", "-").replace(".", "-")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
        raise HTTPException(422, "持仓快照日期必须使用 YYYY-MM-DD 格式。")
    return date_value


def save_holding_snapshots(conn: sqlite3.Connection, customer_id: str, snapshots: list[dict[str, Any]], user: dict[str, Any]) -> None:
    for raw in snapshots:
        try:
            quantity = float(clean_import_cell(raw.get("quantity", 0)) or 0)
            market_value = float(clean_import_cell(raw.get("marketValue", raw.get("market_value", 0))) or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "持仓数量和市值必须是数字。") from exc
        if quantity == 0 and market_value == 0:
            continue
        snapshot_date = normalize_snapshot_date(raw.get("snapshotDate", raw.get("snapshot_date")))
        security_name = str(raw.get("securityName", raw.get("security_name", "二级市场持仓"))).strip() or "二级市场持仓"
        source_label = str(raw.get("sourceLabel", raw.get("source_label", ""))).strip()
        conn.execute(
            """INSERT INTO customer_holding_snapshots(id, customer_id, snapshot_date, security_name, quantity, market_value, source_label, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id, snapshot_date, security_name) DO UPDATE SET quantity=excluded.quantity, market_value=excluded.market_value, source_label=excluded.source_label, created_by=excluded.created_by, created_at=excluded.created_at""",
            (str(uuid4()), customer_id, snapshot_date, security_name[:100], quantity, market_value, source_label[:200], user["id"], now_iso()),
        )


def add_identifiers(conn: sqlite3.Connection, customer_id: str, phone: str, email: str) -> None:
    if normalize_phone(phone):
        conn.execute("INSERT INTO customer_identifiers(customer_id, kind, normalized_value, display_value, created_at) VALUES (?, 'phone', ?, ?, ?)", (customer_id, normalize_phone(phone), phone.strip(), now_iso()))
    if normalize_email(email):
        conn.execute("INSERT INTO customer_identifiers(customer_id, kind, normalized_value, display_value, created_at) VALUES (?, 'email', ?, ?, ?)", (customer_id, normalize_email(email), email.strip(), now_iso()))


def find_customer_by_tw(conn: sqlite3.Connection, tw_code: str) -> dict[str, Any] | None:
    """Look up a live customer by the broker's canonical TW identifier."""
    code = normalize_tw_code(tw_code)
    if not code:
        return None
    row = conn.execute(
        """SELECT c.* FROM customer_identifiers i
        JOIN customers c ON c.id=i.customer_id
        WHERE i.kind='tw' AND i.normalized_value=? AND c.archived_at IS NULL""",
        (code,),
    ).fetchone()
    if not row:
        # Older migrations used TW as the display code before identifiers were added.
        row = conn.execute("SELECT * FROM customers WHERE customer_code=? AND archived_at IS NULL", (code,)).fetchone()
    return dict(row) if row else None


def add_tw_identifier(conn: sqlite3.Connection, customer_id: str, tw_code: str) -> None:
    code = normalize_tw_code(tw_code)
    if not code:
        return
    existing = conn.execute(
        "SELECT customer_id FROM customer_identifiers WHERE kind='tw' AND normalized_value=?",
        (code,),
    ).fetchone()
    if existing and existing["customer_id"] != customer_id:
        raise HTTPException(409, f"TW 编号 {code} 已绑定到另一位客户，请先人工合并。")
    if not existing:
        conn.execute(
            "INSERT INTO customer_identifiers(customer_id, kind, normalized_value, display_value, created_at) VALUES (?, 'tw', ?, ?, ?)",
            (customer_id, code, code, now_iso()),
        )


def update_broker_snapshot_customer(conn: sqlite3.Connection, current: dict[str, Any], row: dict[str, Any], tw_code: str, user: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Apply only broker-authoritative fields; preserve internal ownership and workflow."""
    updates: dict[str, Any] = {}
    name = str(clean_import_cell(row.get("name", "")) or "").strip()
    if name and not str(current["name"] or "").strip():
        updates["name"] = name
    account_status = str(row.get("accountStatus", "") or "").strip()
    if account_status in ACCOUNT_STATUSES and account_status != current["account_status"]:
        updates["account_status"] = account_status
    if not updates:
        return {}
    changes = {key: {"from": current[key], "to": value} for key, value in updates.items()}
    assignments = ", ".join(f"{key}=?" for key in updates)
    timestamp = now_iso()
    conn.execute(f"UPDATE customers SET {assignments}, updated_at=?, version=version+1 WHERE id=?", (*updates.values(), timestamp, current["id"]))
    audit(conn, user, "customer.broker_snapshot_updated", "customer", current["id"], {"twCode": tw_code, "changes": changes})
    return changes


def create_customer_record(conn: sqlite3.Connection, payload: dict[str, Any], owner: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    require_hongan_advisor_permission(user, payload.get("hkAdvisor", payload.get("hongan_advisor", "")))
    name = str(clean_import_cell(payload.get("name", "")) or "").strip()
    nickname = str(clean_import_cell(payload.get("wechatNickname", payload.get("wechat_nickname", ""))) or "").strip()
    if not name and not nickname:
        raise HTTPException(422, "客户姓名和微信昵称至少填写一项。")
    phone, email = str(payload.get("phone", "")).strip(), str(payload.get("email", "")).strip()
    duplicates = duplicate_matches(conn, phone, email)
    if duplicates:
        raise HTTPException(409, detail={"message": "手机号或邮箱已存在，请人工确认是否合并。", "matches": duplicates})
    collaborators = collaborators_for_request(payload.get("collaboratorIds", payload.get("collaborator_ids", [])) or [], owner, user)
    customer_id, created_at = str(uuid4()), now_iso()
    tw_code = normalize_tw_code(payload.get("twCode", ""))
    record = {
        "id": customer_id, "customer_code": tw_code or next_customer_code(conn), "name": name, "phone": phone, "email": email, "wechat_nickname": nickname,
        "company": str(payload.get("company", "")).strip(), "source": str(payload.get("source", "")).strip(),
        "source_detail": str(payload.get("sourceDetail", payload.get("source_detail", ""))).strip(),
        "stage": str(payload.get("stage", "新客户")) or "新客户", "priority": str(payload.get("priority", "普通")) or "普通",
        "owner_id": owner["id"], "owner_name": owner["name"], "owner_team": owner["team"],
        "account_status": str(payload.get("accountStatus", payload.get("account_status", "未启动"))) or "未启动",
        "account_broker": str(payload.get("accountBroker", payload.get("account_broker", ""))).strip(),
        "account_opened_at": payload.get("accountOpenedAt", payload.get("account_opened_at")) or None,
        "broker_deposit_amount": float(payload.get("brokerDepositAmount", payload.get("broker_deposit_amount", 0)) or 0),
        "capital_destination": str(payload.get("capitalDestination", payload.get("capital_destination", ""))).strip(),
        "intent_status": str(payload.get("intentStatus", payload.get("intent_status", "未确认"))) or "未确认",
        "placement_status": str(payload.get("placementStatus", payload.get("placement_status", "未进入"))) or "未进入",
        "target_batch_id": payload.get("targetBatchId") or payload.get("target_batch_id") or None,
        "intent_amount": float(payload.get("intentAmount", payload.get("intent_amount", 0)) or 0),
        "funded_amount": float(payload.get("fundedAmount", payload.get("funded_amount", 0)) or 0),
        "actual_amount": float(payload.get("actualAmount", payload.get("actual_amount", 0)) or 0),
        "lost_reason": str(payload.get("lostReason", payload.get("lost_reason", ""))).strip(),
        "closed_at": payload.get("closedAt", payload.get("closed_at")) or None,
        "hongan_advisor": str(payload.get("hkAdvisor", payload.get("hongan_advisor", ""))).strip(),
        "source_advisor_label": str(payload.get("sourceAdvisorLabel", payload.get("source_advisor_label", ""))).strip(),
        "notes": str(payload.get("notes", "")).strip(), "created_by": user["id"], "created_at": created_at, "updated_at": created_at,
    }
    conn.execute(
        """INSERT INTO customers(id, customer_code, name, phone, email, wechat_nickname, company, source, source_detail, stage, priority, owner_id, owner_name, owner_team,
        account_status, account_broker, account_opened_at, broker_deposit_amount, capital_destination, intent_status, placement_status, target_batch_id, intent_amount, funded_amount, actual_amount, lost_reason, closed_at, hongan_advisor, source_advisor_label,
        notes, created_by, created_at, updated_at)
        VALUES (:id, :customer_code, :name, :phone, :email, :wechat_nickname, :company, :source, :source_detail, :stage, :priority, :owner_id, :owner_name, :owner_team,
        :account_status, :account_broker, :account_opened_at, :broker_deposit_amount, :capital_destination, :intent_status, :placement_status, :target_batch_id, :intent_amount, :funded_amount, :actual_amount, :lost_reason, :closed_at, :hongan_advisor, :source_advisor_label,
        :notes, :created_by, :created_at, :updated_at)""", record,
    )
    add_identifiers(conn, customer_id, phone, email)
    add_tw_identifier(conn, customer_id, tw_code)
    replace_collaborators(conn, customer_id, collaborators, user)
    save_holding_snapshots(conn, customer_id, payload.get("holdingSnapshots", payload.get("holding_snapshots", [])) or [], user)
    conn.execute("INSERT INTO assignments VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)", (str(uuid4()), customer_id, owner["id"], owner["name"], owner["team"], "新建客户", user["id"], user["name"], created_at))
    audit(conn, user, "customer.created", "customer", customer_id, {"ownerId": owner["id"], "collaboratorIds": [item["id"] for item in collaborators]})
    return record


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "platformConnected": platform_available(),
        "database": "postgresql" if uses_postgres(DATABASE_URL) else "sqlite",
    }


@app.get("/api/auth/config")
def auth_config() -> dict[str, Any]:
    return {
        "passwordLoginEnabled": CRM_ALLOW_PASSWORD_LOGIN,
        "muskzoomEntryUrl": os.getenv("MUSKZOOM_ENTRY_URL", "https://muskzoom.com").strip(),
    }


@app.post("/api/auth/login")
def login(payload: LoginPayload) -> dict[str, Any]:
    if not CRM_ALLOW_PASSWORD_LOGIN:
        raise HTTPException(403, "请从 MuskZoom 的客户数据入口进入系统。")
    user = authenticate_platform(payload.username.strip(), payload.password)
    if not user:
        raise HTTPException(401, "账号或密码错误，或该角色尚未开放客户模块。")
    return {"token": create_session(user["id"]), "user": enrich_user(user)}


@app.post("/api/auth/sso")
def sso(payload: SsoPayload, response: Response) -> dict[str, Any]:
    claims = decode_sso_claims(payload.token)
    user = platform_user_by_username(str(claims["username"]))
    if not user or not user["active"]:
        raise HTTPException(401, "账号不存在、已停用或该角色尚未开放客户模块。")
    consume_sso_handoff(claims, user["id"])
    session_token = create_session(user["id"])
    response.set_cookie(
        key=CRM_SESSION_COOKIE,
        value=session_token,
        max_age=86400 * 7,
        httponly=True,
        secure=CRM_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"token": session_token, "user": enrich_user(user)}


@app.get("/api/session")
def session(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"user": user, "platformConnected": platform_available()}


@app.delete("/api/session")
def logout(response: Response, authorization: str | None = Header(default=None), crm_session: str | None = Cookie(default=None, alias=CRM_SESSION_COOKIE)) -> dict[str, bool]:
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else (crm_session or "")
    if token:
        with db() as conn:
            conn.execute("DELETE FROM module_sessions WHERE token = ?", (token,))
    response.delete_cookie(CRM_SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/meta")
def meta(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    users = platform_users()
    if user["customerScope"] == "self":
        available = [user]
    elif user["customerScope"] == "team":
        available = [item for item in users if item["team"] == user["team"] and item["rolePermission"] == "manager"]
    else:
        available = [item for item in users if item["rolePermission"] == "manager"]
    collaborator_users = [
        item for item in users
        if item["rolePermission"] in {"manager", "supervisor"}
        and (user["customerScope"] == "all" or item["team"] == user["team"])
    ]
    customer_clause, customer_params = access_clause(user)
    # PostgreSQL does not resolve a SELECT DISTINCT alias inside lower(...).
    # Ordering by the alias itself is portable and keeps this metadata query valid.
    advisor_order = "advisor" if uses_postgres(DATABASE_URL) else "advisor COLLATE NOCASE"
    with db() as conn:
        batches = conn.execute("SELECT * FROM placement_batches ORDER BY COALESCE(close_date, '9999-12-31'), created_at DESC").fetchall()
        fields = conn.execute("SELECT * FROM customer_fields WHERE active=1 ORDER BY display_order, created_at").fetchall()
        hongan_advisors = conn.execute(
            f"""SELECT DISTINCT TRIM(c.hongan_advisor) advisor
            FROM customers c
            WHERE c.archived_at IS NULL AND TRIM(c.hongan_advisor) <> '' AND {customer_clause}
            ORDER BY {advisor_order}""",
            customer_params,
        ).fetchall()
    owner_choices = list(available)
    if user["rolePermission"] in {"admin", "developer"}:
        owner_choices.insert(0, dict(UNASSIGNED_OWNER))
    return {
        "stages": STAGES, "sources": SOURCES, "followupMethods": FOLLOWUP_METHODS, "owners": available, "collaboratorUsers": collaborator_users,
        "accountStatuses": ACCOUNT_STATUSES, "intentStatuses": INTENT_STATUSES,
        "placementStatuses": PLACEMENT_STATUSES, "batchStatuses": BATCH_STATUSES,
        "batches": [dict(row) for row in batches], "customerFields": [field_dict(row) for row in fields], "ownerChoices": owner_choices,
        "honganAdvisors": [row["advisor"] for row in hongan_advisors],
    }


@app.get("/api/advisor-bindings")
def list_advisor_bindings(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    users = {item["id"]: item for item in platform_users(True)}
    advisor_order = "lower(hongan_advisor)" if uses_postgres(DATABASE_URL) else "hongan_advisor COLLATE NOCASE"
    with db() as conn:
        rows = conn.execute(f"SELECT * FROM advisor_bindings ORDER BY active DESC, customer_type, {advisor_order}, updated_at DESC").fetchall()
        items = [advisor_binding_dict(row) for row in rows]
    for item in items:
        advisor = users.get(item.get("jiaoyangAdvisorId"))
        item["jiaoyangAccountActive"] = bool(advisor and advisor.get("active"))
        item["jiaoyangAdvisorRole"] = advisor.get("roleLabel", "") if advisor else ""
        item["jiaoyangAdvisorTeam"] = advisor.get("team", "") if advisor else ""
    return {"items": items, "customerTypes": ADVISOR_CUSTOMER_TYPES, "assignmentModes": ADVISOR_ASSIGNMENT_MODES}


@app.post("/api/advisor-bindings", status_code=201)
def create_advisor_binding(payload: AdvisorBindingPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    hongan = payload.honganAdvisor.strip()
    jiaoyang = payload.jiaoyangAdvisor.strip()
    validate_advisor_binding_values(payload.customerType, payload.assignmentMode)
    validate_binding_owner(payload.jiaoyangAdvisorId, jiaoyang, user)
    binding_id, timestamp = str(uuid4()), now_iso()
    with db() as conn:
        try:
            conn.execute(
                """INSERT INTO advisor_bindings(id, hongan_advisor, jiaoyang_advisor_label, jiaoyang_advisor_id, customer_type, assignment_mode, active, notes, created_by, created_at, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (binding_id, hongan, jiaoyang, payload.jiaoyangAdvisorId or None, payload.customerType, payload.assignmentMode, int(payload.active), payload.notes.strip(), user["id"], timestamp, user["id"], timestamp),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "同一港安顾问在该客户类型和分配方式下已经存在绑定规则。") from exc
        audit(conn, user, "advisor_binding.created", "advisor_binding", binding_id, {"honganAdvisor": hongan, "jiaoyangAdvisor": jiaoyang, "customerType": payload.customerType, "assignmentMode": payload.assignmentMode})
        row = conn.execute("SELECT * FROM advisor_bindings WHERE id=?", (binding_id,)).fetchone()
    return {"binding": advisor_binding_dict(row)}


@app.patch("/api/advisor-bindings/{binding_id}")
def update_advisor_binding(binding_id: str, payload: AdvisorBindingPatch, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    values = payload.model_dump(exclude_unset=True)
    change_reason = str(values.pop("changeReason", "") or "").strip()
    if not change_reason:
        raise HTTPException(422, "修改绑定规则必须填写变更原因。")
    with db() as conn:
        current = conn.execute("SELECT * FROM advisor_bindings WHERE id=?", (binding_id,)).fetchone()
        if not current:
            raise HTTPException(404, "绑定规则不存在。")
        merged = dict(current)
        if "honganAdvisor" in values:
            merged["hongan_advisor"] = str(values["honganAdvisor"] or "").strip()
        if "jiaoyangAdvisor" in values:
            merged["jiaoyang_advisor_label"] = str(values["jiaoyangAdvisor"] or "").strip()
        if "jiaoyangAdvisorId" in values:
            merged["jiaoyang_advisor_id"] = str(values["jiaoyangAdvisorId"] or "").strip() or None
        if "customerType" in values:
            merged["customer_type"] = values["customerType"]
        if "assignmentMode" in values:
            merged["assignment_mode"] = values["assignmentMode"]
        if "active" in values:
            merged["active"] = int(bool(values["active"]))
        if "notes" in values:
            merged["notes"] = str(values["notes"] or "").strip()
        if not merged["hongan_advisor"] or not merged["jiaoyang_advisor_label"]:
            raise HTTPException(422, "港安顾问和骄阳顾问名称不能为空。")
        validate_advisor_binding_values(merged["customer_type"], merged["assignment_mode"])
        validate_binding_owner(merged["jiaoyang_advisor_id"], merged["jiaoyang_advisor_label"], user)
        timestamp = now_iso()
        try:
            conn.execute(
                """UPDATE advisor_bindings SET hongan_advisor=?, jiaoyang_advisor_label=?, jiaoyang_advisor_id=?, customer_type=?, assignment_mode=?, active=?, notes=?, updated_by=?, updated_at=? WHERE id=?""",
                (merged["hongan_advisor"], merged["jiaoyang_advisor_label"], merged["jiaoyang_advisor_id"], merged["customer_type"], merged["assignment_mode"], merged["active"], merged["notes"], user["id"], timestamp, binding_id),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "同一港安顾问在该客户类型和分配方式下已经存在绑定规则。") from exc
        audit(conn, user, "advisor_binding.updated", "advisor_binding", binding_id, {"fields": list(values), "reason": change_reason})
        row = conn.execute("SELECT * FROM advisor_bindings WHERE id=?", (binding_id,)).fetchone()
    return {"binding": advisor_binding_dict(row)}


@app.get("/api/dashboard")
def dashboard(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    clause, params = access_clause(user)
    latest_next_followup = "(SELECT f.next_followup_at FROM followups f WHERE f.customer_id=c.id AND f.next_followup_at IS NOT NULL ORDER BY f.created_at DESC LIMIT 1)"
    latest_followup = "(SELECT MAX(f.created_at) FROM followups f WHERE f.customer_id=c.id)"
    due_expression = sql_date(latest_next_followup)
    today_expression = sql_today()
    created_date_expression = sql_date("c.created_at")
    last_followup_date_expression = sql_date(latest_followup)
    updated_recent_expression = sql_recent_timestamp("c.updated_at", 7)
    activity_date_expression = sql_date("f.created_at")
    with db() as conn:
        summary = conn.execute(
            f"""SELECT COUNT(*) total,
            SUM(CASE WHEN account_status = '已开户' THEN 1 ELSE 0 END) accounts_opened,
            SUM(CASE WHEN intent_status IN ('有意向','已锁定') THEN 1 ELSE 0 END) intended,
            SUM(CASE WHEN target_batch_id IS NOT NULL THEN 1 ELSE 0 END) batched,
            SUM(CASE WHEN placement_status IN ('资金到账','已参与') THEN 1 ELSE 0 END) funded,
            SUM(CASE WHEN placement_status = '已参与' THEN 1 ELSE 0 END) closed,
            SUM(CASE WHEN placement_status = '已流失' THEN 1 ELSE 0 END) lost,
            COALESCE(SUM(intent_amount), 0) intent_amount,
            COALESCE(SUM(funded_amount), 0) funded_amount,
            COALESCE(SUM(actual_amount), 0) actual_amount,
            SUM(CASE WHEN {due_expression} <= {today_expression} THEN 1 ELSE 0 END) due
            FROM customers c WHERE c.archived_at IS NULL AND {clause}""", params,
        ).fetchone()
        stages = conn.execute(f"SELECT stage, COUNT(*) count FROM customers c WHERE c.archived_at IS NULL AND {clause} GROUP BY stage ORDER BY count DESC", params).fetchall()
        recent = conn.execute(
            f"SELECT c.id, c.customer_code, c.name, c.stage, c.account_status, c.placement_status, c.owner_name, c.updated_at FROM customers c WHERE c.archived_at IS NULL AND {clause} ORDER BY c.updated_at DESC LIMIT 8", params,
        ).fetchall()
        batches = conn.execute(
            f"""SELECT b.id, b.name, b.close_date, b.status, b.target_amount,
            COUNT(c.id) customer_count, COALESCE(SUM(c.intent_amount),0) intent_amount,
            COALESCE(SUM(c.funded_amount),0) funded_amount, COALESCE(SUM(c.actual_amount),0) actual_amount
            FROM placement_batches b LEFT JOIN customers c ON c.target_batch_id=b.id AND c.archived_at IS NULL AND {clause}
            GROUP BY b.id ORDER BY COALESCE(b.close_date, '9999-12-31') LIMIT 6""", params,
        ).fetchall()
        risk = conn.execute(
            f"""SELECT
            SUM(CASE WHEN {due_expression} <= {today_expression} THEN 1 ELSE 0 END) due,
            SUM(CASE WHEN c.intent_status IN ('有意向','已锁定') AND c.target_batch_id IS NULL AND c.placement_status NOT IN ('已参与','已流失') THEN 1 ELSE 0 END) intent_unbatched,
            SUM(CASE WHEN TRIM(c.phone) = '' AND TRIM(c.email) = '' AND TRIM(c.wechat_nickname) = '' THEN 1 ELSE 0 END) missing_contact,
            SUM(CASE WHEN c.owner_id = ? OR TRIM(c.owner_name) = '' THEN 1 ELSE 0 END) unassigned,
            SUM(CASE WHEN c.placement_status NOT IN ('已参与','已流失') AND {created_date_expression} <= {sql_days_ago(7)} AND COALESCE({last_followup_date_expression}, {created_date_expression}) < {sql_days_ago(7)} THEN 1 ELSE 0 END) stalled
            FROM customers c WHERE c.archived_at IS NULL AND {clause}""", (UNASSIGNED_OWNER_ID, *params),
        ).fetchone()
        quality = conn.execute(
            f"""SELECT
            SUM(CASE WHEN TRIM(c.owner_name) != '' AND c.owner_id != ? THEN 1 ELSE 0 END) assigned,
            SUM(CASE WHEN TRIM(c.phone) != '' OR TRIM(c.email) != '' OR TRIM(c.wechat_nickname) != '' THEN 1 ELSE 0 END) contactable,
            SUM(CASE WHEN {updated_recent_expression} THEN 1 ELSE 0 END) recently_updated
            FROM customers c WHERE c.archived_at IS NULL AND {clause}""", (UNASSIGNED_OWNER_ID, *params),
        ).fetchone()
        duplicate_name_groups = conn.execute(
            f"""SELECT COUNT(*) FROM (
            SELECT c.name FROM customers c
            WHERE c.archived_at IS NULL AND TRIM(c.name) != '' AND {clause}
            GROUP BY c.name HAVING COUNT(*) > 1
            )""", params,
        ).fetchone()[0]
        activity_rows = conn.execute(
            f"""SELECT {activity_date_expression} activity_date, COUNT(*) count
            FROM followups f JOIN customers c ON c.id=f.customer_id
            WHERE c.archived_at IS NULL AND {clause} AND {activity_date_expression} >= {sql_days_ago(13)}
            GROUP BY {activity_date_expression}""", params,
        ).fetchall()
        teams = conn.execute(
            f"""SELECT
            CASE WHEN TRIM(c.owner_team) != '' AND c.owner_team != '待分配池' THEN c.owner_team ELSE c.owner_name END group_name,
            COUNT(*) total,
            SUM(CASE WHEN c.target_batch_id IS NOT NULL THEN 1 ELSE 0 END) batched,
            SUM(CASE WHEN c.placement_status = '已参与' THEN 1 ELSE 0 END) closed
            FROM customers c
            WHERE c.archived_at IS NULL AND {clause}
            GROUP BY group_name
            ORDER BY batched DESC, closed DESC, total DESC
            LIMIT 4""", params,
        ).fetchall()
    summary_data = {key: float(summary[key] or 0) if key.endswith("_amount") else int(summary[key] or 0) for key in summary.keys()}
    risk_data = {key: int(risk[key] or 0) for key in risk.keys()}
    quality_data = {key: int(quality[key] or 0) for key in quality.keys()}
    quality_data["duplicate_name_groups"] = int(duplicate_name_groups or 0)
    activity_counts = {row["activity_date"]: int(row["count"] or 0) for row in activity_rows}
    today = datetime.now(timezone.utc).date()
    activity_trend = [
        {"date": (today - timedelta(days=offset)).isoformat(), "label": (today - timedelta(days=offset)).strftime("%m/%d"), "count": activity_counts.get((today - timedelta(days=offset)).isoformat(), 0)}
        for offset in range(13, -1, -1)
    ]
    return {
        "summary": summary_data,
        "stages": [dict(row) for row in stages],
        "recent": [dict(row) for row in recent],
        "batches": [dict(row) for row in batches],
        "risks": risk_data,
        "quality": quality_data,
        "activityTrend": activity_trend,
        "teams": [dict(row) for row in teams],
    }


def customer_search_terms(value: str) -> list[str]:
    return [term for term in re.split(r"[\s,，;；]+", value.strip()) if term][:6]


@app.get("/api/customers")
def list_customers(
    search: str = "", stage: str = "", owner_id: str = Query("", alias="ownerId"),
    workflow: str = "", metric: str = "", batch_id: str = Query("", alias="batchId"), placement_status: str = Query("", alias="placementStatus"),
    account_status: str = Query("", alias="accountStatus"), intent_status: str = Query("", alias="intentStatus"),
    source: str = "", hongan_advisor: str = Query("", alias="honganAdvisor"), contact_state: str = Query("", alias="contactState"),
    page: int = Query(1, ge=1), page_size: int = Query(30, alias="pageSize", ge=1, le=100),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    clause, params = access_clause(user)
    filters = ["c.archived_at IS NULL", clause]
    values: list[Any] = list(params)
    for term in customer_search_terms(search):
        filters.append("""(
            c.name LIKE ? OR c.wechat_nickname LIKE ? OR c.phone LIKE ? OR c.email LIKE ? OR c.company LIKE ?
            OR c.customer_code LIKE ? OR c.owner_name LIKE ? OR c.source_advisor_label LIKE ? OR c.hongan_advisor LIKE ?
        )""")
        values.extend([f"%{term}%"] * 9)
    if stage:
        filters.append("c.stage = ?")
        values.append(stage)
    if owner_id:
        filters.append("c.owner_id = ?")
        values.append(owner_id)
    if workflow == "account":
        filters.append("c.account_status != '已开户'")
    if workflow == "placement":
        filters.append("c.intent_status IN ('有意向','已锁定') AND c.placement_status NOT IN ('已参与','已流失')")
    if workflow == "closed":
        filters.append("c.placement_status = '已参与'")
    if workflow == "lost":
        filters.append("c.placement_status = '已流失'")
    if metric == "opened":
        filters.append("c.account_status = '已开户'")
    if metric == "intent":
        filters.append("c.intent_status IN ('有意向','已锁定')")
    if metric == "batched":
        filters.append("c.target_batch_id IS NOT NULL")
    if metric == "funded":
        filters.append("c.placement_status IN ('资金到账','已参与')")
    if metric == "closed":
        filters.append("c.placement_status = '已参与'")
    if batch_id:
        filters.append("c.target_batch_id = ?")
        values.append(batch_id)
    if placement_status:
        filters.append("c.placement_status = ?")
        values.append(placement_status)
    if account_status:
        filters.append("c.account_status = ?")
        values.append(account_status)
    if intent_status:
        filters.append("c.intent_status = ?")
        values.append(intent_status)
    if source:
        filters.append("c.source = ?")
        values.append(source)
    if hongan_advisor:
        filters.append("c.hongan_advisor = ?")
        values.append(hongan_advisor)
    if contact_state == "complete":
        filters.append("(TRIM(c.phone) <> '' OR TRIM(c.email) <> '' OR TRIM(c.wechat_nickname) <> '')")
    if contact_state == "missing":
        filters.append("TRIM(c.phone) = '' AND TRIM(c.email) = '' AND TRIM(c.wechat_nickname) = ''")
    if contact_state == "wechat_only":
        filters.append("TRIM(c.phone) = '' AND TRIM(c.email) = '' AND TRIM(c.wechat_nickname) <> ''")
    where = " AND ".join(filters)
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM customers c WHERE {where}", values).fetchone()[0]
        rows = conn.execute(
            f"""SELECT c.*, b.name target_batch_name,
            (SELECT MAX(f.created_at) FROM followups f WHERE f.customer_id=c.id) last_followup_at,
            (SELECT f.next_followup_at FROM followups f WHERE f.customer_id=c.id AND f.next_followup_at IS NOT NULL ORDER BY f.created_at DESC LIMIT 1) next_followup_at
            FROM customers c LEFT JOIN placement_batches b ON b.id=c.target_batch_id WHERE {where} ORDER BY c.updated_at DESC LIMIT ? OFFSET ?""",
            (*values, page_size, (page - 1) * page_size),
        ).fetchall()
        items = attach_customer_relations(conn, [dict(row) for row in rows])
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


@app.post("/api/customers", status_code=201)
def create_customer(payload: CustomerPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_hongan_advisor_permission(user, payload.hkAdvisor)
    with db() as conn:
        owner = None
        if not payload.ownerId and user["rolePermission"] != "manager":
            bound_owner, _ = default_advisor_binding(conn, payload.hkAdvisor, customer_type_for_values(payload.model_dump()))
            owner = bound_owner
        if owner is None:
            owner = owner_for_request(payload.ownerId, user)
        record = create_customer_record(conn, payload.model_dump(), owner, user)
        save_custom_values(conn, record["id"], payload.customValues, user)
        potential_duplicates = potential_identity_matches(conn, record["name"], record["wechat_nickname"], record["id"])
        record = attach_customer_relations(conn, [record])[0]
    return {"customer": record, "potentialDuplicates": potential_duplicates}


@app.get("/api/customers/{customer_id}")
def customer_detail(customer_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db() as conn:
        assert_customer_access(conn, customer_id, user)
        customer = conn.execute("SELECT c.*, b.name target_batch_name FROM customers c LEFT JOIN placement_batches b ON b.id=c.target_batch_id WHERE c.id=?", (customer_id,)).fetchone()
        followups = conn.execute("SELECT * FROM followups WHERE customer_id = ? ORDER BY created_at DESC", (customer_id,)).fetchall()
        assignments = conn.execute("SELECT * FROM assignments WHERE customer_id = ? ORDER BY changed_at DESC", (customer_id,)).fetchall()
        snapshots = conn.execute("SELECT * FROM customer_holding_snapshots WHERE customer_id = ? ORDER BY snapshot_date DESC, created_at DESC", (customer_id,)).fetchall()
        customer_data = attach_customer_relations(conn, [dict(customer)])[0]
    return {"customer": customer_data, "followups": [dict(row) for row in followups], "assignments": [dict(row) for row in assignments], "holdingSnapshots": [dict(row) for row in snapshots]}


@app.put("/api/customers/{customer_id}/collaborators")
def update_collaborators(customer_id: str, payload: CollaboratorPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    with db() as conn:
        customer = assert_customer_access(conn, customer_id, user)
        owner = platform_user_by_id(customer["owner_id"]) or {
            "id": customer["owner_id"], "name": customer["owner_name"], "team": customer["owner_team"], "rolePermission": "manager",
        }
        collaborators = collaborators_for_request(payload.userIds, owner, user)
        replace_collaborators(conn, customer_id, collaborators, user)
        conn.execute("UPDATE customers SET updated_at=?, version=version+1 WHERE id=?", (now_iso(), customer_id))
        audit(conn, user, "customer.collaborators_updated", "customer", customer_id, {"collaboratorIds": [item["id"] for item in collaborators]})
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        customer_data = attach_customer_relations(conn, [dict(row)])[0]
    return {"customer": customer_data}


@app.get("/api/customers/{customer_id}/holding-snapshots")
def list_holding_snapshots(customer_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db() as conn:
        assert_customer_access(conn, customer_id, user)
        rows = conn.execute("SELECT * FROM customer_holding_snapshots WHERE customer_id = ? ORDER BY snapshot_date DESC, created_at DESC", (customer_id,)).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/customers/{customer_id}/holding-snapshots", status_code=201)
def create_holding_snapshot(customer_id: str, payload: HoldingSnapshotPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if payload.quantity == 0 and payload.marketValue == 0:
        raise HTTPException(422, "持仓数量和市值不能同时为 0。")
    with db() as conn:
        assert_customer_access(conn, customer_id, user)
        save_holding_snapshots(conn, customer_id, [payload.model_dump()], user)
        conn.execute("UPDATE customers SET updated_at=?, version=version+1 WHERE id=?", (now_iso(), customer_id))
        audit(conn, user, "customer.holding_snapshot_saved", "customer", customer_id, {"snapshotDate": payload.snapshotDate, "securityName": payload.securityName})
        row = conn.execute("SELECT * FROM customer_holding_snapshots WHERE customer_id=? AND snapshot_date=? AND security_name=?", (customer_id, normalize_snapshot_date(payload.snapshotDate), payload.securityName.strip() or "二级市场持仓")).fetchone()
    return {"snapshot": dict(row)}


@app.patch("/api/customers/{customer_id}")
def update_customer(customer_id: str, payload: CustomerPatch, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    expected_version = values.pop("version")
    custom_values = values.pop("customValues", None)
    change_reason = str(values.pop("changeReason", "") or "").strip()
    mapping = {
        "sourceDetail": "source_detail", "wechatNickname": "wechat_nickname", "accountStatus": "account_status", "accountBroker": "account_broker", "brokerDepositAmount": "broker_deposit_amount", "capitalDestination": "capital_destination",
        "accountOpenedAt": "account_opened_at", "intentStatus": "intent_status", "placementStatus": "placement_status",
        "targetBatchId": "target_batch_id", "intentAmount": "intent_amount", "fundedAmount": "funded_amount",
        "actualAmount": "actual_amount", "lostReason": "lost_reason", "closedAt": "closed_at", "hkAdvisor": "hongan_advisor", "sourceAdvisorLabel": "source_advisor_label",
    }
    values = {mapping.get(key, key): value.strip() if isinstance(value, str) else value for key, value in values.items()}
    values = {key: value for key, value in values.items() if value is not None or key in {"target_batch_id", "account_opened_at", "closed_at"}}
    allowed = {
        "name", "phone", "email", "wechat_nickname", "company", "source", "source_detail", "stage", "priority", "notes",
        "account_status", "account_broker", "account_opened_at", "broker_deposit_amount", "capital_destination", "intent_status", "placement_status",
        "target_batch_id", "intent_amount", "funded_amount", "actual_amount", "lost_reason", "closed_at", "hongan_advisor", "source_advisor_label",
    }
    values = {key: value for key, value in values.items() if key in allowed}
    with db() as conn:
        current = assert_customer_access(conn, customer_id, user)
        if current["version"] != expected_version:
            raise HTTPException(409, "客户资料已被其他人更新，请刷新后重试。")
        if "hongan_advisor" in values and user["rolePermission"] == "manager":
            raise HTTPException(403, "港安顾问属于外部引荐关系，请由部门主管或管理员维护。")
        if "hongan_advisor" in values and values["hongan_advisor"] != current["hongan_advisor"] and not change_reason:
            raise HTTPException(422, "修改港安顾问必须填写变更原因。")
        phone, email = values.get("phone", current["phone"]), values.get("email", current["email"])
        matches = duplicate_matches(conn, phone, email, customer_id)
        if matches:
            raise HTTPException(409, detail={"message": "手机号或邮箱与其他客户重复，请交由主管确认合并。", "matches": matches})
        if values:
            assignments = ", ".join(f"{key} = ?" for key in values)
            conn.execute(f"UPDATE customers SET {assignments}, updated_at = ?, version = version + 1 WHERE id = ?", (*values.values(), now_iso(), customer_id))
            if "phone" in values or "email" in values:
                conn.execute("DELETE FROM customer_identifiers WHERE customer_id = ?", (customer_id,))
                add_identifiers(conn, customer_id, phone, email)
            changes = {key: {"from": current[key], "to": value} for key, value in values.items() if key in current.keys() and current[key] != value}
            audit(conn, user, "customer.updated", "customer", customer_id, {"fields": list(values), "changes": changes, "reason": change_reason or None})
        if custom_values is not None:
            save_custom_values(conn, customer_id, custom_values, user)
            if not values:
                conn.execute("UPDATE customers SET updated_at=?, version=version+1 WHERE id=?", (now_iso(), customer_id))
            audit(conn, user, "customer.custom_fields_updated", "customer", customer_id, {"fieldIds": list(custom_values)})
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        customer_data = attach_customer_relations(conn, [dict(row)])[0]
    return {"customer": customer_data}


@app.post("/api/customers/{customer_id}/followups", status_code=201)
def add_followup(customer_id: str, payload: FollowupPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if payload.stageAfter not in STAGES:
        raise HTTPException(422, "无效的客户阶段。")
    followup_id, created_at = str(uuid4()), now_iso()
    with db() as conn:
        assert_customer_access(conn, customer_id, user)
        conn.execute(
            "INSERT INTO followups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (followup_id, customer_id, user["id"], user["name"], payload.method, payload.content.strip(), payload.outcome.strip(), payload.nextAction.strip(), payload.nextFollowupAt or None, payload.stageAfter, created_at),
        )
        conn.execute("UPDATE customers SET stage = ?, updated_at = ?, version = version + 1 WHERE id = ?", (payload.stageAfter, created_at, customer_id))
        audit(conn, user, "followup.created", "customer", customer_id, {"followupId": followup_id, "stage": payload.stageAfter})
    return {"id": followup_id, "createdAt": created_at}


@app.post("/api/customers/{customer_id}/assign")
def assign_customer(customer_id: str, payload: AssignPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    owner = owner_for_request(payload.ownerId, user)
    with db() as conn:
        current = assert_customer_access(conn, customer_id, user)
        if current["owner_id"] == owner["id"]:
            raise HTTPException(422, "客户已经属于该商务经理。")
        changed_at = now_iso()
        conn.execute("UPDATE customers SET owner_id=?, owner_name=?, owner_team=?, updated_at=?, version=version+1 WHERE id=?", (owner["id"], owner["name"], owner["team"], changed_at, customer_id))
        conn.execute("INSERT INTO assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid4()), customer_id, current["owner_id"], current["owner_name"], current["owner_team"], owner["id"], owner["name"], owner["team"], payload.reason.strip(), user["id"], user["name"], changed_at))
        audit(conn, user, "customer.assigned", "customer", customer_id, {"from": current["owner_id"], "to": owner["id"], "reason": payload.reason})
    return {"ok": True}


@app.post("/api/customers/merge")
def merge_customers(payload: MergePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    if payload.sourceCustomerId == payload.targetCustomerId:
        raise HTTPException(422, "不能将客户与自己合并。")
    with db() as conn:
        source = assert_customer_access(conn, payload.sourceCustomerId, user)
        target = assert_customer_access(conn, payload.targetCustomerId, user)
        for field in ("phone", "email"):
            if not target[field] and source[field]:
                conn.execute(f"UPDATE customers SET {field} = ? WHERE id = ?", (source[field], target["id"]))
        conn.execute("UPDATE followups SET customer_id = ? WHERE customer_id = ?", (target["id"], source["id"]))
        conn.execute("UPDATE assignments SET customer_id = ? WHERE customer_id = ?", (target["id"], source["id"]))
        existing = {(row["kind"], row["normalized_value"]) for row in conn.execute("SELECT kind, normalized_value FROM customer_identifiers WHERE customer_id = ?", (target["id"],))}
        for ident in conn.execute("SELECT kind, normalized_value, display_value, created_at FROM customer_identifiers WHERE customer_id = ?", (source["id"],)).fetchall():
            if (ident["kind"], ident["normalized_value"]) not in existing:
                conn.execute("UPDATE customer_identifiers SET customer_id = ? WHERE customer_id = ? AND kind = ? AND normalized_value = ?", (target["id"], source["id"], ident["kind"], ident["normalized_value"]))
        conn.execute("DELETE FROM customer_identifiers WHERE customer_id = ?", (source["id"],))
        for value in conn.execute("SELECT field_id, value_text, updated_by, updated_at FROM customer_field_values WHERE customer_id=?", (source["id"],)).fetchall():
            existing_value = conn.execute("SELECT value_text FROM customer_field_values WHERE customer_id=? AND field_id=?", (target["id"], value["field_id"])).fetchone()
            if not existing_value:
                conn.execute("INSERT INTO customer_field_values VALUES (?, ?, ?, ?, ?)", (target["id"], value["field_id"], value["value_text"], value["updated_by"], value["updated_at"]))
            elif not existing_value["value_text"] and value["value_text"]:
                conn.execute("UPDATE customer_field_values SET value_text=?, updated_by=?, updated_at=? WHERE customer_id=? AND field_id=?", (value["value_text"], value["updated_by"], value["updated_at"], target["id"], value["field_id"]))
        conn.execute("DELETE FROM customer_field_values WHERE customer_id=?", (source["id"],))
        merged_at = now_iso()
        conn.execute("UPDATE customers SET archived_at = ?, merged_into_id = ?, updated_at = ?, version = version + 1 WHERE id = ?", (merged_at, target["id"], merged_at, source["id"]))
        conn.execute("UPDATE customers SET updated_at = ?, version = version + 1 WHERE id = ?", (merged_at, target["id"]))
        conn.execute("INSERT INTO merge_events VALUES (?, ?, ?, ?, ?, ?, ?)", (str(uuid4()), source["id"], target["id"], payload.reason.strip(), user["id"], user["name"], merged_at))
        audit(conn, user, "customer.merged", "customer", target["id"], {"sourceCustomerId": source["id"], "reason": payload.reason})
    return {"ok": True, "targetCustomerId": target["id"]}


@app.get("/api/followups")
def list_followups(
    limit: int = Query(100, ge=1, le=500), due_only: bool = Query(False, alias="dueOnly"),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    clause, params = access_clause(user)
    due_clause = f" AND f.next_followup_at IS NOT NULL AND {sql_date('f.next_followup_at')} <= {sql_today()}" if due_only else ""
    with db() as conn:
        rows = conn.execute(
            f"""SELECT f.*, c.name customer_name, c.customer_code, c.owner_name, c.owner_team, c.placement_status
            FROM followups f JOIN customers c ON c.id=f.customer_id
            WHERE c.archived_at IS NULL AND {clause}{due_clause}
            ORDER BY f.created_at DESC LIMIT ?""", (*params, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/api/batches")
def list_batches(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    clause, params = access_clause(user)
    with db() as conn:
        rows = conn.execute(
            f"""SELECT b.*, COUNT(c.id) customer_count,
            COALESCE(SUM(c.intent_amount),0) intent_amount,
            COALESCE(SUM(c.funded_amount),0) funded_amount,
            COALESCE(SUM(c.actual_amount),0) actual_amount,
            SUM(CASE WHEN c.placement_status='已参与' THEN 1 ELSE 0 END) closed_count,
            SUM(CASE WHEN c.placement_status='已流失' THEN 1 ELSE 0 END) lost_count
            FROM placement_batches b
            LEFT JOIN customers c ON c.target_batch_id=b.id AND c.archived_at IS NULL AND {clause}
            GROUP BY b.id ORDER BY COALESCE(b.close_date, '9999-12-31'), b.created_at DESC""", params,
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/batches", status_code=201)
def create_batch(payload: BatchPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    if payload.status not in BATCH_STATUSES:
        raise HTTPException(422, "无效的批次状态。")
    batch_id, timestamp = str(uuid4()), now_iso()
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO placement_batches VALUES (?, ?, '定增项目', ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, payload.name.strip(), payload.closeDate or None, payload.status, payload.targetAmount, payload.notes.strip(), user["id"], timestamp, timestamp),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "批次名称已经存在。") from exc
        audit(conn, user, "batch.created", "placement_batch", batch_id, {"name": payload.name, "closeDate": payload.closeDate})
        row = conn.execute("SELECT * FROM placement_batches WHERE id=?", (batch_id,)).fetchone()
    return {"batch": dict(row)}


@app.patch("/api/batches/{batch_id}")
def update_batch(batch_id: str, payload: BatchPatch, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_supervisor(user)
    values = payload.model_dump(exclude_none=True)
    mapping = {"closeDate": "close_date", "targetAmount": "target_amount"}
    values = {mapping.get(key, key): value.strip() if isinstance(value, str) else value for key, value in values.items()}
    if values.get("status") and values["status"] not in BATCH_STATUSES:
        raise HTTPException(422, "无效的批次状态。")
    with db() as conn:
        if not conn.execute("SELECT id FROM placement_batches WHERE id=?", (batch_id,)).fetchone():
            raise HTTPException(404, "批次不存在。")
        if values:
            setters = ", ".join(f"{key}=?" for key in values)
            try:
                conn.execute(f"UPDATE placement_batches SET {setters}, updated_at=? WHERE id=?", (*values.values(), now_iso(), batch_id))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "批次名称已经存在。") from exc
            audit(conn, user, "batch.updated", "placement_batch", batch_id, {"fields": list(values)})
        row = conn.execute("SELECT * FROM placement_batches WHERE id=?", (batch_id,)).fetchone()
    return {"batch": dict(row)}


HEADER_ALIASES = {
    "name": ["客户姓名", "真实姓名", "客户真实姓名", "姓名", "客户名称", "名称"], "wechatNickname": ["微信昵称", "微信名", "昵称"], "phone": ["手机号", "手机", "联系电话", "电话"],
    "email": ["邮箱", "电子邮箱", "email"], "company": ["公司", "公司名称", "机构", "单位"],
    "source": ["客户来源", "来源", "渠道"], "sourceDetail": ["来源明细", "活动名称", "渠道名称"],
    "stage": ["客户阶段", "当前阶段", "生命周期", "状态"], "priority": ["优先级", "客户级别"],
    "accountStatus": ["开户状态", "是否完成开户", "港券开户状态", "证券开户状态"], "accountBroker": ["开户券商", "香港券商", "券商", "开户证券"], "accountOpenedAt": ["开户日期", "注册日期"],
    "twCode": ["TW编号", "TW客户编号", "客户唯一编号", "客户编号"],
    "brokerDepositAmount": ["入金金额/USD", "入金金额", "港券入金金额"], "capitalDestination": ["资金流向", "资金去向"],
    "hkAdvisor": ["港安顾问"], "sourceAdvisorLabel": ["骄阳顾问", "商务顾问", "客户顾问"],
    "intentStatus": ["定增意向", "意向状态", "顾问判断"], "placementStatus": ["定增推进", "节点进度", "定增状态"],
    "intentAmount": ["意向金额", "意向额度", "意向额度(USD)"], "fundedAmount": ["到账金额", "到账金额(USD)"],
    "actualAmount": ["实际参与金额", "实际定增", "定增金额"], "lostReason": ["流失原因", "取消原因"],
    "notes": ["备注", "情况说明", "最新跟进"],
}


XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XLSX_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def xlsx_column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    value = 0
    for letter in letters.group(0):
        value = value * 26 + ord(letter) - 64
    return value - 1


def xlsx_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(part.text or "" for part in node.iter(f"{{{XLSX_MAIN_NS}}}t"))


def parse_xlsx_without_styles(content: bytes) -> list[tuple[str, list[list[Any]]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(422, "这不是有效的 .xlsx 文件，文件可能已损坏或扩展名不正确。") from exc
    with archive:
        names = set(archive.namelist())
        required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        if not required.issubset(names):
            raise HTTPException(422, "Excel 文件缺少工作簿结构，可能已损坏。")
        try:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [xlsx_text(item) for item in shared_root.findall(f"{{{XLSX_MAIN_NS}}}si")]

            rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            relationships = {
                rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
                for rel in rel_root.findall(f"{{{XLSX_PACKAGE_REL_NS}}}Relationship")
            }
            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets: list[tuple[str, list[list[Any]]]] = []
            for sheet in workbook_root.findall(f".//{{{XLSX_MAIN_NS}}}sheet"):
                sheet_name = sheet.attrib.get("name", "未命名工作表")
                rel_id = sheet.attrib.get(f"{{{XLSX_REL_NS}}}id", "")
                target = relationships.get(rel_id, "")
                if not target:
                    continue
                sheet_path = target.lstrip("/") if target.startswith("/xl/") else f"xl/{target.lstrip('/')}"
                sheet_path = str(Path(sheet_path))
                if sheet_path not in names:
                    continue
                root = ET.fromstring(archive.read(sheet_path))
                parsed_rows: list[list[Any]] = []
                for row_node in root.findall(f".//{{{XLSX_MAIN_NS}}}sheetData/{{{XLSX_MAIN_NS}}}row"):
                    values: dict[int, Any] = {}
                    for cell in row_node.findall(f"{{{XLSX_MAIN_NS}}}c"):
                        column = xlsx_column_index(cell.attrib.get("r", "A1"))
                        cell_type = cell.attrib.get("t", "")
                        value_node = cell.find(f"{{{XLSX_MAIN_NS}}}v")
                        raw_value = value_node.text if value_node is not None else ""
                        if cell_type == "s":
                            try:
                                value: Any = shared_strings[int(raw_value)]
                            except (ValueError, IndexError):
                                value = ""
                        elif cell_type == "inlineStr":
                            value = xlsx_text(cell.find(f"{{{XLSX_MAIN_NS}}}is"))
                        elif cell_type == "b":
                            value = raw_value == "1"
                        elif cell_type in {"str", "e"}:
                            value = raw_value
                        else:
                            try:
                                number = float(raw_value)
                                value = int(number) if number.is_integer() else number
                            except (TypeError, ValueError):
                                value = raw_value
                        values[column] = value
                    if values:
                        width = max(values) + 1
                        parsed_rows.append([values.get(index, "") for index in range(width)])
                sheets.append((sheet_name, parsed_rows))
            return sheets
        except ET.ParseError as exc:
            raise HTTPException(422, "Excel 文件内部 XML 无法读取，文件可能已损坏。") from exc


def header_match_score(row: list[Any]) -> int:
    return sum(1 for value in row if any(header_alias_matches(value, aliases) for aliases in HEADER_ALIASES.values()))


def header_alias_matches(header: Any, aliases: list[str]) -> bool:
    """Match ordinary aliases plus versioned labels such as 备注(最后更新日期：2026.05.22)."""
    normalized = re.sub(r"\s+", "", simplify_text(header)).lower()
    for alias in aliases:
        alias_normalized = re.sub(r"\s+", "", simplify_text(alias)).lower()
        if normalized == alias_normalized:
            return True
        if alias_normalized == "备注" and normalized.startswith("备注("):
            return True
    return False


def is_secondary_header(row: list[Any]) -> bool:
    header_words = ("姓名", "昵称", "手机", "邮箱", "顾问", "状态", "金额", "数量", "市值", "日期", "备注", "来源", "进度", "批次")
    values = [str(value or "").strip() for value in row]
    matches = sum(1 for value in values if value and any(word in value for word in header_words))
    return bool(values) and not values[0] and matches >= 2


def unique_headers(values: list[Any]) -> list[str]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(values):
        base = simplify_text(value).strip() or f"未命名列{index + 1}"
        counts[base] = counts.get(base, 0) + 1
        headers.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return headers


IMPORT_PLACEHOLDERS = {"", "/", "-", "--", "#NAME?", "#VALUE!", "#REF!"}
ACCOUNT_STATUS_IMPORT_MAP = {
    "未开户": "未启动", "未完成开户": "未启动", "未提交申请": "未启动", "否": "未启动",
    "提交中": "资料准备", "待初审资料": "资料准备", "待传住址证明": "资料准备", "待审住址证明": "资料准备",
    "处理中": "开户审核", "待终审": "开户审核",
    "已开通": "已开户", "已开户": "已开户", "已开户未入金": "已开户", "已开户入金": "已开户", "开户成功": "已开户", "完成开户": "已开户", "已完成开户": "已开户", "是": "已开户",
    "开户失败": "开户失败", "驳回": "开户失败", "开户资料驳回至客服": "开户失败", "开户资料驳回至客户": "开户失败",
}


def clean_import_cell(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = simplify_text(value).strip()
        return "" if cleaned.upper() in IMPORT_PLACEHOLDERS else cleaned
    return value


def simplify_text(value: Any) -> str:
    """Convert user-facing Chinese text to simplified Chinese while preserving non-text cells."""
    return OPENCC_T2S.convert(str(value or "")).translate(TRADITIONAL_VARIANT_OVERRIDES)


def normalize_import_date(value: Any) -> str | None:
    cleaned = clean_import_cell(value)
    if cleaned in (None, "", 0, "0"):
        return None
    text = str(cleaned).strip().replace("/", "-").replace(".", "-")
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def normalize_tw_code(value: Any) -> str:
    """Return a canonical TW identifier, or an empty string for ordinary text."""
    match = re.fullmatch(r"\s*(TW\d+)\s*", str(clean_import_cell(value) or ""), re.IGNORECASE)
    return match.group(1).upper() if match else ""


def detect_tw_header(headers: list[str], rows: list[list[Any]]) -> str | None:
    """Find an explicit TW column, or a column whose values are all TW codes."""
    explicit = next((header for header in headers if header_alias_matches(header, HEADER_ALIASES["twCode"])), None)
    if explicit:
        return explicit
    for index, header in enumerate(headers):
        values = [row[index] for row in rows if index < len(row) and clean_import_cell(row[index])]
        if len(values) >= 3 and all(normalize_tw_code(value) for value in values):
            return header
    return None


def split_advisor_label(value: Any) -> list[str]:
    label = str(clean_import_cell(value) or "").strip()
    return [item.strip() for item in re.split(r"[+＋、,，/／;；]+", label) if item.strip()]


def detect_holding_snapshots(headers: list[str]) -> list[dict[str, str]]:
    year_matches = re.findall(r"(20\d{2})[./-]\d{1,2}[./-]\d{1,2}", " ".join(headers))
    if not year_matches:
        return []
    year = year_matches[-1]
    grouped: dict[str, dict[str, str]] = {}
    for header in headers:
        match = re.search(r"(?P<month>\d{1,2})[./-](?P<day>\d{1,2}).*(?P<metric>持仓数量|持仓股数|持仓市值)", header)
        if not match:
            continue
        snapshot_date = f"{year}-{int(match['month']):02d}-{int(match['day']):02d}"
        item = grouped.setdefault(snapshot_date, {"snapshotDate": snapshot_date, "securityName": "二级市场持仓", "sourceLabel": "港安历史持仓"})
        if "市值" in match["metric"]:
            item["marketValueHeader"] = header
        else:
            item["quantityHeader"] = header
    return [grouped[key] for key in sorted(grouped)]


def import_diagnostics(headers: list[str], rows: list[list[Any]], mapping: dict[str, str]) -> dict[str, Any]:
    tw_header = detect_tw_header(headers, rows)
    if tw_header:
        mapping["twCode"] = tw_header
        if mapping.get("notes") == tw_header:
            mapping.pop("notes")
    value_rows = [{headers[index]: (row[index] if index < len(row) else "") for index in range(len(headers))} for row in rows]
    placeholder_count = sum(1 for row in value_rows for value in row.values() if isinstance(value, str) and value.strip().upper() in IMPORT_PLACEHOLDERS - {""})
    name_header = mapping.get("name")
    nickname_header = mapping.get("wechatNickname")
    mapped_rows = [{field: row.get(header, "") for field, header in mapping.items()} for row in value_rows]
    normalized_rows = [normalize_import_row(row) for row in mapped_rows]
    missing_names = sum(
        1 for row in normalized_rows
        if not str(row.get("name", "")).strip() and not str(row.get("wechatNickname", "")).strip()
    )
    nickname_fallback_count = sum(
        1 for raw, normalized in zip(mapped_rows, normalized_rows)
        if not clean_import_cell(raw.get("name", "")) and bool(clean_import_cell(raw.get("wechatNickname", "")))
    )
    unidentified_rows = sum(
        1 for row in normalized_rows
        if (str(row.get("name", "")).strip() or str(row.get("wechatNickname", "")).strip()) and not normalize_phone(str(row.get("phone", ""))) and not normalize_email(str(row.get("email", ""))) and not normalize_tw_code(row.get("twCode", ""))
    )
    advisor_header = mapping.get("sourceAdvisorLabel")
    labels = sorted({str(clean_import_cell(row.get(advisor_header, "")) or "") for row in value_rows if clean_import_cell(row.get(advisor_header, ""))}) if advisor_header else []
    user_names = {item["name"] for item in platform_users(True)}
    unresolved = sorted({name for label in labels for name in split_advisor_label(label) if name not in user_names})
    warnings: list[dict[str, Any]] = []
    if placeholder_count:
        warnings.append({"code": "placeholder", "message": "已识别 /、#NAME? 等占位值，导入时会按空值处理。", "count": placeholder_count})
    if missing_names:
        warnings.append({"code": "missing_name", "message": "客户姓名和微信昵称均为空的行不会导入。", "count": missing_names})
    if nickname_fallback_count:
        warnings.append({"code": "nickname_fallback", "message": "真实姓名为空的记录会保留姓名为空，并单独写入微信昵称。", "count": nickname_fallback_count})
    if unidentified_rows:
        warnings.append({"code": "unidentified", "message": "以下记录没有手机号和邮箱，无法自动识别重复客户，导入前需要明确确认。", "count": unidentified_rows})
    if unresolved:
        warnings.append({"code": "unresolved_advisor", "message": "以下历史顾问未能匹配当前 MuskZoom 账号，需要管理员后续映射。", "labels": unresolved})
    if tw_header:
        warnings.append({"code": "tw_snapshot", "message": "已识别 TW 编号，将按全量快照合并：已有客户更新券商状态，新 TW 才新增。", "header": tw_header})
    snapshots = detect_holding_snapshots(headers)
    if snapshots:
        warnings.append({"code": "holding_snapshots", "message": f"已识别 {len(snapshots)} 个历史持仓快照日期，将作为独立历史记录导入。", "count": len(snapshots)})
    profile = "hongan_master" if mapping.get("hkAdvisor") and mapping.get("sourceAdvisorLabel") and mapping.get("accountStatus") else "standard"
    return {
        "profile": profile, "warnings": warnings, "advisorLabels": labels[:100], "unresolvedAdvisorAliases": unresolved,
        "holdingSnapshots": snapshots, "twHeader": tw_header, "dataQuality": {"placeholderCells": placeholder_count, "missingDisplayNameRows": missing_names, "nicknameFallbackRows": nickname_fallback_count, "unidentifiedRows": unidentified_rows, "hasTwSnapshot": bool(tw_header)},
    }


def normalize_import_row(row: dict[str, Any]) -> dict[str, Any]:
    values = {key: clean_import_cell(value) for key, value in row.items()}
    tw_code = normalize_tw_code(values.get("twCode", ""))
    if not tw_code:
        tw_code = next((normalize_tw_code(value) for value in values.values() if normalize_tw_code(value)), "")
    if tw_code:
        values["twCode"] = tw_code
    # Some broker snapshots put the TW identifier in a column named 备注.
    # Once recognized, it is an identifier rather than a customer note.
    if tw_code and normalize_tw_code(values.get("notes", "")) == tw_code:
        values["notes"] = ""
    values["accountOpenedAt"] = normalize_import_date(values.get("accountOpenedAt"))
    account_status = str(values.get("accountStatus", "")).strip()
    if account_status:
        values["accountStatus"] = ACCOUNT_STATUS_IMPORT_MAP.get(account_status, account_status)
    if not values.get("placementStatus") and values.get("capitalDestination") == "参与定增":
        values["placementStatus"] = "已参与"
    return values


def save_advisor_alias_mappings(conn: sqlite3.Connection, values: dict[str, str], user: dict[str, Any], users_by_id: dict[str, dict[str, Any]]) -> None:
    for raw_alias, user_id in values.items():
        alias = str(raw_alias).strip()
        if not alias or not user_id:
            continue
        target = users_by_id.get(str(user_id))
        if not target or target["rolePermission"] not in {"manager", "supervisor"}:
            raise HTTPException(422, f"历史顾问“{alias}”未映射到有效的商务经理或部门主管。")
        conn.execute(
            """INSERT INTO advisor_alias_mappings(alias, user_id, user_name, user_team, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET user_id=excluded.user_id, user_name=excluded.user_name, user_team=excluded.user_team, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (alias, target["id"], target["name"], target["team"], user["id"], now_iso()),
        )


def advisor_alias_users(conn: sqlite3.Connection, users_by_name: dict[str, dict[str, Any]], users_by_id: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = dict(users_by_name)
    for row in conn.execute("SELECT alias, user_id FROM advisor_alias_mappings").fetchall():
        target = users_by_id.get(row["user_id"])
        if target:
            result[row["alias"]] = target
    return result


def resolve_import_assignment(conn: sqlite3.Connection, row: dict[str, Any], fallback_owner: dict[str, Any], user: dict[str, Any], advisor_users: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    values = dict(row)
    label = values.get("sourceAdvisorLabel", "")
    if user["rolePermission"] == "manager":
        return fallback_owner, values
    matched = [advisor_users[name] for name in split_advisor_label(label) if name in advisor_users] if label else []
    if user["rolePermission"] == "supervisor":
        matched = [item for item in matched if item["team"] == user["team"]]
    manager = next((item for item in matched if item["rolePermission"] == "manager"), None)
    owner = fallback_owner
    if owner["id"] == UNASSIGNED_OWNER_ID and not matched:
        bound_owner, _ = default_advisor_binding(conn, values.get("hkAdvisor", values.get("hongan_advisor", "")), customer_type_for_values(values))
        if bound_owner:
            owner = bound_owner
    if manager and (user["rolePermission"] in {"admin", "developer"} or manager["team"] == user["team"]):
        owner = manager
    collaborator_ids = list(values.get("collaboratorIds", []) or [])
    collaborator_ids.extend(item["id"] for item in matched if item["id"] != owner["id"])
    values["collaboratorIds"] = list(dict.fromkeys(collaborator_ids))
    return owner, values


def parse_import_file(filename: str, content: bytes) -> tuple[list[str], list[list[Any]], str]:
    if filename.lower().endswith(".csv"):
        text = content.decode("utf-8-sig")
        raw_rows = list(csv.reader(io.StringIO(text)))
        sheet_name = "CSV 数据"
    elif filename.lower().endswith(".xlsx"):
        sheets = parse_xlsx_without_styles(content)
        candidates = []
        for name, rows in sheets:
            non_empty = [list(row) for row in rows if any(value not in (None, "") for value in row)]
            if not non_empty:
                continue
            best_score = max((header_match_score(row) for row in non_empty[:30]), default=0)
            candidates.append((best_score, len(non_empty), name, non_empty))
        if not candidates:
            raise HTTPException(422, "Excel 文件中没有可读取的数据。")
        # Workbooks often carry a short historical sheet beside the current
        # full snapshot. Once a sheet has enough recognizable customer headers,
        # prefer the largest data sheet over a smaller one with one extra label.
        eligible = [item for item in candidates if item[0] >= 2] or candidates
        _, _, sheet_name, raw_rows = max(eligible, key=lambda item: (item[1], item[0]))
    else:
        raise HTTPException(422, "目前支持 .xlsx 和 .csv 文件。")
    raw_rows = [[clean_import_cell(value) for value in row] for row in raw_rows if any(value not in (None, "") for value in row)]
    sheet_name = simplify_text(sheet_name)
    if not raw_rows:
        raise HTTPException(422, "文件中没有可读取的数据。")
    header_index = max(range(min(30, len(raw_rows))), key=lambda i: header_match_score(raw_rows[i]))
    header_values = list(raw_rows[header_index])
    data_index = header_index + 1
    if data_index < len(raw_rows) and is_secondary_header(raw_rows[data_index]):
        secondary = list(raw_rows[data_index])
        width = max(len(header_values), len(secondary))
        header_values += [""] * (width - len(header_values))
        secondary += [""] * (width - len(secondary))
        header_values = [secondary[index] if str(secondary[index] or "").strip() else header_values[index] for index in range(width)]
        data_index += 1
    headers = unique_headers(header_values)
    rows = [list(row) + [None] * (len(headers) - len(row)) for row in raw_rows[data_index:]]
    return headers, rows, sheet_name


@app.post("/api/imports/preview")
def import_preview(payload: ImportPreviewPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user["canImportCustomers"]:
        raise HTTPException(403, "您的账号未开通客户导入权限。")
    try:
        content = base64.b64decode(payload.dataBase64, validate=True)
    except Exception as exc:
        raise HTTPException(422, "文件内容无效。") from exc
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(413, "文件不能超过 15MB。")
    try:
        headers, raw_rows, sheet_name = parse_import_file(payload.filename, content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "无法解析该文件，请确认文件未损坏，或另存为新的 .xlsx 后重试。") from exc
    mapping = {}
    for field, aliases in HEADER_ALIASES.items():
        match = next((header for header in headers if header_alias_matches(header, aliases)), None)
        if match:
            mapping[field] = match
    with db() as conn:
        custom_fields = conn.execute("SELECT * FROM customer_fields WHERE active=1 ORDER BY display_order, created_at").fetchall()
    custom_mapping = {}
    for field in custom_fields:
        match = next((header for header in headers if simplify_text(header).strip().lower() == simplify_text(field["label"]).strip().lower()), None)
        if match:
            custom_mapping[field["id"]] = match
    preview_rows = [{headers[i]: (row[i] if i < len(row) and row[i] is not None else "") for i in range(len(headers))} for row in raw_rows[:IMPORT_ROW_LIMIT]]
    diagnostics = import_diagnostics(headers, raw_rows[:IMPORT_ROW_LIMIT], mapping)
    return {"headers": headers, "suggestedMapping": mapping, "suggestedCustomMapping": custom_mapping, "customerFields": [field_dict(row) for row in custom_fields], "rows": preview_rows, "totalRows": len(raw_rows), "truncated": len(raw_rows) > IMPORT_ROW_LIMIT, "sheetName": sheet_name, "textNormalization": "繁体中文已统一转换为简体中文", **diagnostics}


@app.post("/api/imports/commit")
def import_commit(payload: ImportCommitPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user["canImportCustomers"]:
        raise HTTPException(403, "您的账号未开通客户导入权限。")
    if len(payload.rows) > IMPORT_ROW_LIMIT:
        raise HTTPException(422, f"单次最多导入 {IMPORT_ROW_LIMIT} 行，请拆分文件。")
    mode = str(payload.mode or "append").strip().lower()
    if mode not in {"append", "snapshot"}:
        raise HTTPException(422, "导入模式无效。")
    if user["rolePermission"] != "manager" and not payload.ownerId:
        raise HTTPException(422, "请选择未匹配顾问的默认主负责人。")
    owner = owner_for_request(payload.ownerId, user)
    platform_users_all = platform_users(True)
    users_by_name = {item["name"]: item for item in platform_users_all}
    users_by_id = {item["id"]: item for item in platform_users_all}
    if payload.advisorAliasMappings:
        require_field_manager(user)
    created, updated, conflicts, errors = [], [], [], []
    unchanged_count = 0
    quality = {"placeholderRowsSkipped": 0, "nicknameFallbackRows": 0, "unidentifiedRowsImported": 0, "twMatchedRows": 0, "twNewRows": 0, "twDuplicateRows": 0, "twMissingRows": 0}
    has_tw_snapshot = mode == "snapshot" or any(normalize_tw_code(row.get("twCode", "")) for row in payload.rows)
    seen_tw_codes: set[str] = set()
    with db() as conn:
        if payload.advisorAliasMappings:
            save_advisor_alias_mappings(conn, payload.advisorAliasMappings, user, users_by_id)
            audit(conn, user, "advisor_alias_mappings.updated", "advisor_alias_mapping", "import", {"aliases": sorted(payload.advisorAliasMappings)})
        advisor_users = advisor_alias_users(conn, users_by_name, users_by_id)
        for index, raw_row in enumerate(payload.rows, start=1):
            row = normalize_import_row(raw_row)
            try:
                require_hongan_advisor_permission(user, row.get("hkAdvisor", row.get("hongan_advisor", "")))
            except HTTPException as exc:
                errors.append({"row": index, "message": str(exc.detail)})
                continue
            tw_code = normalize_tw_code(row.get("twCode", ""))
            if has_tw_snapshot and not tw_code:
                errors.append({"row": index, "message": "全量快照行缺少有效 TW 编号，未创建客户。"})
                quality["twMissingRows"] += 1
                continue
            if tw_code:
                if tw_code in seen_tw_codes:
                    conflicts.append({"row": index, "name": row.get("name", "") or row.get("wechatNickname", "") or tw_code, "detail": "同一文件内 TW 编号重复，已跳过重复行。"})
                    quality["twDuplicateRows"] += 1
                    continue
                seen_tw_codes.add(tw_code)
                existing = find_customer_by_tw(conn, tw_code)
                if existing:
                    conn.execute("SAVEPOINT import_row")
                    try:
                        changes = update_broker_snapshot_customer(conn, existing, row, tw_code, user)
                        add_tw_identifier(conn, existing["id"], tw_code)
                        conn.execute("RELEASE SAVEPOINT import_row")
                        quality["twMatchedRows"] += 1
                        if changes:
                            updated.append({"row": index, "id": existing["id"], "customerCode": existing["customer_code"], "changes": changes})
                        else:
                            unchanged_count += 1
                        continue
                    except HTTPException as exc:
                        conn.execute("ROLLBACK TO SAVEPOINT import_row")
                        conn.execute("RELEASE SAVEPOINT import_row")
                        errors.append({"row": index, "message": str(exc.detail)})
                        continue
                quality["twNewRows"] += 1
            if not str(row.get("name", "")).strip() and not str(row.get("wechatNickname", "")).strip():
                errors.append({"row": index, "message": "客户姓名和微信昵称均为空"})
                quality["placeholderRowsSkipped"] += 1
                continue
            if not clean_import_cell(raw_row.get("name", "")) and clean_import_cell(raw_row.get("wechatNickname", "")):
                quality["nicknameFallbackRows"] += 1
            has_identifier = bool(normalize_phone(str(row.get("phone", "")))) or bool(normalize_email(str(row.get("email", "")))) or bool(tw_code)
            if not has_identifier:
                if not payload.allowUnidentifiedRows:
                    errors.append({"row": index, "message": "缺少手机号和邮箱；请在导入页确认允许导入无联系方式的历史记录。"})
                    continue
            conn.execute("SAVEPOINT import_row")
            try:
                row_owner, row = resolve_import_assignment(conn, row, owner, user, advisor_users)
                record = create_customer_record(conn, row, row_owner, user)
                save_custom_values(conn, record["id"], row.get("customValues", {}), user)
                potential_duplicates = potential_identity_matches(conn, record["name"], record["wechat_nickname"], record["id"])
                conn.execute("RELEASE SAVEPOINT import_row")
                created.append({"row": index, "id": record["id"], "customerCode": record["customer_code"], "potentialDuplicates": potential_duplicates})
                if not has_identifier:
                    quality["unidentifiedRowsImported"] += 1
            except HTTPException as exc:
                conn.execute("ROLLBACK TO SAVEPOINT import_row")
                conn.execute("RELEASE SAVEPOINT import_row")
                if exc.status_code == 409:
                    conflicts.append({"row": index, "name": row.get("name", "") or row.get("wechatNickname", ""), "detail": exc.detail})
                else:
                    errors.append({"row": index, "message": str(exc.detail)})
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK TO SAVEPOINT import_row")
                conn.execute("RELEASE SAVEPOINT import_row")
                conflicts.append({"row": index, "name": row.get("name", "") or row.get("wechatNickname", ""), "detail": str(exc)})
        job_id = str(uuid4())
        conn.execute(
            """INSERT INTO import_jobs(id, filename, owner_id, owner_name, total_rows, created_count, updated_count, conflict_count, error_count, imported_by, imported_by_name, created_at, data_quality_json, created_customer_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, payload.filename, owner["id"], owner["name"], len(payload.rows), len(created), len(updated), len(conflicts), len(errors), user["id"], user["name"], now_iso(), json.dumps({**quality, "unchangedRows": unchanged_count, "mode": "snapshot" if has_tw_snapshot else "append"}, ensure_ascii=False), json.dumps([item["id"] for item in created], ensure_ascii=False)),
        )
        audit(conn, user, "import.completed", "import_job", job_id, {"created": len(created), "updated": len(updated), "unchanged": unchanged_count, "conflicts": len(conflicts), "errors": len(errors), "quality": quality})
    return {"jobId": job_id, "mode": "snapshot" if has_tw_snapshot else "append", "created": created, "updated": updated, "unchangedCount": unchanged_count, "conflicts": conflicts, "errors": errors, "dataQuality": quality}


@app.get("/api/imports/template.csv")
def import_template(user: dict[str, Any] = Depends(current_user)):
    if not user["canImportCustomers"]:
        raise HTTPException(403, "您的账号未开通客户导入权限。")
    base_headers = [
        "客户姓名", "微信昵称", "手机号", "邮箱", "公司", "来源", "来源明细", "客户阶段", "优先级",
        "开户状态", "开户券商", "开户日期", "入金金额/USD", "资金流向", "定增意向", "定增推进",
        "意向金额", "到账金额", "实际参与金额", "流失原因", "骄阳顾问", "备注",
    ]
    if user["rolePermission"] in {"supervisor", "admin", "developer"}:
        base_headers.insert(-2, "港安顾问")
    with db() as conn:
        custom_headers = [row["label"] for row in conn.execute("SELECT label FROM customer_fields WHERE active=1 ORDER BY display_order, created_at").fetchall()]
    headers = list(dict.fromkeys(base_headers + custom_headers))
    output = io.StringIO()
    output.write("\ufeff")
    csv.writer(output).writerow(headers)
    from fastapi.responses import Response
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=customer-import-template.csv"})


@app.get("/api/imports")
def list_import_jobs(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    with db() as conn:
        rows = conn.execute("SELECT * FROM import_jobs ORDER BY created_at DESC LIMIT 100").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["dataQuality"] = json.loads(item.pop("data_quality_json") or "{}")
        except json.JSONDecodeError:
            item["dataQuality"] = {}
        try:
            item["createdCustomerIds"] = json.loads(item.pop("created_customer_ids_json") or "[]")
        except json.JSONDecodeError:
            item["createdCustomerIds"] = []
        items.append(item)
    return {"items": items}


@app.post("/api/imports/{job_id}/rollback")
def rollback_import(job_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    with db() as conn:
        job = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "导入记录不存在。")
        if job["rolled_back_at"]:
            return {"jobId": job_id, "archived": [], "protected": [], "alreadyRolledBack": True}
        try:
            created_ids = json.loads(job["created_customer_ids_json"] or "[]")
        except json.JSONDecodeError:
            created_ids = []
        if not created_ids:
            raise HTTPException(422, "该导入记录没有可撤回的客户，可能是系统升级前的历史记录。")
        archived, protected = [], []
        timestamp = now_iso()
        for customer_id in created_ids:
            customer = conn.execute("SELECT id, customer_code, created_by, created_at, updated_at, version, archived_at FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if not customer or customer["archived_at"]:
                continue
            followup_count = conn.execute("SELECT COUNT(*) FROM followups WHERE customer_id = ?", (customer_id,)).fetchone()[0]
            snapshot_count = conn.execute("SELECT COUNT(*) FROM customer_holding_snapshots WHERE customer_id = ?", (customer_id,)).fetchone()[0]
            changed_after_import = customer["created_by"] != job["imported_by"] or customer["version"] != 1 or customer["updated_at"] != customer["created_at"] or followup_count or snapshot_count
            if changed_after_import:
                protected.append({"id": customer["id"], "customerCode": customer["customer_code"], "reason": "导入后已有编辑、跟进或持仓快照"})
                continue
            conn.execute("UPDATE customers SET archived_at = ?, updated_at = ?, version = version + 1 WHERE id = ? AND archived_at IS NULL", (timestamp, timestamp, customer_id))
            archived.append({"id": customer["id"], "customerCode": customer["customer_code"]})
        conn.execute("UPDATE import_jobs SET rolled_back_at = ?, rolled_back_by = ? WHERE id = ?", (timestamp, user["id"], job_id))
        audit(conn, user, "import.rolled_back", "import_job", job_id, {"archived": len(archived), "protected": len(protected)})
    return {"jobId": job_id, "archived": archived, "protected": protected, "alreadyRolledBack": False}


@app.get("/api/admin/users")
def admin_users(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    return {"items": [enrich_user(item) for item in platform_users(True)]}


@app.patch("/api/admin/users/{user_id}/permissions")
def update_permissions(user_id: str, payload: PermissionPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    target = platform_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "用户不存在。")
    with db() as conn:
        existing = conn.execute("SELECT can_import_customers, can_export_all, can_manage_customer_fields FROM user_permissions WHERE user_id = ?", (user_id,)).fetchone()
        import_value = int(payload.canImportCustomers) if payload.canImportCustomers is not None else (existing["can_import_customers"] if existing else None)
        export_value = int(payload.canExportAll) if payload.canExportAll is not None else (existing["can_export_all"] if existing else None)
        fields_value = int(payload.canManageCustomerFields) if payload.canManageCustomerFields is not None else (existing["can_manage_customer_fields"] if existing else None)
        conn.execute(
            "INSERT INTO user_permissions(user_id, can_import_customers, can_export_all, can_manage_customer_fields, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET can_import_customers=excluded.can_import_customers, can_export_all=excluded.can_export_all, can_manage_customer_fields=excluded.can_manage_customer_fields, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (user_id, import_value, export_value, fields_value, user["id"], now_iso()),
        )
        audit(conn, user, "permission.updated", "user", user_id, {"canImportCustomers": payload.canImportCustomers, "canExportAll": payload.canExportAll, "canManageCustomerFields": payload.canManageCustomerFields})
    return {"user": enrich_user(target)}


@app.get("/api/customer-fields")
def list_customer_fields(include_inactive: bool = Query(False, alias="includeInactive"), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if include_inactive:
        require_field_manager(user)
    where = "" if include_inactive else "WHERE active=1"
    with db() as conn:
        rows = conn.execute(f"SELECT * FROM customer_fields {where} ORDER BY display_order, created_at").fetchall()
    return {"items": [field_dict(row) for row in rows]}


@app.post("/api/customer-fields", status_code=201)
def create_customer_field(payload: CustomerFieldPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_field_manager(user)
    field_type = payload.fieldType.strip().lower()
    if field_type not in FIELD_TYPES:
        raise HTTPException(422, "字段类型无效。")
    options = normalize_field_options(payload.options)
    if field_type == "select" and not options:
        raise HTTPException(422, "单选字段至少需要一个选项。")
    field_id, timestamp = str(uuid4()), now_iso()
    field_key = f"custom_{field_id.replace('-', '')[:12]}"
    with db() as conn:
        display_order = conn.execute("SELECT COALESCE(MAX(display_order), -1) + 1 FROM customer_fields").fetchone()[0]
        try:
            conn.execute(
                "INSERT INTO customer_fields VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (field_id, field_key, payload.label.strip(), field_type, json.dumps(options, ensure_ascii=False), display_order, user["id"], timestamp, user["id"], timestamp),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "已经存在同名表头。") from exc
        audit(conn, user, "customer_field.created", "customer_field", field_id, {"label": payload.label.strip(), "fieldType": field_type})
        row = conn.execute("SELECT * FROM customer_fields WHERE id=?", (field_id,)).fetchone()
    return {"field": field_dict(row)}


@app.patch("/api/customer-fields/{field_id}")
def update_customer_field(field_id: str, payload: CustomerFieldPatch, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_field_manager(user)
    values = payload.model_dump(exclude_none=True)
    mapping = {"displayOrder": "display_order"}
    with db() as conn:
        current = conn.execute("SELECT * FROM customer_fields WHERE id=?", (field_id,)).fetchone()
        if not current:
            raise HTTPException(404, "表头不存在。")
        updates: dict[str, Any] = {}
        if "label" in values:
            updates["label"] = values["label"].strip()
        if "options" in values:
            options = normalize_field_options(values["options"])
            if current["field_type"] == "select" and not options:
                raise HTTPException(422, "单选字段至少需要一个选项。")
            updates["options_json"] = json.dumps(options, ensure_ascii=False)
        if "active" in values:
            updates["active"] = int(values["active"])
        if "displayOrder" in values:
            updates["display_order"] = values["displayOrder"]
        updates.update({"updated_by": user["id"], "updated_at": now_iso()})
        setters = ", ".join(f"{key}=?" for key in updates)
        try:
            conn.execute(f"UPDATE customer_fields SET {setters} WHERE id=?", (*updates.values(), field_id))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "已经存在同名表头。") from exc
        audit(conn, user, "customer_field.updated", "customer_field", field_id, {"fields": list(values), "active": values.get("active")})
        row = conn.execute("SELECT * FROM customer_fields WHERE id=?", (field_id,)).fetchone()
    return {"field": field_dict(row)}


@app.put("/api/customers/{customer_id}/custom-fields/{field_id}")
def update_customer_field_value(customer_id: str, field_id: str, payload: CustomerFieldValuePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db() as conn:
        customer = assert_customer_access(conn, customer_id, user)
        if customer["version"] != payload.version:
            raise HTTPException(409, "客户资料已被其他人更新，请刷新后重试。")
        field = conn.execute("SELECT * FROM customer_fields WHERE id=? AND active=1", (field_id,)).fetchone()
        if not field:
            raise HTTPException(404, "表头不存在或已停用。")
        previous_row = conn.execute("SELECT value_text FROM customer_field_values WHERE customer_id=? AND field_id=?", (customer_id, field_id)).fetchone()
        previous_value = previous_row["value_text"] if previous_row else ""
        next_value = normalize_custom_value(field, payload.value)
        save_custom_values(conn, customer_id, {field_id: payload.value}, user)
        conn.execute("UPDATE customers SET updated_at=?, version=version+1 WHERE id=?", (now_iso(), customer_id))
        audit(conn, user, "customer.custom_field_updated", "customer", customer_id, {"fieldId": field_id, "fieldLabel": field["label"], "changes": {"from": previous_value, "to": next_value}})
        version = customer["version"] + 1
    return {"ok": True, "version": version}


@app.get("/api/admin/audit")
def audit_logs(limit: int = Query(100, ge=1, le=500), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    with db() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item.pop("detail_json"))
        items.append(item)
    return {"items": items}


@app.get("/api/export/customers.csv")
def export_customers(user: dict[str, Any] = Depends(current_user)):
    if not user["canExportAll"]:
        raise HTTPException(403, "您的账号未开通完整数据导出权限。")
    with db() as conn:
        export_columns = [
            "customer_code", "name", "wechat_nickname", "phone", "email", "company", "source", "source_detail",
            "hongan_advisor", "source_advisor_label", "owner_name", "owner_team", "stage", "priority",
            "account_status", "account_broker", "account_opened_at", "broker_deposit_amount", "capital_destination",
            "intent_status", "placement_status", "intent_amount", "funded_amount", "actual_amount", "lost_reason",
            "notes", "created_at", "updated_at",
        ]
        rows = conn.execute(
            f"SELECT id, {', '.join(export_columns)} FROM customers WHERE archived_at IS NULL ORDER BY updated_at DESC"
        ).fetchall()
        fields = conn.execute("SELECT id, label, active FROM customer_fields ORDER BY display_order, created_at").fetchall()
        field_values = conn.execute("SELECT customer_id, field_id, value_text FROM customer_field_values").fetchall()
        values_lookup = {(row["customer_id"], row["field_id"]): row["value_text"] for row in field_values}
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([
        "客户编号", "姓名", "微信昵称", "手机号", "邮箱", "公司", "来源", "来源明细",
        "港安顾问（外部引荐）", "原表骄阳顾问", "当前骄阳负责人", "当前负责人团队", "生命周期", "优先级",
        "开户状态", "开户券商", "开户日期", "入金金额/USD", "资金流向", "定增意向", "定增推进",
        "意向金额", "到账金额", "实际参与金额", "流失原因", "备注", "创建时间", "更新时间",
        *[f"{field['label']}{'（已停用）' if not field['active'] else ''}" for field in fields],
    ])
    for row in rows:
        writer.writerow([*[row[column] for column in export_columns], *[values_lookup.get((row["id"], field["id"]), "") for field in fields]])
    from fastapi.responses import Response
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=customers.csv"})


@app.get("/api/platform-logo")
def platform_logo():
    candidates = [MUSKZOOM_DB_PATH.parent / "assets" / "xbelievers.png", ROOT_DIR / "static" / "logo.png"]
    match = next((path for path in candidates if path.is_file()), None)
    if not match:
        raise HTTPException(404, "Logo not configured")
    return FileResponse(match)


app.mount("/", StaticFiles(directory=ROOT_DIR / "frontend", html=True), name="frontend")
