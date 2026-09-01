import base64
import hashlib
import hmac
import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

TEST_DIR = Path(tempfile.mkdtemp(prefix="jiaoyang-tests-"))
os.environ["CUSTOMER_DB_PATH"] = str(TEST_DIR / "test.db")
os.environ["MUSKZOOM_DB_PATH"] = str(TEST_DIR / "missing-platform.db")
os.environ["CRM_DEMO_MODE"] = "true"
os.environ["MUSKZOOM_SSO_SECRET"] = "test-only-sso-secret"

from fastapi.testclient import TestClient

from backend.main import app
import backend.main as crm_main

client = TestClient(app)


def login(username: str, password: str) -> tuple[dict, dict]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["user"]


def signed_sso_token(username: str, jti: str) -> str:
    now = int(time.time())
    payload = {
        "username": username,
        "iat": now,
        "exp": now + 60,
        "iss": "muskzoom",
        "aud": "jiaoyang-customer-crm",
        "jti": jti,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(crm_main.SSO_SECRET.encode(), encoded.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{encoded}.{signature}"


def test_sso_requires_strong_claims_and_rejects_replay():
    previous_requirement = crm_main.SSO_REQUIRE_STRONG_CLAIMS
    crm_main.SSO_REQUIRE_STRONG_CLAIMS = True
    client.cookies.clear()
    try:
        token = signed_sso_token("manager", "replay-test-1")
        first = client.post("/api/auth/sso", json={"token": token})
        assert first.status_code == 200, first.text
        assert "jy_crm_session" in first.headers.get("set-cookie", "")
        cookie_session = client.get("/api/session")
        assert cookie_session.status_code == 200
        assert cookie_session.json()["user"]["username"] == "manager"
        replay = client.post("/api/auth/sso", json={"token": token})
        assert replay.status_code == 401
    finally:
        crm_main.SSO_REQUIRE_STRONG_CLAIMS = previous_requirement
        client.cookies.clear()


def xlsx_with_unreadable_styles() -> bytes:
    workbook = '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="客户总表" sheetId="1" r:id="rId1"/></sheets></workbook>'
    relationships = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    worksheet = '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>客户姓名</t></is></c><c r="B1" t="inlineStr"><is><t>手机号</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>样式异常客户</t></is></c><c r="B2" t="inlineStr"><is><t>13900000000</t></is></c></row></sheetData></worksheet>'
    invalid_styles = '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fills count="1"><fill><solidFill/></fill></fills></styleSheet>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("xl/styles.xml", invalid_styles)
    return output.getvalue()


def xlsx_with_hongan_master_headers() -> bytes:
    workbook = '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="港安客户总表" sheetId="1" r:id="rId1"/></sheets></workbook>'
    relationships = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    worksheet = '''<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
    <row r="2"><c r="A2" t="inlineStr"><is><t>序号</t></is></c><c r="B2" t="inlineStr"><is><t>客户名称</t></is></c><c r="D2" t="inlineStr"><is><t>港安顾问</t></is></c><c r="E2" t="inlineStr"><is><t>骄阳顾问</t></is></c><c r="F2" t="inlineStr"><is><t>开户证券</t></is></c><c r="H2" t="inlineStr"><is><t>注册日期</t></is></c><c r="I2" t="inlineStr"><is><t>开户状态</t></is></c><c r="J2" t="inlineStr"><is><t>入金金额/USD</t></is></c><c r="N2" t="inlineStr"><is><t>资金流向</t></is></c><c r="O2" t="inlineStr"><is><t>5.29持仓数量</t></is></c><c r="P2" t="inlineStr"><is><t>5.29持仓市值</t></is></c><c r="Q2" t="inlineStr"><is><t>备注(最后更新日期：2026.05.22)</t></is></c></row>
    <row r="3"><c r="B3" t="inlineStr"><is><t>微信昵称</t></is></c><c r="C3" t="inlineStr"><is><t>真实姓名</t></is></c></row>
    <row r="4"><c r="A4"><v>1</v></c><c r="C4" t="inlineStr"><is><t>港安导入客户</t></is></c><c r="D4" t="inlineStr"><is><t>港安顾问甲</t></is></c><c r="E4" t="inlineStr"><is><t>演示顾问+演示主管</t></is></c><c r="F4" t="inlineStr"><is><t>港券A</t></is></c><c r="H4" t="inlineStr"><is><t>2026.05.20</t></is></c><c r="I4" t="inlineStr"><is><t>已开户入金</t></is></c><c r="J4"><v>5000</v></c><c r="N4" t="inlineStr"><is><t>参与定增</t></is></c><c r="O4"><v>100</v></c><c r="P4"><v>850</v></c></row>
    </sheetData></worksheet>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


def test_customer_lifecycle_visibility_and_permissions():
    manager_headers, manager = login("manager", "manager123")
    second_headers, second = login("manager2", "manager2123")
    admin_headers, admin = login("admin", "admin123")

    assert manager["customerScope"] == "self"
    assert manager["canImportCustomers"] is False
    assert admin["customerScope"] == "all"

    create = client.post(
        "/api/customers", headers=manager_headers,
        json={"name": "测试客户甲", "phone": "138-0000-0001", "email": "customer-a@example.com", "source": "线下沙龙", "stage": "新客户", "ownerId": second["id"]},
    )
    assert create.status_code == 201, create.text
    customer_id = create.json()["customer"]["id"]
    assert create.json()["customer"]["owner_id"] == manager["id"]
    assert client.get(f"/api/customers/{customer_id}", headers=manager_headers).status_code == 200
    assert client.get(f"/api/customers/{customer_id}", headers=second_headers).status_code == 404
    email_search = client.get("/api/customers?search=customer-a@example.com", headers=manager_headers)
    assert email_search.status_code == 200
    assert [item["id"] for item in email_search.json()["items"]] == [customer_id]

    followup = client.post(
        f"/api/customers/{customer_id}/followups", headers=manager_headers,
        json={"method": "电话", "content": "已确认开户意向", "stageAfter": "开户推进"},
    )
    assert followup.status_code == 201, followup.text

    duplicate = client.post(
        "/api/customers", headers=admin_headers,
        json={"name": "同手机号客户", "phone": "13800000001", "ownerId": second["id"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["matches"][0]["id"] == customer_id

    email_duplicate = client.post(
        "/api/customers", headers=admin_headers,
        json={"name": "同邮箱客户", "email": "CUSTOMER-A@example.com", "ownerId": second["id"]},
    )
    assert email_duplicate.status_code == 409
    assert email_duplicate.json()["detail"]["matches"][0]["id"] == customer_id

    assigned = client.post(
        f"/api/customers/{customer_id}/assign", headers=admin_headers,
        json={"ownerId": second["id"], "reason": "测试重新分配"},
    )
    assert assigned.status_code == 200, assigned.text
    assert client.get(f"/api/customers/{customer_id}", headers=manager_headers).status_code == 404
    detail = client.get(f"/api/customers/{customer_id}", headers=second_headers)
    assert detail.status_code == 200
    assert detail.json()["customer"]["owner_id"] == second["id"]
    assert len(detail.json()["assignments"]) == 2

    csv_payload = {"filename": "customers.csv", "dataBase64": base64.b64encode("客户姓名,手机号\n客户乙,13900000002".encode()).decode()}
    assert client.post("/api/imports/preview", headers=manager_headers, json=csv_payload).status_code == 403

    permission = client.patch(
        f"/api/admin/users/{manager['id']}/permissions", headers=admin_headers,
        json={"canImportCustomers": True, "canExportAll": False},
    )
    assert permission.status_code == 200
    allowed_preview = client.post("/api/imports/preview", headers=manager_headers, json=csv_payload)
    assert allowed_preview.status_code == 200, allowed_preview.text
    assert allowed_preview.json()["suggestedMapping"] == {"name": "客户姓名", "phone": "手机号"}
    assert allowed_preview.json()["totalRows"] == 1

    audit = client.get("/api/admin/audit", headers=admin_headers)
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()["items"]}
    assert {"customer.created", "followup.created", "customer.assigned", "permission.updated"} <= actions


def test_supervisor_team_scope_and_manager_cannot_assign():
    manager_headers, _ = login("manager", "manager123")
    supervisor_headers, supervisor = login("supervisor", "supervisor123")
    assert supervisor["customerScope"] == "team"
    customers = client.get("/api/customers", headers=supervisor_headers)
    assert customers.status_code == 200
    assert customers.json()["total"] >= 1
    customer_id = customers.json()["items"][0]["id"]
    denied = client.post(
        f"/api/customers/{customer_id}/assign", headers=manager_headers,
        json={"ownerId": "demo-manager-2", "reason": "越权操作"},
    )
    assert denied.status_code == 403


def test_customer_assignment_worklist_and_bulk_reassignment():
    manager_headers, manager = login("manager", "manager123")
    second_headers, second = login("manager2", "manager2123")
    supervisor_headers, _ = login("supervisor", "supervisor123")
    admin_headers, _ = login("admin", "admin123")

    created_ids = []
    for name in ("归属批量客户甲", "归属批量客户乙"):
        created = client.post(
            "/api/customers", headers=admin_headers,
            json={"name": name, "ownerId": "unassigned", "sourceAdvisorLabel": "历史顾问甲"},
        )
        assert created.status_code == 201, created.text
        created_ids.append(created.json()["customer"]["id"])

    denied_worklist = client.get("/api/customer-assignments", headers=manager_headers)
    assert denied_worklist.status_code == 403

    worklist = client.get("/api/customer-assignments?ownerId=unassigned", headers=admin_headers)
    assert worklist.status_code == 200, worklist.text
    assert set(created_ids) <= {item["id"] for item in worklist.json()["items"]}
    assert any(item["owner_id"] == "unassigned" for item in worklist.json()["ownerGroups"])

    bulk = client.post(
        "/api/customers/bulk-assign", headers=admin_headers,
        json={"customerIds": created_ids, "ownerId": manager["id"], "reason": "按历史顾问归属批量分配"},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["assignedCount"] == 2

    visible_to_manager = client.get("/api/customers?search=归属批量客户", headers=manager_headers)
    assert visible_to_manager.status_code == 200
    assert set(created_ids) <= {item["id"] for item in visible_to_manager.json()["items"]}
    details = client.get(f"/api/customers/{created_ids[0]}", headers=manager_headers)
    assert details.status_code == 200
    assert details.json()["customer"]["owner_id"] == manager["id"]
    assert any(item["reason"] == "按历史顾问归属批量分配" for item in details.json()["assignments"])

    team_reassignment = client.post(
        "/api/customers/bulk-assign", headers=supervisor_headers,
        json={"customerIds": [created_ids[0]], "ownerId": second["id"], "reason": "组内调整服务负责人"},
    )
    assert team_reassignment.status_code == 200, team_reassignment.text
    assert client.get(f"/api/customers/{created_ids[0]}", headers=manager_headers).status_code == 404
    reassigned = client.get(f"/api/customers/{created_ids[0]}", headers=second_headers)
    assert reassigned.status_code == 200
    assert reassigned.json()["customer"]["owner_id"] == second["id"]


def test_collaborators_extend_visibility_and_holding_snapshots():
    manager_headers, _ = login("manager", "manager123")
    second_headers, second = login("manager2", "manager2123")
    supervisor_headers, supervisor = login("supervisor", "supervisor123")

    created = client.post(
        "/api/customers", headers=manager_headers,
        json={"name": "协同客户", "phone": "13800000077"},
    )
    assert created.status_code == 201, created.text
    customer_id = created.json()["customer"]["id"]

    denied = client.put(
        f"/api/customers/{customer_id}/collaborators", headers=manager_headers,
        json={"userIds": [second["id"]]},
    )
    assert denied.status_code == 403

    shared = client.put(
        f"/api/customers/{customer_id}/collaborators", headers=supervisor_headers,
        json={"userIds": [second["id"], supervisor["id"]]},
    )
    assert shared.status_code == 200, shared.text
    assert {person["id"] for person in shared.json()["customer"]["collaborators"]} == {second["id"], supervisor["id"]}

    collaborator_list = client.get("/api/customers", headers=second_headers)
    assert customer_id in {item["id"] for item in collaborator_list.json()["items"]}
    detail = client.get(f"/api/customers/{customer_id}", headers=second_headers)
    assert detail.status_code == 200, detail.text
    snapshot = client.post(
        f"/api/customers/{customer_id}/holding-snapshots", headers=second_headers,
        json={"snapshotDate": "2026-05-29", "securityName": "二级市场持仓", "quantity": 100, "marketValue": 850, "sourceLabel": "测试"},
    )
    assert snapshot.status_code == 201, snapshot.text
    after_snapshot = client.get(f"/api/customers/{customer_id}", headers=second_headers)
    assert after_snapshot.json()["holdingSnapshots"][0]["market_value"] == 850


def test_hongan_master_preview_and_import_preserve_relations_and_snapshots():
    admin_headers, _ = login("admin", "admin123")
    manager_headers, manager = login("manager", "manager123")
    _, supervisor = login("supervisor", "supervisor123")
    payload = {
        "filename": "港安客户总表测试.xlsx",
        "dataBase64": base64.b64encode(xlsx_with_hongan_master_headers()).decode(),
    }
    preview = client.post("/api/imports/preview", headers=admin_headers, json=payload)
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()
    assert preview_data["profile"] == "hongan_master"
    assert preview_data["suggestedMapping"]["name"] == "真实姓名"
    assert preview_data["suggestedMapping"]["sourceAdvisorLabel"] == "骄阳顾问"
    assert preview_data["suggestedMapping"]["notes"] == "备注(最后更新日期：2026.05.22)"
    assert preview_data["holdingSnapshots"] == [{"snapshotDate": "2026-05-29", "securityName": "二级市场持仓", "sourceLabel": "港安历史持仓", "quantityHeader": "5.29持仓数量", "marketValueHeader": "5.29持仓市值"}]

    raw = preview_data["rows"][0]
    imported = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={
            "filename": "港安客户总表测试.xlsx", "ownerId": manager["id"],
            "allowUnidentifiedRows": True,
            "advisorAliasMappings": {"历史顾问甲": manager["id"], "历史主管乙": supervisor["id"]},
            "rows": [{
                "name": raw["真实姓名"], "accountBroker": raw["开户证券"], "accountOpenedAt": raw["注册日期"],
                "accountStatus": raw["开户状态"], "brokerDepositAmount": raw["入金金额/USD"],
                "capitalDestination": raw["资金流向"], "hkAdvisor": raw["港安顾问"], "sourceAdvisorLabel": "历史顾问甲+历史主管乙",
                "holdingSnapshots": [{"snapshotDate": "2026-05-29", "securityName": "二级市场持仓", "quantity": raw["5.29持仓数量"], "marketValue": raw["5.29持仓市值"], "sourceLabel": "港安历史持仓"}],
            }],
        },
    )
    assert imported.status_code == 200, imported.text
    customer_id = imported.json()["created"][0]["id"]
    detail = client.get(f"/api/customers/{customer_id}", headers=manager_headers)
    assert detail.status_code == 200, detail.text
    customer = detail.json()["customer"]
    assert customer["account_status"] == "已开户"
    assert customer["broker_deposit_amount"] == 5000
    assert customer["capital_destination"] == "参与定增"
    assert customer["hongan_advisor"] == "港安顾问甲"
    assert customer["source_advisor_label"] == "历史顾问甲+历史主管乙"
    assert {person["id"] for person in customer["collaborators"]} == {supervisor["id"]}
    assert detail.json()["holdingSnapshots"][0]["snapshot_date"] == "2026-05-29"
    assert detail.json()["holdingSnapshots"][0]["quantity"] == 100

    reused = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "港安客户总表测试.xlsx", "ownerId": manager["id"], "allowUnidentifiedRows": True, "rows": [{"name": "别名复用客户", "sourceAdvisorLabel": "历史顾问甲+历史主管乙"}]},
    )
    assert reused.status_code == 200, reused.text
    reused_id = reused.json()["created"][0]["id"]
    reused_detail = client.get(f"/api/customers/{reused_id}", headers=manager_headers).json()["customer"]
    assert {person["id"] for person in reused_detail["collaborators"]} == {supervisor["id"]}


def test_import_hygiene_uses_wechat_name_and_keeps_unmatched_advisor_unassigned():
    admin_headers, _ = login("admin", "admin123")
    manual = client.post("/api/customers", headers=admin_headers, json={"name": "", "wechatNickname": "手动微信客户"})
    assert manual.status_code == 201, manual.text
    assert manual.json()["customer"]["name"] == ""
    assert manual.json()["customer"]["wechat_nickname"] == "手动微信客户"
    duplicate_name = client.post("/api/customers", headers=admin_headers, json={"name": "", "wechatNickname": "手动微信客户"})
    assert duplicate_name.status_code == 201, duplicate_name.text
    assert duplicate_name.json()["potentialDuplicates"][0]["id"] == manual.json()["customer"]["id"]
    csv_payload = {
        "filename": "顾问待匹配.csv",
        "dataBase64": base64.b64encode("客户姓名,微信昵称,手机号,骄阳顾问,开户状态\n/,微信小王,,未来顾问甲,未开户\n张三,三三,,未来顾问甲,提交中\n李四,四四,13900000009,未来顾问甲,处理中".encode()).decode(),
    }
    preview = client.post("/api/imports/preview", headers=admin_headers, json=csv_payload)
    assert preview.status_code == 200, preview.text
    quality = preview.json()["dataQuality"]
    assert quality["nicknameFallbackRows"] == 1
    assert quality["missingDisplayNameRows"] == 0
    assert quality["unidentifiedRows"] == 2

    blocked = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "顾问待匹配.csv", "ownerId": "unassigned", "rows": [
            {"name": "", "wechatNickname": "微信小王", "sourceAdvisorLabel": "未来顾问甲", "accountStatus": "未开户"},
            {"name": "张三", "wechatNickname": "三三", "sourceAdvisorLabel": "未来顾问甲", "accountStatus": "提交中"},
            {"name": "李四", "wechatNickname": "四四", "phone": "13900000009", "sourceAdvisorLabel": "未来顾问甲", "accountStatus": "处理中"},
        ]},
    )
    assert blocked.status_code == 200, blocked.text
    assert len(blocked.json()["created"]) == 1
    assert len(blocked.json()["errors"]) == 2

    imported = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "顾问待匹配.csv", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [
            {"name": "", "wechatNickname": "微信小王", "sourceAdvisorLabel": "未来顾问甲", "accountStatus": "未开户"},
            {"name": "张三", "wechatNickname": "三三", "sourceAdvisorLabel": "未来顾问甲", "accountStatus": "提交中"},
        ]},
    )
    assert imported.status_code == 200, imported.text
    customer_id = imported.json()["created"][0]["id"]
    detail = client.get(f"/api/customers/{customer_id}", headers=admin_headers)
    assert detail.status_code == 200
    customer = detail.json()["customer"]
    assert customer["name"] == ""
    assert customer["wechat_nickname"] == "微信小王"
    assert customer["owner_id"] == "unassigned"
    assert customer["source_advisor_label"] == "未来顾问甲"
    assert customer["account_status"] == "未启动"

    filtered = client.get("/api/customers?search=微信 小王&ownerId=unassigned&contactState=wechat_only", headers=admin_headers)
    assert filtered.status_code == 200, filtered.text
    assert customer_id in {item["id"] for item in filtered.json()["items"]}
    missing = client.get("/api/customers?search=微信 小王&ownerId=unassigned&contactState=missing", headers=admin_headers)
    assert missing.status_code == 200, missing.text
    assert customer_id not in {item["id"] for item in missing.json()["items"]}


def test_potential_duplicate_query_is_postgres_ordering_compatible():
    class Cursor:
        def fetchall(self):
            return []

    class Connection:
        sql = ""
        params = ()

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return Cursor()

    conn = Connection()
    assert crm_main.potential_identity_matches(conn, "同名客户", "", "customer-id") == []
    assert "SELECT DISTINCT" not in conn.sql
    assert "ORDER BY c.updated_at DESC" in conn.sql


def test_tw_snapshot_updates_status_and_does_not_create_duplicates():
    admin_headers, _ = login("admin", "admin123")
    first = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "券商周报.xlsx", "mode": "snapshot", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [
            {"twCode": "tw20260828001", "name": "快照客户", "accountStatus": "未开户"},
        ]},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["created"]) == 1
    customer_id = first.json()["created"][0]["id"]
    assert first.json()["created"][0]["customerCode"] == "TW20260828001"

    second = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "券商周报-下一周.xlsx", "mode": "snapshot", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [
            {"twCode": "TW20260828001", "name": "快照客户", "accountStatus": "已开通"},
        ]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] == []
    assert len(second.json()["updated"]) == 1
    detail = client.get(f"/api/customers/{customer_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["customer"]["account_status"] == "已开户"
    assert detail.json()["customer"]["tw_code"] == "TW20260828001"
    listed = client.get("/api/customers?search=tw20260828001", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert customer_id in {item["id"] for item in listed.json()["items"]}

    third = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "券商周报-同一周.xlsx", "mode": "snapshot", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [
            {"twCode": "TW20260828001", "name": "快照客户", "accountStatus": "已开通"},
        ]},
    )
    assert third.status_code == 200, third.text
    assert third.json()["created"] == []
    assert third.json()["updated"] == []
    assert third.json()["unchangedCount"] == 1
    exported = client.get("/api/export/customers.csv", headers=admin_headers)
    assert exported.status_code == 200, exported.text
    export_text = exported.content.decode("utf-8-sig")
    assert "TW唯一编号（TW）" in export_text
    assert "TW20260828001" in export_text


def test_import_batches_record_deltas_and_support_drilldown_filters():
    admin_headers, _ = login("admin", "admin123")
    first = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "批次-第一周.xlsx", "mode": "snapshot", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [
            {"twCode": "TW20269999001", "name": "批次客户", "accountStatus": "未开户"},
        ]},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "批次-第二周.xlsx", "mode": "snapshot", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [
            {"twCode": "TW20269999001", "name": "批次客户", "accountStatus": "已开户"},
        ]},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["openedCount"] == 1
    job = next(item for item in client.get("/api/imports", headers=admin_headers).json()["items"] if item["id"] == second_body["jobId"])
    assert job["updatedCustomerIds"] == [first.json()["created"][0]["id"]]
    assert job["openedCustomerIds"] == job["updatedCustomerIds"]
    filtered = client.get(f"/api/customers?importJobId={second_body['jobId']}&importJobMode=opened", headers=admin_headers)
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1
    dashboard = client.get("/api/dashboard", headers=admin_headers)
    assert dashboard.status_code == 200, dashboard.text
    assert second_body["jobId"] in {job["id"] for job in dashboard.json()["importActivity"]["recentJobs"]}


def test_asset_import_updates_existing_tw_custom_field_and_merges_duplicate_rows():
    admin_headers, _ = login("admin", "admin123")
    field = client.post(
        "/api/customer-fields", headers=admin_headers,
        json={"label": "券商账户资产（USD）", "fieldType": "number", "options": []},
    )
    assert field.status_code == 201, field.text
    field_id = field.json()["field"]["id"]
    preview = client.post(
        "/api/imports/preview", headers=admin_headers,
        json={"filename": "SXY客户信息 20260827.csv", "dataBase64": base64.b64encode("客户编码,客戶姓名,客户权益资产（基币为USD，汇率@7.8）\nTW20260831001,资产客户,100".encode()).decode()},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["importProfile"] == "asset"
    assert preview.json()["suggestedMapping"]["twCode"] == "客户编码"
    assert preview.json()["suggestedCustomMapping"][field_id] == "客户权益资产（基币为USD，汇率@7.8）"
    master = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "主表.xlsx", "mode": "snapshot", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [
            {"twCode": "TW20260831001", "name": "资产客户", "accountStatus": "已开户"},
        ]},
    )
    assert master.status_code == 200, master.text
    asset = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "资产表.xlsx", "importProfile": "asset", "ownerId": "unassigned", "rows": [
            {"twCode": "TW20260831001", "name": "资产客户", "customValues": {field_id: "100.5"}},
            {"twCode": "TW20260831001", "name": "资产客户", "customValues": {field_id: "20"}},
            {"twCode": "#N/A", "name": "无法识别"},
        ]},
    )
    assert asset.status_code == 200, asset.text
    body = asset.json()
    assert body["profile"] == "asset"
    assert len(body["updated"]) == 1
    assert body["conflicts"] == []
    assert len(body["errors"]) == 1
    customer_id = master.json()["created"][0]["id"]
    detail = client.get(f"/api/customers/{customer_id}", headers=admin_headers).json()["customer"]
    assert detail["custom_values"][field_id] == "120.5"


def test_import_preview_converts_traditional_chinese_to_simplified():
    admin_headers, _ = login("admin", "admin123")
    payload = {
        "filename": "繁體客戶.csv",
        "dataBase64": base64.b64encode("客戶姓名,微信暱稱,備註\n張三,小王,已聯絡客戶".encode()).decode(),
    }
    preview = client.post("/api/imports/preview", headers=admin_headers, json=payload)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["textNormalization"] == "繁体中文已统一转换为简体中文"
    assert body["headers"][:3] == ["客户姓名", "微信昵称", "备注"]
    assert body["suggestedMapping"]["wechatNickname"] == "微信昵称"
    assert body["rows"][0]["客户姓名"] == "张三"
    assert body["rows"][0]["微信昵称"] == "小王"
    assert body["rows"][0]["备注"] == "已联络客户"


def test_hongan_advisor_is_external_relation_with_filter_edit_and_export():
    manager_headers, manager = login("manager", "manager123")
    second_headers, second = login("manager2", "manager2123")
    supervisor_headers, _ = login("supervisor", "supervisor123")
    admin_headers, _ = login("admin", "admin123")

    manager_create = client.post(
        "/api/customers", headers=manager_headers,
        json={"name": "商务经理越权港安顾问", "wechatNickname": "越权测试", "hkAdvisor": "港安顾问甲"},
    )
    assert manager_create.status_code == 403, manager_create.text
    created = client.post(
        "/api/customers", headers=supervisor_headers,
        json={"name": "外部引荐客户", "wechatNickname": "引荐微信", "hkAdvisor": "港安顾问甲", "ownerId": manager["id"]},
    )
    assert created.status_code == 201, created.text
    customer = created.json()["customer"]
    customer_id = customer["id"]
    assert customer["hongan_advisor"] == "港安顾问甲"
    customer = client.get(f"/api/customers/{customer_id}", headers=manager_headers).json()["customer"]

    meta = client.get("/api/meta", headers=manager_headers)
    assert meta.status_code == 200
    assert "港安顾问甲" in meta.json()["honganAdvisors"]
    filtered = client.get("/api/customers?honganAdvisor=港安顾问甲", headers=manager_headers)
    assert filtered.status_code == 200
    assert customer_id in {item["id"] for item in filtered.json()["items"]}

    denied = client.patch(
        f"/api/customers/{customer_id}", headers=manager_headers,
        json={"hkAdvisor": "港安顾问乙", "version": customer["version"]},
    )
    assert denied.status_code == 403

    missing_reason = client.patch(
        f"/api/customers/{customer_id}", headers=supervisor_headers,
        json={"hkAdvisor": "港安顾问乙", "version": customer["version"]},
    )
    assert missing_reason.status_code == 422

    changed = client.patch(
        f"/api/customers/{customer_id}", headers=supervisor_headers,
        json={"hkAdvisor": "港安顾问乙", "version": customer["version"], "changeReason": "测试关系调整"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["customer"]["hongan_advisor"] == "港安顾问乙"

    assigned = client.post(
        f"/api/customers/{customer_id}/assign", headers=supervisor_headers,
        json={"ownerId": second["id"], "reason": "参与定增后由另一位骄阳顾问服务"},
    )
    assert assigned.status_code == 200, assigned.text
    detail = client.get(f"/api/customers/{customer_id}", headers=second_headers)
    assert detail.status_code == 200
    assert detail.json()["customer"]["owner_id"] == second["id"]
    assert detail.json()["customer"]["hongan_advisor"] == "港安顾问乙"

    exported = client.get("/api/export/customers.csv", headers=admin_headers)
    assert exported.status_code == 200, exported.text
    export_text = exported.content.decode("utf-8-sig")
    assert "港安顾问（外部引荐）" in export_text
    assert "外部引荐客户" in export_text and "港安顾问乙" in export_text


def test_placement_batch_and_closed_loop_metrics():
    manager_headers, manager = login("manager", "manager123")
    supervisor_headers, _ = login("supervisor", "supervisor123")

    batch = client.post(
        "/api/batches", headers=supervisor_headers,
        json={"name": "测试九月定增批次", "closeDate": "2026-09-30", "status": "开放中", "targetAmount": 1000000},
    )
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["batch"]["id"]

    created = client.post(
        "/api/customers", headers=manager_headers,
        json={
            "name": "闭环客户", "phone": "13800000009", "stage": "批次推进",
            "accountStatus": "已开户", "accountBroker": "香港券商A",
            "intentStatus": "已锁定", "placementStatus": "资金到账",
            "targetBatchId": batch_id, "intentAmount": 200000, "fundedAmount": 200000,
        },
    )
    assert created.status_code == 201, created.text
    customer = created.json()["customer"]
    assert customer["target_batch_id"] == batch_id

    closed = client.patch(
        f"/api/customers/{customer['id']}", headers=manager_headers,
        json={"placementStatus": "已参与", "actualAmount": 200000, "stage": "已参与定增", "version": 1},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["customer"]["actual_amount"] == 200000

    dashboard = client.get("/api/dashboard", headers=manager_headers)
    assert dashboard.status_code == 200, dashboard.text
    summary = dashboard.json()["summary"]
    assert summary["accounts_opened"] >= 1
    assert summary["intended"] >= 1
    assert summary["batched"] >= 1
    assert summary["closed"] >= 1
    assert summary["actual_amount"] >= 200000
    dashboard_data = dashboard.json()
    assert len(dashboard_data["activityTrend"]) == 14
    assert {"due", "intent_unbatched", "missing_contact", "stalled"} <= set(dashboard_data["risks"])
    assert {"assigned", "contactable", "recently_updated", "duplicate_name_groups"} <= set(dashboard_data["quality"])
    assert isinstance(dashboard_data["teams"], list)

    metric_expectations = {
        "opened": lambda item: item["account_status"] == "已开户",
        "intent": lambda item: item["intent_status"] in {"有意向", "已锁定"},
        "batched": lambda item: item["target_batch_id"] is not None,
        "funded": lambda item: item["placement_status"] in {"资金到账", "已参与"},
        "closed": lambda item: item["placement_status"] == "已参与",
    }
    for metric, predicate in metric_expectations.items():
        filtered = client.get(f"/api/customers?metric={metric}", headers=manager_headers)
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] >= 1
        assert all(predicate(item) for item in filtered.json()["items"])

    batches = client.get("/api/batches", headers=manager_headers)
    assert batches.status_code == 200
    target = next(item for item in batches.json()["items"] if item["id"] == batch_id)
    assert target["closed_count"] == 1
    assert target["actual_amount"] == 200000


def test_xlsx_preview_ignores_broken_styles():
    admin_headers, _ = login("admin", "admin123")
    payload = {
        "filename": "样式异常客户表.xlsx",
        "dataBase64": base64.b64encode(xlsx_with_unreadable_styles()).decode(),
    }
    response = client.post("/api/imports/preview", headers=admin_headers, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["sheetName"] == "客户总表"
    assert result["suggestedMapping"] == {"name": "客户姓名", "phone": "手机号"}
    assert result["rows"] == [{"客户姓名": "样式异常客户", "手机号": "13900000000"}]


def test_custom_fields_permission_grid_values_and_safe_deactivation():
    manager_headers, manager = login("manager", "manager123")
    admin_headers, _ = login("admin", "admin123")

    denied = client.post(
        "/api/customer-fields", headers=manager_headers,
        json={"label": "护照状态", "fieldType": "select", "options": ["未办理", "已办理"]},
    )
    assert denied.status_code == 403

    permission = client.patch(
        f"/api/admin/users/{manager['id']}/permissions", headers=admin_headers,
        json={"canManageCustomerFields": True},
    )
    assert permission.status_code == 200, permission.text
    manager_headers, manager = login("manager", "manager123")
    assert manager["canManageCustomerFields"] is True

    created_field = client.post(
        "/api/customer-fields", headers=manager_headers,
        json={"label": "护照状态", "fieldType": "select", "options": ["未办理", "已办理", "已办理"]},
    )
    assert created_field.status_code == 201, created_field.text
    field_id = created_field.json()["field"]["id"]
    assert created_field.json()["field"]["options"] == ["未办理", "已办理"]

    customer = client.post(
        "/api/customers", headers=manager_headers,
        json={"name": "自定义字段客户", "phone": "13800000088", "customValues": {field_id: "已办理"}},
    )
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()["customer"]["id"]
    listing = client.get("/api/customers", headers=manager_headers).json()["items"]
    listed = next(item for item in listing if item["id"] == customer_id)
    assert listed["custom_values"][field_id] == "已办理"
    customer_version = client.get(f"/api/customers/{customer_id}", headers=manager_headers).json()["customer"]["version"]

    updated = client.put(
        f"/api/customers/{customer_id}/custom-fields/{field_id}", headers=manager_headers,
        json={"value": "未办理", "version": customer_version},
    )
    assert updated.status_code == 200, updated.text
    stale = client.put(
        f"/api/customers/{customer_id}/custom-fields/{field_id}", headers=manager_headers,
        json={"value": "已办理", "version": customer_version},
    )
    assert stale.status_code == 409
    invalid = client.put(
        f"/api/customers/{customer_id}/custom-fields/{field_id}", headers=manager_headers,
        json={"value": "随意值"},
    )
    assert invalid.status_code == 422

    disabled = client.patch(
        f"/api/customer-fields/{field_id}", headers=manager_headers, json={"active": False},
    )
    assert disabled.status_code == 200
    assert field_id not in {field["id"] for field in client.get("/api/meta", headers=manager_headers).json()["customerFields"]}
    all_fields = client.get("/api/customer-fields?includeInactive=true", headers=manager_headers).json()["items"]
    assert next(field for field in all_fields if field["id"] == field_id)["active"] is False
    detail = client.get(f"/api/customers/{customer_id}", headers=manager_headers).json()["customer"]
    assert detail["custom_values"][field_id] == "未办理"
    audit_items = client.get("/api/admin/audit", headers=admin_headers).json()["items"]
    field_audit = next(item for item in audit_items if item["action"] == "customer.custom_field_updated" and item["entity_id"] == customer_id)
    assert field_audit["detail"]["changes"] == {"from": "已办理", "to": "未办理"}


def test_import_template_and_safe_rollback():
    admin_headers, _ = login("admin", "admin123")
    template = client.get("/api/imports/template.csv", headers=admin_headers)
    assert template.status_code == 200, template.text
    assert "客户姓名" in template.content.decode("utf-8-sig")
    assert "微信昵称" in template.content.decode("utf-8-sig")

    imported = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "撤回测试.csv", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [{"name": "待撤回客户", "wechatNickname": "撤回微信"}]},
    )
    assert imported.status_code == 200, imported.text
    job_id = imported.json()["jobId"]
    customer_id = imported.json()["created"][0]["id"]
    jobs = client.get("/api/imports", headers=admin_headers)
    assert jobs.status_code == 200
    assert any(item["id"] == job_id for item in jobs.json()["items"])

    rolled_back = client.post(f"/api/imports/{job_id}/rollback", headers=admin_headers)
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["archived"] == [{"id": customer_id, "customerCode": imported.json()["created"][0]["customerCode"]}]

    protected_import = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "保护测试.csv", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [{"name": "保护客户"}]},
    )
    protected_id = protected_import.json()["created"][0]["id"]
    detail = client.get(f"/api/customers/{protected_id}", headers=admin_headers).json()["customer"]
    edited = client.patch(f"/api/customers/{protected_id}", headers=admin_headers, json={"notes": "后续人工编辑", "version": detail["version"]})
    assert edited.status_code == 200, edited.text
    protected_rollback = client.post(f"/api/imports/{protected_import.json()['jobId']}/rollback", headers=admin_headers)
    assert protected_rollback.status_code == 200
    assert protected_rollback.json()["archived"] == []
    assert protected_rollback.json()["protected"][0]["id"] == protected_id


def test_advisor_binding_rules_control_non_placement_defaults_and_manual_placement():
    manager_headers, manager = login("manager", "manager123")
    supervisor_headers, _ = login("supervisor", "supervisor123")
    admin_headers, _ = login("admin", "admin123")

    assert client.get("/api/advisor-bindings", headers=manager_headers).status_code == 403
    created = client.post(
        "/api/advisor-bindings", headers=supervisor_headers,
        json={
            "honganAdvisor": "绑定测试港安顾问", "jiaoyangAdvisor": manager["name"], "jiaoyangAdvisorId": manager["id"],
            "customerType": "non_placement", "assignmentMode": "default", "notes": "非定增固定组合",
        },
    )
    assert created.status_code == 201, created.text
    binding_id = created.json()["binding"]["id"]
    listed = client.get("/api/advisor-bindings", headers=supervisor_headers)
    assert listed.status_code == 200
    assert any(item["id"] == binding_id and item["jiaoyangAdvisorId"] == manager["id"] for item in listed.json()["items"])

    non_placement = client.post(
        "/api/customers", headers=admin_headers,
        json={"name": "默认绑定客户", "phone": "13800000101", "hkAdvisor": "绑定测试港安顾问"},
    )
    assert non_placement.status_code == 201, non_placement.text
    assert non_placement.json()["customer"]["owner_id"] == manager["id"]

    imported = client.post(
        "/api/imports/commit", headers=admin_headers,
        json={"filename": "绑定规则导入.csv", "ownerId": "unassigned", "allowUnidentifiedRows": True, "rows": [{"name": "默认绑定导入客户", "wechatNickname": "绑定微信", "hkAdvisor": "绑定测试港安顾问"}]},
    )
    assert imported.status_code == 200, imported.text
    imported_detail = client.get(f"/api/customers/{imported.json()['created'][0]['id']}", headers=admin_headers)
    assert imported_detail.json()["customer"]["owner_id"] == manager["id"]

    placement = client.post(
        "/api/customers", headers=admin_headers,
        json={"name": "定增手动客户", "phone": "13800000102", "hkAdvisor": "绑定测试港安顾问", "capitalDestination": "参与定增"},
    )
    assert placement.status_code == 201, placement.text
    assert placement.json()["customer"]["owner_id"] == "unassigned"

    manual = client.post(
        "/api/advisor-bindings", headers=supervisor_headers,
        json={"honganAdvisor": "绑定测试港安顾问", "jiaoyangAdvisor": manager["name"], "jiaoyangAdvisorId": manager["id"], "customerType": "placement", "assignmentMode": "manual"},
    )
    assert manual.status_code == 201, manual.text
    missing_reason = client.patch(f"/api/advisor-bindings/{binding_id}", headers=supervisor_headers, json={"active": True})
    assert missing_reason.status_code == 422
    updated = client.patch(
        f"/api/advisor-bindings/{binding_id}", headers=supervisor_headers,
        json={"active": False, "notes": "已停用", "changeReason": "测试停用"},
    )
    assert updated.status_code == 200
    assert updated.json()["binding"]["active"] is False


def test_crm_permissions_can_extend_a_manager_without_changing_muskzoom_role_or_team():
    manager_headers, manager = login("manager", "manager123")
    second_headers, second = login("manager2", "manager2123")
    admin_headers, _ = login("admin", "admin123")

    managed_customer = client.post(
        "/api/customers", headers=admin_headers,
        json={"name": "CRM 全量维护测试客户", "phone": "13800000901", "ownerId": second["id"]},
    )
    assert managed_customer.status_code == 201, managed_customer.text
    customer_id = managed_customer.json()["customer"]["id"]
    assert client.get(f"/api/customers/{customer_id}", headers=manager_headers).status_code == 404

    granted = client.patch(
        f"/api/admin/users/{manager['id']}/permissions", headers=admin_headers,
        json={
            "crmScopeMode": "all",
            "canImportCustomers": True,
            "canManageAssignments": True,
            "canManageAdvisorBindings": True,
            "canManageCustomerFields": True,
            "canExportAll": True,
            "canManageCrmPermissions": True,
        },
    )
    assert granted.status_code == 200, granted.text
    granted_user = granted.json()["user"]
    assert granted_user["rolePermission"] == "manager"
    assert granted_user["team"] == manager["team"]
    assert granted_user["customerScope"] == "all"
    assert granted_user["crmScopeMode"] == "all"
    assert all(granted_user[key] for key in (
        "canImportCustomers", "canManageAssignments", "canManageAdvisorBindings",
        "canManageCustomerFields", "canExportAll", "canManageCrmPermissions",
    ))

    refreshed = client.get("/api/session", headers=manager_headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["customerScope"] == "all"
    assert client.get(f"/api/customers/{customer_id}", headers=manager_headers).status_code == 200
    assert client.get("/api/admin/users", headers=manager_headers).status_code == 200
    assert client.get("/api/customer-assignments", headers=manager_headers).status_code == 200

    reassigned = client.post(
        f"/api/customers/{customer_id}/assign", headers=manager_headers,
        json={"ownerId": manager["id"], "reason": "CRM 数据维护负责人临时接管"},
    )
    assert reassigned.status_code == 200, reassigned.text
    assert client.get(f"/api/customers/{customer_id}", headers=second_headers).status_code == 404

    reset = client.patch(
        f"/api/admin/users/{manager['id']}/permissions", headers=manager_headers,
        json={"crmScopeMode": "inherit"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["user"]["crmScopeMode"] == "inherit"
    assert reset.json()["user"]["customerScope"] == "self"
