"""Promote authoritative TW rosters into the existing shared customer master.

Product revisions remain immutable; membership, amounts and owners are never inferred
from a roster. Missing/archived customers are not deleted or revived by a snapshot.
"""
import re


def identities(conn):
    result = {}
    for r in conn.execute('''SELECT c.id,c.customer_code,c.name,c.account_status,i.normalized_value
              FROM customers c LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'
              WHERE c.archived_at IS NULL ORDER BY c.created_at,c.id''').fetchall():
        code = str(r['normalized_value'] or r['customer_code'] or '').strip().upper()
        if re.fullmatch(r'TW\d+',code):
            result[code] = dict(twCode=code,customerName=r['name'],brokerAccountStatus=r['account_status'],customerId=r['id'])
    return result


def sync(conn, datasets, user, create_customer, add_identifier, unassigned, audit, dry_run=False):
    rows = {}
    for d in sorted(datasets.values(),key=lambda d:d['business_date']):
        if d['kind']=='master':
            for r in d['rows']:
                if r.get('twCode'):
                    rows[r['twCode']] = r
    created, linked, conflicts = [], [], []
    by_code, linked_codes = {},set()
    for c in conn.execute("SELECT c.id,c.name,c.archived_at,c.customer_code,i.normalized_value FROM customers c LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'").fetchall():
        if c['normalized_value']:
            linked_codes.add(c['normalized_value'])
        for code in {c['customer_code'],c['normalized_value']} - {None,''}:
            by_code.setdefault(code,{})[c['id']]=dict(c)
    for code, r in rows.items():
        existing = list(by_code.get(code,{}).values())
        if len(existing)>1:
            conflicts.append(dict(twCode=code,reason='同一 TW 对应多个旧客户，待人工合并'))
            continue
        if existing:
            c=existing[0]
            if c['archived_at']:
                conflicts.append(dict(twCode=code,reason='客户已归档，未自动恢复'))
                continue
            if code not in linked_codes:
                if not dry_run:
                    add_identifier(conn,c['id'],code)
                linked.append(code)
            continue
        if dry_run:
            created.append(dict(twCode=code))
            continue
        c=create_customer(conn,dict(name=r['customerName'],twCode=code,
                 accountStatus=r.get('brokerAccountStatus') or '未启动',source='共用客户名单'),unassigned,user)
        created.append(dict(twCode=code,customerId=c['id']))
    if not dry_run and (created or linked):
        audit(conn,user,'shared_customers.synchronized','customer_registry','tw',dict(created=created,linked=linked,conflicts=conflicts))
    return dict(createdCount=len(created),linkedCount=len(linked),conflicts=conflicts)


def archived_codes(conn):
    active, archived = set(),set()
    for r in conn.execute("SELECT c.customer_code,c.archived_at,i.normalized_value FROM customers c LEFT JOIN customer_identifiers i ON i.customer_id=c.id AND i.kind='tw'").fetchall():
        code=str(r['normalized_value'] or r['customer_code'] or '').strip().upper()
        if re.fullmatch(r'TW\d+',code):
            (archived if r['archived_at'] else active).add(code)
    return archived-active
