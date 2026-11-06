"""Business retirement, three-way holdings and auditable shareholder reports."""
import base64
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from test_priority import client, source, publish, master, assets, icc
from backend import main, business_policy
from backend.priority_import import parse


def test_shareholder_report_default_is_management_only():
    assert main.default_shareholder_report_permission({'rolePermission': 'manager'}) is False
    assert main.default_shareholder_report_permission({'rolePermission': 'supervisor'}) is True
    assert main.default_shareholder_report_permission({'rolePermission': 'admin'}) is True


def test_report_endpoint_denies_business_manager_without_explicit_grant(client):
    client.test_user.update(rolePermission='manager')
    assert client.get('/api/workspace/dashboard/report').status_code == 403


def holding_file():
    return source('持仓.xlsx', [('SH', [
        (1, dict(A='code', B='qty', C='client_acc_name')),
        (2, dict(A='TW2026001', B=100, C='JIA')),
        (3, dict(A='TW2026002', B=50, C='YI')),
        (4, dict(A='二级：', B=150)),
        (5, dict(A='TW2026001', B=20, C='JIA')),
        (6, dict(A='大宗累计：', B=20)),
        (7, dict(A='TW2026001', B=30, C='JIA')),
        (8, dict(A='TW2026003', B=40, C='BING')),
        (9, dict(A='协议累计：', B=70)),
    ])])


def test_three_sections_with_footer_totals_and_overlapping_tw(client):
    f = holding_file()
    rows = parse(base64.b64decode(f['contentBase64']), f['filename'], '2026-10-30', [])[0]['rows']
    assert sum(Decimal(r['quantity']) for r in rows) == 240
    first = next(r for r in rows if r['twCode'] == 'TW2026001')
    assert [first[k] for k in ('secondaryQuantity','blockQuantity','priorityQuantity')] == ['100','20','30']
    assert set(first['holdingTypes']) == {'secondary','block','priority'}
    publish(client, [f], date='2026-10-30')
    result = client.get('/api/workspace/dashboard/report').json()['holdings']
    assert result['holders'] == 3  # Sum of category holders is five, with overlaps.
    assert [result[k+'Holders'] for k in ('secondary','block','priority')] == [2,1,2]
    assert result['quantity'] == result['secondaryQuantity'] + result['blockQuantity'] + result['priorityQuantity']
    assert result['marketValue'] is None
    directory = client.get('/api/workspace/customers').json()['items']
    assert next(r for r in directory if r['twCode']=='TW2026001')['priorityQuantity'] == '30'


def test_purchase_plan_total_mismatch_is_not_silently_accepted():
    f = source('持仓.xlsx', [('SH', [(1,dict(A='code',B='qty',C='name')),
        (2,dict(A='购买计划持仓')),(3,dict(A='TW1',B=5,C='甲')),(4,dict(A='协议累计：',B=6))])])
    with pytest.raises(HTTPException, match='购买计划持仓明细股数与表尾合计不一致'):
        parse(base64.b64decode(f['contentBase64']), f['filename'], '2026-10-30', [])


def test_net_assets_negative_accounts_and_consistent_opening_rate(client):
    f = source('资产.xlsx', [('资产', [
        (1,dict(A='客户编码',B='客户姓名',C='客户权益资产（USD）')),
        (2,dict(A='TW2026001',B='甲',C=100,F='开户成功率',G='0.17')),
        (3,dict(A='TW2026001',B='甲',C=-20)),
        (4,dict(A='TW202609039',B='董方',C=0)),
    ])])
    publish(client, [master(), f])
    report = client.get('/api/workspace/dashboard/report').json()
    assert report['assets']['totalUsd'] == 80
    assert report['assets']['accounts'] == 3
    assert report['kpis']['opened'] == 2
    assert report['kpis']['accountSuccessRate'] == 100
    assert report['assets']['buckets'][-1]['amount'] == -20
    assert sum(b['count'] for b in report['assets']['buckets']) == 3
    assert report['trend'][-1]['opened'] == 2


def test_report_corrections_date_permissions_concurrency_and_restore(client):
    publish(client, [master(), assets(), holding_file()])
    path = '/api/workspace/dashboard/report/adjustments'
    payload = dict(reportDate='2026-09-11',metric='price',value='6.42',reason='财务核对',source='估值表',expectedId='')
    client.test_user.update(rolePermission='manager')
    assert client.post(path,json=payload).status_code == 403
    client.test_user.update(rolePermission='developer',canViewShareholderReport=False)
    assert client.post(path,json=payload).status_code == 403
    assert client.get('/api/workspace/dashboard/report').status_code == 403
    client.test_user.update(canViewShareholderReport=True)
    assert client.post(path,json={**payload,'value':'NaN'}).status_code == 422
    assert client.post(path,json={**payload,'metric':'opened','value':'1.5'}).status_code == 422
    response = client.post(path,json=payload)
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    assert client.post(path,json=payload).status_code == 409
    report = client.get('/api/workspace/dashboard/report').json()
    assert report['holdings']['price'] == 6.42
    assert report['holdings']['marketValue'] == 1540.8
    assert report['adjustments'][0]['source'] == '估值表'
    assert client.get('/api/workspace/dashboard/report?as_of=2026-09-12').json()['holdings']['price'] is None
    restored = client.post(path,json={**payload,'value':None,'expectedId':identifier})
    assert restored.status_code == 201, restored.text
    report = client.get('/api/workspace/dashboard/report').json()
    assert report['holdings']['marketValue'] is None
    assert not report['adjustments'] and len(report['correctionHistory']) == 2
    with main.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='shareholder.report.adjusted'").fetchone()[0] == 2


def test_report_pdf_download_rechecks_permission_and_returns_pdf(client):
    publish(client, [master(), assets(), holding_file()])
    response = client.get('/api/workspace/dashboard/report.pdf')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/pdf')
    assert response.headers['cache-control'] == 'no-store'
    assert response.content.startswith(b'%PDF-')
    assert 'filename*=' in response.headers['content-disposition']
    client.test_user['canViewShareholderReport'] = False
    denied = client.get('/api/workspace/dashboard/report.pdf')
    assert denied.status_code == 403


def test_pdf_fails_clearly_when_chinese_font_cannot_be_embedded(client, monkeypatch, tmp_path):
    # A configured but unsupported font must not silently produce missing text.
    font = tmp_path / 'unsupported.otf'
    font.write_bytes(b'not a TrueType font')
    monkeypatch.setenv('JY_REPORT_FONT', str(font))
    response = client.get('/api/workspace/dashboard/report.pdf')
    assert response.status_code == 503
    assert '中文字体无法嵌入' in response.json()['detail']


def test_history_keeps_later_dates_and_missing_customers_do_not_keep_old_holdings(client):
    publish(client,[holding_file()],date='2026-10-23')
    later=source('最新持仓.xlsx',[('SH',[(1,dict(A='code',B='qty',C='name')),(2,dict(A='TW2026002',B=5,C='YI'))])])
    publish(client,[later],date='2026-10-30')
    early=client.get('/api/workspace/dashboard/report?as_of=2026-10-23').json()
    assert early['holdings']['quantity'] == 240
    assert '2026-10-30' in early['reportDates']
    latest=client.get('/api/workspace/dashboard/report').json()
    assert latest['holdings']['holders'] == 1 and latest['holdings']['quantity'] == 5
    directory = client.get('/api/workspace/customers').json()['items']
    absent = next(r for r in directory if r['twCode'] == 'TW2026001')
    assert absent['xmaxQuantity'] == '0' and not absent['hasXmax']
    card = client.get('/api/workspace/card', params={'key': absent['key']}).json()
    assert card['xmaxQuantity'] == '0'
    assert card['secondary'][-1]['date'] == '2026-10-30'
    assert card['secondary'][-1]['snapshotMissing']


def test_report_without_source_is_flagged_for_review_not_certified(client):
    report = client.get('/api/workspace/dashboard/report').json()
    assert report['assets']['totalUsd'] is None
    assert report['assets']['top10Usd'] is None
    assert report['holdings']['quantity'] is None
    assert {c['key'] for c in report['checks'] if c['status'] == 'review'} >= {
        'masterSourceMissing', 'assetsSourceMissing', 'secondarySourceMissing'}


def test_archived_placement_is_readonly_but_shared_funding_and_active_products_work(client,monkeypatch):
    publish(client,[master(),icc()])
    batch=client.post('/api/batches',json=dict(name='历史定增',closeDate='2026-05-01',status='已完成')).json()['batch']
    customer=client.get('/api/customers').json()['items'][0]
    relation=client.post(f'/api/batches/{batch["id"]}/participations',json=dict(customerId=customer['id'],status='已参与',actualAmount=10)).json()['participation']
    monkeypatch.setattr(business_policy,'PLACEMENT_ARCHIVED',True)
    assert client.post('/api/batches',json=dict(name='不允许新建')).status_code == 409
    assert client.patch('/api/batches/'+batch['id'],json=dict(name='改历史')).status_code == 409
    assert client.patch('/api/batch-participations/'+relation['id'],json=dict(actualAmount=12)).status_code == 409
    assert client.delete('/api/batch-participations/'+relation['id']).status_code == 409
    assert client.patch('/api/customers/'+customer['id'],json=dict(version=customer['version'],actualAmount=12)).status_code == 409
    assert client.patch('/api/customers/'+customer['id'],json=dict(version=customer['version'],fundedAmount=80)).status_code == 200
    assert client.get('/api/batches/'+batch['id']+'/participations').status_code == 200
    for board in ('priority','block'):
        response=client.post('/api/priority-inferior/participation-batches',json=dict(board=board,name=board+'新发车',participationDate='2026-11-05'))
        assert response.status_code == 201, response.text


def test_new_icc_header_and_block_followup_keep_correct_business(client):
    f=source('ICC.xlsx',[('2026.10.30',[(1,dict(A='客户姓名',B='购买计划金额',C='大宗交易金额',D='保险经纪人')),
        (2,dict(A='甲',B=100,C=200,D='经纪人'))])])
    publish(client,[master(),f],date='2026-10-30',batches=['2026-10-30'])
    response=client.post('/api/workspace/followups',json=dict(subjectKey='tw:TW2026001',business='block',batchKey='2026-10-30',content='跟进大宗'))
    assert response.status_code == 201,response.text
    rows=client.get('/api/workspace/followups').json()['items']
    assert rows[0]['business']=='block'


def test_completed_product_amount_is_separate_from_intention(client):
    today=date.today().isoformat()
    publish(client,[master(),icc(amount='100')],date=today)
    batch=client.post('/api/priority-inferior/product-batches',json=dict(name='活动来源',participationDate=today,activityDates=['2026-09-09'])).json()['id']
    row=client.get('/api/priority-inferior/product-batches').json()['items'][0]['rosterRows'][0]
    card=client.post('/api/priority-inferior/participation-batches',json=dict(board='priority',name='第一车',participationDate=today)).json()['id']
    response=client.patch(f'/api/priority-inferior/product-batches/{batch}/roster/{row["id"]}',json=dict(subjectKey=row['subjectKey'],board='priority',status='completed',priorityActualAmount='90',completionBatchId=card))
    assert response.status_code == 200,response.text
    product=client.get('/api/workspace/dashboard/report?as_of='+today).json()['products']['priority']
    assert product['signed']['amount']==100 and product['converted']['amount']==90
    assert product['pending']['amount']==0
    report=client.get('/api/workspace/dashboard/report?as_of='+today).json()
    assert next(c for c in report['checks'] if c['key']=='priorityIntentAmount')['status']=='pass'
