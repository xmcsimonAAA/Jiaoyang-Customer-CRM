/* Real participation-batch grouping for weekly priority-inferior activities. */

function priorityProductBatchCanEdit() {
  return Boolean(state.user?.customerScope === 'all' && state.user?.canImportCustomers && state.user?.canManageAdvisorBindings);
}

/* Status based roster for actual product follow-up.  The legacy activity table
 * above remains available for source auditing; these views are the operational
 * customer lists and therefore exclude zero-intent rows. */
function priorityRosterEditor(batch, row, board, refresh) {
  const editing = Boolean(row?.id);
  const candidates = (priorityState.data?.customers || []).filter(p => !row || p.twCode === row.twCode || p.name === row.name || p.customerName === row.name);
  openModal(`<div class="modal quick-modal"><div class="modal-header"><div><h3>${editing ? '编辑批次客户' : '手动添加跟进客户'}</h3><span class="hint">${board === 'block' ? '大宗交易' : '优先劣后'} · 本批意向金额与实际参与数据分开保存</span></div><button class="close-btn" data-close>×</button></div><form id="priority-roster-form"><div class="modal-body"><div class="field"><label>客户（TW 或姓名） *</label><input name="customer" required list="priority-roster-customers" value="${esc(row?.twCode || row?.name || '')}" ${editing ? 'readonly' : ''}><datalist id="priority-roster-customers">${candidates.concat(priorityState.data?.customers || []).filter((v,i,a)=>a.findIndex(x=>(x.twCode||x.name||x.customerName)===(v.twCode||v.name||v.customerName))===i).map(p=>`<option value="${esc(p.twCode || p.name || p.customerName)}">${esc(p.name || p.customerName || '')}</option>`).join('')}</datalist></div><div class="form-grid"><div class="field"><label>${board === 'block' ? '大宗交易本批意向金额' : '优先劣后协议金额'} · USD *</label><input name="amount" type="number" min="0.01" step="any" required value="${esc(board === 'block' ? row?.block_amount || row?.blockAgreementAmountUsd || '' : row?.priority_amount || row?.agreementAmountUsd || '')}"></div>${board === 'block' ? '<div class="field"><label>实际参与金额 · USD</label><input name="actual" type="number" min="0" step="any" value="'+esc(row?.block_actual_amount || '')+'"></div><div class="field"><label>买入股价 · USD</label><input name="price" type="number" min="0" step="any" value="'+esc(row?.block_buy_price || '')+'"></div><div class="field"><label>参与股数（自动计算）</label><input name="shares" readonly value="'+esc(row?.block_share_quantity || '')+'"></div>' : '<div class="field"><label>实际参与金额 · USD</label><input name="actual" type="number" min="0" step="any" value="'+esc(row?.priority_actual_amount || '')+'"></div>'}</div><div class="field"><label>备注</label><textarea name="notes" maxlength="4000">${esc(row?.notes || '')}</textarea></div><p id="priority-roster-error" class="p-error" role="alert"></p></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存</button></div></form></div>`);
  const form = document.querySelector('#priority-roster-form');
  const actual = form.elements.actual, price = form.elements.price, shares = form.elements.shares;
  const calc = () => { if (shares && Number(actual.value) > 0 && Number(price.value) > 0) shares.value = (Number(actual.value) / Number(price.value)).toFixed(4).replace(/\.0000$/, ''); };
  actual?.addEventListener('input', calc); price?.addEventListener('input', calc); calc();
  form.onsubmit = async event => {
    event.preventDefault(); const values = Object.fromEntries(new FormData(form)); setBusyButton(event.submitter, true, '保存中…');
    try {
      const person = (priorityState.data?.customers || []).find(p => p.twCode === values.customer || p.name === values.customer || p.customerName === values.customer);
      const subjectKey = row?.subjectKey || person?.key || (values.customer.startsWith('TW') ? `tw:${values.customer}` : '');
      if (!subjectKey) throw new Error('请选择带有 TW 编号的客户，或先完成身份匹配。');
      const body = {subjectKey, board, status: row?.status || 'followup', customerName: person?.name || person?.customerName || row?.name || values.customer, twCode: person?.twCode || row?.twCode || (values.customer.startsWith('TW') ? values.customer : ''), priorityAmount: board === 'priority' ? values.amount : row?.priority_amount, blockAmount: board === 'block' ? values.amount : row?.block_amount, priorityActualAmount: board === 'priority' ? values.actual : undefined, blockActualAmount: board === 'block' ? values.actual : undefined, blockBuyPrice: board === 'block' ? values.price : undefined, blockShareQuantity: board === 'block' ? values.shares : undefined, notes: values.notes};
      await pcall(`product-batches/${encodeURIComponent(batch.id)}/roster${row?.id ? `/${encodeURIComponent(row.id)}` : ''}`, body, row?.id ? 'PATCH' : 'POST');
      closeModal(); toast('批次客户已保存'); await refresh();
    } catch (error) { form.querySelector('#priority-roster-error').textContent = error.message || '保存失败'; setBusyButton(event.submitter, false); }
  };
}

function priorityRosterTable(batch, rows, board, refresh) {
  const amount = board === 'block' ? 'block_amount' : 'priority_amount';
  const followups = rows.filter(r => r.status === 'followup');
  const completed = rows.filter(r => r.status === 'completed');
  const abandoned = rows.filter(r => r.status === 'abandoned');
  const cells = row => `<tr><td>${esc(row.name || row.customerName || '未命名客户')}<small>${esc(row.twCode || '身份待确认')}</small></td><td>${pnum(row[amount] || 0)}</td>${board === 'block' ? `<td><input class="p-roster-input" data-roster-field="actual" data-roster-id="${row.id}" value="${esc(row.block_actual_amount || '')}" type="number" min="0" step="any"></td><td><input class="p-roster-input" data-roster-field="price" data-roster-id="${row.id}" value="${esc(row.block_buy_price || '')}" type="number" min="0" step="any"></td><td>${esc(row.block_share_quantity || '—')}</td>` : `<td><input class="p-roster-input" data-roster-field="actual" data-roster-id="${row.id}" value="${esc(row.priority_actual_amount || '')}" type="number" min="0" step="any"></td>`}<td>${row.status === 'followup' ? `<button class="p-link" data-roster-follow="${row.id}">写跟进</button> <button class="p-link" data-roster-save="${row.id}">保存</button> <button class="p-link" data-roster-complete="${row.id}">完成</button> <button class="p-link danger-link" data-roster-abandon="${row.id}">确认放弃</button> <button class="p-link" data-roster-remove="${row.id}">移出本批</button>` : row.status === 'abandoned' ? `<button class="p-link" data-roster-restore="${row.id}">恢复跟进</button>` : '已完成'}</td></tr>`;
  const section = (title, list, empty) => `<div class="priority-roster-section"><div class="section-header"><h5>${title}</h5><span class="hint">${list.length} 人</span></div>${list.length ? `<div class="p-table-wrap"><table class="p-table"><thead><tr><th>客户</th><th>本批意向金额 · USD</th>${board === 'block' ? '<th>实际参与金额 · USD</th><th>买入股价 · USD</th><th>参与股数</th>' : '<th>实际参与金额 · USD</th>'}<th>操作</th></tr></thead><tbody>${list.map(cells).join('')}</tbody></table></div>` : `<p class="hint">${empty}</p>`}</div>`;
  return `<div class="priority-roster" data-roster-board="${board}"><div class="section-header"><div><h5>${board === 'block' ? '大宗交易跟进客户表' : '优先劣后跟进客户表'}</h5><span class="hint">协议/意向金额为本批意向；完成后客户进入批次卡片，放弃客户进入汇总。</span></div><button class="secondary-btn" data-roster-add>＋ 手动添加</button></div>${section('跟进中', followups, '当前没有待跟进客户。')}${section('已完成 · 批次卡片', completed, '还没有完成客户。')}${section('已放弃 · 可恢复', abandoned, '暂无放弃客户。')}</div>`;
}

async function renderPriorityRosterBatches(panel, board) {
  panel.innerHTML = '<div class="p-empty"><p role="status">正在读取批次客户…</p></div>';
  try {
    const data = await pget('product-batches'); const canEdit = priorityProductBatchCanEdit();
    const refresh = async () => renderPriorityRosterBatches(panel, board);
    panel.innerHTML = `<div class="p-panel-heading"><div><h4>${board === 'block' ? '大宗交易' : '跟进客户表'}</h4><p>${board === 'block' ? '填写实际参与金额和买入股价后计算参与股数，确认完成才进入大宗交易卡片。' : '只展示协议金额大于 0 的签约客户；手动完成后进入优先劣后批次卡片。'}</p></div></div><div class="priority-product-batch-list">${data.items?.length ? data.items.map(item => `<article class="priority-product-batch-card"><div class="section-header"><div><span class="eyebrow">${esc(item.participationDate || item.participation_date || '')}</span><h4>${esc(item.name)}</h4><p>${esc(item.activityDates?.join('、') || '')} · ${esc(item.status)}</p></div>${canEdit ? `<button class="secondary-btn" data-edit-product-batch="${esc(item.id)}">编辑批次</button>` : ''}</div><div class="p-metrics"><article><span>优先劣后意向金额</span><strong>${pnum(item.agreementUsd || 0)}</strong></article><article><span>大宗交易意向金额</span><strong>${pnum(item.blockAgreementUsd || 0)}</strong></article></div>${priorityRosterTable(item, board === 'block' ? (item.blockRows || []) : (item.rosterRows || []), board, refresh)}</article>`).join('') : '<div class="p-empty"><h4>还没有批次</h4><p>先创建一个活动参与批次。</p></div>'}</div>`;
    const abandoned = data.items.flatMap(item => (board === 'block' ? (item.blockRows || []) : (item.rosterRows || [])).filter(row => row.status === 'abandoned').map(row => ({...row, batchName: item.name})));
    if (abandoned.length) panel.querySelector('.priority-product-batch-list').insertAdjacentHTML('afterbegin', `<section class="priority-abandoned-summary"><div class="section-header"><div><h4>全部已放弃客户</h4><span class="hint">${abandoned.length} 人 · 可在这里恢复到原批次跟进表</span></div></div><div class="p-table-wrap"><table class="p-table"><thead><tr><th>客户</th><th>所属批次</th><th>意向金额 · USD</th><th>操作</th></tr></thead><tbody>${abandoned.map(row => `<tr><td>${esc(row.name || row.customerName)}<small>${esc(row.twCode || '')}</small></td><td>${esc(row.batchName)}</td><td>${pnum(board === 'block' ? row.block_amount : row.priority_amount)}</td><td><button class="p-link" data-roster-restore="${row.id}">恢复跟进</button></td></tr>`).join('')}</tbody></table></div></section>`);
    panel.querySelectorAll('[data-edit-product-batch]').forEach(button => button.onclick = () => { const item = data.items.find(v => v.id === button.dataset.editProductBatch); if (item) priorityProductBatchEditor(item, data.activityOptions || [], refresh); });
    panel.querySelectorAll('[data-roster-add]').forEach(button => button.onclick = () => { const batch = data.items.find(v => v.id === button.closest('[data-roster-board]')?.closest('article')?.querySelector('[data-edit-product-batch]')?.dataset.editProductBatch) || data.items[0]; if (batch) priorityRosterEditor(batch, null, board, refresh); });
    panel.querySelectorAll('[data-roster-follow]').forEach(button => button.onclick = () => { const row = data.items.flatMap(v => board === 'block' ? (v.blockRows || []) : (v.rosterRows || [])).find(v => v.id === button.dataset.rosterFollow); if (row) openWorkspaceNote(row.subjectKey, 'priority', row.batchKey, board); });
    panel.querySelectorAll('[data-roster-remove]').forEach(button => button.onclick = async () => { const row = data.items.flatMap(v => board === 'block' ? (v.blockRows || []) : (v.rosterRows || [])).find(v => v.id === button.dataset.rosterRemove); if (!row) return; await pcall(`product-batches/${encodeURIComponent(row.batchKey)}/roster/${encodeURIComponent(row.id)}/resolve`, {subjectKey: row.subjectKey, board, status: 'none', priorityAmount: row.priority_amount, blockAmount: row.block_amount}, 'POST'); toast('客户已移出本批'); await refresh(); });
    panel.querySelectorAll('[data-roster-save]').forEach(button => button.onclick = async () => { const row = (data.items.flatMap(v => board === 'block' ? (v.blockRows || []) : (v.rosterRows || []))).find(v => v.id === button.dataset.rosterSave); const wrap = button.closest('tr'); const body = {subjectKey: row.subjectKey, board, status: row.status, priorityAmount: row.priority_amount, blockAmount: row.block_amount, priorityActualAmount: board === 'priority' ? wrap.querySelector('[data-roster-field="actual"]').value : undefined, blockActualAmount: board === 'block' ? wrap.querySelector('[data-roster-field="actual"]').value : undefined, blockBuyPrice: board === 'block' ? wrap.querySelector('[data-roster-field="price"]').value : undefined}; await pcall(`product-batches/${encodeURIComponent(row.batchKey)}/roster/${encodeURIComponent(row.id)}`, body, 'PATCH'); toast('已保存'); await refresh(); });
    for (const action of ['complete','abandon','restore']) panel.querySelectorAll(`[data-roster-${action}]`).forEach(button => button.onclick = async () => {
      const row = data.items.flatMap(v => board === 'block' ? (v.blockRows || []) : (v.rosterRows || [])).find(v => v.id === button.dataset[`roster${action[0].toUpperCase()}${action.slice(1)}`]);
      if (!row) return;
      const tr = button.closest('tr');
      const actual = tr?.querySelector('[data-roster-field="actual"]')?.value || row.block_actual_amount || row.priority_actual_amount;
      const price = tr?.querySelector('[data-roster-field="price"]')?.value || row.block_buy_price;
      await pcall(`product-batches/${encodeURIComponent(row.batchKey)}/roster/${encodeURIComponent(row.id)}/resolve`, {subjectKey: row.subjectKey, board, status: action === 'complete' ? 'completed' : action === 'abandon' ? 'abandoned' : 'followup', priorityAmount: row.priority_amount, blockAmount: row.block_amount, priorityActualAmount: board === 'priority' ? actual : undefined, blockActualAmount: board === 'block' ? actual : undefined, blockBuyPrice: board === 'block' ? price : undefined}, 'POST');
      toast(action === 'complete' ? '客户已完成' : action === 'abandon' ? '客户已移入放弃汇总' : '客户已恢复'); await refresh();
    });
  } catch (error) { panel.innerHTML = `<div class="result-box error-text">${esc(error.message || '批次客户读取失败')}</div>`; }
}

async function renderPriorityBlockBatches(panel) { return renderPriorityRosterBatches(panel, 'block'); }

function priorityProductBatchDate(value) {
  return String(value || '').slice(0, 10);
}

function priorityProductBatchEditor(item, activityOptions, onSaved) {
  const editing = Boolean(item?.id);
  const selectedDates = new Set(item?.activityDates || []);
  const statusOptions = (priorityState.data?.productBatchStatuses || ['筹备中', '进行中', '已完成', '已取消']);
  openModal(`<div class="modal quick-modal priority-product-batch-modal"><div class="modal-header"><div><h3>${editing ? '编辑参与批次' : '添加参与批次'}</h3><span class="hint">参与人只能从完整的 ICC 活动批次导入；每个活动只能归入一个参与批次，不能单独添加或移除客户。</span></div><button class="close-btn" data-close>×</button></div><form id="priority-product-batch-form"><div class="modal-body"><div class="form-grid"><div class="field"><label>参与批次名称 *</label><input name="name" required maxlength="160" value="${esc(item?.name || '')}" placeholder="例如：9 月优先劣后第一批"></div><div class="field"><label>实际参与日期 *</label><input name="participationDate" type="date" required value="${esc(item?.participationDate || '')}"></div><div class="field"><label>批次状态</label><select name="status">${statusOptions.map(value => `<option value="${esc(value)}" ${value === (item?.status || '筹备中') ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select></div></div><div class="field"><label>选择完整 ICC 活动批次 *</label><div class="priority-product-batch-activities">${activityOptions.length ? activityOptions.map(activity => { const disabled = activity.batchId && activity.batchId !== item?.id; return `<label class="import-consent"><input type="checkbox" name="activityDate" value="${esc(activity.date)}" ${selectedDates.has(activity.date) ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span><strong>${esc(activity.date)}</strong><small>${activity.records || 0} 条活动记录${disabled ? ' · 已归入其他参与批次' : ''}</small></span></label>`; }).join('') : '<p class="hint">还没有可归组的 ICC 活动，请先上传活动资料。</p>'}</div><p class="hint">勾选后会整批带入该活动的全部客户；保存后名单由活动批次自动生成，页面不提供单个客户的添加或移除。</p></div><div class="field"><label>批次备注</label><textarea name="notes" maxlength="4000" placeholder="记录这几周活动为何归入同一批">${esc(item?.notes || '')}</textarea></div><p id="priority-product-batch-error" class="p-error" role="alert"></p></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">${editing ? '保存批次修改' : '创建参与批次'}</button></div></form></div>`);
  const form = document.querySelector('#priority-product-batch-form');
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.submitter || form.querySelector('.primary-btn');
    const values = Object.fromEntries(new FormData(form));
    values.activityDates = [...form.querySelectorAll('[name="activityDate"]:checked')].map(input => input.value);
    if (!values.activityDates.length) {
      form.querySelector('#priority-product-batch-error').textContent = '请至少选择一个完整的 ICC 活动批次，不能单独添加或移除客户。';
      setBusyButton(button, false);
      return;
    }
    setBusyButton(button, true, editing ? '正在保存…' : '正在创建…');
    try {
      await pcall(`product-batches${editing ? `/${encodeURIComponent(item.id)}` : ''}`, values, editing ? 'PATCH' : 'POST');
      closeModal();
      toast(editing ? '参与批次已更新' : '参与批次已创建');
      if (onSaved) await onSaved();
    } catch (error) {
      form.querySelector('#priority-product-batch-error').textContent = error.message || '保存失败，请重试。';
      setBusyButton(button, false);
    }
  });
}

async function renderPriorityProductBatches(panel) {
  panel.innerHTML = '<div class="p-empty"><p role="status">正在读取参与批次…</p></div>';
  try {
    const data = await pget('product-batches');
    priorityState.productBatches = data.items || [];
    priorityState.productBatchActivities = data.activityOptions || [];
    const canEdit = priorityProductBatchCanEdit();
    const refresh = async () => { await renderPriorityProductBatches(panel); };
      panel.innerHTML = `<div class="p-panel-heading"><div><h4>参与批次</h4><p>优先劣后与大宗交易分别统计意向金额；同一客户可同时进入两个板块，金额彼此独立。</p></div>${canEdit ? '<button class="primary-btn" id="priority-product-batch-add">＋ 添加参与批次</button>' : ''}</div><div class="notice">未匹配身份的记录按“活动日期 + 原表行”单独保留，不会因姓名相同而合并。活动归组不会修改原始上传历史；金额为本批意向金额，不是实际参与或到账。</div><div class="priority-product-batch-list">${data.items?.length ? data.items.map(item => `<article class="priority-product-batch-card"><div class="section-header"><div><span class="eyebrow">${esc(item.participationDate || item.participation_date || '')}</span><h4>${esc(item.name)}</h4><p>${esc(item.activityDates?.join('、') || '尚未归入 ICC 活动')} · ${esc(item.status)}</p></div>${canEdit ? `<button class="secondary-btn" data-edit-product-batch="${esc(item.id)}">编辑</button>` : ''}</div><div class="p-metrics"><article><span>优先劣后客户</span><strong>${Number(item.participantCount || 0).toLocaleString('zh-CN')}<small> 人</small></strong><p>${Number(item.recordCount || 0).toLocaleString('zh-CN')} 条参与记录</p></article><article><span>优先劣后意向金额 · USD</span><strong>${pnum(item.agreementUsd || 0)}</strong><p>与大宗交易金额分开累计</p></article><article><span>大宗交易客户</span><strong>${Number(item.blockParticipantCount || 0).toLocaleString('zh-CN')}<small> 人</small></strong><p>${Number(item.blockRecordCount || 0).toLocaleString('zh-CN')} 条参与记录</p></article><article><span>大宗交易本批意向金额 · USD</span><strong>${pnum(item.blockAgreementUsd || 0)}</strong><p>${Number(item.activityCount || 0).toLocaleString('zh-CN')} 个活动周 · ${esc(item.notes || '未填写批次说明')}</p></article></div>${item.rows?.length ? `<div class="priority-product-batch-roster"><div class="section-header"><div><h5>本批客户</h5><span class="hint">名单随所选 ICC 活动整批生成；两板块按各自金额独立展示。</span></div><span class="hint">${item.rows.length} 条</span></div><div class="p-table-wrap"><table class="p-table"><thead><tr><th>客户</th><th>TW 编号</th><th>负责人</th><th>优先劣后意向 USD</th><th>大宗交易意向 USD</th><th>当前到账 USD</th><th>跟进</th></tr></thead><tbody>${item.rows.map(row => { const customer = row.canOpenCard ? `<button class="p-link" data-priority-batch-card="${esc(row.subjectKey)}">${esc(row.name || row.customerName || '未命名客户')}</button>` : `<span title="${esc(row.followupDisabledReason || '当前账号只能查看批次名单')}">${esc(row.name || row.customerName || '未命名客户')}</span>`; const followup = row.canFollowup ? `<button class="p-link" data-priority-batch-followup="${esc(row.subjectKey)}" data-priority-batch-key="${esc(row.batchKey || item.id)}">写跟进</button>` : `<button class="p-link" disabled title="${esc(row.followupDisabledReason || '仅客户当前负责人可写跟进')}">不可写</button>`; return `<tr><td>${customer}</td><td>${esc(row.twCode || '待匹配')}</td><td>${esc(row.ownerName || '待分配')}</td><td>${Number(row.agreementAmountUsd)>0?pnum(row.agreementAmountUsd):'—'}</td><td>${Number(row.blockAgreementAmountUsd)>0?pnum(row.blockAgreementAmountUsd):'—'}</td><td>${pnum(row.funded_amount)}<small class="hint">${esc(workspaceFundingSource(row))}</small></td><td>${followup}</td></tr>`; }).join('')}</tbody></table></div></div>` : '<div class="p-empty priority-product-batch-empty"><p>本批暂没有活动客户记录。</p></div>'}</article>`).join('') : '<div class="p-empty"><h4>还没有真实参与批次</h4><p>上传 ICC 活动并确认参与板块后，在这里选择活动周归入真实参与批次。</p></div>'}</div>`;
    panel.querySelector('#priority-product-batch-add')?.addEventListener('click', () => priorityProductBatchEditor(null, data.activityOptions || [], refresh));
    panel.querySelectorAll('[data-edit-product-batch]').forEach(button => button.addEventListener('click', () => {
      const item = data.items.find(candidate => candidate.id === button.dataset.editProductBatch);
      if (item) priorityProductBatchEditor(item, data.activityOptions || [], refresh);
    }));
    panel.querySelectorAll('[data-priority-batch-followup]').forEach(button => button.addEventListener('click', () => openWorkspaceNote(button.dataset.priorityBatchFollowup, 'priority', button.dataset.priorityBatchKey)));
    panel.querySelectorAll('[data-priority-batch-card]').forEach(button => button.addEventListener('click', () => openCustomerCard(button.dataset.priorityBatchCard)));
  } catch (error) {
    panel.innerHTML = `<div class="result-box error-text">${esc(error.message || '参与批次读取失败')}</div>`;
  }
}

// The operational tab uses the status based roster; the source activity table
// remains reachable from the weekly activity view for audit purposes.
async function renderPriorityProductBatches(panel) { return renderPriorityRosterBatches(panel, 'priority'); }
