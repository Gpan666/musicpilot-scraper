/* MusicPilot Scraper — Miuix-style single page UI, no build step.
 * Auth: first-run registration then session login.
 * Folder paths are chosen with a server-side directory picker.
 */
'use strict';

/** 引用 index.html 里的 SVG sprite；图标统一走矢量，避免字符图标在缺字字体下变成方块或宽度抖动。 */
function spriteIcon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#${name}"></use></svg>`;
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
  { id: 'docs', label: '转移方式说明', icon: 'i-docs' },
];
const PAGE_META = {
  dashboard: ['概览', '目录状态、四种转移方式可用性与最近任务'],
  library: ['音乐库', '浏览库内所有曲目、手动刮削、自动扫描查漏补缺（只改标签，不动文件）'],
  files: ['源文件', '扫描源目录、选择文件并生成整理计划'],
  plan: ['整理计划', '先预览源 → 目标的映射，确认后再执行'],
  jobs: ['任务记录', '查看进度与明细、取消任务、导出表格（CSV）报告'],
  settings: ['刮削设置', '目录、转移方式、命名模板与账户'],
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
  orderingNote: '',
  files: [],
  selected: new Set(),
  jobs: [],
  previewJob: null,
  watch: null,
  currentJobId: null,
  pollTimer: null,
  capabilities: [],
  options: { organize: true, skipLowConfidence: false },
  picker: { target: null, path: '', root: '', parent: null, entries: [], loading: false },
  plan: { jobId: null, items: [], choices: new Map(), filter: 'all' },
  library: {
    loaded: false, tracks: [], stats: {}, sources: [], libraryDir: '', warnings: [],
    loading: false, filter: 'all', query: '', open: null,
    scrape: { path: null, source: '', results: [], loading: false, query: {} },
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

async function api(path, options = {}) {
  const init = { credentials: 'same-origin', ...options };
  if (init.body && typeof init.body !== 'string') {
    init.headers = { ...(init.headers || {}), 'Content-Type': 'application/json' };
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
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
  return type.includes('application/json') ? response.json() : response.text();
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
  hideAuth();
  await Promise.all([loadSettings(), loadModes(), loadWatch()]);
  try {
    const about = await api('/api/about');
    $('version').textContent = `v${about.version}`;
  } catch (_) { /* about is optional */ }
  go('dashboard');
}

/* ---------------- shell ---------------- */
function renderNav() {
  $('nav').innerHTML = NAV.map((item) => (
    item.group
      ? `<div class="nav-group">${esc(item.group)}</div>`
      : `<button class="nav-item${state.page === item.id ? ' active' : ''}" data-page="${item.id}">
           <span class="nav-icon">${spriteIcon(item.icon)}</span><span>${esc(item.label)}</span></button>`
  )).join('');
  document.querySelectorAll('[data-page]').forEach((button) => {
    button.addEventListener('click', () => go(button.dataset.page));
  });
}
function go(page) {
  state.page = page;
  renderNav();
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
function closeModal() { $('modal').classList.add('hidden'); }

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
    jobs: renderJobs, settings: renderSettings, docs: renderDocs,
  };
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

async function renderDashboard() {
  let data;
  try { data = await api('/api/dashboard'); }
  catch (error) { toast(error.message, true); return; }
  state.capabilities = data.capabilities;
  const same = data.same_device;
  const watch = state.watch || {};
  const cards = [
    ['源目录', data.source_dir, data.source_exists ? 'ok' : 'bad', data.source_exists ? '目录可访问' : '目录不存在，请检查挂载'],
    ['音乐库目录', data.library_dir, data.library_exists ? 'ok' : 'bad', data.library_exists ? '目录可访问' : '首次执行时自动创建'],
    ['转移方式', modeLabel(state.settings.transfer_mode), 'warn', same === null ? '等待目录就绪' : (same ? '源与库在同一挂载点 · 可用硬链接' : '源与库跨挂载点 · 硬链接不可用')],
    ['当前账号', state.auth.username || '—', 'ok', state.auth.is_admin ? '管理员 · 会话保护已开启' : '普通账号 · 会话保护已开启'],
    ['自动刮削', watch.enabled ? '已开启' : '已关闭', watch.enabled ? 'ok' : 'warn',
      watch.enabled
        ? `每 ${Math.round(watch.interval || 30)} 秒检查源目录${watch.tracked ? ` · 已记录 ${watch.tracked} 个文件` : ''}${watch.in_library ? ` · 库内 ${watch.in_library} 个已过滤` : ''}`
        : '开启后只刮库里还没有的新文件'],
  ];
  const modeRows = data.capabilities.map((item) => `
    <tr>
      <td><b>${esc(item.label)}</b></td>
      <td>${capabilityBadge(item)}${item.probed ? ' <span class="tiny">已自检</span>' : ''}</td>
      <td class="muted">${esc(item.reason)}</td>
    </tr>`).join('');
  const jobs = (data.jobs || []).map((job) => `
    <tr><td class="truncate" title="${esc(jobNote(job))}">${esc(jobLabel(job))}</td>
      <td>${job.dry_run ? '预览' : jobKind(job)}</td>
      <td>${statusBadge(job.status)}</td>
      <td class="muted">${esc(job.processed || 0)} 项</td>
      <td class="muted small">${esc(fmtTime(job.created_at))}</td></tr>`).join('');
  const recent = (data.recent_done || []).map((item) => `
    <tr><td class="truncate" title="${esc(item.source)}">${esc(fileName(item.source))}</td>
      <td>${esc(item.mode_label || modeLabel(item.mode))}</td>
      <td class="muted small">${esc(fmtTime(item.created_at))}</td></tr>`).join('');

  $('page').innerHTML = `
    <div class="grid cols-4">
      ${cards.map(([label, value, tone, note]) => `
        <div class="panel stat"><div class="stat-label">${esc(label)}</div>
          <div class="stat-value ${tone}">${esc(value)}</div>
          <div class="stat-note">${esc(note)}</div></div>`).join('')}
    </div>
    <div class="panel">
      <div class="panel-head">
        <div><h2>四种转移方式可用性</h2><p>硬链接要求源目录与音乐库位于同一文件系统 / 同一挂载点</p></div>
        <div class="btn-row">
          <button class="ghost-btn" id="probe-btn">运行写入自检</button>
          <button class="ghost-btn" data-go="settings">更改目录</button>
        </div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>方式</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>${modeRows}</tbody></table></div>
      <p class="small muted" style="margin:14px 0 0">自检会在两个目录各创建一个临时文件并立即删除，用于确认写权限以及硬链接 / 符号链接是否被文件系统支持。</p>
    </div>
    <div class="grid cols-2">
      <div class="panel"><div class="panel-head"><h2>最近任务</h2>
        <button class="ghost-btn" data-go="jobs">查看全部</button></div>
        <div class="table-wrap"><table class="jobs-table"><thead><tr><th>文件</th><th>类型</th><th>状态</th><th>数量</th><th>时间</th></tr></thead>
          <tbody>${jobs || '<tr><td colspan="5" class="empty">暂无任务</td></tr>'}</tbody></table></div></div>
      <div class="panel"><div class="panel-head"><h2>最近整理</h2></div>
        <div class="table-wrap"><table><thead><tr><th>文件</th><th>方式</th><th>时间</th></tr></thead>
          <tbody>${recent || '<tr><td colspan="3" class="empty">暂无整理记录</td></tr>'}</tbody></table></div></div>
    </div>`;
  $('probe-btn').addEventListener('click', async () => {
    try {
      const result = await api('/api/system/capabilities', { method: 'POST', body: { probe: true } });
      state.capabilities = result.modes;
      await renderDashboard();
      toast('写入自检完成');
    } catch (error) { toast(error.message, true); }
  });
  document.querySelectorAll('[data-go]').forEach((button) => {
    button.addEventListener('click', () => go(button.dataset.go));
  });
}

/* ---------------- 音乐库：横向标签 / 卡片详情 / 手动刮削 / 查漏补缺 ---------------- */
const LIBRARY_FILTERS = [
  ['all', '全部'], ['incomplete', '待补全'], ['complete', '已齐全'],
  ['lyrics', '有歌词'], ['no_lyrics', '无歌词'],
];
const FIELD_NAMES = {
  title: '标题', artist: '歌手', album: '专辑', album_artist: '专辑艺术家',
  year: '年份', track_number: '专辑轨号', disc_number: '碟号', lyrics: '歌词', cover: '封面',
};
const fieldName = (name) => FIELD_NAMES[name] || name;
/** 匹配度 1–10 的配色：7 分以上才算「像」。 */
const scoreTone = (score) => (score >= 8 ? 'ok' : score >= 7 ? 'warn' : 'bad');

function libraryTrackMatches(track) {
  const filter = state.library.filter;
  if (filter === 'incomplete') return !track.complete;
  if (filter === 'complete') return track.complete;
  if (filter === 'lyrics') return track.has_lyrics;
  if (filter === 'no_lyrics') return !track.has_lyrics;
  return true;
}
function libraryVisible() {
  const needle = state.library.query.trim().toLowerCase();
  return (state.library.tracks || []).filter((track) => {
    if (!libraryTrackMatches(track)) return false;
    if (!needle) return true;
    return [track.title, track.artist, track.album, track.album_artist, track.name, track.relative_dir]
      .filter(Boolean).some((value) => String(value).toLowerCase().includes(needle));
  });
}

/** 横向标签：一首歌一个标签，滚到哪算哪。 */
function libraryTag(track) {
  const open = state.library.open === track.path;
  const cover = track.cover_url
    ? `<img src="${esc(track.cover_url)}" alt="" loading="lazy" data-lib-cover>`
    : '<span class="lib-cover-fallback">♪</span>';
  const meta = (label, value, extra = '') => `<div class="lib-meta">
      <span class="lib-meta-key">${esc(label)}</span>
      <span class="lib-meta-value${value ? '' : ' blank'}" ${extra}>${esc(value || '—')}</span></div>`;
  const lyrics = track.has_lyrics
    ? `<div class="lib-lyrics" title="${esc(track.lyrics_preview)}">${esc(track.lyrics_preview || '（歌词为空）')}</div>`
    : '<div class="lib-lyrics blank">无歌词</div>';
  return `<article class="lib-tag${open ? ' open' : ''}${track.complete ? '' : ' gap'}" data-lib="${esc(track.path)}">
    <div class="lib-cover">${cover}</div>
    <div class="lib-body">
      <div class="lib-title-row">
        <span class="lib-title" title="${esc(track.path)}">${esc(track.title || track.name)}</span>
        ${track.complete ? '' : '<span class="lib-flag">待补全</span>'}
      </div>
      ${meta('歌手', track.artist)}
      ${meta('专辑', track.album)}
      ${meta('专辑轨号', track.track_number)}
      ${meta('专辑艺术家', track.album_artist)}
      ${lyrics}
      <div class="lib-foot">
        <span class="lib-dir mono" title="${esc(track.relative_dir)}">${esc(track.relative_dir || '（库根目录）')}</span>
        <span class="lib-expand">${open ? '收起' : '展开详情'}</span>
      </div>
    </div>
    ${open ? libraryDetail(track) : ''}
  </article>`;
}

/** 点击后展开的卡片详情：完整元数据 + 歌词全文 + 操作按钮。 */
function libraryDetail(track) {
  const row = (label, value) => `<div class="lib-detail-row">
      <span class="lib-meta-key">${esc(label)}</span>
      <span class="lib-detail-value${value ? '' : ' blank'}">${esc(value || '—')}</span></div>`;
  const missing = (track.missing || []).map(fieldName).join('、');
  return `<div class="lib-detail">
    <div class="lib-detail-grid">
      ${row('标题', track.title)}
      ${row('歌手', track.artist)}
      ${row('专辑', track.album)}
      ${row('专辑艺术家', track.album_artist)}
      ${row('专辑轨号', track.track_number)}
      ${row('碟号', track.disc_number)}
      ${row('年份', track.year)}
      ${row('时长', fmtDuration(track.duration))}
      ${row('大小', fmtSize(track.size))}
      ${row('内嵌封面', track.has_cover ? '有' : '无')}
      ${row('缺少字段', missing || '无')}
    </div>
    <div class="lib-path mono" title="${esc(track.path)}">${esc(track.path)}</div>
    <div class="lib-lyrics-full">
      <span class="field-label">歌词全文</span>
      <pre>${esc(track._lyrics || (track.has_lyrics ? '加载中…' : '这个文件里没有歌词'))}</pre>
    </div>
    <div class="btn-row">
      <button class="ghost-btn small" data-lib-scrape="${esc(track.path)}">手动刮削</button>
      <button class="ghost-btn small" data-lib-fill="${esc(track.path)}">只补这一首</button>
    </div>
  </div>`;
}

function renderLibraryTags() {
  const visible = libraryVisible();
  const box = $('lib-tags');
  if (!box) return;
  box.innerHTML = visible.map(libraryTag).join('')
    || '<div class="empty">当前筛选条件下没有曲目</div>';
  box.querySelectorAll('img[data-lib-cover]').forEach((img) => {
    img.addEventListener('error', () => img.classList.add('hidden'), { once: true });
  });
  box.querySelectorAll('[data-lib]').forEach((node) => {
    node.addEventListener('click', (event) => {
      if (event.target.closest('button')) return;
      toggleLibraryCard(node.dataset.lib);
    });
  });
  box.querySelectorAll('[data-lib-scrape]').forEach((button) => {
    button.addEventListener('click', () => openScrapeDialog(button.dataset.libScrape));
  });
  box.querySelectorAll('[data-lib-fill]').forEach((button) => {
    button.addEventListener('click', () => fillOne(button.dataset.libFill));
  });
}

async function toggleLibraryCard(path) {
  const wasOpen = state.library.open === path;
  state.library.open = wasOpen ? null : path;
  renderLibraryTags();
  if (wasOpen) return;
  const track = (state.library.tracks || []).find((row) => row.path === path);
  if (!track || track._lyrics !== undefined || !track.has_lyrics) return;
  try {
    const detail = await api(`/api/library/detail?path=${encodeURIComponent(path)}`);
    track._lyrics = detail.lyrics || '';
    if (state.library.open === path) renderLibraryTags();
  } catch (error) { toast(error.message, true); }
}

function libraryStatCard(label, value, note, tone = '') {
  return `<div class="panel stat"><div class="stat-label">${esc(label)}</div>
    <div class="stat-value ${tone}">${esc(String(value ?? 0))}</div>
    <div class="stat-note">${esc(note)}</div></div>`;
}

async function renderLibrary() {
  const store = state.library;
  if (!store.loaded || store.loading) {
    $('page').innerHTML = '<div class="panel"><div class="empty">正在扫描音乐库…</div></div>';
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
      $('page').innerHTML = `<div class="panel"><div class="empty">${esc(error.message)}</div></div>`;
      return;
    }
  }
  const stats = store.stats || {};
  const sourceChips = (store.sources || []).map((item) => `
    <span class="badge ${item.enabled ? 'info' : ''}">${esc(item.label)}${item.enabled ? '' : '（未启用）'}</span>`).join('');
  $('page').innerHTML = `
    <div class="banner info">批量补全只写标签：不移动、不复制、不建硬链接或软链接，文件位置一律不变。</div>
    <div class="grid cols-4">
      ${libraryStatCard('曲目', stats.tracks, '每次进入都重新扫盘枚举')}
      ${libraryStatCard('歌手', stats.artists, '按标签统计')}
      ${libraryStatCard('专辑', stats.albums, '按标签统计')}
      ${libraryStatCard('待补全', stats.incomplete, '缺标题/歌手/专辑等字段', stats.incomplete ? 'warn' : 'ok')}
    </div>
    <div class="panel">
      <div class="panel-head">
        <div><h2>音乐库里的全部曲目</h2>
          <p class="mono small">${esc(store.libraryDir)}${stats.tracks ? ` · 封面 ${stats.with_cover} 首 · 歌词 ${stats.with_lyrics} 首` : ''}</p></div>
        <div class="btn-row">
          <button class="ghost-btn" id="lib-reload">重新扫描</button>
          <button class="ghost-btn" id="lib-auto">自动扫描查漏补缺</button>
        </div>
      </div>
      <div class="lib-toolbar">
        ${LIBRARY_FILTERS.map(([id, label]) => `<button class="ghost-btn small${store.filter === id ? ' active' : ''}" data-lib-filter="${id}">${label}</button>`).join('')}
        <input id="lib-search" class="lib-search" type="search" placeholder="按歌名 / 歌手 / 专辑 / 目录过滤" value="${esc(store.query)}">
        <span class="small muted" id="lib-count"></span>
      </div>
      <p class="small muted" style="margin:0 0 10px">可用的刮削来源：${sourceChips || '—'}（在「刮削设置 → 标签与元数据」里勾选）</p>
      <div id="lib-tags" class="lib-tags"></div>
      <p class="small muted" style="margin:10px 0 0">点标签展开卡片详情；「手动刮削」可以选平台搜索并看到 1–10 的匹配度。</p>
    </div>`;

  renderLibraryTags();
  const count = $('lib-count');
  if (count) count.textContent = `显示 ${libraryVisible().length} / ${store.tracks.length} 首`;

  $('lib-reload').addEventListener('click', () => { store.loaded = false; store.open = null; renderLibrary(); });
  $('lib-auto').addEventListener('click', autoFillLibrary);
  document.querySelectorAll('[data-lib-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      store.filter = button.dataset.libFilter;
      renderLibrary();
    });
  });
  const search = $('lib-search');
  if (search) search.addEventListener('input', () => {
    store.query = search.value;
    renderLibraryTags();
    if (count) count.textContent = `显示 ${libraryVisible().length} / ${store.tracks.length} 首`;
  });
}

/* ---- 手动刮削：选平台 → 搜索 → 看匹配度 → 写入 ---- */
async function openScrapeDialog(path) {
  const track = (state.library.tracks || []).find((row) => row.path === path) || {};
  state.library.scrape = { path, source: '', results: [], loading: false, query: {} };
  const chips = (state.library.sources || []).map((item) => (
    `<button class="cand-chip${item.enabled ? '' : ' dim'}" data-scrape-source="${esc(item.id)}"
       ${item.enabled ? '' : 'disabled title="该平台未在设置中启用"'}>
       <b>${esc(item.label)}</b><span>${item.enabled ? '已启用' : '未启用'}</span></button>`
  )).join('');
  openModal('手动刮削', `
    <p class="small muted">文件：<span class="mono">${esc(path)}</span></p>
    <div class="field" style="margin-top:12px">
      <span class="field-label">来源平台（点一下选中，再搜索）</span>
      <div class="cand-row" id="scrape-sources">
        <button class="cand-chip active" data-scrape-source=""><b>全部已启用</b><span>逐平台并发搜</span></button>
        ${chips}
      </div>
    </div>
    <div class="grid cols-3" style="margin-top:12px">
      <label class="field"><span class="field-label">歌名</span>
        <input id="scrape-title" value="${esc(track.title || '')}"></label>
      <label class="field"><span class="field-label">歌手</span>
        <input id="scrape-artist" value="${esc(track.artist || '')}"></label>
      <label class="field"><span class="field-label">专辑</span>
        <input id="scrape-album" value="${esc(track.album || '')}"></label>
    </div>
    <div class="btn-row" style="margin-top:12px">
      <button class="primary-btn" id="scrape-search">搜索</button>
      <span class="small muted">匹配度 1–10：10 表示歌名、歌手、专辑全对得上；7 分以上才建议写入。</span>
    </div>
    <div id="scrape-results" class="scrape-results"></div>`);
  document.querySelectorAll('[data-scrape-source]').forEach((button) => {
    button.addEventListener('click', () => {
      state.library.scrape.source = button.dataset.scrapeSource;
      document.querySelectorAll('[data-scrape-source]').forEach((node) => {
        node.classList.toggle('active', node === button);
      });
    });
  });
  $('scrape-search').addEventListener('click', searchLibrary);
}

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
        ${fills ? `<span class="badge info">可补：${esc(fills)}</span>` : '<span class="badge">没有能补的字段</span>'}
      </div>
    </div>
    <div class="btn-row">
      <button class="ghost-btn small" data-scrape-apply="${index}" data-dry="1">预览</button>
      <button class="primary-btn small" data-scrape-apply="${index}" data-dry="0">写入标签</button>
    </div>
  </article>`;
}

async function searchLibrary() {
  const scrape = state.library.scrape;
  if (!scrape.path) return;
  const box = $('scrape-results');
  box.innerHTML = '<div class="empty">正在搜索…</div>';
  try {
    const payload = await api('/api/library/search', {
      method: 'POST',
      body: {
        path: scrape.path,
        source: scrape.source || '',
        title: $('scrape-title').value.trim(),
        artist: $('scrape-artist').value.trim(),
        album: $('scrape-album').value.trim(),
      },
    });
    scrape.results = payload.results || [];
    scrape.query = payload.query || {};
    box.innerHTML = scrape.results.length
      ? `<p class="small muted">在 ${(payload.searched || []).map(sourceLabel).map(esc).join(' / ') || '所选平台'} 搜到 ${scrape.results.length} 条结果</p>`
        + scrape.results.map(scrapeResultCard).join('')
      : `<div class="empty">${esc(payload.searched && payload.searched.length ? '没有搜到结果，换个平台或改改搜索词试试' : '没有可用的来源平台，请先在设置里启用')}</div>`;
    box.querySelectorAll('[data-scrape-apply]').forEach((button) => {
      button.addEventListener('click', () => {
        const item = scrape.results[Number(button.dataset.scrapeApply)];
        applyScrapeResult(item, button.dataset.dry === '1');
      });
    });
  } catch (error) {
    box.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}

async function applyScrapeResult(item, dryRun) {
  if (!item) return;
  try {
    const payload = await api('/api/library/apply', {
      method: 'POST',
      body: { path: state.library.scrape.path, metadata: item.metadata, dry_run: dryRun },
    });
    const labels = (payload.written_labels && payload.written_labels.length
      ? payload.written_labels
      : payload.would_write_labels || []).join('、');
    toast(payload.message || (labels ? `补全：${labels}` : '没有可写入的字段'), !dryRun && !labels);
    state.library.loaded = false;
    if (!dryRun) { closeModal(); await renderLibrary(); }
    else if (labels) toast(`预览：将补全 ${labels}（文件位置不变）`);
  } catch (error) { toast(error.message, true); }
}

/* ---- 自动扫描查漏补缺 ---- */
async function autoFillLibrary() {
  const sources = (state.library.sources || []).filter((item) => item.enabled);
  if (!sources.length) { toast('请先在「刮削设置」里启用至少一个在线音源', true); return; }
  const preview = await api('/api/library/scan', { method: 'POST' });
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
    state.library.loaded = false;
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
    state.library.loaded = false;
    await renderLibrary();
  } catch (error) { toast(error.message, true); }
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
      </tr></thead><tbody>${rows || '<tr><td colspan="8" class="empty">源目录中没有找到音频文件</td></tr>'}</tbody></table></div>
    </div>
    <div class="panel">
      <div class="panel-head"><div><h2>对选中的 <span id="selection-count">${state.selected.size}</span> 个文件执行</h2>
        <p>不选择任何文件时，默认处理源目录内全部音频文件</p></div></div>
      <div class="btn-row" style="margin-bottom:12px">
        <label class="check-line"><input type="checkbox" id="opt-organize" ${state.options.organize ? 'checked' : ''}>整理到音乐库（关闭则只写标签）</label>
        <label class="check-line"><input type="checkbox" id="opt-skip" ${state.options.skipLowConfidence ? 'checked' : ''}>跳过低置信度文件</label>
      </div>
      <div class="btn-row">
        <button class="ghost-btn" id="preview-btn">① 生成整理计划（预览）</button>
        <button class="primary-btn" id="scrape-btn">② 直接刮削整理</button>
        <span class="small muted">当前方式：<b>${esc(modeLabel(state.settings.transfer_mode))}</b> · 建议先预览</span>
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
    <div class="grid cols-2">
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
    || '<div class="empty">当前筛选条件下没有条目</div>';
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
    $('page').innerHTML = `<div class="panel"><div class="empty">
      还没有整理计划。请到「源文件」选择文件后点击「生成整理计划（预览）」。
      <div style="margin-top:14px"><button class="ghost-btn" data-go="files">前往源文件</button></div>
    </div></div>`;
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
    </div>
    <div class="panel"><h3>执行说明</h3>
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
    </tr></thead><tbody>${rows || '<tr><td colspan="6" class="empty">没有计划项</td></tr>'}</tbody></table></div>`;
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
  const watchBar = `
    <div class="panel"><div class="between">
      <div><h3>自动刮削 <span class="badge ${watch.enabled ? 'ok' : 'warn'}">${watch.enabled ? '开启' : '关闭'}</span></h3>
        <p class="small muted" style="margin-top:4px">${watch.enabled
          ? `每 ${Math.round(watch.interval || 30)} 秒检查一次${watch.last_scan ? ` · 上次 ${esc(fmtTime(watch.last_scan))}` : ''} · 只刮库里还没有的新文件，已整理的自动跳过`
          : '开启后，源目录里出现库里还没有的新文件会自动刮削，无需手动操作'}</p></div>
      <button class="ghost-btn" data-go="settings">${watch.enabled ? '调整' : '去开启'}</button>
    </div></div>`;
  $('page').innerHTML = `${watchBar}${progressCard}
    <div class="panel"><div class="panel-head"><div><h2>全部任务</h2><p>点「明细」右侧滑出详情；每次只允许一个任务运行，避免文件竞争</p></div>
      <div class="btn-row">
        <button class="ghost-btn" id="refresh-jobs">刷新</button>
        <button class="ghost-btn danger-text" id="clear-jobs" ${jobs.length ? '' : 'disabled'}>清空记录</button>
      </div></div>
      <div class="table-wrap"><table class="jobs-table"><thead><tr><th>文件 / 歌曲名</th><th>类型</th><th>状态</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="empty">暂无任务</td></tr>'}</tbody></table></div>
    </div>`;
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
  $('drawer-body').innerHTML = '<div class="empty">正在读取明细…</div>';
  $('drawer-stat').textContent = '';
  $('drawer-csv').href = `/api/jobs/${encodeURIComponent(jobId)}/report`;
  try { items = (await api(`/api/jobs/${jobId}/items?limit=500`)).items || []; }
  catch (error) { $('drawer-body').innerHTML = `<div class="empty">${esc(error.message)}</div>`; return; }
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
    : '<div class="empty">这次任务没有明细</div>';
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

function renderSettings() {
  const meta = state.settingsMeta;
  const s = state.settings;
  const modeCards = MODE_ORDER.map((mode) => {
    const info = state.modes.find((item) => item.mode === mode) || {};
    return `<div class="mode-card${s.transfer_mode === mode ? ' selected' : ''}" data-mode="${mode}">
      <h3>${esc(info.label || modeLabel(mode))}</h3><div class="mode-en">${esc(info.short || '')}</div>
      <p>${esc(info.short || '')}</p><div class="mode-tag">${s.transfer_mode === mode ? '✓ 当前使用' : '点击选用'}</div></div>`;
  }).join('');
  const presets = (meta.templates || []).map((preset) => (
    `<option value="${esc(preset.template)}">${esc(preset.name)}</option>`
  )).join('');
  const sourceChecks = SOURCE_OPTIONS.map(([name, label]) => `
    <label class="check-line"><input type="checkbox" data-source="${name}" ${(s.metadata_sources || []).includes(name) ? 'checked' : ''}>
      ${esc(label)}</label>`).join('');
  const accountCard = state.auth.is_admin ? `
    <div class="panel">
      <div class="panel-head"><div><h2>账户</h2>
        <p>当前登录：${esc(state.auth.username)}（管理员）。可以在这里添加新的使用账号。</p></div></div>
      <div class="grid cols-2">
        <label class="field"><span class="field-label">新用户名</span>
          <input id="new-user" type="text" autocomplete="off" placeholder="3–32 位字母、数字、_ . -"></label>
        <label class="field"><span class="field-label">新密码</span>
          <input id="new-pass" type="password" autocomplete="new-password" placeholder="至少 8 位"></label>
      </div>
      <button class="ghost-btn" id="create-user">添加账号</button>
    </div>` : '';
  const watch = state.watch || {};
  const watchCard = `
    <div class="panel">
      <div class="panel-head"><div><h2>自动刮削</h2>
        <p>盯住源目录，出现新文件就自动排一个刮削任务，不用再手动点</p></div>
        <span class="badge ${watch.enabled ? 'ok' : 'warn'}">${watch.enabled ? '运行中' : '已关闭'}</span></div>
      <label class="check-line"><input type="checkbox" id="f-watch" ${watch.enabled ? 'checked' : ''}>启用自动刮削（监控源目录）</label>
      <div class="grid cols-2">
        <label class="field"><span class="field-label">检查间隔（秒）</span>
          <input id="f-watch-interval" type="number" min="5" max="3600" step="5" value="${esc(watch.interval ?? 30)}"></label>
        <label class="field"><span class="field-label">稳定等待（秒）</span>
          <input id="f-watch-settle" type="number" min="0" max="3600" step="5" value="${esc(watch.settle ?? 10)}"></label>
      </div>
      <p class="small muted" style="margin:8px 0 12px">只处理<strong>库里还没有</strong>的音频：开启那一刻已存在的存量文件只登记、不刮削；已经整理进音乐库的文件（同名或同大小）也会自动过滤，不会再刮第二遍。稳定等待用于避开「还在下载 / 还在写入」的半成品文件，下载器的 <code>.part</code> / <code>.!qB</code> 之类临时文件同样不会触发。已处理记录存在数据库里，重启不会重刮。</p>
      <div class="btn-row">
        <button class="ghost-btn" id="watch-reset">把现有文件标记为已处理</button>
        <span class="small muted">${watch.enabled
          ? `已记录 ${esc(watch.tracked || 0)} 个源文件（含开启前就存在的 ${esc(watch.baselined || 0)} 个），音乐库里已有 ${esc(watch.in_library || 0)} 个文件不再重复刮削${watch.last_scan ? ` · 上次检查 ${esc(fmtTime(watch.last_scan))}` : ''}${watch.last_job_label ? ` · 最近任务：${esc(watch.last_job_label)}` : ''}`
          : '开启后这里会显示检查状态'}</span>
      </div>
      ${watch.last_error ? `<p class="small" style="color:var(--danger);margin-top:8px">${esc(watch.last_error)}</p>` : ''}
    </div>`;
  $('page').innerHTML = `
    <div class="panel">
      <div class="panel-head"><div><h2>目录</h2>
        <p>直接点「浏览…」从服务器挂载目录中选择，不需要手动输入路径</p></div></div>
      <div class="grid cols-2">
        ${pathField('f-source', '源目录（待刮削的下载文件夹）', s.source_dir, '选择源目录')}
        ${pathField('f-library', '音乐库目录（整理后的目标）', s.library_dir, '选择音乐库目录')}
      </div>
    </div>
    <div class="panel"><div class="panel-head"><div><h2>转移方式</h2>
      <p>决定刮削完成后文件如何进入音乐库；四种方式的完整区别见「转移方式说明」</p></div></div>
      <div class="mode-cards">${modeCards}</div>
    </div>
    <div class="panel"><div class="panel-head"><div><h2>命名与匹配</h2>
      <p>可用变量：${(meta.template_fields || []).map((f) => `<code>{${esc(f)}}</code> <span class="small muted">${esc(TEMPLATE_FIELD_LABELS[f] || '')}</span>`).join(' · ')}</p>
        <p>默认按「专辑名 / 专辑歌手 / 歌曲文件」建目录，播放器与音乐服务器都能直接认</p></div></div>
      <div class="grid cols-2">
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
      </div>
      <p class="small muted">模板示例：<span class="mono" id="template-example">${esc(meta.example_target || '')}</span></p>
    </div>
    <div class="grid cols-2">
      <div class="panel"><h2>标签与元数据</h2>
        <label class="check-line"><input type="checkbox" id="f-write-tags" ${s.write_tags ? 'checked' : ''}>写入音频标签</label>
        <label class="check-line"><input type="checkbox" id="f-cover" ${s.embed_cover ? 'checked' : ''}>下载并内嵌封面</label>
        <label class="check-line"><input type="checkbox" id="f-lyrics" ${s.write_lyrics ? 'checked' : ''}>写入歌词（网易云 / QQ音乐 / 酷狗 / 酷我）</label>
        <label class="check-line"><input type="checkbox" id="f-relative" ${s.symlink_relative ? 'checked' : ''}>软链接使用相对路径（推荐）</label>
        <div class="divider"></div><div class="small muted" style="margin-bottom:8px">在线元数据源</div>${sourceChecks}
      </div>
      <div class="panel"><h2>外观</h2>
        <p class="muted small" style="margin:8px 0 12px">默认夜间模式；「跟随系统」会按操作系统的明暗偏好自动切换。字号与字重随屏幕宽度自适应。</p>
        <div class="btn-row">
          <button class="ghost-btn" data-theme-choice="dark">夜间</button>
          <button class="ghost-btn" data-theme-choice="light">日间</button>
          <button class="ghost-btn" data-theme-choice="auto">跟随系统</button>
        </div>
        <div class="divider"></div>
        <h3>标签写入与链接的关系</h3>
        <p class="muted small" style="margin-top:8px">标签在转移之前写入源文件。使用硬链接 / 软链接时目标共享源文件数据，因此源文件标签也会被补全，之后修改任一边都会影响另一边。若不希望改动源文件，请关闭「写入音频标签」或改用复制。</p>
      </div>
    </div>
    ${watchCard}
    ${accountCard}
    <div class="panel"><div class="between">
      <div><h3>保存设置</h3>
        <p class="small muted" style="margin-top:4px">设置保存在数据目录的 SQLite 中，重启容器后依然生效；环境变量作为默认值。</p></div>
      <button class="primary-btn" id="save-settings">保存设置</button></div></div>`;
  applyTheme(themePreference());
  document.querySelectorAll('[data-mode]').forEach((card) => card.addEventListener('click', () => {
    state.settings.transfer_mode = card.dataset.mode;
    renderSettings();
  }));
  document.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.addEventListener('click', () => applyTheme(button.dataset.themeChoice));
  });
  document.querySelectorAll('[data-pick-dir]').forEach((button) => {
    button.addEventListener('click', () => openPicker(button.dataset.pickDir, button.dataset.pickTitle));
  });
  $('preset').addEventListener('change', (event) => {
    $('f-template').value = event.target.value;
    updateExample();
  });
  $('f-template').addEventListener('input', updateExample);
  $('save-settings').addEventListener('click', saveSettings);
  $('f-watch').addEventListener('change', saveWatch);
  $('watch-reset').addEventListener('click', async () => {
    try { state.watch = await api('/api/watch/reset', { method: 'POST' }); toast('已把现有文件标记为已处理'); await renderSettings(); }
    catch (error) { toast(error.message, true); }
  });
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

function updateExample() {
  const template = $('f-template').value;
  const example = template
    .replace(/\{artist\}/g, 'Queen').replace(/\{album\}/g, 'A Night at the Opera')
    .replace(/\{album_artist\}/g, 'Queen').replace(/\{title\}/g, 'Bohemian Rhapsody')
    .replace(/\{track(?::[^}]*)?\}/g, '11').replace(/\{disc(?::[^}]*)?\}/g, '1')
    .replace(/\{year\}/g, '1975').replace(/\{filename\}/g, '11 Bohemian Rhapsody')
    .replace(/\{ext\}/g, 'flac');
  $('template-example').textContent = `${example}.flac`;
}

async function saveSettings() {
  const values = {
    transfer_mode: state.settings.transfer_mode,
    naming_template: $('f-template').value.trim(),
    conflict_policy: $('f-conflict').value,
    unknown_placeholder: $('f-unknown').value.trim() || '未知',
    confidence_threshold: Number($('f-threshold').value),
    source_dir: $('f-source').value.trim(),
    library_dir: $('f-library').value.trim(),
    write_tags: $('f-write-tags').checked,
    embed_cover: $('f-cover').checked,
    write_lyrics: $('f-lyrics').checked,
    symlink_relative: $('f-relative').checked,
    metadata_sources: [...document.querySelectorAll('[data-source]')]
      .filter((box) => box.checked).map((box) => box.dataset.source),
  };
  try {
    const result = await api('/api/settings', { method: 'PUT', body: { values } });
    state.settings = result.settings;
    state.settingsMeta = result;
    toast('设置已保存');
    await renderSettings();
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
    await renderSettings();
  } catch (error) { toast(error.message, true); }
}

async function renderDocs() {
  let data;
  try { data = await api('/api/transfer-modes'); }
  catch (error) { toast(error.message, true); return; }
  const blocks = (data.modes || []).map((mode) => `
    <div class="doc-block">
      <h3>${esc(mode.label)}</h3>
      <div class="doc-en">${esc(mode.short)}</div>
      <p><b>实现：</b>${esc(mode.how)}</p>
      <div class="grid cols-2" style="gap:12px">
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
    <div class="banner info">${esc(data.ordering_note)}</div>
    <div class="panel"><h2>容器挂载要点</h2>
      <p class="muted small" style="margin-top:8px">硬链接要求「同一挂载点」：请把源目录和音乐库作为同一个宿主机父目录下的两个子目录一次性挂载进容器（例如 <code class="mono">/vol1/1000:/media</code>，对应 <code class="mono">/media/music_downloads</code> 与 <code class="mono">/media/音乐</code>）。同盘前提下两者设备号相同即可建立硬链接；若其中一个目录在宿主机上属于另一个存储池或独立挂载点，则无论怎么映射都无法使用硬链接。软链接默认写相对路径，容器内外都能解析。</p>
    </div>
    <div class="grid" style="gap:var(--gap)">${blocks}</div>`;
}

/* ---------------- boot ---------------- */
async function loadSettings() {
  const result = await api('/api/settings');
  state.settings = result.settings || {};
  state.settingsMeta = result;
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
$('drawer-close').addEventListener('click', closeDrawer);
$('drawer').addEventListener('click', (event) => {
  if (event.target === $('drawer')) closeDrawer();
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!$('drawer').classList.contains('hidden')) closeDrawer();
  else if (!$('picker').classList.contains('hidden')) closePicker();
  else if (!$('modal').classList.contains('hidden')) closeModal();
});
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
  if (themePreference() === 'auto') applyTheme('auto');
});
boot();
