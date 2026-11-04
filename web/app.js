/* MusicPilot Scraper - dependency-free single page UI.
 * Theme: dark by default, switchable to light or "follow system".
 */
'use strict';

const NAV = [
  { group: '工作区' },
  { id: 'dashboard', label: '概览', icon: '◉' },
  { id: 'files', label: '源文件', icon: '♫' },
  { id: 'plan', label: '整理计划', icon: '⇄' },
  { id: 'jobs', label: '任务记录', icon: '≡' },
  { group: '配置' },
  { id: 'settings', label: '刮削设置', icon: '⚙' },
  { id: 'docs', label: '转移方式说明', icon: 'ⓘ' },
];
const PAGE_META = {
  dashboard: ['概览', '目录状态、四种转移方式可用性与最近任务'],
  files: ['源文件', '扫描源目录、选择文件并生成整理计划'],
  plan: ['整理计划', '先预览源 → 目标的映射，确认后再执行'],
  jobs: ['任务记录', '查看进度、取消任务、导出 CSV 报告'],
  settings: ['刮削设置', '转移方式、命名模板、标签与元数据源'],
  docs: ['转移方式说明', '复制 / 移动 / 硬链接 / 软链接 的原理、限制与适用场景'],
};
const MODE_ORDER = ['copy', 'move', 'hardlink', 'symlink'];

const state = {
  page: 'dashboard',
  auth: { enabled: false, authenticated: true },
  settings: {},
  settingsMeta: {},
  modes: [],
  orderingNote: '',
  files: [],
  selected: new Set(),
  jobs: [],
  previewJob: null,
  currentJobId: null,
  pollTimer: null,
  capabilities: [],
  probed: false,
  options: { organize: true, skipLowConfidence: false },
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

async function api(path, options = {}) {
  const init = { credentials: 'same-origin', ...options };
  if (init.body && typeof init.body !== 'string') {
    init.headers = { ...(init.headers || {}), 'Content-Type': 'application/json' };
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  if (response.status === 401) {
    state.auth.authenticated = false;
    showLogin(true);
    throw new Error('需要登录');
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
function themePreference() {
  return localStorage.getItem('mp-theme') || 'dark';
}
function applyTheme(preference) {
  localStorage.setItem('mp-theme', preference);
  const theme = effectiveTheme(preference);
  document.documentElement.setAttribute('data-theme', theme);
  $('theme-icon').textContent = theme === 'dark' ? '☀' : '☾';
  $('theme-toggle').title = theme === 'dark' ? '切换到日间模式' : '切换到夜间模式';
  document.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.classList.toggle('active', button.dataset.themeChoice === preference);
    button.className = `ghost-btn${button.dataset.themeChoice === preference ? ' active' : ''}`;
  });
}
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  applyTheme(current === 'dark' ? 'light' : 'dark');
}

/* ---------------- auth ---------------- */
function showLogin(show) {
  $('login-overlay').classList.toggle('hidden', !show);
  $('logout-btn').classList.toggle('hidden', !state.auth.enabled || show);
  $('auth-hint').textContent = state.auth.enabled
    ? (show ? '未登录' : `已登录：${state.auth.username || 'admin'}`)
    : '未启用登录认证 · 请仅在可信内网使用';
}
async function refreshAuth() {
  state.auth = await api('/api/auth/status');
  showLogin(state.auth.enabled && !state.auth.authenticated);
}

/* ---------------- shell ---------------- */
function renderNav() {
  $('nav').innerHTML = NAV.map((item) => (
    item.group
      ? `<div class="nav-group">${esc(item.group)}</div>`
      : `<button class="nav-item${state.page === item.id ? ' active' : ''}" data-page="${item.id}">
           <span class="nav-icon">${item.icon}</span><span>${esc(item.label)}</span></button>`
  )).join('');
  document.querySelectorAll('[data-page]').forEach((button) => {
    button.addEventListener('click', () => go(button.dataset.page));
  });
}
function go(page) {
  state.page = page;
  renderNav();
  const [title, subtitle] = PAGE_META[page];
  $('page-title').textContent = title;
  $('page-subtitle').textContent = subtitle;
  renderPage();
}
function openModal(title, html) {
  $('modal-title').textContent = title;
  $('modal-body').innerHTML = html;
  $('modal').classList.remove('hidden');
}
function closeModal() { $('modal').classList.add('hidden'); }

/* ---------------- pages ---------------- */
function renderPage() {
  const renderers = {
    dashboard: renderDashboard,
    files: renderFiles,
    plan: renderPlan,
    jobs: renderJobs,
    settings: renderSettings,
    docs: renderDocs,
  };
  (renderers[state.page] || renderDashboard)();
}

function capabilityBadge(item) {
  if (item.available === true) return '<span class="badge ok">可用</span>';
  if (item.available === false) return '<span class="badge bad">不可用</span>';
  return '<span class="badge info">未自检</span>';
}

async function renderDashboard() {
  let data;
  try { data = await api('/api/dashboard'); }
  catch (error) { toast(error.message, true); return; }
  state.capabilities = data.capabilities;
  const same = data.same_device;
  const cards = [
    ['源目录', data.source_dir, data.source_exists ? 'ok' : 'bad', data.source_exists ? '目录可访问' : '目录不存在，请检查挂载'],
    ['音乐库目录', data.library_dir, data.library_exists ? 'ok' : 'bad', data.library_exists ? '目录可访问' : '首次执行时自动创建'],
    ['转移方式', state.settings.transfer_mode || 'copy', 'warn', same === null ? '等待目录就绪' : (same ? '源与库在同一挂载点 · 可用硬链接' : '源与库跨挂载点 · 硬链接不可用')],
    ['登录认证', data.version ? (state.auth.enabled ? '已启用' : '未启用') : '—', state.auth.enabled ? 'ok' : 'warn', state.auth.enabled ? '会话保护已开启' : '请仅在可信内网使用'],
  ];
  const modeRows = data.capabilities.map((item) => `
    <tr>
      <td><b>${esc(item.label)}</b> <span class="mono">${esc(item.mode)}</span></td>
      <td>${capabilityBadge(item)}${item.probed ? ' <span class="small muted">已自检</span>' : ''}</td>
      <td class="muted">${esc(item.reason)}</td>
    </tr>`).join('');
  const jobs = (data.jobs || []).map((job) => `
    <tr><td class="mono">${esc(job.id)}</td>
      <td>${job.dry_run ? '预览' : '刮削'}</td>
      <td>${statusBadge(job.status)}</td>
      <td class="muted">${esc(job.processed || 0)} 项</td>
      <td class="muted small">${esc(fmtTime(job.created_at))}</td></tr>`).join('');
  const recent = (data.recent_done || []).map((item) => `
    <tr><td class="truncate" title="${esc(item.source)}">${esc(item.source.split(/[\\/]/).pop())}</td>
      <td class="mono">${esc(item.mode)}</td>
      <td class="muted small">${esc(fmtTime(item.created_at))}</td></tr>`).join('');

  $('page').innerHTML = `
    <div class="grid cols-4">
      ${cards.map(([label, value, tone, note]) => `
        <div class="stat"><div class="stat-label">${esc(label)}</div>
          <div class="stat-value ${tone}">${esc(value)}</div>
          <div class="stat-note">${esc(note)}</div></div>`).join('')}
    </div>
    <div class="panel">
      <div class="panel-head">
        <div><h2>四种转移方式可用性</h2><p>硬链接要求源目录与音乐库位于同一文件系统/同一挂载点</p></div>
        <button class="ghost-btn" id="probe-btn">运行写入自检</button>
      </div>
      <div class="table-wrap"><table><thead><tr><th>方式</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>${modeRows}</tbody></table></div>
      <p class="small muted" style="margin-bottom:0">自检会在两个目录各创建一个临时文件并立即删除，用于确认写权限以及硬链接/符号链接是否被文件系统支持。</p>
    </div>
    <div class="grid cols-2">
      <div class="panel"><div class="panel-head"><h2>最近任务</h2>
        <button class="ghost-btn" data-go="jobs">查看全部</button></div>
        <div class="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>状态</th><th>数量</th><th>时间</th></tr></thead>
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

async function renderFiles() {
  let data;
  try { data = await api('/api/files'); }
  catch (error) { toast(error.message, true); return; }
  state.files = data.files;
  const warnings = data.warnings || [];
  banner(warnings.join('；'), 'info');
  const rows = data.files.map((file) => {
    const meta = file.local_metadata || {};
    return `<tr>
      <td><input type="checkbox" data-pick="${esc(file.path)}" ${state.selected.has(file.path) ? 'checked' : ''}></td>
      <td class="truncate" title="${esc(file.path)}">${esc(file.display_name)}</td>
      <td>${esc(meta.title || '—')}</td><td>${esc(meta.artist || '—')}</td><td>${esc(meta.album || '—')}</td>
      <td class="muted">${fmtSize(file.size)}</td><td class="muted">${fmtDuration(file.duration)}</td>
      <td><button class="ghost-btn small" data-detail="${esc(file.path)}">详情</button></td></tr>`;
  }).join('');
  $('page').innerHTML = `
    <div class="panel">
      <div class="panel-head"><div><h2>源目录：${esc(data.source_dir)}</h2>
        <p>共扫描到 ${data.total} 个音频文件${data.truncated ? '（列表只显示前 1000 个）' : ''}</p></div>
        <div class="btn-row">
          <button class="ghost-btn" id="refresh-files">重新扫描</button>
          <button class="ghost-btn" id="select-all">全选</button>
          <button class="ghost-btn" id="clear-selection">清空选择</button>
        </div></div>
      <div class="table-wrap"><table><thead><tr>
        <th></th><th>文件名</th><th>标题</th><th>歌手</th><th>专辑</th><th>大小</th><th>时长</th><th>操作</th>
      </tr></thead><tbody>${rows || '<tr><td colspan="8" class="empty">源目录中没有找到音频文件</td></tr>'}</tbody></table></div>
    </div>
    <div class="panel">
      <div class="panel-head"><div><h2>对选中的 ${state.selected.size} 个文件执行</h2>
        <p>不选择任何文件时，默认处理源目录内全部音频文件</p></div></div>
      <div class="btn-row" style="margin-bottom:12px">
        <label class="check-line"><input type="checkbox" id="opt-organize" ${state.options.organize ? 'checked' : ''}>整理到音乐库（关闭则只写标签）</label>
        <label class="check-line"><input type="checkbox" id="opt-skip" ${state.options.skipLowConfidence ? 'checked' : ''}>跳过低置信度文件</label>
      </div>
      <div class="btn-row">
        <button class="ghost-btn" id="preview-btn">① 生成整理计划（预览）</button>
        <button class="primary-btn" id="scrape-btn">② 直接刮削整理</button>
        <span class="small muted">当前方式：<b>${esc(state.settings.transfer_mode || 'copy')}</b> · 建议先预览</span>
      </div>
    </div>`;
  $('refresh-files').addEventListener('click', renderFiles);
  $('select-all').addEventListener('click', () => { state.files.forEach((f) => state.selected.add(f.path)); renderFiles(); });
  $('clear-selection').addEventListener('click', () => { state.selected.clear(); renderFiles(); });
  document.querySelectorAll('[data-pick]').forEach((box) => box.addEventListener('change', () => {
    if (box.checked) state.selected.add(box.dataset.pick); else state.selected.delete(box.dataset.pick);
    const counter = document.querySelector('.panel:last-child h2');
    if (counter) counter.textContent = `对选中的 ${state.selected.size} 个文件执行`;
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
  const row = (label, value) => `<tr><th>${esc(label)}</th><td>${esc(value || '—')}</td></tr>`;
  openModal('文件详情', `
    <div class="table-wrap"><table><tbody>
      ${row('路径', data.file.path)}${row('大小', fmtSize(data.file.size))}
      ${row('时长', fmtDuration(data.file.duration))}${row('码率', data.technical?.bitrate ? `${Math.round(data.technical.bitrate / 1000)} kbps` : '—')}
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
  let items = [];
  try { items = (await api(`/api/jobs/${job.id}/items?limit=2000`)).items || []; }
  catch (error) { toast(error.message, true); }
  const rows = items.map((item) => `<tr>
      <td class="truncate" title="${esc(item.source)}">${esc(item.source.split(/[\\/]/).pop())}</td>
      <td class="mono truncate" title="${esc(item.target)}">${esc(item.target)}</td>
      <td>${esc(item.mode)}</td>
      <td>${(item.confidence || 0).toFixed(2)}</td>
      <td>${item.status === 'needs_review' ? '<span class="badge warn">需人工确认</span>' : statusBadge(item.status)}</td>
      <td class="muted small">${esc(item.detail)}</td></tr>`).join('');
  $('page').innerHTML = `
    <div class="panel">
      <div class="panel-head"><div><h2>整理计划 ${esc(job.id)}</h2>
        <p>${job.dry_run ? '预览结果，尚未写入标签或移动任何文件' : '已执行任务'} · 共 ${items.length} 项 · ${fmtTime(job.created_at)}</p></div>
        <div class="btn-row"><button class="ghost-btn" id="plan-csv">导出 CSV</button>
          ${job.dry_run ? '<button class="primary-btn" id="run-plan">按此计划执行整理</button>' : ''}</div></div>
      <div class="table-wrap"><table><thead><tr>
        <th>源文件</th><th>目标路径</th><th>方式</th><th>置信度</th><th>状态</th><th>说明</th>
      </tr></thead><tbody>${rows || '<tr><td colspan="6" class="empty">没有计划项</td></tr>'}</tbody></table></div>
    </div>
    <div class="panel"><h2 style="font-size:15px;margin:0 0 8px">执行说明</h2>
      <p class="muted" style="margin:0">标签会在文件转移之前写入源文件。复制与移动之后目标文件独立；硬链接与软链接的目标共享源文件数据，源文件的标签也会一起被补全。详见「转移方式说明」。</p>
    </div>`;
  $('plan-csv').addEventListener('click', () => { window.location.href = `/api/jobs/${job.id}/report`; });
  const run = $('run-plan');
  if (run) run.addEventListener('click', async () => {
    if (!confirm(`确认按计划处理 ${items.length} 个文件？将写入标签并使用「${state.settings.transfer_mode}」方式转移。`)) return;
    const paths = items.map((item) => item.source);
    await createJob('scrape', false, paths);
  });
}

function statusBadge(status) {
  const map = {
    completed: ['ok', '已完成'], running: ['info', '运行中'], pending: ['info', '排队中'],
    failed: ['bad', '失败'], cancelled: ['warn', '已取消'], done: ['ok', '成功'],
    skipped: ['warn', '跳过'], planned: ['info', '计划中'], needs_review: ['warn', '需确认'],
  };
  const [tone, label] = map[status] || ['info', status];
  return `<span class="badge ${tone}">${esc(label)}</span>`;
}

async function renderJobs() {
  let jobs = [];
  try { jobs = (await api('/api/jobs?limit=50')).jobs || []; }
  catch (error) { toast(error.message, true); }
  state.jobs = jobs;
  const active = jobs.find((job) => job.status === 'running' || job.status === 'pending');
  const progressCard = active ? `
    <div class="panel"><div class="panel-head"><div><h2>当前任务 ${esc(active.id)}</h2>
      <p>${esc(active.message || '正在处理')}</p></div>
      <button class="danger-btn" id="cancel-job">取消任务</button></div>
      <div class="progress"><span style="width:${Math.round(100 * (active.processed || 0) / Math.max(1, active.total || active.processed || 1))}%"></span></div>
      <div class="between small muted" style="margin-top:8px">
        <span>已处理 ${active.processed || 0} / ${active.total || '—'}</span>
        <span>成功 ${active.done || 0} · 跳过 ${active.skipped || 0} · 失败 ${active.failed || 0}</span></div>
    </div>` : '';
  const rows = jobs.map((job) => `<tr>
    <td class="mono">${esc(job.id)}</td><td>${job.dry_run ? '预览' : '刮削'}</td>
    <td>${statusBadge(job.status)}</td>
    <td class="muted">${esc(job.processed || 0)} / ${esc(job.total || 0)}</td>
    <td class="muted small">${esc(fmtTime(job.created_at))}</td>
    <td class="btn-row">
      <button class="ghost-btn small" data-job="${esc(job.id)}">明细</button>
      <a class="ghost-btn small" href="/api/jobs/${esc(job.id)}/report">CSV</a>
      ${job.dry_run ? `<button class="ghost-btn small" data-plan="${esc(job.id)}">打开计划</button>` : ''}
    </td></tr>`).join('');
  $('page').innerHTML = `${progressCard}
    <div class="panel"><div class="panel-head"><div><h2>全部任务</h2><p>每次只允许一个任务运行，避免文件竞争</p></div>
      <button class="ghost-btn" id="refresh-jobs">刷新</button></div>
      <div class="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>状态</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="empty">暂无任务</td></tr>'}</tbody></table></div>
    </div>
    <div id="job-items"></div>`;
  $('refresh-jobs').addEventListener('click', renderJobs);
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
}

async function showJobItems(jobId) {
  let items = [];
  try { items = (await api(`/api/jobs/${jobId}/items?limit=500`)).items || []; }
  catch (error) { toast(error.message, true); return; }
  const rows = items.map((item) => `<tr>
    <td class="truncate" title="${esc(item.source)}">${esc(item.source.split(/[\\/]/).pop())}</td>
    <td class="mono truncate">${esc(item.target)}</td><td>${esc(item.mode)}</td>
    <td>${statusBadge(item.status)}</td><td class="muted small">${esc(item.detail)}</td></tr>`).join('');
  $('job-items').innerHTML = `<div class="panel"><div class="panel-head"><h2>任务 ${esc(jobId)} 明细</h2></div>
    <div class="table-wrap"><table><thead><tr><th>源文件</th><th>目标</th><th>方式</th><th>状态</th><th>说明</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="empty">暂无明细</td></tr>'}</tbody></table></div></div>`;
  $('job-items').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function createJob(kind, dryRun, forcedPaths) {
  const paths = forcedPaths || [...state.selected];
  const body = {
    kind, dry_run: dryRun, paths,
    organize: state.options.organize,
    skip_low_confidence: state.options.skipLowConfidence,
  };
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
    catch (error) { clearInterval(state.pollTimer); return; }
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

async function renderSettings() {
  const meta = state.settingsMeta;
  const s = state.settings;
  const modeCards = MODE_ORDER.map((mode) => {
    const info = state.modes.find((item) => item.mode === mode) || {};
    return `<div class="mode-card${s.transfer_mode === mode ? ' selected' : ''}" data-mode="${mode}">
      <h3>${esc(info.label || mode)}</h3><div class="mode-en">${esc(info.english || '')}</div>
      <p>${esc(info.short || '')}</p><div class="mode-tag">${s.transfer_mode === mode ? '✓ 当前使用' : '点击选用'}</div></div>`;
  }).join('');
  const presets = (meta.templates || []).map((preset) => (
    `<option value="${esc(preset.template)}">${esc(preset.name)}</option>`
  )).join('');
  const sourceChecks = ['local', 'musicbrainz', 'netease'].map((name) => `
    <label class="check-line"><input type="checkbox" data-source="${name}" ${(s.metadata_sources || []).includes(name) ? 'checked' : ''}>
      ${({ local: '本地标签与文件名（始终参与）', musicbrainz: 'MusicBrainz（国际曲库）', netease: '网易云音乐（中文曲库 / 歌词 / 封面）' })[name]}</label>`).join('');
  $('page').innerHTML = `
    <div class="panel"><div class="panel-head"><div><h2>转移方式</h2>
      <p>决定刮削完成后文件如何进入音乐库；四种方式的完整区别见「转移方式说明」</p></div></div>
      <div class="mode-cards">${modeCards}</div>
    </div>
    <div class="panel"><div class="panel-head"><div><h2>命名与冲突</h2>
      <p>可用变量：${(meta.template_fields || []).map((f) => `<code>{${esc(f)}}</code>`).join(' ')}</p></div></div>
      <div class="grid cols-2">
        <label>命名模板预设<select id="preset">${presets}</select></label>
        <label>命名模板<input id="f-template" value="${esc(s.naming_template || '')}"></label>
        <label>目标冲突策略<select id="f-conflict">
          <option value="rename" ${s.conflict_policy === 'rename' ? 'selected' : ''}>自动改名（保留两者）</option>
          <option value="skip" ${s.conflict_policy === 'skip' ? 'selected' : ''}>跳过（保留已有文件）</option>
          <option value="overwrite" ${s.conflict_policy === 'overwrite' ? 'selected' : ''}>覆盖已有文件</option>
        </select></label>
        <label>缺失字段占位符<input id="f-unknown" value="${esc(s.unknown_placeholder || 'Unknown')}"></label>
        <label>在线匹配置信度阈值（0–1）<input id="f-threshold" type="number" min="0" max="1" step="0.05" value="${esc(s.confidence_threshold ?? 0.62)}"></label>
        <label>源目录<input id="f-source" value="${esc(s.source_dir || '')}"></label>
        <label>音乐库目录<input id="f-library" value="${esc(s.library_dir || '')}"></label>
      </div>
      <p class="small muted">模板示例：<span class="mono" id="template-example">${esc(meta.example_target || '')}</span></p>
    </div>
    <div class="grid cols-2">
      <div class="panel"><h2 style="font-size:15px;margin:0 0 12px">标签与元数据</h2>
        <label class="check-line"><input type="checkbox" id="f-write-tags" ${s.write_tags ? 'checked' : ''}>写入音频标签</label>
        <label class="check-line"><input type="checkbox" id="f-cover" ${s.embed_cover ? 'checked' : ''}>下载并内嵌封面</label>
        <label class="check-line"><input type="checkbox" id="f-lyrics" ${s.write_lyrics ? 'checked' : ''}>写入歌词（网易云）</label>
        <label class="check-line"><input type="checkbox" id="f-relative" ${s.symlink_relative ? 'checked' : ''}>软链接使用相对路径（推荐）</label>
        <div class="divider"></div><div class="small muted" style="margin-bottom:8px">在线元数据源</div>${sourceChecks}
      </div>
      <div class="panel"><h2 style="font-size:15px;margin:0 0 12px">外观</h2>
        <p class="muted small">夜间模式为默认；选择「跟随系统」后按操作系统的明暗偏好自动切换。</p>
        <div class="btn-row">
          <button class="ghost-btn" data-theme-choice="dark">夜间</button>
          <button class="ghost-btn" data-theme-choice="light">日间</button>
          <button class="ghost-btn" data-theme-choice="auto">跟随系统</button>
        </div>
        <div class="divider"></div>
        <h2 style="font-size:15px;margin:0 0 8px">标签写入与链接的关系</h2>
        <p class="muted small" style="margin:0">标签在转移之前写入源文件。使用硬链接/软链接时目标共享源文件数据，因此源文件标签也会被补全，之后修改任一边都会影响另一边。若不希望改动源文件，请关闭「写入音频标签」或改用复制。</p>
      </div>
    </div>
    <div class="panel"><div class="between">
      <div><h2 style="font-size:15px;margin:0">保存设置</h2>
        <p class="small muted" style="margin:2px 0 0">设置保存在数据目录的 SQLite 中，重启容器后依然生效；环境变量作为默认值。</p></div>
      <button class="primary-btn" id="save-settings">保存设置</button></div></div>`;
  applyTheme(themePreference());
  document.querySelectorAll('[data-mode]').forEach((card) => card.addEventListener('click', () => {
    state.settings.transfer_mode = card.dataset.mode;
    renderSettings();
  }));
  document.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.addEventListener('click', () => applyTheme(button.dataset.themeChoice));
  });
  $('preset').addEventListener('change', (event) => {
    $('f-template').value = event.target.value;
    updateExample();
  });
  $('f-template').addEventListener('input', updateExample);
  $('save-settings').addEventListener('click', saveSettings);
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
    unknown_placeholder: $('f-unknown').value.trim() || 'Unknown',
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

async function renderDocs() {
  let data;
  try { data = await api('/api/transfer-modes'); }
  catch (error) { toast(error.message, true); return; }
  const modes = data.modes || [];
  const blocks = modes.map((mode) => `
    <div class="doc-block">
      <h3>${esc(mode.label)} <span class="mono">${esc(mode.mode)}</span></h3>
      <div class="doc-en">${esc(mode.english)} · ${esc(mode.short)}</div>
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
    <div class="panel"><h2 style="font-size:15px;margin:0 0 8px">Docker 挂载要点</h2>
      <p class="muted" style="margin:0">硬链接要求「同一挂载点」：请把源目录和音乐库作为同一个宿主机父目录下的两个子目录一次性挂载到容器（例如 <code class="mono">/volume1/media:/media</code>，对应 <code class="mono">/media/downloads</code> 与 <code class="mono">/media/music</code>）。分别挂载 <code class="mono">/downloads</code> 和 <code class="mono">/music</code> 会被视为两个挂载点，硬链接必然失败。软链接默认写相对路径，因此容器内创建的链接在宿主机上也能解析；若改用绝对路径，请确保播放端能看到完全相同的容器路径。</p>
    </div>
    ${blocks}`;
}

/* ---------------- boot ---------------- */
async function loadSettings() {
  const result = await api('/api/settings');
  state.settings = result.settings || {};
  state.settingsMeta = result;
}
async function loadModes() {
  const result = await api('/api/transfer-modes');
  state.modes = result.modes || [];
  state.orderingNote = result.ordering_note || '';
}
async function boot() {
  applyTheme(themePreference());
  try {
    await refreshAuth();
    await Promise.all([loadSettings(), loadModes()]);
    const about = await api('/api/about');
    $('version').textContent = `v${about.version}`;
    go('dashboard');
  } catch (error) {
    banner(`初始化失败：${error.message}`, 'bad');
  }
}

$('theme-toggle').addEventListener('click', toggleTheme);
$('modal-close').addEventListener('click', closeModal);
$('modal').addEventListener('click', (event) => { if (event.target === $('modal')) closeModal(); });
$('logout-btn').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* ignore */ }
  await refreshAuth();
  toast('已退出登录');
});
$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/auth/login', {
      method: 'POST',
      body: { username: $('login-user').value, password: $('login-pass').value },
    });
    $('login-error').textContent = '';
    await refreshAuth();
    await boot();
  } catch (error) { $('login-error').textContent = error.message; }
});
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
  if (themePreference() === 'auto') applyTheme('auto');
});
boot();
