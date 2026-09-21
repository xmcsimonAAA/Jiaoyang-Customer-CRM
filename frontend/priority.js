/* Dedicated upload and query flow; no field/row mapping from the placement importer. */
const priorityState = {tab: 'batches', preview: null, asOf: '', data: null, batch: null, rows: [], query: '', broker: '', owner: '', secondary: '', participation: '', type: '', minimum: '', maximum: ''};
const priorityLabels = {sourceSequence:'序号', customerName:'原表姓名', canonicalName:'标准姓名', twCode:'TW 编号', brokerAccountStatus:'券商开户状态', depositAmount:'入金金额（原表单位）', onSiteOpener:'骄阳现场开户人', insuranceBroker:'保险经纪人', zhongyangWitness:'中阳见证人', customerType:'客户类型', agreementSigned:'原表协议标记', agreementAmountUsd:'协议金额（USD）', jiaoyangOwner:'骄阳负责人', notes:'备注说明', assetUsd:'总资产（USD）', quantity:'XMax 持股数', serviceOwner:'本板块服务负责人',assignmentReason:'分配来源 / 待办',participationIntent:'参与意向',currentOwner:'原客户系统负责人', agreementUsd:'协议总额（USD）'};
const pnum = value => value === null || value === undefined || value === '' ? '未提供' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 2});
const pcanImport = () => state.user.customerScope === 'all' && state.user.canImportCustomers && state.user.canManageAdvisorBindings;
const pget = path => api(`/api/priority-inferior/${path}`);
const pcall = (path, body, method='POST') => api(`/api/priority-inferior/${path}`, {method, body: JSON.stringify(body)});
const pdate = () => new Date().toLocaleDateString('sv-SE');

async function renderPriority(content) {
  if (priorityState.userId !== state.user.id) {
    Object.assign(priorityState, {userId:state.user.id, preview:null, rows:[], data:null, query:'', broker:'', owner:'', participation:'', secondary:'', type:'', minimum:'', maximum:''});
  }
  content.innerHTML = '<div class="priority-page"><p role="status">正在读取优先劣后资料…</p></div>';
  const data = await pget(`overview${priorityState.asOf ? `?asOf=${encodeURIComponent(priorityState.asOf)}` : ''}`);
  if (state.view !== 'priority') return;
  priorityState.data = data;
  const s = data.summary;
  const taskCount=(pcanImport()||pcanAssign())?(await pget('tasks')).items.length:0;
  content.innerHTML = `<div class="priority-page ${priorityState.tab==='tasks'?'priority-task-view':''}"><div class="priority-heading"><div><span class="eyebrow">优先劣后 · 每周数据</span><h3>本周资料，一处查清</h3><p>按批次查协议与负责人，按客户查资产和 XMax 持仓。</p></div><label>查看截至日期<input id="p-asof" type="date" value="${esc(priorityState.asOf)}" aria-label="查看截至日期"></label></div>
    <div class="p-metrics"><article><span>参与客户（去重）</span><strong>${s.participants}<small> 人</small></strong><p>${s.participationRecords} 条参与记录${s.unmatchedParticipations ? ` · <button class="p-link" id="p-metric-pending">${s.unmatchedParticipations} 条身份待确认 · 去处理</button>` : ''}</p></article><article><span>协议总额 · USD</span><strong>${pnum(s.agreementUsd)}</strong><p>按已填协议金额统计</p></article><article><span>XMax 当前持仓客户</span><strong>${s.secondaryParticipants}<small> 人</small></strong><p>${data.dates.secondary || '尚未上传'} · ${pnum(s.quantity)} 股</p></article><article><span>客户总资产 · USD</span><strong>${pnum(s.assetsUsd)}</strong><p>${data.dates.assets || '尚未上传'} · 同一 TW 账户相加</p></article></div>
    <div class="p-tabs" role="tablist">${[['tasks','待处理中心'],['batches','批次记录'],['customers','客户综合查询'],['upload','资料导入'],['history','上传历史'],['bindings','负责人分配'],['followups','业务跟进']].filter(([key]) => key==='bindings' ? state.user.customerScope==='all' && state.user.canManageAssignments && state.user.canManageAdvisorBindings : !['upload','history'].includes(key) || pcanImport()).map(([key,label])=>`<button role="tab" aria-selected="${priorityState.tab === key}" data-p-tab="${key}">${label}${key==='tasks' && taskCount ? ` <span>${taskCount} 待处理</span>`:''}</button>`).join('')}</div>
    <section id="p-panel" class="p-panel"></section></div>`;
  content.querySelector('#p-metric-pending')?.addEventListener('click',priorityOpenTasks);
  content.querySelector('#p-asof').onchange = async e => { priorityState.asOf=e.target.value; await renderPriority(content); };
  content.querySelectorAll('[data-p-tab]').forEach(b=>b.onclick=async()=>{priorityState.tab=b.dataset.pTab;if(priorityState.tab==='tasks')priorityState.asOf=''; await renderPriority(content);});
  if (!pcanImport() && ['upload','history'].includes(priorityState.tab)) priorityState.tab='batches';
  const panel = content.querySelector('#p-panel');
  if (priorityState.tab==='followups') { workspaceBusiness='priority'; await renderUnifiedFollowups(panel); }
  else if (priorityState.tab==='tasks') await renderPriorityTasks(panel);
  else if (priorityState.tab==='bindings') await renderPriorityBindings(panel);
  else if (priorityState.tab==='upload') renderPriorityUpload(panel);
  else if (priorityState.tab==='customers') renderPriorityCustomers(panel);
  else if (priorityState.tab==='history') await renderPriorityHistory(panel);
  else await renderPriorityBatches(panel);
}

function pfilterMarkup(rows) {
  const opts = (field, selected) => [...new Set(rows.map(r=>r[field]).filter(Boolean))].sort().map(v=>`<option value="${esc(v)}" ${selected===v?'selected':''}>${esc(v)}</option>`).join('');
  return `<div class="p-filters"><label>客户<input id="p-search" placeholder="姓名或 TW 编号" value="${esc(priorityState.query)}"></label><label>保险经纪人<select id="p-broker"><option value="">全部经纪人</option>${opts('insuranceBroker',priorityState.broker)}</select></label><label>本板块服务负责人<select id="p-owner"><option value="">全部负责人</option>${opts('serviceOwner',priorityState.owner)}</select></label><label>参与状态<select id="p-participation"><option value="">全部记录</option><option value="yes" ${priorityState.participation==='yes'?'selected':''}>有优先劣后协议金额</option><option value="pending" ${priorityState.participation==='pending'?'selected':''}>身份待匹配</option></select></label><label>客户类型<select id="p-type"><option value="">全部类型</option>${opts('customerType',priorityState.type)}</select></label><label>最低协议金额（USD）<input id="p-minimum" type="number" min="0" value="${esc(priorityState.minimum)}" placeholder="不限"></label><label>最高协议金额（USD）<input id="p-maximum" type="number" min="0" value="${esc(priorityState.maximum)}" placeholder="不限"></label><button id="p-reset" class="secondary-btn">重置筛选</button></div>`;
}
function pfilterRows(rows) {
  const q=priorityState.query.toLowerCase().trim();
  rows=rows.filter(r=>(!priorityState.type || r.customerType===priorityState.type) && (priorityState.minimum==='' || ((r.agreementAmountUsd??r.agreementUsd)!=null && Number(r.agreementAmountUsd??r.agreementUsd)>=Number(priorityState.minimum))) && (priorityState.maximum==='' || ((r.agreementAmountUsd??r.agreementUsd)!=null && Number(r.agreementAmountUsd??r.agreementUsd)<=Number(priorityState.maximum))));
  return rows.filter(r=>(!q || [r.customerName,r.canonicalName,r.twCode].some(v=>String(v||'').toLowerCase().includes(q))) && (!priorityState.broker || r.insuranceBroker===priorityState.broker) && (!priorityState.owner || r.serviceOwner===priorityState.owner) && (!priorityState.participation || (priorityState.participation==='pending' ? !r.twCode : Number(r.agreementAmountUsd ?? r.agreementUsd)>0)));
}
function pbindFilters(panel, update) {
  for (const [id,field] of [['p-search','query'],['p-broker','broker'],['p-owner','owner'],['p-participation','participation'],['p-type','type'],['p-minimum','minimum'],['p-maximum','maximum']]) {
    panel.querySelector(`#${id}`).addEventListener(id==='p-search'?'input':'change',e=>{priorityState[field]=e.target.value;update();});
  }
  panel.querySelector('#p-reset').onclick=()=>{Object.assign(priorityState,{query:'',broker:'',owner:'',participation:'',secondary:'',type:'',minimum:'',maximum:''});renderPriority(document.querySelector('#content'));};
}
async function renderPriorityBatches(panel) {
  const batches=priorityState.data.batches.sort((a,b)=>b.date.localeCompare(a.date));
  if (!batches.length) {panel.innerHTML='<div class="p-empty"><h4>还没有优先劣后批次</h4><p>上传客户名单和港安 ICC 文件后，这里会显示批次、协议金额与负责人。</p></div>'; return;}
  if (!batches.some(b=>b.date===priorityState.batch)) priorityState.batch=batches[0].date;
  const data=await pget(`batches/${priorityState.batch}/participations`);
  priorityState.rows=data.rows;
  panel.innerHTML=`<div class="p-panel-heading"><label>活动批次<select id="p-batch">${batches.map(b=>`<option value="${b.date}" ${b.date===priorityState.batch?'selected':''}>${b.date} · ${b.participants} 条参与 · ${pnum(b.agreementUsd)} USD</option>`).join('')}</select></label><span>${esc(data.filename)} · ${data.rows.length} 条活动记录</span></div>${pfilterMarkup(data.rows)}<p class="p-hint">保险经纪人和骄阳负责人保留本批次原始归属；修改会留下版本和原因。</p><div id="p-records"></div>`;
  panel.querySelector('#p-batch').onchange=async e=>{priorityState.batch=e.target.value;await renderPriorityBatches(panel);};
  const update=()=>{
    const rows=pfilterRows(data.rows);
    const cols=['customerName','twCode','insuranceBroker','jiaoyangOwner','serviceOwner','assignmentReason','agreementAmountUsd','brokerAccountStatus','depositAmount','onSiteOpener','zhongyangWitness','customerType','agreementSigned','notes'];
    panel.querySelector('#p-records').innerHTML=`<p class="p-count">筛选结果 ${rows.length} 条 · 协议金额 ${pnum(rows.reduce((s,r)=>s+Number(r.agreementAmountUsd||0),0))} USD</p><div class="p-table-wrap"><table class="p-table"><thead><tr>${cols.map(c=>`<th>${priorityLabels[c]}</th>`).join('')}<th>来源与操作</th></tr></thead><tbody>${rows.map((r,i)=>`<tr>${cols.map(c=>`<td>${['agreementAmountUsd','depositAmount'].includes(c)?pnum(r[c]):esc(r[c]||'—')}${c==='customerName' && pcanImport()?`<button class="p-link" data-p-edit-details="${i}">修改资料</button>`:''}${c==='customerName' && r.canonicalName && r.canonicalName!==r.customerName?`<small>标准姓名：${esc(r.canonicalName)}</small>`:''}${c==='twCode' && !r.twCode?(pcanImport()?`<button class="p-link p-pending" data-p-identity="${i}">匹配身份</button>`:'<span class="p-pending">待匹配</span>'):''}${c==='assignmentReason' && !r.serviceOwnerId && pcanAssign()?`<button class="p-link" data-p-assignment="${i}">立即处理</button>`:''}</td>`).join('')}<td><button class="p-link" data-p-follow="${i}">写跟进</button><button class="p-link" data-p-source="${i}">第 ${r.sourceRow} 行</button>${r.twCode?`<button class="p-link" data-p-customer="${esc(r.twCode)}">客户历史</button>`:''}${pcanImport()?`<button class="p-link" data-p-edit="${i}">${r.twCode?'补充 / 修订':'匹配身份'}</button>`:''}</td></tr>`).join('') || `<tr><td colspan="13">没有符合条件的记录。</td></tr>`}</tbody></table></div>`;
    panel.querySelectorAll('[data-p-customer]').forEach(b=>b.onclick=()=>priorityCustomerDetail(b.dataset.pCustomer));
    panel.querySelectorAll('[data-p-follow]').forEach(b=>{const r=rows[Number(b.dataset.pFollow)];b.onclick=()=>openWorkspaceNote(r.twCode?'tw:'+r.twCode:'icc:'+priorityState.batch+'/'+r.recordKey,'priority',priorityState.batch);});
    panel.querySelectorAll('[data-p-source]').forEach(b=>b.onclick=()=>prioritySource(rows[Number(b.dataset.pSource)],data));
    panel.querySelectorAll('[data-p-edit-details]').forEach(b=>b.onclick=()=>priorityOpenRecord(priorityState.batch,rows[Number(b.dataset.pEditDetails)].recordKey,'edit'));
    panel.querySelectorAll('[data-p-identity]').forEach(b=>b.onclick=()=>priorityOpenRecord(priorityState.batch,rows[Number(b.dataset.pIdentity)].recordKey,'identity'));
    panel.querySelectorAll('[data-p-assignment]').forEach(b=>b.onclick=()=>{const r=rows[Number(b.dataset.pAssignment)];priorityOpenRecord(priorityState.batch,r.recordKey,r.assignmentMode==='pending_binding'?(r.insuranceBroker?'binding':'broker'):'owner');});
    panel.querySelectorAll('[data-p-edit]').forEach(b=>b.onclick=()=>{const r=rows[Number(b.dataset.pEdit)];priorityOpenRecord(priorityState.batch,r.recordKey,r.twCode?'edit':'identity');});
  };
  pbindFilters(panel,update);update();
}
function renderPriorityCustomers(panel) {
  const all=priorityState.data.customers;
  panel.innerHTML=`<div class="p-panel-heading"><div><h4>客户综合查询</h4><p>资产截至 ${priorityState.data.dates.assets||'未上传'} · 持仓截至 ${priorityState.data.dates.secondary||'未上传'}。未出现在当期资料中的数值显示“未提供”。</p></div></div>${pfilterMarkup(all)}<label class="p-inline-filter">二级持仓<select id="p-secondary"><option value="">全部客户</option><option value="yes" ${priorityState.secondary==='yes'?'selected':''}>当前持有 XMax</option><option value="missing" ${priorityState.secondary==='missing'?'selected':''}>当期持仓资料未列入</option></select></label><div id="p-customer-table"></div>`;
  const update=()=>{
    const rows=pfilterRows(all).filter(r=>!priorityState.secondary || (priorityState.secondary==='yes'?r.isSecondary:!r.secondaryPresent));
    const cols=['customerName','twCode','brokerAccountStatus','insuranceBroker','jiaoyangOwner','serviceOwner','assignmentReason','currentOwner','assetUsd','quantity','agreementUsd'];
    panel.querySelector('#p-customer-table').innerHTML=`<p class="p-count">${rows.length} 位客户</p><div class="p-table-wrap"><table class="p-table"><thead><tr>${cols.map(c=>`<th>${priorityLabels[c]}</th>`).join('')}<th>参与日期</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${['assetUsd','quantity','agreementUsd'].includes(c)?pnum(r[c]):esc(r[c]||'—')}</td>`).join('')}<td>${r.batchDates.map(esc).join('、')||'—'}</td><td><button class="p-link" data-p-customer="${esc(r.twCode)}">查看历史</button></td></tr>`).join('')||'<tr><td colspan="11">没有符合条件的客户。</td></tr>'}</tbody></table></div>`;
    panel.querySelectorAll('[data-p-customer]').forEach(b=>b.onclick=()=>priorityCustomerDetail(b.dataset.pCustomer));
  };pbindFilters(panel,update);panel.querySelector('#p-secondary').onchange=e=>{priorityState.secondary=e.target.value;update();};update();
}
function prioritySource(row, data) {
  openModal(`<div class="modal p-modal"><div class="modal-header"><h3>${esc(row.customerName)} · 数据来源</h3><button class="close-btn" data-close>×</button></div><div class="modal-body"><p>${esc(data.filename)} / ${esc(row.sourceSheet)} / 第 ${row.sourceRow} 行</p><p>${esc(row.matchReason||'身份尚待匹配')}</p>${row.manualCorrection?`<p>最近人工修订：${esc(row.manualCorrection.reason)} · ${esc(row.manualCorrection.by)}</p>`:''}${row.carriedFields?`<p>以下空白字段沿用之前版本：${Object.keys(row.carriedFields).map(k=>esc(priorityLabels[k]||k)).join('、')}</p>`:''}${row.retainedFromRevision?'<p>本次修订表未列入此人，记录沿用之前版本。</p>':''}<table class="p-table"><tbody>${Object.entries(row.raw||{}).map(([c,v])=>`<tr><th>${esc(c)}</th><td>${esc(v)}</td></tr>`).join('')}</tbody></table></div></div>`);
}
async function priorityCustomerDetail(code) { return openCustomerCard("tw:"+code); }
async function priorityCustomerHistoryLegacy(code) {
  try {
    const data=await pget(`customers/${encodeURIComponent(code)}`);
    const section=(title,rows,fields)=>`<h4>${title}</h4><div class="p-table-wrap"><table class="p-table"><thead><tr><th>日期</th>${fields.map(f=>`<th>${priorityLabels[f]||f}</th>`).join('')}<th>来源</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${r.date}</td>${fields.map(f=>`<td>${['assetUsd','quantity','agreementAmountUsd'].includes(f)?pnum(r[f]):esc(r[f]||'—')}</td>`).join('')}<td>${esc(r.filename)} / ${r.sourceRow}${r.sourceRows?.length>1?`<small>合并 ${r.sourceRows.length} 个账户：${r.sourceRows.map(x=>`第 ${x.sourceRow} 行 ${pnum(x.assetUsd)} USD`).join('；')}</small>`:''}</td></tr>`).join('')||`<tr><td colspan="${fields.length+2}">暂无记录</td></tr>`}</tbody></table></div>`;
    openModal(`<div class="modal p-modal"><div class="modal-header"><h3>${esc(code)} · 客户历史</h3><button class="close-btn" data-close>×</button></div><div class="modal-body">${section('优先劣后批次',data.icc,['customerName','agreementAmountUsd','insuranceBroker','jiaoyangOwner'])}${section('资产快照（USD）',data.assets,['assetUsd'])}${section('XMax 持仓快照（股）',data.secondary,['quantity'])}${section('券商开户记录',data.master,['customerName','brokerAccountStatus'])}</div></div>`);
  } catch(e) {toast(e.message);}
}
async function priorityEdit(row, data) {
  let owners=[];
  if(state.user.canManageAssignments && state.user.customerScope==='all') {
    try {owners=(await pget('bindings')).owners;}catch(err){toast(err.message);return;}
  }

  const excluded=new Set([...(row.excludedTwCodes||[]),row.identityDetached].filter(Boolean));
  const candidates=priorityState.data.customers.filter(c=>!excluded.has(c.twCode)&&((row.candidates||[]).includes(c.twCode)||c.customerName===row.customerName));
  const fields=['twCode','brokerAccountStatus','depositAmount','onSiteOpener','insuranceBroker','zhongyangWitness','customerType','agreementSigned','agreementAmountUsd','jiaoyangOwner','notes'];
  openModal(`<div class="modal p-modal"><div class="modal-header"><h3>${esc(row.customerName)} · 补充业务资料</h3><button class="close-btn" data-close>×</button></div><form id="p-edit-form"><div class="modal-body"><p>批次 ${esc(priorityState.batch)}。原始文件保留，修订生成新版本。</p><p>同名不代表同一人。TW 留空可排除错误关联，批次资料及其业务跟进仍保留。后续名单出现新的唯一匹配时会自动补齐。</p>${row.twCode?'<button type="button" class="secondary-btn" id="p-detach">不是同一人，解除当前 TW 关联</button>':''}${!row.twCode && candidates.length?`<div class="notice"><strong>同名候选：点击选择，无需输入编号</strong><div class="p-candidate-list">${candidates.map(c=>`<button type="button" class="secondary-btn" data-identity-code="${esc(c.twCode)}">${esc(c.customerName)} · ${esc(c.twCode)} · ${esc(c.brokerAccountStatus||'状态未提供')}</button>`).join('')}</div></div>`:''}<div class="form-grid">${fields.map(f=> f==='jiaoyangOwner' ? `<div class="field"><label>领导指派负责人</label><select name="jiaoyangOwner" ${state.user.canManageAssignments?'':'disabled'}><option value="">未指派</option>${row.jiaoyangOwner && !owners.some(p=>p.name===row.jiaoyangOwner)?`<option selected value="${esc(row.jiaoyangOwner)}">${esc(row.jiaoyangOwner)}（原表姓名，待关联）</option>`:''}${owners.map(p=>`<option value="${esc(p.name)}" ${p.name===row.jiaoyangOwner?'selected':''}>${esc(p.name)} · ${esc(p.team)}</option>`).join('')}</select></div>` : `<div class="field"><label>${priorityLabels[f]}</label><input name="${f}" value="${esc(row[f]??'')}" ${['depositAmount','agreementAmountUsd'].includes(f)?'type="number" min="0" step="any"':''} ${f==='twCode'?'placeholder="从客户名单选择 TW" list="p-tw-options"':''}></div>`).join('')}<div class="field"><label>参与意向</label><select name="participationIntent"><option value="service" ${row.participationIntent!=='interested'?'selected':''}>普通服务 / 尚未明确意向</option><option value="interested" ${row.participationIntent==='interested'?'selected':''}>有意向参与，须领导指派</option></select></div><datalist id="p-tw-options">${priorityState.data.customers.map(c=>`<option value="${esc(c.twCode)}">${esc(c.customerName)}</option>`).join('')}</datalist><div class="field span-2"><label>修订原因</label><input name="reason" required placeholder="例如：核对原始资料后确认姓名，或补充本周负责人"></div></div><p id="p-edit-error" class="p-error" role="alert"></p></div><div class="modal-footer"><button type="button" class="secondary-btn" data-close>取消</button><button class="primary-btn">保存修订</button></div></form></div>`);
  document.querySelectorAll('[data-identity-code]').forEach(b=>b.onclick=()=>{const form=document.querySelector('#p-edit-form');form.elements.twCode.value=b.dataset.identityCode;form.elements.reason.value='从同名候选中确认客户身份';toast('已选择 '+b.dataset.identityCode+'，保存即可关联');});
  const detach=document.querySelector('#p-detach');
  if(detach) detach.onclick=()=>{const form=document.querySelector('#p-edit-form');form.elements.twCode.value='';form.elements.reason.value='核实为不同客户，解除同名误关联';form.elements.reason.focus();toast('已清空 TW，请保存修订以解除关联');};
  document.querySelector('#p-edit-form').onsubmit=async e=>{
    e.preventDefault();const button=e.submitter;setBusyButton(button,true);const values=Object.fromEntries(new FormData(e.target));const changes={};
    if(values.participationIntent!==(row.participationIntent||'service')) changes.participationIntent=values.participationIntent;
    for(const f of fields) if(values[f]!==undefined && values[f]!==String(row[f]??'')) changes[f]=values[f];
    try {if(!Object.keys(changes).length)throw new Error('还没有修改字段。');await pcall(`batches/${priorityState.batch}/records/${encodeURIComponent(row.recordKey)}`,{expectedRevision:data.revision,changes,reason:values.reason},'PATCH');closeModal();toast('修订已保存');await priorityRefreshAfterAction();} catch(err){document.querySelector('#p-edit-error').textContent=err.message;setBusyButton(button,false);}
  };
}

function renderPriorityUpload(panel) {
  const remembered=sessionStorage.getItem('priority_import_asof')||pdate();
  const batchDate=sessionStorage.getItem('priority_import_batch')||'2026-09-09';
  panel.innerHTML=`<div class="p-panel-heading"><div><h4>批量上传本周资料</h4><p>自动识别四种常用表格。先上传 ICC 记录活动；开户后上传客户名单、资产和持仓，系统自动补齐历史活动的 TW 并关联数据。客户名单只读第一个子表，资产只读 ABC。</p></div></div><form id="p-upload-form"><div class="p-upload-controls"><label>资产 / 持仓统计日期<input name="asOf" type="date" value="${esc(remembered)}" required></label><label>本次 ICC 批次日期<input name="batchDate" type="date" value="${esc(batchDate)}"></label></div><label class="p-drop" id="p-drop"><strong>把 Excel 拖到这里，或点击选择</strong><span>可一次选择客户名单、资产、XMax 持仓和港安 ICC 文件</span><input id="p-files" type="file" accept=".xlsx" multiple required></label><p id="p-file-names"></p><button class="primary-btn" type="submit">读取并核对变化</button><p id="p-upload-error" class="p-error" role="alert"></p></form><div id="p-preview"></div>`;
  const files=panel.querySelector('#p-files');
  let selected=[];
  const names=()=>panel.querySelector('#p-file-names').textContent=selected.map(f=>f.name).join('；');
  files.onchange=()=>{selected=[...files.files];names();};
  const drop=panel.querySelector('#p-drop');
  drop.ondragover=e=>{e.preventDefault();drop.classList.add('dragging');};drop.ondragleave=()=>drop.classList.remove('dragging');
  drop.ondrop=e=>{e.preventDefault();drop.classList.remove('dragging');files.files=e.dataTransfer.files;selected=[...files.files];names();};
  panel.querySelector('#p-upload-form').onsubmit=async e=>{
    e.preventDefault();const button=e.submitter;setBusyButton(button,true,'正在读取并核对…');panel.querySelector('#p-upload-error').textContent='';
    try {
      const values=Object.fromEntries(new FormData(e.target));
      sessionStorage.setItem('priority_import_asof',values.asOf);sessionStorage.setItem('priority_import_batch',values.batchDate);
      if(selected.some(f=>f.size>18000000)||selected.reduce((s,f)=>s+f.size,0)>45000000)throw new Error('单个文件请小于 18 MB，一次上传合计小于 45 MB。');
      const encoded=await Promise.all(selected.map(file=>new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve({filename:file.name,contentBase64:r.result.split(',')[1]});r.onerror=reject;r.readAsDataURL(file);} )));
      priorityState.preview=await pcall('imports/preview',{asOf:values.asOf,batchDates:values.batchDate?[values.batchDate]:[],files:encoded});
      if(state.view==='shared-import' && priorityState.preview.datasets.some(d=>d.kind==='icc' && !d.automaticIdentity)) {
        priorityState.preview=null;
        throw new Error('港安 ICC 活动表请在“优先劣后 → 资料导入”上传；这里更新共用客户名单、资产和持仓。');
      }
      renderPriorityPreview(panel.querySelector('#p-preview'));
    }catch(err){panel.querySelector('#p-upload-error').textContent=err.message||'读取失败，请重试。';}finally{setBusyButton(button,false);}
  };
  if(priorityState.preview)renderPriorityPreview(panel.querySelector('#p-preview'));
}
function priorityIdentitySummary(result) {
  if (!result) return '';
  const waiting=[], conflicts=[];
  for (const issue of result.issues || []) {
    const isWaiting=!(issue.candidates || []).length || (issue.reason || '').includes('等待');
    (isWaiting ? waiting : conflicts).push(issue);
  }
  const rows=(items, selectable=false)=>items.map(i=>`<li><strong>${esc(i.name)}</strong><span>活动批次 ${esc((i.key || '').replace('icc:',''))} · 原表第 ${esc(i.sourceRow)} 行</span><p>${esc(i.reason)}${i.candidates?.length?`（${i.candidates.map(esc).join('、')}）`:''}</p>${selectable && i.recordKey && i.candidates?.length?`<label class="p-preview-identity-label">为 ${esc(i.name)} 选择 TW<select data-preview-identity data-key="${esc(i.key)}" data-record="${esc(i.recordKey)}"><option value="">暂不选择，保存后再处理</option>${i.candidates.map(code=>`<option value="${esc(code)}">${esc(i.name)} · ${esc(code)}</option>`).join('')}</select></label><p class="p-selection-hint">未选择：保存后进入“优先劣后 → 批次记录 → ${esc((i.key||'').replace('icc:',''))}”，搜索“${esc(i.name)}”，点击“匹配身份”。</p>`:''}</li>`).join('');
  return `<section class="p-identity-summary" aria-label="历史活动身份关联结果">
    <div class="p-identity-cards">
      <div class="p-identity-card p-identity-ready"><span>可自动关联</span><strong>${pnum(result.autoMatched)}<small> 条</small></strong><p>保存本次上传后生效，无需逐条确认。</p></div>
      <div class="p-identity-card"><span>等待开户 / 更新名单</span><strong>${waiting.length}<small> 条</small></strong><p>暂时无需处理，下次上传名单自动重试。</p></div>
      <div class="p-identity-card ${conflicts.length?'p-identity-conflict':''}"><span>需要处理的身份冲突</span><strong>${conflicts.length}<small> 条</small></strong><p>${conflicts.length?'可在下方直接选择 TW，随本次上传一起保存。':'目前没有需要处理的身份冲突。'}</p></div>
    </div>
    <p class="p-identity-context">以下是本次核对涉及的历史活动记录，可能跨多个批次。等待和冲突记录会保留，不影响其他资料保存。</p>
    ${conflicts.length?`<details class="p-identity-details p-identity-conflict" open><summary>需要处理：${conflicts.length} 条身份冲突</summary><ul>${rows(conflicts,true)}</ul></details>`:''}
    ${waiting.length?`<details class="p-identity-details"><summary>暂时无需处理：${waiting.length} 条等待开户 / 更新名单（展开查看）</summary><p>尚未找到足够的匹配信息，不代表数据错误，也不一定尚未开户。</p><ul>${rows(waiting)}</ul></details>`:''}
  </section>`;
}

function renderPriorityPreview(container) {
  const preview=priorityState.preview;
  container.innerHTML=`<div class="p-preview"><h4>本次上传变化</h4>${priorityIdentitySummary(preview.identityReconciliation)}${preview.sharedCustomers?`<div class="notice"><strong>共用客户池：新增 ${preview.sharedCustomers.createdCount} 人，补关联 ${preview.sharedCustomers.linkedCount} 个 TW。</strong><p>两个业务共用这份名单。新增客户不自动参与产品，也不自动继承另一个产品的负责人；撤销来源上传不删除共用客户主档。</p>${preview.sharedCustomers.conflicts.map(c=>`<p>${esc(c.twCode)}：${esc(c.reason)}</p>`).join('')}</div>`:''}${preview.datasets.map(d=>`<article><b>${esc(d.label)} · ${d.date}</b><span>${d.records} 条记录${d.kind==='icc'?` · ${d.participants} 条参与 · ${pnum(d.agreementUsd)} USD · ${d.pending} 条身份待匹配`:''}${d.kind==='assets'?` · ${pnum(d.assetUsd)} USD`:''}${d.kind==='secondary'?` · ${pnum(d.quantity)} 股`:''}</span><p>${d.duplicate?'与当前资料相同，将自动跳过':`${d.changes.length} 条新增或变化`}${d.missingCount?`；${d.missingCount} 条未出现在新文件中，${d.kind==='icc'?'保留原批次记录':'当期快照按新文件列示，旧版可追溯'}`:''}</p>${!d.duplicate&&d.changes.length?`<details><summary>查看变化明细</summary><div class="p-change-list">${d.changes.map(c=>`<p><b>${esc(c.name)} ${esc(c.twCode||'')}</b>${d.kind==='icc'?`<small>${c.twCode?'关联已有 TW 客户（不是新增客户）':'身份待确认，未关联已有客户'}</small>`:''} ${c.fields.map(f=>`${esc(priorityLabels[f]||f)}${c.before?`：${esc(c.before[f]??'空白')} → ${esc(c.after[f]??'空白')}`:''}`).join('；')}</p>`).join('')}</div></details>`:''}</article>`).join('')}${preview.issues.length && !preview.identityReconciliation?`<details open><summary>${preview.issues.length} 条身份待确认</summary><p>可以先保存活动。以后上传更新的客户名单，系统会自动重试关联；仅同名冲突需到“匹配身份”选择客户，无需逐条输入编号。</p>${preview.issues.map(i=>`<p>${esc(i.name)} · 原表第 ${i.sourceRow} 行 · ${i.candidates.length?'同名候选，尚未关联（需核对 TW）':'身份待确认，尚未关联'}${i.candidates.length?`（${i.candidates.map(esc).join('、')}）`:''}</p>`).join('')}</details>`:''}<button id="p-commit" class="primary-btn">确认保存本次资料</button><p id="p-commit-error" class="p-error" role="alert"></p></div>`;
  container.querySelectorAll('[data-preview-identity]').forEach(select=>select.onchange=()=>{select.closest('li').querySelector('.p-selection-hint').textContent=select.value?`已选择 ${select.value}，点击底部“确认保存本次资料”后生效。`:`暂不选择：保存后到“优先劣后 → 批次记录 → ${select.dataset.key.replace('icc:','')} → 搜索客户 → 匹配身份”处理。`;});
  container.querySelector('#p-commit').onclick=async e=>{setBusyButton(e.target,true,'正在保存…');try{const choices=[...container.querySelectorAll('[data-preview-identity]')].filter(s=>s.value).map(s=>({key:s.dataset.key,recordKey:s.dataset.record,twCode:s.value}));const unresolved=(preview.identityReconciliation?.issues||[]).filter(i=>i.candidates?.length && !(i.reason||'').includes('等待') && !choices.some(c=>c.key===i.key&&c.recordKey===i.recordKey));await pcall(`imports/${preview.id}/commit`,{identities:choices});priorityState.preview=null;priorityState.tab='batches';priorityState.asOf='';if(state.view==='shared-import')await navigate('directory');else await renderPriority(document.querySelector('#content'));toast('本次资料与共用客户名单已保存');if(unresolved.length)openModal(`<div class="modal quick-modal"><div class="modal-header"><h3>资料已保存，还有 ${unresolved.length} 条身份待选择</h3><button class="close-btn" data-close>×</button></div><div class="modal-body"><p>其他资料已保存。点击客户右侧“立即匹配”即可处理，或稍后从“待处理中心”继续。</p><ul>${unresolved.map(i=>`<li>${esc(i.key.replace('icc:',''))} · ${esc(i.name)} <button class="p-link" data-saved-identity data-batch="${esc(i.key.replace('icc:',''))}" data-record="${esc(i.recordKey)}">立即匹配</button></li>`).join('')}</ul></div><div class="modal-footer"><button class="secondary-btn" data-close>稍后处理</button><button class="primary-btn" id="p-saved-tasks">打开待处理中心</button></div></div>`);document.querySelectorAll('[data-saved-identity]').forEach(b=>b.onclick=()=>priorityOpenRecord(b.dataset.batch,b.dataset.record,'identity'));document.querySelector('#p-saved-tasks')?.addEventListener('click',priorityOpenTasks);}catch(err){container.querySelector('#p-commit-error').textContent=err.message;setBusyButton(e.target,false);}};
}
async function renderPriorityHistory(panel) {
  const jobs=await pget('imports');
  panel.innerHTML=`<div class="p-panel-heading"><div><h4>上传与修订历史</h4><p>保留来源和历史版本。已有后续修订的资料，需要先撤销后续修订。</p></div></div><div class="p-history">${jobs.map(j=>`<article><div><b>${esc(j.createdAt)}</b><p>${j.datasets.map(d=>`${esc(d.label)} ${d.date}（${d.records} 条）`).join('；')}</p><span>${j.status==='rolled_back'?'已撤销':'已保存'}</span></div>${j.status==='committed'?`<button class="secondary-btn" data-p-rollback="${j.id}">撤销本次</button>`:''}</article>`).join('')||'<p>尚无上传记录。</p>'}</div><p id="p-history-error" class="p-error" role="alert"></p>`;
  panel.querySelectorAll('[data-p-rollback]').forEach(b=>b.onclick=()=>{
    openModal(`<div class="modal quick-modal"><div class="modal-header"><h3>撤销这次上传或修订</h3><button class="close-btn" data-close>×</button></div><div class="modal-body"><p>查询结果将恢复到之前版本。原始文件和操作历史仍会保留。</p><p id="p-rollback-error" class="p-error"></p></div><div class="modal-footer"><button class="secondary-btn" data-close>取消</button><button id="p-do-rollback" class="primary-btn">确认撤销</button></div></div>`);
    document.querySelector('#p-do-rollback').onclick=async e=>{setBusyButton(e.target,true);try{await pcall(`imports/${b.dataset.pRollback}/rollback`,{});closeModal();await renderPriority(document.querySelector('#content'));toast('已恢复到之前版本');}catch(err){document.querySelector('#p-rollback-error').textContent=err.message;setBusyButton(e.target,false);}};
  });
}

async function renderPriorityBindings(panel) {
  const data=await pget('bindings');
  const ownerNames=Object.fromEntries(data.owners.map(p=>[p.id,p.name]));
  panel.innerHTML=`<div class="assignment-intro"><h3>负责人分配</h3><p>两种分配方式：领导可以单独指派客户；普通服务客户按保险经纪人自动分配。仅影响优先劣后业务。</p></div><div class="assignment-methods"><article><span class="assignment-step">方式一 · 人工优先</span><h4>领导指派负责人</h4><p>为具体客户指定骄阳负责人。已签约或有意向的客户，请使用此入口；人工指派不会被默认绑定覆盖。</p><button type="button" class="primary-btn" id="p-leader-assign">选择客户并指派负责人</button></article><article><span class="assignment-step">方式二 · 自动服务</span><h4>保险经纪人 → 骄阳负责人</h4><p>建立固定服务关系：这位经纪人名下、没有人工指派、未签约且未明确参与意向的客户，自动交给绑定的骄阳负责人。</p><button type="button" class="secondary-btn" id="p-binding-focus">设置经纪人默认绑定</button></article></div><h4>经纪人默认绑定设置</h4><p class="p-hint">例如：保险经纪人 A → 骄阳负责人 B。保存前可预览受影响的客户。</p><form id="p-binding-form"><div class="form-grid"><div class="field"><label>保险经纪人</label><input name="broker" required list="p-binding-brokers"><datalist id="p-binding-brokers">${[...new Set(data.assignments.map(a=>a.insuranceBroker).filter(Boolean))].map(b=>`<option>${esc(b)}</option>`).join('')}</datalist></div><div class="field"><label>绑定到哪位骄阳负责人</label><select name="ownerId" required><option value="">请选择系统账号</option>${data.owners.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.team)}</option>`).join('')}</select></div><div class="field"><label>状态</label><select name="active"><option value="true">启用</option><option value="false">停用</option></select></div><div class="field"><label>变更原因</label><input name="reason" required></div></div><button class="primary-btn">预览影响后保存</button></form><p id="p-binding-error" class="p-error"></p><div id="p-binding-preview"></div><h4>已建立的经纪人 → 骄阳负责人绑定</h4><table class="p-table"><thead><tr><th>保险经纪人</th><th>服务负责人</th><th>状态</th><th></th></tr></thead><tbody>${data.rules.map((r,i)=>`<tr><td>${esc(r.broker_name)}</td><td>${esc(ownerNames[r.owner_id]||'账号已停用')}</td><td>${r.active?'启用':'停用'}</td><td><button class="p-link" data-rule="${i}">修改</button></td></tr>`).join('')}</tbody></table><h4>客户当前分配结果</h4><div class="p-table-wrap"><table class="p-table"><thead><tr><th>客户</th><th>保险经纪人</th><th>服务负责人</th><th>分配来源 / 待办</th></tr></thead><tbody>${data.assignments.map(a=>`<tr><td>${esc(a.customerName)}</td><td>${esc(a.insuranceBroker)}</td><td>${esc(a.serviceOwner||'待分配')}</td><td>${esc(a.assignmentReason)} <button class="p-link" data-assignment-key="${esc(a.key)}">指派 / 修改负责人</button>${a.insuranceBroker?`<button class="p-link" data-assignment-broker="${esc(a.insuranceBroker)}">修改经纪人绑定</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;
  panel.querySelectorAll('[data-assignment-key]').forEach(b=>b.onclick=()=>priorityOpenAssignment(b.dataset.assignmentKey));
  panel.querySelectorAll('[data-assignment-broker]').forEach(b=>b.onclick=()=>priorityQuickBinding(b.dataset.assignmentBroker).catch(err=>toast(err.message)));
  const form=panel.querySelector('form');
  panel.querySelector('#p-leader-assign').onclick=()=>priorityLeaderAssignment(data.owners);
  panel.querySelector('#p-binding-focus').onclick=()=>{form.scrollIntoView({behavior:'smooth',block:'center'});form.elements.broker.focus();};
  panel.querySelectorAll('[data-rule]').forEach(b=>b.onclick=()=>{const r=data.rules[Number(b.dataset.rule)];form.elements.broker.value=r.broker_name;form.elements.ownerId.value=r.owner_id;form.elements.active.value=String(Boolean(r.active));form.elements.reason.value='';panel.querySelector('#p-binding-preview').innerHTML='';});
  form.onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(new FormData(form));values.active=values.active==='true';setBusyButton(e.submitter,true);try{
    const result=await pcall('bindings/preview',values);
    const area=panel.querySelector('#p-binding-preview');
    area.innerHTML=`<h4>本次影响：${result.affected.filter(a=>a.changed).length} 位客户发生变化</h4><div class="p-table-wrap"><table class="p-table"><thead><tr><th>客户</th><th>之前负责人</th><th>保存后负责人</th><th>处理依据</th></tr></thead><tbody>${result.affected.map(a=>`<tr><td>${esc(a.customerName)}</td><td>${esc(a.beforeOwner||'待分配')}</td><td>${esc(a.serviceOwner||'待分配')}</td><td>${esc(a.assignmentReason)}${a.changed?'':'（保持）'}</td></tr>`).join('')}</tbody></table></div><button id="p-save-binding" class="primary-btn">确认保存绑定并生效</button>`;
    area.querySelector('button').onclick=async event=>{setBusyButton(event.target,true);try{await pcall('bindings/save',{...values,token:result.token});toast('绑定已生效');await renderPriorityBindings(panel);}catch(err){panel.querySelector('#p-binding-error').textContent=err.message;setBusyButton(event.target,false);}};
  }catch(err){panel.querySelector('#p-binding-error').textContent=err.message;}finally{setBusyButton(e.submitter,false);}};
}

async function priorityLeaderAssignment(owners) {
  try {
    const batches=(await pget('batches')).sort((a,b)=>b.date.localeCompare(a.date));
    if(!batches.length){toast('请先导入港安 ICC 批次资料，再指派负责人。');return;}
    openModal(`<div class="modal p-modal"><div class="modal-header"><div><h3>领导指派负责人</h3><p>选择批次和客户，为这位客户单独指定骄阳负责人。</p></div><button class="close-btn" data-close>×</button></div><form id="leader-form"><div class="modal-body"><div class="form-grid"><div class="field"><label for="leader-batch">客户所在批次</label><select id="leader-batch">${batches.map(b=>`<option value="${b.date}">${b.date}</option>`).join('')}</select></div><div class="field"><label for="leader-search">搜索本批次客户</label><input id="leader-search" type="search" placeholder="姓名或 TW 编号"></div></div><div id="leader-results" class="customer-search-results" aria-live="polite"></div><p id="leader-selected" class="customer-search-selection">请点击选择客户</p><div class="form-grid"><div class="field"><label for="leader-owner">指派给哪位骄阳负责人</label><select id="leader-owner" required><option value="">请选择负责人</option>${owners.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.team)}</option>`).join('')}</select></div><div class="field"><label for="leader-reason">指派原因</label><input id="leader-reason" required maxlength="1000" placeholder="例如：组长安排跟进本批次客户"></div></div><p class="p-hint">按本批次保存指派并记录历史。同一客户跨批次的当前服务负责人，采用最近批次的非空指派；原表负责人仍可在批次修订中清空。</p><p id="leader-error" class="p-error" role="alert"></p></div><div class="modal-footer"><button type="button" class="secondary-btn" data-close>取消</button><button id="leader-save" class="primary-btn" disabled>确认指派负责人</button></div></form></div>`);
    const form=document.querySelector('#leader-form');let dataset=null,selected=null,seq=0;
    const render=()=>{const q=form.querySelector('#leader-search').value.replace(/\s+/g,'').toLowerCase();const rows=(dataset?.rows||[]).filter(r=>!q||[r.customerName,r.canonicalName,r.twCode].some(v=>String(v||'').replace(/\s+/g,'').toLowerCase().includes(q)));form.querySelector('#leader-results').innerHTML=`<p class="hint">${rows.length} 条匹配记录 · 点击选择客户${rows.length>20?'（先显示 20 条）':''}</p>`+rows.slice(0,20).map((r,i)=>`<button type="button" class="customer-search-result" data-leader-row="${i}" aria-pressed="${selected?.recordKey===r.recordKey}"><strong>${esc(r.customerName)} · ${esc(r.twCode||'身份待确认')}</strong><span>当前服务负责人：${esc(r.serviceOwner||'待分配')} · ${esc(r.assignmentReason)}</span></button>`).join('')+(rows.length?'':'<p>没有找到匹配客户，请调整姓名或批次。</p>');form.querySelectorAll('[data-leader-row]').forEach(b=>b.onclick=()=>{selected=rows[+b.dataset.leaderRow];form.querySelector('#leader-selected').textContent=`已选择：${selected.customerName} · ${selected.twCode||'身份待确认'}`;form.querySelector('#leader-save').disabled=false;render();});};
    const load=async()=>{const request=++seq;selected=null;dataset=null;form.querySelector('#leader-selected').textContent='请点击选择客户';form.querySelector('#leader-save').disabled=true;form.querySelector('#leader-results').textContent='正在读取批次客户…';try{const result=await pget(`batches/${form.querySelector('#leader-batch').value}/participations`);if(request!==seq)return;dataset=result;render();}catch(err){form.querySelector('#leader-error').textContent=err.message;}};
    form.querySelector('#leader-batch').onchange=load;form.querySelector('#leader-search').oninput=render;form.querySelector('#leader-search').onkeydown=e=>{if(e.key==='Enter')e.preventDefault();};
    form.onsubmit=async e=>{e.preventDefault();if(!selected||!dataset)return;const owner=owners.find(p=>p.id===form.querySelector('#leader-owner').value);if(!owner)return;const reason=form.querySelector('#leader-reason').value.trim();if(!reason){form.querySelector('#leader-error').textContent='请填写指派原因。';return;}if(owners.filter(p=>p.name===owner.name).length!==1){form.querySelector('#leader-error').textContent='该姓名对应多个账号，请先由管理员核对同名账号后再指派。';return;}setBusyButton(e.submitter,true);try{await pcall(`batches/${form.querySelector('#leader-batch').value}/records/${encodeURIComponent(selected.recordKey)}`,{expectedRevision:dataset.revision,changes:{jiaoyangOwner:owner.name},reason},'PATCH');closeModal();toast('负责人指派已保存');await renderPriority(document.querySelector('#content'));}catch(err){form.querySelector('#leader-error').textContent=err.message;setBusyButton(e.submitter,false);}};
    await load();
  }catch(err){toast(err.message);}
}
