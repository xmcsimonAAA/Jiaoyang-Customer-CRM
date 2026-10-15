"""Shared customer directory and product-scoped notes; legacy notes stay unclassified."""
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from backend.followup_access import can_followup, disabled_reason

SCHEMA = ["""CREATE TABLE IF NOT EXISTS workspace_followups (
 id TEXT PRIMARY KEY, subject_key TEXT NOT NULL, business TEXT NOT NULL,
 batch_key TEXT NOT NULL, author_id TEXT NOT NULL, author_name TEXT NOT NULL,
 method TEXT NOT NULL, content TEXT NOT NULL, outcome TEXT NOT NULL,
 next_action TEXT NOT NULL, next_followup_at TEXT, created_at TEXT NOT NULL,
 completed_at TEXT, completed_by TEXT)""",
 "CREATE INDEX IF NOT EXISTS idx_workspace_followups_subject ON workspace_followups(subject_key,created_at)"]


def customer_note_count(conn, customer_id):
    codes = {r['normalized_value'] for r in conn.execute("SELECT normalized_value FROM customer_identifiers WHERE customer_id=? AND kind='tw'",(customer_id,)).fetchall()}
    row = conn.execute('SELECT customer_code FROM customers WHERE id=?',(customer_id,)).fetchone()
    if row and (row['customer_code'] or '').startswith('TW'):
        codes.add(row['customer_code'])
    keys = ['crm:'+customer_id] + ['tw:'+code for code in codes]
    return conn.execute('SELECT COUNT(*) FROM workspace_followups WHERE subject_key IN ('+','.join('?' for _ in keys)+')',tuple(keys)).fetchone()[0]


class Note(BaseModel):
    subjectKey: str = Field(max_length=250)
    business: Literal['service', 'placement', 'priority']
    batchKey: str = Field(default='', max_length=100)
    method: str = Field(default='沟通', min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=10000)
    outcome: str = Field(default='', max_length=2000)
    nextAction: str = Field(default='', max_length=2000)
    nextFollowupAt: datetime | None = None


def install(app, db, current_user, access_clause, audit, now_iso, priority_scope, heads, platform_users):
    def context(conn, user):
        clause, params = access_clause(user)
        crm = [dict(r) for r in conn.execute(f'''SELECT c.id,c.name,c.customer_code,c.owner_name,c.phone,c.email,
          c.account_status,c.placement_status,c.target_batch_id,c.stage,c.actual_amount,c.intent_status,c.owner_id,
          c.hongan_advisor,c.source_advisor_label,c.funded_amount,c.version,i.normalized_value tw_code
          FROM customers c LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'
          WHERE c.archived_at IS NULL AND {clause}''', params).fetchall()]
        from backend.shared_funding import attach as attach_funding
        crm = attach_funding(conn, crm)
        people, aliases = {}, {}
        for c in crm:
            code = c['tw_code'] or (c['customer_code'] if (c['customer_code'] or '').startswith('TW') else '')
            key = 'tw:' + code if code else 'crm:' + c['id']
            aliases['crm:' + c['id']] = key
            person = people.setdefault(key, dict(key=key, name=c['name'], twCode=code, crm=[], priority=[], assets=[], secondary=[], master=[]))
            if not any(v['id']==c['id'] for v in person['crm']):
                person['crm'].append(c)
        from backend.shared_customers import archived_codes
        blocked=archived_codes(conn)
        source_heads = heads(conn)
        all_heads, _ = priority_scope(conn, source_heads, user)
        # Identity, asset and holding snapshots are shared for an already-visible TW.
        crm_codes = {p['twCode'] for p in people.values() if p['twCode']}
        for key, d in all_heads.items():
            if d['kind'] != 'icc':
                visible_codes = {r.get('twCode') for r in d['rows']} | crm_codes
                d['rows'] = [r for r in source_heads[key]['rows'] if r.get('twCode') in visible_codes]
        for d in sorted(all_heads.values(), key=lambda d:d['business_date']):
            for r in d['rows']:
                code = r.get('twCode')
                if code in blocked:
                    continue
                anchor = 'icc:' + d['business_date'] + '/' + r['recordKey']
                if not code and d['kind'] != 'icc':
                    continue
                key = 'tw:' + code if code else anchor
                if d['kind'] == 'icc':
                    aliases[anchor] = key
                p = people.setdefault(key, dict(key=key, name=r.get('canonicalName') or r['customerName'], twCode=code or '', crm=[], priority=[], assets=[], secondary=[], master=[]))
                p['priority' if d['kind']=='icc' else d['kind']].append(dict(r, date=d['business_date']))
        return people, aliases

    def permits(person, business):
        return business == 'service' or bool(person['crm'] if business in {'placement','legacy'} else person['priority'])

    def followup_permission(person, user):
        customer = person['crm'][-1] if person['crm'] else {}
        activity = person['priority'][-1] if person['priority'] else {}
        owner_id = customer.get('owner_id') if customer else ''
        owner_name = customer.get('owner_name') if customer else ''
        if owner_id in (None, '', 'unassigned') and activity.get('serviceOwnerId'):
            owner_id = activity.get('serviceOwnerId')
            owner_name = activity.get('serviceOwner')
        return dict(ownerId=owner_id or '', ownerName=owner_name or '待分配',
                    canFollowup=can_followup(user, owner_id),
                    followupDisabledReason=disabled_reason(user, owner_id))

    def notes(conn, people, aliases):
        result = []
        for raw in conn.execute('SELECT * FROM workspace_followups ORDER BY created_at DESC').fetchall():
            r = dict(raw)
            key = aliases.get(r['subject_key'], r['subject_key'])
            p = people.get(key)
            if p and permits(p, r['business']):
                result.append(dict(r, subject_key=key, customer_name=p['name'], tw_code=p['twCode'], legacy=False))
        # Old notes retain their original business classification (unknown).
        for raw in conn.execute('SELECT * FROM followups ORDER BY created_at DESC').fetchall():
            r = dict(raw)
            key = aliases.get('crm:' + r['customer_id'])
            if key and key in people:
                result.append(dict(r, subject_key=key, business='legacy', batch_key='', completed_at=None,
                                   customer_name=people[key]['name'], tw_code=people[key]['twCode'], legacy=True))
        return sorted(result, key=lambda r:r['created_at'], reverse=True)

    def summary(p, user=None):
        latest_crm = p['crm'][-1] if p['crm'] else {}
        latest_secondary = p['secondary'][-1] if p['secondary'] else {}
        return dict(key=p['key'], name=p['name'], twCode=p['twCode'], crmIds=[c['id'] for c in p['crm']],
                    searchTerms=[v for c in p['crm'] for v in (c['phone'],c['email'],c['customer_code']) if v],
                    placementOwner='、'.join(sorted({c['owner_name'] for c in p['crm'] if c['owner_name']})),
                    priorityOwner=p['priority'][-1].get('serviceOwner','') if p['priority'] else '',
                    honganAdvisor=latest_crm.get('hongan_advisor','') or (p['priority'][-1].get('insuranceBroker','') if p['priority'] else ''),
                    sourceAdvisorLabel=latest_crm.get('source_advisor_label',''),
                    accountStatus=latest_crm.get('account_status',''),
                    funded_amount=latest_crm.get('funded_amount'),
                    funding_source_kind=latest_crm.get('funding_source_kind',''),
                    funding_source_date=latest_crm.get('funding_source_date',''),
                    funding_source_label=latest_crm.get('funding_source_label',''),
                    funding_updated_at=latest_crm.get('funding_updated_at',''),
                    xmaxQuantity=latest_secondary.get('quantity') if latest_secondary else None,
                    secondaryQuantity=latest_secondary.get('secondaryQuantity') if latest_secondary else None,
                    blockQuantity=latest_secondary.get('blockQuantity') if latest_secondary else None,
                    holdingTypes=latest_secondary.get('holdingTypes', []) if latest_secondary else [],
                    holdingType=latest_secondary.get('holdingType', '') if latest_secondary else '',
                    hasXmax=bool(latest_secondary and Decimal(str(latest_secondary.get('quantity') or 0)) > 0),
                    hasPriority=bool(p['priority']), hasPlacement=bool(p['crm']),
                    businesses=['service'] + (['placement'] if p['crm'] else []) + (['priority'] if p['priority'] else []),
                    **(followup_permission(p, user) if user else {}))

    @app.get('/api/workspace/dashboard')
    def dashboard(user=Depends(current_user)):
        with db() as conn:
            people, aliases = context(conn,user)
            records = notes(conn,people,aliases)
            now=datetime.now(timezone.utc)
            def parsed(value):
                try:
                    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except (ValueError,TypeError,AttributeError):
                    return None
            def due(r):
                dt=parsed(r['next_followup_at'])
                return not r['legacy'] and not r['completed_at'] and dt and dt<=now
            active=[p for p in people.values() if p['twCode'] or p['crm']]
            placement_keys, priority_keys = set(),set()
            placement_amount=Decimal('0')
            priority_amount=Decimal('0')
            placement_records=0
            pending_priority=0
            priority_records=0
            # Fetch once; only use rows belonging to the user's visible CRM records.
            batches={}
            for row in conn.execute('SELECT customer_id,status,actual_amount FROM batch_participations').fetchall():
                batches.setdefault(row['customer_id'],[]).append(dict(row))
            seen_crm=set()
            for key,p in people.items():
                for c in p['crm']:
                    if c['id'] in seen_crm:
                        continue
                    seen_crm.add(c['id'])
                    history=batches.get(c['id'],[])
                    joined=[r for r in history if r['status']=='已参与']
                    # Batch records are authoritative; old master totals are a fallback only.
                    if history:
                        if joined:
                            placement_keys.add(key)
                            placement_records+=len(joined)
                        placement_amount+=sum((Decimal(str(r['actual_amount'] or 0)) for r in joined),Decimal('0'))
                    elif c['placement_status']=='已参与':
                        placement_keys.add(key)
                        placement_records+=1
                        placement_amount+=Decimal(str(c['actual_amount'] or 0))
            # Use the same real-batch rollup as the product board. One customer
            # attending several activity weeks in a batch is one participation.
            from backend.priority_batches import rollup_activity
            priority_heads, _ = priority_scope(conn, heads(conn), user)
            for row in rollup_activity(conn, priority_heads)['rows']:
                amount=Decimal(str(row.get('agreementAmountUsd') or 0))
                if amount>0:
                    priority_amount+=amount
                    priority_records+=1
                    if row.get('twCode'):
                        priority_keys.add('tw:' + row['twCode'])
                    else:
                        pending_priority+=1
            start=now-timedelta(days=14)
            recent=[r for r in records if (parsed(r['created_at']) or datetime.min.replace(tzinfo=timezone.utc))>=start]
            return dict(customers=len(active),pendingIdentities=sum(not p['twCode'] and not p['crm'] for p in people.values()),
                        followups=dict(recent14=len(recent),byBusiness={b:sum(r['business']==b for r in recent) for b in ['service','placement','priority','legacy']},
                                       due=sum(bool(due(r)) for r in records),mineDue=sum(bool(due(r)) and r['author_id']==user['id'] for r in records),
                                       latest=records[:8]),
                        placement=dict(participants=len(placement_keys),records=placement_records,actualAmount=str(placement_amount)),
                        priority=dict(participants=len(priority_keys),records=priority_records,pendingParticipations=pending_priority,agreementUsd=str(priority_amount)),
                        overlap=len(placement_keys & priority_keys))

    @app.get('/api/workspace/dashboard/history')
    def dashboard_history(user=Depends(current_user)):
        """Return weekly snapshots from the uploaded source files.

        The roster and holding exports are point-in-time snapshots.  We carry the
        latest available snapshot forward for dates where only one source was
        uploaded, while leaving metrics without a source as null rather than
        estimating them from an unrelated field such as account equity.
        """
        with db() as conn:
            all_heads, _ = priority_scope(conn, heads(conn), user)
            datasets = {
                kind: sorted((d for d in all_heads.values() if d['kind'] == kind and d.get('head_id')),
                             key=lambda d: d['business_date'])
                for kind in ('master', 'secondary')
            }
            dates = sorted({d['business_date'] for rows in datasets.values() for d in rows})

            def latest(kind, selected):
                values = [d for d in datasets[kind] if d['business_date'] <= selected]
                return values[-1] if values else None

            def status_success(value):
                compact = ''.join(str(value or '').split()).casefold()
                return any(token in compact for token in ('开户成功', '已开户', '开户完成', '完成开户', '开通成功'))

            series = []
            for selected in dates:
                master = latest('master', selected)
                secondary = latest('secondary', selected)
                master_rows = master['rows'] if master else []
                statuses = [str(row.get('brokerAccountStatus') or '').strip() for row in master_rows]
                known_statuses = [value for value in statuses if value]
                opened = sum(status_success(value) for value in statuses)
                total = len(master_rows)
                secondary_rows = secondary['rows'] if secondary else []
                secondary_total = sum((Decimal(str(row.get('secondaryQuantity') or 0)) for row in secondary_rows), Decimal('0'))
                block_total = sum((Decimal(str(row.get('blockQuantity') or 0)) for row in secondary_rows), Decimal('0'))
                secondary_holders = sum(Decimal(str(row.get('secondaryQuantity') or 0)) > 0 for row in secondary_rows)
                block_holders = sum(Decimal(str(row.get('blockQuantity') or 0)) > 0 for row in secondary_rows)
                series.append(dict(
                    date=selected,
                    totalCustomers=total if master else None,
                    openedCount=opened if master and known_statuses else None,
                    accountSuccessRate=(round(opened / total * 100, 2) if master and total and known_statuses else None),
                    depositActivationRate=None,
                    depositActivationAvailable=False,
                    secondaryQuantity=format(secondary_total, 'f') if secondary else None,
                    blockQuantity=format(block_total, 'f') if secondary else None,
                    secondaryHolders=secondary_holders if secondary else None,
                    blockHolders=block_holders if secondary else None,
                    holdingMarketValue=None,
                    holdingMarketValueAvailable=False,
                ))
            return dict(series=series, notes={
                'depositActivationRate': '原始资料未提供入金激活状态，暂不计算。',
                'holdingMarketValue': '持仓表只提供股数，未提供市值，暂不计算。',
            })

    @app.get('/api/workspace/customers')
    def directory(user=Depends(current_user)):
        with db() as conn:
            people, _ = context(conn,user)
            return {'items':[summary(p, user) for p in sorted(people.values(),key=lambda p:(p['name'],p['key']))]}

    @app.get('/api/workspace/card')
    def card(key: str, user=Depends(current_user)):
        with db() as conn:
            people, aliases = context(conn,user)
            key = aliases.get(key,key)
            p = people.get(key)
            if not p:
                raise HTTPException(404,'客户不存在或无权查看。')
            batches = []
            for c in p['crm']:
                batches.extend(dict(r) for r in conn.execute('''SELECT bp.*,b.name batch_name FROM batch_participations bp
                     JOIN placement_batches b ON b.id=bp.batch_id WHERE bp.customer_id=?''',(c['id'],)).fetchall())
            dates = {row['date'] for row in p['priority']}
            priority_batches = [dict(row) for row in conn.execute('''SELECT DISTINCT b.id,b.name,b.participation_date
                FROM priority_product_batches b JOIN priority_batch_activities a ON a.batch_id=b.id
                WHERE a.activity_date IN (''' + ','.join('?' for _ in dates) + ''')
                ORDER BY b.participation_date DESC''', tuple(dates)).fetchall()] if dates else []
            return dict(summary(p, user), **{k:v for k,v in p.items() if k not in {'key','name','twCode'}},
                        placementBatches=batches, priorityBatches=priority_batches,
                        followups=[r for r in notes(conn,people,aliases) if r['subject_key']==key])

    @app.get('/api/workspace/followups')
    def list_notes(user=Depends(current_user)):
        with db() as conn:
            people, aliases = context(conn,user)
            rows = notes(conn,people,aliases)
            return {'items':[dict(row, **followup_permission(people[row['subject_key']], user)) for row in rows]}

    @app.get('/api/workspace/followups/monitor')
    def monitor_followups(day: str = '', user=Depends(current_user)):
        if not user.get('canMonitorFollowups'):
            raise HTTPException(403, '您的账号未开通跟进监控权限。')
        try:
            selected = date.fromisoformat(day) if day else datetime.now(timezone(timedelta(hours=8))).date()
        except ValueError as exc:
            raise HTTPException(422, '日期必须使用 YYYY-MM-DD 格式。') from exc
        local_tz = timezone(timedelta(hours=8))
        def local_day(value):
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(local_tz).date()
            except (TypeError, ValueError):
                return None
        with db() as conn:
            people, aliases = context(conn, dict(user, customerScope='all'))
            rows = [dict(row) for row in conn.execute('SELECT * FROM workspace_followups ORDER BY created_at DESC').fetchall()]
            records = [row for row in rows if local_day(row.get('created_at')) == selected]
            for row in records:
                key = aliases.get(row['subject_key'], row['subject_key'])
                person = people.get(key, {})
                row.update(subject_key=key, customer_name=person.get('name', '资料待核对'),
                           tw_code=person.get('twCode', ''))
            legacy = [dict(row) for row in conn.execute('SELECT f.*, c.name customer_name, c.customer_code tw_code FROM followups f LEFT JOIN customers c ON c.id=f.customer_id ORDER BY f.created_at DESC').fetchall()]
            records.extend(dict(row, business='legacy', batch_key='', author_name=row.get('author_name') or '历史跟进', customer_name=row.get('customer_name') or '未知客户', content=row.get('content') or '') for row in legacy if local_day(row.get('created_at')) == selected)
            records.sort(key=lambda row: str(row.get('created_at') or ''), reverse=True)
            managers = [person for person in platform_users()
                        if person.get('active') and person.get('rolePermission') == 'manager']
            by_author = {person['id']: dict(authorId=person['id'], authorName=person['name'],
                                           team=person.get('team', ''), activeManager=True, count=0, customers=set())
                         for person in managers}
            for row in records:
                author = row.get('author_name') or '未记录姓名'
                author_id = row.get('author_id') or 'legacy-name:' + author
                item = by_author.setdefault(author_id, dict(authorId=author_id, authorName=author,
                                                           team='', activeManager=False, count=0, customers=set()))
                item['count'] += 1
                customer_key = row.get('tw_code') or row.get('subject_key') or 'crm:' + str(row.get('customer_id'))
                item['customers'].add(customer_key)
            summary = [dict(authorId=value['authorId'], authorName=value['authorName'], team=value['team'],
                            activeManager=value['activeManager'], count=value['count'], customerCount=len(value['customers']))
                       for value in sorted(by_author.values(), key=lambda value: (value['count'] > 0, value['authorName'], value['authorId']))]
            missing = [item for item in summary if item['activeManager'] and item['count'] == 0]
            return dict(date=selected.isoformat(), summary=summary, records=records, total=len(records),
                        notFollowed=missing, followedCount=sum(item['activeManager'] and item['count'] > 0 for item in summary))

    @app.post('/api/workspace/followups', status_code=201)
    def create_note(payload: Note, user=Depends(current_user)):
        if not payload.content.strip():
            raise HTTPException(422,'请填写沟通内容。')
        with db() as conn:
            conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
            people, aliases = context(conn,user)
            key = aliases.get(payload.subjectKey,payload.subjectKey)
            p = people.get(key)
            if not p or not permits(p,payload.business):
                raise HTTPException(404,'客户不存在或无权跟进该业务。')
            permission = followup_permission(p, user)
            if not permission['canFollowup']:
                raise HTTPException(403, permission['followupDisabledReason'])
            activity_dates = None
            if payload.batchKey:
                if payload.business == 'priority':
                    valid = {r['date'] for r in p['priority']}
                    mapped = [dict(row) for row in conn.execute(
                        'SELECT batch_id,activity_date FROM priority_batch_activities').fetchall()]
                    valid.update(row['batch_id'] for row in mapped if row['activity_date'] in valid)
                    activity_dates = {row['activity_date'] for row in mapped if row['batch_id'] == payload.batchKey}
                elif payload.business == 'placement':
                    valid = {c['target_batch_id'] for c in p['crm'] if c['target_batch_id']}
                    for c in p['crm']:
                        valid.update(r['batch_id'] for r in conn.execute('SELECT batch_id FROM batch_participations WHERE customer_id=?',(c['id'],)).fetchall())
                else:
                    valid = set()
                if payload.batchKey not in valid:
                    raise HTTPException(422,'所选批次不属于此客户的可见业务。')
            identifier, created = str(uuid4()), now_iso()
            when = payload.nextFollowupAt
            if when and when.tzinfo is None:
                raise HTTPException(422,'下次联系时间必须包含时区。')
            when = when.astimezone(timezone.utc).isoformat() if when else None
            # Preserve an unmatched row anchor so later identity confirmation automatically links its history.
            subject = payload.subjectKey if payload.subjectKey.startswith('icc:') else key
            if payload.business == 'priority':
                activity = [r for r in p['priority'] if not payload.batchKey or r['date']==payload.batchKey
                            or r['date'] in (activity_dates or set())][-1]
                subject = 'icc:' + activity['date'] + '/' + activity['recordKey']
            conn.execute('INSERT INTO workspace_followups VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identifier,subject,payload.business,payload.batchKey,user['id'],user['name'],payload.method,
                          payload.content.strip(),payload.outcome.strip(),payload.nextAction.strip(),when,created,None,None))
            audit(conn,user,'workspace.followup.created','workspace_customer',subject,{'id':identifier,'business':payload.business})
        return {'id':identifier,'createdAt':created}

    @app.post('/api/workspace/followups/{identifier}/complete')
    def complete(identifier: str, user=Depends(current_user)):
        with db() as conn:
            conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
            people, aliases = context(conn,user)
            record = next((r for r in notes(conn,people,aliases) if r['id']==identifier and not r['legacy']),None)
            if not record:
                raise HTTPException(404,'跟进不存在或无权操作。')
            permission = followup_permission(people[record['subject_key']], user)
            if not permission['canFollowup']:
                raise HTTPException(403, permission['followupDisabledReason'])
            if not record['next_followup_at']:
                raise HTTPException(422,'这条记录没有联系待办。')
            if not record['completed_at']:
                conn.execute('UPDATE workspace_followups SET completed_at=?,completed_by=? WHERE id=?',(now_iso(),user['id'],identifier))
                audit(conn,user,'workspace.followup.completed','workspace_followup',identifier,{})
        return {'ok':True}
