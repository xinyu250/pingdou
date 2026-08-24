const appEl = document.querySelector('#app');
const modalRoot = document.querySelector('#modal-root');
const toastRoot = document.querySelector('#toast-root');

const state = {
  session: null,
  csrf: '',
  page: 'dashboard',
  data: {},
  authMode: 'login',
  inventorySeries: '全部',
  inventorySearch: '',
  inventorySort: 'default',
  blueprintStatus: '全部',
  blueprintSearch: '',
  selectedBlueprints: new Set(),
  generatorResult: null,
  generatorFile: null,
  generatorBusy: false,
  generatorMode: 'legend',
  generatorSaving: false,
  generatorSaveKey: '',
  blueprintRecognition: null,
  detail: null,
  detailProgress: null,
  startedAt: Date.now(),
};

const navItems = [
  ['dashboard', '⌂', '总览'],
  ['inventory', '◫', '豆子库存'],
  ['workbench', '⇄', '出入库'],
  ['blueprints', '▦', '我的图纸'],
  ['generator', '✦', '智能制图'],
  ['stats', '↗', '数据统计'],
  ['settings', '⚙', '账户设置'],
];

const pageMeta = {
  dashboard: ['总览', '今天的库存和制作安排'],
  inventory: ['豆子库存', '随时掌握每个色号的余量'],
  workbench: ['出入库', '记录每一次入库、出库和库存调整'],
  blueprints: ['我的图纸', '集中管理图纸、用豆清单和制作进度'],
  generator: ['智能制图', '上传图纸，快速整理色号与用豆数量'],
  stats: ['数据统计', '查看库存变化和常用色号'],
  settings: ['账户设置', '管理账号、库存规则和数据'],
};

const esc = (value = '') => String(value)
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
const number = value => Number(value || 0).toLocaleString('zh-CN');
const when = value => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '';
const operationName = op => ({checkin:'入库', checkout:'出库', set:'盘点', undo:'撤销', initialize:'初始化', clear:'清空'})[op] || op;

function toast(message, type = 'success') {
  const node = document.createElement('div');
  node.className = `toast ${type === 'error' ? 'error' : ''}`;
  node.textContent = message;
  toastRoot.append(node);
  setTimeout(() => node.remove(), 3500);
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.csrf) headers.set('X-CSRF-Token', state.csrf);
  if (options.json !== undefined) {
    headers.set('Content-Type', 'application/json');
    options.body = JSON.stringify(options.json);
  }
  const response = await fetch(url, {...options, headers, credentials: 'same-origin'});
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401 && state.session?.authenticated) {
      state.session = {authenticated:false, user:null};
      renderAuth();
    }
    throw new Error(payload?.error || `请求失败（${response.status}）`);
  }
  return payload;
}

async function optimizeUpload(file) {
  if (!file || !file.type?.startsWith('image/')) return file;
  try {
    const bitmap=await createImageBitmap(file), maxSide=1600;
    const scale=Math.min(1,maxSide/Math.max(bitmap.width,bitmap.height));
    if (scale===1 && file.size<900000) { bitmap.close(); return file; }
    const canvas=document.createElement('canvas');
    canvas.width=Math.max(1,Math.round(bitmap.width*scale)); canvas.height=Math.max(1,Math.round(bitmap.height*scale));
    canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height); bitmap.close();
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/webp',.88));
    return blob ? new File([blob],file.name.replace(/\.[^.]+$/,'.webp'),{type:'image/webp'}) : file;
  } catch (_error) { return file; }
}

function loadingPage() {
  return `<div class="panel"><div class="empty"><strong>正在加载</strong><span class="muted">豆子排队中，请稍候…</span></div></div>`;
}

function renderAuth() {
  const resetToken = new URLSearchParams(location.search).get('reset');
  if (resetToken) state.authMode = 'reset';
  const forms = {
    login: `
      <form id="login-form">
        <label class="field"><span>邮箱</span><input type="email" name="email" autocomplete="email" required placeholder="you@example.com"></label>
        <label class="field"><span>密码</span><input type="password" name="password" autocomplete="current-password" required placeholder="输入密码"></label>
        <button class="btn btn-primary btn-block" type="submit">登录豆仓</button>
        <p class="text-right"><button type="button" class="link-button" data-action="auth-mode" data-mode="forgot">忘记密码？</button></p>
        <div class="auth-divider"><span>或者</span></div>
        <button class="btn btn-secondary btn-block guest-button" type="button" data-action="guest-login">直接以游客身份体验</button>
        <p class="guest-hint">无需注册，点击后直接进入游客模式；操作与正式账号互不影响。</p>
      </form>`,
    register: `
      <form id="register-form">
        <label class="field"><span>怎么称呼你</span><input type="text" name="username" maxlength="40" required placeholder="豆豆"></label>
        <label class="field"><span>邮箱</span><input type="email" name="email" autocomplete="email" required placeholder="you@example.com"></label>
        <label class="field"><span>密码</span><input type="password" name="password" autocomplete="new-password" minlength="10" required placeholder="至少 10 位"></label>
        <button class="btn btn-primary btn-block" type="submit">创建云端豆仓</button>
        <p class="muted">注册即创建独立库存空间，其他用户无法查看或修改。</p>
      </form>`,
    forgot: `
      <form id="forgot-form">
        <h2>找回密码</h2><p class="muted">输入注册邮箱，我们会发送 30 分钟有效的重置链接。</p>
        <label class="field"><span>邮箱</span><input type="email" name="email" required></label>
        <button class="btn btn-primary btn-block" type="submit">发送重置链接</button>
        <p><button type="button" class="link-button" data-action="auth-mode" data-mode="login">← 返回登录</button></p>
      </form>`,
    reset: `
      <form id="reset-form">
        <h2>设置新密码</h2><p class="muted">新密码至少需要 10 位。</p>
        <input type="hidden" name="token" value="${esc(resetToken || '')}">
        <label class="field"><span>新密码</span><input type="password" name="password" minlength="10" required></label>
        <button class="btn btn-primary btn-block" type="submit">保存新密码</button>
      </form>`,
  };
  const showTabs = ['login','register'].includes(state.authMode);
  appEl.innerHTML = `
    <div class="auth-layout">
      <section class="auth-showcase">
        <div class="auth-content">
          <div class="auth-brand"><div class="brand-mark">豆</div><div><strong>豆仓 Pro</strong><small>MARD 221 WORKSPACE</small></div></div>
          <h1>让每一颗豆，都有迹可循。</h1>
          <p>从库存、图纸到智能制图和补豆建议，一套工作台完成整个拼豆流程。</p>
          <div class="feature-pills"><span>221 色库存</span><span>智能识图</span><span>逐格进度</span><span>云端同步</span></div>
        </div>
        <div class="auth-foot">网页版 · 手机、平板、电脑均可使用 · <a href="/privacy" target="_blank">隐私政策</a> · <a href="/terms" target="_blank">用户协议</a></div>
      </section>
      <section class="auth-panel">
        <div class="auth-card">
          ${showTabs ? `<h2>${state.authMode === 'login' ? '欢迎回来' : '建立你的豆仓'}</h2><p class="muted">安全登录后，所有操作只属于你的账号。</p>
          <div class="auth-tabs"><button data-action="auth-mode" data-mode="login" class="${state.authMode === 'login' ? 'active':''}">登录</button><button data-action="auth-mode" data-mode="register" class="${state.authMode === 'register' ? 'active':''}">注册</button></div>` : ''}
          ${forms[state.authMode]}
        </div>
      </section>
    </div>`;
}

function shell(content) {
  const user = state.session.user;
  const [title, subtitle] = pageMeta[state.page];
  const nav = navItems.map(([key, icon, label]) => `<button class="nav-item ${state.page === key ? 'active':''}" data-action="navigate" data-page="${key}"><span>${icon}</span><span>${label}</span></button>`).join('');
  const mobile = navItems.slice(0,5).map(([key, icon, label]) => `<button class="${state.page === key ? 'active':''}" data-action="navigate" data-page="${key}"><span>${icon}</span>${label}</button>`).join('');
  appEl.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="sidebar-brand"><div class="brand-mark">豆</div><div><strong>豆仓 Pro</strong><small>MARD 221 WORKSPACE</small></div></div>
        <nav class="nav-list">${nav}</nav>
        <div class="sidebar-user"><strong>${esc(user.isGuest ? '游客模式' : user.username)}</strong><small>${user.isGuest ? '独立临时数据 · 退出后清理' : esc(user.email)}</small><button class="btn btn-sm btn-secondary" data-action="logout">退出登录</button></div>
      </aside>
      <div class="main-area">
        <header class="topbar"><div><h1>${title}</h1><p>${subtitle}</p></div><div class="button-row">${state.page==='generator'?'':`<button class="btn btn-secondary" data-action="navigate" data-page="generator">✦ 智能制图</button>`}<button class="btn btn-primary" data-action="open-transaction" data-operation="checkin">＋ 入库</button></div></header>
        <main class="page">${content}</main>
      </div>
      <nav class="mobile-nav">${mobile}</nav>
    </div>`;
}

async function navigate(page) {
  state.page = page;
  shell(loadingPage());
  try {
    if (page === 'dashboard') state.data.dashboard = await api('/api/dashboard');
    if (page === 'inventory') state.data.inventory = (await api('/api/inventory')).items;
    if (page === 'workbench') {
      const [history, restock] = await Promise.all([api('/api/inventory/history?limit=120'), api('/api/inventory/restock?target=1000')]);
      state.data.history = history.items; state.data.restock = restock;
    }
    if (page === 'blueprints') state.data.blueprints = (await api('/api/blueprints')).items;
    if (page === 'stats') state.data.stats = await api('/api/stats');
    if (page === 'settings') state.data.settings = await api('/api/settings');
    renderPage();
  } catch (error) {
    shell(`<div class="notice danger">${esc(error.message)}</div>`);
  }
}

function renderPage() {
  const renderers = {dashboard: renderDashboard, inventory: renderInventory, workbench: renderWorkbench,
    blueprints: renderBlueprints, generator: renderGenerator, stats: renderStats, settings: renderSettings};
  shell(renderers[state.page]());
  if (state.page === 'generator' && state.generatorResult) requestAnimationFrame(() => drawPattern(document.querySelector('#generator-canvas'), state.generatorResult));
}

function metric(label, value, tone = '') {
  return `<div class="metric ${tone}"><small>${label}</small><strong>${number(value)}</strong></div>`;
}

function renderDashboard() {
  const d = state.data.dashboard || {};
  const transactions = (d.recentTransactions || []).map(row => `<tr><td class="code-cell">${esc(row.code)}</td><td class="${row.delta < 0 ? 'danger-text':'success-text'}">${row.delta > 0 ? '+':''}${number(row.delta)}</td><td>${esc(row.remark || operationName(row.operation))}</td><td class="muted">${when(row.createdAt)}</td></tr>`).join('');
  const blueprints = (d.recentBlueprints || []).map(bp => `<div class="blueprint-card" data-action="blueprint-detail" data-id="${bp.id}"><div class="blueprint-image">${bp.imageUrl ? `<img src="${bp.imageUrl}" alt="">`:'暂无预览'}</div><div class="blueprint-body"><h3>${esc(bp.name)}</h3><span class="badge ${bp.status === '已拼' ? 'ok':'warn'}">${esc(bp.status)}</span><div class="blueprint-meta"><span>${bp.colorCount} 色</span><span>${number(bp.totalBeads)} 粒</span></div></div></div>`).join('');
  return `
    <div class="page-head"><div><h2>${state.session.user.isGuest?'欢迎体验豆仓':`你好，${esc(state.session.user.username)}`}</h2><p>今天的库存、图纸和制作进度一目了然。</p></div><div class="button-row"><button class="btn btn-secondary" data-action="navigate" data-page="blueprints">查看图纸</button><button class="btn btn-primary" data-action="open-blueprint-editor">上传图纸</button></div></div>
    <div class="metrics">${metric('豆子总库存', d.totalBeads)}${metric('有库存色号', d.trackedColors)}${metric('需要补豆', d.lowColors, 'danger')}${metric('待拼图纸', d.todoBlueprints, 'accent')}</div>
    <div class="grid-2">
      <section class="panel"><div class="panel-head"><h3>最近流水</h3><button class="link-button" data-action="navigate" data-page="workbench">全部记录</button></div><div class="table-wrap">${transactions ? `<table><thead><tr><th>色号</th><th>变化</th><th>备注</th><th>时间</th></tr></thead><tbody>${transactions}</tbody></table>`:`<div class="empty"><strong>还没有流水</strong>从第一次入库开始记录吧。</div>`}</div></section>
      <section class="panel"><div class="panel-head"><h3>最近图纸</h3><button class="link-button" data-action="navigate" data-page="blueprints">管理图纸</button></div><div class="panel-body"><div class="blueprint-grid">${blueprints || `<div class="empty"><strong>还没有图纸</strong>上传图片或使用智能制图。</div>`}</div></div></section>
    </div>`;
}

function inventoryVisibleItems() {
  let items = [...(state.data.inventory || [])];
  if (state.inventorySeries !== '全部') items = items.filter(item => item.series === state.inventorySeries);
  if (state.inventorySearch) items = items.filter(item => item.id.toLowerCase().includes(state.inventorySearch.toLowerCase()));
  if (state.inventorySort === 'asc') items.sort((a,b) => a.quantity - b.quantity);
  if (state.inventorySort === 'desc') items.sort((a,b) => b.quantity - a.quantity);
  if (state.inventorySort === 'low') items = items.filter(item => item.status !== '库存充足').sort((a,b) => a.quantity-b.quantity);
  return items;
}

function renderInventory() {
  const all = state.data.inventory || [];
  const items = inventoryVisibleItems();
  const series = ['全部', ...new Set(all.map(item => item.series))];
  const chips = series.map(value => `<button class="chip ${state.inventorySeries === value ? 'active':''}" data-action="inventory-series" data-value="${esc(value)}">${esc(value)}</button>`).join('');
  const cards = items.map(item => `<button class="color-card" style="--swatch:${item.hex}" data-action="open-transaction" data-code="${item.id}" data-operation="set"><div class="color-card-top"><span class="code-cell"><span class="swatch"></span>${item.id}</span><span class="badge ${item.status === '库存充足' ? 'ok' : item.status === '欠库存' ? 'danger':'warn'}">${item.status}</span></div><strong>${number(item.quantity)}</strong><small>预警线 ${number(item.threshold)}</small></button>`).join('');
  return `
    <div class="page-head"><div><h2>我的豆仓</h2><p>共 ${number(all.reduce((sum,item)=>sum+item.quantity,0))} 粒 · 当前显示 ${items.length} 个色号</p></div><div class="inventory-actions"><button class="btn btn-quiet" data-action="navigate" data-page="workbench">查看补豆清单</button><button class="btn btn-secondary" data-action="open-transaction" data-operation="checkout">记录出库</button><button class="btn btn-primary" data-action="open-transaction" data-operation="checkin">补豆入库</button></div></div>
    <div class="toolbar"><input class="search" id="inventory-search" type="text" placeholder="搜索色号，如 H2" value="${esc(state.inventorySearch)}"><select id="inventory-sort"><option value="default">默认排序</option><option value="asc" ${state.inventorySort==='asc'?'selected':''}>库存由少到多</option><option value="desc" ${state.inventorySort==='desc'?'selected':''}>库存由多到少</option><option value="low" ${state.inventorySort==='low'?'selected':''}>只看需补豆</option></select></div>
    <div class="chips" style="margin-bottom:16px">${chips}</div>
    <div class="inventory-grid">${cards || `<div class="empty">没有匹配的色号</div>`}</div>`;
}

function renderWorkbench() {
  const restock = state.data.restock || {items:[]};
  const history = state.data.history || [];
  const restockRows = restock.items.slice(0,50).map(item => `<tr><td class="code-cell"><span class="swatch" style="--swatch:${item.hex}"></span>${item.id}</td><td>${number(item.current)}</td><td>${number(item.needed)}</td><td>${item.grams}g</td></tr>`).join('');
  const groups = [];
  const seen = new Set();
  history.forEach(row => { if (!seen.has(row.batchId)) {groups.push(row); seen.add(row.batchId);} });
  const historyRows = groups.slice(0,60).map(row => `<tr><td><span class="badge ${row.delta<0?'warn':'ok'}">${operationName(row.operation)}</span></td><td>${esc(row.code)} 等</td><td>${esc(row.remark || '-')}</td><td class="muted">${when(row.createdAt)}</td><td><button class="btn btn-sm btn-secondary" data-action="undo" data-batch="${row.batchId}" ${row.undone || row.operation==='undo' ? 'disabled':''}>撤销批次</button></td></tr>`).join('');
  return `
    <div class="page-head"><div><h2>拼豆工作台</h2><p>支持手动录入、批量粘贴和 CSV 导入。</p></div><div class="button-row"><button class="btn btn-secondary" data-action="open-transaction" data-operation="checkout">记录出库</button><button class="btn btn-primary" data-action="open-transaction" data-operation="checkin">补豆入库</button></div></div>
    <div class="grid-3" style="margin-bottom:18px">
      <button class="panel panel-body text-right" data-action="open-transaction" data-operation="checkout"><strong>扣库存</strong><p class="muted">手动、批量或 CSV 记录出库</p><span class="btn btn-accent">开始出库 →</span></button>
      <button class="panel panel-body text-right" data-action="navigate" data-page="generator"><strong>智能识图</strong><p class="muted">识别图片色号并生成图纸</p><span class="btn btn-soft">打开制图 →</span></button>
      <button class="panel panel-body text-right" data-action="open-transaction" data-operation="checkin"><strong>加库存</strong><p class="muted">补豆、套装和批量入库</p><span class="btn btn-primary">开始入库 →</span></button>
    </div>
    <div class="grid-2">
      <section class="panel"><div class="panel-head"><h3>补豆清单</h3><div class="button-row"><span class="badge warn">${restock.totalColors || 0} 色 · ${restock.totalGrams || 0}g</span><button class="btn btn-sm btn-secondary" data-action="copy-restock">复制口令</button></div></div><div class="table-wrap">${restockRows ? `<table><thead><tr><th>色号</th><th>现有</th><th>需补</th><th>建议克数</th></tr></thead><tbody>${restockRows}</tbody></table>`:'<div class="empty"><strong>库存很充足</strong>目前没有低于预警线的色号。</div>'}</div></section>
      <section class="panel"><div class="panel-head"><h3>出入库流水</h3><a href="/api/inventory/export.csv" class="btn btn-sm btn-secondary">导出库存</a></div><div class="table-wrap">${historyRows ? `<table><thead><tr><th>类型</th><th>色号</th><th>备注</th><th>时间</th><th></th></tr></thead><tbody>${historyRows}</tbody></table>`:'<div class="empty">暂无流水</div>'}</div></section>
    </div>`;
}

function visibleBlueprints() {
  return (state.data.blueprints || []).filter(bp => (state.blueprintStatus === '全部' || bp.status === state.blueprintStatus)
    && (!state.blueprintSearch || bp.name.toLowerCase().includes(state.blueprintSearch.toLowerCase())));
}

function renderBlueprints() {
  const all = state.data.blueprints || [], items = visibleBlueprints();
  const cards = items.map(bp => `<article class="blueprint-card" style="position:relative"><input class="blueprint-select" type="checkbox" data-action="select-blueprint" data-id="${bp.id}" ${state.selectedBlueprints.has(bp.id)?'checked':''}><div class="blueprint-image" data-action="blueprint-detail" data-id="${bp.id}">${bp.imageUrl ? `<img src="${bp.imageUrl}" alt="${esc(bp.name)}">`:'无图纸预览'}</div><div class="blueprint-body"><h3>${esc(bp.name)}</h3><div class="blueprint-meta"><span class="badge ${bp.status==='已拼'||bp.status==='已发布'?'ok':'warn'}">${bp.status}</span><span class="badge">${esc(bp.tag)}</span><span class="badge">${esc(bp.folder)}</span></div><p class="muted">${bp.colorCount} 色 · ${number(bp.totalBeads)} 粒 · ${bp.craftMinutes || 0} 分钟</p><div class="button-row"><button class="btn btn-sm btn-secondary" data-action="blueprint-detail" data-id="${bp.id}">查看进度</button><button class="btn btn-sm btn-accent" data-action="consume-blueprint" data-id="${bp.id}">记录出库</button></div></div></article>`).join('');
  const statuses = ['全部','待拼','拼制中','已拼','已发布'].map(value => `<button class="chip ${state.blueprintStatus===value?'active':''}" data-action="blueprint-status" data-value="${value}">${value}</button>`).join('');
  return `
    <div class="page-head"><div><h2>我的图纸</h2><p>${all.length} 张图纸 · ${all.filter(bp=>bp.status==='已拼'||bp.status==='已发布').length} 张已完成</p></div><div class="button-row"><button class="btn btn-secondary" data-action="calculate-blueprints" ${state.selectedBlueprints.size?'':'disabled'}>消耗计算（${state.selectedBlueprints.size}）</button><button class="btn btn-primary" data-action="open-blueprint-editor">＋ 上传图纸</button></div></div>
    <div class="toolbar"><div class="chips">${statuses}</div><input id="blueprint-search" class="search" type="text" placeholder="搜索图纸名称" value="${esc(state.blueprintSearch)}"></div>
    <div class="blueprint-grid">${cards || `<div class="empty"><strong>没有找到图纸</strong>上传已有图纸，或先用智能制图生成。</div>`}</div>`;
}

function renderGenerator() {
  const r = state.generatorResult;
  const total = r ? r.items.reduce((sum,item)=>sum+Number(item.quantity||0),0) : 0;
  const isLegend = r?.recognitionMode === 'legend';
  const resultRows = r ? r.items.map((item,index) => `<tr><td class="code-cell"><span class="swatch" style="--swatch:${item.hex}"></span><strong>${item.id}</strong></td><td><input class="generator-qty" data-index="${index}" type="number" min="0" value="${item.quantity}" aria-label="${item.id} 数量"></td><td>${(item.quantity/Math.max(1,total)*100).toFixed(1)}%</td></tr>`).join('') : '';
  return `
    <div class="page-head"><div><h2>智能制图</h2><p>上传图纸后，系统会整理色号、数量和可编辑预览。</p></div>${r ? `<div class="button-row">${isLegend?'':`<button class="btn btn-secondary" data-action="export-pattern">下载图纸</button>`}<button class="btn btn-primary" data-action="save-generator">保存到我的图纸</button></div>`:''}</div>
    <div class="generator-layout">
      <section class="panel generator-control"><div class="panel-head"><h3>上传并识别</h3><span class="badge ok">MARD 221</span></div><div class="panel-body">
        <form id="generator-form">
          <fieldset class="recognition-picker"><legend>这张图纸是哪一种？</legend><label><input type="radio" name="recognitionMode" value="legend" ${state.generatorMode==='legend'?'checked':''}><span><strong>带色号图例</strong><small>直接读取图例里的色号和数量</small></span></label><label><input type="radio" name="recognitionMode" value="pattern" ${state.generatorMode==='pattern'?'checked':''}><span><strong>只有图案</strong><small>提取主体后识别所用颜色</small></span></label></fieldset>
          <input type="hidden" name="columns" value="48"><input type="hidden" name="rows" value="0"><input type="hidden" name="maxColors" value="0"><input type="hidden" name="cropMode" value="subject"><input type="hidden" name="cropMargin" value="8">
          <label class="dropzone ${state.generatorBusy?'is-busy':''}"><input class="file-overlay" type="file" name="image" accept="image/jpeg,image/png,image/webp" required ${state.generatorBusy?'disabled':''}>${state.generatorBusy?'<span class="spinner dark"></span><strong>正在识别图纸</strong><small>带图例的图纸首次识别可能需要一些时间</small>':'<span class="file-cta">选择图纸图片</span><strong>也可以直接拖到这里</strong><small id="generator-file-name">选择后会自动开始识别 · 最大 '+(state.session.maxUploadMb || 8)+'MB</small>'}</label>
          <button class="hidden" type="submit">开始识别</button>
        </form>
        <div class="generator-help">${r ? (isLegend?'已读取图例中的色号和数量，你可以在右侧直接修改。':'已提取图案主体并匹配色号，你可以在右侧调整数量。') : '识别完成后会先让你确认，不会自动修改库存。'}</div>
      </div></section>
      <section class="panel generator-result"><div class="panel-head"><h3>${r ? `${isLegend?'图例识别完成':`${r.columns}×${r.rows} 图案`} · ${r.items.length} 色`:'识别结果'}</h3>${r?`<span class="badge">${number(total)} 粒</span>`:''}</div><div class="panel-body">
        ${r ? `${isLegend?'<div class="result-summary"><strong>已读取图例</strong><span>请核对色号和数量后保存</span></div>':`<div class="pattern-stage generator-preview"><canvas id="generator-canvas" class="pattern-canvas" aria-label="拼豆图纸预览"></canvas></div>`}<div class="table-wrap pattern-result-list"><table><colgroup><col style="width:30%"><col style="width:46%"><col style="width:24%"></colgroup><thead><tr><th>色号</th><th>数量</th><th>占比</th></tr></thead><tbody>${resultRows}</tbody></table></div>`:`<div class="empty generator-empty"><strong>等待上传图纸</strong><span>识别出的色号和数量会显示在这里。</span></div>`}
      </div></section>
    </div>`;
}

function renderStats() {
  const d = state.data.stats || {};
  const daily = d.daily || [];
  const max = Math.max(1, ...daily.flatMap(item => [item.in,item.out]));
  const bars = daily.map(item => `<div class="bar-column" title="${item.date} 入 ${item.in} / 出 ${item.out}"><span class="bar" style="height:${Math.max(2,item.in/max*100)}%"></span><span class="bar out" style="height:${Math.max(2,item.out/max*100)}%"></span></div>`).join('');
  const top = (d.topConsumption || []).map((item,index) => `<tr><td>${index+1}</td><td class="code-cell"><span class="swatch" style="--swatch:${item.hex}"></span>${item.id}</td><td>${number(item.quantity)}</td><td><div class="progress"><span style="width:${item.quantity/Math.max(1,d.topConsumption[0]?.quantity)*100}%"></span></div></td></tr>`).join('');
  return `<div class="page-head"><div><h2>数据与统计</h2><p>每一笔数据都来自可追溯的库存流水。</p></div><a class="btn btn-secondary" href="/api/inventory/export.csv">导出库存 CSV</a></div>
    <div class="metrics">${metric('累计入库',d.totalIn)}${metric('累计出库',d.totalOut,'accent')}${metric('当前库存',d.current)}${metric('图纸数量',d.blueprintCount)}</div>
    <div class="grid-2"><section class="panel"><div class="panel-head"><h3>近 30 天出入库</h3><div><span class="badge ok">入库</span> <span class="badge warn">出库</span></div></div><div class="panel-body">${bars?`<div class="bar-chart">${bars}</div>`:'<div class="empty">暂无趋势数据</div>'}</div></section><section class="panel"><div class="panel-head"><h3>高频消耗色号</h3><span class="badge">Top 20</span></div><div class="table-wrap">${top?`<table><thead><tr><th>#</th><th>色号</th><th>累计出库</th><th>占比</th></tr></thead><tbody>${top}</tbody></table>`:'<div class="empty">暂无消耗数据</div>'}</div></section></div>`;
}

function renderSettings() {
  const d = state.data.settings || {user:state.session.user, production:{}};
  const prod = d.production || {};
  return `<div class="page-head"><div><h2>账户与数据</h2><p>管理个人信息、库存规则和数据安全。</p></div></div>
    <div class="grid-2"><section class="panel"><div class="panel-head"><h3>个人设置</h3></div><div class="panel-body"><form id="settings-form"><label class="field"><span>用户名</span><input type="text" name="username" maxlength="40" value="${esc(d.user.username)}" required></label><label class="field"><span>默认低库存预警线</span><input type="number" name="lowThreshold" min="0" value="${d.user.settings.lowThreshold}" required></label><button class="btn btn-primary" type="submit">保存设置</button></form></div></section>
    <section class="panel"><div class="panel-head"><h3>数据状态</h3></div><div class="panel-body"><div class="notice ${prod.durableDatabase?'':'danger'}"><strong>${prod.durableDatabase?'数据已安全保存':'数据存储需要检查'}</strong><br>${prod.durableDatabase?'库存和图纸会随账号持续保留。':'请联系管理员完成在线数据存储配置。'}</div><p><span class="badge ${prod.emailConfigured?'ok':'warn'}">${prod.emailConfigured?'可通过邮件找回密码':'密码找回邮件暂不可用'}</span></p><p class="muted">当前站点：${esc(prod.appUrl || location.origin)}</p></div></section></div>
    <section class="panel" style="margin-top:18px"><div class="panel-head"><h3>数据管理</h3></div><div class="panel-body">${d.user.isGuest?'<div class="notice"><strong>当前为游客模式</strong><br>你可以使用库存、图纸和智能制图；退出后临时数据会自动清理。</div>':`<div class="setting-section"><h3>库存初始化与导出</h3><p class="muted">给 221 个色号设置统一初始数量，操作会留下完整流水。</p><div class="button-row"><button class="btn btn-secondary" data-action="initialize-inventory">快速设置库存</button><a class="btn btn-secondary" href="/api/inventory/export.csv">导出库存 CSV</a><button class="btn btn-secondary" data-action="export-account">导出完整账户数据</button>${d.user.isAdmin?'<button class="btn btn-soft" data-action="admin-import">迁移旧版数据</button>':''}</div></div><div class="setting-section"><h3 class="danger-text">危险操作</h3><p class="muted">清空会保留审计流水；注销账号会永久删除库存、流水和图纸。</p><div class="button-row"><button class="btn btn-danger" data-action="clear-inventory">清空全部库存</button><button class="btn btn-danger" data-action="delete-account">注销账号</button></div></div>`}</div></section>`;
}

function openModal(title, body, foot = '', large = false) {
  modalRoot.innerHTML = `<div class="modal-backdrop" data-action="modal-backdrop"><section class="modal ${large?'large':''}" role="dialog" aria-modal="true"><header class="modal-head"><h2>${title}</h2><button class="icon-btn" data-action="close-modal" aria-label="关闭">×</button></header><div class="modal-body">${body}</div>${foot?`<footer class="modal-foot">${foot}</footer>`:''}</section></div>`;
}

function closeModal() { modalRoot.innerHTML = ''; state.detail = null; state.detailProgress = null; state.blueprintRecognition = null; }

function parseItems(text) {
  const map = new Map();
  String(text || '').split(/\r?\n/).forEach(line => {
    const [rawCode, rawQty] = line.trim().split(/[,，:\s]+/);
    const code = String(rawCode || '').trim().toUpperCase();
    const quantity = Number.parseInt(rawQty, 10);
    if (code && Number.isFinite(quantity) && quantity >= 0) map.set(code, quantity);
  });
  return [...map].map(([id,quantity]) => ({id,quantity}));
}

function openTransaction(operation = 'checkin', code = '') {
  const labels = {checkin:'补豆入库',checkout:'记录出库',set:'库存盘点'};
  const example = code ? `${code},0` : 'A1,1000\nA2,500\nH2,200';
  openModal(labels[operation], `<form id="transaction-form"><input type="hidden" name="operation" value="${operation}"><label class="field"><span>色号与数量</span><textarea name="items" required placeholder="每行一个：色号,数量">${esc(example)}</textarea><small>${operation==='set'?'盘点会把库存直接设置为目标数量':'支持从 CSV 复制粘贴，重复色号会自动合并'}</small></label><label class="field"><span>备注</span><input type="text" name="remark" maxlength="160" placeholder="如：8月补豆、制作生日图纸"></label><label class="field"><span>导入 CSV</span><input id="transaction-csv" type="file" accept=".csv,text/csv"></label></form>`, `<button class="btn btn-secondary" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="submit-transaction">确认${labels[operation]}</button>`);
}

function blueprintEditor(bp = null) {
  const items = bp?.items?.map(item => `${item.id},${item.quantity}`).join('\n') || '';
  state.blueprintRecognition = bp?.pattern || {};
  openModal(bp ? '编辑图纸' : '上传图纸', `<form id="blueprint-form" data-id="${bp?.id || ''}" data-image-url="${esc(bp?.imageUrl || '')}"><textarea name="pattern" hidden>${esc(JSON.stringify(bp?.pattern || {}))}</textarea><div class="form-grid"><label class="field"><span>图纸名称</span><input type="text" name="name" maxlength="80" value="${esc(bp?.name || '')}" required></label><label class="field"><span>状态</span><select name="status">${['待拼','拼制中','已拼','已发布'].map(s=>`<option ${bp?.status===s?'selected':''}>${s}</option>`).join('')}</select></label><label class="field"><span>标签</span><input type="text" name="tag" maxlength="40" value="${esc(bp?.tag || '默认')}"></label><label class="field"><span>文件夹</span><input type="text" name="folder" maxlength="40" value="${esc(bp?.folder || '未分类')}"></label><label class="field"><span>来源链接</span><input type="url" name="source" value="${esc(bp?.source || '')}" placeholder="可选"></label><label class="field"><span>制作时长（分钟）</span><input type="number" name="craftMinutes" min="0" value="${bp?.craftMinutes || 0}"></label></div><label class="field" style="margin-top:16px"><span>${bp?'替换图纸图片':'图纸图片'}</span><input type="file" name="image" accept="image/jpeg,image/png,image/webp"></label><section class="recognize-box"><div><strong>重新识别色号</strong><p class="muted">选择图纸类型后，系统会重新整理色号和数量。</p></div><div class="recognize-controls"><select name="recognizeMode" aria-label="图纸类型"><option value="legend">带色号图例</option><option value="pattern">只有图案</option></select><button class="btn btn-secondary" type="button" data-action="reanalyze-blueprint">重新识别</button></div><div id="recognize-preview" class="recognize-preview">${bp?.pattern?.cells?.length?'<canvas id="recognize-canvas" class="pattern-canvas"></canvas>':'识别结果将在这里预览'}</div></section><label class="field"><span>用豆明细</span><textarea name="items" placeholder="每行一个：A1,120">${esc(items)}</textarea></label></form>`, `<button class="btn btn-secondary" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="submit-blueprint">保存图纸</button>`, true);
  if (bp?.pattern?.cells?.length) requestAnimationFrame(()=>drawPattern(document.querySelector('#recognize-canvas'),bp.pattern));
}

function drawPattern(canvas, pattern, doneCells = new Set()) {
  if (!canvas || !pattern?.cells?.length) return;
  const columns = pattern.columns, rows = pattern.rows;
  const cell = columns <= 52 ? 14 : columns <= 80 ? 9 : 6;
  canvas.width = columns * cell; canvas.height = rows * cell;
  canvas.dataset.cellSize = cell;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0,0,canvas.width,canvas.height);
  pattern.cells.forEach((code,index) => {
    const x = (index % columns)*cell, y = Math.floor(index/columns)*cell;
    ctx.fillStyle = pattern.hex?.[index] || '#fff'; ctx.fillRect(x,y,cell,cell);
    if (cell >= 9) { ctx.strokeStyle = 'rgba(22,33,31,.16)'; ctx.strokeRect(x+.5,y+.5,cell-1,cell-1); }
    if (doneCells.has(index)) { ctx.fillStyle='rgba(8,127,115,.62)'; ctx.fillRect(x,y,cell,cell); }
  });
}

async function showBlueprintDetail(id) {
  try {
    const bp = (await api(`/api/blueprints/${id}`)).item;
    state.detail = bp;
    state.detailProgress = {byColor:{...(bp.progress?.byColor || {})}, doneCells:new Set(bp.progress?.doneCells || [])};
    const itemRows = bp.items.map(item => `<tr><td class="code-cell">${item.id}</td><td>${number(item.quantity)}</td><td><input class="progress-color" data-code="${item.id}" type="number" min="0" max="${item.quantity}" value="${state.detailProgress.byColor[item.id] || 0}"></td></tr>`).join('');
    const done = state.detailProgress.doneCells.size, total = bp.pattern?.cells?.filter(Boolean).length || bp.totalBeads;
    openModal(esc(bp.name), `<div class="blueprint-detail-layout"><div class="blueprint-preview-column">${bp.imageUrl?`<div class="blueprint-detail-media"><img src="${bp.imageUrl}" alt="${esc(bp.name)}"></div>`:'<div class="empty">无原图</div>'}<div class="blueprint-meta"><span class="badge ${bp.status==='已拼'?'ok':'warn'}">${bp.status}</span><span class="badge">${esc(bp.tag)}</span><span class="badge">${number(bp.totalBeads)} 粒</span></div>${bp.pattern?.cells?.length?`<h3>逐格拼制进度</h3><p class="muted">点击格子标记已拼；绿色代表已完成。</p><div class="pattern-stage detail-pattern-stage"><canvas id="progress-canvas" class="pattern-canvas"></canvas></div><p>${done} / ${total} 格</p>`:''}</div><div class="blueprint-progress-panel"><h3>逐色进度</h3><div class="table-wrap detail-progress-table"><table><thead><tr><th>色号</th><th>需要</th><th>已拼</th></tr></thead><tbody>${itemRows}</tbody></table></div></div></div>`, `<button class="btn btn-danger" data-action="delete-blueprint" data-id="${bp.id}">删除</button><button class="btn btn-secondary" data-action="edit-blueprint" data-id="${bp.id}">编辑</button><button class="btn btn-secondary" data-action="share-blueprint" data-id="${bp.id}">分享</button><button class="btn btn-accent" data-action="consume-blueprint" data-id="${bp.id}">记录出库</button><button class="btn btn-primary" data-action="save-progress" data-id="${bp.id}">保存进度</button>`, true);
    requestAnimationFrame(() => drawPattern(document.querySelector('#progress-canvas'), bp.pattern, state.detailProgress.doneCells));
  } catch (error) { toast(error.message, 'error'); }
}

async function calculateSelected() {
  try {
    const result = await api('/api/blueprints/calculate', {method:'POST', json:{selections:[...state.selectedBlueprints].map(id=>({id,count:1}))}});
    const rows = result.items.map(item => `<tr><td class="code-cell"><span class="swatch" style="--swatch:${item.hex}"></span>${item.id}</td><td>${number(item.needed)}</td><td>${number(item.current)}</td><td class="${item.remain<0?'danger-text':'success-text'}">${number(item.remain)}</td><td>${item.shortage?`<span class="badge danger">缺 ${number(item.shortage)}</span>`:'<span class="badge ok">够用</span>'}</td></tr>`).join('');
    openModal('图纸消耗计算', `<div class="metrics">${metric('总消耗',result.totalNeeded)}${metric('缺货色号',result.shortageColors,'danger')}</div><div class="table-wrap"><table><thead><tr><th>色号</th><th>需要</th><th>库存</th><th>剩余</th><th>判断</th></tr></thead><tbody>${rows}</tbody></table></div>`, `<button class="btn btn-primary" data-action="close-modal">完成</button>`, true);
  } catch (error) { toast(error.message,'error'); }
}

function generatorSaveDialog() {
  if (!state.generatorResult) return;
  state.generatorSaveKey=crypto.randomUUID(); state.generatorSaving=false;
  openModal('保存到我的图纸', `<form id="generator-save-form"><label class="field"><span>图纸名称</span><input type="text" name="name" maxlength="80" required placeholder="例如：熊猫杯垫"></label><div class="form-grid"><label class="field"><span>标签</span><input type="text" name="tag" value="智能制图"></label><label class="field"><span>文件夹</span><input type="text" name="folder" value="我的图纸"></label></div><div id="generator-save-status" class="save-status">确认后会保存图片、色号和用豆数量。</div></form>`, `<button class="btn btn-secondary" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="submit-generator-save">确认保存</button>`);
}

function publicShareView(data) {
  const bp = data.item;
  appEl.innerHTML = `<div class="auth-panel" style="min-height:100vh"><article class="panel" style="width:min(900px,100%)"><div class="panel-head"><div><h2>${esc(bp.name)}</h2><p class="muted">由 ${esc(data.owner)} 分享</p></div><a class="btn btn-primary" href="/">打开豆仓</a></div><div class="panel-body"><div class="grid-2"><div>${bp.imageUrl?`<img src="${bp.imageUrl}" style="border-radius:16px;width:100%" alt="">`:''}</div><div><div class="metrics" style="grid-template-columns:repeat(2,1fr)">${metric('总用豆',bp.totalBeads)}${metric('色号数',bp.colorCount)}</div><div class="table-wrap"><table><thead><tr><th>色号</th><th>数量</th></tr></thead><tbody>${bp.items.map(item=>`<tr><td>${item.id}</td><td>${number(item.quantity)}</td></tr>`).join('')}</tbody></table></div></div></div>${bp.pattern?.cells?.length?'<div class="pattern-stage" style="margin-top:18px"><canvas id="share-canvas" class="pattern-canvas"></canvas></div>':''}</div></article></div>`;
  if (bp.pattern?.cells?.length) requestAnimationFrame(()=>drawPattern(document.querySelector('#share-canvas'),bp.pattern));
}

document.addEventListener('click', async event => {
  const target = event.target.closest('[data-action]');
  if (!target) return;
  const action = target.dataset.action;
  try {
    if (action === 'navigate') { closeModal(); await navigate(target.dataset.page); }
    if (action === 'auth-mode') { state.authMode = target.dataset.mode; renderAuth(); }
    if (action === 'guest-login') {
      target.disabled=true; target.textContent='正在进入游客模式…';
      const result=await api('/api/auth/guest',{method:'POST'});
      state.csrf=result.csrfToken; state.session={authenticated:true,user:result.user,maxUploadMb:8};
      toast('已进入游客模式'); await navigate('dashboard');
    }
    if (action === 'logout') { await api('/api/auth/logout',{method:'POST'}); state.session={authenticated:false,user:null}; renderAuth(); }
    if (action === 'close-modal') closeModal();
    if (action === 'modal-backdrop' && event.target === target) closeModal();
    if (action === 'inventory-series') { state.inventorySeries=target.dataset.value; renderPage(); }
    if (action === 'open-transaction') openTransaction(target.dataset.operation, target.dataset.code || '');
    if (action === 'submit-transaction') document.querySelector('#transaction-form')?.requestSubmit();
    if (action === 'open-blueprint-editor') blueprintEditor();
    if (action === 'submit-blueprint') document.querySelector('#blueprint-form')?.requestSubmit();
    if (action === 'reanalyze-blueprint') {
      const form=document.querySelector('#blueprint-form'), input=form.querySelector('input[name="image"]');
      let file=input.files[0];
      if (!file && form.dataset.imageUrl) {
        const response=await fetch(form.dataset.imageUrl,{credentials:'same-origin'});
        if (!response.ok) throw new Error('无法读取当前图纸图片，请重新选择图片');
        file=new File([await response.blob()],'blueprint.webp',{type:response.headers.get('content-type')||'image/webp'});
      }
      if (!file) throw new Error('请先选择一张图纸图片');
      target.disabled=true; target.textContent='识别中…';
      const fd=new FormData(); fd.set('image',file); fd.set('columns','48');
      fd.set('rows','0'); fd.set('maxColors','0'); fd.set('recognitionMode',form.elements.recognizeMode.value); fd.set('cropMode','subject'); fd.set('cropMargin','8'); fd.set('dither','false');
      const result=await api('/api/analyze',{method:'POST',body:fd}); state.blueprintRecognition=result.result;
      form.elements.pattern.value=JSON.stringify(result.result);
      form.elements.items.value=result.result.items.map(item=>`${item.id},${item.quantity}`).join('\n');
      const legend=result.result.recognitionMode==='legend';
      document.querySelector('#recognize-preview').innerHTML=`<div class="recognize-summary">${legend?'已读取图例':`${result.result.columns}×${result.result.rows} 图案`} · ${result.result.items.length} 色</div>${legend?'':'<canvas id="recognize-canvas" class="pattern-canvas"></canvas>'}`;
      if (!legend) drawPattern(document.querySelector('#recognize-canvas'),result.result); toast('色号和数量已更新，可继续手动调整');
      target.disabled=false; target.textContent='重新识别';
    }
    if (action === 'blueprint-detail') await showBlueprintDetail(target.dataset.id);
    if (action === 'blueprint-status') { state.blueprintStatus=target.dataset.value; renderPage(); }
    if (action === 'select-blueprint') {
      target.checked ? state.selectedBlueprints.add(target.dataset.id) : state.selectedBlueprints.delete(target.dataset.id);
      renderPage();
    }
    if (action === 'calculate-blueprints') await calculateSelected();
    if (action === 'consume-blueprint') {
      if (!confirm('确认按这张图纸记录出库？库存可能出现负数以便保留欠货记录。')) return;
      await api(`/api/blueprints/${target.dataset.id}/consume`,{method:'POST',json:{count:1}});
      toast('图纸消耗已写入库存流水'); closeModal(); await navigate(state.page === 'blueprints' ? 'blueprints' : 'dashboard');
    }
    if (action === 'edit-blueprint') {
      const bp = state.detail || (await api(`/api/blueprints/${target.dataset.id}`)).item;
      blueprintEditor(bp);
    }
    if (action === 'delete-blueprint') {
      if (!confirm('确定删除这张图纸？库存流水不会受到影响。')) return;
      await api(`/api/blueprints/${target.dataset.id}`,{method:'DELETE'}); toast('图纸已删除'); closeModal(); await navigate('blueprints');
    }
    if (action === 'share-blueprint') {
      const result = await api(`/api/blueprints/${target.dataset.id}/share`,{method:'POST',json:{enabled:true}});
      await navigator.clipboard.writeText(result.shareUrl); toast('只读分享链接已复制');
    }
    if (action === 'save-progress') {
      const byColor = {};
      document.querySelectorAll('.progress-color').forEach(input => { byColor[input.dataset.code]=Math.max(0,Number(input.value||0)); });
      const progress = {byColor, doneCells:[...state.detailProgress.doneCells]};
      await api(`/api/blueprints/${target.dataset.id}/progress`,{method:'PUT',json:{progress}}); toast('拼制进度已保存');
    }
    if (action === 'undo') {
      if (!confirm('撤销这一整批库存操作？系统会生成反向流水。')) return;
      await api(`/api/inventory/undo/${target.dataset.batch}`,{method:'POST'}); toast('批次已撤销'); await navigate('workbench');
    }
    if (action === 'copy-restock') {
      await navigator.clipboard.writeText(state.data.restock?.command || ''); toast('补豆口令已复制');
    }
    if (action === 'save-generator') generatorSaveDialog();
    if (action === 'submit-generator-save' && !state.generatorSaving) document.querySelector('#generator-save-form')?.requestSubmit();
    if (action === 'export-pattern') await exportPattern();
    if (action === 'initialize-inventory') {
      const quantity = prompt('请输入每个色号的初始数量：','1000');
      if (quantity === null) return;
      if (!confirm(`确认将 221 个色号统一设置为 ${quantity}？`)) return;
      await api('/api/inventory/initialize',{method:'POST',json:{quantity:Number(quantity),confirm:'INIT'}}); toast('库存已初始化'); await navigate('settings');
    }
    if (action === 'clear-inventory') {
      const text = prompt('这是危险操作。请输入 CLEAR 确认清空库存：');
      if (text !== 'CLEAR') { if (text !== null) toast('确认文本不正确','error'); return; }
      await api('/api/inventory/clear',{method:'POST',json:{confirm:'CLEAR'}}); toast('库存已清空'); await navigate('settings');
    }
    if (action === 'admin-import') adminImportDialog();
    if (action === 'submit-admin-import') document.querySelector('#admin-import-form')?.requestSubmit();
    if (action === 'export-account') await exportAccount();
    if (action === 'delete-account') deleteAccountDialog();
    if (action === 'submit-delete-account') document.querySelector('#delete-account-form')?.requestSubmit();
  } catch (error) {
    if (action === 'guest-login') renderAuth();
    if (action === 'reanalyze-blueprint') {
      target.disabled=false; target.textContent='重新识别';
    }
    toast(error.message,'error');
  }
});

document.addEventListener('input', event => {
  if (event.target.id === 'inventory-search') { const value=event.target.value; state.inventorySearch=value; renderPage(); const input=document.querySelector('#inventory-search'); input?.focus(); input?.setSelectionRange(value.length,value.length); }
  if (event.target.id === 'blueprint-search') { const value=event.target.value; state.blueprintSearch=value; renderPage(); const input=document.querySelector('#blueprint-search'); input?.focus(); input?.setSelectionRange(value.length,value.length); }
  if (event.target.classList.contains('generator-qty') && state.generatorResult) {
    state.generatorResult.items[Number(event.target.dataset.index)].quantity=Math.max(0,Number(event.target.value||0));
  }
});

document.addEventListener('change', async event => {
  if (event.target.id === 'inventory-sort') { state.inventorySort=event.target.value; renderPage(); }
  if (event.target.id === 'transaction-csv') {
    const file = event.target.files[0];
    if (file) document.querySelector('#transaction-form textarea[name="items"]').value = await file.text();
  }
  if (event.target.closest('#generator-form') && event.target.name === 'recognitionMode') state.generatorMode=event.target.value;
  if (event.target.closest('#generator-form') && event.target.name === 'image') {
    const file=event.target.files[0], label=document.querySelector('#generator-file-name');
    if (file && label) { label.textContent=`已选择：${file.name}`; event.target.form.requestSubmit(); }
  }
});

document.addEventListener('click', event => {
  if (event.target.id !== 'progress-canvas' || !state.detail?.pattern?.cells) return;
  const canvas = event.target, rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
  const cell = Number(canvas.dataset.cellSize);
  const x = Math.floor((event.clientX-rect.left)*scaleX/cell), y = Math.floor((event.clientY-rect.top)*scaleY/cell);
  const index = y*state.detail.pattern.columns+x;
  if (!state.detail.pattern.cells[index]) return;
  state.detailProgress.doneCells.has(index) ? state.detailProgress.doneCells.delete(index) : state.detailProgress.doneCells.add(index);
  drawPattern(canvas,state.detail.pattern,state.detailProgress.doneCells);
});

document.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const submit = form.querySelector('button[type="submit"]');
  if (submit) submit.disabled = true;
  try {
    if (form.id === 'login-form' || form.id === 'register-form') {
      const data=Object.fromEntries(new FormData(form));
      const endpoint=form.id==='login-form'?'/api/auth/login':'/api/auth/register';
      const result=await api(endpoint,{method:'POST',json:data});
      state.csrf=result.csrfToken; state.session={authenticated:true,user:result.user,maxUploadMb:8}; toast('登录成功'); await navigate('dashboard');
    }
    if (form.id === 'forgot-form') {
      const result=await api('/api/auth/forgot-password',{method:'POST',json:Object.fromEntries(new FormData(form))});
      toast(result.message); if (result.debugToken) { history.replaceState({},'',`/?reset=${result.debugToken}`); state.authMode='reset'; renderAuth(); }
    }
    if (form.id === 'reset-form') {
      await api('/api/auth/reset-password',{method:'POST',json:Object.fromEntries(new FormData(form))});
      history.replaceState({},'','/'); state.authMode='login'; toast('密码已更新，请重新登录'); renderAuth();
    }
    if (form.id === 'transaction-form') {
      const fd=new FormData(form), items=parseItems(fd.get('items'));
      if (!items.length) throw new Error('请至少填写一个有效色号和数量');
      await api('/api/inventory/transactions',{method:'POST',json:{operation:fd.get('operation'),items,remark:fd.get('remark'),source:'web'}});
      toast('库存流水已保存'); closeModal(); await navigate(state.page==='inventory'?'inventory':'workbench');
    }
    if (form.id === 'blueprint-form') {
      const fd=new FormData(form), id=form.dataset.id;
      fd.set('items',JSON.stringify(parseItems(fd.get('items'))));
      await api(id?`/api/blueprints/${id}`:'/api/blueprints',{method:id?'PUT':'POST',body:fd});
      toast('图纸已保存'); closeModal(); await navigate('blueprints');
    }
    if (form.id === 'generator-form') {
      if (state.generatorBusy) return;
      const fd=new FormData(form), rawFile=fd.get('image');
      state.generatorMode=String(fd.get('recognitionMode') || 'legend');
      state.generatorBusy=true; renderPage();
      try {
        state.generatorFile=await optimizeUpload(rawFile);
        fd.set('image',state.generatorFile); fd.set('dither','false');
        const result=await api('/api/analyze',{method:'POST',body:fd});
        state.generatorResult=result.result; toast(result.message);
      } finally { state.generatorBusy=false; }
      renderPage();
    }
    if (form.id === 'generator-save-form') {
      if (state.generatorSaving) return;
      const fd=new FormData(form), payload=new FormData();
      const saveButton=document.querySelector('[data-action="submit-generator-save"]'), status=document.querySelector('#generator-save-status');
      state.generatorSaving=true;
      if (saveButton) { saveButton.disabled=true; saveButton.innerHTML='<span class="spinner"></span>正在保存'; }
      if (status) status.textContent='正在保存图片和用豆清单，请稍候…';
      payload.set('name',fd.get('name')); payload.set('tag',fd.get('tag')); payload.set('folder',fd.get('folder')); payload.set('status','待拼');
      payload.set('requestKey',state.generatorSaveKey);
      if (state.generatorFile) payload.set('image',state.generatorFile);
      payload.set('items',JSON.stringify(state.generatorResult.items.map(({id,quantity})=>({id,quantity:Number(quantity)}))));
      payload.set('pattern',JSON.stringify(state.generatorResult));
      try {
        const result=await api('/api/blueprints',{method:'POST',body:payload});
        if (status) status.textContent='保存成功，正在打开我的图纸…';
        toast(result.duplicate?'这张图纸已经保存过了':'图纸已保存'); closeModal(); await navigate('blueprints');
      } finally {
        state.generatorSaving=false;
        const currentButton=document.querySelector('[data-action="submit-generator-save"]');
        if (currentButton) { currentButton.disabled=false; currentButton.textContent='确认保存'; }
      }
    }
    if (form.id === 'settings-form') {
      const fd=Object.fromEntries(new FormData(form)); fd.lowThreshold=Number(fd.lowThreshold);
      const result=await api('/api/settings',{method:'PUT',json:fd}); state.session.user=result.user; toast('设置已保存'); await navigate('settings');
    }
    if (form.id === 'admin-import-form') await submitLegacyImport(form);
    if (form.id === 'delete-account-form') {
      const values=Object.fromEntries(new FormData(form));
      await api('/api/account',{method:'DELETE',json:{password:values.password,confirm:values.confirm}});
      state.session={authenticated:false,user:null}; closeModal(); toast('账号与数据已删除'); renderAuth();
    }
  } catch (error) { state.generatorBusy=false; toast(error.message,'error'); if (state.page==='generator') renderPage(); }
  finally { if (submit) submit.disabled=false; }
});

async function exportPattern() {
  if (!state.generatorResult) return;
  const response=await fetch('/api/pattern/export.png',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(state.generatorResult)});
  if (!response.ok) { const body=await response.json(); throw new Error(body.error||'导出失败'); }
  const url=URL.createObjectURL(await response.blob()), link=document.createElement('a');
  link.href=url; link.download='pingdou-pattern.png'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function adminImportDialog() {
  openModal('迁移旧版数据', `<div class="notice warn">仅导入库存和图纸，不导入旧访问 IP 或访客记录。重复执行会覆盖库存，请确认文件来自旧版备份。</div><form id="admin-import-form" style="margin-top:16px"><label class="field"><span>旧版 JSON 备份</span><input type="file" name="file" accept="application/json,.json" required></label><label class="check-line"><input type="checkbox" name="replace" required>我确认用备份数量覆盖当前库存</label></form>`, `<button class="btn btn-secondary" data-action="close-modal">取消</button><button class="btn btn-primary" data-action="submit-admin-import">开始迁移</button>`);
}

async function submitLegacyImport(form) {
  const file=new FormData(form).get('file');
  const raw=JSON.parse(await file.text());
  const sanitized={inventory:(raw.inventory||[]).map(item=>({id:item.id,quantity:item.quantity})),blueprints:(raw.blueprints||[]).map(bp=>({id:bp.id,name:bp.name,tag:bp.tag,source:bp.source,status:bp.status,image:bp.image,items:bp.items}))};
  const result=await api('/api/admin/import-legacy',{method:'POST',json:{confirm:'IMPORT',data:sanitized}});
  toast(`迁移完成：${result.inventoryCount} 个色号、${result.blueprintCount} 张图纸`); closeModal(); await navigate('dashboard');
}

async function exportAccount() {
  const data=await api('/api/account/export');
  const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}), url=URL.createObjectURL(blob), link=document.createElement('a');
  link.href=url; link.download=`pingdou-account-${new Date().toISOString().slice(0,10)}.json`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function deleteAccountDialog() {
  openModal('永久注销账号', `<div class="notice danger">此操作无法恢复。请先导出账户数据，并输入当前密码及 DELETE。</div><form id="delete-account-form" style="margin-top:16px"><label class="field"><span>当前密码</span><input type="password" name="password" required></label><label class="field"><span>确认文本</span><input type="text" name="confirm" required placeholder="DELETE"></label></form>`, `<button class="btn btn-secondary" data-action="close-modal">取消</button><button class="btn btn-danger" data-action="submit-delete-account">永久删除</button>`);
}

async function boot() {
  try {
    const sessionData=await api('/api/session'); state.session=sessionData; state.csrf=sessionData.csrfToken;
    const share=new URLSearchParams(location.search).get('share');
    if (share) { publicShareView(await api(`/api/share/${encodeURIComponent(share)}`)); return; }
    if (sessionData.authenticated) await navigate('dashboard'); else renderAuth();
    const visitorKey='pingdou_visitor_id';
    if (!localStorage.getItem(visitorKey)) localStorage.setItem(visitorKey,crypto.randomUUID());
  } catch (error) { appEl.innerHTML=`<div class="boot-screen"><div class="brand-mark">!</div><strong>豆仓暂时无法打开</strong><span class="muted">${esc(error.message)}</span><button class="btn btn-primary" onclick="location.reload()">重新加载</button></div>`; }
}

function trackVisit() {
  if (!state.csrf) return;
  const visitorId=localStorage.getItem('pingdou_visitor_id');
  if (!visitorId) return;
  fetch('/api/visits',{method:'POST',keepalive:true,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify({visitorId,durationSeconds:Math.max(1,Math.round((Date.now()-state.startedAt)/1000))})}).catch(()=>{});
}

window.addEventListener('pagehide',trackVisit,{once:true});
boot();
