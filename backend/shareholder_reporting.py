"""Reconciled report metrics. Source details win over optional workbook summaries."""
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

SCHEMA = """CREATE TABLE IF NOT EXISTS shareholder_report_adjustments (
 id TEXT PRIMARY KEY, report_date TEXT NOT NULL, metric TEXT NOT NULL,
 value TEXT, reason TEXT NOT NULL, source TEXT NOT NULL,
 author_id TEXT NOT NULL, author_name TEXT NOT NULL, created_at TEXT NOT NULL)"""

METRICS = {
    'registered': '注册客户人数', 'opened': '开户成功人数', 'price': 'XMAX 估值单价（USD）',
    **{prefix + key: label + suffix
       for prefix, label in [('historical', '历史定增'), ('priority', '购买计划'), ('block', '大宗交易')]
       for key, suffix in [('SignedCount', '意向/签约人数'), ('SignedAmount', '意向/签约金额'),
                           ('ConvertedCount', '已参与人数'), ('ConvertedAmount', '实际参与金额'),
                           ('DroppedCount', '放弃人数'), ('DroppedAmount', '放弃意向金额'),
                           ('PendingCount', '待转化人数'), ('PendingAmount', '待转化意向金额')]},
}


def dec(value):
    try:
        result = Decimal(str(value if value is not None else 0))
        return result if result.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def can_edit(user):
    return user.get('customerScope') == 'all' and user.get('rolePermission') in {'admin', 'developer'}


def adjustments(conn, selected):
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM shareholder_report_adjustments WHERE report_date=? ORDER BY created_at,id', (selected,)).fetchall()]
    active = {}
    for row in rows:
        if row['value'] is None:
            active.pop(row['metric'], None)
        else:
            active[row['metric']] = row
    return active, rows


def build_report(conn, all_heads, people, user, as_of=''):
    datasets = {kind: sorted([d for d in all_heads.values() if d['kind'] == kind and d.get('head_id')],
                            key=lambda d: d['business_date']) for kind in ('master', 'assets', 'secondary', 'icc')}
    dates = sorted({d['business_date'] for items in datasets.values() for d in items})
    try:
        selected = date.fromisoformat(as_of).isoformat() if as_of else (dates[-1] if dates else date.today().isoformat())
    except ValueError as exc:
        raise HTTPException(422, '统计日期必须使用 YYYY-MM-DD 格式。') from exc
    if dates and selected < dates[0]:
        raise HTTPException(422, '统计日期早于当前已上传的最早资料。')

    def latest(kind, when):
        return next((d for d in reversed(datasets[kind]) if d['business_date'] <= when), None)

    def asset_rows(dataset):
        return [r for row in (dataset or {}).get('rows', []) for r in (row.get('sourceRows') or [row])
                if r.get('assetUsd') is not None]

    def opened_count(assets, master):
        if assets:
            return len({r['twCode'] for r in asset_rows(assets) if r.get('twCode')})
        if master:
            return len({r['twCode'] for r in master['rows'] if r.get('twCode') and
                        any(t in str(r.get('brokerAccountStatus', '')) for t in ('开户成功', '已开户', '开户完成', '完成开户', '开通成功'))})
        return None

    master, assets, holding = [latest(kind, selected) for kind in ('master', 'assets', 'secondary')]
    summary = ((assets or {}).get('summary') or {}) if user.get('customerScope') == 'all' else {}
    source_rows = asset_rows(assets)
    values = [dec(r['assetUsd']) for r in source_rows]
    net_aum = sum(values, Decimal(0))
    positive_aum = sum((v for v in values if v > 0), Decimal(0))
    registered = len({r['twCode'] for r in master['rows'] if r.get('twCode')}) if master else (
        int(dec(summary.get('registration', {}).get('total'))) if summary.get('registration', {}).get('total') is not None else None)
    opened = opened_count(assets, master)
    funded = sum(v > 0 for v in values)
    buckets = []
    definitions = [('10万以上', lambda v: v >= 100000), ('5万–10万', lambda v: 50000 <= v < 100000),
                   ('1万–5万', lambda v: 10000 <= v < 50000), ('1,500–1万', lambda v: 1500 <= v < 10000),
                   ('1,500以下', lambda v: 0 < v < 1500), ('已出金 / 未入金 / 负数', lambda v: v <= 0)]
    for label, predicate in definitions:
        group = [v for v in values if predicate(v)]
        amount = sum(group, Decimal(0))
        buckets.append(dict(label=label, count=len(group), share=round(len(group)/len(values)*100, 2) if values else None,
                            amount=float(amount), assetShare=round(float(amount/net_aum*100), 2) if net_aum else None))
    # Top customers use one TW, while the distribution above intentionally counts source accounts.
    customer_assets = {}
    for r in source_rows:
        key = r.get('twCode') or str(r.get('sourceRow'))
        customer_assets[key] = customer_assets.get(key, Decimal(0)) + dec(r['assetUsd'])
    top10 = sum(sorted(customer_assets.values(), reverse=True)[:10], Decimal(0))
    crm = {c['id']: c for p in people.values() for c in p['crm']}
    placements = []
    if crm:
        placements = [dict(r) for r in conn.execute(
            'SELECT bp.*,b.name batch_name FROM batch_participations bp JOIN placement_batches b ON b.id=bp.batch_id '
            'WHERE bp.customer_id IN (' + ','.join('?' for _ in crm) + ')', tuple(crm)).fetchall()]
    placements = [r for r in placements if '大宗' not in r['batch_name']]

    def pair(rows, field):
        return dict(count=len(rows), amount=float(sum((dec(r.get(field)) for r in rows), Decimal(0))))

    historical = dict(signed=pair(placements, 'intent_amount'),
                      converted=pair([r for r in placements if r['status'] in {'已参与', '资金到账'}], 'actual_amount'),
                      dropped=pair([r for r in placements if r['status'] == '未参与'], 'intent_amount'),
                      pending=pair([r for r in placements if r['status'] == '已锁定'], 'intent_amount'))
    onsite, single = summary.get('onsite') or {}, summary.get('singleSign') or {}
    if onsite.get('signed', {}).get('count') is not None:
        for key in historical:
            source = onsite.get(key) or {}
            extra = single.get(key) or {}
            if key == 'signed':
                extra = dict(count=single.get('consulted'), amount=None)
            # Never add a count to a dollar amount when a source amount is absent.
            historical[key] = dict(count=int(dec(source.get('count')) + dec(extra.get('count'))),
                                   amount=float(dec(source.get('amount')) + dec(extra.get('amount'))))

    from backend.priority_batches import rollup_activity
    visible_heads = {k: d for k, d in all_heads.items() if d['business_date'] <= selected}
    activities = rollup_activity(conn, visible_heads)['rows']
    visible = set(people)
    sources = {}
    for r in activities:
        key = (r.get('productBatchId', ''), r['subjectKey'])
        if key not in sources or sources[key].get('activityDate', '') <= r.get('activityDate', ''):
            sources[key] = dict(r)
    cards = {r['id']: dict(r) for r in conn.execute('SELECT * FROM priority_completion_batches').fetchall()}
    roster = [dict(r) for r in conn.execute('SELECT * FROM priority_batch_roster').fetchall()
              if r['subject_key'] in visible or any(k[1] == r['subject_key'] for k in sources)]
    products = {}
    for board, amount_field in [('priority', 'agreementAmountUsd'), ('block', 'blockAgreementAmountUsd')]:
        entries = {key: dict(intent=dec(r.get(amount_field)), actual=Decimal(0), status='following', subject=key[1]) for key, r in sources.items()}
        live_records_later = 0
        for r in roster:
            key = (r['batch_id'], r['subject_key'])
            if r['created_at'][:10] > selected:
                continue
            # These live workflow records have no historical revision stream. Do not backdate later changes.
            if r['updated_at'][:10] > selected:
                live_records_later += 1
                continue
            status = r[board+'_status']
            if status == 'completed':
                card = cards.get(r[board+'_completion_batch_id'])
                if not card or card['participation_date'] > selected:
                    status = 'following'
            entries[key] = dict(intent=dec(r[board+'_amount']), actual=dec(r[board+'_actual_amount']),
                                status=status, subject=r['subject_key'])
        selected_rows = [r for r in entries.values() if r['intent'] > 0 and r['status'] not in {'none', 'removed'}]
        def product_pair(rows, field):
            return dict(count=len({r['subject'] for r in rows}), amount=float(sum((r[field] for r in rows), Decimal(0))))
        completed = [r for r in selected_rows if r['status'] == 'completed']
        pending = [r for r in selected_rows if r['status'] not in {'completed', 'abandoned'}]
        products[board] = dict(signed=product_pair(selected_rows, 'intent'), converted=product_pair(completed, 'actual'),
                               pending=product_pair(pending, 'intent'), dropped=product_pair([r for r in selected_rows if r['status']=='abandoned'], 'intent'),
                               convertedIntentAmount=float(sum((r['intent'] for r in completed), Decimal(0))),
                               pendingIdentities=sum(r['subject'].startswith('icc:') for r in selected_rows),
                               historicalWorkflowUnavailable=live_records_later > 0)

    active, history = adjustments(conn, selected) if user.get('customerScope') == 'all' else ({}, [])
    price_source = summary.get('xmax', {})
    automatic = {'registered': registered, 'opened': opened, 'price': price_source.get('price') if (
        price_source.get('priceSourceLabel') or price_source.get('marketValue') is not None) else None}
    for prefix, product in [('historical', historical), *products.items()]:
        for key, entry in product.items():
            if key in {'signed', 'converted', 'dropped', 'pending'}:
                for suffix in ('count', 'amount'):
                    automatic[prefix+key.title()+suffix.title()] = entry[suffix]
    effective = dict(automatic)
    for metric, item in active.items():
        effective[metric] = float(dec(item['value']))
    registered, opened = effective['registered'], effective['opened']
    rate = round(opened/registered*100, 2) if registered and opened is not None else None
    for prefix, product in [('historical', historical), *products.items()]:
        for key in ('signed', 'converted', 'dropped', 'pending'):
            for suffix in ('count', 'amount'):
                value = effective.get(prefix+key.title()+suffix.title())
                product[key][suffix] = int(value) if suffix == 'count' else value
    holding_rows = (holding or {}).get('rows', [])
    totals = {key: sum((dec(r.get(key+'Quantity')) for r in holding_rows), Decimal(0)) for key in ('secondary', 'block', 'priority')}
    quantity = sum((dec(r.get('quantity')) for r in holding_rows), Decimal(0))
    price = effective.get('price')
    holding_result = dict(available=bool(holding), quantity=float(quantity) if holding else None,
                         holders=sum(dec(r.get('quantity')) > 0 for r in holding_rows) if holding else None,
                         price=float(dec(price)) if price is not None else None,
                         marketValue=float(quantity*dec(price)) if holding and price is not None else None,
                         **{key+'Quantity': float(value) if holding else None for key, value in totals.items()},
                         **{key+'Holders': sum(dec(r.get(key+'Quantity'))>0 for r in holding_rows) if holding else None for key in totals})
    checks = []
    for kind, label in [('master', '客户名单'), ('assets', '客户资产明细'), ('secondary', '分类持仓')]:
        if latest(kind, selected) is None:
            checks.append(dict(key=kind+'SourceMissing', label=f'截至本期尚无{label}来源；相关指标无法完整核对。', status='review'))
    def check(key, label, left, right, tolerance=0.01):
        delta = float(dec(left)-dec(right))
        checks.append(dict(key=key, label=label, actual=left, expected=right, difference=delta,
                           status='pass' if abs(delta) <= tolerance else 'review'))
    if assets:
        check('assetBuckets', '资产分层金额合计 = 明细净资产', sum(b['amount'] for b in buckets), float(net_aum))
        if summary.get('liveAccountAumUsd') is not None:
            check('sourceAum', '原表汇总资产与明细对照', float(net_aum), float(dec(summary['liveAccountAumUsd'])))
    if holding:
        check('holdings', '二级 + 大宗 + 购买计划 = 总持仓', float(sum(totals.values())), float(quantity), 0)
    check('historicalAmount', '历史定增：参与 + 放弃 + 待转化 = 签约金额',
          sum(historical[k]['amount'] for k in ('converted', 'dropped', 'pending')), historical['signed']['amount'])
    check('historicalCount', '历史定增：参与 + 放弃 + 待转化 = 签约人数',
          sum(historical[k]['count'] for k in ('converted', 'dropped', 'pending')), historical['signed']['count'], 0)
    for board, product in products.items():
        check(board+'IntentAmount', ('购买计划' if board == 'priority' else '大宗交易') + '：已完成意向 + 待转化意向 + 放弃意向 = 意向总额',
              product['convertedIntentAmount'] + product['pending']['amount'] + product['dropped']['amount'],
              product['signed']['amount'])
        if product['historicalWorkflowUnavailable']:
            checks.append(dict(key=board+'History', label='该时点的成交状态无法从之后修改的实时记录完整还原，请核对或提供人工修订。', status='review'))
    if registered is not None and opened is not None and opened > registered:
        checks.append(dict(key='registrationCoverage', label='开户人数超过注册名单人数，请核对来源日期与覆盖范围。', status='review'))
    if onsite.get('signed', {}).get('count') is not None and single.get('consulted') is not None:
        if single.get('signed', {}).get('amount') is None:
            checks.append(dict(key='singleSignAmount', label='原表只提供单签咨询人数，未提供单签签约金额；历史签约金额需核对，不能用人数或实际成交额代替。', status='review'))
    trend = []
    for when in [d for d in dates if d <= selected]:
        m, a, h = [latest(k, when) for k in ('master', 'assets', 'secondary')]
        t_active, _ = adjustments(conn, when) if user.get('customerScope') == 'all' else ({}, [])
        count = len({r['twCode'] for r in m['rows'] if r.get('twCode')}) if m else None
        t_opened = opened_count(a, m)
        trend.append(dict(date=when, customers=float(t_active['registered']['value']) if 'registered' in t_active else count,
                          opened=float(t_active['opened']['value']) if 'opened' in t_active else t_opened,
                          assetAum=float(sum((dec(r['assetUsd']) for r in asset_rows(a)), Decimal(0))) if a else None,
                          secondaryQuantity=float(sum((dec(r.get('secondaryQuantity')) for r in h['rows']), Decimal(0))) if h else None,
                          blockQuantity=float(sum((dec(r.get('blockQuantity')) for r in h['rows']), Decimal(0))) if h else None,
                          priorityQuantity=float(sum((dec(r.get('priorityQuantity')) for r in h['rows']), Decimal(0))) if h else None))
    p = products['priority']
    return dict(asOf=selected, reportDates=dates, scope='全量客户' if user.get('customerScope')=='all' else '当前账号可见范围',
                sourceDates={kind: (latest(kind, selected) or {}).get('business_date') for kind in datasets},
                kpis=dict(customers=registered, opened=opened, accountSuccessRate=rate, assetAum=float(net_aum) if assets else None,
                          placementConvertedAmount=historical['converted']['amount']),
                assets=dict(accounts=len(values) if assets else None, totalUsd=float(net_aum) if assets else None,
                            positiveUsd=float(positive_aum) if assets else None, fundedAccounts=funded if assets else None,
                            activationRate=round(funded/len(values)*100,2) if values else None,
                            top10Usd=float(top10) if assets else None, top10Share=round(float(top10/net_aum*100),2) if net_aum else None, buckets=buckets if assets else []),
                funnel=dict(registration=dict(total=registered, opened=opened, rate=rate), onsite=historical),
                products=products, historical=historical, holdings=holding_result,
                priority=dict(participants=p['signed']['count'], agreementUsd=p['signed']['amount'], pendingIdentities=p['pendingIdentities']),
                trend=trend, checks=checks, adjustments=list(active.values()),
                correctionOptions=[dict(key=k, label=v, automatic=automatic.get(k), effective=effective.get(k)) for k,v in METRICS.items()],
                correctionHistory=history if can_edit(user) else [], canEdit=can_edit(user),
                notes=['资产净额包含零及负数；分层按原始账户计，开户人数按有数值资产记录的 TW 去重。',
                       '持仓以该时点最新完整持仓表为准，三类独立统计；持仓人数总计按 TW 去重，分类人数可交叉。',
                       '购买计划与大宗的意向金额、实际成交金额分别统计；差额不代表待转化金额。',
                       '历史定增已停售，仅作留档，不加回当前客户资产。历史时点的实时业务状态若不可还原，将标记待核对。',
                       '估值价格为来源或人工提供的报告价格，非实时报价；缺失时不计算市值。'])
