/* Direct actions always reload the exact record and its revision before editing. */
const pcanAssign = () => state.user.customerScope==='all' && state.user.canManageAssignments && state.user.canManageAdvisorBindings;
async function priorityOpenTasks() {
  closeModal();
  priorityState.tab='tasks';priorityState.asOf='';
  if(state.view==='priority') await renderPriority(document.querySelector('#content'));
  else await navigate('priority');
}
async function renderPriorityTasks(panel) {
  const data=await pget('tasks');
  const list=items=>items.map((t,i)=>`<article class="p-task-row"><div><strong>${esc(t.action==='binding'?'保险经纪人：'+t.broker:t.name)}</strong><span>${t.action==='binding'?`${t.affectedCount||1} 位客户待设置服务归属`:`${esc(t.batch)} · ${esc(t.twCode||'尚未关联 TW')}`}</span><p>${esc(t.reason)}</p></div><button class="primary-btn" data-task="${i}">${esc(t.label)}</button></article>`).join('');
  panel.innerHTML=`<div class="p-panel-heading"><div><h4>待处理中心 · ${data.items.length} 项</h4><p>点击右侧按钮直接处理，保存后自动移出已完成事项。</p></div><button class="secondary-btn" id="p-task-refresh">刷新</button></div><div class="p-task-list">${list(data.items)||'<p class="p-empty">当前没有需要你处理的事项。</p>'}</div>${data.waiting.length?`<details class="p-task-waiting"><summary>${data.waiting.length} 条等待开户 / 更新名单，暂时无需处理</summary><p class="p-hint">下次上传名单会自动重试；如已核实身份，也可以直接匹配。</p><div>${list(data.waiting)}</div></details>`:''}`;
  const bind=(root,items)=>root.querySelectorAll('[data-task]').forEach(b=>b.onclick=()=>{const t=items[Number(b.dataset.task)];priorityOpenRecord(t.batch,t.recordKey,t.action);});
  bind(panel.querySelector('.p-task-list'),data.items);
  if(data.waiting.length)bind(panel.querySelector('.p-task-waiting'),data.waiting);
  panel.querySelector('#p-task-refresh').onclick=()=>renderPriorityTasks(panel);
}
async function priorityRefreshAfterAction() {
  if(state.view==='priority') await renderPriority(document.querySelector('#content'));
  else await navigate(state.view);
}
async function priorityOpenRecord(batch, recordKey, action='edit') {
  try {
    if(action==='broker' && !pcanImport())throw new Error('需由有资料修订权限的管理员补充保险经纪人。');
    const data=await pget(`batches/${encodeURIComponent(batch)}/participations`);
    const row=data.rows.find(r=>r.recordKey===recordKey);
    if(!row)throw new Error('该记录已变更，请刷新后重试。');
    if(action==='edit') {
      priorityState.batch=batch;
      priorityState.data=await pget('overview');
      await priorityEdit(row,data);return;
    }
    if(action==='binding'){await priorityQuickBinding(row.insuranceBroker);return;}
    const title={identity:'匹配客户身份',owner:'指派负责人',broker:'补充保险经纪人'}[action];
    let people=[],owners=[];
    if(action==='identity') people=(await pget('overview')).customers;
    if(action==='owner') owners=(await pget('bindings')).owners;
    const excluded=new Set([...(row.excludedTwCodes||[]),row.identityDetached].filter(Boolean));
    people=people.filter(c=>!excluded.has(c.twCode));
    const suggestions=people.filter(c=>(row.candidates||[]).includes(c.twCode)||c.customerName===row.customerName);
    const duplicateNames=new Set(owners.filter(p=>owners.filter(q=>q.name===p.name).length>1).map(p=>p.name));
    openModal(`<div class="modal quick-modal"><div class="modal-header"><div><h3>${title}</h3><p>${esc(row.customerName)} · ${esc(batch)}</p></div><button class="close-btn" data-close>×</button></div><form id="p-quick-action"><div class="modal-body">${action==='identity'?`<p>${esc(row.matchReason||'选择已核实的客户编号')}</p><label class="p-preview-identity-label">搜索其他客户<input type="search" id="p-quick-search" placeholder="姓名或 TW"></label><label class="p-preview-identity-label">确认关联哪位客户<select name="twCode" id="p-quick-customer" required><option value="">请选择已核实的客户</option></select></label><p class="p-hint">只关联这条活动，保留原始资料和跟进。</p>${row.twCode?`<button type="button" class="secondary-btn" id="p-quick-detach">不是同一人，解除 ${esc(row.twCode)} 的关联</button>`:''}`:action==='owner'?`<p class="notice">负责人全产品共用：保存后同步更新定增、优先劣后和客户卡片的归属及可见范围。未关联 TW 时先保存活动指派。</p><p>${esc(row.assignmentReason||'选择负责服务这位客户的商务经理')}</p><label class="p-preview-identity-label">骄阳负责人<select name="jiaoyangOwner" required><option value="">请选择负责人</option>${owners.map(p=>`<option value="${esc(p.name)}" ${duplicateNames.has(p.name)?'disabled':''}>${esc(p.name)} · ${esc(p.team)}${duplicateNames.has(p.name)?'（账号重名，请先处理）':''}</option>`).join('')}</select></label>`:`<label class="p-preview-identity-label">保险经纪人<input name="insuranceBroker" required value="${esc(row.insuranceBroker||'')}"></label>`}<details class="p-quick-reason"><summary>补充修改说明（可选）</summary><input name="reason" maxlength="1000" placeholder="系统会自动记录本次操作"></details><p class="p-error" id="p-quick-error" role="alert"></p></div><div class="modal-footer"><button type="button" class="secondary-btn" data-close>取消</button><button class="primary-btn">确认保存</button></div></form></div>`);
    const form=document.querySelector('#p-quick-action');
    if(action==='identity') {
      const render=()=>{const q=form.querySelector('#p-quick-search').value.trim().toLowerCase();const options=q?people.filter(c=>[c.customerName,c.twCode].some(v=>String(v||'').toLowerCase().includes(q))):suggestions;form.querySelector('#p-quick-customer').innerHTML='<option value="">'+(options.length?'请选择已核实的客户':'没有候选，请搜索姓名或 TW')+'</option>'+options.map(c=>`<option value="${esc(c.twCode)}">${esc(c.customerName)} · ${esc(c.twCode)} · ${esc(c.brokerAccountStatus||'状态未提供')}</option>`).join('');};
      form.querySelector('#p-quick-search').oninput=()=>{form.querySelector('#p-quick-customer').required=true;render();};render();
      if(row.twCode)form.querySelector('#p-quick-customer').value=row.twCode;
      form.querySelector('#p-quick-detach')?.addEventListener('click',()=>{form.querySelector('#p-quick-customer').required=false;form.querySelector('#p-quick-customer').value='';form.elements.reason.value='核实为不同人，解除错误 TW 关联';form.querySelector('.primary-btn').textContent='确认解除关联';});
    }
    form.onsubmit=async e=>{e.preventDefault();setBusyButton(e.submitter,true);const values=Object.fromEntries(new FormData(form));const reason=values.reason.trim()||title;delete values.reason;try{await pcall(`batches/${batch}/records/${encodeURIComponent(recordKey)}`,{expectedRevision:data.revision,expectedCustomerVersion:row.customerVersion,changes:values,reason},'PATCH');closeModal();toast('已保存');await priorityRefreshAfterAction();}catch(err){form.querySelector('#p-quick-error').textContent=err.message;setBusyButton(e.submitter,false);}};
  }catch(err){toast(err.message);}
}
async function priorityQuickBinding(broker) {
  const data=await pget('bindings');
  if(!broker)throw new Error('请先补充这位客户的保险经纪人。');
  openModal(`<div class="modal quick-modal"><div class="modal-header"><h3>设置经纪人绑定 · ${esc(broker)}</h3><button class="close-btn" data-close>×</button></div><form id="p-quick-binding"><div class="modal-body"><label class="p-preview-identity-label">默认骄阳负责人<select name="ownerId" required><option value="">请选择负责人</option>${data.owners.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.team)}</option>`).join('')}</select></label><p class="p-hint">适用于该经纪人的普通服务客户；仅补全尚无负责人的客户。已有归属保持不变；调整已有客户请使用“指派负责人”。</p><div id="p-quick-impact"></div><p class="p-error" id="p-quick-binding-error" role="alert"></p></div><div class="modal-footer"><button type="button" class="secondary-btn" data-close>取消</button><button class="primary-btn">预览影响</button></div></form></div>`);
  const form=document.querySelector('#p-quick-binding');let impact=null;
  form.elements.ownerId.onchange=()=>{impact=null;form.querySelector('#p-quick-impact').innerHTML='';form.querySelector('.primary-btn').textContent='预览影响';};
  form.onsubmit=async e=>{e.preventDefault();setBusyButton(e.submitter,true);const values={broker,ownerId:form.elements.ownerId.value,active:true,reason:'待处理快捷设置经纪人绑定'};try{if(!impact){impact=await pcall('bindings/preview',values);form.querySelector('#p-quick-impact').innerHTML=`<p><strong>${impact.affected.filter(a=>a.changed).length} 位客户的服务归属将更新。</strong></p>`;setBusyButton(e.submitter,false);e.submitter.textContent='确认保存绑定';return;}await pcall('bindings/save',{...values,token:impact.token});closeModal();toast('绑定已保存');await priorityRefreshAfterAction();}catch(err){form.querySelector('#p-quick-binding-error').textContent=err.message;setBusyButton(e.submitter,false);}};
}

async function priorityOpenAssignment(key) {
  try {
    if(key.startsWith('icc:')){const slash=key.indexOf('/');return await priorityOpenRecord(key.slice(4,slash),key.slice(slash+1),'owner');}
    const history=await pget(`customers/${encodeURIComponent(key)}`);
    const row=history.icc.slice().sort((a,b)=>b.date.localeCompare(a.date))[0];
    if(!row)throw new Error('没有可修改的活动记录。');
    await priorityOpenRecord(row.date,row.recordKey,'owner');
  }catch(err){toast(err.message);}
}
