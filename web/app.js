/* MusicPilot Scraper — Miuix-style single page UI, no build step.
 * Auth: first-run registration then session login.
 * Folder paths are chosen with a server-side directory picker.
 */
'use strict';

/** 引用 index.html 里的 SVG sprite；图标统一走矢量，避免字符图标在缺字字体下变成方块或宽度抖动。 */
function spriteIcon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#${name}"></use></svg>`;
}

/** 空态统一两段式：轻量图标 + 一句文案。表格单元格里用 emptyCell()，别把行撑高。 */
function emptyBlock(icon, text) {
  return `<div class="empty"><span class="empty-icon">${spriteIcon(icon)}</span><p class="empty-text">${esc(text)}</p></div>`;
}
function emptyCell(icon, text, colspan) {
  return `<tr><td colspan="${colspan}" class="empty"><span class="empty-inline">${spriteIcon(icon)}</span><span class="empty-text">${esc(text)}</span></td></tr>`;
}
/** 紧凑空态条：图标 + 一句文案 + 可选按钮横向排一行，高度不超过 120，
 * 不再用整块大面板居中留白。page 缺省时不带按钮。 */
function emptyBar(icon, text, page, action) {
  return `<div class="empty-bar">
    <span class="empty-inline">${spriteIcon(icon)}</span>
    <span class="empty-text">${esc(text)}</span>
    ${page ? `<button class="ghost-btn small" data-go="${esc(page)}">${esc(action || '前往')}</button>` : ''}
  </div>`;
}

const NAV = [
  { group: '工作区' },
  { id: 'dashboard', label: '概览', icon: 'i-dashboard' },
  { id: 'library', label: '音乐库', icon: 'i-library' },
  { id: 'files', label: '源文件', icon: 'i-files' },
  { id: 'plan', label: '整理计划', icon: 'i-plan' },
  { id: 'jobs', label: '任务记录', icon: 'i-jobs' },
  { group: '配置' },
  { id: 'settings', label: '刮削设置', icon: 'i-settings' },
  { id: 'system', label: '系统设置', icon: 'i-system' },
  { id: 'docs', label: '转移方式说明', icon: 'i-docs' },
];
const PAGE_META = {
  dashboard: ['概览', '音乐库状态、任务与最近入库'],
  library: ['音乐库', '浏览库内所有曲目、手动刮削、自动扫描查漏补缺（只改标签，不动文件）'],
  files: ['源文件', '扫描源目录、选择文件并生成整理计划'],
  plan: ['整理计划', '先预览源 → 目标的映射，确认后再执行'],
  jobs: ['任务记录', '查看进度与明细、取消任务、导出表格（CSV）报告'],
  settings: ['刮削设置', '命名模板、冲突策略、元数据来源与自动刮削'],
  system: ['系统设置', '目录、转移方式与运行环境自检'],
  docs: ['转移方式说明', '复制 / 移动 / 硬链接 / 软链接 的原理、限制与适用场景'],
};
const MODE_ORDER = ['copy', 'move', 'hardlink', 'symlink'];
// 命名模板变量与中文释义；变量名本身是模板语法，保持英文
const TEMPLATE_FIELD_LABELS = {
  artist: '歌手', album: '专辑', album_artist: '专辑歌手', title: '标题', track: '音轨号',
  disc: '碟号', year: '年份', ext: '扩展名', filename: '原文件名',
};

const state = {
  page: 'dashboard',
  auth: { enabled: true, authenticated: false, needs_setup: false, username: null, is_admin: false },
  authMode: 'register',
  settings: {},
  settingsMeta: {},
  modes: [],
  modeChoice: null,   // 系统设置页里点了但还没保存的转移方式
  orderingNote: '',
  files: [],
  selected: new Set(),
  jobs: [],
  previewJob: null,
  watch: null,
  currentJobId: null,
  pollTimer: null,
  // 转移方式可用性：设置页「转移方式可用性」面板单独缓存，读取失败时留 null 走空态
  capabilities: { loaded: false, loading: false, error: '', data: null },
  options: { organize: true, skipLowConfidence: false },
  picker: { target: null, path: '', root: '', parent: null, entries: [], loading: false },
  plan: { jobId: null, items: [], choices: new Map(), filter: 'all' },
  library: {
    // 音乐库页的视图状态都在这里：筛选 / Tab / 分页 / 视图切换，刷新扫盘不会把它清掉。
    loaded: false, loading: false, tracks: [], stats: {}, sources: [], libraryDir: '', warnings: [],
    filter: 'all', lyricFilter: 'all', coverFilter: 'all', genre: '', query: '',
    tab: 'tracks', view: localStorage.getItem('mp-lib-view') || 'table',
    page: 1, pageSize: Number(localStorage.getItem('mp-lib-size')) || 50, openGroup: '',
    // 概览的短时缓存：{ at, stats, tracks, sources }，60s 内重复进概览不再请求 /api/library
    dashCache: null,
  },
  drawer: { jobId: null, items: [] },
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));
const fmtSize = (bytes) => {
  const n = Number(bytes || 0);
  return n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1024).toFixed(0)} KB`;
};
const fmtTime = (stamp) => (stamp ? new Date(stamp * 1000).toLocaleString('zh-CN') : '—');
const fmtDuration = (seconds) => {
  if (!seconds) return '—';
  const m = Math.floor(seconds / 60);
  return `${m}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
};
const fileName = (path) => String(path || '').split(/[\\/]/).pop();
const dirName = (path) => {
  const parts = String(path || '').split(/[\\/]/);
  parts.pop();
  return parts.join('/') || '/';
};
/* 路径太长时保留首尾、中间省略，文件名与所在目录都不会被截掉。 */
const shortPath = (path, head = 18, tail = 34) => {
  const text = String(path || '');
  if (text.length <= head + tail + 1) return text;
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
};
const MODE_LABELS = { copy: '复制', move: '移动', hardlink: '硬链接', symlink: '软链接' };
const SOURCE_LABELS = {
  musicbrainz: '音乐数据库 MusicBrainz', netease: '网易云音乐', qq: 'QQ音乐',
  kugou: '酷狗音乐', kuwo: '酷我音乐', local: '本地标签', manual: '手动填写',
};
// 音源说明只在设置页用，顺序即界面顺序
const SOURCE_OPTIONS = [
  ['local', '本地标签与文件名（始终参与）'],
  ['musicbrainz', '音乐数据库 MusicBrainz（国际曲库）'],
  ['netease', '网易云音乐（中文曲库 / 歌词 / 封面）'],
  ['qq', 'QQ音乐（中文曲库 / 歌词 / 封面 / 轨号）'],
  ['kugou', '酷狗音乐（中文曲库 / 歌词）'],
  ['kuwo', '酷我音乐（中文曲库 / 歌词）'],
];
const sourceLabel = (name) => SOURCE_LABELS[String(name || '').toLowerCase()] || String(name || '未知来源');
const modeLabel = (mode) => MODE_LABELS[String(mode || '')] || '未知方式';
const STATUS_LABELS = {
  planned: '计划中', done: '成功', skipped: '跳过', failed: '失败', needs_review: '需确认',
};
/* 说明列用后端给的中文摘要（detail_brief），完整原文放悬停提示。 */
const detailBrief = (item) => String(item.detail_brief || item.detail || '').trim() || '—';
const jobKind = (job) => ({ preview: '预览', scrape: '刮削', auto: '自动' })[job.kind] || '刮削';
// 任务列表显示「歌曲名」，不再只给一串 id；id 退到提示与明细里。
function jobLabel(job) {
  const label = String(job.label || '').trim();
  if (label) return label;
  if (job.title) return fileName(job.title);
  if (job.current_file) return fileName(job.current_file);
  return `${jobKind(job)} · ${job.processed || 0} 个文件`;
}
function jobNote(job) {
  const parts = [jobKind(job)];
  if (job.total) parts.push(`${job.processed || 0}/${job.total}`);
  else if (job.processed) parts.push(`${job.processed} 项`);
  parts.push(`#${job.id}`);
  return parts.join(' · ');
}

/* ---------------- 请求与中断 ---------------- */
/* 切页 / 重画时会有一批请求还在飞，响应回来已经没人要了：浏览器把它们记成
 * ERR_CONNECTION_RESET / ERR_ABORTED，控制台一片红。这里给每个请求挂上发起它的
 * 「槽位」，换槽位时主动 abort 上一批，并把它当成正常取消（不弹 toast、不写错误态）。 */
const REQUEST_SLOTS = new Map();
let requestSlot = 'boot';
class RequestAborted extends Error {
  constructor() { super('请求已取消'); this.name = 'AbortError'; }
}
function abortSlot(slot) {
  const controllers = REQUEST_SLOTS.get(slot);
  if (!controllers) return;
  REQUEST_SLOTS.delete(slot);
  controllers.forEach((controller) => controller.abort());
}
/** 换一批请求：上一批还没回来的先取消，之后的 request() 都归到新槽位。 */
function switchRequestSlot(slot) {
  if (slot === requestSlot) return;
  abortSlot(requestSlot);
  requestSlot = slot;
}
function abortAllRequests() {
  [...REQUEST_SLOTS.keys()].forEach(abortSlot);
}
const isAbortError = (error) => error instanceof RequestAborted
  || error?.name === 'AbortError' || error?.code === 20;

async function api(path, options = {}) {
  const init = { credentials: 'same-origin', ...options };
  const slot = init.slot || requestSlot;
  delete init.slot;
  const controller = new AbortController();
  const batch = REQUEST_SLOTS.get(slot) || new Set();
  batch.add(controller);
  REQUEST_SLOTS.set(slot, batch);
  init.signal = controller.signal;
  if (init.body && typeof init.body !== 'string') {
    init.headers = { ...(init.headers || {}), 'Content-Type': 'application/json' };
    init.body = JSON.stringify(init.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (isAbortError(error)) throw new RequestAborted();
    throw error;
  } finally {
    batch.delete(controller);
    if (!batch.size && REQUEST_SLOTS.get(slot) === batch) REQUEST_SLOTS.delete(slot);
  }
  // 响应头到了、响应体还在读的时候被取消，也要落进取消分支（fetch 这时已经 resolve）
  if (init.signal.aborted) throw new RequestAborted();
  if (response.status === 401) {
    state.auth.authenticated = false;
    showAuth(state.auth.needs_setup ? 'register' : 'login');
    throw new Error('登录状态已失效，请重新登录');
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail);
    } catch (_) { /* keep status text */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  const type = response.headers.get('content-type') || '';
  try {
    return type.includes('application/json') ? await response.json() : await response.text();
  } catch (error) {
    if (isAbortError(error)) throw new RequestAborted();
    throw error;
  }
}

function toast(message, bad = false) {
  const node = $('toast');
  node.textContent = message;
  node.className = `toast show${bad ? ' bad' : ''}`;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => { node.className = 'toast'; }, 3600);
}
function banner(message, kind = '') {
  const node = $('banner');
  if (!message) { node.className = 'banner hidden'; return; }
  node.textContent = message;
  node.className = `banner ${kind}`;
}

/* ---------------- theme ---------------- */
function effectiveTheme(preference) {
  if (preference === 'auto') {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  return preference;
}
function themePreference() { return localStorage.getItem('mp-theme') || 'dark'; }
function applyTheme(preference) {
  localStorage.setItem('mp-theme', preference);
  const theme = effectiveTheme(preference);
  document.documentElement.setAttribute('data-theme', theme);
  $('theme-icon').innerHTML = spriteIcon(theme === 'dark' ? 'i-sun' : 'i-moon');
  $('theme-toggle').title = theme === 'dark' ? '切换到日间模式' : '切换到夜间模式';
  document.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.className = `ghost-btn${button.dataset.themeChoice === preference ? ' active' : ''}`;
  });
}
function toggleTheme() {
  applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
}

/* ---------------- authentication ---------------- */
function setAuthMode(mode) {
  state.authMode = mode;
  const registering = mode === 'register';
  $('auth-heading').textContent = registering ? '创建管理员账号' : '登录 MusicPilot';
  $('auth-desc').textContent = registering
    ? '首次使用，请先设置一个账号。密码会用 PBKDF2 加盐保存在本机数据库中。'
    : '欢迎回来，请输入账号与密码继续使用。';
  $('auth-submit').textContent = registering ? '创建账号并进入' : '登录';
  $('auth-confirm-field').classList.toggle('hidden', !registering);
  $('auth-pass').setAttribute('autocomplete', registering ? 'new-password' : 'current-password');
  $('auth-error').textContent = '';
  document.querySelectorAll('[data-auth-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.authTab === mode);
  });
}
function showAuth(mode) {
  setAuthMode(mode || (state.auth.needs_setup ? 'register' : 'login'));
  $('auth-screen').classList.remove('hidden');
  $('app-shell').classList.add('hidden');
}
function hideAuth() {
  $('auth-screen').classList.add('hidden');
  $('app-shell').classList.remove('hidden');
}
async function refreshAuth() {
  state.auth = await api('/api/auth/status');
  const name = state.auth.username || '—';
  $('user-name').textContent = name;
  $('user-avatar').textContent = name.slice(0, 1).toUpperCase();
  return state.auth;
}
async function enterApp() {
  switchRequestSlot('app');
  hideAuth();
  await Promise.all([loadSettings(), loadModes(), loadWatch()]);
  try {
    const about = await api('/api/about');
    $('version').textContent = `v${about.version}`;
  } catch (_) { /* about is optional */ }
  go('dashboard');
}

/* ---------------- shell ---------------- */
/* 侧边栏三种形态：宽屏可折叠成图标栏（宽度交给 CSS 断点决定），
 * 窄屏折叠后变成覆盖式抽屉。折叠偏好与主题一样存在 localStorage，刷新后保持。 */
function navCollapsed() { return localStorage.getItem('mp-nav') === 'collapsed'; }
function applyNavCollapsed(collapsed) {
  localStorage.setItem('mp-nav', collapsed ? 'collapsed' : 'expanded');
  const shell = $('app-shell');
  shell.classList.toggle('nav-collapsed', collapsed);
  // 图标栏模式下状态由外壳类决定，这里只维护抽屉的显式开关
  if (!collapsed) shell.classList.remove('nav-open');
  const toggle = $('nav-toggle');
  $('nav-toggle-icon').innerHTML = spriteIcon(collapsed ? 'i-expand' : 'i-collapse');
  toggle.title = collapsed ? '展开侧边栏' : '收起侧边栏';
  toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  $('nav-open').title = collapsed ? '展开导航' : '收起导航';
}
function toggleNav() {
  const shell = $('app-shell');
  // 窄屏（抽屉模式）与宽屏（图标栏）共用同一个按钮，行为按当前断点区分
  const compact = window.matchMedia('(max-width: 900px)').matches;
  if (compact) {
    shell.classList.toggle('nav-open');
    localStorage.setItem('mp-nav', shell.classList.contains('nav-open') ? 'expanded' : 'collapsed');
    applyNavCollapsed(!shell.classList.contains('nav-open'));
    return;
  }
  applyNavCollapsed(!navCollapsed());
}
function renderNav() {
  $('nav').innerHTML = NAV.map((item) => (
    item.group
      ? `<div class="nav-group">${esc(item.group)}</div>`
      : `<button class="nav-item${state.page === item.id ? ' active' : ''}" data-page="${item.id}"
           title="${esc(item.label)}">
           <span class="nav-icon">${spriteIcon(item.icon)}</span><span class="nav-label">${esc(item.label)}</span></button>`
  )).join('');
  document.querySelectorAll('[data-page]').forEach((button) => {
    button.addEventListener('click', () => go(button.dataset.page));
  });
}
function go(page) {
  // 系统设置里点了但没保存的转移方式不跟着跨页走，避免另一页保存时把它顺手写进去
  if (page !== 'system') state.modeChoice = null;
  state.page = page;
  // 上一页还在飞的请求没人要了：先取消，控制台才不会冒出 ERR_CONNECTION_RESET
  switchRequestSlot(`page:${page}`);
  renderNav();
  if (window.matchMedia('(max-width: 900px)').matches) {
    $('app-shell').classList.remove('nav-open');
    applyNavCollapsed(true);
  }
  const [title, subtitle] = PAGE_META[page] || PAGE_META.dashboard;
  $('page-title').textContent = title;
  $('page-subtitle').textContent = subtitle;
  banner('');
  renderPage();
}
function openModal(title, html) {
  $('modal-title').textContent = title;
  $('modal-body').innerHTML = html;
  $('modal').classList.remove('hidden');
}
function closeModal() {
  $('modal').classList.add('hidden');
}

/* ---------------- directory picker ---------------- */
function openPicker(targetId, title) {
  state.picker.target = targetId;
  $('picker-title').textContent = title || '选择文件夹';
  $('picker-error') && ($('picker-error').textContent = '');
  $('picker').classList.remove('hidden');
  const current = $(targetId) ? $(targetId).value.trim() : '';
  loadDir(current);
}
function closePicker() { $('picker').classList.add('hidden'); }
async function loadDir(path) {
  state.picker.loading = true;
  $('picker-list').innerHTML = '<div class="picker-empty">正在读取目录…</div>';
  try {
    const query = path ? `?path=${encodeURIComponent(path)}` : '';
    const data = await api(`/api/files/directories${query}`);
    state.picker.path = data.path;
    state.picker.root = data.root;
    state.picker.parent = data.parent;
    state.picker.entries = data.entries || [];
    renderPicker();
  } catch (error) {
    if (path) {
      // A previously saved path may no longer exist (e.g. after remounting).
      state.picker.loading = false;
      await loadDir('');
      return;
    }
    $('picker-list').innerHTML = `<div class="picker-empty">${esc(error.message)}</div>`;
  } finally {
    state.picker.loading = false;
  }
}
function renderCrumbs() {
  const path = state.picker.path;
  const root = state.picker.root || path;
  const separator = path.includes('\\') ? '\\' : '/';
  const relative = path.startsWith(root) ? path.slice(root.length) : path;
  const parts = relative.split(/[\\/]/).filter(Boolean);
  let accumulated = root.replace(/[\\/]+$/, '');
  const rootLabel = root.split(/[\\/]/).filter(Boolean).pop() || '/';
  let html = `<button class="crumb" data-dir="${esc(root)}">${esc(rootLabel)}</button>`;
  parts.forEach((name, index) => {
    accumulated += separator + name;
    html += `<span class="crumb-sep">›</span>` +
      `<button class="crumb${index === parts.length - 1 ? ' current' : ''}" data-dir="${esc(accumulated)}">${esc(name)}</button>`;
  });
  $('picker-root').textContent = root;
  $('picker-crumbs').innerHTML = html;
  document.querySelectorAll('#picker-crumbs [data-dir]').forEach((button) => {
    button.addEventListener('click', () => loadDir(button.dataset.dir));
  });
}
function renderPicker() {
  renderCrumbs();
  const rows = state.picker.entries.map((entry) => `
    <button class="picker-item" data-dir="${esc(entry.path)}">
      <span class="folder-icon">▣</span>
      <span class="folder-name" title="${esc(entry.path)}">${esc(entry.name)}</span>
      <span class="folder-arrow">›</span>
    </button>`).join('');
  $('picker-list').innerHTML = rows ||
    '<div class="picker-empty">此目录下没有可选的子文件夹；可以直接选择当前文件夹。</div>';
  document.querySelectorAll('#picker-list [data-dir]').forEach((button) => {
    button.addEventListener('click', () => loadDir(button.dataset.dir));
  });
  $('picker-up').disabled = !state.picker.parent;
  $('picker-value').value = state.picker.path;
}

/* ---------------- pages ---------------- */
function renderPage() {
  const renderers = {
    dashboard: renderDashboard, library: renderLibrary, files: renderFiles, plan: renderPlan,
    jobs: renderJobs, settings: renderSettings, system: renderSystem, docs: renderDocs,
  };
  // 同一页重画也算新的一批：换槽位时把上一批没回来的请求取消掉
  switchRequestSlot(`paint:${state.page}`);
  (renderers[state.page] || renderDashboard)();
}
function capabilityBadge(item) {
  if (item.available === true) return '<span class="badge ok">可用</span>';
  if (item.available === false) return '<span class="badge bad">不可用</span>';
  return '<span class="badge info">未自检</span>';
}
const STATUS_BADGES = {
  completed: ['ok', '已完成'], running: ['info', '运行中'], pending: ['info', '排队中'],
  failed: ['bad', '失败'], cancelled: ['warn', '已取消'], done: ['ok', '成功'],
  skipped: ['warn', '跳过'], planned: ['info', '计划中'], needs_review: ['warn', '需确认'],
};
function statusLabel(status) {
  const entry = STATUS_BADGES[status];
  return entry ? entry[1] : String(status || '未知');
}
function statusBadge(status) {
  const [tone, label] = STATUS_BADGES[status] || ['info', status];
  return `<span class="badge ${tone}">${esc(label)}</span>`;
}

/* ---------------- 概览：库状态速览 + 最近入库 + 最近任务 + 最近整理 ---------------- */
/* 概览只读现有接口：/api/dashboard 管任务与账号，/api/library 管库统计与最近入库。
 * 库数据先用音乐库页 / 上次概览留下的缓存，没有或过了 DASH_LIBRARY_TTL 才请求，
 * 免得每次点进概览都把整库标签重扫一遍。两个请求各自降级，任何一个挂了都不影响整页。 */
const DASH_LIBRARY_TTL = 60 * 1000;

/** 概览要用的库数据：音乐库页扫过的（或 60s 内的概览缓存）直接复用，返回 null 表示得去请求。 */
function dashboardLibraryCache() {
  const store = state.library;
  if (store.loaded) return { stats: store.stats, tracks: store.tracks, sources: store.sources };
  const cached = store.dashCache;
  if (cached && Date.now() - cached.at < DASH_LIBRARY_TTL) return cached;
  return null;
}
function rememberDashboardLibrary(lib) {
  state.library.dashCache = {
    at: Date.now(), stats: lib.stats || {}, tracks: lib.tracks || [], sources: lib.sources || [],
  };
}

async function renderDashboard() {
  // 两个请求并行：库数据有缓存就直接用，有错误各自带着，面板单独降级
  const cached = dashboardLibraryCache();
  const [board, listing] = await Promise.all([
    api('/api/dashboard').then((value) => ({ value })).catch((error) => ({ error })),
    cached ? Promise.resolve({ value: cached }) : api('/api/library')
      .then((value) => { rememberDashboardLibrary(value); return { value }; })
      .catch((error) => ({ error })),
  ]);
  if (board.error) { toast(board.error.message, true); return; }
  const data = board.value;
  const libError = listing.error ? listing.error.message || '请稍后重试或检查音乐库目录' : '';
  const lib = listing.value || {};
  const stats = lib.stats || {};
  const tracks = lib.tracks || [];

  const jobRows = (data.jobs || []).map((job) => `
    <div class="mini-row">
      <div class="mini-main">
        <div class="mini-title" title="${esc(jobNote(job))}">${esc(jobLabel(job))}</div>
        <div class="mini-sub">${job.dry_run ? '预览' : esc(jobKind(job))} · ${esc(job.processed || 0)} 项</div>
      </div>
      <div class="mini-side">${statusBadge(job.status)}<span>${esc(fmtTime(job.created_at))}</span></div>
    </div>`).join('');
  const recentRows = (data.recent_done || []).map((item) => `
    <div class="mini-row">
      <div class="mini-main">
        <div class="mini-title" title="${esc(item.source)}">${esc(fileName(item.source))}</div>
        <div class="mini-sub">${esc(item.mode_label || modeLabel(item.mode))}</div>
      </div>
      <div class="mini-side"><span>${esc(fmtTime(item.created_at))}</span></div>
    </div>`).join('');
  /* 三个列表同一份定高：卡片自然等高，条目多时列表自己滚，不把整行拉长 */
  const jobList = jobRows ? `<div class="dash-list">${jobRows}</div>` : '';
  const doneList = recentRows ? `<div class="dash-list">${recentRows}</div>` : '';

  /* 版面是「行 + Grid 分栏」：每行是一张 CSS Grid，间隔由 gap 产生，
   * 卡片自己是 border-box，边框与背景永远不会越出格子，相邻卡片不会互相压住。
   * 概览只留两行 2 等分（库状态速览｜最近入库、最近任务｜最近整理），
   * 左侧导航已有入口的页面不再在概览里重列一遍磁贴或跳转按钮。 */
  $('page').innerHTML = `
    <div class="dash-board">
      <div class="dash-row dash-row-2">
        <div class="panel dash-library" id="dash-library">
          <div class="panel-head"><div><h2>库状态速览</h2>
            <p>曲目、专辑、歌手与待补全进度</p></div></div>
          ${libError ? `
            ${emptyBar('i-library', '音乐库统计读取失败')}
            <p class="small muted dash-empty-note">${esc(libError)}</p>
            <button class="ghost-btn small dash-retry" id="dash-retry">重新读取</button>`
            : dashLibraryHtml(stats)}
        </div>
        <div class="panel dash-recent" id="dash-recent">
          <div class="panel-head"><div><h2>最近入库</h2>
            <p>按文件修改时间倒序的前 ${DASH_RECENT_LIMIT} 首</p></div></div>
          ${libError ? emptyBar('i-clock', '最近入库读取失败') : dashRecentHtml(tracks)}
        </div>
      </div>
      <div class="dash-row dash-row-2">
        <div class="panel dash-jobs">
          <div class="panel-head"><div><h2>最近任务</h2></div></div>
          ${jobList || emptyBar('i-jobs', '暂无任务记录')}
        </div>
        <div class="panel dash-done">
          <div class="panel-head"><div><h2>最近整理</h2>
            <p>确认后的文件按当前转移方式落库</p></div></div>
          ${doneList || emptyBar('i-plan', '暂无整理记录')}
        </div>
      </div>
    </div>`;
  bindCoverFallback($('page'));
  document.querySelectorAll('#page [data-go]').forEach((button) => {
    button.addEventListener('click', () => go(button.dataset.go));
  });
  // 读失败是缓存里的失败：清掉它再重画，否则点重试还是拿到同一份缓存
  const retry = $('dash-retry');
  if (retry) retry.onclick = () => { state.library.dashCache = null; renderDashboard(); };
}

const DASH_RECENT_LIMIT = 8;

/** 库状态速览：三个数字 + 一条待补全进度。
 * 封面 / 歌词覆盖率、最常缺字段都在音乐库页有，概览不再重复列；
 * 页面入口一律交给左侧导航，概览里不放「打开音乐库」这类纯跳转按钮。 */
function dashLibraryHtml(stats) {
  const total = Number(stats.tracks) || 0;
  const incomplete = Math.min(Number(stats.incomplete) || 0, total);
  const rate = total ? Math.round((100 * incomplete) / total) : null;
  const numbers = [
    ['曲目', total], ['专辑', Number(stats.albums) || 0], ['歌手', Number(stats.artists) || 0],
  ].map(([label, value]) => `
    <div class="dash-number"><div class="dash-number-value">${esc(String(value))}</div>
      <div class="dash-number-label">${esc(label)}</div></div>`).join('');
  return `
    <div class="dash-numbers">${numbers}</div>
    <div class="dash-rate">
      <div class="dash-rate-head"><span>待补全占比</span>
        <b>${rate === null ? '—' : `${rate}%`}</b></div>
      <div class="progress dash-rate-bar"><span class="${dashRateTone(incomplete ? rate : 100)}" style="width:${rate === null ? 0 : rate}%"></span></div>
      <div class="dash-rate-note">${total ? `${incomplete} / ${total} 首待补全` : '库里还没有曲目'}</div>
    </div>
    <div class="dash-foot">
      <span class="dash-pending">待补全 <b class="${incomplete ? 'warn' : 'ok'}">${esc(String(incomplete))}</b> 首</span>
    </div>`;
}
/** 待补全占比的配色：补全率越高越绿，全补完是绿的，缺得多是红的。 */
function dashRateTone(pendingRate) {
  if (pendingRate === null || pendingRate === undefined) return '';
  const done = 100 - pendingRate;
  return done >= 85 ? 'ok' : done >= 60 ? 'warn' : 'bad';
}

function dashRecentHtml(tracks) {
  const recent = [...tracks]
    .filter((track) => Number(track.modified_at) > 0)
    .sort((a, b) => Number(b.modified_at) - Number(a.modified_at))
    .slice(0, DASH_RECENT_LIMIT);
  if (!recent.length) return emptyBar('i-clock', '还没有入库曲目');
  // 列表放进定高滚动容器：卡片与左侧「库状态速览」等高，条目多也不会把整行拉长
  const rows = recent.map((track) => `
    <div class="mini-row dash-recent-row">
      <span class="dash-thumb">${track.cover_url
        ? `<img src="${esc(track.cover_url)}" alt="" loading="lazy" data-cover-fallback>${spriteIcon('i-disc')}`
        : spriteIcon('i-disc')}</span>
      <div class="mini-main">
        <div class="mini-title" title="${esc(track.path)}">${esc(track.title || track.name)}</div>
        <div class="mini-sub">${esc([track.artist, track.album].filter(Boolean).join(' · ') || '歌手 / 专辑待补全')}</div>
      </div>
      <div class="mini-side"><span>${esc(fmtTime(track.modified_at))}</span></div>
    </div>`).join('');
  return `<div class="dash-list">${rows}</div>`;
}

/** 封面加载失败就地退回碟片占位，不留破图。 */
function bindCoverFallback(root) {
  if (!root) return;
  root.querySelectorAll('img[data-cover-fallback]').forEach((img) => {
    img.addEventListener('error', () => { img.remove(); }, { once: true });
  });
}

/* ---------------- 音乐库：统计卡 + 高密度表格 + 分组 Tab + 宽版详情 ---------------- */
const LIBRARY_FILTERS = [
  ['all', '全部'], ['incomplete', '待补全'], ['complete', '已齐全'],
  ['lyrics', '有歌词'], ['no_lyrics', '无歌词'],
];
/* Tab 与状态下拉共用同一份可见集合，筛选条件叠加而不是互相覆盖。 */
const LIBRARY_TABS = [['tracks', '歌曲'], ['artists', '歌手'], ['genres', '流派'], ['albums', '专辑']];
const LYRIC_FILTERS = [['all', '全部歌词'], ['yes', '有歌词'], ['no', '无歌词']];
const COVER_FILTERS = [['all', '全部封面'], ['yes', '有封面'], ['no', '无封面']];
const PAGE_SIZES = [20, 50, 100];
const FIELD_NAMES = {
  title: '标题', artist: '歌手', album: '专辑', album_artist: '专辑艺术家',
  year: '年份', track_number: '专辑轨号', disc_number: '碟号', lyrics: '歌词', cover: '封面',
};
const fieldName = (name) => FIELD_NAMES[name] || name;
/** 匹配度 1–10 的配色：7 分以上才算「像」。 */
const scoreTone = (score) => (score >= 8 ? 'ok' : score >= 7 ? 'warn' : 'bad');
/** 完整度配色沿用同一套语义色：≥85% 绿、70–85% 黄、其余红。 */
const completenessTone = (ratio) => (ratio >= .85 ? 'ok' : ratio >= .7 ? 'warn' : 'bad');

// 完整度百分比：缺几个字段就扣几分（九个字段均权），四舍五入到整数。
const trackCompleteness = (track) => (track.complete ? 100 : Math.max(0, Math.round(100 - (100 * (track.missing || []).length) / 9)));
const EXT_LABELS = { '.flac': 'FLAC', '.mp3': 'MP3', '.m4a': 'M4A', '.aac': 'AAC', '.ogg': 'OGG', '.wav': 'WAV', '.ape': 'APE', '.wma': 'WMA', '.mp4': 'MP4' };
const trackFormat = (track) => (EXT_LABELS[String(track.extension || '').toLowerCase()]
  || String(track.extension || '').replace(/^\./, '').toUpperCase() || '未知');

function libraryTrackMatches(track) {
  const filter = state.library.filter;
  if (filter === 'incomplete' && track.complete) return false;
  if (filter === 'complete' && !track.complete) return false;
  if (filter === 'lyrics' && !track.has_lyrics) return false;
  if (filter === 'no_lyrics' && track.has_lyrics) return false;
  // 两个下拉是「附加」条件：选「有封面」时「无歌词」这类主筛选照样生效
  const lyric = state.library.lyricFilter;
  if (lyric === 'yes' && !track.has_lyrics) return false;
  if (lyric === 'no' && track.has_lyrics) return false;
  const cover = state.library.coverFilter;
  if (cover === 'yes' && !track.has_cover) return false;
  if (cover === 'no' && track.has_cover) return false;
  return true;
}
function libraryVisible() {
  const needle = state.library.query.trim().toLowerCase();
  const genre = state.library.genre;
  return (state.library.tracks || []).filter((track) => {
    if (!libraryTrackMatches(track)) return false;
    if (genre && String(track.genre || '') !== genre) return false;
    if (!needle) return true;
    return trackSearchText(track).includes(needle);
  });
}
/** 搜索字段与占位文案一致：标题 / 歌手 / 专辑 / 专辑艺术家 / 文件名 / 目录 / 流派。 */
const trackSearchText = (track) => [track.title, track.artist, track.album, track.album_artist,
  track.name, track.relative_dir, track.genre, track.year].filter(Boolean).join(' ').toLowerCase();

function libraryGenres() {
  const names = new Set();
  (state.library.tracks || []).forEach((track) => {
    if (track.genre) String(track.genre).split(/[;/、]/).forEach((part) => {
      const name = part.trim();
      if (name) names.add(name);
    });
  });
  return [...names].sort((a, b) => a.localeCompare(b, 'zh-CN'));
}

/* ---- 环形完整度 / 进度条 ---- */
function ringHtml(percent, tone, size = 38) {
  const stroke = size >= 60 ? 5 : 3.4;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const dash = (circumference * Math.max(0, Math.min(100, percent))) / 100;
  return `<span class="lib-ring ${tone}" style="--ring-size:${size}px;--ring-stroke:${stroke}px">
    <svg viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle class="ring-track" cx="${size / 2}" cy="${size / 2}" r="${radius}"></circle>
      <circle class="ring-value" cx="${size / 2}" cy="${size / 2}" r="${radius}"
        stroke-dasharray="${dash.toFixed(2)} ${circumference.toFixed(2)}"
        transform="rotate(-90 ${size / 2} ${size / 2})"></circle>
    </svg><b>${percent}%</b></span>`;
}

const COVER_FALLBACK = '<span class="lib-cover-fallback">♪</span>';
function coverThumb(track, size = 40) {
  const img = track.cover_url
    ? `<img src="${esc(track.cover_url)}" alt="" loading="lazy" data-lib-cover>`
    : COVER_FALLBACK;
  return `<span class="lib-thumb" style="--thumb:${size}px">${img}</span>`;
}
function trackBadge(track) {
  return track.complete
    ? '<span class="badge ok">正常</span>'
    : `<span class="badge warn" title="缺 ${esc((track.missing || []).map(fieldName).join('、') || '字段')}">待补全</span>`;
}
const dash = (value) => (value ? esc(value) : '<span class="lib-dash">—</span>');

/* ---- 表格视图：九列，一屏塞得下 15 行以上 ---- */
function libraryTable(tracks) {
  const rows = tracks.map((track, index) => {
    const percent = trackCompleteness(track);
    const sub = [track.artist, track.album_artist && track.album_artist !== track.artist ? track.album_artist : '', track.year]
      .filter(Boolean).join(' · ');
    return `<tr class="lib-row-item${track.complete ? '' : ' gap'}" data-lib="${esc(track.path)}" tabindex="0">
      <td class="lib-col-track"><div class="lib-track-cell">
        ${coverThumb(track, 40)}
        <div class="lib-track-text">
          <div class="lib-track-title" title="${esc(track.title || track.name)}">${esc(track.title || track.name)}</div>
          <div class="lib-track-sub">${sub ? esc(sub) : '<span class="lib-dash">歌手 / 年份待补全</span>'}</div>
        </div></div></td>
      <td class="lib-col-album" title="${esc(track.album || '')}">${dash(track.album)}</td>
      <td class="lib-col-artist" title="${esc(track.artist || '')}">${dash(track.artist)}</td>
      <td class="lib-col-genre" title="${esc(track.genre || '')}">${dash(track.genre)}</td>
      <td class="lib-col-format"><span class="lib-tag mono">${esc(trackFormat(track))}</span></td>
      <td class="lib-col-complete">${ringHtml(percent, completenessTone(percent / 100))}</td>
      <td class="lib-col-lyrics">${track.has_lyrics
        ? '<span class="lib-flag-icon ok" title="有歌词">✓</span>'
        : '<span class="lib-flag-icon bad" title="无歌词">✕</span>'}</td>
      <td class="lib-col-state">${trackBadge(track)}</td>
      <td class="lib-col-actions"><div class="btn-row nowrap">
        <button class="icon-btn small" data-lib-edit="${index}" title="编辑（打开媒体编辑器）" aria-label="编辑">
          ${spriteIcon('i-edit')}</button>
        <button class="icon-btn small" data-lib-scrape="${index}" title="手动刮削" aria-label="手动刮削">
          ${spriteIcon('i-scrape')}</button>
        <button class="icon-btn small danger-text" data-lib-del="${index}" title="从音乐库移除文件" aria-label="删除">
          ${spriteIcon('i-trash')}</button>
      </div></td>
    </tr>`;
  }).join('');
  return `<div class="table-wrap lib-table-wrap"><table class="lib-table">
      <thead><tr>
        <th class="lib-col-track">曲目</th><th class="lib-col-album">专辑</th><th class="lib-col-artist">歌手</th>
        <th class="lib-col-genre">流派</th><th class="lib-col-format">格式</th><th class="lib-col-complete">完整度</th>
        <th class="lib-col-lyrics">歌词</th><th class="lib-col-state">状态</th><th class="lib-col-actions">操作</th>
      </tr></thead>
      <tbody>${rows || emptyCell('i-funnel', '当前筛选条件下没有曲目', 9)}</tbody></table></div>`;
}

/* ---- 网格视图：左封面右文字的横条小卡，比原来的竖排大卡片矮一半 ---- */
function libraryCard(track, index) {
  const percent = trackCompleteness(track);
  return `<article class="lib-row${track.complete ? '' : ' gap'}" data-lib="${esc(track.path)}" tabindex="0"
      title="${esc(track.path)}">
    <div class="lib-row-top">
      <div class="lib-row-cover">${coverThumb(track, 72)}</div>
      <div class="lib-row-main">
        <div class="lib-row-title">${esc(track.title || track.name)}</div>
        <div class="lib-row-sub${track.artist || track.album ? '' : ' blank'}">${esc([track.artist, track.album].filter(Boolean).join(' · ') || '歌手 / 专辑待补全')}</div>
      </div>
      ${ringHtml(percent, completenessTone(percent / 100), 34)}
    </div>
    <div class="lib-row-foot">
      ${trackBadge(track)}
      <span class="lib-tag mono">${esc(trackFormat(track))}</span>
      <span class="tiny">${track.has_lyrics ? '有歌词' : '无歌词'}</span>
      <span class="lib-row-actions">
        <button class="icon-btn small" data-lib-scrape="${index}" title="手动刮削" aria-label="手动刮削">${spriteIcon('i-scrape')}</button>
      </span>
    </div>
  </article>`;
}
function libraryGrid(tracks) {
  return `<div class="lib-tags">${tracks.map(libraryCard).join('')
    || emptyBlock('i-funnel', '当前筛选条件下没有曲目')}</div>`;
}

/* ---- 分组视图：歌手 / 流派 / 专辑按标签聚合，点开即可看组内曲目 ---- */
function groupTracks(tracks, kind) {
  const groups = new Map();
  tracks.forEach((track) => {
    let keys = [];
    if (kind === 'artists') keys = [track.artist || '未知歌手'];
    else if (kind === 'genres') keys = track.genre ? String(track.genre).split(/[;/、]/).map((x) => x.trim()).filter(Boolean) : ['未知流派'];
    else keys = [track.album || '未知专辑'];
    keys.forEach((name) => {
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(track);
    });
  });
  return [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], 'zh-CN'));
}
function libraryGroups(tracks) {
  const kind = state.library.tab;
  const store = state.library;
  const open = store.openGroup;
  const blocks = groupTracks(tracks, kind).map(([name, rows]) => {
    const isOpen = open === `${kind}:${name}`;
    const complete = rows.filter((row) => row.complete).length;
    const percent = Math.round((100 * complete) / rows.length);
    const items = isOpen ? rows.map((track, i) => `<button class="lib-group-item" data-lib="${esc(track.path)}">
        ${coverThumb(track, 28)}
        <span class="lib-group-item-title">${esc(track.title || track.name)}</span>
        <span class="tiny">${esc(track.album || '—')}</span>
      </button>`).join('') : '';
    return `<div class="lib-group${isOpen ? ' open' : ''}">
      <button class="lib-group-head" data-lib-group="${esc(`${kind}:${name}`)}">
        <span class="lib-group-name">${esc(name)}</span>
        <span class="badge">${rows.length} 首</span>
        <span class="lib-group-bar"><span style="width:${percent}%"></span></span>
        <span class="tiny">齐全 ${percent}%</span>
      </button>
      ${isOpen ? `<div class="lib-group-body">${items}</div>` : ''}
    </div>`;
  }).join('');
  return `<div class="lib-groups">${blocks || emptyBlock('i-funnel', '当前筛选条件下没有分组')}</div>`;
}

/* ---- 分页 ---- */
function libraryPageCount(total) {
  return Math.max(1, Math.ceil(total / state.library.pageSize));
}
function pagerHtml(total, compact = false) {
  const store = state.library;
  const pages = libraryPageCount(total);
  const current = Math.min(store.page, pages);
  const numbers = [];
  const from = Math.max(1, Math.min(current - 2, pages - 4));
  for (let i = from; i <= Math.min(pages, from + 4); i += 1) numbers.push(i);
  const sizeSelect = `<select class="lib-page-size" data-lib-size>
      ${PAGE_SIZES.map((size) => `<option value="${size}"${size === store.pageSize ? ' selected' : ''}>${size} / 页</option>`).join('')}
    </select>`;
  return `<div class="lib-pager${compact ? ' compact' : ''}">
    <button class="ghost-btn small" data-lib-page="prev" ${current <= 1 ? 'disabled' : ''}>上一页</button>
    ${numbers.map((n) => `<button class="ghost-btn small${n === current ? ' active' : ''}" data-lib-page="${n}">${n}</button>`).join('')}
    <button class="ghost-btn small" data-lib-page="next" ${current >= pages ? 'disabled' : ''}>下一页</button>
    ${compact ? sizeSelect : `<span class="small muted">共 ${total} 首 · 第 ${current} / ${pages} 页</span>${sizeSelect}`}
  </div>`;
}

/* ---- 统计卡：图标 + 大数字 + 标签 + 进度条 ---- */
function libraryStats() {
  return `<div class="bento lib-stats cols-5" id="lib-stats">${libraryStatsCards()}</div>`;
}

/** 卡片本体单独抽出来：补全后就地重算数字，不必重建整页。 */
function libraryStatsCards() {
  const store = state.library;
  const tracks = store.tracks || [];
  // 数字一律从 tracks 现算：单独补一首 / 写入标签后，卡片和表格不会各说各话。
  const stats = store.stats || {};
  const total = tracks.length || stats.tracks || 0;
  const covers = tracks.filter((t) => t.has_cover).length;
  const lyrics = tracks.filter((t) => t.has_lyrics).length;
  const complete = tracks.filter((t) => t.complete).length;
  const incomplete = tracks.filter((t) => !t.complete).length;
  const pct = (value) => (total ? Math.round((100 * value) / total) : 0);
  const completeRate = pct(complete);
  // 「待补全」是查漏补缺的入口数字：独立一张卡，点一下直接切到待补全筛选。
  const cards = [
    ['i-files', '总曲目', total, total ? 100 : 0, '', `${stats.albums || 0} 张专辑 · ${stats.artists || 0} 位歌手`],
    ['i-disc', '有封面', covers, pct(covers), '', `${total ? pct(covers) : 0}% 已内嵌封面`],
    ['i-lyrics', '有歌词', lyrics, pct(lyrics), '', `${total ? pct(lyrics) : 0}% 有歌词`],
    ['i-scrape', '待补全', incomplete, pct(incomplete), incomplete ? 'warn' : 'ok',
      '缺标题 / 歌手 / 专辑等字段'],
    ['i-check', '完整率', `${completeRate}%`, completeRate, completenessTone(completeRate / 100),
      `${complete} / ${total} 首字段齐全`],
  ];
  return cards.map(([icon, label, value, percent, tone, note]) => `
    <div class="panel lib-stat${label === '待补全' ? ' lib-stat-link' : ''}"${label === '待补全' ? ' data-lib-stat="incomplete" role="button" tabindex="0" title="只看待补全"' : ''}>
      <div class="lib-stat-head">
        <span class="lib-stat-icon">${spriteIcon(icon)}</span>
        <span class="lib-stat-label">${esc(label)}</span>
      </div>
      <div class="lib-stat-value${tone ? ` ${tone}` : ''}">${esc(String(value))}</div>
      <div class="lib-stat-note">${esc(note)}</div>
      <div class="progress lib-stat-bar"><span class="${tone}" style="width:${percent}%"></span></div>
    </div>`).join('');
}

function libraryTabCounts(tracks) {
  const counts = { tracks: tracks.length };
  ['artists', 'genres', 'albums'].forEach((kind) => { counts[kind] = groupTracks(tracks, kind).length; });
  return counts;
}

/** 只重画主内容区：改动筛选 / 翻页 / 切视图都不重新扫盘。 */
function paintLibraryBody() {
  const store = state.library;
  localStorage.setItem('mp-lib-view', store.view);
  const box = $('lib-body');
  if (!box) return;
  const visible = libraryVisible();
  const counts = libraryTabCounts(visible);
  // 只补这一首 / 写入标签会就地改标记，统计卡跟着重算，不用整页重扫
  const statBox = $('lib-stats');
  if (statBox) statBox.innerHTML = libraryStatsCards();
  document.querySelectorAll('[data-lib-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.libTab === store.tab);
    const tag = button.querySelector('em');
    if (tag) tag.textContent = String(counts[button.dataset.libTab] || 0);
  });
  if (store.tab === 'tracks') {
    const pages = libraryPageCount(visible.length);
    if (store.page > pages) store.page = pages;
    const start = (store.page - 1) * store.pageSize;
    const slice = visible.slice(start, start + store.pageSize);
    box.innerHTML = store.view === 'grid' ? libraryGrid(slice) : libraryTable(slice);
    $('lib-pager-main').innerHTML = pagerHtml(visible.length);
  } else {
    box.innerHTML = libraryGroups(visible);
    $('lib-pager-main').innerHTML = '';
  }
  const counter = $('lib-count');
  if (counter) {
    counter.textContent = visible.length === store.tracks.length
      ? `共 ${visible.length} 首`
      : `筛选出 ${visible.length} / ${store.tracks.length} 首`;
  }
  const tool = $('lib-pager-top');
  if (tool) tool.innerHTML = visible.length > store.pageSize && store.tab === 'tracks' ? pagerHtml(visible.length, true) : '';
  bindPager();
  bindLibraryBody(box, visible);
  bindLibraryActions();
}

/* 表头工具条、Tab、筛选控件：每次重绘都重新挂 onclick / onchange，
 * 不依赖容器事件委托，也不会因为节点重建而丢掉监听。 */
function bindLibraryActions() {
  const store = state.library;
  document.querySelectorAll('[data-lib-view]').forEach((button) => {
    button.onclick = () => {
      store.view = button.dataset.libView;
      paintLibraryBody();
    };
  });
  document.querySelectorAll('[data-lib-tab]').forEach((button) => {
    button.onclick = () => {
      store.tab = button.dataset.libTab;
      store.page = 1;
      store.openGroup = '';
      // 流派 tab 多一个流派下拉：切进来补上，切回去拿掉
      if (store.tab === 'genres' && !$('lib-genre')) {
        const holder = document.querySelector('.lib-selects');
        if (holder) {
          holder.insertAdjacentHTML('afterbegin', `<select id="lib-genre" class="lib-select">
            <option value="">全部流派</option>
            ${libraryGenres().map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('')}
          </select>`);
        }
      } else if (store.tab !== 'genres' && $('lib-genre')) { $('lib-genre').remove(); }
      paintLibraryBody();
    };
  });
  // 分页、分组、行 / 卡片的点击归 bindPager 与 bindLibraryBody 管，这里只管工具条与筛选控件
  const search = $('lib-search');
  if (search) {
    search.oninput = () => {
      store.query = search.value;
      store.page = 1;
      // 整块重绘会重建输入框：先记下光标，重画后把焦点与选区还回去，连续输入不卡顿
      const caret = search.selectionStart;
      paintLibraryBody();
      const next = $('lib-search');
      if (!next) return;
      next.focus();
      const at = Math.min(caret ?? next.value.length, next.value.length);
      try { next.setSelectionRange(at, at); } catch { /* 某些浏览器在 type=search 上不支持 */ }
    };
  }
  const lyric = $('lib-lyric');
  if (lyric) lyric.onchange = () => { store.lyricFilter = lyric.value; store.page = 1; paintLibraryBody(); };
  const cover = $('lib-cover');
  if (cover) cover.onchange = () => { store.coverFilter = cover.value; store.page = 1; paintLibraryBody(); };
  const genre = $('lib-genre');
  if (genre) genre.onchange = () => { store.genre = genre.value; store.page = 1; paintLibraryBody(); };
  const filterBtn = $('lib-filter-btn');
  if (filterBtn) filterBtn.onclick = () => { const node = $('lib-search'); node?.focus(); node?.select(); };
  const reset = $('lib-reset');
  if (reset) reset.onclick = () => {
    Object.assign(store, { query: '', filter: 'all', lyricFilter: 'all', coverFilter: 'all', genre: '', page: 1, openGroup: '' });
    if ($('lib-search')) $('lib-search').value = '';
    paintLibraryBody();
  };
  const refresh = $('lib-refresh');
  if (refresh) refresh.onclick = () => { invalidateLibrary(); renderLibrary(); };
  // 「待补全」统计卡：点一下直接落到待补全筛选
  document.querySelectorAll('[data-lib-stat="incomplete"]').forEach((card) => {
    const jump = () => {
      store.filter = store.filter === 'incomplete' ? 'all' : 'incomplete';
      store.page = 1;
      paintLibraryBody();
    };
    card.onclick = jump;
    card.onkeydown = (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      jump();
    };
  });
}

/** 分页：委托在整个页面上，翻页 / 改每页条数都不会因为节点重建而失效。 */
function bindPager() {
  if (bindPager._bound) return;
  bindPager._bound = true;
  const jump = (raw) => {
    const pages = libraryPageCount(libraryVisible().length);
    let next = state.library.page;
    if (raw === 'prev') next -= 1;
    else if (raw === 'next') next += 1;
    else next = Number(raw) || 1;
    state.library.page = Math.min(pages, Math.max(1, next));
    paintLibraryBody();
  };
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-lib-page]');
    if (!button) return;
    jump(button.dataset.libPage);
  });
  document.addEventListener('change', (event) => {
    const select = event.target.closest('[data-lib-size]');
    if (!select) return;
    state.library.pageSize = Number(select.value) || 50;
    state.library.page = 1;
    localStorage.setItem('mp-lib-size', String(state.library.pageSize));
    paintLibraryBody();
  });
}

function bindLibraryBody(box, visible) {
  box.querySelectorAll('img[data-lib-cover]').forEach((img) => {
    img.addEventListener('error', () => img.classList.add('hidden'), { once: true });
  });
  document.querySelectorAll('[data-lib-group]').forEach((button) => {
    button.onclick = () => {
      const key = button.dataset.libGroup;
      state.library.openGroup = state.library.openGroup === key ? '' : key;
      paintLibraryBody();
    };
  });
  // 行 / 卡片：点空白处进编辑器，点按钮走各自的动作，避免一次点击触发两件事
  box.querySelectorAll('[data-lib]').forEach((node) => {
    const path = node.dataset.lib;
    node.querySelectorAll('button[data-lib-edit],button[data-lib-scrape],button[data-lib-del]').forEach((button) => {
      button.onclick = (event) => {
        event.stopPropagation();
        if (button.dataset.libEdit !== undefined) openMediaEditor(path, 'core');
        else if (button.dataset.libScrape !== undefined) openMediaEditor(path, 'scrape');
        else if (button.dataset.libDel !== undefined) removeLibraryTrack(path);
      };
    });
    node.onclick = () => openMediaEditor(path, 'core');
    node.onkeydown = (event) => {
      if (event.target !== node) return;
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      openMediaEditor(path, 'core');
    };
  });
}

/* ================= 通用媒体编辑器 =================
 * 点「编辑」/ 行内「手动刮削」/ 点整行，都进同一个全屏编辑器，只是落在不同的 Tab：
 *   核心数据  只补空字段，先预览再写入（后端 /api/library/apply 的 dry_run）
 *   智能刮削  选平台 → 搜索 → 1–10 匹配度 → 预览 / 写入标签
 *   歌词      懒加载 detail，LRC 预览 + 原始文本编辑，整体替换
 *   操作历史  后端没有历史接口，只显示本次会话内真实做过的写入
 * 弹窗骨架一次建好、事件只绑一次（bindMediaEditor），反复开关不会叠加监听。
 */
// 只有这七个字段后端支持写入（write_missing_tags 的白名单），其余一律只读
const CORE_FIELDS = [
  ['title', '标题'], ['artist', '歌手'], ['album', '专辑'], ['album_artist', '专辑艺术家'],
  ['year', '年份'], ['track_number', '专辑轨号'], ['disc_number', '碟号'],
];
const READONLY_FIELDS = [['genre', '流派'], ['bitrate', '码率'], ['sample_rate', '采样率'], ['channels', '声道']];
const editor = {
  scrolls: {},   // 每个 Tab 内容区的滚动位置，切 Tab 再切回来不跳回顶部
  session: [],   // 本次会话内的操作流水；后端没有历史接口，只记这里
  lyrics: {},    // path → { text, loading, error }
};

function mediaEditorNode() { return $('media-editor'); }
function mediaEditorOpen() { return !!editor.path; }

/** 打开编辑器：传 path + 初始 Tab，数据全从音乐库列表里查。 */
function openMediaEditor(path, tab = 'core') {
  const track = (state.library.tracks || []).find((row) => row.path === path);
  if (!track) { toast('这首曲目不在当前音乐库列表里，请刷新后重试', true); return; }
  const node = mediaEditorNode();
  if (!node) return;
  const switched = editor.path !== path;
  editor.path = path;
  if (switched) {
    editor.scrape = { path, source: '', results: [], loading: false };
    editor.tab = tab || 'core';
  }
  node.classList.remove('hidden');
  node.setAttribute('aria-hidden', 'false');
  document.body.classList.add('editor-open');
  switchMediaTab(tab || 'core', { keepScroll: !switched });
  const close = node.querySelector('[data-me-close]');
  if (close) close.focus({ preventScroll: true });
}

/** 收起三栏里那个搜词面板；返回是否真的收掉了（ESC 先收面板再关编辑器）。 */
function closeLyricsSourcePanel() {
  const lyr = editor.path ? editor.lyrics[editor.path] : null;
  if (!lyr?.select?.open) return false;
  lyr.select.open = false;
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  if (box) paintLyricsSource(box, lyr);
  return true;
}

function closeMediaEditor() {
  closeLyricsSourcePanel();
  const node = mediaEditorNode();
  if (node) {
    node.classList.add('hidden');
    node.setAttribute('aria-hidden', 'true');
  }
  document.body.classList.remove('editor-open');
  editor.path = null;
  // 关闭时列表状态不动：筛选 / 分页 / 视图都留在原地
}

function switchMediaTab(tab, options = {}) {
  const current = editor.tab || 'core';
  // 离开歌词 Tab 时把「从音源搜词」面板收起来，下次回来是干净的两栏
  if (current === 'lyrics' && tab !== 'lyrics') closeLyricsSourcePanel();
  if (options.keepScroll !== false) {
    const oldBody = mediaEditorNode()?.querySelector('[data-me-body]');
    if (oldBody) editor.scrolls[current] = oldBody.scrollTop;
  }
  editor.tab = tab;
  mediaEditorNode()?.querySelectorAll('[data-me-tab]').forEach((button) => {
    const active = button.dataset.meTab === tab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  paintMediaHeader();
  paintMediaSide();
  paintMediaBody(tab);
  const body = mediaEditorNode()?.querySelector('[data-me-body]');
  if (body) body.scrollTop = options.keepScroll === false ? 0 : (editor.scrolls[tab] || 0);
}

/** 重画左栏（完整度 / 待写字段）与当前 Tab；写入标签或歌词后调用。 */
function refreshMediaEditor() {
  paintMediaSide();
  paintMediaHeader();
  paintMediaBody(editor.tab || 'core');
}

function mediaTrack() {
  return (state.library.tracks || []).find((row) => row.path === editor.path) || null;
}

/* ---- 本次会话的操作流水 ---- */
function pushSessionLog(action, fields, path) {
  editor.session.unshift({
    path: path || editor.path,
    action,
    fields: fields || '',
    at: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
  });
  const mine = editor.session.filter((row) => row.path === editor.path);
  editor.session = [];  // 只留最近 50 条，够看就行
  editor.session = mine.concat(editor.session).slice(0, 50);
}

function sessionLogFor(path) {
  return editor.session.filter((row) => row.path === path);
}

/* ---- 左栏 / 顶部 ---- */
function paintMediaHeader() {
  const track = mediaTrack();
  const node = mediaEditorNode();
  if (!node) return;
  const name = node.querySelector('[data-me-heading]');
  if (name) name.textContent = track ? (track.title || track.name) : '媒体编辑器';
  const file = node.querySelector('[data-me-filename]');
  if (file) file.textContent = track ? track.name : '';
}

function paintMediaSide() {
  const box = mediaEditorNode()?.querySelector('[data-me-info]');
  const track = mediaTrack();
  if (!box) return;
  if (!track) { box.innerHTML = emptyBlock('i-disc', '曲目已不在列表中'); return; }
  const percent = trackCompleteness(track);
  const missing = (track.missing || []).map(fieldName);
  const pending = missing;
  const cover = track.cover_url
    ? `<span class="me-cover">${`<img src="${esc(track.cover_url)}" alt="" data-me-cover>`}
        <span class="me-cover-fallback">${spriteIcon('i-disc')}</span></span>`
    : `<span class="me-cover"><span class="me-cover-fallback">${spriteIcon('i-disc')}</span></span>`;
  const facts = [
    ['格式', trackFormat(track)],
    ['时长', fmtDuration(track.duration)],
    ['大小', fmtSize(track.size)],
    ['修改时间', track.modified_at ? new Date(track.modified_at * 1000).toLocaleString('zh-CN') : '—'],
    ['内嵌封面', track.has_cover ? '有' : '无'],
    ['软链接', track.is_symlink ? '是' : '否'],
  ];
  box.innerHTML = `
    ${cover}
    <div class="me-side-names">
      <div class="me-side-title">${esc(track.title || track.name)}</div>
      <div class="me-side-sub">${esc([track.artist || '—', track.album || '—', track.year || '—'].join(' · '))}</div>
    </div>
    <div class="me-side-block">
      <div class="me-complete">
        ${ringHtml(percent, completenessTone(percent / 100), 54)}
        <div class="me-complete-text">
          <span class="field-label">信息完整度</span>
          <span class="me-complete-hint">${pending.length ? `缺：${esc(missing.join('、'))}` : '字段齐全'}</span>
        </div>
      </div>
      <div class="progress me-complete-bar"><span class="${completenessTone(percent / 100)}" style="width:${percent}%"></span></div>
    </div>
    <div class="me-side-block">
      <span class="field-label">待写字段</span>
      ${pending.length
        ? `<p class="me-pending">待写入：${esc(missing.join('、'))}</p>`
        : '<p class="me-pending empty">没有待写字段</p>'}
    </div>
    <div class="me-side-block">
      <span class="field-label">物理属性</span>
      <div class="me-facts">
        ${facts.map(([label, value]) => `<div class="me-fact">
          <span>${esc(label)}</span><b>${esc(value)}</b></div>`).join('')}
      </div>
      <div class="me-path mono" title="${esc(track.path)}">${esc(track.path)}</div>
    </div>`;

  box.querySelector('img[data-me-cover]')?.addEventListener('error', (event) => {
    event.currentTarget.classList.add('hidden');
  }, { once: true });
}

/* ---- Tab 内容分发 ---- */
function paintMediaBody(tab) {
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  if (!box) return;
  box.dataset.tab = tab;   // 歌词 Tab 的双栏布局按它挂样式，其它 Tab 不受影响
  const track = mediaTrack();
  if (!track) { box.innerHTML = emptyBlock('i-disc', '曲目已不在列表中'); return; }
  if (tab === 'scrape') { paintMediaScrape(track); return; }
  if (tab === 'lyrics') { paintMediaLyrics(track); return; }
  if (tab === 'timeline') { paintMediaTimeline(track); return; }
  paintMediaCore(track);
}

/* ---- 核心数据：只补空字段，先预览再写入 ---- */
function paintMediaCore(track) {
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  if (!box) return;
  const allDone = !(track.missing || []).length;
  const rows = CORE_FIELDS.map(([key, label]) => {
    const value = key === 'title' ? (track.title || '') : (track[key] ?? '');
    const filled = String(value ?? '').trim() !== '';
    const hint = filled
      ? '<span class="me-field-hint">已有标签，后端只补空字段</span>'
      : (allDone ? '' : '<span class="me-field-hint">可填写，保存时只补这一项</span>');
    return `<label class="field me-field${filled ? ' locked' : ''}">
      <span class="field-label">${esc(label)}</span>
      <input data-me-field="${key}" value="${esc(filled ? value : '')}"
        placeholder="${filled ? '' : '留空表示不写'}" ${filled ? 'disabled' : ''}>
      ${hint}
    </label>`;
  }).join('');
  const readonly = READONLY_FIELDS.map(([key, label]) => {
    const value = key === 'genre' ? (track.genre || '') : '';
    return `<div class="me-ro-row">
      <span class="me-ro-key">${esc(label)}</span>
      <span class="me-ro-value${value ? '' : ' blank'}">${esc(value || '—')}</span>
      <span class="badge">${value ? '暂不支持写入' : '后端未提供'}</span></div>`;
  }).join('');
  box.innerHTML = `
    <div class="me-core-note">
      <p class="small muted">保存只补空字段，文件里已有的标签一个都不会被覆盖，也不移动文件位置。</p>
    </div>
    <div class="me-form">${rows}</div>
    <div class="me-readonly">
      <span class="field-label">后端不支持写入的字段</span>
      ${readonly}
    </div>
    <div class="me-actions">
      ${allDone
        ? '<span class="small muted">该文件字段齐全，无需补全</span>'
        : '<button class="primary-btn" type="button" data-me-core-save>保存（先预览再写入）</button>'}
      <button class="ghost-btn" type="button" data-me-fill-one>只补这一首</button>
    </div>`;
  box.querySelector('[data-me-core-save]')?.addEventListener('click', saveCorePending);
  box.querySelector('[data-me-fill-one]')?.addEventListener('click', () => fillOne(track.path));
}

/** 核心数据保存：先 dry_run 预览「将写入：…」，用户确认后再真写。 */
async function saveCorePending() {
  const track = mediaTrack();
  if (!track) return;
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  const metadata = { title: track.title || track.name };
  if (box) {
    box.querySelectorAll('input[data-me-field]').forEach((input) => {
      if (input.disabled) return;
      const value = input.value.trim();
      if (value) metadata[input.dataset.meField] = value;  // 留空不写，免得清掉后端读到的值
    });
  }
  const filled = Object.keys(metadata).filter((key) => key !== 'title');
  if (!filled.length) { toast('没有填写任何待补字段', true); return; }
  try {
    const preview = await api('/api/library/apply', {
      method: 'POST', body: { path: track.path, metadata, dry_run: true },
    });
    const labels = preview.would_write_labels || [];
    if (!labels.length) { toast(preview.message || '没有可写入的字段', true); return; }
    if (!confirm(`将写入：${labels.join('、')}\n（只补空字段，文件位置不变）\n\n确认写入？`)) return;
    const result = await api('/api/library/apply', {
      method: 'POST', body: { path: track.path, metadata, dry_run: false },
    });
    const done = result.written_labels || labels;
    toast(`已补全：${done.join('、')}`);
    pushSessionLog('写入标签', done.join('、'));
    await refreshLibraryTrack(track.path);
    paintLibraryBody();
    refreshMediaEditor();
  } catch (error) { toast(error.message, true); }
}

/* ---- 智能刮削：平台 chips → 搜索 → 1–10 匹配度 → 预览 / 写入 ---- */
function paintMediaScrape(track) {
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  if (!box) return;
  if (!editor.scrape || editor.scrape.path !== track.path) {
    editor.scrape = { path: track.path, source: '', results: [], loading: false };
  }
  const scrape = editor.scrape;
  const chips = (state.library.sources || []).map((item) => (
    `<button class="cand-chip${item.enabled ? '' : ' dim'}${scrape.source === item.id ? ' active' : ''}"
       type="button" data-scrape-source="${esc(item.id)}"
       ${item.enabled ? '' : 'disabled title="该平台未在设置中启用"'}>
       <b>${esc(item.label)}</b><span>${item.enabled ? '已启用' : '未启用'}</span></button>`
  )).join('');
  const results = scrape.results.length
    ? `<div class="scrape-results">${scrape.results.map(scrapeResultCard).join('')}</div>`
    : (scrape.searched
      ? emptyBlock('i-search', scrape.searched.hint)
      : emptyBlock('i-search', '填好搜索词后点「搜索」，结果按 1–10 匹配度排序'));
  box.innerHTML = `
    <div class="me-scrape">
      <p class="small muted">文件：<span class="mono">${esc(track.path)}</span></p>
      <div class="field">
        <span class="field-label">来源平台（点一下选中，再搜索）</span>
        <div class="cand-row" id="scrape-sources">
          <button class="cand-chip${scrape.source ? '' : ' active'}" type="button" data-scrape-source="">
            <b>全部已启用</b><span>逐平台并发搜</span></button>
          ${chips}
        </div>
      </div>
      <div class="me-scrape-form">
        <label class="field"><span class="field-label">歌名</span>
          <input id="scrape-title" value="${esc(track.title || '')}" placeholder="歌名"></label>
        <label class="field"><span class="field-label">歌手</span>
          <input id="scrape-artist" value="${esc(track.artist || '')}" placeholder="歌手"></label>
        <label class="field"><span class="field-label">专辑</span>
          <input id="scrape-album" value="${esc(track.album || '')}" placeholder="专辑"></label>
      </div>
      <div class="btn-row me-scrape-bar">
        <button class="primary-btn" type="button" data-scrape-search>${scrape.loading ? '正在搜索…' : '搜索'}</button>
        <span class="small muted">匹配度 1–10：10 表示歌名、歌手、专辑全对得上；7 分以上才建议写入。</span>
      </div>
      <div id="scrape-results">${results}</div>
    </div>`;
  box.querySelectorAll('[data-scrape-source]').forEach((button) => {
    button.addEventListener('click', () => {
      editor.scrape.source = button.dataset.scrapeSource;
      box.querySelectorAll('[data-scrape-source]').forEach((node) => {
        node.classList.toggle('active', node === button);
      });
    });
  });
  box.querySelector('[data-scrape-search]')?.addEventListener('click', searchLibrary);
  box.querySelectorAll('[data-scrape-apply]').forEach((button) => {
    button.addEventListener('click', () => {
      const item = editor.scrape.results[Number(button.dataset.scrapeApply)];
      applyScrapeResult(item, button.dataset.dry === '1');
    });
  });
}

async function searchLibrary() {
  const scrape = editor.scrape;
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  if (!scrape || !scrape.path || !box) return;
  const title = box.querySelector('#scrape-title')?.value.trim() || '';
  const artist = box.querySelector('#scrape-artist')?.value.trim() || '';
  const album = box.querySelector('#scrape-album')?.value.trim() || '';
  const holder = box.querySelector('#scrape-results');
  if (holder) holder.innerHTML = emptyBlock('i-search', '正在搜索…');
  scrape.loading = true;
  try {
    const payload = await api('/api/library/search', {
      method: 'POST',
      body: { path: scrape.path, source: scrape.source || '', title, artist, album },
    });
    if (editor.path !== scrape.path) return;
    scrape.results = payload.results || [];
    scrape.searched = scrape.results.length
      ? { hint: `在 ${(payload.searched || []).map(sourceLabel).join(' / ') || '所选平台'} 搜到 ${scrape.results.length} 条结果` }
      : { hint: payload.searched && payload.searched.length ? '没有搜到结果，换个平台或改改搜索词试试' : '没有可用的来源平台，请先在设置里启用' };
  } catch (error) {
    scrape.results = [];
    scrape.searched = { hint: error.message };
  } finally {
    scrape.loading = false;
    if (editor.path === scrape.path && editor.tab === 'scrape') {
      const scroll = box.scrollTop;
      paintMediaScrape(mediaTrack() || { path: scrape.path });
      const body = mediaEditorNode()?.querySelector('[data-me-body]');
      if (body) body.scrollTop = scroll;
    }
  }
}

async function applyScrapeResult(item, dryRun) {
  if (!item || !editor.path) return;
  const path = editor.path;
  try {
    const payload = await api('/api/library/apply', {
      method: 'POST',
      body: { path, metadata: item.metadata, dry_run: dryRun },
    });
    const labels = (payload.written_labels && payload.written_labels.length
      ? payload.written_labels
      : payload.would_write_labels || []).join('、');
    if (dryRun) {
      toast(labels ? `将补全：${labels}（文件位置不变）` : '这个候选没有能补的字段', !labels);
      return;
    }
    toast(payload.message || (labels ? `已补全：${labels}` : '没有可写入的字段'), !labels);
    if (labels) {
      pushSessionLog('写入标签', labels, path);
      await refreshLibraryTrack(path);
      paintLibraryBody();
      refreshMediaEditor();
    }
  } catch (error) { toast(error.message, true); }
}

/* ---- 歌词：左「同步渲染预览」+ 右「歌词编辑区」
 * 本地搜词只作用在前端已加载/已编辑的文本上；音源搜词复用 /api/library/search 与
 * /api/library/apply（dry_run）探一次「这个来源有没有词」，两条路都不新增后端接口。
 * 写歌词永远只走 PUT /api/library/lyrics，apply 的标签语义不碰。
 */
function mediaLyricsState(path) {
  if (!editor.lyrics[path]) {
    editor.lyrics[path] = {
      text: null,     // 后端读回来的原文
      draft: null,    // 编辑区里还没保存的文本；null 表示没动过
      loading: false,
      error: '',
      query: '',
      hitIndex: 0,
      select: null,   // 从音源搜词的面板状态
    };
  }
  return editor.lyrics[path];
}

const LRC_STAMP_HEAD = /^\s*(?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+/;
const LRC_STAMPS = /\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]/g;
const LRC_META_HEAD = /^\s*\[([a-zA-Z]{2,6})\s*:\s*([^\]]*)\]\s*$/;
// 纯文本的署名行：作词 : 张三 / 作曲：李四 —— 格式化时归组到顶部，不编时间戳
const LRC_CREDIT = /^\s*(作词|作曲|编曲|制作人|词|曲|演唱|原唱|录音|混音|母带|吉他|贝斯|鼓|键盘|和声|出品|监制|发行|OP|SP|Written by|Composed by|Lyrics by|Produced by)\s*[:：]\s*(.+)$/i;
const LRC_META_ORDER = ['ti', 'ar', 'al', 'au', 'by', 'offset', 'length', 're', 've'];
const LYRIC_HINT = '已填入歌词，点保存歌词才会写入文件';
const LYRIC_NO_SOURCE = '该来源没有给出歌词正文';

const lyricsDraftText = (lyr) => (lyr.draft === null ? (lyr.text || '') : lyr.draft);
const lyricsQuery = (lyr) => String(lyr.query || '').trim();
// 行数只数歌词行：[ti:]/[ar:] 这类头标签算元数据，不算歌词
const countLyricLines = (text) => lrcPreviewRows(text).filter((row) => !row.blank && !row.meta).length;

/** 预览行：剥掉 [mm:ss.xx] 时间戳、保留空行当段落间隔；非 LRC 纯文本按行原样。
 * `line` 始终是编辑区里的真实行号，预览、行号列、命中位置才对得上；
 * `meta` 是 [ti:]/[ar:] 这类 LRC 头标签，只取冒号后的值当元信息行渲染。 */
function lrcPreviewRows(text) {
  return String(text ?? '').split(/\r?\n/).map((line, index) => {
    const body = line.replace(LRC_STAMP_HEAD, '').replace(/\s+$/, '');
    const meta = body.trim().match(LRC_META_HEAD);
    // [ti:xxx] 只取冒号后面的值；[ti:] 这种空标签直接当空行，不占版面
    const text = meta ? meta[2].trim() : body;
    return {
      line: index,
      text,
      blank: !text.trim(),
      meta: !!meta && !!text.trim(),
    };
  });
}

/** 末尾的空行不算段落间隔，预览里去掉，免得右下角凭空多一段留白。 */
function trimTrailingBlanks(rows) {
  let end = rows.length;
  while (end > 0 && rows[end - 1].blank) end -= 1;
  return rows.slice(0, end);
}

/** 命中行号。预览与行号列共用同一份，索引天然对齐；
 * 行号列多传 includeMeta，让 [ti:] 这类只在编辑区存在的行也能在右侧看见命中。 */
function lyricsHitLines(text, query, includeMeta = false) {
  const needle = String(query ?? '').toLowerCase();
  if (!needle) return [];
  return lrcPreviewRows(text)
    .filter((row) => !row.blank && (includeMeta || !row.meta) && row.text.toLowerCase().includes(needle))
    .map((row) => row.line);
}

/** 大小写不敏感的本地命中：返回包了 <mark> 的 HTML，中文子串照样切得开。 */
function markHits(text, query) {
  const source = String(text ?? '');
  const needle = String(query ?? '');
  if (!needle) return esc(source);
  const lower = source.toLowerCase();
  const target = needle.toLowerCase();
  let out = '';
  let from = 0;
  for (;;) {
    const at = lower.indexOf(target, from);
    if (at < 0) break;
    out += esc(source.slice(from, at)) + `<mark>${esc(source.slice(at, at + target.length))}</mark>`;
    from = at + target.length;
  }
  return out + esc(source.slice(from));
}

function paintMediaLyrics(track) {
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  if (!box) return;
  const lyr = mediaLyricsState(track.path);
  if (!lyr.select || lyr.select.path !== track.path) {
    lyr.select = {
      path: track.path,
      open: false,
      loading: false,
      busy: -1,
      results: [],
      hint: '',
      stats: [],
      form: { title: track.title || '', artist: track.artist || '', album: track.album || '', source: '' },
    };
  }
  if (lyr.text === null && !lyr.loading && !lyr.error) loadMediaLyrics(track.path);
  box.innerHTML = `
    <div class="me-lyrics-shell">
      <section class="me-lpane me-lpane-preview">
        <header class="me-lpane-head">
          <span class="me-lpane-title">同步渲染预览</span>
          <button class="ghost-btn small me-lsrc-open" type="button" data-lsrc-open>从音源搜词</button>
          <div class="me-find">
            <span class="me-find-icon">${spriteIcon('i-search')}</span>
            <input class="me-find-input" type="search" data-me-find placeholder="搜索歌词"
              value="${esc(lyr.query)}" spellcheck="false" autocomplete="off" aria-label="在当前歌词里搜索">
            <span class="me-find-count small muted" data-me-find-count>0 / 0</span>
            <button class="icon-btn small" type="button" data-me-find-prev title="上一处" aria-label="上一处">
              ${spriteIcon('i-chevron-up')}</button>
            <button class="icon-btn small" type="button" data-me-find-next title="下一处" aria-label="下一处">
              ${spriteIcon('i-chevron-down')}</button>
          </div>
        </header>
        <div class="me-lpane-scroll" data-me-preview></div>
        <footer class="me-lpane-foot">
          <span class="small muted" data-me-preview-foot>共 0 行</span>
        </footer>
      </section>
      <section class="me-lpane me-lpane-edit">
        <header class="me-lpane-head">
          <span class="me-lpane-title">歌词编辑区</span>
          <span class="badge me-lpane-badge">LRC / TXT</span>
          <div class="btn-row me-lpane-tools">
            <button class="ghost-btn small" type="button" data-me-format title="整理成规范 LRC：保留已有时间轴，不编造时间戳">
              ${spriteIcon('i-wand')}格式化结构</button>
            <button class="ghost-btn small" type="button" data-me-clear title="清空编辑区（只清前端，不落盘）">
              ${spriteIcon('i-trash')}清空</button>
          </div>
        </header>
        <div class="me-code">
          <div class="me-code-gutter" data-me-gutter aria-hidden="true"></div>
          <textarea class="me-code-area" data-me-lyrics-text spellcheck="false" wrap="off"
            placeholder="粘贴 LRC 或纯文本歌词…"></textarea>
        </div>
        <footer class="me-lpane-foot me-edit-foot">
          <span class="small muted mono" data-me-caret>Ln 1, Col 1</span>
          <span class="small muted" data-me-lyrics-count>共 0 行歌词</span>
          <button class="primary-btn small" type="button" data-me-lyrics-save>保存歌词</button>
        </footer>
      </section>
      <div class="me-lsrc hidden" data-lsrc>
        <div class="me-lsrc-card" data-lsrc-card></div>
      </div>
    </div>`;
  bindLyricsControls(box);
  const area = box.querySelector('[data-me-lyrics-text]');
  if (area) area.value = lyricsDraftText(lyr);
  renderLyricsPreview(box, lyr);
  syncLyricsGutter(box, lyr);
  syncLyricsStatus(box, lyr);
  paintLyricsSource(box, lyr, track);
}

/** 左栏预览 + 命中计数：文本或查询变了都走这里，不重建整个 Tab。 */
function renderLyricsPreview(box, lyr) {
  const holder = box.querySelector('[data-me-preview]');
  if (!holder) return;
  if (lyr.loading) { holder.innerHTML = emptyBlock('i-lyrics', '正在读取歌词…'); return; }
  if (lyr.error) { holder.innerHTML = emptyBlock('i-lyrics', lyr.error); return; }
  const text = lyricsDraftText(lyr);
  // [ti:]/[ar:] 这些头标签在左栏对话录没用，只在右栏编辑区里保留
  const rows = trimTrailingBlanks(lrcPreviewRows(text).filter((row) => !row.meta));
  const query = lyricsQuery(lyr);
  const hitLines = lyricsHitLines(text, query);
  const html = rows.map((row) => {
    if (row.blank) return '<div class="me-lrc-line blank"><span>&nbsp;</span></div>';
    const hit = hitLines.indexOf(row.line);
    if (hit < 0) return `<div class="me-lrc-line"><span>${esc(row.text)}</span></div>`;
    return `<div class="me-lrc-line hit" data-hitn="${hit}" data-line="${row.line}"><span>${markHits(row.text, query)}</span></div>`;
  }).join('');
  if (!rows.some((row) => !row.blank)) {
    holder.innerHTML = `<div class="me-lpane-empty">
      <span class="me-lpane-empty-icon">${spriteIcon('i-lyrics')}</span>
      <p class="me-lpane-empty-text">这首曲目还没有歌词</p>
      <button class="ghost-btn small" type="button" data-lsrc-open>从音源搜词</button>
    </div>`;
    setLyricsFindCount(box, 0, 0);
    return;
  }
  holder.innerHTML = html;
  const total = hitLines.length;
  if (!total) lyr.hitIndex = 0;
  else lyr.hitIndex = ((lyr.hitIndex % total) + total) % total;
  setLyricsFindCount(box, total ? lyr.hitIndex + 1 : 0, total);
  if (total) scrollLyricsHit(box, lyr, false);
}

function setLyricsFindCount(box, current, total) {
  const counter = box.querySelector('[data-me-find-count]');
  if (counter) counter.textContent = `${current} / ${total}`;
}

/** 把当前命中行滚进左栏可视区：只动这一栏的滚动，不惊动外层。 */
function scrollLyricsHit(box, lyr, animate = true) {
  const holder = box.querySelector('[data-me-preview]');
  const row = holder?.querySelector(`[data-hitn="${lyr.hitIndex}"]`);
  if (!holder || !row) return;
  const rowRect = row.getBoundingClientRect();
  const holderRect = holder.getBoundingClientRect();
  const delta = (rowRect.top - holderRect.top) - (holder.clientHeight - rowRect.height) / 2;
  const top = Math.max(0, holder.scrollTop + delta);
  if (typeof holder.scrollTo === 'function') holder.scrollTo({ top, behavior: animate ? 'smooth' : 'auto' });
  else holder.scrollTop = top;
  holder.querySelectorAll('.me-lrc-line.current').forEach((node) => node.classList.remove('current'));
  row.classList.add('current');
}

/** 行号列：与左栏同一份行数据，命中行在行号上也标出来，右侧同样看得见命中。 */
function syncLyricsGutter(box, lyr) {
  const area = box.querySelector('[data-me-lyrics-text]');
  const gutter = box.querySelector('[data-me-gutter]');
  if (!gutter) return;
  const text = area ? area.value : lyricsDraftText(lyr);
  const rows = lrcPreviewRows(text);
  const query = lyricsQuery(lyr);
  const hitLines = lyricsHitLines(text, query, true);
  const nav = lyricsHitLines(text, query);
  const current = nav.length ? nav[Math.min(lyr.hitIndex, nav.length - 1)] : -1;
  const key = `${rows.length}:${hitLines.join(',')}:${current}`;
  if (gutter.dataset.key !== key) {
    gutter.dataset.key = key;
    const hits = new Set(hitLines);
    gutter.innerHTML = rows.map((row) => (
      `<div class="me-code-ln${hits.has(row.line) ? ' hit' : ''}${row.line === current ? ' current' : ''}">${row.line + 1}</div>`
    )).join('');
  }
  if (area) gutter.scrollTop = area.scrollTop;
}

/** 底部状态栏：光标行列 + 歌词行数（纯编辑信息），行数随编辑实时更新。 */
function syncLyricsStatus(box, lyr) {
  const area = box.querySelector('[data-me-lyrics-text]');
  const caret = box.querySelector('[data-me-caret]');
  const counter = box.querySelector('[data-me-lyrics-count]');
  const foot = box.querySelector('[data-me-preview-foot]');
  const text = area ? area.value : lyricsDraftText(lyr);
  const lines = countLyricLines(text);
  if (counter) counter.textContent = `共 ${lines} 行歌词`;
  if (foot) {
    let isLrc = false;
    try { isLrc = parseLrc(text).isLrc; } catch { isLrc = false; }
    foot.textContent = `共 ${lines} 行 · ${lines ? (isLrc ? 'LRC 时间轴' : '纯文本') : '无歌词'}`;
  }
  if (caret && area) {
    const head = area.value.slice(0, area.selectionStart || 0);
    const line = head.split('\n').length;
    const column = head.length - head.lastIndexOf('\n');
    caret.textContent = `Ln ${line}, Col ${column}`;
  }
}

/* ---- 从音源搜词：候选按源分组，点「填入编辑区」走 POST /api/library/lyrics/fetch
 * 那条通道只读不写：拿不到就显示后端给的具体原因（该来源不支持 / 接口无返回 / 该歌曲没有歌词），
 * 不编造歌词，也不把「拿不到」笼统说成「没有」。写盘永远只走「保存歌词」。 ---- */
/** 源的能力说明：chip 只有「已启用且真能用」才亮，其余置灰并把原因写进 title。 */
function lyricSourceChipState(item) {
  if (!item.enabled) {
    return { usable: false, tone: '未启用', title: '该平台未在设置中启用' };
  }
  if (item.available === false) {
    return { usable: false, tone: '不可用', title: item.note || '该来源接口已失效' };
  }
  if (item.lyrics === false) {
    // 能搜但仍拿不到词的源：把「为什么」写全（覆盖范围 + 不提供歌词正文）
    return { usable: false, tone: '仅元数据', title: item.note || item.lyrics_note || '该来源不提供歌词' };
  }
  return { usable: true, tone: '可取词', title: item.note || '可搜索并按 id 取词' };
}

function lyricsSourceChips(sel) {
  const sources = state.library.sources || [];
  const chips = sources.map((item) => {
    const state = lyricSourceChipState(item);
    // 本次搜索该源失败 / 0 条：在 chip 上就地标出来，别让「亮着却空着」
    const stat = (sel.stats || []).find((row) => row.id === item.id);
    if (stat && state.usable) {
      state.tone = stat.ok ? (stat.count ? `本次 ${stat.count} 条` : '本次 0 条') : (stat.note || '接口无返回');
      state.title = stat.note || `本次搜索 ${stat.count} 条`;
    }
    const cls = `cand-chip${state.usable ? '' : ' dim'}${sel.form.source === item.id ? ' active' : ''}`;
    return `<button class="${cls}" type="button" data-lsrc-source="${esc(item.id)}"
       ${state.usable ? '' : 'disabled'} title="${esc(state.title)}">
       <b>${esc(item.label)}</b><span>${esc(state.tone)}</span></button>`;
  }).join('');
  const usable = sources.filter((item) => lyricSourceChipState(item).usable).length;
  return { chips, usable };
}

/** 候选按源分组：同一平台的候选排在一起，组头写清这个源能不能取词。 */
function lyricsSourceGroups(results) {
  const groups = [];
  const index = new Map();
  results.forEach((item, position) => {
    const source = (item.metadata || {}).source || 'unknown';
    if (!index.has(source)) {
      index.set(source, groups.length);
      groups.push({ source, label: item.source_label || sourceLabel(source), rows: [] });
    }
    groups[index.get(source)].rows.push({ item, position });
  });
  return groups;
}

function lyricsSourceHtml(lyr, track) {
  const sel = lyr.select;
  const { chips, usable } = lyricsSourceChips(sel);
  const groups = lyricsSourceGroups(sel.results);
  const results = sel.loading
    ? `<div class="me-lsrc-note small muted">正在搜索…</div>`
    : (groups.length
      ? `<div class="me-lsrc-list">${groups.map((group) => `
          <div class="me-lsrc-group">
            <div class="me-lsrc-group-head">${esc(group.label)}
              <span class="small muted">${group.rows.length} 条</span></div>
            ${group.rows.map(({ item, position }) => lyricsSourceRow(item, position, sel.busy === position)).join('')}
          </div>`).join('')}</div>`
      : `<div class="me-lsrc-note small muted">${esc(sel.hint || '填好搜索词后点「搜索」，点「填入编辑区」才会取正文。')}</div>`);
  return `
    <div class="me-lsrc-head">
      <span class="me-lpane-title">从音源搜词</span>
      <button class="icon-btn small" type="button" data-lsrc-close title="收起" aria-label="收起">
        ${spriteIcon('i-close')}</button>
    </div>
    <div class="me-lsrc-body">
      <div class="me-lsrc-form">
        <label class="field"><span class="field-label">歌名</span>
          <input data-lsrc-field="title" value="${esc(sel.form.title)}" placeholder="歌名"></label>
        <label class="field"><span class="field-label">歌手</span>
          <input data-lsrc-field="artist" value="${esc(sel.form.artist)}" placeholder="歌手"></label>
        <label class="field"><span class="field-label">专辑</span>
          <input data-lsrc-field="album" value="${esc(sel.form.album)}" placeholder="专辑"></label>
      </div>
      <div class="field">
        <span class="field-label">来源平台（不选则搜全部已启用的可取词来源）</span>
        <div class="cand-row">
          <button class="cand-chip${sel.form.source ? '' : ' active'}" type="button" data-lsrc-source="">
            <b>全部已启用</b><span>逐平台并发搜</span></button>
          ${chips}
        </div>
        ${usable ? '' : '<p class="small muted">当前没有可取词的来源：先到「刮削设置」启用网易云 / QQ音乐 / 酷狗 / 酷我之一。</p>'}
      </div>
      <div class="btn-row me-lsrc-bar">
        <button class="primary-btn small" type="button" data-lsrc-search>${sel.loading ? '正在搜索…' : '搜索'}</button>
        <span class="small muted">取词只读：填进编辑区不会动文件，点「保存歌词」才写盘。</span>
      </div>
      ${lyricsSourceStats(sel)}
      ${results}
      <p class="small muted me-lsrc-foot">
        取不到词时会显示音源给的具体原因（该来源不支持按 id 取词 / 接口无返回 / 该歌曲没有歌词），不编造歌词。
      </p>
      <p class="small muted">文件：<span class="mono">${esc(track.path || '')}</span></p>
    </div>`;
}

/** 逐源交代这次搜索：几个候选、这次有没有搜成，避免「亮着却空着」。 */
function lyricsSourceStats(sel) {
  const stats = sel.stats || [];
  if (!stats.length) return '';
  const rows = stats.map((row) => (
    `<span class="badge ${row.ok ? (row.count ? 'ok' : '') : 'bad'}"
       title="${esc(row.note || '')}">${esc(row.label)} ${row.ok ? `${row.count} 条` : esc(row.note || '接口无返回')}</span>`
  )).join('');
  return `<div class="me-lsrc-stats small muted">本次搜索：${rows}</div>`;
}

function lyricsSourceRow(item, index, busy = false) {
  const meta = item.metadata || {};
  const hasLyrics = String(meta.lyrics || '').trim().length > 0;
  // 后端取词失败时把原因挂在候选上，这里是唯一展示它的位置
  const failed = item._lyricsReason
    ? `<span class="badge warn" title="${esc(item._lyricsReason)}">${esc(item._lyricsReason)}</span>`
    : '';
  // 一个动作：填入编辑区。候选自带词就地填，没带词走后端取词端点，拿到正文才填。
  const action = !item.can_fetch_lyrics && !hasLyrics
    ? '<span class="small muted">该来源不支持取词</span>'
    : `<button class="${hasLyrics ? 'primary-btn' : 'ghost-btn'} small" type="button"
         data-lsrc-fill="${index}" data-need-fetch="${hasLyrics ? '0' : '1'}" ${busy ? 'disabled' : ''}>
         ${busy ? '正在取词…' : '填入编辑区'}</button>`;
  return `<article class="me-lsrc-item">
    <div class="scrape-score ${scoreTone(item.score)}" title="匹配度 1–10"><b>${item.score}</b><span>/10</span></div>
    <div class="scrape-main">
      <div class="scrape-title">${esc(meta.title || '—')}</div>
      <div class="scrape-sub">${esc([meta.artist, meta.album].filter(Boolean).join(' · ') || '—')}</div>
      <div class="scrape-tags">
        <span class="badge">${esc(item.source_label || sourceLabel(meta.source))}</span>
        ${hasLyrics ? '<span class="badge ok">含歌词</span>' : '<span class="badge">列表不带词</span>'}
        ${failed}
      </div>
    </div>
    <div class="btn-row">${action}</div>
  </article>`;
}

function paintLyricsSource(box, lyr, track) {
  const layer = box.querySelector('[data-lsrc]');
  if (!layer) return;
  layer.classList.toggle('hidden', !lyr.select.open);
  const card = layer.querySelector('[data-lsrc-card]');
  if (!card || !lyr.select.open) return;
  // 重画面板时把滚动位置带回来，选平台、取词都不会把列表弹回顶部
  const body = card.querySelector('.me-lsrc-body');
  const keep = body ? body.scrollTop : 0;
  card.innerHTML = lyricsSourceHtml(lyr, track || mediaTrack() || {});
  const next = card.querySelector('.me-lsrc-body');
  if (next) next.scrollTop = keep;
}

/** 面板输入只在搜索时读取，避免每次按键都重画面板把焦点弄丢。 */
function readLyricsSourceForm(box) {
  const form = {};
  box.querySelectorAll('[data-lsrc-field]').forEach((input) => {
    form[input.dataset.lsrcField] = input.value.trim();
  });
  return form;
}

async function searchLyricsSource(box, lyr) {
  const path = editor.path;
  if (!path || lyr.select.loading) return;   // 连点只发一次请求
  Object.assign(lyr.select.form, readLyricsSourceForm(box));
  lyr.select.loading = true;
  lyr.select.hint = '';
  lyr.select.stats = [];
  lyr.select.results = [];
  paintLyricsSource(box, lyr);
  try {
    const payload = await api('/api/library/search', {
      method: 'POST',
      body: {
        path,
        source: lyr.select.form.source || '',
        title: lyr.select.form.title,
        artist: lyr.select.form.artist,
        album: lyr.select.form.album,
      },
    });
    if (editor.path !== path) return;
    // 搜索结果自带每个源的真实可用状态，顺手刷新 chips（不必先进音乐库页）
    if (payload.sources && payload.sources.length) state.library.sources = payload.sources;
    lyr.select.results = payload.results || [];
    lyr.select.stats = payload.source_stats || [];
    // 逐源交代这次搜索：0 条 vs 没搜成要分开说，不能含糊
    const failed = lyr.select.stats.filter((row) => !row.ok);
    lyr.select.hint = lyr.select.results.length
      ? ''
      : (payload.searched && payload.searched.length
        ? (failed.length
          ? `没有搜到结果：${failed.map((row) => `${row.label} ${row.note || '接口无返回'}`).join('、')}`
          : '没有搜到结果，换个平台或改改搜索词试试')
        : '没有可用的来源平台，请先在设置里启用');
  } catch (error) {
    lyr.select.results = [];
    lyr.select.stats = [];
    lyr.select.hint = error.message;
  } finally {
    lyr.select.loading = false;
    if (editor.path === path) paintLyricsSource(mediaEditorNode()?.querySelector('[data-me-body]'), lyr);
  }
}

/** 选中含歌词的候选：只填右侧编辑区，不落盘。 */
function fillLyricsFromCandidate(box, lyr, index) {
  const item = lyr.select.results[index];
  const text = String(item?.metadata?.lyrics || '');
  if (!text.trim()) { toast(LYRIC_NO_SOURCE, true); return; }
  setLyricsDraft(box, lyr, text);
  lyr.select.open = false;
  paintLyricsSource(box, lyr);
  toast(LYRIC_HINT);
}

/** 候选没带词就走 POST /api/library/lyrics/fetch 取正文：拿到才填，拿不到说清原因。 */
async function fetchLyricsForCandidate(box, lyr, index) {
  const path = editor.path;
  const item = lyr.select.results[index];
  const meta = item?.metadata || {};
  if (!path || !item || lyr.select.busy >= 0) return;
  if (!item.can_fetch_lyrics) {
    toast('该来源不支持按 id 取词', true);
    return;
  }
  lyr.select.busy = index;
  item._lyricsReason = '';
  paintLyricsSource(box, lyr);
  try {
    const payload = await api('/api/library/lyrics/fetch', {
      method: 'POST',
      body: { source: meta.source || '', external_id: meta.external_id || '', album: meta.album || null, title: meta.title || null },
    });
    if (editor.path !== path) return;
    const text = String(payload.lyrics || '');
    if (text.trim()) {
      setLyricsDraft(box, lyr, text);
      lyr.select.open = false;
      toast(LYRIC_HINT);
      return;
    }
    // 后端给的是具体原因，原样显示，不换成笼统的「没有提供歌词」
    item._lyricsReason = payload.reason || '接口无返回';
    toast(`取词失败：${item._lyricsReason}`, true);
  } catch (error) {
    item._lyricsReason = error.message;
    toast(error.message, true);
  } finally {
    lyr.select.busy = -1;
    if (editor.path === path) paintLyricsSource(mediaEditorNode()?.querySelector('[data-me-body]'), lyr);
  }
}

function setLyricsDraft(box, lyr, text) {
  lyr.draft = String(text ?? '');
  const area = box.querySelector('[data-me-lyrics-text]');
  if (area) { area.value = lyr.draft; area.scrollTop = 0; }
  renderLyricsPreview(box, lyr);
  syncLyricsGutter(box, lyr);
  syncLyricsStatus(box, lyr);
}

/** 格式化结构：保留已有时间轴、归组元信息、统一空行；没有时间的行如实留着。 */
function formatLrc(text) {
  const meta = new Map();
  const credits = [];
  const body = [];
  String(text ?? '').split(/\r?\n/).forEach((line) => {
    const metaMatch = line.match(LRC_META_HEAD);
    if (metaMatch) {
      const key = metaMatch[1].toLowerCase();
      if (!meta.has(key)) meta.set(key, metaMatch[2].trim());
      return;
    }
    const trimmed = line.replace(LRC_STAMP_HEAD, '').replace(/\s+$/, '').trim();
    const stamped = LRC_STAMP_HEAD.test(line);
    // 没有时间轴的署名行归组到顶部；带时间轴的行不管内容一律留在正文
    if (!stamped && LRC_CREDIT.test(trimmed)) {
      const credit = trimmed.replace(/\s*[:：]\s*/, '：');
      if (!credits.includes(credit)) credits.push(credit);
      return;
    }
    const stampText = stamped ? (line.match(LRC_STAMPS) || []).map(normalizeLrcStamp) : [];
    body.push({ text: trimmed, stamps: stampText });
  });
  // 纯 LRC（每一行都带时间轴）才按时间排序；混着无时间行的保持原顺序，不乱动
  if (body.length && body.every((row) => row.stamps.length)) {
    body.sort((a, b) => stampSeconds(a.stamps[0]) - stampSeconds(b.stamps[0]));
  }
  const keys = [
    ...LRC_META_ORDER.filter((key) => meta.has(key)),
    ...[...meta.keys()].filter((key) => !LRC_META_ORDER.includes(key)),
  ];
  const out = keys.map((key) => `[${key}:${meta.get(key)}]`).concat(credits);
  body.forEach((row) => {
    if (!row.text) {
      // 空行压成段落间隔：连续空行只留一行，首尾不留空行
      if (out.length && out[out.length - 1] !== '') out.push('');
      return;
    }
    out.push(row.stamps.join('') + row.text);
  });
  while (out.length && out[out.length - 1] === '') out.pop();
  return out.join('\n');
}

/** [1:2.5] → [01:02.50]；只补零，不改时间值。 */
function normalizeLrcStamp(stamp) {
  const match = String(stamp).match(/\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/);
  if (!match) return stamp;
  const [, min, sec, frac] = match;
  const fraction = (frac || '00').padEnd(2, '0').slice(0, 2);
  return `[${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${fraction}]`;
}

function stampSeconds(stamp) {
  const match = String(stamp).match(/\[(\d{1,3}):(\d{1,2})(?:\.(\d{1,3}))?\]/);
  if (!match) return 0;
  const [, min, sec, frac] = match;
  return Number(min) * 60 + Number(sec) + Number((frac || '0').padEnd(3, '0')) / 1000;
}

/** 事件只绑一次：内容随 Tab 重画，全部走委托。 */
function bindLyricsControls(box) {
  if (box.dataset.lyricsBound === '1') return;
  box.dataset.lyricsBound = '1';
  box.addEventListener('click', (event) => {
    // 点面板外的暗色区就收起来
    if (event.target.dataset?.lsrc !== undefined) {
      closeLyricsSourcePanel();
      return;
    }
    const hit = event.target.closest?.('[data-lsrc-open],[data-lsrc-close],[data-lsrc-search],[data-lsrc-source],' +
      '[data-lsrc-fill],[data-me-format],[data-me-clear],[data-me-lyrics-save],' +
      '[data-me-find-prev],[data-me-find-next]');
    if (!hit || !box.contains(hit) || editor.tab !== 'lyrics') return;
    const lyr = editor.lyrics[editor.path];
    if (!lyr) return;
    if (hit.dataset.lsrcOpen !== undefined) {
      lyr.select.open = true;
      paintLyricsSource(box, lyr);
      return;
    }
    if (hit.dataset.lsrcClose !== undefined) {
      lyr.select.open = false;
      paintLyricsSource(box, lyr);
      return;
    }
    if (hit.dataset.lsrcSource !== undefined) {
      lyr.select.form.source = hit.dataset.lsrcSource;
      paintLyricsSource(box, lyr);
      return;
    }
    if (hit.dataset.lsrcSearch !== undefined) { searchLyricsSource(box, lyr); return; }
    if (hit.dataset.lsrcFill !== undefined) {
      const index = Number(hit.dataset.lsrcFill);
      // 候选自带词就别白跑一趟网络；没带词的才去音源取正文
      if (hit.dataset.needFetch === '1') fetchLyricsForCandidate(box, lyr, index);
      else fillLyricsFromCandidate(box, lyr, index);
      return;
    }
    if (hit.dataset.meFormat !== undefined) {
      const text = lyricsDraftText(lyr);
      if (!text.trim()) { toast('编辑区还没有歌词', true); return; }
      const next = formatLrc(text);
      if (next === text) { toast('格式已经很规整了'); return; }
      setLyricsDraft(box, lyr, next);
      toast('已整理结构，还没保存');
      return;
    }
    if (hit.dataset.meClear !== undefined) {
      if (!lyricsDraftText(lyr).trim()) { toast('编辑区已经是空的', true); return; }
      if (!confirm('清空编辑区里的全部歌词？\n（只清这里，不写文件；要落盘还得重新保存）')) return;
      setLyricsDraft(box, lyr, '');
      toast('已清空编辑区，文件没动');
      return;
    }
    if (hit.dataset.meLyricsSave !== undefined) { saveMediaLyrics(); return; }
    const total = box.querySelectorAll('.me-lrc-line[data-hitn]').length;
    if (hit.dataset.meFindPrev !== undefined) {
      if (!total) { toast('先在上面的搜索框里输入关键词', true); return; }
      lyr.hitIndex = (lyr.hitIndex - 1 + total) % total;
      setLyricsFindCount(box, lyr.hitIndex + 1, total);
      scrollLyricsHit(box, lyr);
      syncLyricsGutter(box, lyr);
      return;
    }
    if (hit.dataset.meFindNext !== undefined) {
      if (!total) { toast('先在上面的搜索框里输入关键词', true); return; }
      lyr.hitIndex = (lyr.hitIndex + 1) % total;
      setLyricsFindCount(box, lyr.hitIndex + 1, total);
      scrollLyricsHit(box, lyr);
      syncLyricsGutter(box, lyr);
    }
  });
  box.addEventListener('input', (event) => {
    if (editor.tab !== 'lyrics') return;
    const lyr = editor.lyrics[editor.path];
    if (!lyr) return;
    if (event.target.dataset?.meLyricsText !== undefined) {
      lyr.draft = event.target.value;
      renderLyricsPreview(box, lyr);
      syncLyricsGutter(box, lyr);
      syncLyricsStatus(box, lyr);
      return;
    }
    if (event.target.dataset?.meFind !== undefined) {
      lyr.query = event.target.value;
      lyr.hitIndex = 0;
      renderLyricsPreview(box, lyr);
      syncLyricsGutter(box, lyr);
    }
  });
  box.addEventListener('scroll', (event) => {
    if (event.target.dataset?.meLyricsText !== undefined) syncLyricsGutter(box, editor.lyrics[editor.path]);
  }, true);
  box.addEventListener('keydown', (event) => {
    const area = event.target;
    if (area.dataset?.meLyricsText === undefined) {
      // 搜索框里回车：跳到下一处
      if (event.target.dataset?.meFind !== undefined && event.key === 'Enter') {
        event.preventDefault();
        box.querySelector('[data-me-find-next]')?.click();
      }
      return;
    }
    if (event.key === 'Tab') {
      // Tab 键不跑出编辑器：就地在光标处补两个空格
      event.preventDefault();
      const start = area.selectionStart;
      const end = area.selectionEnd;
      area.value = `${area.value.slice(0, start)}  ${area.value.slice(end)}`;
      area.selectionStart = area.selectionEnd = start + 2;
      area.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    // 上下左右只多派发一次状态同步，光标行号跟着动
    if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      setTimeout(() => syncLyricsStatus(box, editor.lyrics[editor.path]), 0);
    }
  });
  box.addEventListener('keyup', (event) => {
    if (event.target.dataset?.meLyricsText === undefined) return;
    syncLyricsStatus(box, editor.lyrics[editor.path]);
  });
  box.addEventListener('blur', (event) => {
    if (event.target.dataset?.meLyricsText === undefined) return;
    syncLyricsStatus(box, editor.lyrics[editor.path]);
  }, true);
}

async function loadMediaLyrics(path) {
  const box = mediaLyricsState(path);
  if (box.loading) return;
  box.loading = true;
  box.error = '';
  paintMediaLyrics(mediaTrack() || { path });
  try {
    const detail = await api(`/api/library/detail?path=${encodeURIComponent(path)}`);
    box.text = detail.lyrics || '';
    box.draft = null;
    box.error = '';
    const row = (state.library.tracks || []).find((item) => item.path === path);
    if (row) Object.assign(row, detail, { _lyrics: detail.lyrics || '' });
  } catch (error) {
    box.text = '';
    box.error = error.message;
  } finally {
    box.loading = false;
    if (editor.path === path && editor.tab === 'lyrics') paintMediaLyrics(mediaTrack() || { path });
  }
}

async function saveMediaLyrics() {
  const path = editor.path;
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  const area = box?.querySelector('[data-me-lyrics-text]');
  if (!path || !area) return;
  const text = area.value.trim();
  if (!text) { toast('歌词为空，没东西可写', true); return; }
  try {
    await api('/api/library/lyrics', {
      method: 'PUT', body: { path, lyrics: text, dry_run: false },
    });
    const lyr = mediaLyricsState(path);
    lyr.text = text;
    lyr.draft = null;
    toast('歌词已写入');
    pushSessionLog('写入歌词', '歌词', path);
    await refreshLibraryTrack(path);
    paintLibraryBody();
    refreshMediaEditor();
  } catch (error) { toast(error.message, true); }
}

/** LRC 解析：[mm:ss.xx] 时间轴 → 逐行；解析不到时间轴就按纯文本返回。 */
function parseLrc(text) {
  const rows = [];
  const stamp = /^\s*((?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+)\s*(.*)$/;
  String(text || '').split(/\r?\n/).forEach((line) => {
    const match = line.match(stamp);
    if (!match) {
      if (line.trim()) rows.push({ stamp: '', text: line.trim() });
      return;
    }
    const times = match[1].match(/\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]/g) || [];
    times.forEach((time) => {
      rows.push({ stamp: time.replace(/[[\]]/g, ''), text: match[2].trim() });
    });
  });
  const isLrc = rows.some((row) => row.stamp);
  return { isLrc, rows };
}

/* ---- 操作历史：本次会话内真实做过的写入；后端没有历史接口，不做任何伪造 ---- */
function paintMediaTimeline(track) {
  const box = mediaEditorNode()?.querySelector('[data-me-body]');
  if (!box) return;
  const rows = sessionLogFor(track.path);
  box.innerHTML = `
    <div class="me-timeline">
      <div class="me-block-head">
        <span class="field-label">本次会话操作历史</span>
        <span class="badge">${rows.length ? `${rows.length} 条` : '暂无记录'}</span>
      </div>
      ${rows.length
        ? `<ol class="me-timeline-list">${rows.map((row) => `<li class="me-timeline-item">
            <span class="me-timeline-dot"></span>
            <div class="me-timeline-main">
              <div class="me-timeline-title">${esc(row.action)}</div>
              <div class="small muted">${esc(row.fields || '—')}</div>
            </div>
            <span class="me-timeline-time mono">${esc(row.at)}</span>
          </li>`).join('')}</ol>`
        : emptyBlock('i-clock', '暂无操作记录 · 这首曲目还没有产生任何写入操作')}
      <p class="small muted me-timeline-foot">后端没有提供操作历史接口，这里只记录本次会话内做过的写入操作（写入标签、写入歌词…），刷新页面后清空。</p>
    </div>`;
}

/** 事件只绑一次：反复开关编辑器不会叠加监听。 */
function bindMediaEditor() {
  const node = mediaEditorNode();
  if (!node || node.dataset.bound === '1') return;
  node.dataset.bound = '1';
  node.addEventListener('click', (event) => {
    if (event.target === node) closeMediaEditor();  // 点遮罩关闭
  });
  node.querySelector('[data-me-close]')?.addEventListener('click', closeMediaEditor);
  node.querySelectorAll('[data-me-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.dataset.meTab !== editor.tab) switchMediaTab(button.dataset.meTab);
    });
  });
}

/** 一条候选：左侧 1–10 匹配度（ok / warn / bad 三色），右侧元数据 + 可补字段。 */
function scrapeResultCard(item, index) {
  const meta = item.metadata || {};
  const fills = (item.fills || []).map(fieldName).join('、');
  return `<article class="scrape-result">
    <div class="scrape-score ${scoreTone(item.score)}" title="匹配度 1–10">
      <b>${item.score}</b><span>/10</span></div>
    <div class="scrape-main">
      <div class="scrape-title">${esc(meta.title || '—')}</div>
      <div class="scrape-sub">${esc([meta.artist, meta.album, meta.year].filter(Boolean).join(' · ') || '—')}</div>
      <div class="scrape-tags">
        <span class="badge">${esc(item.source_label || sourceLabel(meta.source))}</span>
        ${meta.track_number ? `<span class="badge">轨号 ${esc(meta.track_number)}</span>` : ''}
        ${meta.album_artist ? `<span class="badge">专辑艺术家 ${esc(meta.album_artist)}</span>` : ''}
        ${scoreTone(item.score) === 'bad' ? '<span class="badge warn">低于 7 分，不建议写入</span>' : ''}
        ${fills ? `<span class="badge info">可补：${esc(fills)}</span>` : '<span class="badge">没有能补的字段</span>'}
      </div>
    </div>
    <div class="btn-row">
      <button class="ghost-btn small" data-scrape-apply="${index}" data-dry="1">预览</button>
      <button class="primary-btn small" data-scrape-apply="${index}" data-dry="0">写入标签</button>
    </div>
  </article>`;
}

/* ---- 批量补全 / 只补这一首：只写标签，不动文件位置 ---- */
async function autoFillLibrary() {
  const sources = (state.library.sources || []).filter((item) => item.enabled);
  if (!sources.length) { toast('请先在「刮削设置」里启用至少一个在线音源', true); return; }
  let preview;
  try {
    preview = await api('/api/library/scan', { method: 'POST' });
  } catch (error) { toast(error.message, true); return; }
  if (!preview.incomplete) { toast('库里的曲目字段都齐了，没什么要补的'); return; }
  const sample = preview.tracks.slice(0, 5)
    .map((row) => `· ${row.name}：缺 ${(row.missing_labels || []).join('、')}`).join('\n');
  const more = preview.incomplete > 5 ? `\n… 另有 ${preview.incomplete - 5} 首` : '';
  if (!confirm(
    `自动扫描：${preview.incomplete} 首曲目缺字段\n${sample}${more}\n\n`
    + `将用 ${sources.map((item) => item.label).join(' / ')} 逐首搜索并只补空字段。\n`
    + '不会移动、复制、建硬链接或软链接任何文件。继续？'
  )) return;
  toast('正在扫描并补全，请稍候…');
  try {
    const payload = await api('/api/library/fill', {
      method: 'POST',
      body: { sources: sources.map((item) => item.id) },
    });
    toast(payload.message || `已补全 ${payload.filled} 首`);
    invalidateLibrary();
    await renderLibrary();
  } catch (error) { toast(error.message, true); }
}

async function fillOne(path) {
  const sources = (state.library.sources || []).filter((item) => item.enabled).map((item) => item.id);
  if (!sources.length) { toast('请先在「刮削设置」里启用至少一个在线音源', true); return; }
  try {
    const payload = await api('/api/library/fill', {
      method: 'POST', body: { sources, paths: [path] },
    });
    const row = (payload.results || [])[0] || {};
    const labels = (row.written || []).map(fieldName).join('、');
    toast(labels ? `已补全：${labels}` : (payload.message || '没有可补的字段'), !labels);
    if (labels) {
      pushSessionLog('只补这一首', labels, path);
      await refreshLibraryTrack(path);
      paintLibraryBody();
      if (mediaEditorOpen() && editor.path === path) refreshMediaEditor();
      return;
    }
    if (!mediaEditorOpen()) { invalidateLibrary(); await renderLibrary(); }
  } catch (error) { toast(error.message, true); }
}

/** 补全 / 写入只动一个文件：就地重读那一首，不重扫整库，统计卡跟着重算。 */
async function refreshLibraryTrack(path) {
  try {
    const detail = await api(`/api/library/detail?path=${encodeURIComponent(path)}`);
    const row = (state.library.tracks || []).find((item) => item.path === path);
    if (row) Object.assign(row, detail, { _lyrics: detail.lyrics || '' });
  } catch { invalidateLibrary(); }  // 重读失败就退回重新扫盘
}

/** 删除：本次不新增后端接口，点按只提示到文件管理器处理，绝不假装删掉。 */
function removeLibraryTrack(path) {
  toast(`音乐库不提供删除接口，请到文件管理器移除：${fileName(path)}`, true);
}

/** 库数据变了：连概览的短时缓存一起作废，下次进概览重新读，不会拿旧数字糊弄人。 */
function invalidateLibrary() {
  state.library.loaded = false;
  state.library.dashCache = null;
}

async function renderLibrary() {
  const store = state.library;
  if (!store.loaded && !store.loading) {
    $('page').innerHTML = `<div class="panel">${emptyBlock('i-library', '正在扫描音乐库…')}</div>`;
    store.loading = true;
    try {
      const data = await api('/api/library');
      store.tracks = data.tracks || [];
      store.stats = data.stats || {};
      store.sources = data.sources || [];
      store.libraryDir = data.library_dir || '';
      store.warnings = data.warnings || [];
      store.writeTags = data.write_tags;
      store.loaded = true;
    } catch (error) {
      store.loading = false;
      $('page').innerHTML = `<div class="panel">${emptyBlock('i-library', error.message)}</div>`;
      return;
    } finally { store.loading = false; }
  }
  const stats = store.stats || {};
  const sourceChips = (store.sources || []).map((item) => `
    <span class="badge ${item.enabled ? 'info' : ''}">${esc(item.label)}${item.enabled ? '' : '（未启用）'}</span>`).join('');
  const visible = libraryVisible();
  const counts = libraryTabCounts(visible);
  const searchFields = '搜索标题 / 歌手 / 专辑 / 专辑艺术家 / 文件名';
  $('page').innerHTML = `
    <div class="banner info">批量补全只写标签：不移动、不复制、不建硬链接或软链接，文件位置一律不变。</div>
    ${libraryStats()}
    <div class="panel lib-panel">
      <div class="panel-head">
        <div><h2>全部曲目</h2>
          <p class="mono small">${esc(store.libraryDir)} · 曲目 ${stats.tracks || 0} · 封面 ${stats.with_cover || 0} · 歌词 ${stats.with_lyrics || 0}${stats.incomplete ? ` · 待补全 ${stats.incomplete}` : ''}</p></div>
        <div class="btn-row">
          <button class="ghost-btn" id="lib-auto">批量补全</button>
          <button class="ghost-btn" id="lib-reload">刷新</button>
        </div>
      </div>
      <div class="lib-filterbar">
        <label class="lib-search-field">
          ${spriteIcon('i-search')}
          <input id="lib-search" class="lib-search" type="search" placeholder="${searchFields}" value="${esc(store.query)}">
        </label>
        <div class="lib-selects">
          ${store.tab === 'genres' ? `<select id="lib-genre" class="lib-select">
            <option value="">全部流派</option>
            ${libraryGenres().map((name) => `<option value="${esc(name)}"${name === store.genre ? ' selected' : ''}>${esc(name)}</option>`).join('')}
          </select>` : ''}
          <select id="lib-lyric" class="lib-select">
            ${LYRIC_FILTERS.map(([id, label]) => `<option value="${id}"${store.lyricFilter === id ? ' selected' : ''}>${label}</option>`).join('')}
          </select>
          <select id="lib-cover" class="lib-select">
            ${COVER_FILTERS.map(([id, label]) => `<option value="${id}"${store.coverFilter === id ? ' selected' : ''}>${label}</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="lib-toolrow">
        <div class="btn-row">
          <button class="ghost-btn small" id="lib-filter-btn">过滤</button>
          <button class="ghost-btn small" id="lib-reset">重置</button>
          <button class="ghost-btn small" id="lib-refresh">刷新</button>
        </div>
        <div class="lib-toolrow-right">
          <span class="small muted" id="lib-count"></span>
          <div class="lib-viewswitch" role="group" aria-label="视图切换">
            <button class="icon-btn small${store.view === 'table' ? ' active' : ''}" data-lib-view="table"
              title="表格视图" aria-label="表格视图">${spriteIcon('i-table')}</button>
            <button class="icon-btn small${store.view === 'grid' ? ' active' : ''}" data-lib-view="grid"
              title="网格视图" aria-label="网格视图">${spriteIcon('i-grid')}</button>
          </div>
          <span id="lib-pager-top" class="lib-pager-slot"></span>
        </div>
      </div>
      <div class="lib-sublabel small muted">可用的刮削来源：${sourceChips || '—'}（在「刮削设置 → 标签与元数据」里勾选）</div>
      <div class="lib-tabs" role="tablist">
        ${LIBRARY_TABS.map(([id, label]) => `<button class="lib-tab${store.tab === id ? ' active' : ''}" data-lib-tab="${id}" role="tab">
          ${esc(label)} <em>${counts[id] || 0}</em></button>`).join('')}
      </div>
      <div id="lib-body" class="lib-body"></div>
      <div id="lib-pager-main" class="lib-pager-main"></div>
    </div>`;

  paintLibraryBody();

  // 工具条、Tab、筛选、分页的监听统一在 paintLibraryBody → bindLibraryActions 里挂，
  // 每次重绘都是新节点，就地重挂不依赖事件委托。
  $('lib-reload').onclick = () => { invalidateLibrary(); renderLibrary(); };
  $('lib-auto').onclick = () => autoFillLibrary();
}

async function renderFiles() {
  let data;
  try { data = await api('/api/files'); }
  catch (error) { toast(error.message, true); return; }
  state.files = data.files;
  banner((data.warnings || []).join('；'), 'info');
  const rows = data.files.map((file) => {
    const meta = file.local_metadata || {};
    return `<tr>
      <td><input type="checkbox" class="pick-box" data-pick="${esc(file.path)}" ${state.selected.has(file.path) ? 'checked' : ''}></td>
      <td class="truncate" title="${esc(file.path)}">${esc(file.display_name)}</td>
      <td>${esc(meta.title || '—')}</td><td>${esc(meta.artist || '—')}</td><td>${esc(meta.album || '—')}</td>
      <td class="muted">${fmtSize(file.size)}</td><td class="muted">${fmtDuration(file.duration)}</td>
      <td><button class="ghost-btn small" data-detail="${esc(file.path)}">详情</button></td></tr>`;
  }).join('');
  $('page').innerHTML = `
    <div class="page-stack">
      <div class="panel">
        <div class="panel-head"><div><h2>源目录</h2>
        <p>${esc(data.source_dir)} · 共扫描到 ${data.total} 个音频文件${data.truncated ? '（列表只显示前 1000 个）' : ''}</p></div>
        <div class="btn-row">
          <button class="ghost-btn" id="refresh-files">重新扫描</button>
          <button class="ghost-btn" id="select-all">全选</button>
          <button class="ghost-btn" id="clear-selection">清空选择</button>
        </div></div>
      <div class="table-wrap"><table class="files-table"><thead><tr>
        <th class="col-pick"></th><th>文件名</th><th>标题</th><th>歌手</th><th>专辑</th><th>大小</th><th>时长</th><th>操作</th>
      </tr></thead><tbody>${rows || emptyCell('i-folder', '源目录中没有找到音频文件', 8)}</tbody></table></div>
    </div>
    <div class="panel action-bar">
      <div class="action-bar-main">
        <h2>对选中的 <span id="selection-count">${state.selected.size}</span> 个文件执行</h2>
        <div class="action-bar-opts">
          <label class="check-line"><input type="checkbox" id="opt-organize" ${state.options.organize ? 'checked' : ''}>整理到音乐库（关闭则只写标签）</label>
          <label class="check-line"><input type="checkbox" id="opt-skip" ${state.options.skipLowConfidence ? 'checked' : ''}>跳过低置信度文件</label>
        </div>
      </div>
      <div class="btn-row action-bar-btns">
        <button class="ghost-btn" id="preview-btn">① 生成整理计划（预览）</button>
        <button class="primary-btn" id="scrape-btn">② 直接刮削整理</button>
        <span class="small muted">当前方式：<b>${esc(modeLabel(state.settings.transfer_mode))}</b> · 建议先预览</span>
      </div>
    </div>
    </div>`;
  $('refresh-files').addEventListener('click', renderFiles);
  $('select-all').addEventListener('click', () => { state.files.forEach((f) => state.selected.add(f.path)); renderFiles(); });
  $('clear-selection').addEventListener('click', () => { state.selected.clear(); renderFiles(); });
  document.querySelectorAll('[data-pick]').forEach((box) => box.addEventListener('change', () => {
    if (box.checked) state.selected.add(box.dataset.pick); else state.selected.delete(box.dataset.pick);
    $('selection-count').textContent = state.selected.size;
  }));
  document.querySelectorAll('[data-detail]').forEach((button) => button.addEventListener('click', () => showFileDetail(button.dataset.detail)));
  $('opt-organize').addEventListener('change', (e) => { state.options.organize = e.target.checked; });
  $('opt-skip').addEventListener('change', (e) => { state.options.skipLowConfidence = e.target.checked; });
  $('preview-btn').addEventListener('click', () => createJob('preview', true));
  $('scrape-btn').addEventListener('click', () => {
    if (!confirm('将按当前设置写标签并把文件转移到音乐库。确认执行？')) return;
    createJob('scrape', false);
  });
}

async function showFileDetail(path) {
  let data;
  try { data = await api('/api/files/detail', { method: 'POST', body: { path } }); }
  catch (error) { toast(error.message, true); return; }
  const tags = data.embedded_tags || {};
  const guess = data.filename_guess || {};
  const row = (label, value) => `<tr><th class="th-key">${esc(label)}</th><td>${esc(value || '—')}</td></tr>`;
  openModal('文件详情', `
    <div class="table-wrap"><table><tbody>
      ${row('路径', data.file.path)}${row('大小', fmtSize(data.file.size))}
      ${row('时长', fmtDuration(data.file.duration))}${row('码率', data.technical?.bitrate ? `${Math.round(data.technical.bitrate / 1000)} 千比特/秒` : '—')}
      ${row('内嵌封面', data.has_embedded_cover ? '有' : '无')}
    </tbody></table></div>
    <div class="divider"></div>
    <div class="grid">
      <div><h3>文件内已有标签</h3><div class="table-wrap"><table><tbody>
        ${row('标题', tags.title)}${row('歌手', tags.artist)}${row('专辑', tags.album)}
        ${row('年份', tags.year)}${row('音轨', tags.track_number)}
      </tbody></table></div></div>
      <div><h3>文件名 / 目录推断</h3><div class="table-wrap"><table><tbody>
        ${row('标题', guess.title)}${row('歌手', guess.artist)}${row('专辑', guess.album)}
        ${row('年份', guess.year)}${row('音轨', guess.track_number)}
      </tbody></table></div></div>
    </div>
    <div class="divider"></div>
    <p class="small muted">按当前命名模板生成的目标路径（未查询在线元数据）：</p>
    <p class="mono">${esc(data.preview_target)}</p>`);
}

/* ---------------- 整理计划：可视化预览 ---------------- */
const PLAN_FILTERS = [
  ['all', '全部'], ['needs_review', '需确认'], ['matched', '已匹配'], ['failed', '异常'],
];

function coverHref(item) {
  const online = item.cover_url ? `/api/cover?url=${encodeURIComponent(item.cover_url)}` : '';
  const local = item.source ? `/api/cover?path=${encodeURIComponent(item.source)}` : '';
  return online || local;
}
function planChoice(item) {
  const entry = state.plan.choices.get(item.source) || { index: -2 };
  const options = item.candidates || [];
  if (entry.index === -1) return { index: -1, meta: localMetaOf(item) };
  const candidate = options[entry.index];
  if (candidate && candidate.metadata) {
    const meta = metaOf(candidate.metadata);
    return { index: entry.index, meta: entry.edits ? { ...meta, ...entry.edits } : meta };
  }
  const meta = itemFromPlan(item);
  return { index: -2, meta: entry.edits ? { ...meta, ...entry.edits } : meta };
}
function localMetaOf(item) {
  return metaOf({
    title: item.local_title, artist: item.local_artist, album: item.local_album,
    album_artist: item.local_album_artist, year: item.local_year,
    track_number: item.local_track_number,
  });
}
function itemFromPlan(item) {
  return metaOf({
    title: item.title, artist: item.artist, album: item.album,
    album_artist: item.album_artist, year: item.year,
    track_number: item.track_number,
  });
}
function metaOf(values) {
  const artist = values.artist || '';
  return {
    title: values.title || '', artist, album: values.album || '',
    album_artist: values.album_artist || artist || '',
    year: values.year || '', track_number: values.track_number || '',
  };
}
function planCount() {
  let picked = 0;
  state.plan.choices.forEach((entry) => { if (entry.index > -2) picked += 1; });
  return picked;
}
function planFilter(item) {
  const filter = state.plan.filter;
  if (filter === 'needs_review') return item.status === 'needs_review';
  if (filter === 'failed') return item.status === 'failed' || item.target === '';
  if (filter === 'matched') return item.status === 'planned' && item.source_name !== 'local';
  return true;
}
function confidenceTone(value) {
  if (value >= 0.8) return 'ok';
  if (value >= 0.62) return 'warn';
  return 'bad';
}

function planCard(item, index) {
  const choice = planChoice(item);
  const meta = choice.meta;
  const candidates = item.candidates || [];
  const chosenLabel = choice.index === -2
    ? `自动结果 · ${esc(sourceLabel(item.source_name))}`
    : choice.index === -1 ? '保留文件原标签' : `候选 ${choice.index + 1} · ${esc(sourceLabel(candidates[choice.index]?.metadata?.source))}`;
  const candidateButtons = candidates.map((candidate, i) => {
    const m = candidate.metadata || {};
    return `<button class="cand-chip${choice.index === i ? ' active' : ''}" data-cand="${i}" data-idx="${index}"
      title="${esc([m.title, m.artist, m.album, m.year].filter(Boolean).join(' · '))}">
      <b>${esc(m.title || '—')}</b><span>${esc(m.artist || '')}${m.album ? ` · ${esc(m.album)}` : ''}</span>
      <em class="conf ${confidenceTone(candidate.score || 0)}">${(candidate.score || 0).toFixed(2)}</em></button>`;
  }).join('');
  const field = (key, label) => {
    const before = item[`local_${key}`] ?? '';
    const changed = String(before || '') !== String(meta[key] || '');
    return `<div class="diff-row">
      <span class="diff-key">${esc(label)}</span>
      <span class="diff-before${changed ? ' changed' : ''}">${esc(before || '—')}</span>
      <span class="diff-arrow">→</span>
      <input class="diff-input" data-field="${key}" data-idx="${index}" value="${esc(meta[key] || '')}" placeholder="—">
    </div>`;
  };
  const tone = confidenceTone(item.confidence || 0);
  return `<article class="plan-card" data-idx="${index}">
    <div class="plan-cover">
      ${item.cover_url || item.source
        ? `<img src="${esc(coverHref(item))}" alt="" loading="lazy" data-cover="${index}">`
        : ''}
      <span class="plan-cover-fallback">♪</span>
    </div>
    <div class="plan-main">
      <div class="plan-head">
        <div class="plan-titles">
          <div class="plan-title">${esc(meta.title || fileName(item.source))}</div>
          <div class="plan-sub">${esc([meta.artist, meta.album, meta.year].filter((x) => x).join(' · ')) || '—'}</div>
        </div>
        <div class="plan-flags">
          ${item.status === 'needs_review' || choice.index > -2 ? '<span class="badge warn">待确认</span>' : statusBadge(item.status)}
          <span class="conf ${tone}">${(item.confidence || 0).toFixed(2)}</span>
        </div>
      </div>
      <div class="plan-source mono" title="${esc(item.source)}">${esc(shortPath(item.source))}</div>
      <div class="diff">
        ${field('title', '标题')}${field('artist', '歌手')}${field('album', '专辑')}
        ${field('album_artist', '专辑歌手')}${field('year', '年份')}${field('track_number', '轨号')}
      </div>
      <div class="plan-target mono" title="${esc(item.target)}">${esc(shortPath(item.target) || '—')}</div>
      <div class="plan-foot">
        <span class="small muted">${chosenLabel}</span>
        <span class="small muted truncate" title="${esc(item.detail)}">${esc(item.detail)}</span>
      </div>
      ${candidates.length ? `<div class="cand-row">${candidateButtons}</div>` : ''}
      ${candidates.length || (item.local_title && choice.index !== -1) ? `<div class="cand-row">
        <button class="cand-chip${choice.index === -2 ? ' active' : ''}" data-cand="-2" data-idx="${index}">
          <b>自动结果</b><span>${esc(sourceLabel(item.source_name))}</span></button>
        <button class="cand-chip${choice.index === -1 ? ' active' : ''}" data-cand="-1" data-idx="${index}">
          <b>保留原标签</b><span>${esc(item.local_title || '无')}</span></button>
      </div>` : ''}
    </div>
  </article>`;
}

function renderPlanCards() {
  const items = state.plan.items || [];
  const visible = items.map((item, index) => ({ item, index })).filter(({ item }) => planFilter(item));
  $('plan-grid').innerHTML = visible.map(({ item, index }) => planCard(item, index)).join('')
    || emptyBlock('i-funnel', '当前筛选条件下没有条目');
  $('plan-grid').querySelectorAll('[data-cand]').forEach((button) => {
    button.addEventListener('click', () => {
      const index = Number(button.dataset.idx);
      const item = state.plan.items[index];
      state.plan.choices.set(item.source, { index: Number(button.dataset.cand) });
      renderPlanCards();
      updatePlanBar();
    });
  });
  $('plan-grid').querySelectorAll('[data-field]').forEach((input) => {
    input.addEventListener('change', () => {
      const index = Number(input.dataset.idx);
      const item = state.plan.items[index];
      const current = planChoice(item);
      const meta = { ...current.meta };
      const key = input.dataset.field;
      meta[key] = key === 'year' || key === 'track_number'
        ? (Number(input.value) || '') : input.value.trim();
      let entry = state.plan.choices.get(item.source);
      if (!entry || entry.index === -2) {
        const base = (item.candidates || []).findIndex((c) => (
          (c.metadata?.title || '') === (current.meta.title || '')
          && (c.metadata?.artist || '') === (current.meta.artist || '')
        ));
        entry = { index: base, edits: {} };
      }
      entry = { ...entry, edits: { ...(entry.edits || {}), [key]: meta[key] } };
      state.plan.choices.set(item.source, entry);
      updatePlanBar();
    });
  });
}

function updatePlanBar() {
  const picked = planCount();
  const total = (state.plan.items || []).length;
  const run = $('run-plan');
  if (run) run.textContent = picked
    ? `按选择执行整理（${picked} 处人工调整）`
    : '按此计划执行整理';
  const note = $('plan-picked');
  if (note) note.textContent = String(picked);
  const hint = $('plan-hint');
  if (hint) hint.textContent = picked
    ? `已人工调整 ${picked} / ${total} 项，其余沿用自动结果`
    : `全部沿用自动结果（共 ${total} 项）；点选候选或改标签即可覆盖`;
}

async function renderPlan() {
  const job = state.previewJob;
  if (!job) {
    $('page').innerHTML = `<div class="page-stack"><div class="panel panel-tight">${emptyBar('i-plan',
      '还没有整理计划，去「源文件」选择文件后点「生成整理计划（预览）」', 'files', '前往源文件')}</div></div>`;
    document.querySelectorAll('[data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));
    return;
  }
  if (!job.created_at) {
    try { job.created_at = (await api(`/api/jobs/${job.id}`)).job.created_at; }
    catch (_) { /* timestamp is cosmetic */ }
  }
  if (state.plan.jobId !== job.id) {
    state.plan = { jobId: job.id, items: [], choices: new Map(), filter: 'all' };
    try {
      state.plan.items = (await api(`/api/jobs/${job.id}/items?limit=2000`)).items || [];
    } catch (error) { toast(error.message, true); }
  }
  const items = state.plan.items;
  const review = items.filter((item) => item.status === 'needs_review').length;
  const matched = items.filter((item) => item.source_name && item.source_name !== 'local').length;
  $('page').innerHTML = `
    <div class="panel">
      <div class="panel-head"><div><h2>整理计划预览</h2>
        <p>${esc(job.id)} · ${job.dry_run ? '预览结果，尚未写入标签或移动任何文件' : '已执行任务'} · ${fmtTime(job.created_at)}</p></div>
        <div class="btn-row">
          <button class="ghost-btn" id="plan-csv">导出表格（CSV）</button>
          <button class="ghost-btn" id="plan-table">表格视图</button>
          ${job.dry_run ? '<button class="primary-btn" id="run-plan">按此计划执行整理</button>' : ''}
        </div></div>
      <div class="plan-stats">
        <div class="plan-stat"><b>${items.length}</b><span>条目</span></div>
        <div class="plan-stat ok"><b>${matched}</b><span>在线匹配</span></div>
        <div class="plan-stat warn"><b>${review}</b><span>需确认</span></div>
        <div class="plan-stat"><b id="plan-picked">0</b><span>人工调整</span></div>
      </div>
      <div class="plan-bar">
        ${PLAN_FILTERS.map(([id, label]) => `<button class="ghost-btn small${state.plan.filter === id ? ' active' : ''}" data-filter="${id}">${label}</button>`).join('')}
        <span class="small muted" id="plan-hint"></span>
      </div>
      <div id="plan-grid" class="plan-grid plan-grid-cards"></div>
      <div id="plan-table-wrap" class="hidden"></div>
      <div class="divider"></div>
      <h3>执行说明</h3>
      <p class="muted small" style="margin-top:8px">标签会在文件转移之前写入源文件。复制与移动之后目标文件独立；硬链接与软链接的目标共享源文件数据，源文件的标签也会一起被补全。详见「转移方式说明」。</p>
    </div>`;
  renderPlanCards();
  updatePlanBar();

  $('plan-grid').querySelectorAll('img[data-cover]').forEach((img) => {
    img.addEventListener('error', () => img.classList.add('hidden'), { once: true });
  });
  document.querySelectorAll('[data-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      state.plan.filter = button.dataset.filter;
      document.querySelectorAll('[data-filter]').forEach((b) => b.classList.toggle('active', b === button));
      renderPlanCards();
    });
  });
  $('plan-csv').addEventListener('click', () => { window.location.href = `/api/jobs/${job.id}/report`; });
  $('plan-table').addEventListener('click', () => togglePlanTable(items));
  const run = $('run-plan');
  if (run) run.addEventListener('click', async () => {
    const choices = buildChoices();
    const count = Object.keys(choices).length;
    const outline = count ? `其中 ${count} 个文件按你的人工选择写入。` : '';
    if (!confirm(`确认处理 ${items.length} 个文件？将写入标签并使用「${state.settings.transfer_mode}」方式转移。${outline}`)) return;
    await createJob('scrape', false, items.map((item) => item.source), choices);
  });
}

function buildChoices() {
  const choices = {};
  state.plan.choices.forEach((entry, source) => {
    const item = (state.plan.items || []).find((row) => row.source === source);
    if (!item) return;
    let payload = null;
    if (entry.index === -1) {
      payload = metaOf({
        title: item.local_title, artist: item.local_artist, album: item.local_album,
        album_artist: item.local_album_artist, year: item.local_year,
        track_number: item.local_track_number,
      });
      payload.source = 'manual';
    } else {
      const candidate = (item.candidates || [])[entry.index];
      if (candidate && candidate.metadata) {
        payload = metaOf(candidate.metadata);
        payload.source = candidate.metadata.source || 'manual';
        payload.cover_url = candidate.metadata.cover_url || null;
        payload.external_id = candidate.metadata.external_id || '';
      }
    }
    if (!payload) {
      if (!entry.edits) return;
      payload = planChoice(item).meta;
      payload.source = item.source_name || 'manual';
    }
    if (entry.edits) payload = { ...payload, ...entry.edits };
    // An untouched auto result needs no override at all.
    if (entry.index === -2 && !entry.edits) return;
    choices[source] = {
      title: String(payload.title || ''),
      artist: payload.artist || null,
      album: payload.album || null,
      album_artist: payload.album_artist || payload.artist || null,
      year: payload.year === '' ? null : Number(payload.year) || null,
      track_number: payload.track_number === '' ? null : Number(payload.track_number) || null,
      cover_url: payload.cover_url || null,
      source: payload.source || 'manual',
      external_id: payload.external_id || '',
    };
  });
  return choices;
}

function togglePlanTable(items) {
  const wrap = $('plan-table-wrap');
  if (!wrap.classList.contains('hidden')) { wrap.classList.add('hidden'); return; }
  const rows = items.map((item) => `<tr>
      <td class="truncate" title="${esc(item.source)}">${esc(fileName(item.source))}</td>
      <td>${esc(item.title || '—')}</td><td>${esc(item.artist || '—')}</td>
      <td class="mono truncate" title="${esc(item.target)}">${esc(item.target)}</td>
      <td>${(item.confidence || 0).toFixed(2)}</td>
      <td>${item.status === 'needs_review' ? '<span class="badge warn">需人工确认</span>' : statusBadge(item.status)}</td>
    </tr>`).join('');
  wrap.innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>源文件</th><th>标题</th><th>歌手</th><th>目标路径</th><th>置信度</th><th>状态</th>
    </tr></thead><tbody>${rows || emptyCell('i-plan', '没有计划项', 6)}</tbody></table></div>`;
  wrap.classList.remove('hidden');
}

async function renderJobs() {
  let jobs = [];
  try { jobs = (await api('/api/jobs?limit=50')).jobs || []; }
  catch (error) { toast(error.message, true); }
  state.jobs = jobs;
  const active = jobs.find((job) => job.status === 'running' || job.status === 'pending');
  const progressCard = active ? `
    <div class="panel"><div class="panel-head"><div><h2>当前任务</h2>
      <p>${esc(jobLabel(active))} · ${esc(active.message || '正在处理')} · <span class="mono">${esc(active.id)}</span></p></div>
      <button class="danger-btn" id="cancel-job">取消任务</button></div>
      <div class="progress"><span style="width:${Math.round(100 * (active.processed || 0) / Math.max(1, active.total || active.processed || 1))}%"></span></div>
      <div class="between small muted" style="margin-top:8px">
        <span>已处理 ${active.processed || 0} / ${active.total || '—'}</span>
        <span>成功 ${active.done || 0} · 跳过 ${active.skipped || 0} · 失败 ${active.failed || 0}</span></div>
    </div>` : '';
  const rows = jobs.map((job) => {
    const running = job.status === 'running' || job.status === 'pending';
    return `<tr>
    <td class="truncate" title="${esc(jobNote(job))}">${esc(jobLabel(job))}</td>
    <td>${job.dry_run ? '预览' : jobKind(job)}</td>
    <td>${statusBadge(job.status)}</td>
    <td class="muted">${esc(job.processed || 0)} / ${esc(job.total || 0)}</td>
    <td class="muted small">${esc(fmtTime(job.created_at))}</td>
    <td><div class="btn-row nowrap">
      <button class="ghost-btn small" data-job="${esc(job.id)}">明细</button>
      <a class="ghost-btn small" href="/api/jobs/${esc(job.id)}/report">导出表格</a>
      ${job.dry_run ? `<button class="ghost-btn small" data-plan="${esc(job.id)}">打开计划</button>` : ''}
      <button class="ghost-btn small danger-text" data-del="${esc(job.id)}" ${running ? 'disabled title="任务正在运行，先取消再删除"' : ''}>删除</button>
    </div></td></tr>`;
  }).join('');
  const watch = state.watch || {};
  /* 自动刮削的开关只留在「刮削设置」（唯一可配置处），这里只放一个跳到那里的入口；
   * 页面标题右侧一排工具条：刷新 / 调整自动刮削 / 清空记录。 */
  $('page').innerHTML = `<div class="page-stack">${progressCard}
    <div class="panel">
      <div class="panel-head"><div><h2>全部任务</h2>
        <p>点「明细」右侧滑出详情；每次只允许一个任务运行，避免文件竞争</p></div>
      <div class="btn-row">
        <button class="ghost-btn" id="refresh-jobs">刷新</button>
        <button class="ghost-btn danger-text" id="clear-jobs" ${jobs.length ? '' : 'disabled'}>清空记录</button>
        <button class="ghost-btn" id="tune-watch">${watch.enabled ? '调整自动刮削' : '开启自动刮削'}</button>
      </div></div>
      <div class="table-wrap"><table class="jobs-table"><thead><tr><th>文件 / 歌曲名</th><th>类型</th><th>状态</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>${rows || emptyCell('i-jobs', '暂无任务', 6)}</tbody></table></div>
    </div>
    </div>`;
  $('tune-watch').addEventListener('click', () => go('settings'));
  $('refresh-jobs').addEventListener('click', renderJobs);
  document.querySelectorAll('[data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));
  const cancel = $('cancel-job');
  if (cancel) cancel.addEventListener('click', async () => {
    try { await api(`/api/jobs/${active.id}/cancel`, { method: 'POST' }); toast('已请求取消'); await renderJobs(); }
    catch (error) { toast(error.message, true); }
  });
  document.querySelectorAll('[data-job]').forEach((b) => b.addEventListener('click', () => showJobItems(b.dataset.job)));
  document.querySelectorAll('[data-plan]').forEach((b) => b.addEventListener('click', () => {
    state.previewJob = { id: b.dataset.plan, dry_run: true };
    go('plan');
  }));
  document.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    const job = (state.jobs || []).find((row) => row.id === b.dataset.del) || {};
    if (!confirm(`删除任务记录「${jobLabel(job)}」？\n明细会一起删掉，音乐库里已经整理好的文件不受影响。`)) return;
    try {
      await api(`/api/jobs/${b.dataset.del}`, { method: 'DELETE' });
      if (state.drawer.jobId === b.dataset.del) closeDrawer();
      toast('任务记录已删除');
      await renderJobs();
    } catch (error) { toast(error.message, true); }
  }));
  const clear = $('clear-jobs');
  if (clear) clear.addEventListener('click', async () => {
    if (!confirm('清空全部任务记录？\n明细会一起删除，正在运行的任务会保留，音乐库里的文件不受影响。')) return;
    try {
      const result = await api('/api/jobs', { method: 'DELETE' });
      closeDrawer();
      toast(result.message || '任务记录已清空');
      await renderJobs();
    } catch (error) { toast(error.message, true); }
  });
}

/* 任务明细：从右侧滑出的抽屉，不再把页面拉到最下面去找。 */
function openDrawer(title, sub) {
  $('drawer-title').textContent = title;
  $('drawer-sub').textContent = sub || '';
  $('drawer').classList.remove('hidden');
  $('drawer').setAttribute('aria-hidden', 'false');
  document.body.classList.add('drawer-open');
}
function closeDrawer() {
  state.drawer.items = [];
  $('drawer').classList.add('hidden');
  $('drawer').setAttribute('aria-hidden', 'true');
  document.body.classList.remove('drawer-open');
  state.drawer.jobId = null;
}

async function showJobItems(jobId) {
  const job = (state.jobs || []).find((row) => row.id === jobId) || {};
  let items = [];
  openDrawer('任务明细', `${jobLabel(job)} · ${statusLabel(job.status)} · ${fmtTime(job.created_at)}`);
  $('drawer-body').innerHTML = emptyBlock('i-jobs', '正在读取明细…');
  $('drawer-stat').textContent = '';
  $('drawer-csv').href = `/api/jobs/${encodeURIComponent(jobId)}/report`;
  try { items = (await api(`/api/jobs/${jobId}/items?limit=500`)).items || []; }
  catch (error) { $('drawer-body').innerHTML = emptyBlock('i-jobs', error.message); return; }
  state.drawer.jobId = jobId;
  const done = items.filter((item) => item.status === 'done').length;
  const failed = items.filter((item) => item.status === 'failed').length;
  const skipped = items.filter((item) => item.status === 'skipped').length;
  $('drawer-stat').textContent = items.length
    ? `共 ${items.length} 项 · 成功 ${done} · 跳过 ${skipped} · 失败 ${failed} · 任务号 ${jobId}`
    : `任务号 ${jobId}`;
  state.drawer.items = items;
  const rows = items.map((item, index) => `<tr>
    <td class="path-cell"><button class="song-link" data-item="${index}" title="查看刮削前后的标签对比">
        <span class="path-name">${esc(fileName(item.source))}</span></button>
      <div class="path-dir with-title" data-full="${esc(dirName(item.source))}">${esc(shortPath(dirName(item.source)))}</div></td>
    <td class="path-cell"><div class="path-name" title="${esc(fileName(item.target))}">${esc(fileName(item.target) || '—')}</div>
      <div class="path-dir with-title" data-full="${esc(dirName(item.target))}">${esc(item.target ? shortPath(dirName(item.target)) : '—')}</div></td>
    <td class="mode-cell">${esc(item.mode_label || modeLabel(item.mode))}</td>
    <td>${statusBadge(item.status)}</td>
    <td class="muted small detail-cell with-title" data-full="${esc(item.detail)}">${esc(detailBrief(item))}</td></tr>`).join('');
  $('drawer-body').innerHTML = items.length
    ? `<p class="small muted drawer-tip">点歌曲名查看刮削前后的标签对比卡片</p>
      <div class="table-wrap"><table class="items-table"><thead><tr>
      <th>源文件</th><th>目标</th><th>方式</th><th>状态</th><th>说明</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`
    : emptyBlock('i-jobs', '这次任务没有明细');
  // 悬停提示在渲染后统一绑定，避免把长路径塞进 innerHTML 属性。
  $('drawer-body').querySelectorAll('.with-title').forEach((node) => { node.title = node.dataset.full || ''; });
  $('drawer-body').querySelectorAll('[data-item]').forEach((node) => {
    node.addEventListener('click', () => showItemDiff(items[Number(node.dataset.item)]));
  });
}

/* 刮削前后对比：单个文件一张卡，左右并排「原标签 → 新标签」。 */
const DIFF_FIELDS = [
  ['title', '歌曲名'], ['year', '年份'], ['album', '专辑'],
  ['album_artist', '专辑艺术家'], ['artist', '歌手'],
];
// 歌词太长，卡片里只比「有没有 / 多少行」，完整内容折叠在后面
const lyricsState = (item, local) => {
  const flag = local ? 'has_local_lyrics' : 'has_lyrics';
  if (typeof item[flag] === 'boolean') return item[flag];
  const text = local ? item.local_lyrics : item.lyrics;
  return Boolean(text && String(text).trim());
};
const lyricsLines = (item) => (String(item.lyrics || '').trim()
  ? String(item.lyrics).trim().split('\n').filter((line) => line.trim()).length : 0);
const itemValue = (item, key) => {
  const raw = item[key];
  return raw === null || raw === undefined || String(raw).trim() === '' ? '' : String(raw);
};
const localValue = (item, key) => {
  const raw = item[`local_${key}`];
  return raw === null || raw === undefined || String(raw).trim() === '' ? '' : String(raw);
};

function diffCell(value, blank = '文件里没有') {
  return value ? esc(value) : `<span class="diff-blank">${esc(blank)}</span>`;
}

function diffRow(item, key, label) {
  const before = localValue(item, key);
  const after = itemValue(item, key);
  // 旧值被改写才划掉并标红，只是补空的旧值保持原样
  const replaced = before && after && before !== after;
  const filled = !before && after;
  const tone = replaced ? 'replaced' : filled ? 'filled' : before ? '' : 'missing';
  return `<div class="song-diff-row">
    <span class="diff-key">${esc(label)}</span>
    <span class="side before${replaced ? ' replaced' : ''}${before ? '' : ' blank'}">${diffCell(before)}</span>
    <span class="diff-arrow">→</span>
    <span class="side after${tone === 'missing' ? ' blank' : ''}${filled ? ' filled' : ''}">${diffCell(after)}</span>
  </div>`;
}

function lyricsRow(item) {
  const before = lyricsState(item, true);
  const after = lyricsState(item, false);
  const lines = lyricsLines(item);
  const replaced = before && after;
  const filled = !before && after;
  const tone = replaced ? 'replaced' : filled ? 'filled' : before ? '' : 'missing';
  const beforeText = before ? '文件里有歌词' : '文件里没有';
  const afterText = after
    ? `写入 ${lines || ''}${lines ? ' 行' : '歌词'}`.trim()
    : '没有歌词';
  const detail = after && item.lyrics
    ? `<details class="song-lyrics"><summary>查看歌词</summary><pre>${esc(String(item.lyrics).slice(0, 4000))}</pre></details>`
    : '';
  return `<div class="song-diff-row">
    <span class="diff-key">歌词</span>
    <span class="side before${replaced ? ' replaced' : ''}${before ? '' : ' blank'}">${diffCell(beforeText, '文件里没有')}</span>
    <span class="diff-arrow">→</span>
    <span class="side after${tone === 'missing' ? ' blank' : ''}${filled ? ' filled' : ''}">${diffCell(afterText, '没有歌词')}</span>
  </div>${detail}`;
}

function songCardHtml(item) {
  const changed = DIFF_FIELDS.filter(([key]) => {
    const before = localValue(item, key);
    const after = itemValue(item, key);
    return before !== after;
  }).length + (lyricsState(item, true) !== lyricsState(item, false) ? 1 : 0);
  const cover = item.cover_url
    ? `<img src="/api/cover?url=${encodeURIComponent(item.cover_url)}" alt="" loading="lazy" data-song-cover>`
    : `<img src="/api/cover?path=${encodeURIComponent(item.source)}" alt="" loading="lazy" data-song-cover>`;
  return `<div class="song-card-wrap"><article class="song-card" data-tone="${changed ? 'changed' : 'same'}">
    <div class="song-card-head">
      <div class="song-cover">${cover}<span class="song-cover-fallback">♪</span></div>
      <div class="song-card-titles">
        <div class="song-name">${esc(itemValue(item, 'title') || fileName(item.source))}</div>
        <div class="song-sub">${esc([itemValue(item, 'artist'), itemValue(item, 'album'), itemValue(item, 'year')]
          .filter(Boolean).join(' · ')) || '—'}</div>
        <div class="song-badges">
          ${statusBadge(item.status)}
          <span class="badge">${esc(item.mode_label || modeLabel(item.mode))}</span>
          <span class="badge">${esc(sourceLabel(item.source_name))}</span>
          <span class="conf ${confidenceTone(Number(item.confidence) || 0)}" title="匹配置信度">${(Number(item.confidence) || 0).toFixed(2)}</span>
        </div>
      </div>
    </div>
    <div class="song-diff">
      <div class="song-diff-head"><span>字段</span><span>刮削前（文件原标签）</span><span></span><span>刮削后（写入库）</span></div>
      ${DIFF_FIELDS.map(([key, label]) => diffRow(item, key, label)).join('')}
      ${lyricsRow(item)}
    </div>
    <div class="song-files">
      <div><span class="field-label">源文件</span>
        <div class="path-name">${esc(fileName(item.source))}</div>
        <div class="path-dir mono">${esc(dirName(item.source))}</div></div>
      <div><span class="field-label">目标位置</span>
        <div class="path-name">${esc(fileName(item.target) || '—')}</div>
        <div class="path-dir mono">${esc(item.target ? dirName(item.target) : '—')}</div></div>
    </div>
    <p class="song-note" title="${esc(item.detail)}">${esc(detailBrief(item))}</p>
    ${changed ? '' : '<p class="small muted">这次没有改写已有标签，只是补空或原样保留。</p>'}
  </article></div>`;
}

function showItemDiff(item) {
  if (!item) return;
  openModal('刮削前后对比', songCardHtml(item));
  $('modal-body').querySelectorAll('[data-song-cover]').forEach((img) => {
    img.addEventListener('error', () => img.classList.add('hidden'), { once: true });
  });
}

async function createJob(kind, dryRun, forcedPaths, choices) {
  const body = {
    kind, dry_run: dryRun, paths: forcedPaths || [...state.selected],
    organize: state.options.organize,
    skip_low_confidence: state.options.skipLowConfidence,
  };
  if (choices && Object.keys(choices).length) body.choices = choices;
  try {
    const job = await api('/api/jobs', { method: 'POST', body });
    state.currentJobId = job.id;
    toast(dryRun ? '已开始生成整理计划' : '已开始刮削整理');
    go('jobs');
    startPolling(job.id, dryRun);
  } catch (error) { toast(error.message, true); }
}

function startPolling(jobId, isPreview) {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    let payload;
    try { payload = await api(`/api/jobs/${jobId}`); }
    catch (_) { clearInterval(state.pollTimer); return; }
    const job = payload.job;
    if (job.status === 'running' || job.status === 'pending') {
      if (state.page === 'jobs') await renderJobs();
      return;
    }
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    if (job.status === 'completed' && isPreview) {
      state.previewJob = { id: job.id, dry_run: true, created_at: job.created_at };
      toast('整理计划已生成');
      go('plan');
    } else {
      toast(job.status === 'failed' ? `任务失败：${job.message}` : `任务${job.status === 'cancelled' ? '已取消' : '完成'}：${job.message}`, job.status === 'failed');
      if (state.page === 'jobs') await renderJobs();
      try { await loadSettings(); } catch (_) { /* ignore */ }
    }
  }, 1000);
}

function pathField(id, label, value, title) {
  return `<div class="field">
    <span class="field-label">${esc(label)}</span>
    <div class="path-picker">
      <input id="${id}" type="text" readonly value="${esc(value || '')}" spellcheck="false">
      <button type="button" class="ghost-btn" data-pick-dir="${id}" data-pick-title="${esc(title)}">浏览…</button>
    </div>
  </div>`;
}

/* ---------------- 设置页：转移方式可用性（从概览搬来） ---------------- */
function sameDeviceHint(same) {
  if (same === true) return '源与库在同一挂载点 · 可用硬链接';
  if (same === false) return '源与库跨挂载点 · 硬链接不可用';
  return '等待目录就绪';
}
function capabilityPanelHtml() {
  const store = state.capabilities;
  const head = `<div class="panel-head"><div><h2>转移方式可用性</h2>
      <p>硬链接要求源目录与音乐库位于同一文件系统 / 同一挂载点</p></div>
    <div class="btn-row"><button class="ghost-btn small" id="cap-probe">运行写入自检</button></div></div>`;
  const foot = `<p class="small muted" style="margin:14px 0 0">自检会在两个目录各创建一个临时文件并立即删除，用于确认写权限以及硬链接 / 符号链接是否被文件系统支持。</p>`;
  if (store.error) {
    return `${head}${emptyBlock('i-files', '目录状态读取失败，暂时无法判断可用性')}
      <p class="small muted cap-error">${esc(store.error)}</p>${foot}`;
  }
  const data = store.data;
  if (!data) {
    return `${head}${emptyBlock('i-files', store.loading ? '正在读取目录状态…' : '正在等待目录状态')}${foot}`;
  }
  const paths = [
    ['源目录', data.source_dir, data.source_exists],
    ['音乐库目录', data.library_dir, data.library_exists],
  ].map(([label, path, exists]) => `
    <div class="cap-path">
      <div class="cap-path-head">
        <span class="field-label">${esc(label)}</span>
        <span class="badge ${exists ? 'ok' : 'bad'}">${exists ? '目录可访问' : '目录不存在'}</span>
      </div>
      <span class="cap-path-value mono" title="${esc(path)}">${esc(path || '—')}</span>
    </div>`).join('');
  const tiles = (data.modes || []).map((item) => `
    <div class="mode-tile">
      <div class="mode-tile-head">
        <b>${esc(item.label || modeLabel(item.mode))}</b>
        <span>${capabilityBadge(item)}${item.probed ? ' <span class="tiny">已自检</span>' : ''}</span>
      </div>
      <p>${esc(item.reason || '—')}</p>
    </div>`).join('');
  return `${head}
    <div class="cap-paths">${paths}</div>
    <p class="cap-same ${data.same_device === false ? 'bad' : data.same_device === true ? 'ok' : ''}">
      ${esc(sameDeviceHint(data.same_device))}</p>
    <div class="mode-grid">${tiles || emptyBlock('i-files', '后端没有返回转移方式')}</div>
    ${foot}`;
}
/** 读目录状态：失败降级为空态，不影响设置页其它内容与「保存设置」。 */
async function loadCapabilities() {
  const store = state.capabilities;
  store.error = '';
  try {
    store.data = await api('/api/system/capabilities');
  } catch (error) {
    store.data = null;
    store.error = error.message;
  } finally {
    store.loading = false;
    store.loaded = true;
  }
}
/* 只重画这一块面板：不整页 reload，旁边没保存的输入框不会被冲掉。 */
function paintCapabilityPanel() {
  const panel = $('cap-panel');
  if (!panel) return;
  panel.innerHTML = capabilityPanelHtml();
  bindCapabilityProbe();
}
/** 读目录状态；失败只落到本面板的空态（含首次进入设置页）。 */
async function refreshCapabilityPanel() {
  if (!$('cap-panel') || state.capabilities.loading) return;
  state.capabilities.loading = true;
  paintCapabilityPanel();
  await loadCapabilities();
  paintCapabilityPanel();
}
function bindCapabilityProbe() {
  const button = $('cap-probe');
  if (!button) return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const result = await api('/api/system/capabilities', { method: 'POST', body: { probe: true } });
      state.capabilities.data = { ...(state.capabilities.data || {}), modes: result.modes || [] };
      state.capabilities.error = '';
      toast('写入自检完成');
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.capabilities.loading = false;
      state.capabilities.loaded = true;
      paintCapabilityPanel();
    }
  });
}

/** 保存条：两页都有，保存逻辑共用 saveSettings()。整行一条、压到 72 以内。 */
function saveCard(hint) {
  return `
    <div class="panel save-bar">
      <div class="save-bar-text"><h3>保存设置</h3>
        <p class="small muted">${esc(hint)}</p></div>
      <button class="primary-btn" id="save-settings">保存设置</button>
    </div>`;
}
/** 目录字段的「浏览…」：两页只有系统设置用到，集中在这里绑，避免重复。 */
function bindPathPickers() {
  document.querySelectorAll('[data-pick-dir]').forEach((button) => {
    button.addEventListener('click', () => openPicker(button.dataset.pickDir, button.dataset.pickTitle));
  });
}

/* ---------------- 系统设置：目录、转移方式与运行环境自检 ---------------- */
function renderSystem() {
  const s = state.settings;
  const chosen = state.modeChoice || s.transfer_mode;
  const modeCards = MODE_ORDER.map((mode) => {
    const info = state.modes.find((item) => item.mode === mode) || {};
    return `<div class="mode-card${chosen === mode ? ' selected' : ''}" data-mode="${mode}">
      <h3>${esc(info.label || modeLabel(mode))}</h3>
      <p>${esc(info.short || '')}</p><div class="mode-tag">${chosen === mode ? '✓ 当前使用' : '点击选用'}</div></div>`;
  }).join('');
  const accountCard = state.auth.is_admin ? `
    <div class="panel">
      <div class="panel-head"><div><h2>账户</h2>
        <p>当前登录：${esc(state.auth.username)}（管理员）。可以在这里添加新的使用账号。</p></div></div>
      <div class="grid">
        <label class="field"><span class="field-label">新用户名</span>
          <input id="new-user" type="text" autocomplete="off" placeholder="3–32 位字母、数字、_ . -"></label>
        <label class="field"><span class="field-label">新密码</span>
          <input id="new-pass" type="password" autocomplete="new-password" placeholder="至少 8 位"></label>
      </div>
      <button class="ghost-btn" id="create-user">添加账号</button>
    </div>` : '';
  $('page').innerHTML = `
    <div class="page-stack">
      <div class="panel panel-compact">
        <div class="panel-head compact"><div><h2>目录</h2>
          <p>直接点「浏览…」从服务器挂载目录中选择，不需要手动输入路径</p></div></div>
        <div class="grid grid-2 fields-row">
          ${pathField('f-source', '源目录（待刮削的下载文件夹）', s.source_dir, '选择源目录')}
          ${pathField('f-library', '音乐库目录（整理后的目标）', s.library_dir, '选择音乐库目录')}
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><div><h2>转移方式</h2>
          <p>决定刮削完成后文件如何进入音乐库；四种方式的完整区别见「转移方式说明」</p></div></div>
        <div class="mode-cards">${modeCards}</div>
      </div>
      <div class="panel" id="cap-panel">
        ${capabilityPanelHtml()}
      </div>
      ${accountCard}
      ${saveCard('目录与转移方式保存在数据目录的 SQLite 中，重启容器后依然生效；环境变量作为默认值。')}
    </div>`;
  bindPathPickers();
  bindSaveButton();
  document.querySelectorAll('[data-mode]').forEach((card) => card.addEventListener('click', () => {
    // 只记在内存里，点「保存设置」才真正落库：改了不保存就切走，不会污染另一页的保存。
    // 就地换选中态而不整页重画，旁边没保存的目录输入不会被冲掉。
    state.modeChoice = card.dataset.mode;
    document.querySelectorAll('[data-mode]').forEach((item) => {
      const on = item.dataset.mode === state.modeChoice;
      item.classList.toggle('selected', on);
      const tag = item.querySelector('.mode-tag');
      if (tag) tag.textContent = on ? '✓ 当前使用' : '点击选用';
    });
  }));
  // 转移方式可用性：系统设置页自己拉 /api/system/capabilities
  if (state.capabilities.loaded) paintCapabilityPanel(); else refreshCapabilityPanel();
  const createUser = $('create-user');
  if (createUser) createUser.addEventListener('click', async () => {
    try {
      await api('/api/auth/register', {
        method: 'POST',
        body: { username: $('new-user').value.trim(), password: $('new-pass').value },
      });
      $('new-user').value = '';
      $('new-pass').value = '';
      toast('账号已创建');
    } catch (error) { toast(error.message, true); }
  });
}

/* ---------------- 刮削设置：命名、元数据来源与自动刮削 ---------------- */
function renderSettings() {
  const meta = state.settingsMeta;
  const s = state.settings;
  const presets = (meta.templates || []).map((preset) => (
    `<option value="${esc(preset.template)}">${esc(preset.name)}</option>`
  )).join('');
  const sourceChecks = SOURCE_OPTIONS.map(([name, label]) => `
    <label class="check-line"><input type="checkbox" data-source="${name}" ${(s.metadata_sources || []).includes(name) ? 'checked' : ''}>
      ${esc(label)}</label>`).join('');
  const watch = state.watch || {};
  /* 版面：整行「命名与匹配」→ 2×2 等宽等高网格（标签与元数据 / 外观 / 自动刮削 / 说明）
   * → 整行紧凑保存条。每格吃同一份 grid 轨道，同行不会被内容拉出高度差。 */
  $('page').innerHTML = `
    <div class="settings-board">
      <div class="panel">
        <div class="panel-head"><div><h2>命名与匹配</h2>
          <p>可用变量：${(meta.template_fields || []).map((f) => `<code>{${esc(f)}}</code> <span class="small muted">${esc(TEMPLATE_FIELD_LABELS[f] || '')}</span>`).join(' · ')}</p>
          <p>默认按「专辑名 / 专辑歌手 / 歌曲文件」建目录，音乐服务器与刮削工具都能直接识别</p></div></div>
        <div class="grid grid-2 fields">
          <label class="field"><span class="field-label">命名模板预设</span>
            <select id="preset">${presets}</select></label>
          <label class="field"><span class="field-label">命名模板</span>
            <input id="f-template" value="${esc(s.naming_template || '')}"></label>
          <label class="field"><span class="field-label">目标冲突策略</span><select id="f-conflict">
            <option value="rename" ${s.conflict_policy === 'rename' ? 'selected' : ''}>自动改名（保留两者）</option>
            <option value="skip" ${s.conflict_policy === 'skip' ? 'selected' : ''}>跳过（保留已有文件）</option>
            <option value="overwrite" ${s.conflict_policy === 'overwrite' ? 'selected' : ''}>覆盖已有文件</option>
          </select></label>
          <label class="field"><span class="field-label">缺失字段占位符</span>
            <input id="f-unknown" value="${esc(s.unknown_placeholder || '未知')}" placeholder="未知"></label>
          <label class="field"><span class="field-label">在线匹配置信度阈值（0–1）</span>
            <input id="f-threshold" type="number" min="0" max="1" step="0.05" value="${esc(s.confidence_threshold ?? 0.62)}"></label>
          <label class="field"><span class="field-label">模板示例（只读，随上面的模板实时变化）</span>
            <input id="template-example" class="mono" type="text" readonly value="${esc(meta.example_target || '')}"></label>
        </div>
      </div>
      <div class="settings-grid">
        <div class="panel settings-cell">
          <h2>标签写入</h2>
          <label class="check-line"><input type="checkbox" id="f-write-tags" ${s.write_tags ? 'checked' : ''}>写入音频标签</label>
          <label class="check-line"><input type="checkbox" id="f-cover" ${s.embed_cover ? 'checked' : ''}>下载并内嵌封面</label>
          <label class="check-line"><input type="checkbox" id="f-lyrics" ${s.write_lyrics ? 'checked' : ''}>写入歌词（网易云 / QQ音乐 / 酷狗 / 酷我）</label>
          <label class="check-line"><input type="checkbox" id="f-relative" ${s.symlink_relative ? 'checked' : ''}>软链接使用相对路径（推荐）</label>
        </div>
        <div class="panel settings-cell">
          <h2>外观</h2>
          <p class="muted small">「跟随系统」按操作系统的明暗偏好自动切换，其余两档为固定主题。</p>
          <div class="btn-row">
            <button class="ghost-btn" data-theme-choice="dark">夜间</button>
            <button class="ghost-btn" data-theme-choice="light">日间</button>
            <button class="ghost-btn" data-theme-choice="auto">跟随系统</button>
          </div>
          <p class="muted small cell-note">切换只换配色，不改排版；字号与字重随屏幕宽度自适应，窄屏自动按 4 → 2 → 1 收列。</p>
        </div>
        <div class="panel settings-cell">
          <h2>在线元数据源</h2>
          ${sourceChecks}
        </div>
        <div class="panel settings-cell">
          <div class="panel-head"><div><h2>自动刮削</h2>
            <p>盯住源目录，出现新文件就自动排一个刮削任务</p></div>
            <span class="badge ${watch.enabled ? 'ok' : 'warn'}">${watch.enabled ? '运行中' : '已关闭'}</span></div>
          <label class="check-line"><input type="checkbox" id="f-watch" ${watch.enabled ? 'checked' : ''}>启用自动刮削（监控源目录）</label>
          <div class="grid grid-2">
            <label class="field"><span class="field-label">检查间隔（秒）</span>
              <input id="f-watch-interval" type="number" min="5" max="3600" step="5" value="${esc(watch.interval ?? 30)}"></label>
            <label class="field"><span class="field-label">稳定等待（秒）</span>
              <input id="f-watch-settle" type="number" min="0" max="3600" step="5" value="${esc(watch.settle ?? 10)}"></label>
          </div>
          <p class="small muted">只处理库里还没有的音频：开启那刻已存在的存量文件只登记不刮削，库里已有的同名 / 同大小文件自动过滤；稳定等待用于避开还在下载的半成品，<code>.part</code> / <code>.!qB</code> 不会触发，记录存数据库、重启不重刮。</p>
          <div class="btn-row cell-note">
            <button class="ghost-btn" id="watch-reset">把现有文件标记为已处理</button>
            <span class="small muted">${watch.enabled
              ? `已记录 ${esc(watch.tracked || 0)} 个源文件${watch.last_scan ? ` · 上次检查 ${esc(fmtTime(watch.last_scan))}` : ''}`
              : '开启后这里会显示检查状态'}</span>
          </div>
          ${watch.last_error ? `<p class="small" style="color:var(--danger);margin-top:8px">${esc(watch.last_error)}</p>` : ''}
        </div>
      </div>
      ${saveCard('刮削参数保存在数据目录的 SQLite 中，重启容器后依然生效；改完点右侧按钮保存。')}
    </div>`;
  applyTheme(themePreference());
  document.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.addEventListener('click', () => applyTheme(button.dataset.themeChoice));
  });
  $('preset').addEventListener('change', (event) => {
    $('f-template').value = event.target.value;
    updateExample();
  });
  $('f-template').addEventListener('input', updateExample);
  bindSaveButton();
  $('f-watch').addEventListener('change', saveWatch);
  $('watch-reset').addEventListener('click', async () => {
    try { state.watch = await api('/api/watch/reset', { method: 'POST' }); toast('已把现有文件标记为已处理'); renderPage(); }
    catch (error) { toast(error.message, true); }
  });
}

function bindSaveButton() {
  const button = $('save-settings');
  if (button) button.addEventListener('click', saveSettings);
}

function updateExample() {
  const template = $('f-template').value;
  const example = template
    .replace(/\{artist\}/g, 'Queen').replace(/\{album\}/g, 'A Night at the Opera')
    .replace(/\{album_artist\}/g, 'Queen').replace(/\{title\}/g, 'Bohemian Rhapsody')
    .replace(/\{track(?::[^}]*)?\}/g, '11').replace(/\{disc(?::[^}]*)?\}/g, '1')
    .replace(/\{year\}/g, '1975').replace(/\{filename\}/g, '11 Bohemian Rhapsody')
    .replace(/\{ext\}/g, 'flac');
  $('template-example').value = `${example}.flac`;
}

/** 保存设置：只收集当前页真实存在的控件，另一页没有的字段沿用 state.settings 的原值，
 * 整体 PUT /api/settings（不新增接口），保存成功后重画当前页。 */
async function saveSettings() {
  const values = { ...(state.settings || {}) };
  const withNode = (id, apply) => { const node = $(id); if (node) apply(node); };
  withNode('f-template', (node) => { values.naming_template = node.value.trim(); });
  withNode('f-conflict', (node) => { values.conflict_policy = node.value; });
  withNode('f-unknown', (node) => { values.unknown_placeholder = node.value.trim() || '未知'; });
  withNode('f-threshold', (node) => {
    const number = Number(node.value);
    if (Number.isFinite(number)) values.confidence_threshold = number;
  });
  withNode('f-source', (node) => { values.source_dir = node.value.trim(); });
  withNode('f-library', (node) => { values.library_dir = node.value.trim(); });
  withNode('f-write-tags', (node) => { values.write_tags = node.checked; });
  withNode('f-cover', (node) => { values.embed_cover = node.checked; });
  withNode('f-lyrics', (node) => { values.write_lyrics = node.checked; });
  withNode('f-relative', (node) => { values.symlink_relative = node.checked; });
  const boxes = [...document.querySelectorAll('[data-source]')];
  if (boxes.length) values.metadata_sources = boxes.filter((box) => box.checked).map((box) => box.dataset.source);
  // 转移方式的选中状态只在系统设置页有卡片，取内存里的待保存值
  if (state.modeChoice) values.transfer_mode = state.modeChoice;
  try {
    const result = await api('/api/settings', { method: 'PUT', body: { values } });
    state.settings = result.settings || state.settings;
    state.settingsMeta = result;
    state.modeChoice = null;
    toast('设置已保存');
    renderPage();
  } catch (error) { toast(error.message, true); }
}

async function saveWatch() {
  const body = {
    enabled: $('f-watch').checked,
    interval: Number($('f-watch-interval').value) || 30,
    settle: $('f-watch-settle').value === '' ? 0 : Number($('f-watch-settle').value),
  };
  try {
    state.watch = await api('/api/watch', { method: 'PUT', body });
    toast(body.enabled ? '自动刮削已开启' : '自动刮削已关闭');
    await loadSettings();
    renderPage();
  } catch (error) { toast(error.message, true); }
}

async function renderDocs() {
  let data;
  try { data = await api('/api/transfer-modes'); }
  catch (error) { toast(error.message, true); return; }
  /* 四种方式 2×2 等宽等高：每格约半行宽，正文正常换行，不再挤成 190px 窄条。
   * 面板头与「容器挂载要点」都是整行，行列数只有 1 和 2，天然对称。 */
  const blocks = (data.modes || []).map((mode) => `
    <div class="doc-block">
      <h3>${esc(mode.label)}</h3>
      <div class="doc-en">${esc(mode.short)}</div>
      <p><b>实现：</b>${esc(mode.how)}</p>
      <div class="grid grid-2" style="gap:12px">
        <div><b class="small">优点</b><ul>${(mode.pros || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>
        <div><b class="small">限制与风险</b><ul>${(mode.cons || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>
      </div>
      <div class="doc-grid">
        <div><b>空间占用：</b>${esc(mode.space)}</div>
        <div><b>源文件之后：</b>${esc(mode.source_after)}</div>
        <div><b>跨文件系统：</b>${mode.cross_filesystem ? '支持' : '不支持'}</div>
        <div><b>删掉源文件后目标：</b>${mode.survives_source_delete ? '仍可读取' : '失效（悬空链接）'}</div>
        <div style="grid-column:1/-1"><b>标签写入影响：</b>${esc(mode.tag_effect)}</div>
        <div style="grid-column:1/-1"><b>适用：</b>${esc(mode.use_when)}</div>
        <div style="grid-column:1/-1"><b>失败条件：</b>${esc(mode.fails_when)}</div>
      </div>
    </div>`).join('');
  $('page').innerHTML = `
    <div class="page-stack">
      <div class="banner info">${esc(data.ordering_note)}</div>
      <div class="panel panel-compact">
        <div class="panel-head"><div><h2>容器挂载要点</h2>
          <p>硬链接要求源目录与音乐库在同一个挂载点内；是否满足由「系统设置」的实时自检告诉你。</p></div>
          <div class="btn-row"><button class="ghost-btn small" data-go="system">去系统设置自检</button></div></div>
        <p class="muted small">把源目录与音乐库作为同一个宿主机父目录下的两个子目录一次性挂载进容器（例如 <code class="mono">/vol1/1000:/media</code>），两者设备号相同才能建硬链接。软链接默认写相对路径，容器内外都能解析。</p>
      </div>
      <div class="doc-grid-2">${blocks}</div>
    </div>`;
  document.querySelectorAll('#page [data-go]').forEach((button) => {
    button.addEventListener('click', () => go(button.dataset.go));
  });
}

/* ---------------- boot ---------------- */
async function loadSettings() {
  const result = await api('/api/settings');
  state.settings = result.settings || {};
  state.settingsMeta = result;
  state.modeChoice = null;   // 后端值已刷新，丢弃页面上没保存的选择
}
async function loadWatch() {
  try { state.watch = await api('/api/watch'); } catch (_) { state.watch = null; }
}
async function loadModes() {
  const result = await api('/api/transfer-modes');
  state.modes = result.modes || [];
  state.orderingNote = result.ordering_note || '';
}
async function boot() {
  applyTheme(themePreference());
  applyNavCollapsed(navCollapsed());
  try {
    const status = await refreshAuth();
    $('auth-tabs').classList.add('hidden');
    if (!status.authenticated) {
      showAuth(status.needs_setup ? 'register' : 'login');
      return;
    }
    await enterApp();
  } catch (error) {
    $('auth-error').textContent = `初始化失败：${error.message}`;
    showAuth(state.auth.needs_setup ? 'register' : 'login');
  }
}

/* ---------------- global events ---------------- */
$('theme-toggle').addEventListener('click', toggleTheme);
$('nav-toggle').addEventListener('click', toggleNav);
$('nav-open').addEventListener('click', toggleNav);
window.matchMedia('(max-width: 900px)').addEventListener('change', () => {
  $('app-shell').classList.remove('nav-open');
  applyNavCollapsed(navCollapsed());
});
// 窄屏抽屉：点主区域或按 Esc 收起，避免遮住内容又找不到关闭入口
$('page').addEventListener('click', () => {
  if (window.matchMedia('(max-width: 900px)').matches) $('app-shell').classList.remove('nav-open');
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!$('app-shell').classList.contains('nav-open')) return;
  $('app-shell').classList.remove('nav-open');
});
$('modal-close').addEventListener('click', closeModal);
$('modal').addEventListener('click', (event) => { if (event.target === $('modal')) closeModal(); });
$('logout-btn').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* ignore */ }
  state.auth.authenticated = false;
  state.auth.needs_setup = false;
  showAuth('login');
  toast('已退出登录');
});
$('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const registering = state.authMode === 'register';
  const username = $('auth-user').value.trim();
  const password = $('auth-pass').value;
  if (registering && password !== $('auth-confirm').value) {
    $('auth-error').textContent = '两次输入的密码不一致';
    return;
  }
  const button = $('auth-submit');
  button.disabled = true;
  try {
    await api(registering ? '/api/auth/register' : '/api/auth/login', {
      method: 'POST', body: { username, password },
    });
    $('auth-pass').value = '';
    $('auth-confirm').value = '';
    $('auth-error').textContent = '';
    const status = await refreshAuth();
    state.auth.needs_setup = status.needs_setup;
    if (status.authenticated) await enterApp();
    else showAuth('login');
  } catch (error) {
    $('auth-error').textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
document.querySelectorAll('[data-auth-tab]').forEach((button) => {
  button.addEventListener('click', () => setAuthMode(button.dataset.authTab));
});
$('picker-close').addEventListener('click', closePicker);
$('picker-cancel').addEventListener('click', closePicker);
$('picker').addEventListener('click', (event) => { if (event.target === $('picker')) closePicker(); });
$('picker-up').addEventListener('click', () => {
  if (state.picker.parent) loadDir(state.picker.parent);
});
$('picker-home').addEventListener('click', () => loadDir(state.picker.root || ''));
$('picker-confirm').addEventListener('click', () => {
  const value = $('picker-value').value.trim();
  if (!value) { toast('请先选择一个文件夹', true); return; }
  const target = $(state.picker.target);
  if (target) {
    target.value = value;
    target.dispatchEvent(new Event('change', { bubbles: true }));
  }
  closePicker();
  toast('已选择文件夹，记得保存设置');
});
bindMediaEditor();
$('drawer-close').addEventListener('click', closeDrawer);
$('drawer').addEventListener('click', (event) => {
  if (event.target === $('drawer')) closeDrawer();
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  // 搜词面板盖在歌词 Tab 上时，ESC 先收它，再按一次才关编辑器
  if (mediaEditorOpen()) {
    if (!closeLyricsSourcePanel()) closeMediaEditor();
    return;
  }
  if (!$('drawer').classList.contains('hidden')) closeDrawer();
  else if (!$('picker').classList.contains('hidden')) closePicker();
  else if (!$('modal').classList.contains('hidden')) closeModal();
});
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
  if (themePreference() === 'auto') applyTheme('auto');
});
// 离开页面 / 切到后台：把在飞的请求主动收掉，别留给浏览器判成连接被重置
window.addEventListener('pagehide', abortAllRequests);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') abortAllRequests();
});
// 兜底：任何没被接住的拒绝都不该变成控制台里的红色报错
window.addEventListener('unhandledrejection', (event) => {
  if (!isAbortError(event.reason)) return;
  event.preventDefault();
});
boot();
