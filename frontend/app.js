const state = {
  token: localStorage.getItem("jy_customer_token"), user: null, view: "dashboard", meta: null,
  customers: [], customerPage: 1, search: "", workflow: "all", metric: "", stage: "", ownerId: "", accountStatus: "", intentStatus: "", placementStatus: "", source: "", honganAdvisor: "", contactState: "", importJobId: "", importJobMode: "all", importHistoryExpanded: false, detail: null, importPreview: null, dashboardAssistantFocus: false,
  assignmentOwnerId: "", assignmentSourceAdvisorLabel: "", assignmentHonganAdvisor: "", assignmentSelectedCustomerIds: [], importReviewsIncludeResolved: false,
  viewHistory: [], muskzoomEntryUrl: "https://muskzoom.com",
};
const app = document.querySelector("#app");
const VIEWS = new Set(["dashboard", "customers", "placement", "followups", "imports", "reviews", "assignments", "fields", "bindings", "permissions", "audit"]);
const WORKSPACE_STATE_KEY = "jy_customer_workspace_state";
const WORKSPACE_STATE_FIELDS = ["view", "customerPage", "search", "workflow", "metric", "stage", "ownerId", "accountStatus", "intentStatus", "placementStatus", "source", "honganAdvisor", "contactState", "importJobId", "importJobMode"];
let restoredScrollY = null;
let lockedScrollY = 0;
let pageScrollLockDepth = 0;
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

function currentScrollY() { return document.body.classList.contains("drawer-open") ? lockedScrollY : window.scrollY; }
function saveWorkspaceState() {
  try {
    const saved = Object.fromEntries(WORKSPACE_STATE_FIELDS.map((key) => [key, state[key]]));
    sessionStorage.setItem(WORKSPACE_STATE_KEY, JSON.stringify({...saved, scrollY: currentScrollY()}));
  } catch {}
}
function restoreWorkspaceState() {
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(WORKSPACE_STATE_KEY) || "{}") || {}; } catch {}
  const hashView = decodeURIComponent(window.location.hash.slice(1));
  const view = VIEWS.has(hashView) ? hashView : VIEWS.has(saved.view) ? saved.view : "dashboard";
  if (saved.view === view) {
    WORKSPACE_STATE_FIELDS.filter((key) => key !== "view").forEach((key) => { if (saved[key] !== undefined) state[key] = saved[key]; });
    restoredScrollY = Math.max(0, Number(saved.scrollY) || 0);
  }
  state.view = view;
}

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#39;"}[char]));
const fmt = (value) => value ? String(value).replace("T", " ").slice(0, 16) : "-";
const money = (value) => `$${Number(value || 0).toLocaleString("en-US", {maximumFractionDigits: 0})}`;
const tag = (value, tone = "gray") => `<span class="tag ${tone}">${esc(value || "未填写")}</span>`;
const toneFor = (value) => ["已参与", "已开户", "资金到账", "已完成"].includes(value) ? "teal" : ["已流失", "开户失败"].includes(value) ? "red" : ["已锁定", "批次确认", "开放中"].includes(value) ? "amber" : "gray";
const roleTone = (role) => role === "manager" ? "teal" : role === "supervisor" ? "amber" : "cyan";
const toast = (message) => { const node = document.querySelector("#toast"); node.textContent = message; node.classList.add("show"); setTimeout(() => node.classList.remove("show"), 2400); };
const customerDisplayName = (row) => {
  const name = String(row.name || "").trim();
  return name && !["/", "-", "—"].includes(name) ? name : "";
};

async function runGlobalCustomerSearch(value) {
  const query = String(value || "").trim();
  if (!query) { toast("请输入客户姓名、手机号、公司或客户编号"); return; }
  state.search = query;
  state.stage = "";
  state.workflow = "all";
  state.metric = "";
  state.ownerId = "";
  state.accountStatus = "";
  state.intentStatus = "";
  state.placementStatus = "";
  state.source = "";
  state.honganAdvisor = "";
  state.contactState = "";
  state.importJobId = "";
  state.importJobMode = "all";
  state.customerPage = 1;
  await navigate("customers");
}

async function api(path, options = {}) {
  const headers = { ...(options.body ? {"Content-Type": "application/json"} : {}), ...(state.token ? {Authorization: `Bearer ${state.token}`} : {}), ...(options.headers || {}) };
  let response;
  try {
    response = await fetch(path, {...options, headers, credentials: "same-origin"});
  } catch {
    throw new Error("无法连接客户系统服务，请确认本机服务正在运行后重试。");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const fallback = response.status >= 500 ? "服务器处理本次导入时发生异常。文件预览已通过，请勿重复导入，并联系管理员查看服务日志。" : "操作失败，请稍后重试。"; const message = typeof data.detail === "string" ? data.detail : data.detail?.message || fallback; throw Object.assign(new Error(message), {status: response.status, data}); }
  return data;
}

function renderLogin(error = "", authConfig = {}) {
  state.muskzoomEntryUrl = authConfig.muskzoomEntryUrl || state.muskzoomEntryUrl;
  if (authConfig.passwordLoginEnabled === false) {
    const entryUrl = state.muskzoomEntryUrl;
    app.innerHTML = `<main class="login"><section class="login-panel"><div class="brand"><div class="brand-mark">JY</div><div><h1>骄阳</h1><p>客户生命周期工作台</p></div></div><div class="login-kicker">SECURE WORKSPACE / 01</div><div class="login-sso-message"><h2>请从 MuskZoom 进入</h2><p>${esc(error || "客户系统仅支持使用 MuskZoom 工作账号单点登录。")}</p><a class="primary-btn full" href="${esc(entryUrl)}">返回 MuskZoom <span>→</span></a></div></section></main>`;
    return;
  }
  app.innerHTML = `<main class="login"><section class="login-panel"><div class="brand"><div class="brand-mark">JY</div><div><h1>骄阳</h1><p>定增客户生命周期工作台</p></div></div><div class="login-kicker">SECURE WORKSPACE / 01</div><form id="login-form"><div class="field"><label for="username">MuskZoom 账号</label><input id="username" name="username" autocomplete="username" required placeholder="输入工作账号"></div><div class="field"><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required placeholder="输入密码"></div><div class="login-error">${esc(error)}</div><button class="primary-btn full" type="submit">进入客户工作台 <span>→</span></button></form><p class="hint" style="margin-top:18px">请使用 MuskZoom 中已启用的工作账号登录。账号权限由 MuskZoom 统一管理。</p></section></main>`;
  document.querySelector("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); try { const data = await api("/api/auth/login", {method:"POST", body: JSON.stringify({username: form.get("username"), password: form.get("password")})}); state.token = data.token; state.user = data.user; localStorage.setItem("jy_customer_token", state.token); restoreWorkspaceState(); await bootApp(); } catch (err) { renderLogin(err.message); } });
}

async function bootApp() { state.meta = await api("/api/meta"); renderShell(); await navigate(state.view, {preserveWorkflow:true, restoreScrollY:restoredScrollY}); restoredScrollY = null; }

function navButton(view, icon, label, active) { return `<button data-view="${view}" class="${active ? "active" : ""}"><span class="nav-icon">${icon}</span><span>${label}</span></button>`; }
function renderShell() {
  const canManageAssignments = Boolean(state.user.canManageAssignments);
  const canManageBindings = Boolean(state.user.canManageAdvisorBindings);
  const canManagePermissions = Boolean(state.user.canManageCrmPermissions);
  const canGoBack = state.viewHistory.length > 0 || state.view !== "dashboard";
  app.innerHTML = `<div class="shell flux-shell"><aside class="sidebar"><div class="side-brand"><div class="brand-mark">JY</div><div><strong>骄阳</strong><span>客户生命周期系统</span></div></div><div class="nav-caption">WORKSPACE</div><nav class="nav">${navButton("dashboard", "⌗", "工作台", state.view === "dashboard")}${navButton("customers", "▦", "客户数据表", state.view === "customers")}${navButton("placement", "◈", "定增批次", state.view === "placement")}${navButton("followups", "◌", "跟进工作", state.view === "followups")}${navButton("imports", "＋", "录入中心", state.view === "imports")}${state.user.canImportCustomers || canManagePermissions ? navButton("reviews", "!", "导入复核", state.view === "reviews") : ""}</nav><div class="nav-caption secondary">CONTROL</div><nav class="nav">${canManageAssignments ? navButton("assignments", "⇆", "客户归属", state.view === "assignments") : ""}${state.user.canManageCustomerFields ? navButton("fields", "⊞", "表头管理", state.view === "fields") : ""}${canManageBindings ? navButton("bindings", "⇄", "顾问绑定", state.view === "bindings") : ""}${canManagePermissions ? navButton("permissions", "⚙", "权限设置", state.view === "permissions") : ""}${canManagePermissions ? navButton("audit", "≡", "审计日志", state.view === "audit") : ""}</nav><section class="rail-promo"><div class="rail-promo-icon">✦</div><strong>客户推进助手</strong><p>把每次跟进都沉淀成可汇报的业务进度。</p><button data-view="followups">打开工作台</button></section><div class="side-user"><div class="online-dot"></div><strong>${esc(state.user.name)}</strong><span>${esc(state.user.roleLabel)} · ${esc(state.user.team)}</span><button class="muskzoom-link" id="return-muskzoom" type="button" title="返回 MuskZoom" aria-label="返回 MuskZoom">返回 MuskZoom <span aria-hidden="true">↩</span></button><button id="logout">退出登录</button></div></aside><main class="main"><header class="topbar"><button class="top-icon-button topbar-back" id="crm-back" type="button" title="返回 CRM 上一页" aria-label="返回 CRM 上一页" ${canGoBack ? "" : "disabled"}>←</button><div class="profile-chip"><div class="profile-avatar">${esc(state.user.name.slice(0, 1))}</div><div><strong>${esc(state.user.name)}</strong><span>${esc(state.user.roleLabel)} · ${esc(state.user.team)}</span></div></div><div class="topbar-center"><div class="eyebrow">JY CUSTOMER OPERATIONS</div><h2 id="page-title">工作台</h2><span class="context">数据范围：${state.user.customerScope === "self" ? "我的客户" : state.user.customerScope === "team" ? `本组 · ${esc(state.user.team)}` : "全量客户"}</span></div><div class="top-actions"><form class="top-search" id="global-search" role="search"><input id="global-customer-search" type="search" value="${esc(state.search)}" autocomplete="off" placeholder="搜索客户" aria-label="搜索姓名、手机号、公司或客户编号"><button type="submit" title="搜索客户" aria-label="搜索客户">⌕</button></form><span class="data-live"><i></i> LIVE</span>${tag(state.user.roleLabel, roleTone(state.user.role))}</div></header><section class="content" id="content"></section><div class="mobile-actions"><button data-view="customers">◎<span>客户表</span></button><button id="mobile-add">＋<span>新增</span></button><button data-view="followups">↗<span>跟进</span></button></div></main></div>`;
  const railAssistant = document.querySelector(".rail-promo button");
  railAssistant.dataset.view = "dashboard";
  railAssistant.textContent = "打开推进助手";
  document.querySelector(".rail-promo p").textContent = "识别风险、生成经营摘要和管理汇报。";
  document.querySelectorAll(".mobile-actions button").forEach((button) => {
    const label = button.querySelector("span");
    if (!label) return;
    Array.from(button.childNodes).filter((node) => node.nodeType === Node.TEXT_NODE).forEach((node) => node.remove());
    const icon = document.createElement("span");
    icon.className = "mobile-nav-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = button.id === "mobile-add" ? "＋" : button.dataset.view === "customers" ? "▦" : "↗";
    button.insertBefore(icon, label);
  });
  railAssistant.addEventListener("click", () => { state.dashboardAssistantFocus = true; });
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  document.querySelector("#crm-back")?.addEventListener("click", returnWithinCrm);
  document.querySelector("#return-muskzoom")?.addEventListener("click", returnToMuskZoom);
  document.querySelector("#global-search")?.addEventListener("submit", (event) => { event.preventDefault(); runGlobalCustomerSearch(document.querySelector("#global-customer-search").value); });
  document.querySelector("#mobile-add")?.addEventListener("click", () => openQuickCustomerForm());
  document.querySelector("#logout").addEventListener("click", async () => { await api("/api/session", {method:"DELETE"}).catch(() => {}); localStorage.removeItem("jy_customer_token"); state.token = null; state.user = null; state.viewHistory = []; await showLogin(); });
}

function returnToMuskZoom() { window.location.assign(state.muskzoomEntryUrl); }

async function returnWithinCrm() {
  const previousView = state.viewHistory.pop();
  if (previousView) {
    await navigate(previousView, {preserveWorkflow:true, skipViewHistory:true});
    return;
  }
  if (state.view !== "dashboard") await navigate("dashboard", {preserveWorkflow:true, skipViewHistory:true});
}

async function navigate(view, options = {}) {
  if (!VIEWS.has(view)) view = "dashboard";
  if (view === "assignments" && !state.user.canManageAssignments) view = "dashboard";
  if (view === "bindings" && !state.user.canManageAdvisorBindings) view = "dashboard";
  if (["permissions", "audit"].includes(view) && !state.user.canManageCrmPermissions) view = "dashboard";
  if (view === "fields" && !state.user.canManageCustomerFields) view = "dashboard";
  if (view === "reviews" && !state.user.canImportCustomers && !state.user.canManageCrmPermissions) view = "dashboard";
  saveWorkspaceState();
  const previousView = state.view;
  if (previousView !== view && !options.skipViewHistory) {
    state.viewHistory.push(previousView);
    if (state.viewHistory.length > 30) state.viewHistory.shift();
  }
  state.view = view; if (view === "customers" && previousView !== "customers" && !options.preserveWorkflow) { state.workflow = "all"; state.metric = ""; }
  if (!options.preserveRoute && window.location.hash !== `#${view}`) history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${view}`);
  renderShell(); const title = {dashboard:"工作台", customers:"客户数据表", placement:"定增批次", followups:"跟进工作", imports:"录入中心", reviews:"导入复核", assignments:"客户归属", fields:"表头管理", bindings:"顾问绑定", permissions:"权限设置", audit:"审计日志"}[view]; document.querySelector("#page-title").textContent = title;
  const content = document.querySelector("#content"); content.innerHTML = `<div class="empty">正在同步数据...</div>`;
  try { if (view === "dashboard") await renderDashboard(content); else if (view === "customers") await renderCustomers(content); else if (view === "placement") await renderBatches(content); else if (view === "followups") await renderFollowups(content); else if (view === "imports") renderImports(content); else if (view === "reviews") await renderImportReviews(content); else if (view === "assignments") await renderCustomerAssignments(content); else if (view === "fields") await renderFields(content); else if (view === "bindings") await renderAdvisorBindings(content); else if (view === "permissions") await renderPermissions(content); else if (view === "audit") await renderAudit(content); } catch (err) { content.innerHTML = `<section class="section"><div class="empty">${esc(err.message)}</div></section>`; }
  if (view === "imports" && state.user.canImportCustomers && !state.user.canManageCrmPermissions && !document.querySelector("#import-history")) {
    content.insertAdjacentHTML("beforeend", `<section class="section import-history" id="import-history"><div class="empty">正在读取导入批次...</div></section>`);
    renderImportHistory();
  }
  const scrollY = options.restoreScrollY;
  if (scrollY !== null && scrollY !== undefined) requestAnimationFrame(() => { window.scrollTo(0, scrollY); saveWorkspaceState(); });
  else if (previousView !== view) { window.scrollTo(0, 0); saveWorkspaceState(); }
  else saveWorkspaceState();
}

async function renderDashboardLegacy(content) {
  const data = await api("/api/dashboard"); const s = data.summary;
  const followupCount = data.recent.filter((row) => row.placement_status !== "已参与" && row.placement_status !== "已流失").length;
  const conversion = s.total ? Math.round(s.closed / s.total * 100) : 0;
  const funnel = [["客户进入", s.total, ""], ["已完成开户", s.accounts_opened, "opened"], ["定增意向", s.intended, "intent"], ["进入批次", s.batched, "batched"], ["资金到账", s.funded, "funded"], ["已参与", s.closed, "closed"]];
  const maxFunnel = Math.max(...funnel.map(([, count]) => count), 1);
  const bars = funnel.map(([label, count, workflow]) => `<button class="pipeline-bar" data-workflow="${workflow}"><span class="pipeline-bar-value">${count}</span><i style="height:${count ? Math.max(10, Math.round(count / maxFunnel * 138)) : 4}px"></i><small>${label}</small></button>`).join("");
  content.innerHTML = `<div class="dashboard-page component-dashboard"><div class="section-heading dashboard-heading"><div><div class="eyebrow">JY CUSTOMER OPERATIONS</div><h3>你好，${esc(state.user.name)}</h3><p>客户从香港开户到参与定增项目的推进概览。</p></div><div class="dashboard-date"><span>数据范围</span><strong>${state.user.customerScope === "self" ? "我的客户" : state.user.customerScope === "team" ? `本组 · ${esc(state.user.team)}` : "全量客户"}</strong><small>${new Date().toLocaleDateString("zh-CN")} · 实时</small></div></div><div class="component-grid"><section class="component-card pipeline-card"><div class="component-card-heading"><div><h3>客户闭环走势</h3><p>当前客户在每个关键节点的数量</p></div></div><div class="pipeline-chart">${bars}</div><div class="pipeline-footer"><span>当前客户池 <b>${s.total}</b> 位</span><button class="black-pill" data-view="customers">查看客户数据表</button></div></section><section class="component-card action-card"><div class="component-card-heading"><div><h3>今日推进</h3><p>优先处理需要继续跟进的客户</p></div></div><div class="action-number">${followupCount}</div><div class="action-label">位客户等待推进</div><div class="action-stat"><span>已参与定增</span><b>${s.closed} 位</b></div><button class="black-pill full-width" id="dashboard-followup">写今日跟进</button></section><section class="component-card health-card"><div class="component-card-heading"><div><h3>客户池概览</h3><p>可见客户的当前业务分布</p></div><button class="outline-pill" data-view="customers">查看客户</button></div><div class="health-value">${s.total}<small>位客户</small></div><div class="health-dots"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div><div class="health-footer"><span>进入批次 ${s.batched}</span><span>资金到账 ${s.funded}</span></div></section><section class="component-card goals-card"><div class="component-card-heading"><div><h3>定增目标</h3><p>基于当前可见客户的实时完成情况</p></div><button class="outline-pill" data-view="placement">批次计划</button></div><div class="goal-row"><span>香港券商开户</span><b>${s.accounts_opened} <small>/ ${s.total}</small></b><div class="progress-track"><i style="width:${s.total ? Math.round(s.accounts_opened / s.total * 100) : 0}%"></i></div><small>${s.total ? Math.round(s.accounts_opened / s.total * 100) : 0}% 已完成</small></div><div class="goal-row"><span>明确参与意向</span><b>${money(s.intent_amount)}</b><div class="progress-track"><i style="width:${s.total ? Math.round(s.intended / s.total * 100) : 0}%"></i></div><small>${s.intended} 位客户已确认意向</small></div><div class="goal-row"><span>实际参与金额</span><b>${money(s.actual_amount)}</b><div class="progress-track"><i style="width:${conversion}%"></i></div><small>${conversion}% 客户已实现闭环</small></div></section><section class="component-card batches-card"><div class="component-card-heading"><div><h3>进行中的批次</h3><p>一至两个月滚动的定增计划</p></div><button class="outline-pill" data-view="placement">查看全部</button></div><div class="batch-list component-list">${data.batches.length ? data.batches.slice(0, 3).map(batchRow).join("") : `<div class="empty">还没有定增批次。</div>`}</div></section><section class="component-card updates-card"><div class="component-card-heading"><div><h3>最近更新</h3><p>客户资料与业务状态变化</p></div><button class="outline-pill" data-view="customers">客户表</button></div><div class="recent-list component-list">${data.recent.length ? data.recent.slice(0, 5).map(recentRow).join("") : `<div class="empty">还没有客户数据。</div>`}</div></section><section class="component-card amount-card"><div class="component-card-heading"><div><h3>已确认参与</h3><p>客户实际参与金额</p></div><span class="neutral-dot"></span></div><div class="amount-number">${money(s.actual_amount)}</div><div class="amount-grid"><span><small>已参与客户</small><b>${s.closed}</b></span><span><small>最终流失</small><b>${s.lost}</b></span></div><button class="black-pill full-width" data-view="placement">查看定增批次</button></section></div></div>`;
  content.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  content.querySelectorAll("[data-customer-id]").forEach((button) => button.addEventListener("click", () => openDetail(button.dataset.customerId)));
  content.querySelector("#dashboard-followup")?.addEventListener("click", () => openQuickFollowForm());
  content.querySelectorAll("[data-workflow]").forEach((button) => button.addEventListener("click", () => { state.metric = button.dataset.workflow; state.workflow = "all"; state.search = ""; state.stage = ""; state.ownerId = ""; state.accountStatus = ""; state.intentStatus = ""; state.placementStatus = ""; state.source = ""; state.honganAdvisor = ""; state.contactState = ""; state.importJobId = ""; state.importJobMode = "all"; state.customerPage = 1; navigate("customers", {preserveWorkflow:true}); }));
}

function dashboardScopeLabel() { return state.user.customerScope === "self" ? "我的客户" : state.user.customerScope === "team" ? `本组 · ${state.user.team}` : "全量客户"; }
function dashboardRate(value, total) { return total ? Math.round(value / total * 100) : 0; }
function dashboardSmoothPath(points) {
  if (!points.length) return "";
  let path = `M ${points[0][0]} ${points[0][1]}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const previous = points[index - 1] || points[index]; const current = points[index];
    const next = points[index + 1]; const after = points[index + 2] || next;
    const controlOneX = current[0] + (next[0] - previous[0]) / 6;
    const controlOneY = current[1] + (next[1] - previous[1]) / 6;
    const controlTwoX = next[0] - (after[0] - current[0]) / 6;
    const controlTwoY = next[1] - (after[1] - current[1]) / 6;
    path += ` C ${controlOneX} ${controlOneY}, ${controlTwoX} ${controlTwoY}, ${next[0]} ${next[1]}`;
  }
  return path;
}
function dashboardTrendMarkup(trend) {
  const values = trend.map((point) => Number(point.count) || 0); const total = values.reduce((sum, value) => sum + value, 0);
  const width = 720; const height = 178; const left = 12; const right = 12; const top = 10; const bottom = 28;
  const max = Math.max(...values, 1); const usableWidth = width - left - right; const usableHeight = height - top - bottom;
  const points = values.map((value, index) => [left + usableWidth * index / Math.max(values.length - 1, 1), top + usableHeight - value / max * usableHeight]);
  const path = dashboardSmoothPath(points); const baseline = top + usableHeight;
  const area = `${path} L ${points[points.length - 1][0]} ${baseline} L ${points[0][0]} ${baseline} Z`;
  const grid = [0, .25, .5, .75, 1].map((fraction) => `<line x1="${left}" x2="${width - right}" y1="${top + usableHeight * fraction}" y2="${top + usableHeight * fraction}"/>`).join("");
  const labels = [0, 3, 6, 9, 13].filter((index) => trend[index]).map((index) => `<span style="left:${left + usableWidth * index / Math.max(values.length - 1, 1) / width * 100}%">${esc(trend[index].label)}</span>`).join("");
  let latestIndex = -1; values.forEach((value, index) => { if (value > 0) latestIndex = index; }); const focus = latestIndex >= 0 ? points[latestIndex] : null;
  return `<div class="ops-trend-number"><b>${total}</b><span>近 14 日已记录跟进</span>${total ? `<i>峰值 ${max} 次</i>` : `<i>等待首条记录</i>`}</div><div class="ops-trend-visual ${total ? "" : "is-empty"}"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="近十四日跟进记录趋势"><defs><linearGradient id="opsTrendArea" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#d5f14c" stop-opacity=".48"/><stop offset="100%" stop-color="#d5f14c" stop-opacity=".02"/></linearGradient></defs><g class="ops-chart-grid">${grid}</g><path class="ops-trend-area" d="${area}"/><path class="ops-trend-line" d="${path}"/>${focus ? `<line class="ops-focus-line" x1="${focus[0]}" x2="${focus[0]}" y1="${top}" y2="${baseline}"/><circle class="ops-focus-dot" cx="${focus[0]}" cy="${focus[1]}" r="5.2"/>` : ""}</svg>${total ? "" : `<div class="ops-trend-empty">近 14 日暂无跟进记录</div>`}<div class="ops-trend-labels">${labels}</div></div><div class="ops-trend-legend"><span><i></i>跟进记录</span><span>数据按当前可见范围统计</span></div>`;
}
function dashboardRadarMarkup(metrics) {
  const center = 65; const radius = 53; const vertex = (index, rate = 1) => {
    const angle = -Math.PI / 2 + Math.PI * 2 * index / metrics.length;
    return `${(center + Math.cos(angle) * radius * rate).toFixed(1)},${(center + Math.sin(angle) * radius * rate).toFixed(1)}`;
  };
  const polygon = (rate) => metrics.map((_, index) => vertex(index, rate)).join(" ");
  const valuePolygon = metrics.map((metric, index) => vertex(index, metric.value / 100)).join(" ");
  return `<div class="ops-radar-graphic"><svg viewBox="0 0 130 130" aria-label="客户推进结构"><polygon class="ops-radar-grid" points="${polygon(1)}"/><polygon class="ops-radar-grid middle" points="${polygon(.66)}"/><polygon class="ops-radar-grid inner" points="${polygon(.33)}"/><polygon class="ops-radar-fill" points="${valuePolygon}"/>${metrics.map((metric, index) => `<circle class="ops-radar-dot" cx="${vertex(index, metric.value / 100).split(",")[0]}" cy="${vertex(index, metric.value / 100).split(",")[1]}" r="2.6"/>`).join("")}</svg></div><div class="ops-radar-list">${metrics.map((metric, index) => `<div><span><i class="metric-${index}"></i>${esc(metric.label)}</span><b>${metric.value}%</b></div>`).join("")}</div>`;
}
function dashboardImportActivityMarkup(activity) {
  if (!activity?.weeks?.length) return "";
  const weeks = activity.weeks; const max = Math.max(...weeks.flatMap((week) => [Number(week.created) || 0, Number(week.opened) || 0]), 1);
  const bars = weeks.map((week) => `<div class="ops-import-week"><div class="ops-import-bars"><i class="created" style="height:${Math.max(3, Math.round((Number(week.created) || 0) / max * 92))}px" title="本周新增 ${week.created}"></i><i class="opened" style="height:${Math.max(3, Math.round((Number(week.opened) || 0) / max * 92))}px" title="本周新开户 ${week.opened}"></i></div><small>${esc(week.label)}</small></div>`).join("");
  const recent = (activity.recentJobs || []).slice(0, 3).map((job) => `<button class="ops-import-recent" data-import-filter="${esc(job.id)}" data-import-mode="all"><span>${esc(job.filename)}</span><b>+${job.created} · 开户 ${job.opened}</b></button>`).join("");
  return `<section class="ops-card ops-import-activity"><div class="ops-card-head"><div><h4>导入增长曲线</h4><p>按周统计真实新增与本批新开户；全量快照的重复客户不会重复计入。</p></div><span class="ops-period-chip">近 8 周</span></div><div class="ops-import-legend"><span><i class="created"></i>新增客户</span><span><i class="opened"></i>新开户</span></div><div class="ops-import-chart">${bars}</div>${recent ? `<div class="ops-import-recent-list">${recent}</div>` : ""}</section>`;
}
function dashboardResetCustomerFilters() {
  state.search = ""; state.workflow = "all"; state.metric = ""; state.stage = ""; state.ownerId = ""; state.accountStatus = "";
  state.intentStatus = ""; state.placementStatus = ""; state.source = ""; state.honganAdvisor = ""; state.contactState = ""; state.importJobId = ""; state.importJobMode = "all"; state.customerPage = 1;
}
function dashboardOpenCustomers(filters = {}) { dashboardResetCustomerFilters(); Object.assign(state, filters); navigate("customers", {preserveWorkflow:true}); }
function dashboardHandleAction(action, data) {
  if (action === "due") return navigate("followups");
  if (action === "intent") return dashboardOpenCustomers({workflow:"placement"});
  if (action === "contact") return dashboardOpenCustomers({contactState:"missing"});
  if (action === "customers") return dashboardOpenCustomers();
  if (action === "write-followup") return openQuickFollowForm();
  if (action === "risk-scan") return openDashboardRiskScan(data);
}
function dashboardReportLines(data, mode) {
  const s = data.summary; const risk = data.risks; const scope = dashboardScopeLabel(); const accountRate = dashboardRate(s.accounts_opened, s.total); const batchRate = dashboardRate(s.batched, s.intended);
  const period = new Date().toLocaleDateString("zh-CN", {year:"numeric", month:"long", day:"numeric"});
  const heading = mode === "management" ? "管理汇报摘要" : "今日经营摘要";
  return {heading, period, lines:[
    `${scope}当前共有 ${s.total} 位客户，已完成开户 ${s.accounts_opened} 位，开户完成率 ${accountRate}%。`,
    `确认定增意向 ${s.intended} 位，其中 ${s.batched} 位已进入批次，意向进入批次转化率 ${batchRate}%。`,
    `已实际参与 ${s.closed} 位客户，实际参与金额 ${money(s.actual_amount)}。`,
    `当前需关注：${risk.due} 位客户到期或逾期未跟进，${risk.intent_unbatched} 位确认意向客户尚未进入批次，${risk.missing_contact} 位客户缺少联系方式。`,
    mode === "management" ? "建议优先清理已确认意向但尚未进入批次的客户，并由负责人补齐跟进计划。" : "建议先处理到期跟进客户，再推进已确认意向、尚未入批次的客户。",
  ]};
}
function openDashboardReport(data, mode) {
  const report = dashboardReportLines(data, mode);
  openModal(`<div class="modal ops-report-modal"><div class="modal-header"><div><div class="eyebrow">AUTOMATED BUSINESS SUMMARY</div><h3>${report.heading}</h3><span class="hint">按当前数据范围自动生成 · ${report.period}</span></div><button class="close-btn" data-close>×</button></div><div class="modal-body"><div class="ops-report-copy">${report.lines.map((line, index) => `<p><b>${String(index + 1).padStart(2, "0")}</b><span>${esc(line)}</span></p>`).join("")}</div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>关闭</button></div></div>`);
}
function openDashboardRiskScan(data) {
  const risk = data.risks;
  const items = [
    ["到期或逾期跟进", risk.due, "查看跟进工作", "due"],
    ["确认意向但尚未进入批次", risk.intent_unbatched, "查看待推进客户", "intent"],
    ["缺少可用联系方式", risk.missing_contact, "补充客户资料", "contact"],
    ["长期未有跟进记录", risk.stalled, "查看客户数据表", "customers"],
  ];
  openModal(`<div class="modal ops-report-modal"><div class="modal-header"><div><div class="eyebrow">RULE-BASED RISK SCAN</div><h3>客户风险扫描</h3><span class="hint">仅基于当前可见范围内的真实客户状态和跟进记录。</span></div><button class="close-btn" data-close>×</button></div><div class="modal-body"><div class="ops-risk-list">${items.map(([label, count, actionLabel, action]) => `<article><div><span>${esc(label)}</span><b>${count} 位</b></div><button class="secondary-btn" data-dashboard-action="${action}">${esc(actionLabel)}</button></article>`).join("")}</div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>关闭</button></div></div>`);
  document.querySelectorAll(".modal [data-dashboard-action]").forEach((button) => button.addEventListener("click", () => { closeModal(); dashboardHandleAction(button.dataset.dashboardAction, data); }));
}
async function renderDashboard(content) {
  const data = await api("/api/dashboard"); const s = data.summary; const risk = data.risks || {}; const quality = data.quality || {}; const teams = data.teams || [];
  const accountRate = dashboardRate(s.accounts_opened, s.total); const batchRate = dashboardRate(s.batched, s.intended); const closeRate = dashboardRate(s.closed, s.batched);
  const healthScore = s.total ? Math.round(((quality.assigned || 0) + (quality.contactable || 0) + (quality.recently_updated || 0) + Math.max(0, s.total - (quality.duplicate_name_groups || 0))) / s.total / 4 * 100) : 0;
  const radarMetrics = [{label:"开户转化", value:accountRate}, {label:"意向沉淀", value:dashboardRate(s.intended, s.accounts_opened)}, {label:"入批次", value:batchRate}, {label:"闭环", value:closeRate}];
  const teamMax = Math.max(...teams.map((team) => Number(team.batched) || 0), 1);
  const scope = dashboardScopeLabel(); const dateLabel = new Date().toLocaleDateString("zh-CN", {year:"numeric", month:"2-digit", day:"2-digit"});
  const dueMessage = risk.due ? "先处理已有下次跟进计划、但尚未完成跟进的客户，避免业务推进停滞。" : "当前没有到期跟进事项，可转向推进已确认意向、尚未进入批次的客户。";
  content.innerHTML = `<div class="ops-dashboard"><section class="ops-dashboard-heading"><div><div class="eyebrow">JY CUSTOMER INTELLIGENCE</div><h3>经营工作台</h3><p>把客户推进、转化结构与需要处理的异常，收敛到同一个工作视图。</p></div><div class="ops-date-context"><span>数据范围</span><b>${esc(scope)}</b><small>${dateLabel} · 实时</small></div></section><section class="ops-score-strip"><div class="ops-score-lead"><div class="ops-score-orbit"><b>${healthScore}</b></div><div><small>数据健康指数</small><strong>客户数据基础 ${healthScore >= 75 ? "处于稳定区间" : "仍有改善空间"}</strong><span>基于负责人、联系方式、更新时效和同名待核查情况</span></div></div><div class="ops-score"><label>全量客户</label><b>${s.total}</b><i></i><small>当前可见客户池</small></div><div class="ops-score"><label>进入定增批次</label><b>${s.batched}</b><i></i><small>占确认意向 ${batchRate}%</small></div><div class="ops-score"><label>实际参与金额</label><b>${money(s.actual_amount)}</b><i></i><small>已闭环 ${s.closed} 位客户</small></div></section><section class="ops-main-grid"><div class="ops-primary-column"><article class="ops-card ops-trend-card"><div class="ops-card-head"><div><h4>客户跟进动能</h4><p>近 14 日完成的跟进记录，不以导入时间代替业务趋势。</p></div><span class="ops-period-chip">近 14 日</span></div>${dashboardTrendMarkup(data.activityTrend || [])}</article><article class="ops-card ops-flow-card"><div class="ops-card-head"><div><h4>闭环流向</h4><p>客户从进入客户池到实际参与的当前分布</p></div><button class="ops-outline-btn" data-dashboard-action="intent">查看漏斗</button></div><div class="ops-flow-stages"><div><label>客户进入</label><b>${s.total}</b><span>当前可见客户</span></div><div><label>完成开户</label><b>${s.accounts_opened}</b><span>转化率 ${accountRate}%</span></div><div><label>确认意向</label><b>${s.intended}</b><span>开户后 ${dashboardRate(s.intended, s.accounts_opened)}%</span></div><div><label>进入批次</label><b>${s.batched}</b><span>意向后 ${batchRate}%</span></div><div><label>实际参与</label><b>${s.closed}</b><span>批次后 ${closeRate}%</span></div></div><div class="ops-flow-progress"><i></i><i></i><i></i><i></i><i></i></div><footer><span>当前主要待提升环节：<b>确认意向 → 进入批次</b></span><button class="ops-link-btn" data-dashboard-action="intent">查看 ${risk.intent_unbatched || 0} 位待推进客户 →</button></footer></article></div><aside class="ops-side-column"><article class="ops-attention-card"><div><h4>今日需要决策</h4><span>${risk.due ? "优先级 P1" : "当前无到期"}</span></div><div class="ops-attention-value"><b>${risk.due || 0}</b><span>位客户到期或逾期未跟进</span></div><p>${dueMessage}</p><button data-dashboard-action="${risk.due ? "due" : "intent"}">${risk.due ? "查看待跟进客户" : "查看待推进客户"}</button></article><article class="ops-card ops-radar-card"><div class="ops-card-head"><div><h4>团队推进结构</h4><p>转化率按当前可见客户实时计算</p></div><span class="ops-period-chip">转化维度</span></div><div class="ops-radar-body">${dashboardRadarMarkup(radarMetrics)}</div></article></aside></section><section class="ops-bottom-grid"><article class="ops-card ops-team-card"><div class="ops-card-head"><div><h4>团队推进表现</h4><p>按进入定增批次的客户数排序</p></div><button class="ops-outline-btn" data-dashboard-action="customers">查看客户</button></div><div class="ops-team-list">${teams.length ? teams.map((team, index) => `<div class="ops-team-row"><em>${String(index + 1).padStart(2, "0")}</em><div><b>${esc(team.group_name || "待分配")}</b><span>${team.total} 位客户 · 已参与 ${team.closed || 0} 位</span></div><div class="ops-team-bar"><i style="width:${Math.round((team.batched || 0) / teamMax * 100)}%"></i></div><strong>${team.batched || 0} 位</strong></div>`).join("") : `<div class="ops-empty-row">暂无可比较的团队数据。</div>`}</div></article><article class="ops-card ops-quality-card"><div class="ops-card-head"><div><h4>数据可信度</h4><p>经营判断依赖的数据基础</p></div><button class="ops-outline-btn" data-dashboard-action="contact">去修复</button></div><div class="ops-quality-body"><div class="ops-quality-ring" style="--score:${healthScore}%"><div><b>${healthScore}%</b><span>完整度</span></div></div><div class="ops-quality-list"><div><span>负责人已明确</span><b>${quality.assigned || 0}</b></div><div><span>有可用联系方式</span><b>${quality.contactable || 0}</b></div><div><span>近 7 日记录更新</span><b>${quality.recently_updated || 0}</b></div><div><span>同名待核查组</span><b>${quality.duplicate_name_groups || 0}</b></div></div></div></article></section><section class="ops-assistant" id="dashboard-assistant"><header><div class="ops-ai-mark">✦</div><div><h4>客户推进助手</h4><p>从数据异常到下一步动作，而不只是页面跳转。</p></div><i></i></header><div class="ops-assistant-tasks"><article><span>01</span><div><b>${risk.due || 0} 位客户到期或逾期未跟进</b><small>已有跟进计划，优先完成沟通并记录结果</small></div><button data-dashboard-action="due">处理 →</button></article><article><span>02</span><div><b>${risk.intent_unbatched || 0} 位确认意向客户尚未进入批次</b><small>是当前漏斗中最直接的推进机会</small></div><button data-dashboard-action="intent">查看 →</button></article><article><span>03</span><div><b>${risk.missing_contact || 0} 位客户缺少可用联系方式</b><small>先补全基础资料，再参与后续经营统计</small></div><button data-dashboard-action="contact">补全 →</button></article></div><div class="ops-assistant-intel"><small>经营解读</small><p>开户完成率为 ${accountRate}%，确认意向到进入批次的转化率为 ${batchRate}%。优先推进已确认意向、尚未进入批次的客户，能对本周期闭环形成最直接的提升。</p></div><footer><button data-dashboard-action="risk-scan">风险扫描</button><button data-dashboard-report="daily">生成日报</button><button class="ops-primary-action" data-dashboard-report="management">✦ 生成管理汇报</button></footer></section></div>`;
  content.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  content.querySelectorAll("[data-dashboard-action]").forEach((button) => button.addEventListener("click", () => dashboardHandleAction(button.dataset.dashboardAction, data)));
  content.querySelectorAll("[data-dashboard-report]").forEach((button) => button.addEventListener("click", () => openDashboardReport(data, button.dataset.dashboardReport)));
  if (data.importActivity) content.insertAdjacentHTML("beforeend", dashboardImportActivityMarkup(data.importActivity));
  content.querySelectorAll("[data-import-filter]").forEach((button) => button.addEventListener("click", () => { state.importJobId = button.dataset.importFilter; state.importJobMode = button.dataset.importMode || "all"; state.customerPage = 1; navigate("customers", {preserveWorkflow:true}); }));
  if (state.dashboardAssistantFocus) { state.dashboardAssistantFocus = false; requestAnimationFrame(() => content.querySelector("#dashboard-assistant")?.scrollIntoView({behavior:"smooth", block:"center"})); }
}

function batchRow(row) { const ratio = row.intent_amount ? Math.min(100, row.funded_amount / row.intent_amount * 100) : 0; return `<div class="batch-row"><div><strong>${esc(row.name)}</strong><span>${esc(row.close_date || "日期待定")} · ${tag(row.status, toneFor(row.status))}</span></div><div class="batch-progress"><i style="width:${ratio}%"></i></div><div><b>${money(row.funded_amount)}</b><small> / ${money(row.intent_amount)}</small></div></div>`; }
function recentRow(row) { return `<button class="recent-row" data-customer-id="${esc(row.id)}"><span class="recent-mark ${toneFor(row.placement_status)}"></span><span><strong>${esc(row.name)}</strong><small>${esc(row.customer_code)} · ${esc(row.owner_name)}</small></span><span>${tag(row.placement_status, toneFor(row.placement_status))}<small>${fmt(row.updated_at)}</small></span></button>`; }

async function renderCustomers(content) {
  const query = new URLSearchParams({
    search: state.search, stage: state.stage, workflow: state.workflow === "all" ? "" : state.workflow,
    metric: state.metric, ownerId: state.ownerId, accountStatus: state.accountStatus, intentStatus: state.intentStatus,
    placementStatus: state.placementStatus, source: state.source, honganAdvisor: state.honganAdvisor,
    contactState: state.contactState, importJobId: state.importJobId, importJobMode: state.importJobMode, page: state.customerPage, pageSize: 100,
  });
  const data = await api(`/api/customers?${query}`);
  state.customers = data.items;
  const tabs = [["all", "全部客户"], ["account", "开户未完成"], ["placement", "定增推进"], ["closed", "已参与"], ["lost", "已流失"]];
  const metricLabels = {opened:"已完成开户", intent:"已确认定增意向", batched:"已进入定增批次", funded:"资金已到账", closed:"已参与定增"};
  const metricNotice = state.metric ? `<span class="filter-chip">指标：${metricLabels[state.metric] || "指标客户"}<button id="clear-metric" title="清除指标筛选" aria-label="清除指标筛选">×</button></span>` : "";
  const importNotice = state.importJobId ? `<span class="filter-chip">导入批次：${esc(state.importJobMode === "created" ? "本批新增" : state.importJobMode === "updated" ? "本批更新" : state.importJobMode === "opened" ? "本批新开户" : "本批变更")}<button id="clear-import-job" title="清除导入批次筛选" aria-label="清除导入批次筛选">×</button></span>` : "";
  const filterCount = [state.search, state.stage, state.ownerId, state.accountStatus, state.intentStatus, state.placementStatus, state.source, state.honganAdvisor, state.contactState, state.metric, state.importJobId].filter(Boolean).length;
  const pages = Math.max(1, Math.ceil(data.total / 100));
  const ownerChoices = state.meta.ownerChoices || state.meta.owners || [];
  const honganAdvisors = state.meta.honganAdvisors || [];
  content.innerHTML = `<div class="section-heading"><div><h3>客户数据表</h3><p>港安顾问记录外部引荐关系；当前骄阳负责人决定内部跟进、可见范围和业绩归属。</p></div><div class="heading-actions">${state.user.canManageCustomerFields ? `<button class="secondary-btn" id="manage-fields">⊞ 管理表头</button>` : ""}<button class="primary-btn" id="quick-add">＋ 新增客户</button></div></div><div class="tabs">${tabs.map(([key, label]) => `<button class="tab ${state.workflow === key ? "active" : ""}" data-workflow-tab="${key}">${label}</button>`).join("")}</div><form class="customer-filters" id="customer-filters"><div class="filter-search-row"><input class="search-input" id="customer-search" value="${esc(state.search)}" placeholder="客户、微信昵称、顾问、手机号或公司；多个词可组合"><button class="primary-btn filter-submit" type="submit">搜索</button></div><div class="filter-selects"><select data-customer-filter="ownerId"><option value="">全部当前骄阳负责人</option>${ownerChoices.map((owner) => `<option value="${esc(owner.id)}" ${state.ownerId === owner.id ? "selected" : ""}>${esc(owner.name)}${owner.team ? ` · ${esc(owner.team)}` : ""}</option>`).join("")}</select><select data-customer-filter="honganAdvisor"><option value="">全部港安顾问</option>${honganAdvisors.map((advisor) => `<option value="${esc(advisor)}" ${state.honganAdvisor === advisor ? "selected" : ""}>${esc(advisor)}</option>`).join("")}</select><select data-customer-filter="stage"><option value="">全部生命周期</option>${state.meta.stages.map((value) => `<option value="${esc(value)}" ${state.stage === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><select data-customer-filter="accountStatus"><option value="">全部开户状态</option>${state.meta.accountStatuses.map((value) => `<option value="${esc(value)}" ${state.accountStatus === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><select data-customer-filter="intentStatus"><option value="">全部定增意向</option>${state.meta.intentStatuses.map((value) => `<option value="${esc(value)}" ${state.intentStatus === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><select data-customer-filter="placementStatus"><option value="">全部定增推进</option>${state.meta.placementStatuses.map((value) => `<option value="${esc(value)}" ${state.placementStatus === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><select data-customer-filter="source"><option value="">全部来源</option>${state.meta.sources.map((value) => `<option value="${esc(value)}" ${state.source === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><select data-customer-filter="contactState"><option value="">联系方式不限</option><option value="complete" ${state.contactState === "complete" ? "selected" : ""}>有联系方式（手机/邮箱/微信）</option><option value="missing" ${state.contactState === "missing" ? "selected" : ""}>待补联系方式（均为空）</option><option value="wechat_only" ${state.contactState === "wechat_only" ? "selected" : ""}>仅有微信昵称（待补手机/邮箱）</option></select><button class="secondary-btn clear-filters" id="clear-customer-filters" type="button">清除筛选</button></div><div class="filter-status"><span>${filterCount ? `已启用 ${filterCount} 个条件` : "未启用筛选条件"}</span>${metricNotice}${importNotice}<b>${data.total} 位客户</b></div></form><section class="section table-section"><div class="table-wrap">${customerTable(data.items, true)}</div>${pages > 1 ? `<div class="table-pagination"><button class="secondary-btn" data-page="${state.customerPage - 1}" ${state.customerPage === 1 ? "disabled" : ""}>上一页</button><span>第 ${state.customerPage} / ${pages} 页</span><button class="secondary-btn" data-page="${state.customerPage + 1}" ${state.customerPage >= pages ? "disabled" : ""}>下一页</button></div>` : ""}</section>`;
  setupCustomerFloatingScrollbar(content);
  const customerSearch = content.querySelector("#customer-search");
  if (customerSearch) customerSearch.placeholder = "客户、TW编号、微信昵称、顾问、手机号或公司；多个词可组合";
  document.querySelector("#customer-filters").addEventListener("submit", (event) => { event.preventDefault(); state.search = document.querySelector("#customer-search").value.trim(); state.customerPage = 1; renderCustomers(content); });
  content.querySelectorAll("[data-customer-filter]").forEach((select) => select.addEventListener("change", () => { state[select.dataset.customerFilter] = select.value; state.customerPage = 1; renderCustomers(content); }));
  document.querySelector("#clear-customer-filters").addEventListener("click", () => { state.search = ""; state.stage = ""; state.ownerId = ""; state.accountStatus = ""; state.intentStatus = ""; state.placementStatus = ""; state.source = ""; state.honganAdvisor = ""; state.contactState = ""; state.importJobId = ""; state.importJobMode = "all"; state.metric = ""; state.workflow = "all"; state.customerPage = 1; renderCustomers(content); });
  document.querySelector("#clear-import-job")?.addEventListener("click", () => { state.importJobId = ""; state.importJobMode = "all"; state.customerPage = 1; renderCustomers(content); });
  document.querySelector("#clear-metric")?.addEventListener("click", () => { state.metric = ""; state.customerPage = 1; renderCustomers(content); });
  document.querySelector("#quick-add").addEventListener("click", () => openQuickCustomerForm()); document.querySelector("#manage-fields")?.addEventListener("click", () => navigate("fields"));
  content.querySelectorAll("[data-workflow-tab]").forEach((button) => button.addEventListener("click", () => { state.workflow = button.dataset.workflowTab; state.metric = ""; state.customerPage = 1; renderCustomers(content); }));
  content.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => { state.customerPage = Number(button.dataset.page); renderCustomers(content); })); content.querySelectorAll("[data-open-customer]").forEach((node) => node.addEventListener("click", () => openDetail(node.dataset.openCustomer))); content.querySelectorAll("[data-grid-field]").forEach((input) => input.addEventListener("change", () => saveGridCell(input))); content.querySelectorAll("[data-grid-core]").forEach((input) => input.addEventListener("change", () => saveCoreGridCell(input)));
}

function assignmentDisplayName(item) { return customerDisplayName(item) || item.customer_code || "未命名客户"; }
function assignmentOwnerOptions(selected = "", includeBlank = true) {
  const owners = state.meta.ownerChoices || state.meta.owners || [];
  return `${includeBlank ? '<option value="">选择新的负责人</option>' : ""}${owners.map((owner) => `<option value="${esc(owner.id)}" ${owner.id === selected ? "selected" : ""}>${esc(owner.name)}${owner.team ? ` · ${esc(owner.team)}` : ""}</option>`).join("")}`;
}
function updateAssignmentSelectionUI(root) {
  const selected = new Set(state.assignmentSelectedCustomerIds);
  const visibleIds = [...root.querySelectorAll("[data-assignment-customer]")].map((input) => input.dataset.assignmentCustomer);
  const selectedVisible = visibleIds.filter((id) => selected.has(id));
  const count = root.querySelector("#assignment-selected-count");
  const assign = root.querySelector("#bulk-assign-customers");
  const all = root.querySelector("#assignment-select-all");
  const tableAll = root.querySelector("#assignment-select-all-table");
  if (count) count.textContent = `已选择 ${selectedVisible.length} 位客户`;
  if (assign) assign.disabled = selectedVisible.length === 0;
  if (all) {
    all.checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
    all.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
  }
  if (tableAll) {
    tableAll.checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
    tableAll.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
  }
}
async function renderCustomerAssignments(content) {
  if (!Array.isArray(state.assignmentSelectedCustomerIds)) state.assignmentSelectedCustomerIds = [];
  const query = new URLSearchParams({ownerId: state.assignmentOwnerId, sourceAdvisorLabel: state.assignmentSourceAdvisorLabel, honganAdvisor: state.assignmentHonganAdvisor});
  const data = await api(`/api/customer-assignments?${query}`);
  const visibleIds = new Set(data.items.map((item) => item.id));
  state.assignmentSelectedCustomerIds = state.assignmentSelectedCustomerIds.filter((id) => visibleIds.has(id));
  const selected = new Set(state.assignmentSelectedCustomerIds);
  const unassigned = (data.ownerGroups || []).find((item) => item.owner_id === "unassigned");
  const sourceOptions = (data.sourceAdvisorGroups || []).map((item) => `<option value="${esc(item.source_advisor_label)}" ${state.assignmentSourceAdvisorLabel === item.source_advisor_label ? "selected" : ""}>${esc(item.source_advisor_label)} · ${item.count} 位</option>`).join("");
  const honganOptions = (data.honganAdvisorGroups || []).map((item) => `<option value="${esc(item.hongan_advisor)}" ${state.assignmentHonganAdvisor === item.hongan_advisor ? "selected" : ""}>${esc(item.hongan_advisor)} · ${item.count} 位</option>`).join("");
  const currentOwnerOptions = (data.ownerGroups || []).map((item) => `<option value="${esc(item.owner_id)}" ${state.assignmentOwnerId === item.owner_id ? "selected" : ""}>${esc(item.owner_name)}${item.owner_team ? ` · ${esc(item.owner_team)}` : ""} · ${item.count} 位</option>`).join("");
  const canReturnToPool = state.user.canManageAssignments && state.user.customerScope === "all";
  content.innerHTML = `<div class="section-heading"><div><div class="eyebrow">OWNERSHIP CONTROL</div><h3>客户归属</h3><p>负责人决定客户可见范围、跟进责任和后续业绩归属。原表骄阳顾问可作为历史分配依据，港安顾问仍是独立的外部引荐关系。</p></div><div class="heading-actions"><span class="assignment-total">当前范围 ${data.total} 位</span><button class="primary-btn" id="apply-source-advisors" type="button">按原表顾问分配待分配客户</button></div></div><section class="assignment-overview"><article><span>待分配池</span><b>${unassigned?.count || 0}</b><small>${canReturnToPool ? "可统一分配或放回待分配池" : "仅全量数据范围可处理待分配池"}</small></article><article><span>当前负责人类别</span><b>${(data.ownerGroups || []).length}</b><small>可按负责人筛选后统一调整</small></article><article><span>历史顾问标签</span><b>${(data.sourceAdvisorGroups || []).length}</b><small>一人设为主负责人，其余设为协同负责人</small></article></section><section class="section assignment-workspace"><div class="assignment-filters"><label><span>当前负责人</span><select id="assignment-owner-filter"><option value="">全部当前负责人</option>${currentOwnerOptions}</select></label><label><span>原表骄阳顾问</span><select id="assignment-source-filter"><option value="">全部历史顾问</option>${sourceOptions}</select></label><label><span>港安顾问</span><select id="assignment-hongan-filter"><option value="">全部港安顾问</option>${honganOptions}</select></label><button class="secondary-btn" id="clear-assignment-filters" type="button">清除筛选</button></div><div class="assignment-bulkbar"><label class="assignment-select-all"><input id="assignment-select-all" type="checkbox"><span>选择当前结果</span></label><span id="assignment-selected-count">已选择 ${selected.size} 位客户</span><div class="assignment-bulk-actions"><select id="assignment-target-owner" aria-label="选择新的骄阳负责人">${assignmentOwnerOptions()}</select><button class="secondary-btn" id="assignment-to-pool" ${canReturnToPool ? "" : "hidden"}>放回待分配池</button><button class="primary-btn" id="bulk-assign-customers" ${selected.size ? "" : "disabled"}>批量调整归属</button></div></div><div class="table-wrap"><table class="assignment-table"><thead><tr><th><input id="assignment-select-all-table" type="checkbox" aria-label="选择全部当前结果"></th><th>客户</th><th>当前骄阳负责人</th><th>原表骄阳顾问</th><th>港安顾问</th><th>开户 / 定增</th><th>最后更新</th><th></th></tr></thead><tbody>${data.items.length ? data.items.map((item) => { const collaborators = (item.collaborators || []).map((person) => person.name).filter(Boolean).join("、"); return `<tr><td><input type="checkbox" data-assignment-customer="${esc(item.id)}" ${selected.has(item.id) ? "checked" : ""} aria-label="选择 ${esc(assignmentDisplayName(item))}"></td><td><button class="link-btn" data-open-customer="${esc(item.id)}">${esc(assignmentDisplayName(item))}</button><div class="hint">${esc(item.customer_code)}</div></td><td><strong>${esc(item.owner_name)}</strong><div class="hint">${esc(item.owner_team)}${collaborators ? `<br>协同负责人：${esc(collaborators)}` : ""}</div></td><td>${esc(item.source_advisor_label || "-")}</td><td>${esc(item.hongan_advisor || "-")}</td><td>${tag(item.account_status, toneFor(item.account_status))} ${tag(item.placement_status, toneFor(item.placement_status))}</td><td class="hint">${fmt(item.updated_at)}</td><td><button class="secondary-btn" data-reassign-customer="${esc(item.id)}">调整</button></td></tr>`; }).join("") : `<tr><td colspan="8"><div class="empty">当前条件下没有可调整的客户。</div></td></tr>`}</tbody></table></div></section>`;
  const applyFilters = () => renderCustomerAssignments(content);
  content.querySelector("#assignment-owner-filter").addEventListener("change", (event) => { state.assignmentOwnerId = event.target.value; state.assignmentSelectedCustomerIds = []; applyFilters(); });
  content.querySelector("#assignment-source-filter").addEventListener("change", (event) => { state.assignmentSourceAdvisorLabel = event.target.value; state.assignmentSelectedCustomerIds = []; applyFilters(); });
  content.querySelector("#assignment-hongan-filter").addEventListener("change", (event) => { state.assignmentHonganAdvisor = event.target.value; state.assignmentSelectedCustomerIds = []; applyFilters(); });
  content.querySelector("#clear-assignment-filters").addEventListener("click", () => { state.assignmentOwnerId = ""; state.assignmentSourceAdvisorLabel = ""; state.assignmentHonganAdvisor = ""; state.assignmentSelectedCustomerIds = []; applyFilters(); });
  content.querySelector("#apply-source-advisors").addEventListener("click", async () => {
    const waiting = Number(unassigned?.count || 0);
    if (!waiting) { toast("当前没有待分配客户"); return; }
    if (!window.confirm(`将按“原表骄阳顾问”分配 ${waiting} 位待分配客户。每组第一位设为主负责人，其余设为协同负责人；已有负责人不会被覆盖。确认继续吗？`)) return;
    try {
      const result = await api("/api/customer-assignments/apply-source-advisors", {method:"POST", body:JSON.stringify({onlyUnassigned:true})});
      const unresolved = result.unresolvedCount ? `，${result.unresolvedCount} 条需检查顾问名称` : "";
      toast(`已分配 ${result.assignedCount} 位客户，${result.collaboratorCount} 位含协同负责人${unresolved}`);
      state.assignmentSelectedCustomerIds = [];
      await renderCustomerAssignments(content);
    } catch (err) { toast(err.message); }
  });
  const setAll = (checked) => { state.assignmentSelectedCustomerIds = checked ? [...visibleIds] : []; updateAssignmentSelectionUI(content); };
  content.querySelector("#assignment-select-all").addEventListener("change", (event) => setAll(event.target.checked));
  content.querySelector("#assignment-select-all-table").addEventListener("change", (event) => setAll(event.target.checked));
  content.querySelectorAll("[data-assignment-customer]").forEach((input) => input.addEventListener("change", () => { const next = new Set(state.assignmentSelectedCustomerIds); if (input.checked) next.add(input.dataset.assignmentCustomer); else next.delete(input.dataset.assignmentCustomer); state.assignmentSelectedCustomerIds = [...next]; updateAssignmentSelectionUI(content); }));
  content.querySelectorAll("[data-open-customer]").forEach((button) => button.addEventListener("click", () => openDetail(button.dataset.openCustomer)));
  content.querySelectorAll("[data-reassign-customer]").forEach((button) => button.addEventListener("click", () => { const item = data.items.find((row) => row.id === button.dataset.reassignCustomer); if (item) openAssignFormNew(item, () => renderCustomerAssignments(content)); }));
  content.querySelector("#bulk-assign-customers").addEventListener("click", () => openBulkAssignForm(content, data.items));
  content.querySelector("#assignment-to-pool")?.addEventListener("click", () => openBulkAssignForm(content, data.items, "unassigned"));
  updateAssignmentSelectionUI(content);
}
function openBulkAssignForm(content, items, forcedOwnerId = "") {
  const selectedIds = state.assignmentSelectedCustomerIds.filter((id) => items.some((item) => item.id === id));
  if (!selectedIds.length) { toast("请先选择需要调整的客户"); return; }
  const selectedOwner = forcedOwnerId || document.querySelector("#assignment-target-owner")?.value || "";
  const ownerOptions = assignmentOwnerOptions(selectedOwner, Boolean(forcedOwnerId));
  openModal(`<div class="modal"><div class="modal-header"><div><h3>${forcedOwnerId ? "放回待分配池" : "批量调整客户归属"}</h3><span class="hint">将修改 ${selectedIds.length} 位客户的当前骄阳负责人。</span></div><button class="close-btn" data-close>×</button></div><form id="bulk-assignment-form"><div class="modal-body"><div class="notice">本操作不会修改港安顾问、原表顾问、开户或定增数据。每一位客户都会留下独立的负责人变更记录。</div><div class="field"><label>新的骄阳负责人 *</label><select name="ownerId" required ${forcedOwnerId ? "disabled" : ""}>${ownerOptions}</select>${forcedOwnerId ? `<input type="hidden" name="ownerId" value="unassigned">` : ""}</div><div class="field"><label>调整原因 *</label><textarea name="reason" required placeholder="例如：按团队分工接管历史客户"></textarea></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">确认调整 ${selectedIds.length} 位客户</button></div></form></div>`);
  document.querySelector("#bulk-assignment-form").addEventListener("submit", async (event) => { event.preventDefault(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form)); if (forcedOwnerId) values.ownerId = forcedOwnerId; const label = form.querySelector('[name="ownerId"] option:checked')?.textContent || "待分配池"; if (!window.confirm(`确认将 ${selectedIds.length} 位客户调整为“${label}”吗？`)) return; try { const result = await api("/api/customers/bulk-assign", {method:"POST", body:JSON.stringify({customerIds:selectedIds, ...values})}); closeModal(); state.assignmentSelectedCustomerIds = []; toast(`已调整 ${result.assignedCount} 位客户${result.unchangedCount ? `，${result.unchangedCount} 位无需变更` : ""}`); state.meta = await api("/api/meta"); await renderCustomerAssignments(content); } catch (err) { toast(err.message); } });
}

async function renderBatches(content) {
  const data = await api("/api/batches"); const canManage = Boolean(state.user.canManageAssignments);
  content.innerHTML = `<div class="section-heading"><div><h3>定增批次</h3><p>批次通常按一至两个月滚动，客户可先有意向，再锁定具体批次。</p></div>${canManage ? `<button class="primary-btn" id="create-batch">＋ 创建批次</button>` : ""}</div><div class="batch-cards">${data.items.length ? data.items.map((batch) => `<button class="batch-card" data-batch-id="${esc(batch.id)}"><div><span class="eyebrow">定增项目</span>${tag(batch.status, toneFor(batch.status))}</div><h3>${esc(batch.name)}</h3><p>${esc(batch.close_date || "截止日期待定")} · ${batch.customer_count || 0} 位客户</p><div class="batch-metrics"><span><small>意向</small><b>${money(batch.intent_amount)}</b></span><span><small>到账</small><b>${money(batch.funded_amount)}</b></span><span><small>实际参与</small><b>${money(batch.actual_amount)}</b></span></div><div class="batch-progress"><i style="width:${batch.intent_amount ? Math.min(100, batch.funded_amount / batch.intent_amount * 100) : 0}%"></i></div><footer>闭环 ${batch.closed_count || 0} · 流失 ${batch.lost_count || 0}</footer></button>`).join("") : `<section class="section"><div class="empty">还没有批次。主管可以创建第一个定增批次。</div></section>`}</div><section id="batch-customers"></section>`;
  document.querySelector("#create-batch")?.addEventListener("click", openBatchForm); content.querySelectorAll("[data-batch-id]").forEach((card) => card.addEventListener("click", () => loadBatchCustomers(card.dataset.batchId, content.querySelector("#batch-customers"))));
}
async function loadBatchCustomers(batchId, target) { const data = await api(`/api/customers?batchId=${encodeURIComponent(batchId)}`); target.innerHTML = `<div class="section-heading subheading"><div><h3>批次客户</h3><p>本批次共 ${data.total} 位客户</p></div></div><section class="section"><div class="table-wrap">${customerTable(data.items)}</div></section>`; target.querySelectorAll("[data-open-customer]").forEach((node) => node.addEventListener("click", () => openDetail(node.dataset.openCustomer))); target.scrollIntoView({behavior:"smooth", block:"start"}); }
function openBatchForm() { openModal(`<div class="modal"><div class="modal-header"><h3>创建定增批次</h3><button class="close-btn" data-close>×</button></div><form id="batch-form"><div class="modal-body"><div class="field"><label>批次名称 *</label><input name="name" required placeholder="例如：2026 年 9 月批次"></div><div class="form-grid"><div class="field"><label>截止日期</label><input name="closeDate" type="date"></div><div class="field"><label>批次状态</label><select name="status">${state.meta.batchStatuses.map((v) => `<option>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>目标金额 (USD)</label><input name="targetAmount" type="number" min="0" inputmode="decimal"></div></div><div class="field"><label>批次备注</label><textarea name="notes"></textarea></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">创建批次</button></div></form></div>`); document.querySelector("#batch-form").addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); values.targetAmount = Number(values.targetAmount || 0); values.closeDate = values.closeDate || null; try { await api("/api/batches", {method:"POST", body: JSON.stringify(values)}); closeModal(); state.meta = await api("/api/meta"); toast("批次已创建"); navigate("placement"); } catch (err) { toast(err.message); } }); }
function gridFieldControl(row, field) { const value = row.custom_values?.[field.id] || ""; const attrs = `data-grid-field="${esc(field.id)}" data-customer="${esc(row.id)}" data-version="${row.version}" data-saved="${esc(value)}"`; if (field.fieldType === "select") return `<select class="grid-input" ${attrs}><option value="">未填写</option>${field.options.map((option) => `<option ${value === option ? "selected" : ""}>${esc(option)}</option>`).join("")}</select>`; return `<input class="grid-input" ${attrs} type="${field.fieldType === "number" ? "number" : field.fieldType === "date" ? "date" : "text"}" value="${esc(value)}">`; }
function coreGridSelect(row, key, values, current) { return `<select class="grid-input status-input" data-grid-core="${key}" data-customer="${esc(row.id)}" data-version="${row.version}" data-saved="${esc(current)}">${values.map((value) => `<option ${value === current ? "selected" : ""}>${esc(value)}</option>`).join("")}</select>`; }
function coreGridText(row, key, current, placeholder) { return `<input class="grid-input relation-input" data-grid-core="${key}" data-customer="${esc(row.id)}" data-version="${row.version}" data-saved="${esc(current)}" value="${esc(current)}" placeholder="${esc(placeholder)}">`; }
function customerTable(rows, editable = false) {
  const fields = state.meta.customerFields || [];
  const canManageHonganAdvisor = editable && state.user.canManageAdvisorBindings;
  return rows.length ? `<table class="customer-grid"><thead><tr><th class="sticky-col">客户</th><th>TW唯一编号</th><th>微信昵称</th><th>联系方式</th><th>港安顾问<br><small>外部引荐</small></th><th>开户状态</th><th>定增推进</th><th>当前骄阳负责人</th>${fields.map((field) => `<th>${esc(field.label)}</th>`).join("")}<th>最后更新</th></tr></thead><tbody>${rows.map((row) => {
    const displayName = customerDisplayName(row);
    const nickname = String(row.wechat_nickname || "").trim();
    const twCode = String(row.tw_code || "").trim();
    const honganAdvisor = String(row.hongan_advisor || "").trim();
    const collaborators = (row.collaborators || []).map((person) => person.name).filter(Boolean).join("、");
    return `<tr><td class="sticky-col">${displayName ? `<button class="link-btn" data-open-customer="${esc(row.id)}">${esc(displayName)}</button>` : ""}</td><td class="tw-code-cell">${esc(twCode || "-")}</td><td class="customer-nickname">${nickname ? `<button class="link-btn" data-open-customer="${esc(row.id)}">${esc(nickname)}</button>` : "-"}</td><td>${esc(row.phone || "-")}${row.email ? `<div class="hint">${esc(row.email)}</div>` : ""}</td><td class="hongan-advisor-cell">${canManageHonganAdvisor ? coreGridText(row,"hkAdvisor",honganAdvisor,"未填写") : esc(honganAdvisor || "-")}</td><td>${editable ? coreGridSelect(row,"accountStatus",state.meta.accountStatuses,row.account_status) : tag(row.account_status,toneFor(row.account_status))}</td><td>${editable ? coreGridSelect(row,"placementStatus",state.meta.placementStatuses,row.placement_status) : tag(row.placement_status,toneFor(row.placement_status))}</td><td>${esc(row.owner_name)}<div class="hint">${esc(row.owner_team)}${collaborators ? `<br>协同负责人：${esc(collaborators)}` : ""}${row.source_advisor_label ? `<br>原表骄阳顾问：${esc(row.source_advisor_label)}` : ""}</div></td>${fields.map((field) => `<td>${editable ? gridFieldControl(row, field) : esc(row.custom_values?.[field.id] || "-")}</td>`).join("")}<td class="hint">${fmt(row.updated_at)}</td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty">没有匹配的客户记录。点击“新增客户”添加第一位客户。</div>`;
}
function setupCustomerFloatingScrollbar(root) {
  const wrap = root.querySelector(".table-section .table-wrap");
  if (!wrap || !wrap.querySelector(".customer-grid")) return;
  const bar = document.createElement("div");
  bar.className = "customer-scrollbar-floating";
  bar.setAttribute("aria-label", "客户数据表横向滚动");
  bar.innerHTML = "<div></div>";
  root.appendChild(bar);
  const track = bar.firstElementChild;
  let syncing = false;
  const syncWidth = () => {
    const overflow = wrap.scrollWidth > wrap.clientWidth + 1;
    bar.hidden = !overflow;
    track.style.width = `${wrap.scrollWidth}px`;
    if (!syncing) bar.scrollLeft = wrap.scrollLeft;
  };
  wrap.addEventListener("scroll", () => {
    if (syncing) return;
    syncing = true;
    bar.scrollLeft = wrap.scrollLeft;
    syncing = false;
  }, { passive: true });
  bar.addEventListener("scroll", () => {
    if (syncing) return;
    syncing = true;
    wrap.scrollLeft = bar.scrollLeft;
    syncing = false;
  }, { passive: true });
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(syncWidth);
    observer.observe(wrap);
    if (wrap.firstElementChild) observer.observe(wrap.firstElementChild);
  }
  syncWidth();
}
async function saveGridCell(input) { const previous = input.dataset.saved ?? ""; input.disabled = true; try { const result = await api(`/api/customers/${encodeURIComponent(input.dataset.customer)}/custom-fields/${encodeURIComponent(input.dataset.gridField)}`, {method:"PUT", body:JSON.stringify({value:input.value,version:Number(input.dataset.version)})}); document.querySelectorAll(`[data-customer="${CSS.escape(input.dataset.customer)}"]`).forEach((control) => control.dataset.version = result.version); input.dataset.saved = input.value; input.classList.add("saved"); setTimeout(() => input.classList.remove("saved"), 900); } catch (err) { input.value = previous; toast(err.message); } finally { input.disabled = false; } }
async function saveCoreGridCell(input) { const previous = input.dataset.saved || ""; input.disabled = true; try { const body = {[input.dataset.gridCore]:input.value,version:Number(input.dataset.version)}; if (input.dataset.gridCore === "hkAdvisor" && input.value !== previous) { if (!window.confirm(`确定将港安顾问从“${previous || "未填写"}”修改为“${input.value || "未填写"}”吗？`)) { input.value = previous; return; } const reason = window.prompt("请输入港安顾问变更原因"); if (!reason?.trim()) { input.value = previous; toast("已取消修改：必须填写变更原因"); return; } body.changeReason = reason.trim(); } const result = await api(`/api/customers/${encodeURIComponent(input.dataset.customer)}`, {method:"PATCH",body:JSON.stringify(body)}); document.querySelectorAll(`[data-customer="${CSS.escape(input.dataset.customer)}"]`).forEach((control) => control.dataset.version = result.customer.version); input.dataset.saved = input.value; if (input.dataset.gridCore === "hkAdvisor") state.meta = await api("/api/meta"); input.classList.add("saved"); setTimeout(() => input.classList.remove("saved"),900); } catch (err) { input.value = previous; toast(err.message); } finally { input.disabled = false; } }

async function renderFollowups(content) { const [data, customers] = await Promise.all([api("/api/followups?limit=120"), api("/api/customers?pageSize=100")]); state.customers = customers.items; const now = new Date(); const today = now.toISOString().slice(0, 10); const scheduled = data.items.filter((row) => row.next_followup_at); const todayDue = scheduled.filter((row) => String(row.next_followup_at).slice(0, 10) === today).length; const undated = data.items.filter((row) => !row.next_followup_at).length; content.innerHTML = `<div class="section-heading"><div><div class="eyebrow">FOLLOW-UP WORKSPACE</div><h3>跟进工作台</h3><p>每天的沟通内容和下一步动作都沉淀到客户时间线里。</p></div><button class="primary-btn" id="follow-add">＋ 写今日跟进</button></div><div class="followup-summary"><article><span>今日需跟进</span><b>${todayDue}</b><small>已设定今日下次跟进时间</small></article><article><span>已安排后续</span><b>${scheduled.length}</b><small>等待继续推进的跟进事项</small></article><article><span>待补下一步</span><b>${undated}</b><small>建议补充下次动作和时间</small></article></div><section class="section"><div class="section-header"><div><h3>沟通记录</h3><span class="hint">最近 ${data.items.length} 条记录</span></div><span class="hint">点击客户查看完整时间线</span></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>客户</th><th>方式</th><th>跟进内容</th><th>跟进后阶段</th><th>下一次动作</th></tr></thead><tbody>${data.items.length ? data.items.map((row) => `<tr data-customer-id="${esc(row.customer_id)}"><td class="hint">${fmt(row.created_at)}</td><td><button class="link-btn">${esc(row.customer_name)}</button><div class="hint">${esc(row.owner_name)}</div></td><td>${tag(row.method, "gray")}</td><td class="wrap-cell">${esc(row.content)}</td><td>${tag(row.stage_after, toneFor(row.stage_after))}</td><td class="hint">${fmt(row.next_followup_at)}</td></tr>`).join("") : `<tr><td colspan="6"><div class="empty">还没有跟进记录。</div></td></tr>`}</tbody></table></div></section>`; document.querySelector("#follow-add").addEventListener("click", () => openQuickFollowForm()); content.querySelectorAll("[data-customer-id]").forEach((node) => node.addEventListener("click", () => openDetail(node.dataset.customerId))); }

function renderImports(content) { const canManageImportHistory = Boolean(state.user.canManageCrmPermissions); content.innerHTML = `<div class="section-heading entry-heading"><div><div class="eyebrow">DAILY CUSTOMER INTAKE</div><h3>录入中心</h3><p>日常新增直接进入统一客户池；历史数据迁移使用批量导入。</p></div><span class="entry-status"><i></i>数据实时同步</span></div><div class="entry-workspace"><section class="entry-primary"><div class="entry-primary-copy"><span class="entry-overline">日常工作</span><h4>先记住客户，再逐步完善进度</h4><p>姓名、微信昵称、手机号和当前情况即可新建。开户、定增和自定义字段可在后续跟进中继续补充。</p></div><button class="entry-cta" id="single-entry"><span>＋</span>新增一位客户</button></section><div class="entry-cards"><button class="entry-card" id="bulk-entry" ${state.user.canImportCustomers ? "" : "disabled"}><span>⇧</span><strong>批量导入历史数据</strong><small>${state.user.canImportCustomers ? "Excel / CSV 先预览列映射，再确认并入客户表。" : "此账号尚未获得批量导入权限，请联系管理员开通。"}</small><b>${state.user.canImportCustomers ? "打开导入工具 →" : "暂未开通"}</b></button><article class="entry-card entry-note"><span>◎</span><strong>录入后的处理方式</strong><small>重复客户不会自动合并；系统会提示冲突，由具备客户归属权限的成员确认处理。</small></article></div></div><section class="section import-workspace" id="import-workspace"><div class="section-header"><div><h3>历史数据迁移</h3><span class="hint">仅在需要导入 Excel 或 CSV 时展开。</span></div>${state.user.canImportCustomers ? `<div class="heading-actions"><button class="secondary-btn" id="download-import-template">下载导入模板</button><button class="secondary-btn" id="open-import">选择表格</button></div>` : ""}</div><div class="empty">日常客户请使用上方的“新增一位客户”。</div></section>${canManageImportHistory ? `<section class="section import-history" id="import-history"><div class="empty">正在读取导入记录...</div></section>` : ""}`; document.querySelector("#single-entry").addEventListener("click", () => openQuickCustomerForm()); document.querySelector("#bulk-entry")?.addEventListener("click", () => renderBulkImport()); document.querySelector("#open-import")?.addEventListener("click", () => renderBulkImport()); document.querySelector("#download-import-template")?.addEventListener("click", downloadImportTemplate); if (canManageImportHistory) renderImportHistory(); }
function renderBulkImport() { const workspace = document.querySelector("#import-workspace"); workspace.innerHTML = `<div class="section-header"><div><h3>批量导入历史数据</h3><span class="hint">支持 .xlsx / .csv · 单次最多 5000 行</span></div><button class="secondary-btn" id="download-import-template">下载导入模板</button></div><div class="section-body"><div class="dropzone"><strong>选择客户表格</strong><span class="hint">系统会先识别列并展示预览，不会自动合并重复客户</span><label class="primary-btn" style="display:inline-block;margin-top:16px"><input type="file" id="import-file" accept=".xlsx,.csv">选择文件</label></div><div id="bulk-preview"></div></div>`; document.querySelector("#import-file").addEventListener("change", handleImportFile); document.querySelector("#download-import-template")?.addEventListener("click", downloadImportTemplate); }
async function downloadImportTemplate() { try { const response = await fetch("/api/imports/template.csv", {headers: state.token ? {Authorization: `Bearer ${state.token}`} : {}}); if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail || "模板下载失败"); } const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "骄阳客户导入模板.csv"; document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast("导入模板已下载"); } catch (err) { toast(err.message); } }
async function renderImportHistory() { const workspace = document.querySelector("#import-history"); if (!workspace) return; try { const data = await api("/api/imports"); const totalBatches = data.items.length; const visibleItems = state.importHistoryExpanded ? data.items : data.items.slice(0, 4); const hiddenCount = totalBatches - visibleItems.length; const groups = {}; visibleItems.forEach((item) => { const key = String(item.created_at || "").slice(0, 7) || "未标日期"; (groups[key] ||= []).push(item); }); const months = Object.entries(groups).sort(([a], [b]) => b.localeCompare(a)); const monthLabel = (key) => { const [year, month] = key.split("-"); return year && month ? `${year} 年 ${Number(month)} 月` : key; }; workspace.innerHTML = `<div class="section-header"><div><h3>导入批次</h3><span class="hint">每次上传都会保留为一个批次；点击指标可下钻查看该批新增、更新或新开户客户。</span></div><span class="hint">显示 ${visibleItems.length} / 共 ${totalBatches} 批</span></div>${months.length ? months.map(([month, items]) => `<section class="import-month"><header><h4>${monthLabel(month)}</h4><span>${items.length} 批导入</span></header><div class="import-batch-list">${items.map((item) => { const changed = Number(item.created_count || 0) + Number(item.updated_count || 0); const mode = item.dataQuality?.mode === "snapshot" ? "全量快照" : item.dataQuality?.mode === "holding_pinyin" ? "中阳拼音持仓" : item.dataQuality?.mode === "hongan_activity" ? "港安活动分表" : "增量 / 手工"; const status = item.rolled_back_at ? "已撤回" : changed ? "已完成" : "无变化"; const rollbackLabel = item.dataQuality?.mode === "hongan_activity" ? "恢复本批变更" : "撤回"; const reviewLink = item.pendingReviewCount ? `<button class="import-review-link" data-open-import-reviews="${esc(item.id)}">待复核 ${item.pendingReviewCount} 条</button>` : ""; return `<article class="import-batch-card ${item.rolled_back_at ? "is-rolled-back" : ""}"><div class="import-batch-head"><div><span class="import-batch-date">${fmt(item.created_at)}</span><h4>${esc(item.filename)}</h4><p>${esc(item.imported_by_name)} · ${mode} · ${item.total_rows} 行</p></div>${tag(status, item.rolled_back_at ? "gray" : changed ? "teal" : "gray")}</div><div class="import-batch-metrics"><button data-import-filter="${esc(item.id)}" data-import-mode="created"><small>本批新增</small><b>${item.created_count || 0}</b></button><button data-import-filter="${esc(item.id)}" data-import-mode="updated"><small>本批更新</small><b>${item.updated_count || 0}</b></button><button data-import-filter="${esc(item.id)}" data-import-mode="opened"><small>本批新开户</small><b>${item.openedCount || 0}</b></button><div><small>冲突 / 错误</small><b>${item.conflict_count || 0} / ${item.error_count || 0}</b></div></div><footer>${reviewLink}<button class="import-batch-link" data-import-filter="${esc(item.id)}" data-import-mode="all">查看本批变更 →</button>${!item.rolled_back_at && (item.created_count || item.dataQuality?.mode === "hongan_activity") ? `<button class="secondary-btn" data-rollback-import="${esc(item.id)}" data-rollback-profile="${esc(item.dataQuality?.mode || "")}">${rollbackLabel}</button>` : ""}</footer></article>`; }).join("")}</div></section>`).join("") : `<div class="empty">暂无批量导入记录</div>`}`; if (hiddenCount || (state.importHistoryExpanded && totalBatches > 4)) workspace.insertAdjacentHTML("beforeend", `<button class="secondary-btn import-history-toggle" id="toggle-import-history" type="button">${state.importHistoryExpanded ? "收起较早批次" : `查看全部 ${totalBatches} 批`}</button>`); workspace.querySelector("#toggle-import-history")?.addEventListener("click", () => { state.importHistoryExpanded = !state.importHistoryExpanded; renderImportHistory(); }); workspace.querySelectorAll("[data-open-import-reviews]").forEach((button) => button.addEventListener("click", () => navigate("reviews"))); workspace.querySelectorAll("[data-import-filter]").forEach((button) => button.addEventListener("click", () => { state.importJobId = button.dataset.importFilter; state.importJobMode = button.dataset.importMode || "all"; state.customerPage = 1; navigate("customers", {preserveWorkflow:true}); })); workspace.querySelectorAll("[data-rollback-import]").forEach((button) => button.addEventListener("click", () => rollbackImport(button.dataset.rollbackImport, button.dataset.rollbackProfile === "hongan_activity"))); } catch (err) { workspace.innerHTML = `<div class="empty">${esc(err.message)}</div>`; } }
async function rollbackImport(jobId, isHonganActivity = false) { const prompt = isHonganActivity ? "这批是港安活动分表。确认只恢复这批导入造成的错误骄阳负责人分配吗？港安顾问信息不会被清除，导入后人工修改过的负责人会保留。" : "确认撤回这次导入造成的变更吗？系统会保护导入后已被再次编辑的客户。"; if (!window.confirm(prompt)) return; try { const data = await api(`/api/imports/${encodeURIComponent(jobId)}/rollback`, {method:"POST"}); const message = isHonganActivity ? `已恢复 ${data.restored?.length || 0} 条负责人${data.protected?.length ? `，保护 ${data.protected.length} 条` : ""}` : data.protected.length ? `已撤回 ${data.archived.length} 条，保护 ${data.protected.length} 条` : `已撤回 ${data.archived.length} 条`; toast(message); renderImportHistory(); } catch (err) { toast(err.message); } }
async function handleImportFile(event) { const file = event.target.files[0]; if (!file) return; const workspace = document.querySelector("#bulk-preview"); workspace.innerHTML = `<div class="empty">正在读取 ${esc(file.name)}...</div>`; const buffer = await file.arrayBuffer(); let binary = ""; const bytes = new Uint8Array(buffer); for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]); try { const preview = await api("/api/imports/preview", {method:"POST", body: JSON.stringify({filename:file.name, dataBase64:btoa(binary)})}); state.importPreview = {file, preview}; renderImportPreview(workspace); renderImportDiagnostics(workspace, preview); } catch (err) { workspace.innerHTML = `<div class="result-box error-text">${esc(err.message)}</div>`; } }

function renderImportDiagnostics(workspace, preview) {
  if (preview.importProfile === "holding_pinyin") return;
  if (preview.importProfile === "hongan_activity") {
    const warningRows = (preview.warnings || []).map((warning) => `<li>${esc(warning.message)}${warning.count != null ? ` <b>${warning.count}</b>` : ""}</li>`).join("");
    if (warningRows) workspace.querySelector(".hongan-import-banner")?.insertAdjacentHTML("beforeend", `<ul class="hongan-import-warnings">${warningRows}</ul>`);
    return;
  }
  const extraMappings = {accountBroker:"开户券商", accountOpenedAt:"开户/注册日期", brokerDepositAmount:"港券入金金额", capitalDestination:"资金流向", hkAdvisor:"港安顾问", sourceAdvisorLabel:"历史骄阳顾问"};
  const warnings = preview.warnings || [];
  const warningRows = warnings.map((warning) => `<li>${esc(warning.message)}${warning.count != null ? ` <b>${warning.count}</b>` : ""}${warning.labels?.length ? `<small>${warning.labels.slice(0, 8).map(esc).join("、")}${warning.labels.length > 8 ? " 等" : ""}</small>` : ""}</li>`).join("");
  const selectedMappings = Object.entries(extraMappings).filter(([key]) => preview.suggestedMapping?.[key] || preview.profile === "hongan_master");
  const mappingSection = selectedMappings.length ? `<details class="mapping-details import-diagnostics-mapping" open><summary>港安总表补充映射</summary><div class="mapping-grid">${selectedMappings.map(([key, label]) => mappingControl(key, label, preview.suggestedMapping?.[key])).join("")}</div></details>` : "";
  const profile = preview.profile === "hongan_master" ? `<span class="import-profile">港安客户总表识别模式</span>` : preview.importProfile === "hongan_activity" ? `<span class="import-profile">港安活动分表模式</span>` : preview.importProfile === "asset" ? `<span class="import-profile">券商资产更新模式</span>` : preview.importProfile === "holding" ? `<span class="import-profile">持仓快照更新模式</span>` : preview.importProfile === "holding_pinyin" ? `<span class="import-profile">中阳拼音持仓模式</span>` : "";
  const ownerChoices = state.meta.ownerChoices || state.meta.owners || [];
  const ownerSelection = state.user.canManageAssignments && !["hongan_activity", "holding_pinyin"].includes(preview.importProfile) ? `<div class="import-owner-select"><label>未匹配顾问的处理方式</label><select id="import-default-owner">${ownerChoices.map((owner) => `<option value="${esc(owner.id)}" ${owner.id === "unassigned" ? "selected" : ""}>${esc(owner.name)}${owner.team ? ` · ${esc(owner.team)}` : ""}</option>`).join("")}</select><small>原表顾问会单独保留；未匹配到系统账号时，建议先进入待分配池。</small></div>` : "";
  const aliasOptions = (state.meta.collaboratorUsers || []).map((person) => `<option value="${esc(person.id)}">${esc(person.name)} · ${esc(person.roleLabel)} · ${esc(person.team)}</option>`).join("");
  const aliasMapping = state.user.canManageAssignments && preview.unresolvedAdvisorAliases?.length ? `<details class="mapping-details advisor-alias-mapping" open><summary>历史顾问账号映射</summary><div class="hint">映射会保存，下次导入遇到同一历史姓名时自动复用。</div><div class="advisor-alias-list">${preview.unresolvedAdvisorAliases.map((alias) => `<label><span>${esc(alias)}</span><select data-advisor-alias="${esc(alias)}"><option value="">暂不映射</option>${aliasOptions}</select></label>`).join("")}</div></details>` : "";
  const quality = preview.dataQuality || {};
  const qualitySummary = `<div class="import-quality"><span>可识别客户 ${Math.max(0, Number(preview.totalRows || 0) - Number(quality.missingDisplayNameRows || 0))} 条</span><span>仅有微信昵称 ${Number(quality.nicknameFallbackRows || 0)} 条</span><span>${["asset", "holding"].includes(preview.importProfile) ? "无手机号/邮箱" : "待补联系方式"} ${Number(quality.unidentifiedRows || 0)} 条</span></div>`;
  const unidentifiedConsent = quality.unidentifiedRows && !["asset", "holding"].includes(preview.importProfile) ? `<label class="import-consent"><input type="checkbox" id="allow-unidentified-rows"><span>我确认这批历史记录可能没有手机号或邮箱，允许导入并在系统中继续补充。</span></label>` : "";
  const snapshotMode = preview.dataQuality?.hasTwSnapshot ? `<p class="hint import-snapshot-note">${preview.importProfile === "asset" ? "已识别 TW 编号：确认后按 TW 更新券商账户资产，不会新建客户。" : preview.importProfile === "holding" ? "已识别 TW 编号：确认后按 TW 写入持仓快照，不会新建客户。" : "已识别 TW 编号：确认后按券商全量快照合并，已有客户只更新券商状态，新 TW 才新增。"}</p>` : "";
  const diagnostics = `<section class="import-diagnostics">${profile}${qualitySummary}${snapshotMode}${ownerSelection}${warningRows ? `<ul>${warningRows}</ul>` : `<p class="hint">未发现需要人工处理的占位值或异常列。</p>`}${unidentifiedConsent}${preview.holdingSnapshots?.length ? `<p class="hint">将自动写入 ${preview.holdingSnapshots.map((item) => esc(item.snapshotDate)).join("、")} 的持仓快照。</p>` : ""}${aliasMapping}${mappingSection}</section>`;
  workspace.querySelector(".import-actions")?.insertAdjacentHTML("beforebegin", diagnostics);
}
function mappingControl(key, label, selected, custom = false) { const {headers} = state.importPreview.preview; return `<div class="mapping-row"><strong>${esc(label)}</strong><select ${custom ? "data-custom-map" : "data-map"}="${esc(key)}"><option value="">不导入</option>${headers.map((header) => `<option value="${esc(header)}" ${selected === header ? "selected" : ""}>${esc(header)}</option>`).join("")}</select></div>`; }
function renderHonganActivityPreview(workspace, preview) {
  const activity = preview.honganActivity || {};
  const counts = activity.counts || {};
  const stat = (label, value, tone = "") => `<div class="hongan-import-stat ${tone}"><b>${Number(value || 0)}</b><span>${label}</span></div>`;
  const sample = (items, label) => items?.length ? `<details class="hongan-import-list"><summary>${label}（${items.length}${items.length >= 200 ? "+" : ""}）</summary><div>${items.slice(0, 8).map((item) => `<span>${esc(item.name)}${item.targetAdvisor ? ` · ${esc(item.targetAdvisor)}` : item.advisors?.length ? ` · ${esc(item.advisors.join("、"))}` : ""}${item.sourceAdvisors?.length ? ` · 现场开户人：${esc(item.sourceAdvisors.join("、"))}` : ""}</span>`).join("")}</div></details>` : "";
  workspace.innerHTML = `<div class="result-box hongan-import-banner"><strong>已识别港安活动分表</strong><div class="hint">${esc(preview.sheetName || "")}${preview.sheetStats?.length ? ` · ${preview.sheetStats.map((item) => `${esc(item.name)}（${item.rows}）`).join("、")}` : ""}</div><div class="hint import-normalization-note">${esc(preview.textNormalization || "繁体中文已统一转换为简体中文")}</div></div><section class="hongan-import-summary"><div class="hongan-import-stats">${stat("活动记录", activity.totalRows)}${stat("唯一客户", activity.uniqueNames)}${stat("可自动补全", counts.autoFill, "positive")}${stat("已有一致顾问", counts.unchanged)}${stat("需人工复核", (counts.conflicts || 0) + (counts.ambiguous || 0), "warning")}${stat("系统未找到", counts.unmatched, "muted")}</div>${sample(activity.conflicts, "顾问冲突")}${sample(activity.ambiguous, "同名多条记录")}${sample(activity.unmatched, "未匹配客户")}</section><div class="toolbar import-actions"><label class="import-consent hongan-import-consent"><input type="checkbox" id="confirm-hongan-activity"><span>我确认这次只补全唯一匹配且当前为空的港安顾问，不覆盖已有港安顾问、不修改骄阳负责人、不新建客户。</span></label><button class="primary-btn" id="commit-import" ${preview.truncated || !state.user.canManageAdvisorBindings ? "disabled" : ""}>确认补全港安顾问</button></div><div class="table-wrap import-sample"><table><thead><tr>${preview.headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr></thead><tbody>${preview.rows.slice(0, 5).map((row) => `<tr>${preview.headers.map((header) => `<td>${esc(row[header])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const checkbox = workspace.querySelector("#confirm-hongan-activity");
  const button = workspace.querySelector("#commit-import");
  checkbox?.addEventListener("change", () => { button.disabled = !checkbox.checked || preview.truncated || !state.user.canManageAdvisorBindings; });
  if (!state.user.canManageAdvisorBindings) button.title = "需要顾问绑定管理权限";
  button?.addEventListener("click", commitImport);
}
function renderPinyinHoldingPreview(workspace, preview) {
  const holding = preview.pinyinHolding || {};
  const counts = holding.counts || {};
  const stat = (label, value, tone = "") => `<div class="hongan-import-stat ${tone}"><b>${Number(value || 0)}</b><span>${label}</span></div>`;
  const sample = (items, title, render) => items?.length ? `<details class="hongan-import-list"><summary>${title}（${items.length}${items.length >= 200 ? "+" : ""}）</summary><div>${items.slice(0, 12).map(render).join("")}</div></details>` : "";
  const ambiguous = sample(holding.ambiguous, "需确认的同音客户", (item) => `<span>${esc(item.pinyinName)} · ${Number(item.quantity || 0).toLocaleString()} 股 · ${esc((item.candidates || []).map((candidate) => `${candidate.customerName}（${candidate.twCode}）`).join(" / "))}</span>`);
  const unmatched = sample(holding.unmatched, "暂未匹配", (item) => `<span>${esc(item.pinyinName)} · ${Number(item.quantity || 0).toLocaleString()} 股</span>`);
  const date = preview.holdingSnapshots?.[0]?.snapshotDate || "";
  workspace.innerHTML = `<div class="result-box hongan-import-banner"><strong>已识别中阳证券拼音持仓表</strong><div class="hint">${esc(preview.sheetName || "")} · 将写入 ${esc(date)} 的持仓快照</div><div class="hint import-normalization-note">${esc(preview.textNormalization || "拼音仅用于匹配，不会替换客户姓名")}</div></div><section class="hongan-import-summary"><div class="hongan-import-stats">${stat("持仓记录", holding.totalRows)}${stat("唯一匹配", counts.matched, "positive")}${stat("同音待确认", counts.ambiguous, "warning")}${stat("暂未匹配", counts.unmatched, "muted")}</div><p class="hint">确认后只写入唯一匹配的 ${Number(counts.matched || 0)} 条。其余 ${Number(counts.ambiguous || 0) + Number(counts.unmatched || 0)} 条不会写入，也不会新建客户或修改负责人。</p>${ambiguous}${unmatched}</section><div class="toolbar import-actions"><label class="import-consent hongan-import-consent"><input type="checkbox" id="confirm-pinyin-holding"><span>我确认只导入唯一匹配的持仓记录；同音、重名和未匹配客户暂不写入。</span></label><button class="primary-btn" id="commit-import" ${preview.truncated ? "disabled" : ""}>确认写入持仓快照</button></div><div class="table-wrap import-sample"><table><thead><tr>${preview.headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr></thead><tbody>${preview.rows.slice(0, 5).map((row) => `<tr>${preview.headers.map((header) => `<td>${esc(row[header])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const checkbox = workspace.querySelector("#confirm-pinyin-holding");
  const button = workspace.querySelector("#commit-import");
  if (button) button.disabled = true;
  checkbox?.addEventListener("change", () => { if (button) button.disabled = !checkbox.checked || preview.truncated; });
  button?.addEventListener("click", commitImport);
}
function renderImportPreview(workspace) { const {preview} = state.importPreview; if (preview.importProfile === "hongan_activity") { renderHonganActivityPreview(workspace, preview); return; } if (preview.importProfile === "holding_pinyin") { renderPinyinHoldingPreview(workspace, preview); return; } const core = {name:"客户姓名",wechatNickname:"微信昵称",phone:"手机号",source:"来源",accountStatus:"开户状态",twCode:"客户唯一编号（TW）",placementStatus:"定增推进",notes:"备注"}; const advanced = {email:"邮箱",company:"公司",stage:"生命周期",intentStatus:"定增意向",intentAmount:"意向金额",fundedAmount:"到账金额",actualAmount:"实际参与金额",lostReason:"流失原因"}; const custom = preview.customerFields || []; workspace.innerHTML = `<div class="result-box"><strong>已读取：${esc(preview.sheetName || "默认工作表")}</strong><div class="hint">识别到 ${preview.totalRows} 行。姓名和微信昵称至少填写一项，其余字段可以后补。</div><div class="hint import-normalization-note">${esc(preview.textNormalization || "中文文本将统一转换为简体中文")}</div></div><div class="mapping-section"><h4>核心信息</h4><div class="mapping-grid">${Object.entries(core).map(([key,label]) => mappingControl(key,label,preview.suggestedMapping[key])).join("")}</div></div>${custom.length ? `<div class="mapping-section"><h4>自定义表头</h4><div class="mapping-grid">${custom.map((field) => mappingControl(field.id,field.label,preview.suggestedCustomMapping?.[field.id],true)).join("")}</div></div>` : ""}<details class="mapping-details"><summary>其他业务字段</summary><div class="mapping-grid">${Object.entries(advanced).map(([key,label]) => mappingControl(key,label,preview.suggestedMapping[key])).join("")}</div></details><div class="toolbar import-actions"><span class="hint">${preview.truncated ? "超过 5000 行，请拆分后再导入" : preview.dataQuality?.hasTwSnapshot ? "已识别 TW：确认后将合并周快照，不会重复新增客户" : "导入前不会自动合并重名或重复客户"}</span><button class="primary-btn" id="commit-import" ${preview.truncated ? "disabled" : ""}>确认导入</button></div><div class="table-wrap import-sample"><table><thead><tr>${preview.headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr></thead><tbody>${preview.rows.slice(0, 5).map((row) => `<tr>${preview.headers.map((header) => `<td>${esc(row[header])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; workspace.querySelector("#commit-import")?.addEventListener("click", commitImport); }
async function commitImport() {
  const {file, preview} = state.importPreview;
  const mapping = Object.fromEntries([...document.querySelectorAll("[data-map]")].map((select) => [select.dataset.map, select.value]));
  const customMapping = Object.fromEntries([...document.querySelectorAll("[data-custom-map]")].map((select) => [select.dataset.customMap, select.value]));
  const advisorAliasMappings = Object.fromEntries([...document.querySelectorAll("[data-advisor-alias]")].filter((select) => select.value).map((select) => [select.dataset.advisorAlias, select.value]));
  const numeric = (value) => Number(String(value ?? "").replaceAll(",", "")) || 0;
  const rows = preview.importProfile === "hongan_activity" ? (preview.activityRows || []) : preview.rows.map((raw) => {
    const holdingSnapshots = (preview.holdingSnapshots || []).map((snapshot) => ({
      snapshotDate: snapshot.snapshotDate, securityName: snapshot.securityName, sourceLabel: snapshot.sourceLabel,
      quantity: numeric(raw[snapshot.quantityHeader]), marketValue: numeric(raw[snapshot.marketValueHeader]),
    })).filter((snapshot) => snapshot.quantity || snapshot.marketValue);
    return {
      ...(preview.importProfile === "holding_pinyin"
        ? {name: raw[preview.suggestedMapping?.name], holdingQuantity: raw[preview.suggestedMapping?.holdingQuantity]}
        : Object.fromEntries(Object.entries(mapping).filter(([, header]) => header).map(([field, header]) => [field, raw[header]]))),
      customValues: Object.fromEntries(Object.entries(customMapping).filter(([, header]) => header).map(([field, header]) => [field, raw[header]])),
      holdingSnapshots,
    };
  });
  const ownerId = state.user.canManageAssignments ? document.querySelector("#import-default-owner")?.value : state.user.id;
  const allowUnidentifiedRows = Boolean(document.querySelector("#allow-unidentified-rows")?.checked);
  const importProfile = preview.importProfile || preview.profile || "standard";
  try {
    const data = await api("/api/imports/commit", {method:"POST", body: JSON.stringify({filename:file.name, ownerId, mode: importProfile === "standard" && preview.dataQuality?.hasTwSnapshot ? "snapshot" : "append", importProfile, advisorAliasMappings, allowUnidentifiedRows, confirmHonganActivity: Boolean(document.querySelector("#confirm-hongan-activity")?.checked), confirmPinyinHolding: Boolean(document.querySelector("#confirm-pinyin-holding")?.checked), rows})});
    const potentialCount = data.created.reduce((total, item) => total + (item.potentialDuplicates?.length || 0), 0);
    document.querySelector("#bulk-preview").insertAdjacentHTML("afterbegin", `<div class="result-box"><strong>导入完成</strong><div class="hint">新增 ${data.created.length} 条 · 更新 ${data.updated?.length || 0} 条 · 无变化 ${data.unchangedCount || 0} 条 · 冲突 ${data.conflicts.length} 条 · 错误 ${data.errors.length} 条${data.assignedCount ? ` · 已分配骄阳负责人 ${data.assignedCount} 条` : ""}${data.dataQuality?.unidentifiedRowsImported ? ` · 无联系方式 ${data.dataQuality.unidentifiedRowsImported} 条` : ""}${data.dataQuality?.duplicateTwRowsMerged ? ` · 重复 TW 已合并 ${data.dataQuality.duplicateTwRowsMerged} 行` : ""}${potentialCount ? ` · 发现 ${potentialCount} 条可能重名记录，请人工确认` : ""}</div></div>`);
    toast("数据已并入客户数据表");
    if (state.user.canManageCrmPermissions || state.user.canImportCustomers) renderImportHistory();
  } catch (err) {
    toast(err.message);
  }
}

async function renderFields(content) { const data = await api("/api/customer-fields?includeInactive=true"); content.innerHTML = `<div class="section-heading"><div><h3>客户表头管理</h3><p>新增字段会立即成为客户数据表的一列；停用只隐藏列，已有数据不会删除。</p></div><button class="primary-btn" id="add-field">＋ 新增表头</button></div><div class="notice">姓名、手机号、顾问归属、开户状态和定增推进是系统核心字段，不能删除。这里管理的是各阶段需要的补充数据。</div><section class="section"><div class="section-header"><h3>自定义表头</h3><span class="hint">${data.items.filter((item) => item.active).length} 个启用 · ${data.items.filter((item) => !item.active).length} 个停用</span></div><div class="field-list">${data.items.length ? data.items.map((field) => `<div class="field-row ${field.active ? "" : "inactive"}"><span class="field-grip">⋮⋮</span><div><strong>${esc(field.label)}</strong><small>${{text:"文本",number:"数字",date:"日期",select:"单选"}[field.fieldType]}${field.options.length ? ` · ${field.options.map(esc).join(" / ")}` : ""}</small></div><span>${field.active ? tag("使用中","teal") : tag("已停用","gray")}</span><button class="secondary-btn" data-toggle-field="${esc(field.id)}" data-active="${field.active}">${field.active ? "停用" : "恢复"}</button></div>`).join("") : `<div class="empty">还没有自定义表头。新增后，商务经理可直接在客户数据表里填写。</div>`}</div></section>`; document.querySelector("#add-field").addEventListener("click", openFieldForm); content.querySelectorAll("[data-toggle-field]").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/customer-fields/${button.dataset.toggleField}`, {method:"PATCH",body:JSON.stringify({active:button.dataset.active !== "true"})}); state.meta = await api("/api/meta"); await renderFields(content); toast(button.dataset.active === "true" ? "表头已停用，历史数据仍保留" : "表头已恢复"); } catch (err) { toast(err.message); } })); }
function openFieldForm() { openModal(`<div class="modal field-modal"><div class="modal-header"><div><h3>新增客户表头</h3><span class="hint">选择最符合数据用途的类型，创建后类型不可修改。</span></div><button class="close-btn" data-close>×</button></div><form id="field-form"><div class="modal-body"><div class="field"><label>表头名称 *</label><input name="label" required maxlength="50" placeholder="例如：护照有效期"></div><div class="field"><label>数据类型</label><div class="type-picker"><label><input type="radio" name="fieldType" value="text" checked><span><b>Aa</b>文本<small>姓名、说明、编号</small></span></label><label><input type="radio" name="fieldType" value="number"><span><b>123</b>数字<small>金额、数量</small></span></label><label><input type="radio" name="fieldType" value="date"><span><b>日</b>日期<small>到期日、登记日</small></span></label><label><input type="radio" name="fieldType" value="select"><span><b>⌄</b>单选<small>固定状态或分类</small></span></label></div></div><div class="field" id="field-options" hidden><label>可选项 *</label><textarea name="options" placeholder="每行一个选项，例如：&#10;已提交&#10;审核中&#10;已完成"></textarea></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">创建表头</button></div></form></div>`); document.querySelectorAll('[name="fieldType"]').forEach((radio) => radio.addEventListener("change", () => { document.querySelector("#field-options").hidden = radio.value !== "select"; })); document.querySelector("#field-form").addEventListener("submit", async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const payload = {label:form.get("label"),fieldType:form.get("fieldType"),options:String(form.get("options") || "").split("\n")}; try { await api("/api/customer-fields", {method:"POST",body:JSON.stringify(payload)}); closeModal(); state.meta = await api("/api/meta"); toast("新表头已加入客户数据表"); navigate("fields"); } catch (err) { toast(err.message); } }); }
async function renderAdvisorBindings(content) {
  const data = await api("/api/advisor-bindings");
  const typeLabels = data.customerTypes || {non_placement: "非定增", placement: "定增"};
  const modeLabels = data.assignmentModes || {default: "默认绑定", manual: "手动分配"};
  const activeCount = data.items.filter((item) => item.active).length;
  content.innerHTML = `<div class="section-heading"><div><div class="eyebrow">RELATIONSHIP RULES</div><h3>顾问绑定关系</h3><p>港安顾问不登录系统；这里维护外部引荐人与骄阳团队之间的业务关系。</p></div><button class="primary-btn" id="add-advisor-binding">＋ 新增绑定</button></div><div class="notice binding-notice"><strong>使用规则：</strong>非定增客户可以按唯一的“默认绑定”自动带出骄阳负责人；定增客户建议使用“手动分配”，由部门主管或管理员在客户详情中指定。只保留姓名、不关联账号的规则不会自动分配客户。</div><section class="section"><div class="section-header"><div><h3>当前规则</h3><span class="hint">${activeCount} 条启用 · ${data.items.length - activeCount} 条停用</span></div><span class="hint">同一港安顾问、客户类型和分配方式只能有一条规则</span></div><div class="table-wrap"><table class="binding-table"><thead><tr><th>港安顾问</th><th>骄阳顾问</th><th>客户类型</th><th>分配方式</th><th>系统账号</th><th>状态</th><th>操作</th></tr></thead><tbody>${data.items.length ? data.items.map((item) => `<tr class="${item.active ? "" : "inactive-row"}"><td><strong>${esc(item.honganAdvisor)}</strong></td><td><strong>${esc(item.jiaoyangAdvisor)}</strong>${item.jiaoyangAdvisorTeam ? `<div class="hint">${esc(item.jiaoyangAdvisorTeam)}</div>` : ""}</td><td>${tag(typeLabels[item.customerType] || item.customerType, item.customerType === "placement" ? "amber" : "teal")}</td><td>${tag(modeLabels[item.assignmentMode] || item.assignmentMode, item.assignmentMode === "default" ? "cyan" : "gray")}</td><td>${item.jiaoyangAdvisorId ? (item.jiaoyangAccountActive ? tag("已关联", "teal") : tag("账号已停用", "red")) : tag("仅保留姓名", "gray")}</td><td>${item.active ? tag("启用", "teal") : tag("停用", "gray")}</td><td><button class="secondary-btn" data-edit-advisor-binding="${esc(item.id)}">编辑</button><button class="secondary-btn" data-toggle-advisor-binding="${esc(item.id)}" data-active="${item.active}">${item.active ? "停用" : "启用"}</button></td></tr>`).join("") : `<tr><td colspan="7"><div class="empty">还没有绑定规则。新增后，非定增客户才会按规则自动分配。</div></td></tr>`}</tbody></table></div></section>`;
  content.querySelector("#add-advisor-binding").addEventListener("click", () => openAdvisorBindingForm());
  content.querySelectorAll("[data-edit-advisor-binding]").forEach((button) => button.addEventListener("click", () => openAdvisorBindingForm(data.items.find((item) => item.id === button.dataset.editAdvisorBinding))));
  content.querySelectorAll("[data-toggle-advisor-binding]").forEach((button) => button.addEventListener("click", async () => { const reason = window.prompt("请输入启用或停用绑定规则的原因"); if (!reason?.trim()) return; try { await api(`/api/advisor-bindings/${button.dataset.toggleAdvisorBinding}`, {method:"PATCH", body:JSON.stringify({active: button.dataset.active !== "true", changeReason: reason.trim()})}); await renderAdvisorBindings(content); toast(button.dataset.active === "true" ? "绑定规则已停用" : "绑定规则已启用"); } catch (err) { toast(err.message); } }));
}

function openAdvisorBindingForm(item = null) {
  const owners = state.meta.owners || [];
  const ownerChoices = owners.map((owner) => `<option value="${esc(owner.id)}" data-name="${esc(owner.name)}" ${item?.jiaoyangAdvisorId === owner.id ? "selected" : ""}>${esc(owner.name)} · ${esc(owner.team)}</option>`).join("");
  const selectedMissing = item?.jiaoyangAdvisorId && !owners.some((owner) => owner.id === item.jiaoyangAdvisorId) ? `<option value="${esc(item.jiaoyangAdvisorId)}" selected>${esc(item.jiaoyangAdvisor)} · 当前账号不在可见范围</option>` : "";
  const honganOptions = (state.meta.honganAdvisors || []).map((advisor) => `<option value="${esc(advisor)}"></option>`).join("");
  openModal(`<div class="modal binding-modal"><div class="modal-header"><div><h3>${item ? "编辑顾问绑定" : "新增顾问绑定"}</h3><span class="hint">港安顾问是外部引荐人；骄阳顾问账号可稍后补关联。</span></div><button class="close-btn" data-close>×</button></div><form id="advisor-binding-form"><div class="modal-body"><datalist id="binding-hongan-options">${honganOptions}</datalist><div class="form-grid"><div class="field"><label>港安顾问 *</label><input name="honganAdvisor" list="binding-hongan-options" required maxlength="100" value="${esc(item?.honganAdvisor || "")}" placeholder="例如：黄玉竹"></div><div class="field"><label>骄阳顾问姓名 *</label><input name="jiaoyangAdvisor" required maxlength="100" value="${esc(item?.jiaoyangAdvisor || "")}" placeholder="未注册账号也可先填姓名"></div><div class="field"><label>关联系统账号</label><select name="jiaoyangAdvisorId"><option value="">仅保留姓名，暂不关联账号</option>${selectedMissing}${ownerChoices}</select><span class="hint">关联后，非定增的默认规则才可以自动分配。</span></div><div class="field"><label>客户类型 *</label><select name="customerType"><option value="non_placement" ${item?.customerType === "non_placement" || !item ? "selected" : ""}>非定增</option><option value="placement" ${item?.customerType === "placement" ? "selected" : ""}>定增</option></select></div><div class="field"><label>分配方式 *</label><select name="assignmentMode"><option value="default" ${item?.assignmentMode === "default" || !item ? "selected" : ""}>默认绑定</option><option value="manual" ${item?.assignmentMode === "manual" ? "selected" : ""}>手动分配</option></select></div><div class="field"><label class="switch binding-active"><input name="active" type="checkbox" ${item?.active !== false ? "checked" : ""}>启用这条规则</label></div>${item ? `<div class="field span-2"><label>变更原因 *</label><input name="changeReason" required placeholder="例如：港安顾问更换对应商务经理"></div>` : ""}<div class="field span-2"><label>备注</label><textarea name="notes" placeholder="例如：非定增客户固定组合；定增客户仅作历史参考。">${esc(item?.notes || "")}</textarea></div></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存绑定</button></div></form></div>`);
  const form = document.querySelector("#advisor-binding-form");
  form.querySelector('[name="jiaoyangAdvisorId"]').addEventListener("change", (event) => { const option = event.target.selectedOptions[0]; if (option?.dataset.name) form.querySelector('[name="jiaoyangAdvisor"]').value = option.dataset.name; });
  form.querySelector('[name="customerType"]').addEventListener("change", (event) => { if (event.target.value === "placement") form.querySelector('[name="assignmentMode"]').value = "manual"; });
  form.addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(form)); values.active = form.querySelector('[name="active"]').checked; values.jiaoyangAdvisorId = values.jiaoyangAdvisorId || null; try { await api(item ? `/api/advisor-bindings/${item.id}` : "/api/advisor-bindings", {method: item ? "PATCH" : "POST", body: JSON.stringify(values)}); closeModal(); toast(item ? "顾问绑定已更新" : "顾问绑定已创建"); navigate("bindings"); } catch (err) { toast(err.message); } });
}
async function renderPermissions(content) {
  const data = await api("/api/admin/users");
  const scopeOptions = (selected) => [["inherit", "继承 MuskZoom"], ["self", "仅本人客户"], ["team", "本组客户"], ["all", "全量客户"]].map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
  const permissionSwitch = (type, item, checked, label) => `<label class="switch"><input type="checkbox" data-permission="${type}" data-user="${esc(item.id)}" ${checked ? "checked" : ""}>${label}</label>`;
  content.innerHTML = `<div class="notice"><strong>CRM 独立权限：</strong>账号、岗位和业务团队仍由 MuskZoom 维护；这里仅决定该账号在骄阳客户系统中可见的数据范围和可执行的功能，不会改变 MuskZoom 的岗位、团队或聊天质检权限。</div><section class="section"><div class="section-header"><div><h3>客户模块权限</h3><span class="hint">数据范围选“继承 MuskZoom”时：商务经理仅本人、部门主管仅本组、管理员与开发者为全量。</span></div><span class="hint">${data.items.length} 个账号</span></div><div class="table-wrap"><table class="permission-table"><thead><tr><th>成员</th><th>MuskZoom 角色 / 团队</th><th>CRM 数据范围</th><th>批量导入</th><th>客户归属</th><th>顾问绑定</th><th>表头管理</th><th>完整导出</th><th>CRM 权限管理</th></tr></thead><tbody>${data.items.map((item) => `<tr><td><strong>${esc(item.name)}</strong><div class="hint">${esc(item.username)}</div></td><td>${tag(item.roleLabel, roleTone(item.role))}<div class="hint">${esc(item.team)}</div></td><td><select data-permission="scope" data-user="${esc(item.id)}">${scopeOptions(item.crmScopeMode || "inherit")}</select></td><td>${permissionSwitch("import", item, item.canImportCustomers, "允许")}</td><td>${permissionSwitch("assignments", item, item.canManageAssignments, "允许")}</td><td>${permissionSwitch("bindings", item, item.canManageAdvisorBindings, "允许")}</td><td>${permissionSwitch("fields", item, item.canManageCustomerFields, "允许")}</td><td>${permissionSwitch("export", item, item.canExportAll, "允许")}</td><td>${permissionSwitch("permissions", item, item.canManageCrmPermissions, "允许")}</td></tr>`).join("")}</tbody></table></div></section>`;
  content.querySelectorAll("[data-permission]").forEach((input) => input.addEventListener("change", () => savePermission(input, data.items.find((item) => item.id === input.dataset.user))));
}
async function savePermission(input, item) {
  const selector = (name) => document.querySelector(`[data-permission=${name}][data-user="${CSS.escape(item.id)}"]`);
  const scopeInput = selector("scope");
  const importInput = selector("import");
  const assignmentsInput = selector("assignments");
  const bindingsInput = selector("bindings");
  const fieldsInput = selector("fields");
  const exportInput = selector("export");
  const permissionsInput = selector("permissions");
  const before = input.type === "checkbox" ? !input.checked : item.crmScopeMode || "inherit";
  try {
    const result = await api(`/api/admin/users/${encodeURIComponent(item.id)}/permissions`, {method:"PATCH", body: JSON.stringify({
      crmScopeMode: scopeInput.value,
      canImportCustomers: importInput.checked,
      canManageAssignments: assignmentsInput.checked,
      canManageAdvisorBindings: bindingsInput.checked,
      canManageCustomerFields: fieldsInput.checked,
      canExportAll: exportInput.checked,
      canManageCrmPermissions: permissionsInput.checked,
    })});
    if (item.id === state.user.id) {
      state.user = result.user;
      state.meta = await api("/api/meta");
      toast("CRM 权限已更新，当前界面已按新权限刷新");
      await navigate("permissions", {preserveWorkflow:true});
      return;
    }
    toast("CRM 权限已更新");
  } catch (err) {
    if (input.type === "checkbox") input.checked = before;
    else input.value = before;
    toast(err.message);
  }
}
async function renderAudit(content) { const data = await api("/api/admin/audit"); content.innerHTML = `<section class="section"><div class="section-header"><h3>操作审计</h3><span class="hint">客户、批次、跟进和权限变更</span></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>详情</th></tr></thead><tbody>${data.items.length ? data.items.map((item) => `<tr><td class="hint">${fmt(item.created_at)}</td><td>${esc(item.actor_name)}</td><td>${esc(item.action)}</td><td>${esc(item.entity_type)} / ${esc(item.entity_id.slice(0, 12))}</td><td class="hint">${esc(JSON.stringify(item.detail))}</td></tr>`).join("") : `<tr><td colspan="5"><div class="empty">暂无审计记录</div></td></tr>`}</tbody></table></div></section>`; }

function openQuickCustomerForm() {
  const batches = (state.meta.batches || []).map((batch) => `<option value="${esc(batch.id)}">${esc(batch.name)} · ${esc(batch.status)}</option>`).join("");
  const customFields = (state.meta.customerFields || []).map((field) => `<div class="field"><label>${esc(field.label)}</label>${field.fieldType === "select" ? `<select data-custom-input="${esc(field.id)}"><option value="">未填写</option>${field.options.map((option) => `<option>${esc(option)}</option>`).join("")}</select>` : `<input data-custom-input="${esc(field.id)}" type="${field.fieldType === "number" ? "number" : field.fieldType === "date" ? "date" : "text"}">`}</div>`).join("");
  const ownerChoices = state.meta.ownerChoices || state.meta.owners || [];
  const ownerField = state.user.canManageAssignments ? `<div class="field"><label>当前骄阳负责人 *</label><select name="ownerId" required>${ownerChoices.map((owner) => `<option value="${esc(owner.id)}" ${owner.id === "unassigned" ? "selected" : ""}>${esc(owner.name)}${owner.team ? ` · ${esc(owner.team)}` : ""}</option>`).join("")}</select></div>` : "";
  const canManageHonganAdvisor = state.user.canManageAdvisorBindings;
  const advisorOptions = (state.meta.honganAdvisors || []).map((advisor) => `<option value="${esc(advisor)}"></option>`).join("");
  openModal(`<div class="modal quick-modal"><div class="modal-header"><div><h3>新增客户</h3><span class="hint">港安顾问是外部引荐关系；当前骄阳负责人是内部跟进与归属关系。</span></div><button class="close-btn" data-close>×</button></div><form id="quick-customer-form"><div class="modal-body"><datalist id="hongan-advisor-options">${advisorOptions}</datalist><div class="form-grid"><div class="field"><label>客户姓名</label><input name="name" autofocus></div><div class="field"><label>微信昵称</label><input name="wechatNickname"></div><div class="field"><label>手机号</label><input name="phone" inputmode="tel"></div><div class="field"><label>邮箱</label><input name="email" type="email" inputmode="email" autocomplete="email"></div><div class="field"><label>港安顾问（外部引荐）</label><input name="hkAdvisor" list="hongan-advisor-options" placeholder="可选择或输入姓名"></div>${ownerField}<div class="field"><label>客户来源</label><select name="source"><option value="">未填写</option>${state.meta.sources.map((v) => `<option>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>当前阶段</label><select name="stage">${state.meta.stages.map((v) => `<option>${esc(v)}</option>`).join("")}</select></div><div class="field span-2"><label>当前情况</label><textarea name="notes" placeholder="客户诉求、当前进度或下一步安排"></textarea></div></div>${customFields ? `<details class="form-details"><summary>补充信息 · ${state.meta.customerFields.length} 项</summary><div class="form-grid">${customFields}</div></details>` : ""}<details class="form-details"><summary>开户与定增进度</summary><div class="form-grid"><div class="field"><label>港券开户状态</label><select name="accountStatus">${state.meta.accountStatuses.map((v) => `<option>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>定增意向</label><select name="intentStatus">${state.meta.intentStatuses.map((v) => `<option>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>定增推进</label><select name="placementStatus">${state.meta.placementStatuses.map((v) => `<option>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>目标批次</label><select name="targetBatchId"><option value="">暂不排批次</option>${batches}</select></div><div class="field"><label>意向金额 (USD)</label><input name="intentAmount" type="number" min="0" inputmode="decimal"></div><div class="field"><label>到账金额 (USD)</label><input name="fundedAmount" type="number" min="0" inputmode="decimal"></div><div class="field"><label>实际参与金额 (USD)</label><input name="actualAmount" type="number" min="0" inputmode="decimal"></div><div class="field"><label>开户券商</label><input name="accountBroker"></div></div></details></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存客户</button></div></form></div>`);
  if (!canManageHonganAdvisor) document.querySelector("#quick-customer-form [name=hkAdvisor]")?.closest(".field")?.remove();
  document.querySelector("#quick-customer-form").addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); for (const key of ["intentAmount", "fundedAmount", "actualAmount"]) values[key] = Number(values[key] || 0); values.targetBatchId = values.targetBatchId || null; values.customValues = Object.fromEntries([...event.currentTarget.querySelectorAll("[data-custom-input]")].map((input) => [input.dataset.customInput,input.value])); try { const result = await api("/api/customers", {method:"POST", body: JSON.stringify(values)}); state.meta = await api("/api/meta"); closeModal(); toast(result.potentialDuplicates?.length ? `客户已保存，发现 ${result.potentialDuplicates.length} 条同名或同昵称记录，请主管确认` : "客户已进入数据表"); await navigate(state.view === "imports" ? "customers" : state.view); } catch (err) { toast(err.message); } });
}
async function openQuickFollowForm(selectedCustomerId = "") { if (!state.customers.length) { const data = await api("/api/customers?pageSize=100"); state.customers = data.items; } const customerOptions = state.customers.length ? state.customers.map((row) => `<option value="${esc(row.id)}" ${selectedCustomerId === row.id ? "selected" : ""}>${esc(row.name || row.wechat_nickname || row.customer_code)} · ${esc(row.customer_code)}</option>`).join("") : `<option value="">暂无可跟进客户</option>`; openModal(`<div class="modal"><div class="modal-header"><h3>写今日跟进</h3><button class="close-btn" data-close>×</button></div><form id="quick-follow-form"><div class="modal-body"><div class="field"><label>客户 *</label><select name="customerId" required>${customerOptions}</select></div><div class="form-grid"><div class="field"><label>沟通方式</label><select name="method">${state.meta.followupMethods.map((v) => `<option>${v}</option>`).join("")}</select></div><div class="field"><label>跟进后阶段</label><select name="stageAfter">${state.meta.stages.map((v) => `<option>${esc(v)}</option>`).join("")}</select></div></div><div class="field"><label>沟通内容 *</label><textarea name="content" required placeholder="记录客户反馈、资金进度和下一步动作"></textarea></div><div class="form-grid"><div class="field"><label>沟通结果</label><input name="outcome"></div><div class="field"><label>下一步动作</label><input name="nextAction"></div><div class="field"><label>下次跟进时间</label><input name="nextFollowupAt" type="datetime-local"></div></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存跟进</button></div></form></div>`); document.querySelector("#quick-follow-form").addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); const customerId = values.customerId; delete values.customerId; values.nextFollowupAt = values.nextFollowupAt || null; try { await api(`/api/customers/${customerId}/followups`, {method:"POST", body: JSON.stringify(values)}); closeModal(); toast("跟进已保存"); await navigate("followups"); } catch (err) { toast(err.message); } }); }

function openDetail(id) { api(`/api/customers/${encodeURIComponent(id)}`).then((data) => { state.detail = data; lockPageScroll(); renderDrawer(); renderCustomerRelations(); }).catch((err) => toast(err.message)); }
function lockPageScroll() {
  if (pageScrollLockDepth === 0) {
    lockedScrollY = window.scrollY;
    document.body.classList.add("drawer-open");
    document.body.style.top = `-${lockedScrollY}px`;
  }
  pageScrollLockDepth += 1;
}
function unlockPageScroll() {
  pageScrollLockDepth = Math.max(0, pageScrollLockDepth - 1);
  if (pageScrollLockDepth !== 0) return;
  document.body.classList.remove("drawer-open");
  document.body.style.removeProperty("top");
  window.scrollTo(0, lockedScrollY);
}
function renderDrawer() {
  const item = state.detail.customer;
  const canManage = state.user.canManageAssignments;
  const backdrop = document.createElement("div");
  backdrop.className = "drawer-backdrop";
  backdrop.id = "detail-drawer";
  backdrop.innerHTML = `<aside class="drawer"><div class="detail-title"><div><div class="eyebrow">CUSTOMER PROFILE</div><h3>${esc(item.name || item.wechat_nickname || "")}</h3><div class="detail-meta">${esc(item.customer_code)} · ${esc(item.owner_name)} · ${esc(item.owner_team)}</div></div><div class="detail-actions"><button class="secondary-btn" data-followup>＋ 跟进</button>${canManage ? `<button class="secondary-btn" data-assign>分配</button><button class="secondary-btn" data-merge>合并</button>` : ""}<button class="close-btn" data-close>×</button></div></div><div class="detail-grid"><div class="detail-item"><small>港券开户</small>${tag(item.account_status, toneFor(item.account_status))}<div class="hint">${esc(item.account_broker || "券商待填")}</div></div><div class="detail-item"><small>定增意向</small>${tag(item.intent_status, toneFor(item.intent_status))}<div class="hint">意向 ${money(item.intent_amount)}</div></div><div class="detail-item"><small>目标批次</small>${esc(item.target_batch_name || "未排批次")}</div><div class="detail-item"><small>资金 / 实际参与</small>${money(item.funded_amount)} / ${money(item.actual_amount)}</div><div class="detail-item"><small>定增推进</small>${tag(item.placement_status, toneFor(item.placement_status))}</div><div class="detail-item"><small>生命周期</small>${tag(item.stage, toneFor(item.stage))}</div></div><div class="section-header" style="padding:0 0 12px;border:0"><div><h3>引荐与顾问关系</h3><span class="hint">港安顾问不登录系统；骄阳负责人负责内部跟进与归属。</span></div></div><div class="detail-grid compact"><div class="detail-item"><small>港安顾问（外部引荐）</small>${esc(item.hongan_advisor || "未填写")}</div><div class="detail-item"><small>原表骄阳顾问</small>${esc(item.source_advisor_label || "未填写")}</div><div class="detail-item"><small>当前骄阳负责人</small>${esc(item.owner_name || "待分配")}<div class="hint">${esc(item.owner_team || "待分配池")}</div></div></div><div class="section-header" style="padding:22px 0 12px;border:0"><h3>客户资料</h3><button class="secondary-btn" data-edit>编辑资料</button></div><div class="detail-grid compact"><div class="detail-item"><small>微信昵称</small>${esc(item.wechat_nickname || "未填写")}</div><div class="detail-item"><small>手机号</small>${esc(item.phone || "未填写")}</div><div class="detail-item"><small>邮箱</small>${esc(item.email || "未填写")}</div><div class="detail-item"><small>来源</small>${esc(item.source || "未填写")}</div><div class="detail-item"><small>备注</small>${esc(item.notes || "未填写")}</div></div><div class="section-header" style="padding:22px 0 0;border:0"><h3>跟进时间线</h3></div><div class="timeline">${state.detail.followups.length ? state.detail.followups.map((follow) => `<div class="timeline-item"><div class="timeline-head"><span>${esc(follow.author_name)} · ${esc(follow.method)}</span><span>${fmt(follow.created_at)}</span></div><div class="timeline-content">${esc(follow.content)}${follow.outcome ? `<div class="hint">结果：${esc(follow.outcome)}</div>` : ""}${follow.next_action ? `<div class="hint">下一步：${esc(follow.next_action)}</div>` : ""}${follow.next_followup_at ? `<div class="hint">下次跟进：${fmt(follow.next_followup_at)}</div>` : ""}</div></div>`).join("") : `<div class="empty" style="padding:25px 0">还没有跟进记录</div>`}</div></aside>`;
  document.body.appendChild(backdrop);
  const detailMeta = backdrop.querySelector(".detail-meta");
  if (detailMeta && item.tw_code) detailMeta.insertAdjacentHTML("beforeend", ` · TW ${esc(item.tw_code)}`);
  backdrop.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", closeDrawer));
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) closeDrawer(); });
  backdrop.querySelector("[data-followup]").addEventListener("click", () => openQuickFollowFormFor(item));
  backdrop.querySelector("[data-edit]").addEventListener("click", () => openEditFormNew(item));
  if (canManage) {
    backdrop.querySelector("[data-assign]").addEventListener("click", () => openAssignFormNew(item));
    backdrop.querySelector("[data-merge]").addEventListener("click", () => openMergeForm(item));
  }
}
function renderCustomerRelations() {
  const drawer = document.querySelector("#detail-drawer .drawer");
  const item = state.detail?.customer;
  if (!drawer || !item) return;
  const canManage = state.user.canManageAssignments;
  const collaborators = item.collaborators || [];
  const snapshots = state.detail.holdingSnapshots || [];
  const collaboratorText = collaborators.length
    ? collaborators.map((person) => `<span class="collaborator-chip"><b>${esc(person.name)}</b><small>${esc(person.role)}</small></span>`).join("")
    : `<span class="hint">暂无协同负责人</span>`;
  const snapshotRows = snapshots.length
    ? snapshots.slice(0, 3).map((snapshot) => `<div class="snapshot-row"><span>${esc(snapshot.snapshot_date)}</span><span>${Number(snapshot.quantity || 0).toLocaleString("en-US", {maximumFractionDigits: 2})} 股</span><b>${money(snapshot.market_value)}</b></div>`).join("")
    : `<div class="hint">还没有持仓快照。</div>`;
  const panel = document.createElement("section");
  panel.className = "customer-relations";
  panel.innerHTML = `<div class="relation-section"><div class="relation-heading"><div><h3>协同负责人</h3><span class="hint">主负责人：${esc(item.owner_name)}</span></div>${canManage ? `<button class="secondary-btn" data-manage-collaborators>调整</button>` : ""}</div><div class="collaborator-list">${collaboratorText}</div></div><div class="relation-section"><div class="relation-heading"><div><h3>历史持仓快照</h3><span class="hint">按日期保存数量和市值</span></div><button class="secondary-btn" data-add-snapshot>新增快照</button></div><div class="snapshot-list">${snapshotRows}</div></div>`;
  const timeline = drawer.querySelector(".timeline");
  const timelineHeading = timeline?.previousElementSibling;
  (timelineHeading?.classList.contains("section-header") ? timelineHeading : timeline)?.insertAdjacentElement("beforebegin", panel);
  panel.querySelector("[data-manage-collaborators]")?.addEventListener("click", () => openCollaboratorForm(item));
  panel.querySelector("[data-add-snapshot]")?.addEventListener("click", () => openHoldingSnapshotForm(item));
}

function openCollaboratorForm(item) {
  const selected = new Set((item.collaborators || []).map((person) => person.id));
  const choices = (state.meta.collaboratorUsers || []).filter((person) => person.id !== item.owner_id);
  openModal(`<div class="modal"><div class="modal-header"><div><h3>设置协同负责人</h3><span class="hint">主负责人负责业绩归属；协同成员可查看并跟进客户。</span></div><button class="close-btn" data-close>×</button></div><form id="collaborator-form"><div class="modal-body"><div class="collaborator-options">${choices.length ? choices.map((person) => `<label class="collaborator-option"><input type="checkbox" name="collaborator" value="${esc(person.id)}" ${selected.has(person.id) ? "checked" : ""}><span><strong>${esc(person.name)}</strong><small>${esc(person.roleLabel)} · ${esc(person.team)}</small></span></label>`).join("") : `<div class="empty">当前范围内没有可选协同成员。</div>`}</div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存协同关系</button></div></form></div>`);
  document.querySelector("#collaborator-form").addEventListener("submit", async (event) => { event.preventDefault(); const userIds = [...event.currentTarget.querySelectorAll('[name="collaborator"]:checked')].map((input) => input.value); try { await api(`/api/customers/${item.id}/collaborators`, {method:"PUT", body:JSON.stringify({userIds})}); closeModal(); toast("协同负责人已更新"); openDetail(item.id); } catch (err) { toast(err.message); } });
}

function openHoldingSnapshotForm(item) {
  const today = new Date().toISOString().slice(0, 10);
  openModal(`<div class="modal"><div class="modal-header"><div><h3>新增持仓快照</h3><span class="hint">同一客户、日期和证券会更新为最新录入值。</span></div><button class="close-btn" data-close>×</button></div><form id="holding-snapshot-form"><div class="modal-body"><div class="form-grid"><div class="field"><label>快照日期 *</label><input name="snapshotDate" type="date" required value="${today}"></div><div class="field"><label>证券名称</label><input name="securityName" value="二级市场持仓"></div><div class="field"><label>持仓数量</label><input name="quantity" type="number" min="0" step="any" inputmode="decimal"></div><div class="field"><label>持仓市值 (USD)</label><input name="marketValue" type="number" min="0" step="any" inputmode="decimal"></div><div class="field span-2"><label>来源说明</label><input name="sourceLabel" value="手动录入"></div></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存快照</button></div></form></div>`);
  document.querySelector("#holding-snapshot-form").addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); values.quantity = Number(values.quantity || 0); values.marketValue = Number(values.marketValue || 0); try { await api(`/api/customers/${item.id}/holding-snapshots`, {method:"POST", body:JSON.stringify(values)}); closeModal(); toast("持仓快照已保存"); openDetail(item.id); } catch (err) { toast(err.message); } });
}

function closeDrawer() { const drawer = document.querySelector("#detail-drawer"); if (!drawer) return; drawer.remove(); unlockPageScroll(); }
function openQuickFollowFormFor(item) { closeDrawer(); openQuickFollowForm(item.id); }
function openEditForm(item) { const batches = (state.meta.batches || []).map((batch) => `<option value="${esc(batch.id)}" ${item.target_batch_id === batch.id ? "selected" : ""}>${esc(batch.name)}</option>`).join(""); openModal(`<div class="modal quick-modal"><div class="modal-header"><h3>更新客户进度</h3><button class="close-btn" data-close>×</button></div><form id="edit-form"><div class="modal-body"><div class="form-grid"><div class="field"><label>客户姓名</label><input name="name" value="${esc(item.name)}"></div><div class="field"><label>微信昵称</label><input name="wechatNickname" value="${esc(item.wechat_nickname)}"></div><div class="field"><label>手机号</label><input name="phone" inputmode="tel" value="${esc(item.phone)}"></div><div class="field"><label>邮箱</label><input name="email" type="email" inputmode="email" autocomplete="email" value="${esc(item.email)}"></div><div class="field"><label>港券开户状态</label><select name="accountStatus">${state.meta.accountStatuses.map((v) => `<option ${item.account_status === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>定增意向</label><select name="intentStatus">${state.meta.intentStatuses.map((v) => `<option ${item.intent_status === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>定增推进</label><select name="placementStatus">${state.meta.placementStatuses.map((v) => `<option ${item.placement_status === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div><div class="field"><label>目标批次</label><select name="targetBatchId"><option value="">暂不排批次</option>${batches}</select></div><div class="field"><label>意向金额</label><input name="intentAmount" type="number" min="0" value="${item.intent_amount || 0}"></div><div class="field"><label>到账金额</label><input name="fundedAmount" type="number" min="0" value="${item.funded_amount || 0}"></div><div class="field"><label>实际参与金额</label><input name="actualAmount" type="number" min="0" value="${item.actual_amount || 0}"></div><div class="field"><label>流失原因</label><input name="lostReason" value="${esc(item.lost_reason)}"></div><div class="field span-2"><label>备注</label><textarea name="notes">${esc(item.notes)}</textarea></div></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存更新</button></div></form></div>`); document.querySelector("#edit-form").addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); for (const key of ["intentAmount", "fundedAmount", "actualAmount"]) values[key] = Number(values[key] || 0); values.targetBatchId = values.targetBatchId || null; values.version = item.version; try { await api(`/api/customers/${item.id}`, {method:"PATCH", body: JSON.stringify(values)}); closeModal(); toast("客户进度已更新"); openDetail(item.id); } catch (err) { toast(err.message); } }); }
function openEditFormNew(item) {
  const canManageHonganAdvisor = state.user.canManageAdvisorBindings;
  const batches = (state.meta.batches || []).map((batch) => `<option value="${esc(batch.id)}" ${item.target_batch_id === batch.id ? "selected" : ""}>${esc(batch.name)}</option>`).join("");
  const advisorOptions = (state.meta.honganAdvisors || []).map((advisor) => `<option value="${esc(advisor)}"></option>`).join("");
  const honganField = canManageHonganAdvisor
    ? `<div class="field"><label>港安顾问（外部引荐）</label><input name="hkAdvisor" list="hongan-advisor-options" value="${esc(item.hongan_advisor)}"></div>`
    : "";
  const honganReasonField = canManageHonganAdvisor
    ? `<div class="field"><label>港安顾问变更原因</label><input name="changeReason" placeholder="仅修改港安顾问时必填"></div>`
    : "";
  openModal(`<div class="modal quick-modal"><div class="modal-header"><div><h3>更新客户进度</h3><span class="hint">港安顾问由具备顾问绑定权限的成员维护；重新分配只改变骄阳负责人。</span></div><button class="close-btn" data-close>×</button></div><form id="edit-form"><div class="modal-body"><datalist id="hongan-advisor-options">${advisorOptions}</datalist><div class="form-grid"><div class="field"><label>客户姓名</label><input name="name" value="${esc(item.name)}"></div><div class="field"><label>微信昵称</label><input name="wechatNickname" value="${esc(item.wechat_nickname)}"></div><div class="field"><label>手机号</label><input name="phone" inputmode="tel" value="${esc(item.phone)}"></div><div class="field"><label>邮箱</label><input name="email" type="email" inputmode="email" autocomplete="email" value="${esc(item.email)}"></div>${honganField}${honganReasonField}<div class="field"><label>港券开户状态</label><select name="accountStatus">${state.meta.accountStatuses.map((value) => `<option ${item.account_status === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></div><div class="field"><label>定增意向</label><select name="intentStatus">${state.meta.intentStatuses.map((value) => `<option ${item.intent_status === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></div><div class="field"><label>定增推进</label><select name="placementStatus">${state.meta.placementStatuses.map((value) => `<option ${item.placement_status === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></div><div class="field"><label>目标批次</label><select name="targetBatchId"><option value="">暂不排批次</option>${batches}</select></div><div class="field"><label>意向金额</label><input name="intentAmount" type="number" min="0" value="${item.intent_amount || 0}"></div><div class="field"><label>到账金额</label><input name="fundedAmount" type="number" min="0" value="${item.funded_amount || 0}"></div><div class="field"><label>实际参与金额</label><input name="actualAmount" type="number" min="0" value="${item.actual_amount || 0}"></div><div class="field"><label>流失原因</label><input name="lostReason" value="${esc(item.lost_reason)}"></div><div class="field span-2"><label>备注</label><textarea name="notes">${esc(item.notes)}</textarea></div></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">保存更新</button></div></form></div>`);
  document.querySelector("#edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    for (const key of ["intentAmount", "fundedAmount", "actualAmount"]) values[key] = Number(values[key] || 0);
    values.targetBatchId = values.targetBatchId || null;
    values.version = item.version;
    try {
      await api(`/api/customers/${item.id}`, {method:"PATCH", body: JSON.stringify(values)});
      state.meta = await api("/api/meta");
      closeModal();
      toast("客户资料已更新");
      openDetail(item.id);
    } catch (err) { toast(err.message); }
  });
}

function openAssignFormNew(item, onComplete = null) {
  const owners = (state.meta.ownerChoices || state.meta.owners || []).filter((owner) => owner.id !== item.owner_id);
  openModal(`<div class="modal"><div class="modal-header"><h3>重新分配骄阳负责人</h3><button class="close-btn" data-close>×</button></div><form id="assign-form"><div class="modal-body"><div class="notice">本操作只修改骄阳团队的当前负责人、可见范围和后续业绩归属，不会修改港安顾问。当前港安顾问：<b>${esc(item.hongan_advisor || "未填写")}</b></div><div class="field"><label>分配给</label><select name="ownerId" required>${owners.map((owner) => `<option value="${esc(owner.id)}">${esc(owner.name)} · ${esc(owner.team)}</option>`).join("")}</select></div><div class="field"><label>分配原因 *</label><textarea name="reason" required></textarea></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">确认分配</button></div></form></div>`);
  document.querySelector("#assign-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    if (!window.confirm(`确认将客户分配给“${document.querySelector('#assign-form [name="ownerId"] option:checked')?.textContent || "所选负责人"}”吗？`)) return;
    try {
      await api(`/api/customers/${item.id}/assign`, {method:"POST", body: JSON.stringify(values)});
      closeModal();
      closeDrawer();
      toast("当前骄阳负责人已重新分配");
      if (onComplete) await onComplete();
      else openDetail(item.id);
    } catch (err) { toast(err.message); }
  });
}

function openAssignForm(item) { const owners = state.meta.owners.filter((owner) => owner.id !== item.owner_id); openModal(`<div class="modal"><div class="modal-header"><h3>重新分配客户</h3><button class="close-btn" data-close>×</button></div><form id="assign-form"><div class="modal-body"><div class="field"><label>分配给</label><select name="ownerId" required>${owners.map((owner) => `<option value="${esc(owner.id)}">${esc(owner.name)} · ${esc(owner.team)}</option>`).join("")}</select></div><div class="field"><label>分配原因 *</label><textarea name="reason" required></textarea></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">确认分配</button></div></form></div>`); document.querySelector("#assign-form").addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); try { await api(`/api/customers/${item.id}/assign`, {method:"POST", body: JSON.stringify(values)}); closeModal(); closeDrawer(); toast("客户已重新分配"); openDetail(item.id); } catch (err) { toast(err.message); } }); }
function openMergeForm(item) { openModal(`<div class="modal"><div class="modal-header"><h3>合并重复客户</h3><button class="close-btn" data-close>×</button></div><form id="merge-form"><div class="modal-body"><div class="notice">重复记录会归档，主客户保留；跟进和分配历史会一并保留。</div><div class="field"><label>主客户</label><select name="targetCustomerId" required>${state.customers.filter((row) => row.id !== item.id).map((row) => `<option value="${esc(row.id)}">${esc(row.name)} · ${esc(row.customer_code)} · ${esc(row.owner_name)}</option>`).join("")}</select></div><div class="field"><label>合并原因 *</label><textarea name="reason" required></textarea></div></div><div class="modal-footer"><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn">确认合并</button></div></form></div>`); document.querySelector("#merge-form").addEventListener("submit", async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); if (!window.confirm("确认合并吗？源客户将被归档，跟进和分配历史会转移到主客户。")) return; try { await api("/api/customers/merge", {method:"POST", body: JSON.stringify({sourceCustomerId:item.id, ...values})}); closeModal(); closeDrawer(); toast("客户已合并"); navigate("customers"); } catch (err) { toast(err.message); } }); }
function openModal(html) { const existing = document.querySelector("#modal-root"); if (existing) existing.remove(); else lockPageScroll(); const root = document.createElement("div"); root.id = "modal-root"; root.className = "modal-backdrop"; root.innerHTML = html; document.body.appendChild(root); root.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", closeModal)); root.addEventListener("click", (event) => { if (event.target === root) closeModal(); }); }
function closeModal() { const modal = document.querySelector("#modal-root"); if (!modal) return; modal.remove(); unlockPageScroll(); }
function debounce(fn, wait) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); }; }
window.addEventListener("pagehide", saveWorkspaceState);
window.addEventListener("scroll", debounce(saveWorkspaceState, 180), {passive:true});

async function showLogin(error = "") {
  const config = await fetch("/api/auth/config", {credentials:"same-origin"}).then((response) => response.json()).catch(() => ({}));
  renderLogin(error, config);
}

async function startSsoLogin(token) {
  const data = await api("/api/auth/sso", {method:"POST", body:JSON.stringify({token})});
  localStorage.removeItem("jy_customer_token");
  state.token = null;
  state.user = data.user;
  const url = new URL(window.location.href);
  url.searchParams.delete("sso_token");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  restoreWorkspaceState();
  await bootApp();
}

(async function init() {
  const ssoToken = new URLSearchParams(window.location.search).get("sso_token");
  if (ssoToken) {
    try { await startSsoLogin(ssoToken); return; } catch (error) { await showLogin(error.message); return; }
  }
  try {
    const data = await api("/api/session");
    state.user = data.user;
    restoreWorkspaceState();
    await bootApp();
  } catch {
    localStorage.removeItem("jy_customer_token");
    state.token = null;
    await showLogin();
  }
})();

/* The review queue is intentionally explicit: every pending row can be opened
   before a write, and "keep" records a decision without changing customer data. */
function reviewName(item) { return item.pinyinName || item.name || item.rawRow?.name || item.rawRow?.twCode || "未命名记录"; }
function reviewProfileLabel(profile) { return ({holding_pinyin:"中阳拼音持仓", hongan_activity:"港安活动分表", asset:"券商资产", holding:"持仓快照", standard:"普通导入"})[profile] || profile || "普通导入"; }
function reviewIssueText(item) {
  if (item.category === "conflict") {
    if (item.profile === "hongan_activity") return "系统已有港安顾问，但本次活动表给出了不同值；系统没有自动覆盖。";
    return item.detail?.message || item.detail || "系统已有相同客户，本次导入的字段需要人工确认。";
  }
  if (item.category === "ambiguous") return `同一个姓名匹配到 ${item.candidateCount || item.candidates?.length || "多个"} 条客户记录，需要用 TW 编号确认。`;
  if (item.category === "unmatched") return "文件里有这条记录，但系统暂时找不到对应客户，不会自动新建。";
  return item.detail?.message || item.detail || "这条记录没有成功写入，需要人工检查原始行。";
}

function reviewImportedAdvisor(item) { return item.targetAdvisor || (item.advisors || []).join("、") || "未填写"; }

async function renderImportReviews(content) {
  try {
    const data = await api(`/api/import-reviews?include_resolved=${state.importReviewsIncludeResolved ? "true" : "false"}`);
    const items = data.items || [];
    const counts = {conflict: 0, error: 0, ambiguous: 0, unmatched: 0};
    items.forEach((item) => { if (counts[item.category] !== undefined) counts[item.category] += 1; });
    const rows = items.map((item) => {
      const candidates = (item.candidates || []).map((candidate) => `${candidate.customerName}（${candidate.twCode || candidate.customerCode || "无编号"}）`).join(" / ");
      const detail = item.category === "ambiguous" && candidates ? `${reviewIssueText(item)} 候选：${candidates}` : reviewIssueText(item);
      const actionLabel = item.profile === "hongan_activity" ? "核对港安顾问" : item.canApply ? "选择客户并写入" : "查看详情";
      const isPending = item.status === "pending";
      const status = isPending ? "" : `<span class="review-status ${item.status === "resolved" ? "resolved" : "ignored"}">${item.status === "resolved" ? "已完成" : "暂不处理"}</span>`;
      return `<article class="review-row ${isPending ? "" : "is-reviewed"}"><div><button class="review-record-button" data-review-open="${esc(item.id)}"><strong>${esc(reviewName(item))}</strong><small>${esc(item.pinyinName && item.customerName ? `${item.pinyinName} → ${item.customerName}` : item.customerCode || item.twCode || "点击查看详情")}</small></button></div><div><strong>${esc(item.filename)}</strong><small>${fmt(item.importedAt)} · ${esc(reviewProfileLabel(item.profile))}</small></div><div><span class="review-category ${esc(item.category)}">${esc(item.categoryLabel || item.category)}</span>${status}<small>${esc(String(detail))}</small></div><div class="review-actions"><button class="secondary-btn" data-review-open="${esc(item.id)}">查看详情</button>${isPending ? `<button class="secondary-btn" data-review-ignore="${esc(item.id)}">暂不处理</button>` : ""}</div></article>`;
    }).join("");
    content.innerHTML = `<div class="section-heading"><div><div class="eyebrow">IMPORT QUALITY CONTROL</div><h3>导入复核</h3><p>这里集中显示导入时没有安全自动写入的记录。打开详情后再决定如何处理。</p></div><div class="review-heading-actions"><button class="secondary-btn" id="toggle-reviewed">${state.importReviewsIncludeResolved ? "只看待处理" : "显示已处理"}</button><span class="entry-status ${data.pendingCount ? "is-warning" : ""}"><i></i>${data.pendingCount ? `待处理 ${data.pendingCount} 条` : "暂无待处理"}</span></div></div><section class="review-guide"><div><b>冲突</b><span>系统已有客户，但导入值不同；不会自动覆盖。</span></div><div><b>同名待确认</b><span>同一个姓名匹配到多个客户，要按 TW 编号选对人。</span></div><div><b>未匹配</b><span>找不到对应客户，不会自动新建。</span></div><div><b>处理</b><span>详情里可以保留系统值、采用导入值或暂不处理。</span></div></section><section class="review-summary"><div><b>${data.pendingCount || 0}</b><span>待处理</span></div><div><b>${counts.ambiguous}</b><span>同名待确认</span></div><div><b>${counts.unmatched}</b><span>未匹配</span></div><div><b>${counts.conflict + counts.error}</b><span>冲突 / 错误</span></div></section>${items.length ? `<section class="section review-list"><div class="section-header"><div><h3>${state.importReviewsIncludeResolved ? "全部复核记录" : "待复核记录"}</h3><span class="hint">点击记录名称或按钮查看原始值；处理后会保留在“已处理”视图。</span></div><span class="hint">${items.length} 条</span></div><div class="review-table"><div class="review-table-head"><span>记录</span><span>来源批次</span><span>问题</span><span>操作</span></div>${rows}</div></section>` : `<section class="section"><div class="empty">${state.importReviewsIncludeResolved ? "目前还没有复核记录。" : "目前没有待人工复核记录。以后导入出现冲突、错误或无法匹配时，会自动汇总到这里。"}</div></section>`}`;
    content.querySelector("#toggle-reviewed")?.addEventListener("click", () => { state.importReviewsIncludeResolved = !state.importReviewsIncludeResolved; renderImportReviews(content); });
    content.querySelectorAll("[data-review-open]").forEach((button) => button.addEventListener("click", () => { const item = items.find((entry) => entry.id === button.dataset.reviewOpen); if (item) openImportReviewResolver(item); }));
    content.querySelectorAll("[data-review-ignore]").forEach((button) => button.addEventListener("click", () => { if (window.confirm("暂不处理这条记录？它不会修改客户资料，之后仍可在已处理记录中查看。")) resolveImportReview(button.dataset.reviewIgnore, "ignore"); }));
  } catch (err) { content.innerHTML = `<section class="section"><div class="empty">${esc(err.message)}</div></section>`; }
}

function openImportReviewResolver(item) {
  const candidates = item.candidates || [];
  const initial = item.customerId ? {customerId: item.customerId, customerName: item.customerName || item.name, twCode: item.twCode, customerCode: item.customerCode, currentAdvisor: item.currentAdvisor} : candidates.length === 1 ? candidates[0] : null;
  const importedAdvisor = reviewImportedAdvisor(item);
  const comparison = item.profile === "hongan_activity" ? `<div class="review-comparison"><div><span>系统当前港安顾问</span><strong>${esc(item.currentAdvisor || initial?.currentAdvisor || "未填写")}</strong></div><div><span>本次导入港安顾问</span><strong>${esc(importedAdvisor)}</strong></div></div>` : "";
  const activityScope = item.profile === "hongan_activity" ? `<div class="review-scope"><b>本条冲突字段：港安顾问</b><span>客户姓名、TW 编号仅用于确认客户身份。金额、开户状态、骄阳现场开户人、见证人、定增信息和备注不参与本次比较，也不会被本次操作修改。</span>${item.sourceAdvisors?.length ? `<small>原表骄阳现场开户人：${esc(item.sourceAdvisors.join("、"))}（仅展示，不作为当前负责人）</small>` : ""}</div>` : "";
  const rawDetails = [item.sourceSheet ? `来源分表：${item.sourceSheet}` : "", item.sourceRow ? `来源行：第 ${item.sourceRow} 行` : "", item.rows ? `活动表同名记录数：${item.rows}` : "", item.quantity != null ? `持仓数量：${item.quantity}` : "", item.detail?.message || (typeof item.detail === "string" ? item.detail : "")].filter(Boolean).join(" · ");
  const directCustomer = initial && !candidates.length ? `<div class="review-candidate-heading">系统已关联客户</div><button type="button" class="review-customer-option selected" data-review-customer="${esc(initial.customerId)}"><span><strong>${esc(initial.customerName || reviewName(item))}</strong><small>${esc(initial.twCode || initial.customerCode || "无 TW 编号")}</small></span><small>${esc(initial.currentAdvisor || "港安顾问未填写")}</small></button>` : "";
  const customerOptions = candidates.length ? `<div class="review-candidate-heading">系统找到的候选客户</div>${candidates.map((candidate) => `<button type="button" class="review-customer-option ${initial?.customerId === candidate.customerId ? "selected" : ""}" data-review-customer="${esc(candidate.customerId)}"><span><strong>${esc(candidate.customerName || "未命名")}</strong><small>${esc(candidate.twCode || candidate.customerCode || "无 TW 编号")}</small></span><small>${esc(candidate.currentAdvisor || "港安顾问未填写")}</small></button>`).join("")}` : directCustomer || `<span class="hint">系统没有直接找到唯一客户，请用 TW 编号、姓名或手机号搜索。</span>`;
  const profileHint = item.profile === "hongan_activity" ? "这里只处理港安顾问，不会修改骄阳当前负责人。" : item.profile === "holding_pinyin" ? "确认后只会写入这条客户的持仓快照。" : "确认后只会处理这一条导入记录。";
  openModal(`<div class="modal review-resolver-modal"><div class="modal-header"><div><div class="eyebrow">MANUAL REVIEW</div><h3>${item.profile === "hongan_activity" ? "核对港安顾问" : "复核导入记录"}</h3><span class="hint">${esc(reviewName(item))} · ${esc(reviewProfileLabel(item.profile))}</span></div><button class="close-btn" data-close>×</button></div><div class="modal-body"><div class="review-resolver-note"><strong>${esc(item.categoryLabel || "待复核")}</strong><span>${esc(reviewIssueText(item))}</span><small>${esc(profileHint)}</small></div>${comparison}${activityScope}${rawDetails ? `<div class="review-source-detail">${esc(rawDetails)}</div>` : ""}<form id="review-customer-form"><div class="field"><label>关联到系统客户</label><div class="review-search-row"><input id="review-customer-search" value="${esc(initial?.customerName || reviewName(item))}" placeholder="搜索姓名、TW编号或手机号"><button class="secondary-btn" type="submit">搜索客户</button></div></div></form><div id="review-customer-results" class="review-customer-results">${customerOptions}</div>${item.profile === "hongan_activity" ? `<div class="field review-advisor-field"><label for="review-advisor">准备写入的港安顾问</label><input id="review-advisor" value="${esc(item.targetAdvisor || (item.advisors || [""])[0] || "")}" placeholder="填写港安顾问姓名"></div>` : ""}</div><div class="modal-footer"><button class="secondary-btn" type="button" id="review-keep">保留系统值并完成复核</button><button class="secondary-btn" type="button" data-close>取消</button><button class="primary-btn" type="button" id="review-apply">${item.profile === "hongan_activity" ? "采用导入值" : item.canApply ? "确认关联并写入" : "完成复核"}</button></div></div>`);
  let selectedId = initial?.customerId || "";
  const results = document.querySelector("#review-customer-results");
  const selectResult = (button) => { selectedId = button.dataset.reviewCustomer; results.querySelectorAll(".review-customer-option").forEach((node) => node.classList.toggle("selected", node === button)); };
  const renderResults = (rows) => { results.innerHTML = rows.length ? rows.map((row) => `<button type="button" class="review-customer-option ${selectedId === row.id ? "selected" : ""}" data-review-customer="${esc(row.id)}"><span><strong>${esc(row.name || row.wechat_nickname || "未命名")}</strong><small>${esc(row.tw_code || row.customer_code || "无 TW 编号")}</small></span></button>`).join("") : `<span class="hint">没有找到可见客户。</span>`; results.querySelectorAll("[data-review-customer]").forEach((button) => button.addEventListener("click", () => selectResult(button))); };
  results.querySelectorAll("[data-review-customer]").forEach((button) => button.addEventListener("click", () => selectResult(button)));
  document.querySelector("#review-customer-form")?.addEventListener("submit", async (event) => { event.preventDefault(); const query = document.querySelector("#review-customer-search").value.trim(); if (!query) return; try { const data = await api(`/api/customers?search=${encodeURIComponent(query)}&page=1&pageSize=20`); renderResults(data.items || []); } catch (err) { toast(err.message); } });
  document.querySelector("#review-keep")?.addEventListener("click", () => resolveImportReview(item.id, "keep", selectedId, ""));
  document.querySelector("#review-apply")?.addEventListener("click", () => { if (!item.canApply) { resolveImportReview(item.id, "keep", selectedId, "已在客户表完成人工检查"); return; } if (!selectedId) { toast("请先选择要关联的客户"); return; } const advisor = document.querySelector("#review-advisor")?.value.trim() || ""; if (item.profile === "hongan_activity" && !advisor) { toast("请填写本次要采用的港安顾问"); return; } resolveImportReview(item.id, "apply", selectedId, advisor); });
}

async function resolveImportReview(reviewId, action, customerId = "", honganAdvisor = "") { try { await api(`/api/import-reviews/${encodeURIComponent(reviewId)}/resolve`, {method:"POST", body: JSON.stringify({action, customerId: customerId || null, honganAdvisor: honganAdvisor || null})}); closeModal(); const message = action === "apply" ? "已采用导入值并写入" : action === "keep" ? "已保留系统值并完成复核" : "已暂不处理"; toast(message); await renderImportReviews(document.querySelector("#content")); } catch (err) { toast(err.message); } }
