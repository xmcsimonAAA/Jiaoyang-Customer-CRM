"""Product-local default service rules. Source assignments always take precedence."""
from decimal import Decimal
from backend.priority_import import normalized

SCHEMA = """CREATE TABLE IF NOT EXISTS priority_broker_bindings (
    broker_key TEXT PRIMARY KEY, broker_name TEXT NOT NULL, owner_id TEXT NOT NULL,
    active INTEGER NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)"""


def resolve(all_heads, rules, users, aliases):
    people = {p['id']: p for p in users if p.get('active') and p.get('rolePermission') in {'manager','supervisor'}}
    labels = {}
    for p in people.values():
        for name in (p.get('name'), p.get('username')):
            if name:
                labels.setdefault(normalized(name), set()).add(p['id'])
    for a in aliases:
        if a['user_id'] in people:
            labels.setdefault(normalized(a['alias']), set()).add(a['user_id'])
    groups = {}
    for d in sorted(all_heads.values(), key=lambda x: x['business_date']):
        if d['kind'] == 'icc':
            for r in d['rows']:
                key = r.get('twCode') or d['dataset_key'] + '/' + r['recordKey']
                groups.setdefault(key, []).append(r)
    result = {}
    for key, rows in groups.items():
        explicit = next((r.get('jiaoyangOwner') for r in reversed(rows) if r.get('jiaoyangOwner')), '')
        broker = next((r.get('insuranceBroker') for r in reversed(rows) if r.get('insuranceBroker')), '')
        involved = any(Decimal(r.get('agreementAmountUsd') or '0') > 0 or
                       normalized(r.get('agreementSigned')) in {'☑','✓','✔','true','1','是','已签署','已签约'} or
                       r.get('participationIntent') == 'interested' for r in rows)
        owner = None
        if explicit:
            candidates = labels.get(normalized(explicit), set())
            owner = people.get(next(iter(candidates))) if len(candidates) == 1 else None
            reason = '领导 / 原表指派' if owner else '指派姓名未唯一关联有效账号'
            mode = 'manual'
        elif involved:
            reason, mode = '已签约或有意向，待领导指派', 'pending_manual'
        else:
            rule = rules.get(normalized(broker))
            owner = people.get(rule['owner_id']) if rule and rule['active'] else None
            if owner and owner.get('rolePermission') not in {'manager', 'supervisor'}:
                owner = None
            reason = '保险经纪人默认绑定' if owner else ('未填写保险经纪人' if not broker else
                     '未配置绑定' if not rule else '绑定已停用' if not rule['active'] else '绑定账号已停用或不适用')
            mode = 'binding' if owner else 'pending_binding'
        result[key] = dict(serviceOwner=owner['name'] if owner else explicit, serviceOwnerId=owner['id'] if owner else '',
                           serviceOwnerTeam=owner.get('team','') if owner else '', assignmentMode=mode,
                           assignmentReason=reason, insuranceBroker=broker, customerName=rows[-1]['customerName'], key=key)
    return result


def decorate(all_heads, assignments):
    for d in all_heads.values():
        if d['kind'] == 'icc':
            for r in d['rows']:
                key = r.get('twCode') or d['dataset_key'] + '/' + r['recordKey']
                r.update({k:v for k,v in assignments[key].items() if k not in {'insuranceBroker','customerName','key'}})
