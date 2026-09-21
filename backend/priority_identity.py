"""Reconcile pending activities when an authoritative dated roster arrives.

Name links are inferred, recorded and reversible; they never merge customer masters.
Explicit negative matches exclude that TW, not all future accounts for the person.
"""
from collections import defaultdict
from backend.priority_import import normalized


def excluded_codes(row):
    return set(row.get('excludedTwCodes', [])) | ({row['identityDetached']} if row.get('identityDetached') else set())


def reconcile(rows, batch_date, datasets, identities, blocked_codes=()):
    names = defaultdict(set)
    source_dates = defaultdict(list)
    code_names = defaultdict(set)
    for code, identity in identities.items():
        name = normalized(identity['customerName'])
        names[name].add(code)
        code_names[code].add(name)
    for dataset in datasets.values():
        if dataset['kind'] != 'master':
            continue
        for identity in dataset['rows']:
            code = identity['twCode']
            if code not in identities:
                continue
            name = normalized(identity['customerName'])
            names[name].add(code)
            code_names[code].add(name)
            source_dates[code].append(dataset['business_date'])
    name_counts = defaultdict(int)
    occupied = {r['twCode'] for r in rows if r.get('twCode')}
    for row in rows:
        name_counts[normalized(row['customerName'])] += 1
    matched = 0
    proposals = []
    for row in rows:
        if row.get('twCode'):
            continue  # Never rewrite an existing explicit/manual identity.
        name = normalized(row['customerName'])
        blocked = excluded_codes(row)
        candidates = sorted(names[name] - blocked)
        row['candidates'] = candidates
        row['matchStatus'] = 'unmatched'
        reason = '尚未在客户名单中找到，后续上传名单将自动重试'
        code = candidates[0] if len(candidates) == 1 else None
        if not candidates and blocked:
            reason = '已排除错误 TW，等待新客户编号；后续上传名单将自动重试'
        elif len(candidates) > 1:
            reason = '有多个同名 TW，需选择正确客户'
        elif code:
            if code in blocked_codes:
                reason = 'TW 在共用客户池存在冲突或已归档，需核对'
            elif name_counts[name] > 1 or code in occupied:
                reason = '同批次存在同名或已关联记录，需核对'
            elif len(code_names[code]) > 1:
                reason = '同一 TW 在资料中姓名不一致，需核对'
            elif not any(date >= batch_date for date in source_dates[code]):
                reason = '名单早于活动日期，等待活动后更新的客户名单'
            else:
                proposals.append((row, code))
                continue
        row['matchStatus'] = 'ambiguous' if candidates else 'unmatched'
        row['matchReason'] = reason
    counts = defaultdict(int)
    for _, code in proposals:
        counts[code] += 1
    for row, code in proposals:
        if counts[code] != 1:
            row.update(matchStatus='ambiguous', matchReason='多条记录指向同一 TW，需核对')
            continue
        reason = '更新名单自动关联：姓名一致、TW 唯一、无冲突或已排除其他同名编号'
        row.update(twCode=code, canonicalName=identities[code]['customerName'], matchStatus='matched',
                   matchReason=reason, identityMatchMethod='roster_auto', candidates=[code],
                   excludedTwCodes=sorted(excluded_codes(row)), identityDetached='',
                   identityEvidence=dict(rosterDate=max(source_dates[code]), rule='unique-roster-name-v1'))
        matched += 1
    return matched
