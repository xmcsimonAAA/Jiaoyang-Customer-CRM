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
        crm = [dict(r) for r in conn.execute(f'''SELECT c.id,c.name,c.name_pinyin,c.customer_code,c.owner_name,c.phone,c.email,
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
        latest_asset = max(p['assets'], key=lambda row: str(row.get('date') or '')) if p['assets'] else {}
        name_pinyin = str(latest_crm.get('name_pinyin') or '').strip()
        return dict(key=p['key'], name=p['name'], twCode=p['twCode'], crmIds=[c['id'] for c in p['crm']],
                    searchTerms=[v for c in p['crm'] for v in (c['phone'],c['email'],c['customer_code']) if v],
                    namePinyin=name_pinyin,
                    namePinyinInitials=''.join(part[0] for part in name_pinyin.split() if part),
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
                    latestAssetUsd=latest_asset.get('assetUsd') if latest_asset else None,
                    latestAssetDate=latest_asset.get('date','') if latest_asset else '',
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

    @app.get('/api/workspace/dashboard/report')
    def dashboard_report(as_of: str = '', user=Depends(current_user)):
        """Return the shareholder-report metrics from uploaded source snapshots.

        The report deliberately keeps source coverage visible.  It can combine
        snapshots from different dates, but every metric carries the source date
        and missing financial fields remain null instead of being inferred.
        """
        report_permission = user.get('canViewShareholderReport')
        if report_permission is None:
            report_permission = user.get('rolePermission') in {'manager', 'supervisor', 'admin', 'developer'}
        if not report_permission:
            raise HTTPException(403, '您的账号未开通股东报告查看权限。')
        def decimal(value):
            try:
                return Decimal(str(value or 0))
            except (TypeError, ValueError, ArithmeticError):
                return Decimal('0')

        def number(value, digits=2):
            if value is None:
                return None
            value = decimal(value)
            return float(value) if digits else int(value)

        def money_value(value):
            return float(decimal(value))

        def source_date(dataset):
            return dataset.get('business_date') if dataset else None

        def latest(datasets, selected):
            values = [item for item in datasets if item.get('business_date') <= selected]
            return values[-1] if values else None

        def status_success(value):
            compact = ''.join(str(value or '').split()).casefold()
            return any(token in compact for token in ('开户成功', '已开户', '开户完成', '完成开户', '开通成功'))

        def asset_rows(dataset):
            """Expand grouped duplicate TW rows back into source accounts."""
            rows = []
            for row in (dataset or {}).get('rows', []):
                rows.extend(row.get('sourceRows') or [row])
            return rows

        def asset_customer_count(dataset):
            """Count opened customers from unique TW identities in the asset snapshot.

            One TW account can have several source rows/accounts.  The shareholder
            funnel is a customer funnel, so those rows must count once.
            """
            rows = (dataset or {}).get('rows', [])
            codes = {str(row.get('twCode') or '').strip() for row in rows if str(row.get('twCode') or '').strip()}
            return len(codes) if codes else (len(rows) if dataset else None)

        def placement_rollup(conn, crm_ids):
            records = []
            if crm_ids:
                placeholders = ','.join('?' for _ in crm_ids)
                records = [dict(row) for row in conn.execute(
                    f'''SELECT bp.status,bp.intent_amount,bp.actual_amount,bp.funded_amount,b.name batch_name
                        FROM batch_participations bp JOIN placement_batches b ON b.id=bp.batch_id
                        WHERE bp.customer_id IN ({placeholders})''', tuple(crm_ids)).fetchall()]
            # Preserve the old customer-level source when no participation table exists.
            if not records:
                for c in crm_records:
                    status = c.get('placement_status') or ''
                    if status in {'批次确认', '资金筹备', '资金到账', '已参与', '已流失'}:
                        records.append(dict(status='已参与' if status == '已参与' else '已锁定',
                                            intent_amount=c.get('intent_amount') or 0,
                                            actual_amount=c.get('actual_amount') or 0,
                                            funded_amount=c.get('funded_amount') or 0,
                                            batch_name='客户主档'))
            return records

        with db() as conn:
            all_heads, _ = priority_scope(conn, heads(conn), user)
            datasets = {
                kind: sorted((d for d in all_heads.values() if d.get('kind') == kind and d.get('head_id')),
                             key=lambda d: d.get('business_date', ''))
                for kind in ('master', 'assets', 'secondary', 'icc')
            }
            all_dates = sorted({d.get('business_date') for rows in datasets.values() for d in rows if d.get('business_date')})
            if as_of:
                try:
                    selected = date.fromisoformat(as_of).isoformat()
                except ValueError as exc:
                    raise HTTPException(422, '统计日期必须使用 YYYY-MM-DD 格式。') from exc
                if all_dates and selected < all_dates[0]:
                    raise HTTPException(422, '统计日期早于当前已上传的最早资料。')
            else:
                selected = all_dates[-1] if all_dates else date.today().isoformat()

            latest_master = latest(datasets['master'], selected)
            latest_assets = latest(datasets['assets'], selected)
            latest_secondary = latest(datasets['secondary'], selected)
            latest_icc = latest(datasets['icc'], selected)
            asset_summary = (latest_assets or {}).get('summary') or {}
            master_rows = (latest_master or {}).get('rows', [])
            expanded_assets = asset_rows(latest_assets)
            asset_values = [decimal(row.get('assetUsd')) for row in expanded_assets if row.get('assetUsd') is not None]
            asset_values = [value for value in asset_values if value >= 0]

            buckets = [
                ('10万美金以上', Decimal('100000'), None),
                ('5-10万美金', Decimal('50000'), Decimal('100000')),
                ('1-5万美金', Decimal('10000'), Decimal('50000')),
                ('1500美金-1万美金', Decimal('1500'), Decimal('10000')),
                ('1500美金以下', Decimal('0'), Decimal('1500')),
                ('无入金或已出金', None, Decimal('0')),
            ]
            bucket_items = []
            for label, lower, upper in buckets:
                if label == '无入金或已出金':
                    count = sum(value <= 0 for value in asset_values)
                elif upper is None:
                    count = sum(value >= lower for value in asset_values)
                else:
                    count = sum((value > lower if lower == 0 else value >= lower) and value < upper
                                for value in asset_values)
                bucket_items.append(dict(label=label, count=count,
                                         share=(round(count / len(asset_values) * 100, 2) if asset_values else None)))
            if asset_summary.get('assetBuckets'):
                bucket_items = [dict(label=item.get('label'), count=int(decimal(item.get('count'))),
                                     share=(round(decimal(item.get('share')) * 100, 2) if item.get('share') is not None else None))
                                for item in asset_summary['assetBuckets']]
            funded_accounts = int(decimal(asset_summary.get('fundedAccounts'))) if asset_summary.get('fundedAccounts') is not None else sum(value > 0 for value in asset_values)
            activation_rate = (float(decimal(asset_summary['activationRate']) * 100)
                               if asset_summary.get('activationRate') is not None
                               else (round(funded_accounts / len(asset_values) * 100, 2) if asset_values else None))
            asset_total = sum(asset_values, Decimal('0'))
            reported_account_aum = decimal(asset_summary.get('liveAccountAumUsd')) if asset_summary.get('liveAccountAumUsd') is not None else None
            reported_total_aum = decimal(asset_summary.get('liveTotalAumUsd')) if asset_summary.get('liveTotalAumUsd') is not None else None
            sorted_assets = sorted(asset_values, reverse=True)
            top10 = sum(sorted_assets[:10], Decimal('0'))

            crm_records = []
            people, _ = context(conn, user)
            for person in people.values():
                crm_records.extend(person.get('crm', []))
            crm_ids = sorted({c['id'] for c in crm_records if c.get('id')})
            master_statuses = [str(row.get('brokerAccountStatus') or '') for row in master_rows]
            known_statuses = [value for value in master_statuses if value]
            registration_summary = asset_summary.get('registration') or {}
            registered = int(decimal(registration_summary.get('total'))) if registration_summary.get('total') is not None else (len(master_rows) if latest_master else len({p['key'] for p in people.values() if p.get('twCode')}))
            opened = asset_customer_count(latest_assets) if latest_assets else (int(decimal(registration_summary.get('opened'))) if registration_summary.get('opened') is not None else (sum(status_success(value) for value in master_statuses) if latest_master else None))
            registration_rate = (float(decimal(registration_summary['rate']) * 100)
                                 if registration_summary.get('rate') is not None
                                 else (round(opened / registered * 100, 2) if opened is not None and registered else None))

            placements = placement_rollup(conn, crm_ids)
            signed = [row for row in placements if row.get('status') != '未参与']
            converted = [row for row in placements if row.get('status') in {'已参与', '资金到账'}]
            dropped = [row for row in placements if row.get('status') == '未参与']
            pending = [row for row in placements if row.get('status') == '已锁定']

            def funnel_item(rows, amount_key='intent_amount'):
                return dict(count=len(rows), amount=money_value(sum((decimal(row.get(amount_key)) for row in rows), Decimal('0'))))

            def source_pair(value, fallback):
                if not isinstance(value, dict) or (value.get('count') is None and value.get('amount') is None):
                    return fallback
                return dict(count=(int(decimal(value.get('count'))) if value.get('count') is not None else 0),
                            amount=(money_value(value.get('amount')) if value.get('amount') is not None else 0))

            summary_onsite = asset_summary.get('onsite') or {}
            onsite = dict(
                signed=source_pair(summary_onsite.get('signed'), funnel_item(signed)),
                converted=source_pair(summary_onsite.get('converted'), funnel_item(converted, 'actual_amount')),
                dropped=source_pair(summary_onsite.get('dropped'), funnel_item(dropped)),
                pending=source_pair(summary_onsite.get('pending'), funnel_item(pending)),
            )

            def named_rollup(terms):
                selected_rows = [row for row in placements if any(term in str(row.get('batch_name') or '') for term in terms)]
                return dict(available=bool(selected_rows), signed=funnel_item(selected_rows),
                            converted=funnel_item([r for r in selected_rows if r.get('status') in {'已参与', '资金到账'}], 'actual_amount'),
                            pending=funnel_item([r for r in selected_rows if r.get('status') == '已锁定']))

            single_sign = named_rollup(('单签', '单独签约'))
            summary_single = asset_summary.get('singleSign') or {}
            if summary_single.get('consulted') is not None or summary_single.get('converted', {}).get('count') is not None:
                single_sign = dict(available=True,
                                   consulted=int(decimal(summary_single.get('consulted'))),
                                   signed=dict(count=int(decimal(summary_single.get('consulted'))), amount=0),
                                   converted=source_pair(summary_single.get('converted'), dict(count=0, amount=0)),
                                   pending=source_pair(summary_single.get('pending'), dict(count=0, amount=0)))
            promise_plan = named_rollup(('承诺购买', '购买计划', '计划'))
            summary_promise = asset_summary.get('promisePlan') or {}
            if summary_promise.get('signed', {}).get('count') is not None or summary_promise.get('converted', {}).get('count') is not None:
                promise_plan = dict(available=True,
                                    signed=source_pair(summary_promise.get('signed'), dict(count=0, amount=0)),
                                    converted=source_pair(summary_promise.get('converted'), dict(count=0, amount=0)),
                                    pending=source_pair(summary_promise.get('pending'), dict(count=0, amount=0)))

            secondary_rows = (latest_secondary or {}).get('rows', [])
            secondary_quantity = sum((decimal(row.get('secondaryQuantity')) for row in secondary_rows), Decimal('0'))
            block_quantity = sum((decimal(row.get('blockQuantity')) for row in secondary_rows), Decimal('0'))
            total_quantity = sum((decimal(row.get('quantity')) for row in secondary_rows), Decimal('0'))
            holders = sum(decimal(row.get('quantity')) > 0 for row in secondary_rows)
            secondary_holders = sum(decimal(row.get('secondaryQuantity')) > 0 for row in secondary_rows)
            block_holders = sum(decimal(row.get('blockQuantity')) > 0 for row in secondary_rows)
            xmax_summary = asset_summary.get('xmax') or {}
            if xmax_summary.get('quantity') is not None or xmax_summary.get('holders') is not None:
                holders = int(decimal(xmax_summary.get('holders'))) if xmax_summary.get('holders') is not None else holders
                total_quantity = decimal(xmax_summary.get('quantity')) if xmax_summary.get('quantity') is not None else total_quantity
                block_quantity = decimal(xmax_summary.get('blockQuantity')) if xmax_summary.get('blockQuantity') is not None else block_quantity
                block_holders = int(decimal(xmax_summary.get('blockHolders'))) if xmax_summary.get('blockHolders') is not None else block_holders
                secondary_quantity = total_quantity - block_quantity if xmax_summary.get('blockQuantity') is not None else secondary_quantity

            from backend.priority_batches import rollup_activity
            priority_rows = rollup_activity(conn, all_heads)['rows']
            priority_positive = [row for row in priority_rows if decimal(row.get('agreementAmountUsd')) > 0]
            priority_participants = {row.get('twCode') for row in priority_positive if row.get('twCode')}
            priority_pending = sum(not row.get('twCode') for row in priority_positive)
            priority_agreement = sum((decimal(row.get('agreementAmountUsd')) for row in priority_positive), Decimal('0'))
            priority_deposit = sum((decimal(row.get('depositAmount')) for row in priority_positive), Decimal('0'))
            priority_funded = sum(decimal(row.get('depositAmount')) > 0 for row in priority_positive)

            trend_dates = sorted({d for d in all_dates if d <= selected})
            trend = []
            for trend_date in trend_dates:
                master = latest(datasets['master'], trend_date)
                assets = latest(datasets['assets'], trend_date)
                secondary = latest(datasets['secondary'], trend_date)
                master_values = (master or {}).get('rows', [])
                trend_assets = asset_rows(assets)
                values = [decimal(row.get('assetUsd')) for row in trend_assets if row.get('assetUsd') is not None]
                statuses = [str(row.get('brokerAccountStatus') or '') for row in master_values]
                trend_summary = (assets or {}).get('summary') or {}
                trend_asset_aum = decimal(trend_summary.get('liveTotalAumUsd')) if trend_summary.get('liveTotalAumUsd') is not None else sum(values, Decimal('0'))
                trend.append(dict(date=trend_date, customers=(int(decimal((trend_summary.get('registration') or {}).get('total'))) if (trend_summary.get('registration') or {}).get('total') is not None else (len(master_values) if master else None)),
                                  opened=(asset_customer_count(assets) if assets else (int(decimal((trend_summary.get('registration') or {}).get('opened'))) if (trend_summary.get('registration') or {}).get('opened') is not None else (sum(status_success(value) for value in statuses) if master else None))),
                                  assetAum=money_value(trend_asset_aum) if assets else None,
                                  fundedRate=(float(decimal(trend_summary['activationRate']) * 100) if trend_summary.get('activationRate') is not None else (round(sum(value > 0 for value in values) / len(values) * 100, 2) if values else None)),
                                  secondaryQuantity=(money_value(sum((decimal(row.get('secondaryQuantity')) for row in (secondary or {}).get('rows', [])), Decimal('0'))) if secondary else None),
                                  blockQuantity=(money_value(sum((decimal(row.get('blockQuantity')) for row in (secondary or {}).get('rows', [])), Decimal('0'))) if secondary else None)))

            source_dates = {kind: source_date(latest(datasets[kind], selected)) for kind in datasets}
            insights = []
            if asset_values:
                inactive_share = next((item['share'] for item in bucket_items if item['label'] == '无入金或已出金'), 0) or 0
                insights.append(dict(tone='attention', title='资产激活仍是最大增长杠杆', body=f'无入金或已出金账户占资产快照 {inactive_share:.2f}%，入金账户为 {funded_accounts:,} 个。'))
                insights.append(dict(tone='positive', title='高净值客户贡献集中', body=f'资产排名前 10 的客户合计 ${float(top10):,.0f}，占快照资产 ${float(asset_total):,.0f} 的 {(float(top10 / asset_total * 100) if asset_total else 0):.1f}%。'))
            if registered and opened is not None:
                insights.append(dict(tone='neutral', title='开户漏斗保持可量化', body=f'开户成功 {opened:,} / 注册 {registered:,}，成功率 {opened / registered * 100:.2f}%。'))
            if onsite['signed']['count']:
                conversion_rate = onsite['converted']['count'] / onsite['signed']['count'] * 100
                insights.append(dict(tone='positive' if conversion_rate >= 30 else 'attention', title='定增现场签约转化', body=f"现场签约 {onsite['signed']['count']:,} 人，已转化 {onsite['converted']['count']:,} 人，人数转化率 {conversion_rate:.1f}%。"))

            return dict(asOf=selected, reportDates=all_dates, scope=('全量客户' if user.get('customerScope') == 'all' else '当前账号可见范围'),
                        sourceDates=source_dates,
                        kpis=dict(customers=registered, opened=opened, accountSuccessRate=registration_rate,
                                  fundedAccounts=funded_accounts if latest_assets else None, activationRate=activation_rate,
                                  assetAum=money_value(reported_total_aum if reported_total_aum is not None else asset_total) if latest_assets else None, top10Share=(round(float(top10 / asset_total * 100), 2) if asset_total else None),
                                  placementConvertedAmount=onsite['converted']['amount'], priorityAgreementUsd=money_value(priority_agreement)),
                        assets=dict(accounts=len(asset_values) if latest_assets else None, fundedAccounts=funded_accounts if latest_assets else None,
                                    activationRate=activation_rate, totalUsd=(money_value(asset_total) if latest_assets else None),
                                    reportedAccountAumUsd=(money_value(reported_account_aum) if reported_account_aum is not None else None),
                                    reportedLiveTotalUsd=(money_value(reported_total_aum) if reported_total_aum is not None else None),
                                    historicalPlacementUsd=(money_value(decimal(asset_summary['historicalPlacementUsd'])) if asset_summary.get('historicalPlacementUsd') is not None else None),
                                    top10Usd=(money_value(top10) if latest_assets else None), buckets=bucket_items if latest_assets else [],
                                    note='资产分层按上传资产明细的原始账户行统计；重复 TW 仍按原始账户记录保留。'),
                        funnel=dict(registration=dict(total=registered, opened=opened, rate=registration_rate), onsite=onsite,
                                    singleSign=single_sign, promisePlan=promise_plan),
                        holdings=dict(available=bool(latest_secondary) or bool(xmax_summary), holders=holders if (latest_secondary or xmax_summary) else None,
                                      secondaryHolders=secondary_holders if latest_secondary else None, blockHolders=block_holders if latest_secondary else None,
                                      quantity=money_value(total_quantity) if (latest_secondary or xmax_summary) else None, secondaryQuantity=money_value(secondary_quantity) if (latest_secondary or xmax_summary) else None,
                                      blockQuantity=money_value(block_quantity) if (latest_secondary or xmax_summary) else None,
                                      marketValue=(money_value(decimal(xmax_summary['marketValue'])) if xmax_summary.get('marketValue') is not None else None),
                                      price=xmax_summary.get('price'),
                                      note=('持仓市值来自资产分析表的价格口径。' if xmax_summary.get('marketValue') is not None else '持仓资料未提供估值价格，市值保持空值，不用股数推算。')),
                        priority=dict(available=bool(datasets['icc']), participants=len(priority_participants), records=len(priority_positive), pendingIdentities=priority_pending,
                                      agreementUsd=money_value(priority_agreement), depositUsd=money_value(priority_deposit), fundedAccounts=priority_funded),
                        trend=trend, insights=insights,
                        notes=['所有指标按当前账号可见范围计算。', '来源日期不一致时，报告显示各指标自己的最新快照日期。', '没有上传来源的字段不会估算。'])

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
