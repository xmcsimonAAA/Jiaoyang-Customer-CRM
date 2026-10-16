/* Real participation-batch grouping for weekly priority-inferior activities. */

function priorityProductBatchCanEdit() {
  return Boolean(state.user?.customerScope === 'all' && state.user?.canImportCustomers && state.user?.canManageAdvisorBindings);
}

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
      panel.innerHTML = `<div class="p-panel-heading"><div><h4>参与批次</h4><p>每周活动先单独保留，实际参与时把多周活动整批归入同一批次；同一 TW 在本批次只按最新活动金额统计。名单由活动批次自动生成，不支持单独添加或移除客户。</p></div>${canEdit ? '<button class="primary-btn" id="priority-product-batch-add">＋ 添加参与批次</button>' : ''}</div><div class="notice">未匹配身份的记录按“活动日期 + 原表行”单独保留，不会因姓名相同而合并。活动归组不会修改原始上传历史。</div><div class="priority-product-batch-list">${data.items?.length ? data.items.map(item => `<article class="priority-product-batch-card"><div class="section-header"><div><span class="eyebrow">${esc(item.participationDate || item.participation_date || '')}</span><h4>${esc(item.name)}</h4><p>${esc(item.activityDates?.join('、') || '尚未归入 ICC 活动')} · ${esc(item.status)}</p></div>${canEdit ? `<button class="secondary-btn" data-edit-product-batch="${esc(item.id)}">编辑</button>` : ''}</div><div class="p-metrics"><article><span>本批参与客户</span><strong>${Number(item.participantCount || 0).toLocaleString('zh-CN')}<small> 人</small></strong><p>${Number(item.pendingCount || 0).toLocaleString('zh-CN')} 条身份待确认</p></article><article><span>协议金额 · USD</span><strong>${pnum(item.agreementUsd || 0)}</strong><p>${Number(item.recordCount || 0).toLocaleString('zh-CN')} 条最新参与记录</p></article><article><span>活动周数</span><strong>${Number(item.activityCount || 0).toLocaleString('zh-CN')}<small> 周</small></strong><p>${esc(item.notes || '未填写批次说明')}</p></article></div>${item.rows?.length ? `<div class="priority-product-batch-roster"><div class="section-header"><div><h5>本批客户</h5><span class="hint">名单随所选 ICC 活动整批生成；当前到账来自最新资产表，可在客户卡片中手动修正。</span></div><span class="hint">${item.rows.length} 条</span></div><div class="p-table-wrap"><table class="p-table"><thead><tr><th>客户</th><th>TW 编号</th><th>负责人</th><th>协议金额 USD</th><th>当前到账 USD</th><th>跟进</th></tr></thead><tbody>${item.rows.map(row => { const customer = row.canOpenCard ? `<button class="p-link" data-priority-batch-card="${esc(row.subjectKey)}">${esc(row.name || row.customerName || '未命名客户')}</button>` : `<span title="${esc(row.followupDisabledReason || '当前账号只能查看批次名单')}">${esc(row.name || row.customerName || '未命名客户')}</span>`; const followup = row.canFollowup ? `<button class="p-link" data-priority-batch-followup="${esc(row.subjectKey)}" data-priority-batch-key="${esc(row.batchKey || item.id)}">写跟进</button>` : `<button class="p-link" disabled title="${esc(row.followupDisabledReason || '仅客户当前负责人可写跟进')}">不可写</button>`; return `<tr><td>${customer}</td><td>${esc(row.twCode || '待匹配')}</td><td>${esc(row.ownerName || '待分配')}</td><td>${pnum(row.agreementAmountUsd || 0)}</td><td>${pnum(row.funded_amount)}<small class="hint">${esc(workspaceFundingSource(row))}</small></td><td>${followup}</td></tr>`; }).join('')}</tbody></table></div></div>` : '<div class="p-empty priority-product-batch-empty"><p>本批暂没有活动客户记录。</p></div>'}</article>`).join('') : '<div class="p-empty"><h4>还没有真实参与批次</h4><p>上传每周 ICC 活动后，点击“添加参与批次”，选择几周活动归入同一批次。</p></div>'}</div>`;
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
