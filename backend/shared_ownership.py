"""Canonical customer ownership; imported assignments are proposals, not overrides."""
import hashlib
import json
from uuid import uuid4

SCHEMA = '''CREATE TABLE IF NOT EXISTS shared_owner_state (
 customer_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, mode TEXT NOT NULL,
 source_signature TEXT NOT NULL)'''


def customers(conn):
    rows = conn.execute('''SELECT c.*,i.normalized_value tw_code FROM customers c
        LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'
        WHERE c.archived_at IS NULL''').fetchall()
    return {r['tw_code'] or r['customer_code']:dict(r) for r in rows if r['tw_code'] or r['customer_code']}


def signature(a):
    return hashlib.sha256(json.dumps([a.get('serviceOwnerId'),a.get('serviceOwner'),a.get('assignmentMode'),a.get('insuranceBroker')],ensure_ascii=False).encode()).hexdigest()


def remember(conn, customer_id, owner_id, mode, source_signature=''):
    conn.execute('''INSERT INTO shared_owner_state VALUES (?,?,?,?) ON CONFLICT(customer_id)
        DO UPDATE SET owner_id=excluded.owner_id,mode=excluded.mode,source_signature=excluded.source_signature''',
        (customer_id,owner_id,mode,source_signature))


def assign(conn, current, owner, user, now, reason):
    conn.execute('UPDATE customers SET owner_id=?,owner_name=?,owner_team=?,updated_at=?,version=version+1 WHERE id=?',
        (owner['id'],owner['name'],owner.get('team',''),now,current['id']))
    conn.execute('INSERT INTO assignments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (str(uuid4()),current['id'],current['owner_id'],current['owner_name'],current['owner_team'],owner['id'],owner['name'],owner.get('team',''),reason,user['id'],user['name'],now))
    conn.execute('INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?,?)',
        (str(uuid4()),user['id'],user['name'],'customer.shared_owner','customer',current['id'],json.dumps({'from':current['owner_id'],'to':owner['id'],'reason':reason},ensure_ascii=False),now))


def effective(conn, proposals):
    records = customers(conn)
    states = {r['customer_id']:dict(r) for r in conn.execute('SELECT * FROM shared_owner_state').fetchall()}
    result = {}
    for code, proposal in proposals.items():
        a = dict(proposal)
        c = records.get(code)
        if c:
            state = states.get(c['id'],{})
            assigned = c['owner_id'] not in ('','unassigned',None)
            # A later explicit customer assignment supersedes all imported labels.
            manual = bool(state) and (state.get('mode') == 'manual' or state.get('owner_id') != c['owner_id'])
            conflict = assigned and bool(a['serviceOwnerId']) and c['owner_id'] != a['serviceOwnerId'] and proposal.get('assignmentMode') == 'manual' and not manual
            a.update(ownerConflict=conflict, customerId=c['id'], customerVersion=c['version'])
            if assigned:
                a.update(serviceOwner=c['owner_name'],serviceOwnerId=c['owner_id'],serviceOwnerTeam=c['owner_team'],
                    assignmentMode='manual' if manual else state.get('mode','shared'),
                    assignmentReason=(f"负责人不一致：客户主档为{c['owner_name']}，原表建议为{proposal['serviceOwner']}；请确认统一负责人" if conflict else '全产品共用客户负责人'))
            elif manual:
                a.update(serviceOwner='',serviceOwnerId='',serviceOwnerTeam='',assignmentMode='pending_manual',assignmentReason='客户主档已解除分配，待领导统一指派')
        result[code] = a
    return result


def sync(conn, proposals, user, now):
    records = customers(conn)
    states = {r['customer_id']:dict(r) for r in conn.execute('SELECT * FROM shared_owner_state').fetchall()}
    for code,c in records.items():
        state = states.get(c['id'])
        if code not in proposals and state and state['mode'] in ('source','binding') and state['owner_id'] == c['owner_id']:
            assign(conn,c,dict(id='unassigned',name='待分配',team='待分配池'),user,now,'活动关联已解除或来源已撤销，撤回自动补全负责人')
            conn.execute('DELETE FROM shared_owner_state WHERE customer_id=?',(c['id'],))
    for code,a in proposals.items():
        c = records.get(code)
        if not c or not a['serviceOwnerId']:
            continue
        # Never overwrite an existing customer owner from an uploaded table or broker rule.
        if c['owner_id'] not in ('','unassigned',None):
            continue
        state = states.get(c['id'])
        if state and state['mode'] == 'manual':
            continue
        owner = dict(id=a['serviceOwnerId'],name=a['serviceOwner'],team=a['serviceOwnerTeam'])
        assign(conn,c,owner,user,now,'从活动资料补全全产品共用负责人')
        remember(conn,c['id'],owner['id'],'source' if a['assignmentMode']=='manual' else 'binding',signature(a))
