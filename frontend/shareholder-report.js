function shareholderNumber(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—';
  return Number(value).toLocaleString('zh-CN', {maximumFractionDigits: digits});
}

function shareholderMoney(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—';
  return `$${Number(value).toLocaleString('en-US', {maximumFractionDigits: digits})}`;
}

function shareholderPercent(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  return `${Number(value).toFixed(digits)}%`;
}

function shareholderDateLabel(value) {
  return value ? value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$1.$2.$3') : '暂无日期';
}

function shareholderDonut(items, centerValue, centerLabel, palette) {
  const total = items.reduce((sum, item) => sum + Math.max(0, Number(item.count) || 0), 0);
  const radius = 49;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const rings = items.map((item, index) => {
    const share = total ? Math.max(0, Number(item.count) || 0) / total : 0;
    const length = circumference * share;
    const markup = `<circle class="report-donut-segment" cx="70" cy="70" r="${radius}" stroke="${palette[index % palette.length]}" stroke-dasharray="${length} ${circumference - length}" stroke-dashoffset="${-offset}"/>`;
    offset += length;
    return markup;
  }).join('');
  return `<div class="report-donut-wrap"><svg class="report-donut" viewBox="0 0 140 140" role="img" aria-label="资产分层占比"><circle class="report-donut-track" cx="70" cy="70" r="${radius}"/><g>${rings}</g></svg><div class="report-donut-center"><strong>${esc(centerValue)}</strong><span>${esc(centerLabel)}</span></div></div>`;
}

function shareholderSparkline(trend,key,stroke,formatter) {
  const values=trend.map(item=>item[key]==null?null:Number(item[key]));
  const available=values.map((v,i)=>v==null?null:[i,v]).filter(Boolean);
  if(!available.length)return '<div class="report-chart-empty">暂无可绘制的来源数据</div>';
  const width=560,height=180,left=55,right=25,top=16,bottom=30;
  const times=trend.map(r=>Date.parse(r.date)),start=Math.min(...times),span=Math.max(1,Math.max(...times)-start);
  const min=Math.min(0,...available.map(r=>r[1])),max=Math.max(1,...available.map(r=>r[1])),range=Math.max(1,max-min);
  const x=i=>left+(width-left-right)*(times[i]-start)/span,y=v=>top+(height-top-bottom)*(1-(v-min)/range);
  const short=v=>Math.abs(v)>=1e6?`${(v/1e6).toFixed(1)}m`:Math.abs(v)>=1e4?`${(v/1e4).toFixed(1)}万`:shareholderNumber(v);
  const grid=[0,.5,1].map(f=>{const value=min+range*f;return `<line x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text x="${left-8}" y="${y(value)+3}" text-anchor="end" fill="#8995a1" stroke="none" font-size="9">${esc(short(value))}</text>`;}).join('');
  const segments=[];let current=[];
  values.forEach((v,i)=>{if(v==null){if(current.length)segments.push(current);current=[];}else current.push([i,v]);});if(current.length)segments.push(current);
  const lines=segments.map(points=>`<polyline class="report-spark-line" points="${points.map(([i,v])=>`${x(i)},${y(v)}`).join(' ')}" stroke="${stroke}"/>`).join('');
  const dots=available.map(([i,v])=>`<circle cx="${x(i)}" cy="${y(v)}" r="3.4"><title>${esc(trend[i].date)}：${esc(formatter(v))}</title></circle>`).join('');
  const labels=[available[0][0],available[Math.floor((available.length-1)/2)][0],available.at(-1)[0]].filter((v,i,arr)=>arr.indexOf(v)===i).map(i=>`<text x="${x(i)}" y="${height-9}" text-anchor="middle" fill="#8995a1" font-size="9">${esc(trend[i].date.slice(5))}</text>`).join('');
  return `<div class="report-chart-value"><strong>${esc(formatter(available.at(-1)[1]))}</strong><span>${esc(trend[available.at(-1)[0]].date)} · ${available.length} 个时点</span></div><div class="report-sparkline"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(key)}历史趋势"><g class="report-chart-grid">${grid}</g>${lines}<g class="report-spark-dots" fill="${stroke}">${dots}</g>${labels}</svg></div>`;
}

function shareholderFunnel(items, maxValue) {
  const max = Math.max(maxValue || 0, ...items.map(item => Number(item.count) || 0), 1);
  return `<div class="report-funnel">${items.map((item, index) => {
    const width = Math.max(8, Math.round((Number(item.count) || 0) / max * 100));
    return `<div class="report-funnel-row"><div class="report-funnel-label"><span>${esc(item.label)}</span><strong>${shareholderNumber(item.count)} <small>人</small></strong></div><div class="report-funnel-track"><i style="width:${width}%;--funnel-index:${index}"></i></div><span class="report-funnel-note">${esc(item.note || '')}</span></div>`;
  }).join('')}</div>`;
}

function shareholderAmountBridge(onsite) {
  const items = [
    ['签约金额', onsite.signed?.amount, 'report-bridge-positive'],
    ['已转化', onsite.converted?.amount, 'report-bridge-positive'],
    ['已放弃 / 转二级', onsite.dropped?.amount, 'report-bridge-negative'],
    ['待转化', onsite.pending?.amount, 'report-bridge-pending'],
  ];
  const max = Math.max(...items.map(item => Number(item[1]) || 0), 1);
  return `<div class="report-bridge">${items.map(([label, amount, tone]) => `<div class="report-bridge-item"><div class="report-bridge-bar"><i class="${tone}" style="height:${Math.max(8, Math.round((Number(amount) || 0) / max * 124))}px"></i></div><strong>${shareholderMoney(amount)}</strong><span>${label}</span></div>`).join('')}</div>`;
}


function shareholderReportMarkup(data, {standalone = false} = {}) {
  const k=data.kpis||{}, a=data.assets||{}, h=data.holdings||{}, historical=data.historical||{}, products=data.products||{};
  const n=shareholderNumber, money=shareholderMoney, pct=shareholderPercent;
  const colors=['#1c3c60','#315f8c','#4d7fab','#8faec9','#cbdbe7','#e9e5df'];
  const ratio=(num,den)=>den&&num!=null?pct(num/den*100):'—';
  const sources=Object.entries(data.sourceDates||{}).filter(([,v])=>v).map(([key,v])=>`${{master:'客户名单',assets:'资产明细',secondary:'分类持仓',icc:'ICC 活动'}[key]} ${shareholderDateLabel(v)}`).join(' · ');
  const options=[...new Set(data.reportDates||[])].sort().reverse().map(d=>`<option value="${esc(d)}" ${d===data.asOf?'selected':''}>${esc(shareholderDateLabel(d))}</option>`).join('');
  const section=(num,en,title,description,body,extra='')=>`<section class="brief-section ${extra}"><header class="brief-section-head"><div><span class="brief-eyebrow">${num} / ${en}</span><h4>${title}</h4><p>${description}</p></div></header>${body}</section>`;
  const productLine=(board,label)=>{const p=products[board]||{}, signed=p.signed||{}, converted=p.converted||{}, pending=p.pending||{}, dropped=p.dropped||{};return `<article class="brief-product"><div><span class="brief-tag">在售</span><h5>${label}</h5><p>意向 ${n(signed.count)} 人 / ${money(signed.amount)}</p></div><div class="brief-product-value"><strong>${money(converted.amount)}</strong><span>${n(converted.count)} 人已完成 · 金额 ${ratio(converted.amount,signed.amount)}</span></div><div class="brief-stacked"><i style="width:${signed.amount?Math.min(100,(p.convertedIntentAmount||0)/signed.amount*100):0}%"></i></div><p>待转化 ${n(pending.count)} 人 / ${money(pending.amount)}${dropped.count?` · 已放弃 ${n(dropped.count)} 人 / ${money(dropped.amount)}`:''}</p>${p.historicalWorkflowUnavailable?'<p class="brief-review">该时点成交状态待核对</p>':''}</article>`;};
  const rows=(a.buckets||[]).map(b=>`<tr><th scope="row">${esc(b.label)}</th><td>${n(b.count)}</td><td>${pct(b.share)}</td><td>${money(b.amount,2)}</td><td>${pct(b.assetShare)}<span class="brief-mini-bar"><i style="width:${Math.max(0,Math.min(100,b.assetShare||0))}%"></i></span></td></tr>`).join('');
  const donut=(a.buckets||[]).filter(b=>b.amount>0).map(b=>({...b,count:b.amount}));
  const topTwo=(a.buckets||[]).slice(0,2), topTwoAmount=a.totalUsd==null?null:topTwo.reduce((s,b)=>s+Number(b.amount||0),0), topTwoCount=a.accounts==null?null:topTwo.reduce((s,b)=>s+Number(b.count||0),0);
  const assetBody=`<div class="brief-asset-layout"><div>${shareholderDonut(donut,n(a.accounts),'账户数 · 按资产金额分布',colors)}<div class="brief-legend">${donut.map((b,i)=>`<div><i style="background:${colors[i]}"></i><span>${esc(b.label)}</span><b>${pct(b.assetShare)}</b></div>`).join('')}</div></div><div class="brief-table-wrap"><table class="brief-table"><thead><tr><th>资产分层</th><th>账户数</th><th>账户占比</th><th>资产合计 · USD</th><th>资产占比</th></tr></thead><tbody>${rows}<tr class="brief-total"><th>合计</th><td>${n(a.accounts)}</td><td>${a.accounts?'100.00%':'—'}</td><td>${money(a.totalUsd,2)}</td><td>${a.totalUsd?'100.00%':'—'}</td></tr></tbody></table></div></div><div class="brief-readouts"><div><span>PARETO</span><p>前两档合计 ${n(topTwoCount)} 个账户，占 ${ratio(topTwoCount,a.accounts)}，持有 ${money(topTwoAmount)}、全表 ${ratio(topTwoAmount,a.totalUsd)} 的资产。</p></div><div><span>CONCENTRATION</span><p>前十大客户按 TW 合并账户计算，持有 ${money(a.top10Usd)}，占净资产 ${pct(a.top10Share)}。</p></div><div><span>DEFINITIONS</span><p>资产净额保留负数；开户人数按 TW 去重。账户分布与人数分别呈现。</p></div></div>`;
  const funnelRows=[['注册开户',k.customers,'客户名单 / 注册口径',k.customers],['开户成功',k.opened,'资产表中有数值的 TW，合并重复账户',k.customers],['资产入金',a.fundedAccounts,'正资产账户数；与客户人数口径分开',a.accounts]];
  const funnelBody=`<div class="brief-funnel">${funnelRows.map(([label,count,note,den])=>`<div><div><b>${label}</b><small>${note}</small></div><span class="brief-funnel-track"><i style="width:${den?Math.min(100,(count||0)/den*100):0}%"></i></span><div><strong>${n(count)} <small>${label==='资产入金'?'个账户':'人'}</small></strong><small>${ratio(count,den)} · ${label==='资产入金'?'占资产账户':'占注册客户'}</small></div></div>`).join('')}</div><div class="brief-product-grid">${productLine('priority','购买计划')}${productLine('block','大宗交易')}</div>`;
  const bridgeBody=`<p class="brief-section-note">历史产品 · 已停售，单独留档，不并入当前客户资产</p><div class="brief-capital-grid">${[['签约总额','signed'],['已参与','converted'],['放弃','dropped'],['待转化','pending']].map(([label,key])=>`<div><span>${label}</span><strong>${money(historical[key]?.amount)}</strong><small>${n(historical[key]?.count)} 人${key==='converted'?` · 金额参与率 ${ratio(historical[key]?.amount,historical.signed?.amount)}`:''}</small></div>`).join('')}</div>`;
  const holdingBody=`<div class="brief-holdings-grid">${[['二级买入','secondary'],['大宗已成交','block'],['购买计划已成交','priority']].map(([label,key],i)=>`<div style="--holding-color:${colors[i]}"><span>${label}</span><strong>${n(h[key+'Quantity'])} <small>股</small></strong><p>${n(h[key+'Holders'])} 位客户</p></div>`).join('')}<div class="brief-holding-total"><span>总持仓 · TW 去重</span><strong>${n(h.quantity)} <small>股</small></strong><p>${n(h.holders)} 位客户 · 占开户 ${ratio(h.holders,k.opened)}</p></div></div><div class="brief-valuation"><strong>${money(h.marketValue)}</strong><span>${h.price==null?'来源未提供估值价格，市值留空':`按 $${n(h.price,4)} / 股估算；非实时报价`}</span></div>`;
  const trendBody=`<p class="brief-section-note">仅展示已上传的历史快照；日期按真实时间间隔绘制，不补造增长数据。鼠标悬停查看每个时点。</p><div class="brief-trend-grid"><article><h5>开户成功人数</h5>${shareholderSparkline(data.trend||[],'opened',colors[0],v=>`${n(v)} 人`)}</article><article><h5>客户资产净额 · USD</h5>${shareholderSparkline(data.trend||[],'assetAum',colors[1],money)}</article><article><h5>二级持仓 · 股</h5>${shareholderSparkline(data.trend||[],'secondaryQuantity',colors[2],n)}</article><article><h5>购买计划持仓 · 股</h5>${shareholderSparkline(data.trend||[],'priorityQuantity','#9b783d',n)}</article></div>`;
  const reviews=(data.checks||[]).filter(c=>c.status!=='pass');
  const controlBody=`<div class="brief-control-summary ${reviews.length?'brief-review':''}"><strong>${reviews.length?`${reviews.length} 项待核对`:'已通过现有勾稽检查'}</strong><span>${data.adjustments?.length?`${data.adjustments.length} 项人工修订已应用`:'当前使用系统计算值'}</span></div><div class="brief-check-list">${(data.checks||[]).map(c=>`<div class="${c.status==='pass'?'':'brief-review'}"><span>${c.status==='pass'?'✓':'!'}</span><p>${esc(c.label)}</p>${c.difference==null?'':`<b>${c.status==='pass'?'一致':`差额 ${n(c.difference,2)}`}</b>`}</div>`).join('')}</div>${data.adjustments?.length?`<table class="brief-table brief-corrections"><thead><tr><th>人工修订指标</th><th>采用值</th><th>依据 / 原因</th><th>修订人</th></tr></thead><tbody>${data.adjustments.map(c=>`<tr><th>${esc(data.correctionOptions?.find(o=>o.key===c.metric)?.label||c.metric)}</th><td>${n(c.value,4)}</td><td>${esc(c.source)}<small>${esc(c.reason)}</small></td><td>${esc(c.author_name)}<small>${esc(c.created_at?.slice(0,10)||'')}</small></td></tr>`).join('')}</tbody></table>`:''}`;
  return `<div class="shareholder-report brief-report" id="shareholder-report-page"><header class="shareholder-hero"><div class="brief-eyebrow">JY INVESTOR BRIEFING / ${esc(data.asOf?.slice(0,4)||'')}</div><h3>港安客户资产与业务经营报告</h3><p>资产结构、客户转化与在售产品的经营全景</p><div class="shareholder-hero-meta"><span class="brief-confidential">JY CONFIDENTIAL</span><span>报告时点 <b>${esc(shareholderDateLabel(data.asOf))}</b></span><span>数据范围 <b>${esc(data.scope)}</b></span><span>来源 ${esc(sources||'暂无已上传来源')}</span></div>${standalone?'':`<div class="shareholder-controls"><label>报告时点<select id="shareholder-as-of">${options||`<option>${esc(data.asOf)}</option>`}</select></label><button class="report-action report-action-light" id="download-shareholder-report">↓ 下载完整报告</button><button class="report-action report-action-outline" id="print-shareholder-report">打印 / 保存 PDF</button>${data.canEdit?'<button class="report-action report-action-outline" id="correct-shareholder-report">修订报告数据</button>':''}</div>`}</header><section class="report-kpi-grid">${[
    ['客户资产净额',money(k.assetAum),`${n(a.accounts)} 个账户 · 正资产合计 ${money(a.positiveUsd)}`],
    ['注册 → 开户成功',pct(k.accountSuccessRate),`${n(k.opened)} / ${n(k.customers)} 人`],
    ['历史定增已参与',money(historical.converted?.amount),`${n(historical.converted?.count)} 人 · 已停售`],
    ['XMAX 总持仓',n(h.quantity),`${n(h.holders)} 人 · 市值 ${money(h.marketValue)}`],
    ['购买计划意向',money(products.priority?.signed?.amount),`${n(products.priority?.signed?.count)} 人 · 已完成 ${n(products.priority?.converted?.count)} 人`]
  ].map(([label,value,note])=>`<article class="report-kpi"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join('')}</section>${section('01','ASSET COMPOSITION','客户资产分层','账户数量与资产贡献并列展示，保留零资产及负资产。',assetBody)}${section('02','CONVERSION FUNNEL','从客户进入到实际参与','客户漏斗与产品线分别使用自己的分母；意向与实际成交分别呈现。',funnelBody)}${section('03','CAPITAL BRIDGE','历史定增金额桥','签约总额与已参与、放弃、待转化进行勾稽。',bridgeBody)}${section('04','XMAX POSITION','持仓结构与产品信号','二级、大宗、购买计划三条来源；同一 TW 可同时持有多个类别。',holdingBody)}${section('05','HISTORICAL TREND','历史数据与经营轨迹','按各期实际上传来源追踪规模变化。',trendBody)}${section('06','DATA ASSURANCE','数据核对与报告修订','保留系统原始计算；人工修订按日期、依据与操作人留痕。',controlBody,'brief-assurance')}<footer class="report-footnote"><span>统计口径</span><p>${(data.notes||[]).map(esc).join(' · ')}</p><small>JY Customer Operations · ${esc(data.asOf)}</small></footer></div>`;
}

let shareholderRequest=0;
async function renderShareholderReport(content) {
  const request=++shareholderRequest;
  const query=state.shareholderAsOf?`?as_of=${encodeURIComponent(state.shareholderAsOf)}`:'';
  const data=await api(`/api/workspace/dashboard/report${query}`);
  if(request!==shareholderRequest||state.view!=='shareholder-report')return;
  state.shareholderAsOf=data.asOf;
  content.innerHTML=shareholderReportMarkup(data);
  content.querySelector('#shareholder-as-of')?.addEventListener('change',event=>{state.shareholderAsOf=event.target.value;renderShareholderReport(content).catch(error=>toast(error.message));});
  content.querySelector('#print-shareholder-report')?.addEventListener('click',()=>window.print());
  content.querySelector('#download-shareholder-report')?.addEventListener('click',()=>downloadShareholderReport(data));
  content.querySelector('#correct-shareholder-report')?.addEventListener('click',()=>openShareholderCorrection(data,content));
}

function openShareholderCorrection(data,content) {
  openModal(`<div class="modal"><div class="modal-header"><div><h3>修订报告数据 · ${esc(data.asOf)}</h3><span class="hint">只作用于本期报告，不修改客户原档或导入数据。资产、持仓明细错误请重新上传。</span></div><button class="close-btn" data-close>×</button></div><form id="shareholder-correction-form"><div class="modal-body"><div class="field"><label>修订指标<select name="metric">${data.correctionOptions.map(o=>`<option value="${esc(o.key)}">${esc(o.label)}</option>`).join('')}</select></label></div><p id="correction-values" class="notice"></p><div class="field"><label>采用值<input type="number" name="value" min="0" step="any" required></label></div><div class="field"><label><input type="checkbox" name="restore"> 恢复为系统计算值</label></div><div class="field"><label>数据依据<input name="source" maxlength="1000" placeholder="例如：财务核对表、报告文件及章节" required></label></div><div class="field"><label>修订原因<textarea name="reason" maxlength="2000" required></textarea></label></div><p id="correction-error" class="p-error" role="alert"></p><details><summary>本期修订历史（${data.correctionHistory.length} 条）</summary>${data.correctionHistory.slice().reverse().map(r=>`<p>${esc(data.correctionOptions.find(o=>o.key===r.metric)?.label||r.metric)}：${r.value==null?'恢复系统值':shareholderNumber(r.value,4)} · ${esc(r.author_name)} · ${esc(r.created_at)}<br>${esc(r.source)} · ${esc(r.reason)}</p>`).join('')}</details></div><div class="modal-footer"><button type="button" class="secondary-btn" data-close>取消</button><button class="primary-btn">保存修订</button></div></form></div>`);
  const form=document.querySelector('#shareholder-correction-form');
  const update=()=>{const option=data.correctionOptions.find(o=>o.key===form.elements.metric.value);form.querySelector('#correction-values').textContent=`系统计算：${shareholderNumber(option.automatic,4)} · 当前报告：${shareholderNumber(option.effective,4)}`;form.elements.value.value=option.effective??'';form.elements.value.step=option.key.endsWith('Count')||['registered','opened'].includes(option.key)?'1':'any';};
  form.elements.metric.onchange=update;update();
  form.elements.restore.onchange=()=>{form.elements.value.disabled=form.elements.restore.checked;form.elements.value.required=!form.elements.restore.checked;};
  form.onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await api('/api/workspace/dashboard/report/adjustments',{method:'POST',body:JSON.stringify({reportDate:data.asOf,metric:form.elements.metric.value,value:form.elements.restore.checked?null:form.elements.value.value,source:form.elements.source.value,reason:form.elements.reason.value,expectedId:data.adjustments.find(c=>c.metric===form.elements.metric.value)?.id||''})});closeModal();await renderShareholderReport(content);toast('报告修订已保存');}catch(error){form.querySelector('#correction-error').textContent=error.message;button.disabled=false;}};
}

async function downloadShareholderReport(data) {
  const query=`?as_of=${encodeURIComponent(data.asOf)}`;
  try {
    const response=await fetch('/api/workspace/dashboard/report.pdf'+query,{headers:state.token?{Authorization:`Bearer ${state.token}`}:{}});
    if(!response.ok){const detail=await response.json().catch(()=>({}));throw new Error(detail.detail||'PDF 报告下载失败');}
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=`港安客户资产与业务经营报告_${data.asOf}.pdf`;link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);toast('PDF 股东报告已下载');
  }catch(error){toast(error.message);}
}
