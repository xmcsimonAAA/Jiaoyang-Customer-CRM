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

function shareholderSparkline(trend, key, stroke, formatter) {
  const values = trend.map(item => item[key] === null || item[key] === undefined ? null : Number(item[key]));
  const available = values.map((value, index) => value === null ? null : [index, value]).filter(Boolean);
  if (!available.length) return `<div class="report-chart-empty">暂无可绘制的来源数据</div>`;
  const width = 720;
  const height = 188;
  const left = 18;
  const right = 18;
  const top = 18;
  const bottom = 30;
  const max = Math.max(...available.map(item => item[1]), 1);
  const min = Math.min(...available.map(item => item[1]), 0);
  const span = Math.max(max - min, 1);
  const x = index => left + (width - left - right) * index / Math.max(values.length - 1, 1);
  const y = value => top + (height - top - bottom) * (1 - (value - min) / span);
  const points = available.map(([index, value]) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(' ');
  const areaPoints = `${points} ${x(available[available.length - 1][0]).toFixed(1)},${height - bottom} ${x(available[0][0]).toFixed(1)},${height - bottom}`;
  const grid = [0, .25, .5, .75, 1].map(fraction => `<line x1="${left}" x2="${width - right}" y1="${top + (height - top - bottom) * fraction}" y2="${top + (height - top - bottom) * fraction}"/>`).join('');
  const dots = available.map(([index, value]) => `<circle cx="${x(index).toFixed(1)}" cy="${y(value).toFixed(1)}" r="3.6"/>`).join('');
  const labels = [0, Math.floor((values.length - 1) / 2), values.length - 1].filter((index, position, array) => values[index] !== null && array.indexOf(index) === position).map(index => `<span style="left:${(x(index) / width * 100).toFixed(2)}%">${esc(String(trend[index].date || '').slice(5))}</span>`).join('');
  const latest = available[available.length - 1][1];
  return `<div class="report-chart-value"><strong>${esc(formatter(latest))}</strong><span>最新来源快照</span></div><div class="report-sparkline"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="趋势图"><g class="report-chart-grid">${grid}</g><polygon class="report-spark-area" points="${areaPoints}" fill="${stroke}"/><polyline class="report-spark-line" points="${points}" stroke="${stroke}"/> <g class="report-spark-dots" fill="${stroke}">${dots}</g></svg><div class="report-chart-labels">${labels}</div></div>`;
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
  const k = data.kpis || {};
  const a = data.assets || {};
  const f = data.funnel || {};
  const h = data.holdings || {};
  const p = data.priority || {};
  const trend = data.trend || [];
  const bucketPalette = ['#d8ef72', '#91bd68', '#6aa994', '#5b8f94', '#516f7e', '#d86f68'];
  const bucketTotal = a.buckets?.reduce((sum, item) => sum + Number(item.count || 0), 0) || 0;
  const assetLegend = (a.buckets || []).map((item, index) => `<div class="report-legend-row"><i style="background:${bucketPalette[index % bucketPalette.length]}"></i><span>${esc(item.label)}</span><strong>${shareholderNumber(item.count)} <small>${shareholderPercent(item.share)}</small></strong></div>`).join('');
  const funnelItems = [
    {label:'注册开户', count:f.registration?.total, note:'客户名单 / 注册口径'},
    {label:'开户成功', count:f.registration?.opened, note:shareholderPercent(f.registration?.rate)},
    {label:'资产入金', count:a.fundedAccounts, note:shareholderPercent(a.activationRate)},
    {label:'现场签约', count:f.onsite?.signed?.count, note:'定增（含大宗）'},
    {label:'现场转化', count:f.onsite?.converted?.count, note:'已形成实际参与'},
  ];
  const sourceLines = Object.entries(data.sourceDates || {}).filter(([, value]) => value).map(([key, value]) => `<span>${esc({master:'开户名单',assets:'资产明细',secondary:'XMAX 持仓',icc:'优先劣后'}[key] || key)} · ${esc(shareholderDateLabel(value))}</span>`).join('');
  const asOfOptions = [...new Set(trend.map(item => item.date).filter(Boolean))].sort().reverse().map(value => `<option value="${esc(value)}" ${value === data.asOf ? 'selected' : ''}>${esc(shareholderDateLabel(value))}</option>`).join('');
  const top10Text = a.top10Share === null || a.top10Share === undefined ? '暂无' : shareholderPercent(a.top10Share, 1);
  return `<div class="shareholder-report" id="shareholder-report-page">
    <header class="shareholder-hero">
      <div class="shareholder-hero-top"><div><div class="shareholder-kicker">JY INVESTOR BRIEFING / 2026</div><h3>港安客户资产与业务经营报告</h3><p>将资产结构、开户效率与产品转化放在同一张经营地图中。</p></div><div class="shareholder-hero-mark"><span>JY</span><small>CONFIDENTIAL</small></div></div>
      ${standalone ? '' : `<div class="shareholder-controls"><label>报告时点<select id="shareholder-as-of">${asOfOptions || `<option value="${esc(data.asOf)}">${esc(shareholderDateLabel(data.asOf))}</option>`}</select></label><button class="report-action report-action-light" id="download-shareholder-report" type="button">↓ 下载报告</button><button class="report-action report-action-outline" id="print-shareholder-report" type="button">打印 / PDF</button></div>`}
      <div class="shareholder-hero-meta"><span>报告时点 <b>${esc(shareholderDateLabel(data.asOf))}</b></span><span>数据范围 <b>${esc(data.scope || '当前账号可见范围')}</b></span><span>源数据 <b>${sourceLines || '暂无已上传快照'}</b></span></div>
    </header>
    <section class="report-kpi-grid">
      <article class="report-kpi report-kpi-featured"><span>实时总资产</span><strong>${shareholderMoney(k.assetAum)}</strong><small>${a.accounts == null ? '暂无资产来源' : `明细 ${shareholderNumber(a.accounts)} 个账户 · 入金 ${shareholderPercent(a.activationRate)}`}</small></article>
      <article class="report-kpi"><span>注册 → 开户成功</span><strong>${shareholderPercent(k.accountSuccessRate)}</strong><small>${shareholderNumber(k.opened)} / ${shareholderNumber(k.customers)} 人</small></article>
      <article class="report-kpi"><span>定增已转化金额</span><strong>${shareholderMoney(k.placementConvertedAmount)}</strong><small>${shareholderNumber(f.onsite?.converted?.count)} 人已形成实际参与</small></article>
      <article class="report-kpi"><span>XMAX 持仓</span><strong>${shareholderNumber(h.quantity)} <em>股</em></strong><small>${shareholderNumber(h.holders)} 人 · 市值 ${h.marketValue == null ? '未提供' : shareholderMoney(h.marketValue)}</small></article>
      <article class="report-kpi"><span>优先劣后协议金额</span><strong>${shareholderMoney(p.agreementUsd)}</strong><small>${shareholderNumber(p.participants)} 位已匹配参与客户</small></article>
    </section>
    <section class="report-grid report-grid-top">
      <article class="report-panel report-panel-asset"><header class="report-panel-head"><div><span class="report-eyebrow">ASSET COMPOSITION</span><h4>客户资产分层</h4><p>高净值资产与未激活账户的结构对照</p></div><strong class="report-panel-value">${shareholderPercent(a.activationRate)}</strong></header><div class="report-asset-body">${shareholderDonut(a.buckets || [], shareholderPercent(a.activationRate, 1), '入金率', bucketPalette)}<div class="report-legend">${assetLegend || '<div class="report-chart-empty">暂无资产分层来源</div>'}</div></div><footer class="report-panel-foot"><span>前 10 大客户占资产</span><b>${top10Text}</b><span>快照账户合计 ${shareholderNumber(bucketTotal)} 个</span></footer></article>
      <article class="report-panel report-panel-funnel"><header class="report-panel-head"><div><span class="report-eyebrow">CONVERSION FUNNEL</span><h4>从客户进入到实际参与</h4><p>每一步都保留源数据，不以缺失指标补齐漏斗</p></div><span class="report-chip">${esc(shareholderDateLabel(data.asOf))}</span></header>${shareholderFunnel(funnelItems, f.registration?.total)}<div class="report-funnel-callout"><span>最大提升空间</span><strong>${a.activationRate !== null && a.activationRate !== undefined && Number(a.activationRate) < 70 ? '资产激活' : '现场签约转化'}</strong><small>建议围绕低占比环节配置下一周期跟进</small></div></article>
    </section>
    <section class="report-grid report-grid-middle">
      <article class="report-panel report-panel-trend"><header class="report-panel-head"><div><span class="report-eyebrow">MULTI-SNAPSHOT TREND</span><h4>客户池与资产动能</h4><p>按上传快照日期绘制，空缺日期保持空白</p></div><div class="report-inline-legend"><span><i class="legend-dot legend-dot-teal"></i>资产总额</span><span><i class="legend-dot legend-dot-lime"></i>客户数</span></div></header><div class="report-trend-grid"><div>${shareholderSparkline(trend, 'assetAum', '#5b9b8d', shareholderMoney)}</div><div>${shareholderSparkline(trend, 'customers', '#c2db5f', shareholderNumber)}</div></div></article>
      <article class="report-panel report-panel-bridge"><header class="report-panel-head"><div><span class="report-eyebrow">CAPITAL BRIDGE</span><h4>定增现场金额桥</h4><p>签约金额拆解为已转化、已放弃 / 转二级与待转化</p></div><span class="report-chip">USD</span></header>${shareholderAmountBridge(f.onsite || {})}<div class="report-bridge-total"><span>签约总额</span><strong>${shareholderMoney(f.onsite?.signed?.amount)}</strong><small>已转化占签约金额 ${f.onsite?.signed?.amount ? shareholderPercent(f.onsite.converted.amount / f.onsite.signed.amount * 100, 1) : '—'}</small></div></article>
    </section>
    <section class="report-grid report-grid-bottom">
      <article class="report-panel report-panel-holdings"><header class="report-panel-head"><div><span class="report-eyebrow">XMAX POSITION</span><h4>持仓结构与产品信号</h4><p>${h.available ? `持仓 ${shareholderNumber(h.quantity)} 股 · 市值 ${h.marketValue == null ? '未提供' : shareholderMoney(h.marketValue)}${h.price ? ` @ $${h.price}/股` : ''}` : '尚未上传持仓快照'}</p></div><strong class="report-panel-value">${shareholderNumber(h.holders)}<small> 位持仓客户</small></strong></header><div class="report-holding-metrics"><div><span>二级买入</span><strong>${shareholderNumber(h.secondaryQuantity)}</strong><small>${shareholderNumber(h.secondaryHolders)} 位</small></div><div><span>大宗交易</span><strong>${shareholderNumber(h.blockQuantity)}</strong><small>${shareholderNumber(h.blockHolders)} 位</small></div><div><span>持仓占开户</span><strong>${h.holders != null && k.opened ? shareholderPercent(h.holders / k.opened * 100, 1) : '—'}</strong><small>以开户成功数为分母</small></div></div><div class="report-holding-bar"><i style="width:${h.quantity ? Math.min(100, Number(h.secondaryQuantity || 0) / Number(h.quantity) * 100) : 0}%"></i></div><div class="report-holding-bar-label"><span>二级</span><span>大宗</span></div></article>
      <article class="report-panel report-panel-insights"><header class="report-panel-head"><div><span class="report-eyebrow">EXECUTIVE READOUT</span><h4>经营解读</h4><p>用数据指出下一步应关注的经营动作</p></div><span class="report-chip">BOARD VIEW</span></header><div class="report-insight-list">${(data.insights || []).slice(0, 4).map(item => `<div class="report-insight report-insight-${esc(item.tone || 'neutral')}"><i></i><div><strong>${esc(item.title)}</strong><p>${esc(item.body)}</p></div></div>`).join('') || '<div class="report-chart-empty">当前资料不足以生成经营解读</div>'}</div></article>
      <article class="report-panel report-panel-priority"><header class="report-panel-head"><div><span class="report-eyebrow">PRODUCT MIX</span><h4>优先劣后资金质量</h4><p>协议金额与入金记录分开呈现</p></div><strong class="report-panel-value">${shareholderNumber(p.participants)}<small> 位客户</small></strong></header><div class="report-priority-number"><strong>${shareholderMoney(p.agreementUsd)}</strong><span>协议金额 · USD</span></div><div class="report-priority-rows"><div><span>已有入金记录</span><b>${shareholderNumber(p.fundedAccounts)} 位</b><small>${shareholderMoney(p.depositUsd)}</small></div><div><span>身份待确认金额</span><b>${shareholderNumber(p.pendingIdentities)} 条</b><small>金额已计入协议总额</small></div></div><div class="report-product-submetrics"><div><span>定增单签转化</span><strong>${shareholderNumber(f.singleSign?.converted?.count)} 人</strong><small>${shareholderMoney(f.singleSign?.converted?.amount)}</small></div><div><span>承诺购买待转化</span><strong>${shareholderNumber(f.promisePlan?.pending?.count)} 人</strong><small>${shareholderMoney(f.promisePlan?.pending?.amount)}</small></div></div></article>
    </section>
    <footer class="report-footnote"><span>口径说明</span><p>${(data.notes || []).map(note => esc(note)).join(' · ')}</p><small>JY Customer Operations · ${esc(shareholderDateLabel(data.asOf))}</small></footer>
  </div>`;
}

async function renderShareholderReport(content) {
  const query = state.shareholderAsOf ? `?as_of=${encodeURIComponent(state.shareholderAsOf)}` : '';
  const data = await api(`/api/workspace/dashboard/report${query}`);
  state.shareholderAsOf = data.asOf;
  content.innerHTML = shareholderReportMarkup(data);
  content.querySelector('#shareholder-as-of')?.addEventListener('change', event => { state.shareholderAsOf = event.target.value; renderShareholderReport(content).catch(error => { content.innerHTML = `<section class="section"><div class="empty">${esc(error.message)}</div></section>`; }); });
  content.querySelector('#print-shareholder-report')?.addEventListener('click', () => window.print());
  content.querySelector('#download-shareholder-report')?.addEventListener('click', () => downloadShareholderReport(data));
}

async function downloadShareholderReport(data) {
  const report = document.querySelector('#shareholder-report-page')?.cloneNode(true);
  if (!report) return;
  report.querySelector('.shareholder-controls')?.remove();
  report.querySelectorAll('button, select, label').forEach(node => node.remove());
  let css = '';
  try { css = await fetch('/shareholder-report.css').then(response => response.text()); } catch {}
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>港安客户资产与业务经营报告 · ${esc(data.asOf)}</title><style>${css}</style><style>body{margin:0;background:#f0f2ef}.shareholder-report{max-width:1440px;margin:0 auto}.shareholder-hero{border-radius:0!important}</style></head><body>${report.outerHTML}</body></html>`;
  const blob = new Blob([html], {type:'text/html;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `港安客户资产与业务经营报告-${data.asOf || '最新'}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  toast('股东报告已下载');
}
