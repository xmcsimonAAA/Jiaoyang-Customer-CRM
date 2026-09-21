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


def icc(amount='100', owner='演示顾问', batch='2026.09.09', name='甲', notes='', row=5, code=None):
    return source('icc.xlsx',[(batch,[(2,{**dict(zip('ABCDEFGHIJKL',HEADERS)), 'M':'TW 编号'}),
        (row,dict(A=1,B=name,C='已提交',F='外部经纪人',I='☑',J=amount,K=owner,L=notes,M=('TW2026001' if name=='甲' else '') if code is None else code)),
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
        assert c.execute('SELECT count(*) FROM customers').fetchone()[0]==2
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
    content=xlsx([('2026.09.09',[(2,{**dict(zip('ABCDEFGHIJKL',HEADERS)), 'M':'TW 编号'}),
                     (5,dict(B='甲',F='外部经纪人',I='☐',M='TW2026001'))])])
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
    # A default only fills unassigned customers; existing ownership is stable.
    bind_service(client,'demo-manager-2')
    assert client.get('/api/priority-inferior/overview').json()['customers'][0]['serviceOwner']=='演示顾问'
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
    assert row['assignmentMode']=='binding' and row['serviceOwnerId']=='demo-manager'


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


def test_binding_disable_preserves_owner_and_inactive_account_flags_task(client, monkeypatch):
    publish(client,[master(),service_icc()]);bind_service(client)
    payload={'broker':'外部经纪人','ownerId':'demo-manager','active':False,'reason':'停止服务绑定'}
    p=client.post('/api/priority-inferior/bindings/preview',json=payload).json()
    assert not p['affected'][0]['changed'] and p['affected'][0]['serviceOwnerId']=='demo-manager'
    assert client.post('/api/priority-inferior/bindings/save',json={**payload,'token':p['token']}).status_code==200
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['serviceOwnerId']=='demo-manager'
    bind_service(client)
    original=main.DEMO_USERS['manager']
    monkeypatch.setitem(main.DEMO_USERS,'manager',{**original,'active':False})
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['ownerConflict'] and '停用' in row['assignmentReason']


def workspace_note(client, key='tw:TW2026001', business='priority', **extra):
    return client.post('/api/workspace/followups',json=dict(subjectKey=key,business=business,
            content='客户已收到资料，下周联系',nextAction='确认资料',**extra))


def test_workspace_notes_batch_validation_completion_and_no_placement_change(client):
    publish(client,[master(),icc()])
    assert workspace_note(client,batchKey='2026-01-01').status_code==422
    # Shared roster creates a selectable customer; no participation is inferred.
    r=workspace_note(client,batchKey='2026-09-09',nextFollowupAt='2026-09-20T09:00:00+08:00')
    assert r.status_code==201,r.text
    rows=client.get('/api/workspace/followups').json()['items']
    assert len(rows)==1 and rows[0]['next_followup_at']=='2026-09-20T01:00:00+00:00'
    assert client.post('/api/workspace/followups/'+r.json()['id']+'/complete').status_code==200
    assert client.get('/api/workspace/followups').json()['items'][0]['completed_at']
    with main.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers WHERE placement_status='已参与'").fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==2


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
        conn.execute("UPDATE customers SET owner_id='demo-manager-2',owner_name='演示顾问二',owner_team='演示一组' WHERE customer_code='TW2026001'")
    assert workspace_note(client,business='placement').status_code==201
    assert workspace_note(client,business='service').status_code==201
    assert workspace_note(client).status_code==201
    admin=dict(client.test_user)
    client.test_user.update(id='demo-manager',customerScope='self',rolePermission='manager',team='演示一组')
    assert client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).status_code==404
    assert workspace_note(client,business='placement').status_code==404
    client.test_user.update(id='demo-manager-2')
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert card['priority'] and card['crm'] and {r['business'] for r in card['followups']}=={'service','placement','priority'}
    assert workspace_note(client).status_code==201
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


def test_workspace_shared_owner_sees_assets_and_both_products(client):
    publish(client,[master(),icc(),assets()])
    with main.db() as conn:
        conn.execute("UPDATE customers SET owner_id='demo-manager-2',owner_name='演示顾问二',owner_team='演示一组' WHERE customer_code='TW2026001'")
    client.test_user.update(id='demo-manager-2',customerScope='self',rolePermission='manager',team='演示一组')
    p=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert p['priority'] and p['assets'][0]['assetUsd']=='26.37'


def test_workspace_service_note_protects_last_identity_rollback(client):
    p=publish(client,[master()])
    assert workspace_note(client,business='service').status_code==201
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/rollback').status_code==200
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert len(card['followups'])==1  # Shared master survives source rollback.


def test_leader_assignment_without_import_permission_is_narrowly_scoped(client):
    publish(client,[master(),icc()])
    data=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    url='/api/priority-inferior/batches/2026-09-09/records/'+data['rows'][0]['recordKey']
    client.test_user['canImportCustomers']=False
    payload={'expectedRevision':data['revision'],'changes':{'jiaoyangOwner':'演示顾问二'},'reason':'组长指派服务负责人'}
    assert client.patch(url,json={**payload,'changes':{'notes':'越权编辑'}}).status_code==403
    response=client.patch(url,json=payload)
    assert response.status_code==200,response.text
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['serviceOwnerId']=='demo-manager-2'
    assert client.patch(url,json=payload).status_code==409
    client.test_user['canManageAssignments']=False
    assert client.patch(url,json={**payload,'expectedRevision':response.json()['revision']}).status_code==403


def test_shared_roster_100_new_customers_ten_priority_participants(client):
    roster=source('master.xlsx',[('全部',[(1,dict(A='客户姓名',B='是否完成开户',C='备注'))]+
           [(i+2,dict(A=f'新增客户{i}',B='已开户',C=f'TW202699{i:03}')) for i in range(100)])])
    activity=source('icc.xlsx',[('2026.09.09',[(2,{**dict(zip('ABCDEFGHIJKL',HEADERS)), 'M':'TW 编号'})]+
             [(i+3,dict(B=f'新增客户{i}',J='100',K='演示顾问',M=f'TW202699{i:03}')) for i in range(10)])])
    p=preview(client,[roster,activity])
    assert p['sharedCustomers']['createdCount']==100
    with main.db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==0
    r=client.post('/api/priority-inferior/imports/'+p['id']+'/commit')
    assert r.status_code==200,r.text
    assert r.json()['sharedCustomers']['createdCount']==100
    dashboard=client.get('/api/workspace/dashboard').json()
    assert dashboard['customers']==100 and dashboard['priority']['participants']==10
    assert dashboard['placement']['participants']==0 and dashboard['placement']['actualAmount']=='0'
    assert dashboard['priority']['agreementUsd']=='1000'
    assert len(client.get('/api/workspace/customers').json()['items'])==100
    assert len(client.get('/api/priority-inferior/overview').json()['customers'])==100
    assert client.get('/api/customers?pageSize=100').json()['total']==100
    again=preview(client,[roster])
    assert again['sharedCustomers']['createdCount']==0
    publish(client,[roster])
    assert client.get('/api/workspace/dashboard').json()['customers']==100


def test_shared_backfill_is_idempotent_and_preserves_owners_amounts_and_names(client):
    with main.db() as conn:
        c=main.create_customer_record(conn,{'name':'已有客户','twCode':'TW2026001','placementStatus':'已参与','actualAmount':999},main.DEMO_USERS['manager2'],client.test_user)
    publish(client,[master()])
    with main.db() as conn:
        row=dict(conn.execute('SELECT * FROM customers WHERE id=?',(c['id'],)).fetchone())
        assert row['name']=='已有客户' and row['actual_amount']==999 and row['owner_id']=='demo-manager-2'
        from backend.priority_inferior import heads
        result=main.sync_shared_customers(conn,heads(conn),client.test_user)
        assert result['createdCount']==0
        assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==2
    d=client.get('/api/workspace/dashboard').json()
    assert d['placement']['participants']==1 and d['placement']['actualAmount']=='999.0'


def test_legacy_roster_is_available_to_priority_import_and_query(client):
    with main.db() as conn:
        main.create_customer_record(conn,{'name':'甲','twCode':'TW2026001'},main.DEMO_USERS['manager'],client.test_user)
    publish(client,[icc()])  # No separate priority master upload required.
    row=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()['rows'][0]
    assert row['twCode']=='TW2026001'
    assert client.get('/api/workspace/dashboard').json()['customers']==1


def test_shared_dashboard_followups_products_and_visibility(client):
    publish(client,[master(),icc()])
    with main.db() as conn:
        conn.execute("UPDATE customers SET placement_status='已参与',actual_amount=500,owner_id='demo-manager-2',owner_team='演示一组' WHERE customer_code='TW2026001'")
    note=workspace_note(client,nextFollowupAt='2020-01-01T00:00:00Z')
    assert note.status_code==201
    assert workspace_note(client,business='service').status_code==201
    d=client.get('/api/workspace/dashboard').json()
    assert d['overlap']==1 and d['followups']['recent14']==2 and d['followups']['due']==1
    assert d['followups']['byBusiness']['priority']==1
    assert client.post('/api/workspace/followups/'+note.json()['id']+'/complete').status_code==200
    assert client.get('/api/workspace/dashboard').json()['followups']['due']==0
    client.test_user.update(id='demo-manager',customerScope='self',rolePermission='manager',team='演示一组')
    d=client.get('/api/workspace/dashboard').json()
    assert d['customers']==0 and d['placement']['participants']==0 and d['priority']['participants']==0
    client.test_user.update(id='demo-manager-2')
    d=client.get('/api/workspace/dashboard').json()
    assert d['customers']==1 and d['placement']['participants']==1 and d['priority']['participants']==1
    assert d['followups']['byBusiness']['priority']==1


def test_archived_tw_not_resurrected_or_duplicated_by_shared_roster(client):
    with main.db() as conn:
        c=main.create_customer_record(conn,{'name':'甲','twCode':'TW2026001'},main.DEMO_USERS['manager'],client.test_user)
        conn.execute('UPDATE customers SET archived_at=? WHERE id=?',(main.now_iso(),c['id']))
    p=preview(client,[master()])
    assert p['sharedCustomers']['createdCount']==1
    assert p['sharedCustomers']['conflicts'][0]['twCode']=='TW2026001'
    publish(client,[master()])
    with main.db() as conn:
        assert conn.execute('SELECT archived_at FROM customers WHERE id=?',(c['id'],)).fetchone()[0]
        assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==2


def test_shared_dashboard_batch_totals_do_not_double_count_master(client):
    publish(client,[master(),icc()])
    with main.db() as conn:
        c=conn.execute("SELECT id FROM customers WHERE customer_code='TW2026001'").fetchone()
        conn.execute("UPDATE customers SET placement_status='已参与',actual_amount=9999 WHERE id=?",(c['id'],))
    for n,amount in enumerate([100,200]):
        r=client.post('/api/batches',json={'name':f'批次{n}','status':'已完成'})
        assert r.status_code==201,r.text
        bid=r.json()['batch']['id']
        r=client.post('/api/batches/'+bid+'/participations',json={'customerId':c['id'],'status':'已参与','actualAmount':amount})
        assert r.status_code==201,r.text
    d=client.get('/api/workspace/dashboard').json()
    assert d['placement']['participants']==1 and d['placement']['records']==2
    assert Decimal(d['placement']['actualAmount'])==300
    assert d['overlap']==1


def test_shared_backfill_existing_published_sources_without_reupload(client):
    from backend.priority_inferior import heads
    from backend.priority_import import parse
    import json
    dataset=parse(base64.b64decode(master()['contentBase64']),'master.xlsx','2026-09-11',[])[0]
    with main.db() as conn:
        conn.execute("INSERT INTO priority_datasets VALUES ('master:2026-09-11','master','2026-09-11','r-old')")
        conn.execute('INSERT INTO priority_revisions VALUES (?,?,?,?,?,?,?,?,?)',('r-old','master:2026-09-11','','old',json.dumps(dataset['rows']),'master.xlsx','old',main.now_iso(),'old'))
        first=main.sync_shared_customers(conn,heads(conn),client.test_user)
        second=main.sync_shared_customers(conn,heads(conn),client.test_user)
        assert first['createdCount']==2 and second['createdCount']==0
    assert client.get('/api/workspace/dashboard').json()['customers']==2
    assert client.get('/api/customers').json()['total']==2


def test_same_name_only_suggests_candidate_and_explicit_tw_matches(client):
    publish(client,[master()],date='2026-09-08')
    p=publish(client,[icc(code='')])
    issue=p['issues'][0]
    assert issue['candidates']==['TW2026001']
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    assert d['rows'][0]['twCode']==''
    assert p['datasets'][0]['changes'][0]['fields']==['新增批次记录']
    assert client.get('/api/priority-inferior/customers/TW2026001').json()['icc']==[]
    publish(client,[icc(code='TW2026001',batch='2026.09.17')],batches=['2026-09-17'])
    assert len(client.get('/api/priority-inferior/customers/TW2026001').json()['icc'])==1
    bad=client.post('/api/priority-inferior/imports/preview',json=dict(asOf='2026-09-11',batchDates=['2026-09-09'],files=[icc(code='TW999999')]))
    assert bad.status_code==422


def test_unlink_keeps_activity_followups_assets_and_survives_reupload(client):
    publish(client,[master(),icc(notes='保留'),assets()])
    assert workspace_note(client).status_code==201
    assert workspace_note(client,business='service').status_code==201
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    row=d['rows'][0]
    url='/api/priority-inferior/batches/2026-09-09/records/'+row['recordKey']
    result=client.patch(url,json=dict(expectedRevision=d['revision'],changes={'twCode':''},reason='不是同一人'))
    assert result.status_code==200,result.text
    assert client.patch(url,json=dict(expectedRevision=d['revision'],changes={'twCode':''},reason='重复')).status_code==409
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert not card['priority']
    assert [n['business'] for n in card['followups']]==['service']
    assert client.get('/api/priority-inferior/customers/TW2026001').json()['assets'][0]['assetUsd']=='26.37'
    anchor='icc:2026-09-09/'+row['recordKey']
    card=client.get('/api/workspace/card',params={'key':anchor}).json()
    assert len(card['followups'])==1 and card['followups'][0]['business']=='priority'
    publish(client,[icc(notes='保留')])  # identical file stays detached
    publish(client,[icc(amount='200')])  # changed file with the old TW also stays detached
    after=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    assert len(after['rows'])==1
    assert after['rows'][0]['recordKey']==row['recordKey']
    assert after['rows'][0]['twCode']=='' and after['rows'][0]['identityDetached']=='TW2026001'
    assert after['rows'][0]['agreementAmountUsd']=='200'
    client.test_user['canImportCustomers']=False
    assert client.patch(url,json=dict(expectedRevision=after['revision'],changes={'twCode':''},reason='无权限')).status_code==403


def test_pre_upgrade_preview_cannot_commit(client):
    import json
    p=preview(client,[master(),icc()])
    with main.db() as conn:
        row=conn.execute('SELECT payload_json FROM priority_uploads WHERE id=?',(p['id'],)).fetchone()
        payload=json.loads(row['payload_json']);payload.pop('matchingVersion')
        conn.execute('UPDATE priority_uploads SET payload_json=? WHERE id=?',(json.dumps(payload),p['id']))
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/commit').status_code==409


def test_targeted_repair_is_audited_idempotent_and_does_not_change_other_rows(client):
    from backend.scripts.unlink_priority_identity import repair
    publish(client,[master(),icc(),assets()])
    kwargs=dict(batch='2026-09-09',name='甲',expected_tw='TW2026001',broker='外部经纪人',reason='用户确认不同人')
    with main.db() as conn:
        old=conn.execute("SELECT head_id FROM priority_datasets WHERE dataset_key='icc:2026-09-09'").fetchone()[0]
        assert repair(conn,**kwargs)['status']=='preview'
        assert conn.execute("SELECT head_id FROM priority_datasets WHERE dataset_key='icc:2026-09-09'").fetchone()[0]==old
        with pytest.raises(ValueError):repair(conn,**{**kwargs,'broker':'错误经纪人'},apply=True)
        result=repair(conn,**kwargs,apply=True)
        assert result['status']=='unlinked'
        assert repair(conn,**kwargs,apply=True)['status']=='already_unlinked'
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE actor_id='system-identity-repair'").fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==2
    assert client.get('/api/priority-inferior/overview').json()['summary']['agreementUsd']=='100'
    assert client.get('/api/priority-inferior/customers/TW2026001').json()['icc']==[]
    # Existing immutable version can still be inspected and repair can be rolled back.
    assert client.get('/api/priority-inferior/revisions/'+old).json()['rows'][0]['twCode']=='TW2026001'
    assert client.post('/api/priority-inferior/imports/'+result['jobId']+'/rollback').status_code==200
    assert len(client.get('/api/priority-inferior/customers/TW2026001').json()['icc'])==1


def roster_rows(entries):
    return source('开户名单.xlsx',[('全部',[(1,dict(A='客户姓名',B='是否完成开户',C='备注'))]+
        [(i+2,dict(A=name,B='开户成功',C=code)) for i,(name,code) in enumerate(entries)])])


def test_roster_after_activity_automatically_links_history_followup_and_can_undo(client):
    publish(client,[icc(code='',batch='2026.09.17')],batches=['2026-09-17'])
    old=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()
    key=old['rows'][0]['recordKey']
    assert workspace_note(client,key='icc:2026-09-17/'+key,batchKey='2026-09-17').status_code==201
    p=preview(client,[master(),assets(date='2026.09.18')],date='2026-09-18')
    assert p['identityReconciliation']['autoMatched']==1
    assert p['datasets'][-1]['automaticIdentity']
    assert client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]['twCode']==''
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/commit').status_code==200
    row=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert row['recordKey']==key and row['twCode']=='TW2026001'
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert len(card['followups'])==1 and len(card['priority'])==1
    again=publish(client,[master()],date='2026-09-18')
    assert again['identityReconciliation']['autoMatched']==0 and len(again['datasets'])==1
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/rollback').status_code==200
    row=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert row['recordKey']==key and row['twCode']==''
    card=client.get('/api/workspace/card',params={'key':'icc:2026-09-17/'+key}).json()
    assert len(card['followups'])==1


def test_rejected_old_namesake_does_not_block_new_account(client):
    publish(client,[roster_rows([('张萌','TW202605018')]),icc(name='张萌',code='TW202605018',batch='2026.09.17')],batches=['2026-09-17'])
    d=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()
    url='/api/priority-inferior/batches/2026-09-17/records/'+d['rows'][0]['recordKey']
    assert client.patch(url,json=dict(expectedRevision=d['revision'],changes={'twCode':''},reason='不同人')).status_code==200
    p=publish(client,[roster_rows([('张萌','TW202605018'),('张萌','TW202609050')])],date='2026-09-18')
    assert p['identityReconciliation']['autoMatched']==1
    row=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert row['twCode']=='TW202609050' and 'TW202605018' in row['excludedTwCodes']
    assert client.get('/api/priority-inferior/customers/TW202605018').json()['icc']==[]
    publish(client,[icc(name='张萌',code='',batch='2026.09.17',amount='200')],batches=['2026-09-17'])
    row=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert row['twCode']=='TW202609050' and 'TW202605018' in row['excludedTwCodes']
    publish(client,[icc(name='张萌',code='TW202605018',batch='2026.09.17',amount='300')],batches=['2026-09-17'])
    rows=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows']
    assert len(rows)==1 and rows[0]['twCode']=='TW202609050'


def test_ambiguous_names_stay_pending_and_same_tw_name_conflicts_do_not_guess(client):
    publish(client,[icc(code='',batch='2026.09.17')],batches=['2026-09-17'])
    p=publish(client,[roster_rows([('甲','TW2026001'),('甲','TW2026002')])],date='2026-09-18')
    assert p['identityReconciliation']['autoMatched']==0
    assert p['identityReconciliation']['pending']==1
    assert len(p['identityReconciliation']['issues'][0]['candidates'])==2
    # Removing an account from the next snapshot must not erase known ambiguity.
    p=publish(client,[roster_rows([('甲','TW2026002')])],date='2026-09-19')
    assert p['identityReconciliation']['autoMatched']==0


def test_roster_retry_updates_existing_duplicate_upload_and_stale_preview_is_blocked(client):
    publish(client,[master()],date='2026-09-18')
    publish(client,[icc(name='访客',code='',batch='2026.09.17')],batches=['2026-09-17'])
    # Simulate an unresolved record published before reconciliation was introduced.
    import json
    with main.db() as conn:
        d=conn.execute("SELECT r.id,r.rows_json FROM priority_datasets d JOIN priority_revisions r ON r.id=d.head_id WHERE d.dataset_key='icc:2026-09-17'").fetchone()
        rows=json.loads(d['rows_json']);rows[0]['customerName']='甲'
        conn.execute('UPDATE priority_revisions SET rows_json=? WHERE id=?',(json.dumps(rows),d['id']))
    p=preview(client,[master()],date='2026-09-18')
    assert p['datasets'][0]['duplicate'] and p['identityReconciliation']['autoMatched']==1
    stale=preview(client,[master()],date='2026-09-18')
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/commit').status_code==200
    assert client.post('/api/priority-inferior/imports/'+stale['id']+'/commit').status_code==409


def test_secondary_reordered_headers_multi_accounts_and_trailing_total():
    f=source('SXY SH 20260918二级.xlsx',[('SH',[(1,dict(A='Cd',B='client_acc_name',C='qty')),
        (2,dict(A='TW2026001',B='JIA',C='12100')),(3,dict(A='TW2026001',B='JIA',C='10333')),
        (4,dict(A='TW2026002',B='YI',C='10')),(5,dict(C='22443'))])])
    rows=parse(base64.b64decode(f['contentBase64']),f['filename'],'2026-09-18',[])[0]['rows']
    assert len(rows)==2 and rows[0]['quantity']=='22433' and len(rows[0]['sourceRows'])==2
    assert sum(Decimal(r['quantity']) for r in rows)==22443


def test_reconcile_does_not_use_conflicting_tw_names_or_occupied_batch_identity():
    from backend.priority_identity import reconcile
    identities={'TW1':dict(customerName='甲')}
    datasets={'master':dict(kind='master',business_date='2026-09-18',rows=[dict(twCode='TW1',customerName='乙')])}
    row=dict(customerName='乙',twCode='')
    assert reconcile([row],'2026-09-17',datasets,identities)==0
    assert '姓名不一致' in row['matchReason']
    datasets['master']['rows'][0]['customerName']='甲'
    row=dict(customerName='甲',twCode='')
    assert reconcile([row],'2026-09-17',datasets,identities,{'TW1'})==0
    assert '冲突' in row['matchReason']
    assert reconcile([row,dict(customerName='甲',twCode='TW1')],'2026-09-17',datasets,identities)==0


def test_secondary_wrong_total_is_rejected():
    f=source('二级.xlsx',[('SH',[(1,dict(A='Cd',B='client_acc_name',C='qty')),
        (2,dict(A='TW1',B='JIA',C=10)),(3,dict(C=11))])])
    from fastapi import HTTPException
    with pytest.raises(HTTPException,match='合计不一致'):
        parse(base64.b64decode(f['contentBase64']),f['filename'],'2026-09-18',[])


def test_choose_identity_in_upload_preview_atomic_audited_and_rollback(client):
    publish(client,[icc(code='',batch='2026.09.17')],batches=['2026-09-17'])
    roster=roster_rows([('甲','TW2026001'),('甲','TW2026002')])
    p=preview(client,[roster],date='2026-09-18')
    issue=p['identityReconciliation']['issues'][0]
    choice=dict(key=issue['key'],recordKey=issue['recordKey'],twCode='TW2026002')
    url='/api/priority-inferior/imports/'+p['id']+'/commit'
    assert client.post(url,json={'identities':[{**choice,'twCode':'TW999'}]}).status_code==422
    before=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()
    assert before['rows'][0]['twCode']==''
    assert client.post(url,json={'identities':[choice]}).status_code==200
    row=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert row['twCode']=='TW2026002' and row['recordKey']==choice['recordKey']
    assert row['manualCorrection']['reason']=='上传预览中人工选择候选 TW'
    assert client.post(url,json={'identities':[choice]}).json()['alreadyCommitted']
    assert client.post(url.replace('/commit','/rollback')).status_code==200
    assert client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]['twCode']==''


def test_choose_existing_unchanged_conflict_and_reupload_preserves_choice(client):
    publish(client,[icc(code='',batch='2026.09.17')],batches=['2026-09-17'])
    roster=roster_rows([('甲','TW2026001'),('甲','TW2026002')])
    publish(client,[roster],date='2026-09-18')
    p=preview(client,[roster],date='2026-09-18')
    assert len(p['datasets'])==1 and p['datasets'][0]['duplicate']
    issue=p['identityReconciliation']['issues'][0]
    choice=dict(key=issue['key'],recordKey=issue['recordKey'],twCode='TW2026001')
    assert client.post('/api/priority-inferior/imports/'+p['id']+'/commit',json={'identities':[choice]}).status_code==200
    publish(client,[icc(code='',batch='2026.09.17',amount='200')],batches=['2026-09-17'])
    row=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert row['twCode']=='TW2026001' and row['agreementAmountUsd']=='200'


def test_preview_choice_cannot_duplicate_same_tw_in_batch(client):
    from backend.priority_inferior import dumps
    publish(client,[icc(code='',batch='2026.09.17')],batches=['2026-09-17'])
    roster=roster_rows([('甲','TW2026001'),('甲','TW2026002')])
    publish(client,[roster],date='2026-09-18')
    import json
    with main.db() as conn:
        head=conn.execute("SELECT head_id FROM priority_datasets WHERE dataset_key='icc:2026-09-17'").fetchone()[0]
        rows=json.loads(conn.execute('SELECT rows_json FROM priority_revisions WHERE id=?',(head,)).fetchone()[0])
        rows.append({**rows[0],'recordKey':'another-record','twCode':'TW2026001'})
        conn.execute('UPDATE priority_revisions SET rows_json=? WHERE id=?',(dumps(rows),head))
    p=preview(client,[roster],date='2026-09-18')
    i=p['identityReconciliation']['issues'][0]
    choice=dict(key=i['key'],recordKey=i['recordKey'],twCode='TW2026001')
    url='/api/priority-inferior/imports/'+p['id']+'/commit'
    assert client.post(url,json={'identities':[choice]}).status_code==409
    client.test_user['canImportCustomers']=False
    assert client.post(url,json={'identities':[choice]}).status_code==403


def test_task_center_only_actionable_and_permissions(client):
    publish(client,[icc(code='',batch='2026.09.17')],batches=['2026-09-17'])
    tasks=client.get('/api/priority-inferior/tasks').json()
    assert not tasks['items'] and len(tasks['waiting'])==1
    publish(client,[roster_rows([('甲','TW2026001'),('甲','TW2026002')])],date='2026-09-18')
    tasks=client.get('/api/priority-inferior/tasks').json()
    assert len(tasks['items'])==1 and not tasks['waiting']
    task=tasks['items'][0]
    assert task['action']=='identity' and task['batch']=='2026-09-17'
    d=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()
    assert client.patch('/api/priority-inferior/batches/2026-09-17/records/'+task['recordKey'],json=dict(expectedRevision=d['revision'],changes={'twCode':'TW2026001'},reason='快捷确认')).status_code==200
    assert client.get('/api/priority-inferior/tasks').json()['items']==[]
    client.test_user.update(customerScope='self',canImportCustomers=False,canManageAssignments=False)
    assert client.get('/api/priority-inferior/tasks').json()==dict(items=[],waiting=[])


def test_task_center_owner_binding_and_missing_broker(client):
    publish(client,[master(),icc(owner='')])
    tasks=client.get('/api/priority-inferior/tasks').json()['items']
    assert len(tasks)==1 and tasks[0]['action']=='owner'
    d=client.get('/api/priority-inferior/batches/2026-09-09/participations').json()
    url='/api/priority-inferior/batches/2026-09-09/records/'+tasks[0]['recordKey']
    assert client.patch(url,json=dict(expectedRevision=d['revision'],changes={'jiaoyangOwner':'演示顾问'},reason='快捷指派')).status_code==200
    assert not client.get('/api/priority-inferior/tasks').json()['items']
    # Separate service customer without agreement needs broker binding, not leader assignment.
    service=icc(name='乙',code='',owner='',amount='',batch='2026.09.17')
    service['contentBase64']=base64.b64encode(xlsx([('2026.09.17',[(2,dict(zip('ABCDEFGHIJKL',HEADERS))),(3,dict(B='乙',F='外部经纪人',I='☐'))])])).decode()
    publish(client,[service],batches=['2026-09-17'])
    assert client.get('/api/priority-inferior/tasks').json()['items'][0]['action']=='binding'
    bind_service(client)
    assert not client.get('/api/priority-inferior/tasks').json()['items']


def test_edit_activity_name_preserves_original_and_survives_reupload(client):
    publish(client,[roster_rows([('杜勇锋','TW202609056')]),icc(name='杜勇峰',code='TW202609056',batch='2026-09-17')],batches=['2026-09-17'])
    d=client.get('/api/priority-inferior/batches/2026-09-17/participations').json(); row=d['rows'][0]
    url='/api/priority-inferior/batches/2026-09-17/records/'+row['recordKey']
    r=client.patch(url,json={'expectedRevision':d['revision'],'changes':{'customerName':'杜勇锋'},'reason':'核对客户姓名录入错误'})
    assert r.status_code==200,r.text
    fixed=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert fixed['customerName']=='杜勇锋' and fixed['originalCustomerName']=='杜勇峰'
    assert fixed['manualCorrection']['fields']==['customerName']
    publish(client,[icc(name='杜勇峰',code='TW202609056',batch='2026-09-17')],batches=['2026-09-17'])
    fixed=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows'][0]
    assert fixed['customerName']=='杜勇锋'

    publish(client,[icc(name='杜勇峰',code='',amount='200',batch='2026-09-17')],batches=['2026-09-17'])
    rows=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()['rows']
    assert len(rows)==1 and rows[0]['customerName']=='杜勇锋' and rows[0]['twCode']=='TW202609056'
    assert rows[0]['recordKey']==row['recordKey'] and rows[0]['agreementAmountUsd']=='200'
    assert rows[0]['raw']['B']=='杜勇峰'
    d=client.get('/api/priority-inferior/batches/2026-09-17/participations').json()
    assert client.patch(url,json={'expectedRevision':d['revision'],'changes':{'customerName':'  '},'reason':'空白'}).status_code==422


def owner_customer():
    with main.db() as conn:
        return dict(conn.execute("SELECT * FROM customers WHERE customer_code='TW2026001'").fetchone())


def activity(client):
    return client.get('/api/priority-inferior/batches/2026-09-09/participations').json()


def set_activity_owner(client, name, snapshot=None):
    data=snapshot or activity(client)
    return client.patch('/api/priority-inferior/batches/2026-09-09/records/'+data['rows'][0]['recordKey'],json={
        'expectedRevision':data['revision'],'expectedCustomerVersion':data['rows'][0].get('customerVersion'),
        'changes':{'jiaoyangOwner':name},'reason':'领导确认统一负责人'})


def test_shared_owner_adopt_and_bidirectional_assignment(client):
    publish(client,[master(),icc()])
    c=owner_customer()
    assert c['owner_id']=='demo-manager'
    assert set_activity_owner(client,'演示顾问二').status_code==200
    assert owner_customer()['owner_id']=='demo-manager-2'
    row=activity(client)['rows'][0]
    assert row['serviceOwnerId']=='demo-manager-2' and not row['ownerConflict']
    result=client.post('/api/customers/'+c['id']+'/assign',json={'ownerId':'demo-manager','reason':'客户统一转交'})
    assert result.status_code==200,result.text
    row=activity(client)['rows'][0]
    assert row['serviceOwnerId']=='demo-manager' and not row['ownerConflict']
    # Uploads cannot undo a leader decision; raw source remains available.
    publish(client,[icc(owner='演示顾问二',amount='500')])
    assert activity(client)['rows'][0]['serviceOwnerId']=='demo-manager'
    assert owner_customer()['owner_id']=='demo-manager'


def test_shared_owner_existing_conflict_direct_resolution_and_stale_edit(client):
    publish(client,[master()])
    c=owner_customer()
    with main.db() as conn:
        conn.execute("UPDATE customers SET owner_id='demo-manager-2',owner_name='演示顾问二',owner_team='演示一组' WHERE id=?",(c['id'],))
    publish(client,[icc()])
    data=activity(client)
    row=data['rows'][0]
    assert row['ownerConflict'] and row['serviceOwnerId']=='demo-manager-2'
    tasks=client.get('/api/priority-inferior/tasks').json()['items']
    task=next(t for t in tasks if t['twCode']=='TW2026001')
    assert task['action']=='owner' and task['label']=='确认统一负责人'
    # Keeping the existing master owner is a valid, recorded resolution.
    assert set_activity_owner(client,'演示顾问二',data).status_code==200
    assert not activity(client)['rows'][0]['ownerConflict']
    stale=activity(client)
    assert client.post('/api/customers/'+c['id']+'/assign',json={'ownerId':'demo-manager','reason':'领导再次转交'}).status_code==200
    assert set_activity_owner(client,'演示顾问二',stale).status_code==409
    assert owner_customer()['owner_id']=='demo-manager'


def test_shared_owner_detach_reverts_only_automatic_owner(client):
    publish(client,[master(),icc()])
    data=activity(client)
    url='/api/priority-inferior/batches/2026-09-09/records/'+data['rows'][0]['recordKey']
    r=client.patch(url,json={'expectedRevision':data['revision'],'changes':{'twCode':''},'reason':'同名不同人'})
    assert r.status_code==200,r.text
    assert owner_customer()['owner_id']=='unassigned'


def test_shared_owner_manual_survives_detach(client):
    publish(client,[master(),icc()])
    assert set_activity_owner(client,'演示顾问二').status_code==200
    data=activity(client)
    r=client.patch('/api/priority-inferior/batches/2026-09-09/records/'+data['rows'][0]['recordKey'],json={
        'expectedRevision':data['revision'],'changes':{'twCode':''},'reason':'不同人'})
    assert r.status_code==200,r.text
    assert owner_customer()['owner_id']=='demo-manager-2'


def test_shared_owner_migration_idempotent_and_reassignment_permissions(client):
    from backend import shared_ownership
    publish(client,[master(),icc()])
    c=owner_customer()
    with main.db() as conn:
        count=conn.execute('SELECT COUNT(*) FROM assignments').fetchone()[0]
        proposal={'TW2026001':dict(serviceOwnerId='demo-manager',serviceOwner='演示顾问',serviceOwnerTeam='演示一组',assignmentMode='manual')}
        shared_ownership.sync(conn,proposal,client.test_user,main.now_iso())
        assert conn.execute('SELECT COUNT(*) FROM assignments').fetchone()[0]==count
    assert client.post('/api/customers/'+c['id']+'/assign',json={'ownerId':'demo-manager-2','reason':'转交两个产品'}).status_code==200
    client.test_user.update(id='demo-manager',customerScope='self',rolePermission='manager',team='演示一组')
    assert client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).status_code==404
    assert client.get('/api/priority-inferior/overview').json()['customers']==[]
    client.test_user.update(id='demo-manager-2')
    card=client.get('/api/workspace/card',params={'key':'tw:TW2026001'}).json()
    assert card['crm'][0]['owner_id']=='demo-manager-2'
    assert card['priority'][0]['serviceOwnerId']=='demo-manager-2'


def test_shared_owner_late_tw_link_and_upload_rollback(client):
    publish(client,[icc(code='')])
    assert activity(client)['rows'][0]['twCode']==''
    roster=publish(client,[master()])
    assert activity(client)['rows'][0]['twCode']=='TW2026001'
    assert owner_customer()['owner_id']=='demo-manager'
    r=client.post('/api/priority-inferior/imports/'+roster['id']+'/rollback',json={})
    assert r.status_code==200,r.text
    assert owner_customer()['owner_id']=='unassigned'
    assert activity(client)['rows'][0]['twCode']==''


def test_shared_owner_binding_preview_rejects_intervening_manual_assignment(client):
    publish(client,[master(),service_icc()])
    payload={'broker':'外部经纪人','ownerId':'demo-manager','reason':'补全客户负责人'}
    p=client.post('/api/priority-inferior/bindings/preview',json=payload).json()
    c=owner_customer()
    assert client.post('/api/customers/'+c['id']+'/assign',json={'ownerId':'demo-manager-2','reason':'人工安排'}).status_code==200
    assert client.post('/api/priority-inferior/bindings/save',json={**payload,'token':p['token']}).status_code==409
    assert activity(client)['rows'][0]['serviceOwnerId']=='demo-manager-2'


def filtered_icc_preview(client, file, enabled=True):
    r=client.post('/api/priority-inferior/imports/preview',json={
        'asOf':'2026-09-11','batchDates':['2026-09-09'],'files':[file],
        'skipIccBlankAccountStatus':enabled})
    assert r.status_code==200,r.text
    return r.json()


def filter_fixture(status='  '):
    return source('ICC筛选.xlsx',[('2026.09.09',[
        (2,{**dict(zip('ABCDEFGHIJKL',HEADERS)), 'M':'TW 编号'}),
        (3,dict(B='已提交客户',C='已提交',J='100',K='演示顾问')),
        (4,dict(B='尚未开户客户',C=status,J='200',K='演示顾问'))])])


def commit_preview(client,p):
    r=client.post('/api/priority-inferior/imports/'+p['id']+'/commit',json={})
    assert r.status_code==200,r.text


def test_icc_blank_account_filter_preview_save_and_disable(client):
    p=filtered_icc_preview(client,filter_fixture())
    d=p['datasets'][0]
    assert d['records']==1 and d['participants']==1 and d['agreementUsd']=='100'
    assert d['iccAccountFilter']==dict(enabled=True,sourceCount=2,includedCount=1,
        skipped=[dict(name='尚未开户客户',sourceRow=4)])
    commit_preview(client,p)
    rows=activity(client)['rows']
    assert len(rows)==1 and rows[0]['customerName']=='已提交客户' and rows[0]['twCode']==''
    history=client.get('/api/priority-inferior/imports').json()
    assert history[0]['datasets'][0]['iccAccountFilter']['skipped'][0]['sourceRow']==4
    # Turning the switch off can import the exact same file's previously skipped rows.
    p=filtered_icc_preview(client,filter_fixture(),False)
    assert p['datasets'][0]['records']==2 and not p['datasets'][0]['duplicate']
    commit_preview(client,p)
    assert len(activity(client)['rows'])==2
    # Filtering a later upload never deletes a previously stored activity.
    p=filtered_icc_preview(client,filter_fixture())
    assert p['datasets'][0]['records']==2 and p['datasets'][0]['missingCount']==1
    commit_preview(client,p)
    assert len(activity(client)['rows'])==2


def test_icc_blank_account_filter_later_status_and_all_skipped(client):
    f=source('ICC全空.xlsx',[('2026.09.09',[(2,dict(zip('ABCDEFGHIJKL',HEADERS))),
        (3,dict(B='甲',C='',J='500'))])])
    p=filtered_icc_preview(client,f)
    assert p['datasets'][0]['records']==0 and p['datasets'][0]['iccAccountFilter']['includedCount']==0
    commit_preview(client,p)
    assert activity(client)['rows']==[]
    assert client.get('/api/priority-inferior/tasks').json()['waiting']==[]
    # A subsequent nonblank status admits the row even though it still has no TW.
    p=filtered_icc_preview(client,icc(code='',owner=''))
    assert p['datasets'][0]['records']==1
    commit_preview(client,p)
    assert activity(client)['rows'][0]['customerName']=='甲'
    assert activity(client)['rows'][0]['twCode']==''


def test_icc_account_filter_does_not_filter_roster_assets_or_positions(client):
    r=client.post('/api/priority-inferior/imports/preview',json={
        'asOf':'2026-09-11','batchDates':['2026-09-09'],'files':[master(),assets()],
        'skipIccBlankAccountStatus':True})
    assert r.status_code==200,r.text
    data=r.json()
    assert {d['kind']:d['records'] for d in data['datasets']}=={'master':2,'assets':1}
    assert all(d['iccAccountFilter'] is None for d in data['datasets'])
    commit_preview(client,data)
    assert owner_customer()['customer_code']=='TW2026001'
