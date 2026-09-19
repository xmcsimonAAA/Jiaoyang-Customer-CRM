#!/usr/bin/env python3
"""Safely unlink one mistaken ICC identity; preview by default, --apply to save.

Uses DATABASE_URL or CUSTOMER_DB_PATH without importing/initializing the app.
Creates an immutable repair revision; no customer, asset or holding is deleted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.database import connection as database_connection
from backend.priority_inferior import detached_identity, dumps, heads
from backend.priority_import import iso, normalized, tw


def repair(conn, *, batch, name, expected_tw, broker, reason, apply=False):
    key = 'icc:' + iso(batch)
    expected_tw = tw(expected_tw)
    if apply:
        conn.execute('UPDATE priority_write_lock SET version=version+1 WHERE id=1')
    dataset = heads(conn).get(key)
    if not dataset:
        raise ValueError('找不到指定批次，未修改数据。')
    matches = [r for r in dataset['rows'] if normalized(r['customerName']) == normalized(name)
               and normalized(r.get('insuranceBroker')) == normalized(broker)
               and (r.get('twCode') == expected_tw or
                    (not r.get('twCode') and r.get('identityDetached') == expected_tw))]
    if len(matches) != 1:
        raise ValueError(f'目标必须唯一，当前找到 {len(matches)} 条；未修改数据。')
    row = matches[0]
    result = dict(batch=batch, name=row['customerName'], recordKey=row['recordKey'],
                  oldTw=expected_tw, status='preview', revision=dataset['head_id'])
    if not row.get('twCode'):
        return dict(result, status='already_unlinked')
    if not apply:
        return result
    before = dict(row)
    changes = detached_identity(row, reason)
    row.update(changes)
    now = datetime.now(timezone.utc).isoformat()
    actor = 'system-identity-repair'
    row['manualCorrection'] = dict(reason=reason, by='身份纠错', at=now, fields=list(changes))
    rev, job = str(uuid4()), str(uuid4())
    conn.execute('INSERT INTO priority_uploads VALUES (?,?,?,?,?,?,?)',
                 (job, 'committed', '{}', '[]', dumps([dict(label='解除错误身份关联', date=batch, records=1, changes=[])]), now, actor))
    conn.execute('INSERT INTO priority_revisions VALUES (?,?,?,?,?,?,?,?,?)',
                 (rev, key, dataset['head_id'], job, dumps(dataset['rows']), dataset['filename'], 'manual:'+rev, now, actor))
    conn.execute('UPDATE priority_datasets SET head_id=? WHERE dataset_key=?', (rev, key))
    conn.execute('INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?,?)',
                 (str(uuid4()), actor, '身份纠错', 'priority.edit', 'priority_record', row['recordKey'],
                  dumps(dict(batchDate=batch, before={k:before.get(k) for k in changes}, after=changes, reason=reason)), now))
    return dict(result, status='unlinked', revision=rev, jobId=job)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('batch', 'name', 'expected-tw', 'broker', 'reason'):
        parser.add_argument('--'+arg, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    url = os.environ.get('DATABASE_URL', '').strip()
    path = os.environ.get('CUSTOMER_DB_PATH', '')
    if not url and (not path or not Path(path).is_file()):
        parser.error('请设置生产环境 DATABASE_URL 或已有 CUSTOMER_DB_PATH，禁止使用默认数据库。')
    if not args.reason.strip():
        parser.error('修订原因不能为空。')
    with database_connection(url, Path(path or '/unused')) as conn:
        result = repair(conn, **vars(args))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
