"""Business regression tests; generated workbooks contain no real customer data."""
import base64
import io
import os
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

# Safe standalone collection; fixture also replaces the database for each test.
os.environ.setdefault('CUSTOMER_DB_PATH', str(Path(tempfile.mkdtemp()) / 'priority-tests.db'))
os.environ.setdefault('CRM_DEMO_MODE', 'true')
from backend import main
from backend.priority_import import HEADERS, parse
from backend.priority_inferior import SCHEMA


def xlsx(sheets):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="r{i}"/>' for i,(name,_) in enumerate(sheets,1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships>'+''.join(f'<Relationship Id="r{i}" Target="worksheets/s{i}.xml"/>' for i in range(1,len(sheets)+1))+'</Relationships>')
        for i,(_,rows) in enumerate(sheets,1):
            xml=[]
            for rownum, cells in rows:
                body=''
                for col,value in cells.items():
                    if value is None:
                        continue
                    body+=f'<c r="{col}{rownum}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                xml.append(f'<row r="{rownum}">{body}</row>')
            z.writestr(f'xl/worksheets/s{i}.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(xml)+'</sheetData></worksheet>')
        z.writestr('xl/styles.xml','<broken-styles/>')
    return output.getvalue()


def source(name, sheets):
    return dict(filename=name, contentBase64=base64.b64encode(xlsx(sheets)).decode())


def master():
    return source('master.xlsx', [('全部', [(1,dict(A='客户姓名',B='是否完成开户',C='备注')),
        (2,dict(A='甲',B='已开户',C='TW2026001')),(4,dict(A='董方',B='待初审',C='TW202609039'))]),
        ('不读取',[(1,dict(A='无效内容'))])])


def icc(amount='100', owner='演示顾问', batch='2026.09.09', name='甲', notes='', row=5):
    return source('icc.xlsx',[(batch,[(2,dict(zip('ABCDEFGHIJKL',HEADERS))),
        (row,dict(A=1,B=name,C='已提交',F='外部经纪人',I='☑',J=amount,K=owner,L=notes)),
        (50,dict(A=1,I='☐'))]), ('2026.08.01',[(1,dict(B='这张不读取',J='错误金额'))])])


def assets(date='2026.09.11', amount='20'):
    return source(f'资产{date}.xlsx',[('Sheet1',[(1,dict(A='客户编码',B='客戶姓名',C='客户权益资产（基币为USD，汇率@7.8）')),
        (2,dict(A='TW2026001',B='甲',C=amount,E='忽略')), (8,dict(A='TW2026001',B='甲',C='6.37'))])])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',tmp_path/'db.sqlite')
    monkeypatch.setattr(main,'DATABASE_URL','')
    monkeypatch.setattr(main,'MUSKZOOM_DB_PATH',tmp_path/'missing-platform.db')
    monkeypatch.setattr(main,'MUSKZOOM_IDENTITY_URL','')
    monkeypatch.setattr(main,'CRM_DEMO_MODE',True)
    main.init_db()
    with main.db() as c:
        for sql in SCHEMA:c.execute(sql)
    user={'id':'demo-admin','name':'管理员','team':'系统后台','rolePermission':'developer',
          'customerScope':'all','canImportCustomers':True,'canManageAdvisorBindings':True,'canManageAssignments':True}
    main.app.dependency_overrides[main.current_user]=lambda:user
    with TestClient(main.app) as c:
        c.test_user=user
        yield c
    main.app.dependency_overrides.clear()


def preview(client, files, date='2026-09-11', batches=None):
    r=client.post('/api/priority-inferior/imports/preview',json={'asOf':date,'batchDates':batches or ['2026-09-09'],'files':files})
    assert r.status_code==200,r.text
    return r.json()


def publish(client, files, date='2026-09-11', batches=None):
    p=preview(client,files,date,batches)
    r=client.post(f"/api/priority-inferior/imports/{p['id']}/commit",json={})
    assert r.status_code==200,r.text
    return p


def test_real_rows_selection_and_all_fields():
    file=icc(name='董芳')
    d=parse(base64.b64decode(file['contentBase64']),'icc.xlsx','2026-09-11',['2026-09-09'])[0]
    assert len(d['rows'])==1
    r=d['rows'][0]
    assert r['sourceRow']==5 and r['batchDate']=='2026-09-09'
    assert r['insuranceBroker']=='外部经纪人' and r['jiaoyangOwner']=='演示顾问'
    assert r['raw']['J']=='100'
    a=assets()
    d=parse(base64.b64decode(a['contentBase64']),a['filename'],'2026-09-11',[])[0]
    assert len(d['rows'])==1 and d['rows'][0]['assetUsd']=='26.37'
    assert [r['sourceRow'] for r in d['rows'][0]['sourceRows']]==[2,8]
    assert 'E' not in d['rows'][0]['raw']


def test_complete_upload_duplicate_and_unmatched_preserved(client):
    p=publish(client,[icc(name='董芳'),master(),assets()])
    result=client.get('/api/priority-inferior/overview').json()
    assert result['summary']['participants']==1 and result['summary']['agreementUsd']=='100'
    assert result['summary']['assetsUsd']=='26.37'
    r=client.get('/api/priority-inferior/customers/TW202609039').json()['icc'][0]
    assert r['canonicalName']=='董方' and r['customerName']=='董芳'
    assert client.post(f"/api/priority-inferior/imports/{p['id']}/commit",json={}).json()['alreadyCommitted']
    duplicate=publish(client,[master(),icc(name='董芳'),assets()])
    assert all(d['duplicate'] for d in duplicate['datasets'])
    publish(client,[icc(name='未开户访客',batch='2026.09.16')],date='2026-09-18',batches=['2026-09-16'])
    result=client.get('/api/priority-inferior/overview').json()['summary']
    assert result['participants']==1 and result['pending']==1 and result['unmatchedParticipations']==1
    with main.db() as c:
        assert c.execute('SELECT count(*) FROM customers').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM batch_participations').fetchone()[0]==0
        assert len(c.execute('SELECT * FROM priority_revisions').fetchall())==4
        assert 'contentBase64' in c.execute('SELECT sources_json FROM priority_uploads WHERE id=?',(p['id'],)).fetchone()[0]


def test_revisions_blank_carry_stale_preview_rollback_and_history(client):
    p=publish(client,[master(),icc(notes='保留此备注'),assets()])
    stale=preview(client,[icc(amount='300')])
    revised=publish(client,[icc(amount='200',owner='演示顾问二',notes='')])
    r=client.post(f"/api/priority-inferior/imports/{stale['id']}/commit",json={})
    assert r.status_code==409
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    assert d['rows'][0]['notes']=='保留此备注'
    assert d['rows'][0]['carriedFields']['notes']['revision']
    assert client.post(f"/api/priority-inferior/imports/{p['id']}/rollback",json={}).status_code==409
    assert client.post(f"/api/priority-inferior/imports/{revised['id']}/rollback",json={}).status_code==200
    assert client.get('/api/priority-inferior/overview').json()['summary']['agreementUsd']=='100'
    # A later dated batch preserves its own owner and does not double count people.
    publish(client,[icc(batch='2026.09.16',owner='演示顾问二')],date='2026-09-18',batches=['2026-09-16'])
    result=client.get('/api/priority-inferior/overview').json()
    assert result['summary']['participants']==1 and result['summary']['participationRecords']==2
    hist=client.get('/api/priority-inferior/customers/TW2026001').json()['icc']
    assert [r['jiaoyangOwner'] for r in hist]==['演示顾问二','演示顾问']
    assert client.get('/api/priority-inferior/overview?asOf=2026-09-11').json()['summary']['participationRecords']==1


def test_edit_resolution_clear_fields_conflict_and_undo(client):
    publish(client,[master(),icc(name='别名',notes='原始备注')])
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    url='/api/priority-inferior/batches/2026-09-09/records/'+d['rows'][0]['recordKey']
    data=dict(expectedRevision=d['revision'],changes={'twCode':'TW2026001','notes':'','insuranceBroker':'更正经纪人'},reason='人工核对')
    r=client.patch(url,json=data)
    assert r.status_code==200,r.text
    assert client.patch(url,json=data).status_code==409
    result=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert result['twCode']=='TW2026001' and result['notes']==''
    assert result['raw']['L']=='原始备注' and result['insuranceBroker']=='更正经纪人'
    assert client.post('/api/priority-inferior/imports/'+r.json()['jobId']+'/rollback',json={}).status_code==200
    assert client.get('/api/priority-inferior/overview').json()['summary']['pending']==1


def test_bad_numbers_invalid_dates_and_qty_total(client):
    payload=dict(asOf='2026-09-11',batchDates=['2026-09-09'],files=[icc(amount='NaN')])
    assert client.post('/api/priority-inferior/imports/preview',json=payload).status_code==422
    payload['files']=[assets(date='2026.09.18')]
    assert client.post('/api/priority-inferior/imports/preview',json=payload).status_code==422
    secondary=source('二级20260911.xlsx',[('SH',[(1,dict(A='NO.',B='qty',C='client_acc_name')),
        (2,dict(A='TW2026001',B=12,C='JIA')), (3,dict(A='TW202609039',B=0,C='DONG')),
        (6,dict(B=12))])])
    publish(client,[master(),secondary])
    result=client.get('/api/priority-inferior/overview').json()
    assert result['summary']['secondaryParticipants']==1 and result['summary']['quantity']=='12'
    zero=next(c for c in result['customers'] if c['twCode']=='TW202609039')
    assert zero['quantity']=='0' and zero['secondaryPresent'] and not zero['isSecondary']
    with main.db() as c:
        assert not c.execute("SELECT * FROM priority_uploads WHERE status='preview'").fetchall()


def test_permissions_on_totals_rows_history_raw_and_mutations(client):
    p=publish(client,[master(),icc(owner='演示顾问'),assets()])
    client.test_user.update(id='demo-manager-2',name='演示顾问二',team='演示一组',customerScope='self',canImportCustomers=False,canManageAdvisorBindings=False)
    assert client.get('/api/priority-inferior/overview').json()['summary']['participants']==0
    assert client.get('/api/priority-inferior/batches/2026-09-09/participations').status_code==404
    assert client.get('/api/priority-inferior/customers/TW2026001').status_code==404
    assert client.get('/api/priority-inferior/imports').status_code==403
    assert client.post(f"/api/priority-inferior/imports/{p['id']}/commit",json={}).status_code==403
    client.test_user.update(id='demo-manager',name='演示顾问')
    assert client.get('/api/priority-inferior/overview').json()['summary']['participants']==1
    result=client.get('/api/priority-inferior/customers/TW2026001').json()
    assert result['assets'][0]['assetUsd']=='26.37'
    assert client.get('/api/priority-inferior/revisions/'+result['icc'][0]['revision']).status_code==403


def test_new_snapshot_not_added_to_old_and_missing_not_zero(client):
    publish(client,[master(),assets()])
    publish(client,[assets(date='2026.09.18',amount='30')],date='2026-09-18')
    result=client.get('/api/priority-inferior/overview').json()
    assert result['summary']['assetsUsd']=='36.37'
    assert client.get('/api/priority-inferior/overview?asOf=2026-09-11').json()['summary']['assetsUsd']=='26.37'
    assert next(c for c in result['customers'] if c['twCode']=='TW202609039')['assetUsd'] is None
    history=client.get('/api/priority-inferior/customers/TW2026001').json()['assets']
    assert len(history)==2 and history[0]['date']=='2026-09-18'


def test_reupload_preserves_manual_identity_and_corrections(client):
    publish(client,[master(),icc(name='别名',notes='原始备注')])
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    r=client.patch('/api/priority-inferior/batches/2026-09-09/records/'+d['rows'][0]['recordKey'],json={
        'expectedRevision':d['revision'],'changes':{'twCode':'TW2026001','notes':'人工补充'},'reason':'核对'})
    assert r.status_code==200,r.text
    p=publish(client,[icc(name='别名',notes='原始备注')])
    assert p['datasets'][0]['duplicate']
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['notes']=='人工补充' and row['twCode']=='TW2026001'
    publish(client,[icc(name='别名',amount='200')])
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['twCode']=='TW2026001' and row['notes']=='人工补充' and row['agreementAmountUsd']=='200'


def test_reordered_rows_do_not_reuse_other_clients_record_keys(client):
    publish(client,[master(),icc()])
    before=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    key=before['rows'][0]['recordKey']
    replacement=source('revised.xlsx',[('2026.09.09',[(2,dict(zip('ABCDEFGHIJKL',HEADERS))),
        (5,dict(B='新访客',F='外部经纪人',J='50')),
        (9,dict(B='甲',F='外部经纪人',J='200'))])])
    publish(client,[replacement])
    rows=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows']
    assert len({r['recordKey'] for r in rows})==2
    assert next(r for r in rows if r['twCode']=='TW2026001')['recordKey']==key


def service_icc():
    f=icc(amount='',owner='')
    # An unchecked row is ordinary service, not an explicit rejection.
    content=xlsx([('2026.09.09',[(2,dict(zip('ABCDEFGHIJKL',HEADERS))),
                     (5,dict(B='甲',F='外部经纪人',I='☐'))])])
    f['contentBase64']=base64.b64encode(content).decode()
    return f


def bind_service(client, owner='demo-manager'):
    payload={'broker':'外部经纪人','ownerId':owner,'active':True,'reason':'建立服务关系'}
    r=client.post('/api/priority-inferior/bindings/preview',json=payload)
    assert r.status_code==200,r.text
    p=r.json()
    r=client.post('/api/priority-inferior/bindings/save',json={**payload,'token':p['token']})
    assert r.status_code==200,r.text
    return p


def test_binding_existing_new_clients_permissions_and_manual_protection(client):
    publish(client,[master(),service_icc(),assets()])
    p=bind_service(client)
    assert p['affected'][0]['serviceOwner']=='演示顾问' and p['affected'][0]['changed']
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['serviceOwner']=='演示顾问' and row['jiaoyangOwner']==''
    # Changing a default takes effect for existing auto-managed clients.
    bind_service(client,'demo-manager-2')
    assert client.get('/api/priority-inferior/overview').json()['customers'][0]['serviceOwner']=='演示顾问二'
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    url='/api/priority-inferior/batches/2026-09-09/records/'+d['rows'][0]['recordKey']
    r=client.patch(url,json={'expectedRevision':d['revision'],'changes':{'jiaoyangOwner':'演示顾问'},'reason':'领导指派'})
    assert r.status_code==200,r.text
    p=bind_service(client,'demo-manager-2')
    assert not p['affected'][0]['changed'] and p['affected'][0]['assignmentMode']=='manual'
    client.test_user.update(id='demo-manager-2',customerScope='self')
    assert client.get('/api/priority-inferior/overview').json()['customers']==[]
    assert client.post('/api/priority-inferior/bindings/preview',json={'broker':'外部经纪人','ownerId':'demo-manager-2','reason':'越权'}).status_code==403
    client.test_user.update(id='demo-manager')
    assert client.get('/api/priority-inferior/customers/TW2026001').status_code==200


def test_signed_intent_never_default_and_stale_binding_preview(client):
    publish(client,[master(),icc(amount='',owner='')]) # checked but amount missing
    p=bind_service(client)
    assert p['affected'][0]['assignmentMode']=='pending_manual'
    assert not p['affected'][0]['serviceOwnerId']
    payload={'broker':'外部经纪人','ownerId':'demo-manager-2','reason':'改绑定'}
    preview=client.post('/api/priority-inferior/bindings/preview',json=payload).json()
    publish(client,[icc(amount='300',owner='演示顾问')])
    assert client.post('/api/priority-inferior/bindings/save',json={**payload,'token':preview['token']}).status_code==409


def test_explicit_intent_blocks_default_and_survives_import(client):
    publish(client,[master(),service_icc()]);bind_service(client)
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    r=client.patch('/api/priority-inferior/batches/2026-09-09/records/'+d['rows'][0]['recordKey'],json={'expectedRevision':d['revision'],'changes':{'participationIntent':'interested'},'reason':'客户表达意向'})
    assert r.status_code==200,r.text
    publish(client,[service_icc()])
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['assignmentMode']=='pending_manual'


def test_legacy_default_apply_preview_preserves_existing_and_placement(client):
    user=client.test_user
    with main.db() as conn:
        for name,owner,intent in [('普通',main.UNASSIGNED_OWNER,'未确认'),('意向',main.UNASSIGNED_OWNER,'有意向'),('人工',main.DEMO_USERS['manager2'],'未确认')]:
            main.create_customer_record(conn,{'name':name,'hkAdvisor':'外部经纪人','intentStatus':intent},owner,user)
    rule={'honganAdvisor':'外部经纪人','jiaoyangAdvisor':'演示顾问','jiaoyangAdvisorId':'demo-manager','customerType':'non_placement','assignmentMode':'default','active':True,'notes':''}
    r=client.post('/api/advisor-bindings',json=rule)
    assert r.status_code==201,r.text
    p=client.post('/api/advisor-bindings/apply-existing/preview').json()
    assert p['count']==1
    assert next(i for i in p['items'] if i['name']=='普通')['apply']
    r=client.post('/api/advisor-bindings/apply-existing/commit',json={'token':p['token']})
    assert r.status_code==200 and r.json()['assignedCount']==1,r.text
    assert client.post('/api/advisor-bindings/apply-existing/commit',json={'token':p['token']}).status_code==409
    with main.db() as conn:
        rows={r['name']:r['owner_id'] for r in conn.execute('SELECT name,owner_id FROM customers').fetchall()}
    assert rows=={'普通':'demo-manager','意向':'unassigned','人工':'demo-manager-2'}


def test_binding_disable_and_inactive_account_remove_auto_access(client, monkeypatch):
    publish(client,[master(),service_icc()]);bind_service(client)
    payload={'broker':'外部经纪人','ownerId':'demo-manager','active':False,'reason':'停止服务绑定'}
    p=client.post('/api/priority-inferior/bindings/preview',json=payload).json()
    assert p['affected'][0]['assignmentReason']=='绑定已停用'
    assert client.post('/api/priority-inferior/bindings/save',json={**payload,'token':p['token']}).status_code==200
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert not row['serviceOwnerId']
    bind_service(client)
    original=main.DEMO_USERS['manager']
    monkeypatch.setitem(main.DEMO_USERS,'manager',{**original,'active':False})
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert not row['serviceOwnerId'] and '停用' in row['assignmentReason']


def workspace_note(client, key='tw:TW2026001', business='priority', **extra):
    return client.post('/api/workspace/followups',json=dict(subjectKey=key,business=business,
            content='客户已收到资料，下周联系',nextAction='确认资料',**extra))


def test_workspace_notes_batch_validation_completion_and_no_placement_change(client):
    publish(client,[master(),icc()])
    assert workspace_note(client,batchKey='2026-01-01').status_code==422
    assert workspace_note(client,business='placement').status_code==404
    r=workspace_note(client,batchKey='2026-09-09',nextFollowupAt='2026-09-20T09:00:00+08:00')
    assert r.status_code==201,r.text
    rows=client.get('/api/workspace/followups').json()['items']
    assert len(rows)==1 and rows[0]['next_followup_at']=='2026-09-20T01:00:00+00:00'
    assert client.post('/api/workspace/followups/'+r.json()['id']+'/complete').status_code==200
    assert client.get('/api/workspace/followups').json()['items'][0]['completed_at']
    with main.db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==0


def test_workspace_pending_identity_notes_follow_confirmation(client):
    publish(client,[master(),icc(name='待核实姓名')])
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    record=d['rows'][0]
    anchor='icc:2026-09-09/'+record['recordKey']
    assert workspace_note(client,key=anchor).status_code==201
    r=client.patch('/api/priority-inferior/batches/2026-09-09/records/'+record['recordKey'],json={
        'expectedRevision':d['revision'],'changes':{'twCode':'TW2026001'},'reason':'核对客户编号'})
    assert r.status_code==200,r.text
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert len(card['followups'])==1
    assert card['followups'][0]['subject_key']=='tw:TW2026001'


def test_workspace_product_permissions_and_reassignment(client):
    publish(client,[master(),service_icc()]);bind_service(client)
    with main.db() as conn:
        main.create_customer_record(conn,{'name':'甲','customerCode':'TW2026001'},main.DEMO_USERS['manager2'],client.test_user)
        c=conn.execute('SELECT id FROM customers').fetchone()
        conn.execute('UPDATE customers SET customer_code=? WHERE id=?',('TW2026001',c['id']))
    assert workspace_note(client,business='placement').status_code==201
    assert workspace_note(client,business='service').status_code==201
    assert workspace_note(client).status_code==201
    admin=dict(client.test_user)
    client.test_user.update(id='demo-manager',customerScope='self',rolePermission='manager',team='演示一组')
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert card['crm']==[] and {r['business'] for r in card['followups']}=={'service','priority'}
    assert workspace_note(client,business='placement').status_code==404
    client.test_user.update(id='demo-manager-2')
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert card['priority']==[] and {r['business'] for r in card['followups']}=={'service','placement'}
    assert workspace_note(client).status_code==404
    client.test_user.update(admin);bind_service(client,'demo-manager-2')
    client.test_user.update(id='demo-manager',customerScope='self',rolePermission='manager',team='演示一组')
    assert client.get('/api/workspace/followups').json()['items']==[]


def test_workspace_preserves_legacy_classification_and_customer_stage(client):
    with main.db() as conn:
        main.create_customer_record(conn,{'name':'老客户'},main.DEMO_USERS['manager'],client.test_user)
        c=dict(conn.execute('SELECT * FROM customers').fetchone())
        conn.execute('INSERT INTO followups VALUES (?,?,?,?,?,?,?,?,?,?,?)',('old',c['id'],'demo-admin','管理员','电话','旧跟进','','',None,c['stage'],main.now_iso()))
    assert workspace_note(client,key='crm:'+c['id'],business='service').status_code==201
    result=client.get('/api/workspace/card',params={'key':'crm:'+c['id']})
    assert result.status_code==200,result.text
    assert {r['business'] for r in result.json()['followups']}=={'legacy','service'}
    with main.db() as conn:
        after=dict(conn.execute('SELECT * FROM customers WHERE id=?',(c['id'],)).fetchone())
    assert after['stage']==c['stage'] and after['placement_status']==c['placement_status']


def test_workspace_input_validation_and_foreign_completion(client):
    publish(client,[master(),icc()])
    r=workspace_note(client,nextFollowupAt='2026-09-20T12:00:00')
    assert r.status_code==422
    assert client.post('/api/workspace/followups',json={'subjectKey':'tw:TW2026001','business':'priority','content':'  '}).status_code==422
    r=workspace_note(client,nextFollowupAt='2026-09-20T12:00:00Z')
    assert r.status_code==201
    client.test_user.update(id='stranger',customerScope='self',rolePermission='manager',team='其他组')
    assert client.post('/api/workspace/followups/'+r.json()['id']+'/complete').status_code==404
    assert client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).status_code==404


def test_workspace_followups_protect_activity_rollback_and_crm_import(client):
    p=publish(client,[master(),icc()])
    assert workspace_note(client).status_code==201
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/rollback').status_code==409
    with main.db() as conn:
        main.create_customer_record(conn,{'name':'普通服务客户'},main.DEMO_USERS['manager'],client.test_user)
        c=dict(conn.execute('SELECT * FROM customers').fetchone())
    assert workspace_note(client,key='crm:'+c['id'],business='service').status_code==201
    with main.db() as conn:
        assert main.customer_followup_count(conn,c['id'])==1


def test_workspace_shares_assets_without_product_private_records(client):
    publish(client,[master(),icc(),assets()])
    with main.db() as conn:
        main.create_customer_record(conn,{'name':'甲'},main.DEMO_USERS['manager2'],client.test_user)
        conn.execute("UPDATE customers SET customer_code='TW2026001'")
    client.test_user.update(id='demo-manager-2',customerScope='self',rolePermission='manager',team='演示一组')
    p=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert p['priority']==[] and p['assets'][0]['assetUsd']=='26.37'


def test_workspace_service_note_protects_last_identity_rollback(client):
    p=publish(client,[master()])
    assert workspace_note(client,business='service').status_code==201
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/rollback').status_code==409
