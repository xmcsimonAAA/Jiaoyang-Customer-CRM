"""Versioned imports and scoped queries for the independent priority-inferior board.

Each dataset is a source type + business date. Published revisions are immutable;
only its head changes. Raw files and preview payloads stay server-side. No writes
are made to placement fields, client ownership, or the old import pipeline.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
from decimal import Decimal
from typing import Any, Callable
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from backend.priority_assignment import SCHEMA as BINDING_SCHEMA, resolve as resolve_assignments, decorate
from backend.priority_import import FIELDS, iso, normalized, number, parse, sum_values, text, tw

from backend.priority_identity import reconcile, excluded_codes
from backend.workspace import SCHEMA as WORKSPACE_SCHEMA, install as install_workspace

SCHEMA = [
    *WORKSPACE_SCHEMA,
    BINDING_SCHEMA,
    "CREATE TABLE IF NOT EXISTS priority_write_lock (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)",
    "INSERT INTO priority_write_lock VALUES (1,0) ON CONFLICT(id) DO NOTHING",
    """CREATE TABLE IF NOT EXISTS priority_datasets (
        dataset_key TEXT PRIMARY KEY, kind TEXT NOT NULL, business_date TEXT NOT NULL,
        head_id TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS priority_revisions (
        id TEXT PRIMARY KEY, dataset_key TEXT NOT NULL REFERENCES priority_datasets(dataset_key),
        parent_id TEXT NOT NULL, job_id TEXT NOT NULL, rows_json TEXT NOT NULL,
        filename TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
        created_by TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS priority_uploads (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL,
        sources_json TEXT NOT NULL, summary_json TEXT NOT NULL,
        created_at TEXT NOT NULL, created_by TEXT NOT NULL)""",
    'CREATE INDEX IF NOT EXISTS idx_priority_revision_dataset ON priority_revisions(dataset_key, created_at)',
]
MATCHING_VERSION = 3

LABELS = {'master': '客户身份与券商开户', 'assets': '客户资产（USD）', 'secondary': 'XMax 二级持仓（股）', 'icc': '港安 ICC 批次'}


class BindingChange(BaseModel):
    broker: str = Field(min_length=1,max_length=100)
    ownerId: str
    active: bool = True
    reason: str = Field(min_length=1,max_length=1000)
    token: str = ''


class Source(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    contentBase64: str = Field(max_length=24_000_000)


class Upload(BaseModel):
    asOf: str
    batchDates: list[str] = Field(default_factory=lambda: ['2026-09-09'], max_length=30)
    files: list[Source] = Field(min_length=1, max_length=12)


class IdentitySelection(BaseModel):
    key: str
    recordKey: str
    twCode: str


class CommitSelections(BaseModel):
    identities: list[IdentitySelection] = Field(default_factory=list, max_length=500)


class Edit(BaseModel):
    expectedRevision: str
    changes: dict[str, Any]
    reason: str = Field(min_length=1, max_length=1000)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def heads(conn):
    result = {}
    for row in conn.execute('''SELECT d.*,r.rows_json,r.filename,r.created_at FROM priority_datasets d
                              LEFT JOIN priority_revisions r ON r.id=d.head_id''').fetchall():
        r = dict(row)
        r['rows'] = json.loads(r.pop('rows_json') or '[]')
        result[r['dataset_key']] = r
    return result


def registry(all_heads):
    result = {}
    for dataset in sorted(all_heads.values(), key=lambda d: d['business_date']):
        if dataset['kind'] == 'master':
            for r in dataset['rows']:
                result[r['twCode']] = r
    return result


def match_records(rows, identities, batch_date):
    by_name = {}
    for code, r in identities.items():
        by_name.setdefault(normalized(r['customerName']), []).append(code)
    for r in rows:
        code = r.get('twCode')
        trusted = False
        if r.get('identityDetached'):
            candidates = by_name.get(normalized(r['customerName']), [])
        elif code:
            if code not in identities:
                raise HTTPException(422, f'{batch_date} 的 TW {code} 不在客户名单中，请先补充客户名单。')
            candidates = [code]
            trusted = True
        elif normalized(r['customerName']) == normalized('董芳') and batch_date == '2026-09-09' and 'TW202609039' in identities and normalized(identities['TW202609039']['customerName']) == normalized('董方'):
            candidates = ['TW202609039']
            r['matchReason'] = '用户已确认：2026-09-09 董芳即董方'
            trusted = True
        else:
            candidates = by_name.get(normalized(r['customerName']), [])
        r['candidates'] = candidates
        if trusted:
            r['twCode'] = candidates[0]
            r['canonicalName'] = identities[candidates[0]]['customerName']
            r['matchStatus'] = 'matched'
            r['matchReason'] = r.get('matchReason') or '按明确 TW 编号关联'
        else:
            r['twCode'] = ''
            r['canonicalName'] = ''
            r['matchStatus'] = 'ambiguous' if candidates else 'unmatched'
            r['matchReason'] = r.get('matchReason') if r.get('identityDetached') else '姓名仅作候选，须核对 TW 后确认'
    unresolved_names = [normalized(r['customerName']) for r in rows if not r.get('twCode')]
    if len(unresolved_names) != len(set(unresolved_names)):
        raise HTTPException(422, f'{batch_date} 存在多条未确认身份的同名记录，请在表格添加 TW 编号区分后重传。')
    codes = [r['twCode'] for r in rows if r.get('twCode')]
    if len(codes) != len(set(codes)):
        raise HTTPException(422, f'{batch_date} 同一 TW 出现多条活动记录，请核对后重传。')


def detached_identity(row, reason):
    """Unlink only the activity, keeping its stable record key and business history."""
    blocked = excluded_codes(row) | ({row['twCode']} if row.get('twCode') else set())
    return dict(twCode='', canonicalName='', matchStatus='unmatched', candidates=[], excludedTwCodes=sorted(blocked),
                matchReason=reason, identityDetached=row.get('twCode') or row.get('identityDetached') or 'manual')


def identity_key(r):
    return r.get('twCode') or 'name:' + normalized(r.get('customerName'))


def prepare_dataset(dataset, previous):
    """ICC blank cells are non-destructive; snapshots replace a complete dated source."""
    old = previous.get('rows', [])
    by_id = {identity_key(r): r for r in old}
    # Also align a newly resolved identity with its previous unresolved source name.
    name_groups = {}
    for row in old:
        name_groups.setdefault(normalized(row['customerName']), []).append(row)
    by_name = {name: group[0] for name, group in name_groups.items() if len(group) == 1}
    seen = set()
    changes = []
    for r in dataset['rows']:
        before = by_id.get(identity_key(r))
        candidate = by_name.get(normalized(r['customerName']))
        if not before and candidate and (not r.get('twCode') or not candidate.get('twCode')):
            before = candidate
        if before and before['recordKey'] in seen:
            raise HTTPException(422, '多条记录指向同一历史身份，请核对 TW 后重传。')
        if before:
            seen.add(before['recordKey'])
            r['recordKey'] = before['recordKey']
            if before.get('excludedTwCodes'):
                r['excludedTwCodes'] = before['excludedTwCodes']
            if 'participationIntent' in before:
                r['participationIntent'] = before['participationIntent']
            if dataset['kind'] in {'icc', 'master'}:
                carried = {}
                for field in FIELDS:
                    if r.get(field) in (None, '') and before.get(field) not in (None, ''):
                        r[field] = before[field]
                        carried[field] = before.get('carriedFields', {}).get(field) or {'revision': previous['head_id'], 'row': before.get('sourceRow')}
                if carried:
                    r['carriedFields'] = carried
            fields = [f for f in set(FIELDS + ['assetUsd', 'quantity', 'twCode']) if r.get(f) != before.get(f)]
            if fields:
                changes.append({'name': r['customerName'], 'twCode': r.get('twCode'), 'fields': fields,
                                'before': {f: before.get(f) for f in fields}, 'after': {f: r.get(f) for f in fields}})
        else:
            if dataset['kind'] == 'icc':
                r['recordKey'] = 'record:' + str(uuid4())
            changes.append({'name': r['customerName'], 'twCode': r.get('twCode'), 'fields': ['新增批次记录' if dataset['kind'] == 'icc' else '新增记录']})
    missing = [r for r in old if r['recordKey'] not in seen]
    # ICC omissions are not cancellations. Keep the dated client record and provenance.
    if dataset['kind'] == 'icc':
        for row in missing:
            retained = copy.deepcopy(row)
            retained['retainedFromRevision'] = previous.get('head_id')
            dataset['rows'].append(retained)
    dataset['changes'] = changes
    dataset['missingCount'] = len(missing)
    dataset['parent'] = previous.get('head_id', '')
    return dataset


def install(app: Any, db: Callable, current_user: Callable, access_clause: Callable, audit: Callable,
            now_iso: Callable, platform_users: Callable, sync_customers: Callable) -> None:
    with db() as conn:
        for sql in SCHEMA:
            conn.execute(sql)
        conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
        sync_customers(conn, heads(conn), {'id':'system-shared-registry','name':'共用客户主档迁移',
                      'customerScope':'all','canManageAssignments':True,'canManageAdvisorBindings':True})

    def shared_registry(conn, datasets):
        from backend.shared_customers import identities, archived_codes
        # Keep existing CRM names when TW already exists; sources remain available in history.
        source=registry(datasets)
        result={**source, **identities(conn)}
        for code, row in source.items():
            if row.get('brokerAccountStatus'):
                result[code]['brokerAccountStatus']=row['brokerAccountStatus']
        blocked=archived_codes(conn)
        return {k:v for k,v in result.items() if k not in blocked}

    def require_import(user):
        if not user.get('canImportCustomers') or user.get('customerScope') != 'all':
            raise HTTPException(403, '整份名单上传与修订需要全量客户范围及导入权限。')

    def require_edit(user):
        require_import(user)
        if not user.get('canManageAdvisorBindings'):
            raise HTTPException(403, '编辑批次业务资料需要顾问绑定管理权限。')

    def links(conn, user):
        clause, params = access_clause(user)
        rows = conn.execute(f'''SELECT c.id,c.customer_code,c.name,c.owner_name,
                          i.normalized_value tw_code FROM customers c
                          LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'
                          WHERE c.archived_at IS NULL AND {clause}''', params).fetchall()
        result = {}
        for r in rows:
            code = r['tw_code'] or r['customer_code']
            if code:
                result[code] = dict(r)
        return result

    def assignment_data(conn, all_heads, proposed=None):
        rules = {r['broker_key']: dict(r) for r in conn.execute('SELECT * FROM priority_broker_bindings').fetchall()}
        if proposed:
            rules[proposed['broker_key']] = proposed
        aliases = [dict(r) for r in conn.execute('SELECT alias,user_id FROM advisor_alias_mappings').fetchall()]
        return resolve_assignments(all_heads, rules, platform_users(), aliases)

    def scope_rows(conn, all_heads, user):
        visible = links(conn, user)
        result = copy.deepcopy(all_heads)
        from backend.shared_customers import archived_codes
        blocked=archived_codes(conn)
        for d in result.values():
            d['rows']=[r for r in d['rows'] if r.get('twCode') not in blocked]
        assignments = assignment_data(conn, all_heads)
        decorate(result, assignments)
        if user['customerScope'] == 'all':
            return result, visible
        def allowed(a):
            return bool(a['serviceOwnerId']) and (a['serviceOwnerId'] == user['id'] or
                (user['customerScope'] == 'team' and a['serviceOwnerTeam'] == user.get('team')))
        # Product ownership is independent of placement/CRM ownership.
        codes = {key for key, a in assignments.items() if allowed(a)}
        for d in result.values():
            d['rows'] = [r for r in d['rows'] if (r.get('twCode') or d['dataset_key']+'/'+r['recordKey']) in codes]
        return result, visible

    def require_assignment(user):
        if user.get('customerScope') != 'all' or not user.get('canManageAdvisorBindings') or not user.get('canManageAssignments'):
            raise HTTPException(403, '维护默认服务绑定需要全量范围、顾问绑定和客户归属权限。')

    @app.get('/api/priority-inferior/bindings')
    def bindings(user=Depends(current_user)):
        require_assignment(user)
        with db() as conn:
            h = heads(conn)
            rules = [dict(r) for r in conn.execute('SELECT * FROM priority_broker_bindings ORDER BY broker_name').fetchall()]
            assignments = assignment_data(conn, h)
        return {'rules': rules, 'assignments': list(assignments.values()),
                'owners': [dict(id=p['id'], name=p['name'], team=p.get('team','')) for p in platform_users()
                           if p.get('active') and p.get('rolePermission') in {'manager','supervisor'}]}

    @app.post('/api/priority-inferior/bindings/preview')
    def binding_preview(payload: BindingChange, user=Depends(current_user)):
        require_assignment(user)
        with db() as conn:
            return binding_impact(conn, payload)

    def binding_impact(conn, payload):
        if not payload.broker.strip() or not payload.reason.strip():
            raise HTTPException(422, '保险经纪人和变更原因不能为空。')
        people = platform_users()
        if not any(p['id']==payload.ownerId and p.get('active') and p.get('rolePermission') in {'manager','supervisor'} for p in people):
            raise HTTPException(422, '请选择有效商务经理或主管账号。')
        h = heads(conn)
        rule = dict(broker_key=normalized(payload.broker), broker_name=payload.broker.strip(), owner_id=payload.ownerId, active=int(payload.active))
        before, after = assignment_data(conn,h), assignment_data(conn,h,rule)
        affected = [dict(a, beforeOwner=before[k]['serviceOwner'], changed=a!=before[k]) for k,a in after.items()
                    if normalized(a['insuranceBroker'])==rule['broker_key']]
        return {'rule':rule, 'affected':affected, 'token':digest({'heads':h,'before':before,'after':after,'rule':rule})}

    @app.post('/api/priority-inferior/bindings/save')
    def binding_save(payload: BindingChange, user=Depends(current_user)):
        require_assignment(user)
        with db() as conn:
            conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
            impact = binding_impact(conn,payload)
            if payload.token != impact['token']:
                raise HTTPException(409, '客户或绑定已变化，请重新预览。')
            r=impact['rule']
            conn.execute('INSERT INTO priority_broker_bindings VALUES (?,?,?,?,?,?) ON CONFLICT(broker_key) DO UPDATE SET broker_name=excluded.broker_name,owner_id=excluded.owner_id,active=excluded.active,updated_at=excluded.updated_at,updated_by=excluded.updated_by',
                         (r['broker_key'],r['broker_name'],r['owner_id'],r['active'],now_iso(),user['id']))
            audit(conn,user,'priority.binding','priority_broker',r['broker_key'],{'reason':payload.reason,'impact':impact['affected'],'rule':r})
        return {'ok':True,'changed':sum(a['changed'] for a in impact['affected'])}

    def summary(dataset):
        rows = dataset['rows']
        participating = [r for r in rows if r.get('agreementAmountUsd') is not None and Decimal(r['agreementAmountUsd']) > 0]
        return dict(key=dataset.get('key') or dataset.get('dataset_key'), kind=dataset['kind'], label=LABELS[dataset['kind']],
                    date=dataset.get('date') or dataset.get('business_date'), records=len(rows),
                    participants=len(participating), agreementUsd=sum_values(r['agreementAmountUsd'] for r in participating),
                    assetUsd=sum_values(r.get('assetUsd') for r in rows), quantity=sum_values(r.get('quantity') for r in rows),
                    automaticIdentity=dataset.get('automaticIdentity', False),
                    pending=sum(1 for r in rows if dataset['kind'] == 'icc' and not r.get('twCode')),
                    missingCount=dataset.get('missingCount', 0), duplicate=dataset.get('duplicate', False),
                    changes=dataset.get('changes', []))

    @app.post('/api/priority-inferior/imports/preview')
    def preview(payload: Upload, user=Depends(current_user)):
        require_edit(user)
        as_of = iso(payload.asOf)
        dates = [iso(d) for d in payload.batchDates]
        if sum(len(f.contentBase64) for f in payload.files) > 60_000_000:
            raise HTTPException(422, '一次上传请控制在 45 MB 以内。')
        sources, datasets = [], []
        for source in payload.files:
            if not source.filename.lower().endswith('.xlsx'):
                raise HTTPException(422, '请上传 .xlsx 文件。')
            try:
                content = base64.b64decode(source.contentBase64, validate=True)
            except ValueError as exc:
                raise HTTPException(422, '文件编码无效。') from exc
            source_hash = hashlib.sha256(content).hexdigest()
            sources.append(dict(filename=source.filename, sha256=source_hash, contentBase64=source.contentBase64))
            for d in parse(content, source.filename, as_of, dates):
                d.update(key=f"{d['kind']}:{d['date']}", filename=source.filename, sourceHash=source_hash)
                d['fingerprint'] = digest({'kind': d['kind'], 'date': d['date'], 'rows': d['rows']})
                datasets.append(d)
        if len({d['key'] for d in datasets}) != len(datasets):
            raise HTTPException(422, '同一日期同一来源只保留一份文件；修订表请单独上传。')
        with db() as conn:
            current = heads(conn)
            identities = shared_registry(conn, current)
            for d in datasets:
                if d['kind'] == 'master':
                    identities.update({r['twCode']: r for r in d['rows']})
            for d in datasets:
                before = current.get(d['key'], {})
                if d['kind'] == 'icc':
                    groups = {}
                    for old in before.get('rows', []):
                        groups.setdefault(normalized(old['customerName']), []).append(old)
                    confirmed = {name: group[0] for name, group in groups.items() if len(group) == 1 and
                                 (group[0].get('identityDetached') or
                                  (group[0].get('twCode') and group[0].get('matchReason') != '客户名单中的唯一同名记录'))}
                    for r in d['rows']:
                        prior = confirmed.get(normalized(r['customerName']))
                        if prior and r.get('twCode') and r['twCode'] in excluded_codes(prior):
                            r['twCode'] = prior.get('twCode', '')
                        if prior and prior.get('twCode') and r.get('twCode') and prior['twCode'] != r['twCode']:
                            raise HTTPException(422, '本批次该姓名已有其他 TW，需先在匹配身份中修订，避免覆盖已确认客户。')
                        if prior and (not r.get('twCode') or prior.get('identityDetached')):
                            r['identityDetached'] = prior.get('identityDetached', '')
                            r['twCode'] = prior.get('twCode', '')
                            r['matchReason'] = prior.get('matchReason', '沿用本批次已确认身份')
                    match_records(d['rows'], identities, d['date'])
                previous_hash = None
                ancestor = before.get('head_id')
                while ancestor:
                    revision = conn.execute('SELECT fingerprint,parent_id FROM priority_revisions WHERE id=?', (ancestor,)).fetchone()
                    previous_hash = revision['fingerprint']
                    if not previous_hash.startswith('manual:'):
                        break
                    ancestor = revision['parent_id']
                d['duplicate'] = previous_hash == d['fingerprint']
                prepare_dataset(d, before)
                if d['duplicate']:
                    d['rows'] = copy.deepcopy(before['rows'])
                    d['changes'] = []
            proposed=copy.deepcopy(current)
            for d in datasets:
                proposed[d['key']]={**d,'business_date':d['date']}
            shared_preview=sync_customers(conn,proposed,user,dry_run=True)
            # A roster upload also repairs pending ICC identities, in the SAME reversible job.
            blocked = {c['twCode'] for c in shared_preview['conflicts']}
            identities = shared_registry(conn, proposed)
            auto_matched = 0
            uploaded = {d['key']: d for d in datasets}
            for key, activity in list(proposed.items()):
                if activity['kind'] != 'icc':
                    continue
                before_rows = copy.deepcopy(activity['rows'])
                auto_matched += reconcile(activity['rows'], activity['business_date'], proposed, identities, blocked)
                if activity['rows'] == before_rows:
                    continue
                dataset = uploaded.get(key)
                if dataset is None:
                    dataset = dict(key=key, kind='icc', date=activity['business_date'], filename=activity['filename'],
                                   rows=activity['rows'], parent=activity['head_id'], duplicate=False,
                                   fingerprint='manual:auto:'+digest(activity['rows']), changes=[], missingCount=0,
                                   automaticIdentity=True)
                    datasets.append(dataset)
                else:
                    was_duplicate = dataset['duplicate']
                    dataset['duplicate'] = False
                    dataset['rows'] = activity['rows']
                    if was_duplicate:
                        dataset['fingerprint'] = 'manual:auto:'+digest(activity['rows'])
                for before_row, row in zip(before_rows, activity['rows']):
                    if before_row.get('twCode') != row.get('twCode'):
                        dataset['changes'].append(dict(name=row['customerName'], twCode=row.get('twCode'),
                            fields=['twCode'], before={'twCode':before_row.get('twCode')},
                            after={'twCode':row.get('twCode')}, reason=row['matchReason']))
            pending_rows = [(d,r) for d in proposed.values() if d['kind']=='icc' for r in d['rows'] if not r.get('twCode')]
            identity_summary = dict(autoMatched=auto_matched, pending=len(pending_rows),
                waiting=sum(not r.get('candidates') or '等待' in r.get('matchReason','') for _,r in pending_rows),
                issues=[dict(key=d.get('dataset_key') or d.get('key'), name=r['customerName'],
                    recordKey=r['recordKey'], sourceRow=r['sourceRow'], reason=r.get('matchReason','身份待确认'),
                    candidates=r.get('candidates',[])) for d,r in pending_rows])
            result = [summary(d) for d in datasets]
            job = str(uuid4())
            conn.execute('INSERT INTO priority_uploads VALUES (?,?,?,?,?,?,?)',
                         (job, 'preview', dumps({'matchingVersion': MATCHING_VERSION, 'identityReconciliation': identity_summary, 'datasets': datasets, 'baseHeads': {k: v['head_id'] for k, v in current.items()}}), dumps(sources), dumps(result), now_iso(), user['id']))
        return {'id': job, 'datasets': result, 'identityReconciliation':identity_summary, 'sharedCustomers':shared_preview, 'issues': [dict(key=d['key'], recordKey=r['recordKey'], name=r['customerName'], sourceRow=r['sourceRow'], status=r['matchStatus'], candidates=r['candidates'])
                for d in datasets if d['kind'] == 'icc' and not d['duplicate'] for r in d['rows'] if not r.get('twCode')]}

    @app.post('/api/priority-inferior/imports/{job_id}/commit')
    def commit(job_id: str, selections: CommitSelections | None = None, user=Depends(current_user)):
        require_edit(user)
        try:
            with db() as conn:
                # Serialize all publications, edits and rollbacks in both database engines.
                conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
                # Serialize publish against duplicate clicks and concurrent approval.
                conn.execute("UPDATE priority_uploads SET status=status WHERE id=?", (job_id,))
                job = conn.execute('SELECT * FROM priority_uploads WHERE id=?', (job_id,)).fetchone()
                if not job or job['created_by'] != user['id']:
                    raise HTTPException(404, '找不到本人的上传预览。')
                if job['status'] == 'committed':
                    return {'id': job_id, 'status': 'committed', 'alreadyCommitted': True}
                if job['status'] != 'preview':
                    raise HTTPException(409, '本次上传已撤销，请重新预览。')
                payload = json.loads(job['payload_json'])
                if payload.get('matchingVersion') != MATCHING_VERSION:
                    raise HTTPException(409, '身份匹配规则已更新，请重新上传生成预览。')
                current = heads(conn)
                if {k: v['head_id'] for k, v in current.items()} != payload['baseHeads']:
                    raise HTTPException(409, '数据已被其他操作更新，请重新生成预览后保存。')
                chosen = selections.identities if selections else []
                issues = {(i['key'], i.get('recordKey')):i for i in payload.get('identityReconciliation',{}).get('issues',[])}
                datasets = {d['key']:d for d in payload['datasets']}
                original_keys = set(datasets)
                proposed = copy.deepcopy(current)
                for key, d in datasets.items():
                    proposed[key] = {**d, 'business_date':d['date']}
                identities = shared_registry(conn, proposed)
                blocked = {c['twCode'] for c in sync_customers(conn, proposed, user, dry_run=True)['conflicts']}
                seen = set()
                for selection in chosen:
                    anchor = (selection.key, selection.recordKey)
                    issue = issues.get(anchor)
                    code = tw(selection.twCode)
                    if anchor in seen or not issue or code not in issue.get('candidates',[]) or code not in identities or code in blocked:
                        raise HTTPException(422, '身份选择无效或编号存在冲突，请重新核对预览候选。')
                    seen.add(anchor)
                    d = datasets.get(selection.key)
                    if d is None:
                        old = current.get(selection.key)
                        if not old or old['kind'] != 'icc':
                            raise HTTPException(422, '所选批次不存在。')
                        d = dict(key=selection.key,kind='icc',date=old['business_date'],filename=old['filename'],
                                 parent=old['head_id'],rows=copy.deepcopy(old['rows']),changes=[],duplicate=False)
                        payload['datasets'].append(d)
                        datasets[selection.key]=d
                    row = next((r for r in d['rows'] if r['recordKey']==selection.recordKey),None)
                    if not row or row.get('twCode') or code in excluded_codes(row):
                        raise HTTPException(409, '该记录已关联或已排除所选编号，请刷新预览。')
                    if any(r.get('twCode')==code for r in d['rows']):
                        raise HTTPException(409, '该 TW 已在本批次关联其他记录，请核对重复选择。')
                    row.update(twCode=code,canonicalName=identities[code]['customerName'],matchStatus='matched',
                               matchReason='上传预览中人工选择候选 TW',identityMatchMethod='manual',identityDetached='',candidates=[code])
                    row['manualCorrection']=dict(reason=row['matchReason'],by=user['name'],at=now_iso(),fields=['twCode'])
                    was_duplicate = d['duplicate']
                    d['duplicate']=False
                    # Preserve the source fingerprint on new uploads; unchanged historical sources use manual revisions.
                    if selection.key not in original_keys or was_duplicate or d.get('fingerprint','').startswith('manual:'):
                        d['fingerprint']='manual:identity:'+str(uuid4())
                    d['changes'].append(dict(name=row['customerName'],twCode=code,fields=['twCode'],before={'twCode':''},after={'twCode':code}))
                if chosen:
                    conn.execute('UPDATE priority_uploads SET summary_json=? WHERE id=?',(dumps([summary(d) for d in payload['datasets']]),job_id))
                for d in payload['datasets']:
                    if d['duplicate']:
                        continue
                    key = d['key']
                    conn.execute("INSERT INTO priority_datasets VALUES (?,?,?,'') ON CONFLICT(dataset_key) DO NOTHING", (key, d['kind'], d['date']))
                    rev = str(uuid4())
                    conn.execute('INSERT INTO priority_revisions VALUES (?,?,?,?,?,?,?,?,?)', (rev, key, d['parent'], job_id, dumps(d['rows']), d['filename'], d['fingerprint'], now_iso(), user['id']))
                    conn.execute('UPDATE priority_datasets SET head_id=? WHERE dataset_key=?', (rev, key))
                shared_result = sync_customers(conn, heads(conn), user)
                conn.execute("UPDATE priority_uploads SET status='committed' WHERE id=?", (job_id,))
                audit(conn, user, 'priority.import', 'priority_upload', job_id, {'datasets': [summary(d) for d in payload['datasets']], 'identityReconciliation':payload.get('identityReconciliation'), 'identitySelections':[s.model_dump() for s in chosen]})
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, '另一项上传刚刚更新了同一份资料，请重新预览。') from exc
        return {'id': job_id, 'status': 'committed', 'sharedCustomers':shared_result}

    @app.get('/api/priority-inferior/imports')
    def imports(user=Depends(current_user)):
        require_import(user)
        with db() as conn:
            rows = conn.execute("SELECT id,status,summary_json,created_at FROM priority_uploads WHERE status!='preview' ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(id=r['id'], status=r['status'], createdAt=r['created_at'], datasets=json.loads(r['summary_json'])) for r in rows]

    @app.post('/api/priority-inferior/imports/{job_id}/rollback')
    def rollback(job_id: str, user=Depends(current_user)):
        require_edit(user)
        with db() as conn:
            conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
            conn.execute('UPDATE priority_uploads SET status=status WHERE id=?', (job_id,))
            job = conn.execute('SELECT status FROM priority_uploads WHERE id=?', (job_id,)).fetchone()
            if not job or job['status'] != 'committed':
                raise HTTPException(409, '该上传不可撤销。')
            revisions = conn.execute('SELECT * FROM priority_revisions WHERE job_id=?', (job_id,)).fetchall()
            for r in revisions:
                # Lock the same heads as imports/edits before checking references.
                conn.execute('UPDATE priority_datasets SET head_id=head_id WHERE dataset_key=?', (r['dataset_key'],))
                head = conn.execute('SELECT head_id FROM priority_datasets WHERE dataset_key=?', (r['dataset_key'],)).fetchone()
                if head['head_id'] != r['id']:
                    raise HTTPException(409, '这份资料已有后续修订，请先撤销后续修订。')
            # A rollback must not orphan a customer or activity with followups.
            anchors = {r['subject_key'] for r in conn.execute('SELECT subject_key FROM workspace_followups').fetchall()}
            before_heads = heads(conn)
            after_heads = copy.deepcopy(before_heads)
            for r in revisions:
                parent = conn.execute('SELECT rows_json FROM priority_revisions WHERE id=?',(r['parent_id'],)).fetchone()
                after_heads[r['dataset_key']]['rows'] = json.loads(parent['rows_json']) if parent else []
            def subject_keys(datasets):
                keys = set()
                for d in datasets.values():
                    for row in d['rows']:
                        if row.get('twCode'):
                            keys.add('tw:' + row['twCode'])
                        if d['kind'] == 'icc':
                            keys.add(d['dataset_key'] + '/' + row['recordKey'])
                # Existing CRM identities remain reachable after source rollback.
                keys.update('tw:' + code for code in links(conn,user))
                return keys
            if anchors & (subject_keys(before_heads) - subject_keys(after_heads)):
                raise HTTPException(409, '资料已有客户跟进，撤销会使跟进失去关联；请修订资料而不是撤销整批。')
            for r in revisions:
                conn.execute('UPDATE priority_datasets SET head_id=? WHERE dataset_key=?', (r['parent_id'], r['dataset_key']))
            conn.execute("UPDATE priority_uploads SET status='rolled_back' WHERE id=?", (job_id,))
            audit(conn, user, 'priority.rollback', 'priority_upload', job_id, {})
        return {'status': 'rolled_back'}

    def scoped(conn, user):
        return scope_rows(conn, heads(conn), user)

    @app.get('/api/priority-inferior/overview')
    def overview(asOf: str = '', user=Depends(current_user)):
        with db() as conn:
            all_heads, visible = scoped(conn, user)
            if asOf:
                cutoff = iso(asOf)
                all_heads = {k: v for k, v in all_heads.items() if v['business_date'] <= cutoff}
            identities = shared_registry(conn, all_heads)
            if user['customerScope'] != 'all':
                permitted=set(visible)|{r.get('twCode') for d in all_heads.values() for r in d['rows']}
                identities={k:v for k,v in identities.items() if k in permitted}
            rows_by_kind = {}
            date_by_kind = {}
            for kind in ('assets', 'secondary', 'master'):
                sources = [v for v in all_heads.values() if v['kind'] == kind and v['head_id']]
                latest = max(sources, key=lambda d: d['business_date']) if sources else None
                rows_by_kind[kind] = {r['twCode']: r for r in latest['rows']} if latest else {}
                date_by_kind[kind] = latest['business_date'] if latest else None
            activity = [dict(r, datasetKey=k, revision=d['head_id']) for k, d in all_heads.items() if d['kind'] == 'icc' for r in d['rows']]
            customers = {}
            for code in set(identities) | set(rows_by_kind['assets']) | set(rows_by_kind['secondary']) | {r['twCode'] for r in activity if r.get('twCode')}:
                identity = identities.get(code, {})
                a, q = rows_by_kind['assets'].get(code), rows_by_kind['secondary'].get(code)
                own = sorted([r for r in activity if r.get('twCode') == code], key=lambda r: r['batchDate'])
                participating = [r for r in own if r.get('agreementAmountUsd') and Decimal(r['agreementAmountUsd']) > 0]
                customers[code] = dict(twCode=code, customerName=identity.get('customerName') or (own[-1].get('canonicalName') or own[-1]['customerName'] if own else '') or (a or q or {}).get('customerName', ''),
                    brokerAccountStatus=identity.get('brokerAccountStatus', ''), assetUsd=a.get('assetUsd') if a else None,
                    quantity=q.get('quantity') if q else None, secondaryPresent=q is not None,
                    isSecondary=q is not None and Decimal(q['quantity']) > 0,
                    agreementUsd=sum_values(r['agreementAmountUsd'] for r in participating),
                    batchDates=[r['batchDate'] for r in participating],
                    insuranceBroker=next((r['insuranceBroker'] for r in reversed(own) if r.get('insuranceBroker')), ''),
                    jiaoyangOwner=next((r['jiaoyangOwner'] for r in reversed(own) if r.get('jiaoyangOwner')), ''),
                    currentOwner=visible.get(code, {}).get('owner_name', ''),
                    serviceOwner=own[-1].get('serviceOwner','') if own else '',
                    assignmentReason=own[-1].get('assignmentReason','') if own else '',
                    customerType=next((r['customerType'] for r in reversed(own) if r.get('customerType')), ''))
            part = [r for r in activity if r.get('agreementAmountUsd') and Decimal(r['agreementAmountUsd']) > 0]
            count = len({r['twCode'] for r in part if r.get('twCode')})
            pending_part = sum(1 for r in part if not r.get('twCode'))
            return dict(dates=date_by_kind, batches=[summary(d) for d in all_heads.values() if d['kind'] == 'icc' and d['head_id']],
                        customers=sorted(customers.values(), key=lambda r: r['twCode']),
                        summary=dict(participants=count, unmatchedParticipations=pending_part, participationRecords=len(part),
                                     agreementUsd=sum_values(r['agreementAmountUsd'] for r in part),
                                     assetsUsd=sum_values(r['assetUsd'] for r in customers.values()) if date_by_kind['assets'] else None,
                                     secondaryParticipants=sum(1 for r in customers.values() if r['isSecondary']),
                                     quantity=sum_values(r['quantity'] for r in customers.values()) if date_by_kind['secondary'] else None,
                                     pending=sum(1 for r in activity if not r.get('twCode'))))

    @app.get('/api/priority-inferior/tasks')
    def tasks(user=Depends(current_user)):
        can_edit = bool(user.get('customerScope') == 'all' and user.get('canImportCustomers') and user.get('canManageAdvisorBindings'))
        can_assign = bool(user.get('customerScope') == 'all' and user.get('canManageAssignments') and user.get('canManageAdvisorBindings'))
        items, waiting = [], []
        with db() as conn:
            datasets, _ = scoped(conn, user)
            seen_owners = set()
            for d in sorted(datasets.values(),key=lambda d:d['business_date'],reverse=True):
                if d['kind'] != 'icc':
                    continue
                for r in d['rows']:
                    base = dict(batch=d['business_date'], recordKey=r['recordKey'], name=r['customerName'],
                                twCode=r.get('twCode',''), broker=r.get('insuranceBroker',''))
                    if can_edit and not r.get('twCode'):
                        reason = r.get('matchReason') or '等待更新客户名单'
                        task = dict(base, action='identity', label='匹配身份', reason=reason)
                        (waiting if not r.get('candidates') or '等待' in reason else items).append(task)
                    owner_key = r.get('twCode') or d['dataset_key']+'/'+r['recordKey']
                    if owner_key in seen_owners:
                        continue
                    seen_owners.add(owner_key)
                    if can_assign and not r.get('serviceOwnerId'):
                        action = 'owner' if r.get('assignmentMode') != 'pending_binding' else 'binding' if r.get('insuranceBroker') else 'broker'
                        if action == 'broker' and not can_edit:
                            continue
                        items.append(dict(base, action=action, label={'owner':'指派负责人','binding':'设置经纪人绑定','broker':'补充保险经纪人'}[action], reason=r.get('assignmentReason','待分配')))
        # One broker rule resolves many service customers; show one action, not many duplicates.
        grouped, compact = {}, []
        for task in items:
            if task['action'] != 'binding':
                compact.append(task)
                continue
            key = normalized(task['broker'])
            if key in grouped:
                grouped[key]['affectedCount'] += 1
            else:
                task['affectedCount'] = 1
                grouped[key] = task
                compact.append(task)
        return dict(items=compact, waiting=waiting)

    @app.get('/api/priority-inferior/batches')
    def batches(user=Depends(current_user)):
        return overview(user=user)['batches']

    @app.get('/api/priority-inferior/batches/{batch_date}/participations')
    def participations(batch_date: str, user=Depends(current_user)):
        key = 'icc:' + iso(batch_date)
        with db() as conn:
            all_heads, visible = scoped(conn, user)
            d = all_heads.get(key)
            if not d or not d['head_id'] or (user['customerScope'] != 'all' and not d['rows']):
                raise HTTPException(404, '批次不存在或无权查看。')
            return dict(key=key, revision=d['head_id'], filename=d['filename'], rows=[dict(r, currentOwner=visible.get(r.get('twCode'), {}).get('owner_name', '')) for r in d['rows']])

    @app.get('/api/priority-inferior/customers/{tw_code}')
    def history(tw_code: str, user=Depends(current_user)):
        code = tw(tw_code)
        with db() as conn:
            all_heads, _ = scoped(conn, user)
            result = {kind: [] for kind in LABELS}
            for d in all_heads.values():
                for r in d['rows']:
                    if r.get('twCode') == code:
                        result[d['kind']].append(dict(r, date=d['business_date'], filename=d['filename'], revision=d['head_id']))
            if not any(result.values()):
                raise HTTPException(404, '客户不存在或无权查看。')
            return dict(twCode=code, **{k: sorted(v, key=lambda r: r['date'], reverse=True) for k, v in result.items()})

    @app.patch('/api/priority-inferior/batches/{batch_date}/records/{record_key}')
    def edit(batch_date: str, record_key: str, payload: Edit, user=Depends(current_user)):
        if set(payload.changes) == {'jiaoyangOwner'}:
            require_assignment(user)
        else:
            require_edit(user)
        allowed = set(FIELDS) - {'sourceSequence', 'customerName'} | {'twCode', 'participationIntent'}
        if not payload.changes or set(payload.changes) - allowed:
            raise HTTPException(422, '包含不可编辑字段。')
        key = 'icc:' + iso(batch_date)
        with db() as conn:
            conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
            conn.execute('UPDATE priority_datasets SET head_id=head_id WHERE dataset_key=?', (key,))
            all_heads = heads(conn)
            d = all_heads.get(key)
            if not d or d['head_id'] != payload.expectedRevision:
                raise HTTPException(409, '记录已更新，请刷新后再修改。')
            rows = copy.deepcopy(d['rows'])
            r = next((r for r in rows if r['recordKey'] == record_key), None)
            if not r:
                raise HTTPException(404, '记录不存在。')
            changes = dict(payload.changes)
            if 'participationIntent' in changes and changes['participationIntent'] not in {'service','interested'}:
                raise HTTPException(422, '请选择普通服务或有意向参与。')
            if {'jiaoyangOwner','participationIntent','insuranceBroker'} & changes.keys():
                if not user.get('canManageAssignments'):
                    raise HTTPException(403, '修改归属或参与意向需要客户归属权限。')
            if 'twCode' in changes:
                code = tw(changes['twCode']) if text(changes['twCode']) else ''
                identities = shared_registry(conn, all_heads)
                if code:
                    if code not in identities:
                        raise HTTPException(422, '该 TW 不在客户名单中，请先补充客户名单。')
                    if any(x is not r and x.get('twCode') == code for x in rows):
                        raise HTTPException(409, '该客户已经在本批次中，请核对重复记录。')
                    changes.update(twCode=code, canonicalName=identities[code]['customerName'], matchStatus='matched', matchReason=payload.reason, candidates=[code], identityDetached='')
                else:
                    changes.update(detached_identity(r, payload.reason))
            for field in ('depositAmount', 'agreementAmountUsd'):
                if field in changes:
                    changes[field] = number(changes[field], field, nonnegative=True)
            for field, value in changes.items():
                if isinstance(value, (dict, list)) and field not in {'candidates', 'excludedTwCodes'}:
                    raise HTTPException(422, '字段值类型错误。')
                if field not in {'depositAmount', 'agreementAmountUsd', 'candidates', 'excludedTwCodes'}:
                    changes[field] = text(value)
                    if len(changes[field]) > 5000:
                        raise HTTPException(422, '字段内容过长。')
            before = {field: r.get(field) for field in changes}
            r.update(changes)
            r['manualCorrection'] = {'reason': payload.reason, 'by': user['name'], 'at': now_iso(), 'fields': list(changes)}
            rev, job = str(uuid4()), str(uuid4())
            conn.execute('INSERT INTO priority_uploads VALUES (?,?,?,?,?,?,?)',
                         (job, 'committed', '{}', '[]', dumps([{'label': '人工修订', 'date': batch_date, 'records': 1, 'changes': []}]), now_iso(), user['id']))
            conn.execute('INSERT INTO priority_revisions VALUES (?,?,?,?,?,?,?,?,?)',
                         (rev, key, d['head_id'], job, dumps(rows), d['filename'], 'manual:' + rev, now_iso(), user['id']))
            conn.execute('UPDATE priority_datasets SET head_id=? WHERE dataset_key=?', (rev, key))
            audit(conn, user, 'priority.edit', 'priority_record', record_key, dict(batchDate=batch_date, before=before, after=changes, reason=payload.reason))
        return {'revision': rev, 'jobId': job}

    @app.get('/api/priority-inferior/revisions/{revision_id}')
    def revision(revision_id: str, user=Depends(current_user)):
        require_import(user)
        with db() as conn:
            row = conn.execute('SELECT * FROM priority_revisions WHERE id=?', (revision_id,)).fetchone()
            if not row:
                raise HTTPException(404, '版本不存在。')
            r = dict(row)
            r['rows'] = json.loads(r.pop('rows_json'))
            return r

    install_workspace(app, db, current_user, access_clause, audit, now_iso, scope_rows, heads)
