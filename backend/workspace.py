"""Shared customer directory and product-scoped notes; legacy notes stay unclassified."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

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


def install(app, db, current_user, access_clause, audit, now_iso, priority_scope, heads):
    def context(conn, user):
        clause, params = access_clause(user)
        crm = [dict(r) for r in conn.execute(f'''SELECT c.id,c.name,c.customer_code,c.owner_name,c.phone,c.email,
          c.account_status,c.placement_status,c.target_batch_id,c.stage,i.normalized_value tw_code
          FROM customers c LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'
          WHERE c.archived_at IS NULL AND {clause}''', params).fetchall()]
        people, aliases = {}, {}
        for c in crm:
            code = c['tw_code'] or (c['customer_code'] if (c['customer_code'] or '').startswith('TW') else '')
            key = 'tw:' + code if code else 'crm:' + c['id']
            aliases['crm:' + c['id']] = key
            person = people.setdefault(key, dict(key=key, name=c['name'], twCode=code, crm=[], priority=[], assets=[], secondary=[], master=[]))
            if not any(v['id']==c['id'] for v in person['crm']):
                person['crm'].append(c)
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

    def summary(p):
        return dict(key=p['key'], name=p['name'], twCode=p['twCode'], crmIds=[c['id'] for c in p['crm']],
                    searchTerms=[v for c in p['crm'] for v in (c['phone'],c['email'],c['customer_code']) if v],
                    placementOwner='、'.join(sorted({c['owner_name'] for c in p['crm'] if c['owner_name']})),
                    priorityOwner=p['priority'][-1].get('serviceOwner','') if p['priority'] else '',
                    businesses=['service'] + (['placement'] if p['crm'] else []) + (['priority'] if p['priority'] else []))

    @app.get('/api/workspace/customers')
    def directory(user=Depends(current_user)):
        with db() as conn:
            people, _ = context(conn,user)
            return {'items':[summary(p) for p in sorted(people.values(),key=lambda p:(p['name'],p['key']))]}

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
            return dict(summary(p), **{k:v for k,v in p.items() if k not in {'key','name','twCode'}},
                        placementBatches=batches, followups=[r for r in notes(conn,people,aliases) if r['subject_key']==key])

    @app.get('/api/workspace/followups')
    def list_notes(user=Depends(current_user)):
        with db() as conn:
            people, aliases = context(conn,user)
            return {'items':notes(conn,people,aliases)}

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
            if payload.batchKey:
                if payload.business == 'priority':
                    valid = {r['date'] for r in p['priority']}
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
                activity = [r for r in p['priority'] if not payload.batchKey or r['date']==payload.batchKey][-1]
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
            if not record['next_followup_at']:
                raise HTTPException(422,'这条记录没有联系待办。')
            if not record['completed_at']:
                conn.execute('UPDATE workspace_followups SET completed_at=?,completed_by=? WHERE id=?',(now_iso(),user['id'],identifier))
                audit(conn,user,'workspace.followup.completed','workspace_followup',identifier,{})
        return {'ok':True}
