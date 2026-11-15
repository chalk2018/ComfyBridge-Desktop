/* ComfyBridge 1.01 前端
 * 界面形态与交互结构按已确认的版式实现；状态与判定全部来自服务端，
 * 前端只做「呈现 + 触发」（需求 §0.2）。页面刷新、多窗口都重新拉状态。
 */
'use strict';

let S = null;                       // 服务端状态镜像
const UI = {
  route: 'studio', adminPage: 'caps', capId: 'image.generate',
  refs: [], params: {}, comfyOnline: false,
  mode: 't2i',                                   // 't2i' = 文生图, 'i2i' = 图生图（参考生图）
  prompt: '',                                    // 序列化后的提示词文本（含 <imageN> 引用令牌）
  // 角色设定图的表单（只有 sheet.character 用）：版式/画法/参考方式/角色行/风格
  sheetForm: { layout: 'A', ink: 'color', lineStyle: '', ref: 'lock', grade: '', character: '', style: '', styleText: '' },
  sheetOpen: false,                              // 「看完整提示词」面板是否展开
  sheetPreview: null,                            // 最近一次服务端组装结果
  comboSel: '',                                  // 管理台档位表当前选中的组合（C.color.lock 这种）
  comboDetail: null,                             // 该组合的档位参数生效核对结果
  gradeForm: null,                               // 新建/编辑档位的表单草稿
  selTasks: new Set(),                           // 本会话任务批量删除的勾选集合
  sheet: false,                                  // 手机端右栏抽屉（状态与任务）是否拉起
  navOpen: false,                                // 手机端左侧抽屉（能力 / 管理台菜单）是否拉起
  editingWfIndex: 0, gOpen: 0, draft: { name: '', values: {} },
  galleryFilter: '全部', galleryDate: '全部', selected: new Set(),
  wizardSeen: false, autoScroll: false, qJobId: null, qGrade: null,
  _navPending: false, _draftPending: false,      // 导航/草稿写入在途标记（轮询别把旧值灌回来）
};
let _pollTimer = null;
let _lastSignature = '';

/* ------------------------------------------------------------ 基础工具 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nn = (v, d = 0) => (Number.isFinite(+v) ? +v : d);

function toast(msg, ms = 2000) {
  $$('.toast').forEach(x => x.remove());
  const el = document.createElement('div');
  el.className = 'toast'; el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = '.2s'; setTimeout(() => el.remove(), 220); }, ms);
}
function toastBlocked(title, detail, ms = 4600) {
  $$('.tblock').forEach(e => e.remove());
  const el = document.createElement('div');
  el.className = 'tblock';
  el.innerHTML = `<div class="tb-i">⏳</div><div class="tb-t"><b>${esc(title)}</b><span>${esc(detail)}</span></div>`;
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 260); }, ms);
}

async function api(path, options = {}) {
  const init = { method: options.method || (options.body ? 'POST' : 'GET'), headers: {} };
  if (options.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  if (options.form) { init.body = options.form; init.method = 'POST'; delete init.headers['Content-Type']; }
  if (options.method === 'DELETE' || options.method === 'PUT') init.method = options.method;
  let resp;
  try { resp = await fetch(path, init); }
  catch (e) { toast('连不上服务，检查应用是不是还开着'); return { ok: false, reason: String(e) }; }
  let data;
  try { data = await resp.json(); }
  catch (e) { data = { ok: false, reason: `服务返回了非 JSON 响应（HTTP ${resp.status}）` }; }
  if (!resp.ok) data.ok = false;
  // 405/500 这类错误后端不一定带 reason，补一个能看懂的说法，别让调用方摸黑
  if (!resp.ok && !data.reason) data.reason = `服务返回 HTTP ${resp.status}`;
  return data;
}

/* 图片格式：上传/粘贴时统一按这几个常见类型处理（扩展名缺失时按 MIME 补） */
const MIME_EXT = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/bmp': '.bmp', 'image/gif': '.gif', 'image/tiff': '.tif' };
const IMAGE_EXT_RE = /\.(png|jpe?g|jfif|webp|bmp|gif|tiff?)$/i;

function normalizeImageFile(file) {
  // 从剪贴板/某些站点拿到的 File 可能没有扩展名（浏览器就叫它 image），
  // 服务端是按扩展名收图的，这里按 MIME 补一个，否则会被判成「不是图片格式」
  if (!file || !(file.type || '').startsWith('image/')) return file;
  if (IMAGE_EXT_RE.test(file.name || '')) return file;
  const ext = MIME_EXT[file.type] || '.png';
  return new File([file], `paste-${Date.now()}${ext}`, { type: file.type });
}

function clipboardImages(e) {
  const dt = e.clipboardData || window.clipboardData;
  if (!dt) return [];
  let files = [...(dt.files || [])];
  if (!files.length && dt.items) {
    for (const it of dt.items) {
      if (it.kind === 'file' && (it.type || '').startsWith('image/')) {
        const f = it.getAsFile(); if (f) files.push(f);
      }
    }
  }
  return files.filter(f => f && (f.type || '').startsWith('image/'));
}

async function imageToPngBlob(url) {
  // 剪贴板只吃 PNG，别的格式先过一遍 canvas 转码
  const resp = await fetch(url, { cache: 'no-store' });
  if (!resp.ok) throw new Error('图片读取失败');
  const blob = await resp.blob();
  if (blob.type === 'image/png') return blob;
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width; canvas.height = bitmap.height;
  canvas.getContext('2d').drawImage(bitmap, 0, 0);
  return await new Promise((resolve, reject) => canvas.toBlob(
    b => (b ? resolve(b) : reject(new Error('转 PNG 失败'))), 'image/png'));
}

async function copyImageToClipboard(url, label = '图片') {
  try {
    if (!navigator.clipboard || !window.ClipboardItem) throw new Error('这个浏览器不支持写剪贴板');
    const png = await imageToPngBlob(url);
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
    toast(`${label}已复制 · 切到「图生图」后在输入框 Ctrl+V 就是参考图`);
    return true;
  } catch (err) {
    toast('复制失败：' + (err && err.message ? err.message : err) + '（也可以右键图片选「复制图片」）');
    return false;
  }
}

function stateSignature() {
  if (!S) return '';
  // 进度百分比不进签名：pct 变化只局部刷新进度条，不重建 DOM（避免图片闪烁）。
  // 删除标记必须进签名，否则另一窗口删除后不会同步刷新。
  const runs = S.jobs.map(j => `${j.id}:${j.sessionRemoved ? 1 : 0}:` +
    (j.runs || []).map(r => `${r.id}:${r.state}:${r.file}:${r.hidden ? 1 : 0}:` +
      `${(r.files || []).filter(f => f.deleted).length}`).join(',')).join('|');
  return `${S.health.online}|${S.health.queue.running}|${S.health.queue.pending}|${runs}|${S.capabilities.length}|${S.configError || ''}`;
}

function hashRoute() {
  const h = decodeURIComponent(location.hash.replace(/^#/, ''));
  if (h.startsWith('admin/')) return { route: 'admin', adminPage: h.split('/')[1] || 'caps' };
  if (['studio', 'admin', 'gallery'].includes(h)) return { route: h };
  return null;
}
function syncHash() {
  // 地址栏始终反映当前页面：刷新/新开窗口能回到原处，也避免 hash 与服务端状态打架
  const h = UI.route === 'admin' ? `#admin/${UI.adminPage}` : `#${UI.route}`;
  if (location.hash !== h) history.replaceState(null, '', h);
}
async function fetchState() {
  const prevDiag = S && S.diagRows;
  const data = await api('/api/state');
  if (!data || !data.capabilities) return false;
  S = data;
  if (prevDiag) S.diagRows = prevDiag;
  UI.comfyOnline = S.health.online;
  if (!S.ui) S.ui = {};
  // 本地刚发起导航、保存还没回来时，不许服务端旧值把页面拉回去。
  // capId 也必须一起挡：点档位表子菜单是「先改本地 capId、再 await 写回服务端」，
  // 这中间只要撞上一次每秒轮询，旧 capId 就会把页面拽回上一个能力（表现为「子菜单点了没反应」）。
  if (!UI._navPending) {
    if (S.ui.capId && S.ui.capId !== UI.capId && !UI._userSwitched) UI.capId = S.ui.capId;
    if (S.ui.adminPage) UI.adminPage = S.ui.adminPage;
  }
  if (S.ui.galleryFilter) UI.galleryFilter = S.ui.galleryFilter;
  if (S.ui.wizardSeen) UI.wizardSeen = true;
  // 只在切换能力时载入草稿：以前每次轮询都 loadDraft，会用服务端旧草稿把正在编辑的
  // 模式/参考图/文字覆盖掉（切图生图后被拽回文生图、上传的图被清空、刚清空的输入框又被填回）。
  // 另外草稿还在写入途中时不载入：那一轮拿到的是「写之前」的服务端快照，
  // 用它覆盖等于把用户刚点的选项又弹回去，下一轮（写完了）再载入才对。
  if (UI._draftCap !== UI.capId && !UI._draftPending) {
    UI._draftCap = UI.capId;
    loadDraft();
  }
  return true;
}

function draftKey() { return UI.capId; }
function loadDraft() {
  const d = ((S.ui.drafts || {})[draftKey()] || {});
  UI.params = { grade: d.grade || '默认' };
  UI.refs = Array.isArray(d.refs) ? d.refs.map(id => ({ id, kind: 'image' })) : [];
  UI.mode = (d.mode === 'i2i' || d.mode === 't2i') ? d.mode : 't2i';
  UI.prompt = d.prompt || '';
  // 角色设定图：表单和「提示词面板」也一起恢复，切走再切回来不用重填。
  // 只在设定图能力下恢复——别的能力的草稿里没有 sheet 字段，
  // 拿它去覆盖会把「面板展开态」误清成 false、把表单拽回默认值。
  if (isSheetCap(currentCap())) {
    if (d.sheet && typeof d.sheet === 'object') UI.sheetForm = { ...UI.sheetForm, ...d.sheet };
    UI.sheetOpen = !!d.sheetOpen;
    UI.sheetPreview = null;
  }
}
function currentDraftText() {
  const out = { prompt: serializeEditor(), refs: UI.refs.map(r => r.id),
                grade: UI.params.grade || '默认', mode: UI.mode };
  if (isSheetCap(currentCap())) {
    out.sheet = { ...UI.sheetForm };
    out.sheetOpen = !!UI.sheetOpen;
  }
  return out;
}
function saveDraft(extra = {}) {
  // route/adminPage 每次都写全：以前从「配置」按钮进档位表时没存 adminPage，
  // 勾一下节点触发 pull 就被服务端的旧值（caps）拽回去，看起来像页面跳走。
  const patch = {
    capId: UI.capId, route: UI.route, adminPage: UI.adminPage,
    galleryFilter: UI.galleryFilter, editingWfIndex: UI.editingWfIndex,
    drafts: { ...(S.ui.drafts || {}), [draftKey()]: currentDraftText() }, ...extra,
  };
  S.ui.drafts = patch.drafts;
  S.ui.adminPage = patch.adminPage;
  S.ui.route = patch.route;
  UI._navPending = true;
  UI._draftPending = true;
  const p = api('/api/ui', { body: { patch } });
  p.finally(() => { UI._navPending = false; UI._draftPending = false; });
  return p;
}
let _draftTimer = null;
function scheduleDraft(ms = 450) {
  // 打字类输入不要每个键都打一次接口：攒 450ms 再写，草稿照样不丢
  clearTimeout(_draftTimer);
  _draftTimer = setTimeout(() => { _draftTimer = null; saveDraft(); }, ms);
}

/* ============================================================ 角色设定图
   为什么单独一套表单：图片生成是「一句描述 + 一张图」，角色设定图是
   「版式 + 画法 + 参考方式 + 两行变量」——提示词 800 字左右且由固定段落拼成，
   让用户在输入框里手写整段既容易写漏（漏一句分割线就出竖线），也没法复用提示词库。
   这里只填变量，整条提示词由服务端按 prompts/character_sheet.json 组装。 */
function sheetLib() { return (S && S.sheetLib) || {}; }
function sheetLayouts() { return sheetLib().layouts || []; }
function sheetStyles() { return (sheetLib().styles || []).map(s => s.name); }
function sheetLineStyles() { return sheetLib().line_styles || []; }
function sheetLayoutOf(id) { return sheetLayouts().find(l => l.id === id) || null; }
function sheetForm() {
  // 库改过 / 档位被删时把表单拉回合法值，避免「版式不存在」这种低级报错
  const f = UI.sheetForm = UI.sheetForm || {};
  const defs = { layout: '', ink: 'color', lineStyle: '', ref: 'lock', grade: '', character: '', style: '', styleText: '' };
  Object.keys(defs).forEach(k => { if (f[k] === undefined || f[k] === null) f[k] = defs[k]; });
  const layouts = sheetLayouts();
  if (layouts.length && !layouts.some(l => l.id === f.layout)) f.layout = layouts[0].id;
  if (!f.lineStyle) f.lineStyle = (sheetLineStyles()[0] || {}).id || '';
  const styles = sheetStyles();
  if (!f.style && styles.length) f.style = styles[0];
  return f;
}
function sheetLayout() { return sheetLayoutOf(sheetForm().layout) || sheetLayouts()[0] || null; }
/* 组合 = 版式 × 画法 × 参考方式：一个组合就是一条母版配方（提示词 + 工作流 + 画幅）。
   档位只在这个组合里可选，换组合时旧档位可能不存在，所以要回到「母版」。 */
function sheetCombos() { return (S && S.sheetCombos) || []; }
function sheetComboOf(layout, ink, ref) {
  return sheetCombos().find(c => c.layout === layout && c.ink === ink && c.ref === ref) || null;
}
function sheetCombo() {
  const f = sheetForm();
  return sheetComboOf(f.layout, f.ink, UI.mode === 'i2i' ? f.ref : 'none');
}
function sheetGrades() { const c = sheetCombo(); return (c && c.grades) || []; }
function sheetGrade() {
  const f = sheetForm(), grades = sheetGrades();
  if (!grades.length) return null;
  // 组合一变，原来的档位可能不在这个组合里（「精细」可能只在某几个组合下建过）
  if (!grades.some(g => g.name === f.grade)) f.grade = grades[0].name;
  return grades.find(g => g.name === f.grade) || grades[0];
}
function sheetStyleValue() {
  const f = sheetForm(), custom = String(f.styleText || '').trim();
  return custom || f.style;
}
function sheetPayload() {
  const f = sheetForm();
  return {
    layout: f.layout, character: String(f.character || '').trim(), style: sheetStyleValue(),
    ink: f.ink, lineStyle: f.ink === 'line' ? (f.lineStyle || null) : null,
    ref: UI.mode === 'i2i' ? f.ref : 'none',
    // 档位只影响参数（画幅/像素/CFG/步数），提示词由组合决定，跟档位无关
    grade: (sheetGrade() || {}).name || '默认',
  };
}
function sheetPreviewHTML() {
  const p = UI.sheetPreview;
  if (!p) return '<div class="pvhead">正在组装提示词…</div>';
  if (p.error) return `<div class="pvhead bad">✕ ${esc(p.error)}</div>`;
  return `<div class="pvhead"><b>${esc(p.grade || '')}</b>
      <span class="tiny muted">${esc(p.size || '')} · ${esc(p.wf || '')} · 共 ${String((p.prompt || '').length)} 字</span></div>
    <pre>${esc(p.prompt || '')}</pre>`;
}
async function refreshSheetPreview() {
  // 预览永远是「服务端真正会提交的那条」，前端不另拼一份，免得两边漂移
  const res = await api('/api/sheet/preview', { body: sheetPayload() });
  UI.sheetPreview = res.ok ? (res.data || {}) : { error: res.reason || '组装失败' };
  const box = $('#sheetPv');
  if (box) { box.innerHTML = sheetPreviewHTML(); bindSheetPreviewButtons(); }
  else refreshComposer();
}
function bindSheetPreviewButtons() {
  const cp = $('#sheetCopy');
  if (cp) cp.onclick = async () => {
    const text = (UI.sheetPreview && UI.sheetPreview.prompt) || '';
    try { await navigator.clipboard.writeText(text); toast('整条提示词已复制'); }
    catch (e) { toast('复制失败，手动选中复制一下'); }
  };
}

function viewSheetComposer(cap) {
  const lib = sheetLib();
  const f = sheetForm();
  if (!lib.layouts || !lib.layouts.length) {
    return `<div class="composer"><div class="composer-inner"><div class="inputbox" style="border-color:var(--line);box-shadow:none;background:#fafbfe">
      <div class="tiny muted" style="padding:18px 4px;text-align:center;line-height:1.9">提示词库读不到（prompts/character_sheet.json）。<br>
      去管理台 →「设定图模板」看一眼，或点顶栏「重读配置」。</div></div></div></div>`;
  }
  const layout = sheetLayout();
  const grade = sheetGrade();
  const grades = sheetGrades();
  const i2i = UI.mode === 'i2i';
  const empty = !String(f.character || '').trim();
  const styles = sheetStyles(), lineStyles = sheetLineStyles();
  const curStyle = styles.includes(f.style) ? f.style : '';
  const custom = !curStyle;
  return `<div class="composer"><div class="composer-inner">
    <div class="parambar">
      <div class="modepick" role="group" aria-label="生成方式">
        <button class="${i2i ? '' : 'on'}" data-mode="t2i"><span class="dotg"></span>文生图</button>
        <button class="${i2i ? 'on' : ''}" data-mode="i2i"><span class="dotb"></span>图生图</button>
      </div>
      <span class="modedis">${i2i ? '拿现有角色图重画成设定图' : '纯文字描述直接出设定图'}</span>
      <span class="scope-hint"></span>
    </div>
    <div class="inputbox sheetbox" id="inputbox">
      <div class="sheetbody">
      <div class="sheetbar">
        <span class="slb">版式</span>
        <div class="seg" role="group" aria-label="版式">
          ${lib.layouts.map(l => `<button class="segb ${l.id === f.layout ? 'on' : ''}" data-sheet-layout="${esc(l.id)}" title="${esc(l.summary || '')}">${esc(l.name)}<i class="segx">${esc(l.ratio || '')}</i></button>`).join('')}
        </div>
        <span class="tiny muted">出图 ${esc((grade || {}).size || (layout || {}).size || '')}</span>
      </div>
      <div class="sheetbar">
        <span class="slb">画法</span>
        <div class="seg" role="group" aria-label="画法">
          <button class="segb ${f.ink === 'line' ? '' : 'on'}" data-sheet-ink="color">彩色</button>
          <button class="segb ${f.ink === 'line' ? 'on' : ''}" data-sheet-ink="line">线稿</button>
        </div>
        ${f.ink === 'line'
          ? `<select class="input segsel" id="sheetLine" title="线稿风格句">
              ${lineStyles.map(s => `<option value="${esc(s.id)}" ${s.id === f.lineStyle ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}
            </select><span class="warnhint">线稿别写颜色词</span>`
          : '<span class="tiny muted">彩色：风格行用下面的「风格」</span>'}
      </div>
      ${i2i ? `<div class="refs" id="refsRow">${UI.refs.map(refThumbHTML).join('')}</div>
      <div class="sheetbar">
        <span class="slb">参考</span>
        <div class="seg" role="group" aria-label="参考方式">
          <button class="segb ${f.ref === 'style' ? '' : 'on'}" data-sheet-ref="lock">锁定同角色</button>
          <button class="segb ${f.ref === 'style' ? 'on' : ''}" data-sheet-ref="style">只借画风</button>
        </div>
        <span class="tiny muted">${f.ref === 'style' ? '图 1 只借画风笔触，人物照文字新写' : '图 1 就是这个人：不换人、不改性别'}</span>
      </div>` : ''}
      <textarea class="sheetchar" id="sheetChar" rows="3" placeholder="角色描述：年龄身份 + 脸型五官 + 发型发色 + 服装分件（上衣/下装/外套/鞋）+ 2~3 个标志物">${esc(f.character)}</textarea>
      <div class="sheetbar">
        <span class="slb">档位</span>
        <div class="seg" role="group" aria-label="档位">
          ${grades.length
            ? grades.map(g => `<button class="segb ${grade && g.name === grade.name ? 'on' : ''}" data-sheet-grade="${esc(g.name)}" title="${esc(g.note || '')} · 出图 ${esc(g.size || '')}">${esc(g.name)}</button>`).join('')
            : '<span class="tiny muted">这个组合还没有档位</span>'}
        </div>
        <span class="tiny muted">${grade
          ? `${esc(grade.locked ? '默认就是这个组合的完整配方' : '与默认同提示词、同参考，只改参数')} · 出图 ${esc(grade.size || '')}`
          : ''}</span>
      </div>
      <div class="sheetbar">
        <span class="slb">风格</span>
        <select class="input segsel" id="sheetStyle" style="min-width:148px" title="12 类实测画风">
          ${styles.map(n => `<option ${n === curStyle ? 'selected' : ''}>${esc(n)}</option>`).join('')}
          <option value="" ${custom ? 'selected' : ''}>自定义…</option>
        </select>
        <input class="input segtext" id="sheetStyleText" placeholder="画种 + 材质笔触 + 色调 + 光线，一两句"
          value="${esc(f.styleText)}" ${custom ? '' : 'style="display:none"'}>
      </div>
      ${UI.sheetOpen ? `<div class="pvbox" id="sheetPv">${sheetPreviewHTML()}</div>` : ''}
      </div>
      <div class="foot">
        ${i2i ? '<button class="btn ghost sm" id="addRef">＋ 上传</button>' : '<span class="tiny muted">切到「图生图」可上传参考图</span>'}
        <button class="btn ghost sm" id="sheetPvBtn">${UI.sheetOpen ? '收起提示词' : '看完整提示词'}</button>
        ${UI.sheetOpen ? '<button class="btn ghost sm" id="sheetCopy">复制</button>' : ''}
        <span class="tiny muted" id="emptyHint" style="${empty ? '' : 'display:none'}">写点角色描述再发送</span>
        <span style="flex:1"></span><span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>
        <button class="sendbtn" id="sendBtn" title="发送" ${empty ? 'disabled' : ''}>↑</button>
      </div>
    </div>
  </div></div>`;
}

function bindSheetComposer() {
  // 只是切表单选项：重绘一次输入区即可（角色描述存在 UI.sheetForm 里，不会丢）
  const after = async (rerender = true) => {
    saveDraft();
    if (UI.sheetOpen) await refreshSheetPreview();
    else if (rerender) refreshComposer();
  };
  $$('[data-sheet-layout]').forEach(el => el.onclick = () => {
    const layout = sheetLayoutOf(el.dataset.sheetLayout);
    if (!layout) return;
    UI.sheetForm.layout = layout.id;
    // 版式只决定组合（配方），不再兼职当档位名；档位由 sheetGrade() 负责回落到合法值
    after();
  });
  $$('[data-sheet-grade]').forEach(el => el.onclick = () => {
    UI.sheetForm.grade = el.dataset.sheetGrade;
    after();
  });
  $$('[data-sheet-ink]').forEach(el => el.onclick = () => {
    UI.sheetForm.ink = el.dataset.sheetInk === 'line' ? 'line' : 'color';
    after();
  });
  $$('[data-sheet-ref]').forEach(el => el.onclick = () => {
    UI.sheetForm.ref = el.dataset.sheetRef === 'style' ? 'style' : 'lock';
    after();
  });
  const line = $('#sheetLine');
  if (line) line.onchange = () => { UI.sheetForm.lineStyle = line.value; after(); };
  const ch = $('#sheetChar');
  if (ch) {
    ch.oninput = () => {
      UI.sheetForm.character = ch.value;
      const btn = $('#sendBtn'); if (btn) btn.disabled = !ch.value.trim();
      const hint = $('#emptyHint'); if (hint) hint.style.display = ch.value.trim() ? 'none' : '';
      const box = $('#inputbox'); if (box) box.classList.remove('err');
      scheduleDraft();
    };
    ch.onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doSend(); } };
  }
  const st = $('#sheetStyle');
  if (st) st.onchange = () => {
    // 选「自定义…」→ 清掉预设，露出自由输入框；选预设 → 收起自由输入框
    UI.sheetForm.style = st.value;
    if (!st.value) UI.sheetForm.styleText = '';
    after();
  };
  const stx = $('#sheetStyleText');
  if (stx) stx.oninput = () => { UI.sheetForm.styleText = stx.value; scheduleDraft(); };
  const pv = $('#sheetPvBtn');
  if (pv) pv.onclick = async () => {
    UI.sheetOpen = !UI.sheetOpen;
    saveDraft();
    if (UI.sheetOpen) await refreshSheetPreview();
    refreshComposer();
  };
  bindSheetPreviewButtons();
}

/* ------------------------------------------------------------ 输入区 */

/* ------------------------------------------------------------ 数据辅助 */
function capStatus(cap) {
  if (!cap.workflows || !cap.workflows.length) return 'empty';
  if (cap.workflows.every(w => w.status === 'err')) return 'err';
  if (!cap.enabled) return 'off';
  if (cap.workflows.some(w => w.status === 'err')) return 'partial';
  return 'ok';
}
const capUsable = (cap) => ['ok', 'partial'].includes(capStatus(cap));
const currentCap = () => (S.capabilities || []).find(c => c.id === UI.capId) || S.capabilities[0];
function capGrades(cap) {
  if (!cap || !cap.workflows || !cap.workflows.length) return [];
  const ok = cap.workflows.find(x => x.status !== 'err' && x.grades && x.grades.length);
  return ok ? ok.grades : [];
}
// 按当前模式取子工作流名与档位（显式模式切换：文生图 / 图生图）
function wfByName(cap, name) { return (cap.workflows || []).find(w => w.name === name) || null; }
function gradesForWorkflow(cap, name) { const w = wfByName(cap, name); return (w && w.grades) || []; }

/* 模式 → 子工作流：口径与 config.yaml 的 rules 一致（文生图 = 0 张图，图生图 ≥ 1 张图）。
   为什么不写死「文生图 / 参考生图」：角色设定图的子工作流叫「设定图-文生图 / 设定图-参考生图」，
   写死会在新能力上取不到档位（页面表现为「没有档位、发不出去」）。 */
function wfForMode(cap, mode) {
  const names = (cap.workflows || []).map(w => w.name);
  if (!names.length) return null;
  const want = mode === 'i2i' ? 1 : 0;
  for (const rule of (cap.rules || [])) {
    const m = String(rule.when || '').match(/(图片)\s*(=|≥|>|≤|<)\s*(\d+)/);
    if (!m) continue;
    const op = m[2], v = +m[3];
    const hit = op === '=' ? want === v : op === '≥' ? want >= v : op === '>' ? want > v : op === '≤' ? want <= v : want < v;
    if (hit && names.includes(rule.wf)) return rule.wf;
  }
  const byName = mode === 'i2i'
    ? names.find(n => /参考|编辑|图生/.test(n))
    : names.find(n => /文生/.test(n));
  return byName || names[0];
}
const isSheetCap = (cap) => !!cap && cap.id === 'sheet.character';
function currentWorkflowName(cap) { return wfForMode(cap, UI.mode); }
function currentGrades(cap) { return gradesForWorkflow(cap, currentWorkflowName(cap)); }

/* ---------- 行内图片引用编辑器（contenteditable + chip） ----------
   提示词文本里用 <imageN> 令牌指代第 N 张参考图（与 ComfyUI 里 TextEncodeQwenImage21 的
   images.image_N 输入、以及 Qwen-Image 2.1 提示词规范一致）。编辑器只在界面上把令牌渲染成
   带缩略图的小 chip；真正提交时才序列化回 <imageN>。N = 该参考图在当前 UI.refs 中的序号 +1。 */
const IMAGE_TOKEN = /<image(\d+)>/g;
function refChipHTML(id, slot) {
  return `<span class="refchip" contenteditable="false" data-ref="${id}" title="第 ${slot} 张参考图"><img src="${refUrl(id)}" alt="参考图${slot}"><b>图片 ${slot}</b><span class="x" data-rmchip title="移除引用">✕</span></span>`;
}
function editorInnerHTML(text) {
  // 把纯文本里的 <imageN> 还原成可编辑的缩略图 chip
  const parts = (text || '').split(/(<image\d+>)/g);
  return parts.map(p => {
    const m = p.match(/^<image(\d+)>$/);
    if (m) {
      const slot = +m[1], ref = UI.refs[slot - 1];
      return ref ? refChipHTML(ref.id, slot)
        : `<span class="refchip miss" contenteditable="false" data-miss="1" data-token="${p}" title="第 ${slot} 张参考图（已移除）"><b>图片 ${slot}</b><span class="x" data-rmchip>✕</span></span>`;
    }
    return esc(p).replace(/\n/g, '<br>');
  }).join('');
}
function serializeEditor() {
  const el = $('#promptInput');
  if (!el) return UI.prompt || '';
  let out = '';
  el.childNodes.forEach(n => {
    if (n.nodeType === 3) out += n.textContent;                 // 文本节点
    else if (n.nodeType === 1 && n.classList.contains('refchip')) {
      const id = n.dataset.ref, idx = UI.refs.findIndex(r => r.id === id);
      out += idx >= 0 ? `<image${idx + 1}>` : '';               // 顺序随 UI.refs 实时计算
    }
  });
  return out;
}
function onEditorInput() {
  UI.prompt = serializeEditor();
  const empty = !UI.prompt.trim();
  const btn = $('#sendBtn'); if (btn) btn.disabled = empty;
  const hint = $('#emptyHint'); if (hint) hint.style.display = empty ? '' : 'none';
  const box = $('#inputbox'); if (box) box.classList.remove('err');
  saveDraft();
}
async function insertRefChip(refId) {
  const el = $('#promptInput'); if (!el) return;
  el.focus();
  const slot = (UI.refs.findIndex(r => r.id === refId) + 1) || UI.refs.length || 1;
  const chip = document.createElement('span');
  chip.className = 'refchip'; chip.contentEditable = 'false'; chip.dataset.ref = refId;
  chip.innerHTML = `<img src="${refUrl(refId)}" alt="参考图${slot}"><b>图片 ${slot}</b><span class="x" data-rmchip title="移除引用">✕</span>`;
  const sel = window.getSelection();
  let range;
  if (sel && sel.rangeCount && el.contains(sel.anchorNode)) {
    range = sel.getRangeAt(0); range.deleteContents();
  } else {
    range = document.createRange(); range.selectNodeContents(el); range.collapse(false);
  }
  range.insertNode(chip);
  const space = document.createTextNode(' '); chip.after(space);
  const r2 = document.createRange(); r2.setStart(space, 1); r2.collapse(true);
  sel.removeAllRanges(); sel.addRange(r2);
  onEditorInput();
}

function gradeParams(wf, g) {
  // 母版也有自己的参数（版式画幅），所以不能因为 locked 就忽略 params
  return { ...((wf && wf.original) || {}), ...((g && g.params) || {}) };
}
function gradeDetail(g, fields, wf) {
  if (!g) return '';
  const vals = gradeParams(wf, g);
  const fs = (fields && fields.length) ? fields : Object.keys(vals).map(k => ({ field: k }));
  const used = [], parts = [];
  fs.forEach(f => {
    if (used.includes(f.field)) return;
    const v = vals[f.field];
    if (v === '' || v === undefined || v === null) return;
    if (f.field === 'width' && vals.height !== undefined) { used.push('height'); parts.push(v + '×' + vals.height); return; }
    used.push(f.field); parts.push(fieldLabel(f.field) + ' ' + (Array.isArray(v) ? v.join('/') : v));
  });
  return parts.join(' · ');
}
function fieldOf(name) { return name.split('.').pop(); }
const FIELD_LABELS = {
  width: '宽度', height: '高度', batch_size: '张数', steps: '步数', cfg: 'CFG', denoise: '改写幅度',
  sampler_name: '采样器', scheduler: '调度器', seed: '种子', noise_seed: '种子', resolution: '分辨率',
  megapixels: '百万像素', multiple: '倍数', aspect_ratio: '宽高比', filename_prefix: '文件名前缀',
  format: '格式', bit_depth: '位深度', input_color_space: '色彩空间', prompt: '提示词',
  negative_prompt: '负面词', text: '文本', cfg_: 'CFG', sigmas: '采样节奏',
};
const fieldLabel = (f) => FIELD_LABELS[fieldOf(f)] || (f.includes('.') ? f.split('.').pop() : f);

function fieldMeta(cls, field) {
  const base = field.split('.')[0];
  const info = (((S.schema || {})[cls] || {}).fields || {})[base];
  if (info) return info;
  return { type: 'text', options: null, min: null, max: null, step: null };
}
function controlOf(cls, field) {
  const t = fieldMeta(cls, field).type;
  if (t === 'bool') return 'switch';
  if (t === 'combo') return 'select';
  if (t === 'multi') return 'multi';
  if (t === 'int' || t === 'float') return 'number';
  if (t === 'dynamic') return 'text';
  return field.includes('prompt') || field.includes('text') ? 'textarea' : 'text';
}
const CONTROL_LABEL = { number: '输入框（数字）', text: '输入框（文字）', textarea: '输入框（多行）', switch: '开关', select: '选择菜单', multi: '多选' };
function wfLib(name) { return (S.wfLib || {})[name] || { list: [], path: '' }; }
function paramNodes(name) { return wfLib(name).list.filter(n => n.role === 'param' && (n.w || []).length); }
function excludedNodes(name) { return wfLib(name).list.filter(n => n.role !== 'param'); }
function nodeFields(nodeId, name) { const n = wfLib(name).list.find(x => x.id === String(nodeId)); return n && n.role === 'param' ? (n.w || []).slice() : []; }
function pickedNodeIds(wf) {
  const ids = [];
  ((wf && wf.fields) || []).forEach(f => { if (f.node && !ids.includes(String(f.node))) ids.push(String(f.node)); });
  return ids;
}
function pickWorkflow(cap, refs) {
  const n = refs.length;
  for (const rule of (cap.rules || [])) {
    const m = String(rule.when).match(/(图片)\s*(=|≥|>|≤|<)\s*(\d+)/);
    if (!m || !cap.workflows.includes(rule.wf)) continue;
    const op = m[2], v = +m[3];
    const hit = op === '=' ? n === v : op === '≥' ? n >= v : op === '>' ? n > v : op === '≤' ? n <= v : n < v;
    if (hit) return { wf: rule.wf };
  }
  return { wf: (cap.workflows || [])[0] || null };
}
function needsRef(cap) { return false; }                 // 1.01 图片生成：参考图可选
function placeholderFor(cap) {
  const id = (cap || currentCap() || {}).id;
  const m = {
    'image.generate': '描述你想要的画面，例如：雨夜霓虹街道，赛博朋克，湿地面反光',
    'image.reference': '描述想要的画风，并上传参考图',
    'sheet.character': '角色描述：年龄身份 + 脸型五官 + 发型发色 + 服装分件 + 2~3 个标志物',
    'board.9': '一句话说清故事，例如：小机器人找猫，9 格分镜',
    'audio.tts': '把要读的文字粘进来',
    'audio.music': '描述想要什么音乐，例如：低沉的钢琴配雨声',
  };
  return m[id] || '描述你想生成的内容…';
}
function pickHintFor(cap) { return UI.refs.length === 0 ? '有参考图就拖进来，这次按它来改' : '拿这张图来改'; }
function samplePrompts(cap) {
  const id = (cap || {}).id;
  const m = {
    'image.generate': ['雨夜霓虹街道，赛博朋克，竖版', '一只橘猫坐在窗台上，午后阳光', '水墨山水，远山淡雾'],
    'sheet.character': [
      '二十五岁中国女子，清冷气质，长直黑发用竹簪高盘，细眉凤眼，穿素白交领长衫外罩月白薄纱披风，腰系青色织带，青玉玉佩',
      '三十岁独臂刀客，断眉，左臂旧铁皮义肢，灰麻短打束腰布带，背后一把无鞘长刀',
      '赛博机械维修师，紫色短发，护目镜推在额头上，黄色工装背心，机械右手，胸口编号 K-07',
    ],
    'video.firstlast': ['从白天到黑夜的延时摄影', '镜头从远推近，人物回头'],
    'board.9': ['小机器人找猫的 9 格分镜', '雨夜追凶，冷色调 9 格分镜'],
    'audio.music': ['低沉的钢琴配雨声，慢速', '轻快的电子乐'],
  };
  return m[id] || ['描述你想生成的内容'];
}
function groupedCaps() {
  const groups = {};
  (S.capabilities || []).forEach(c => { (groups[c.group] = groups[c.group] || []).push(c); });
  return groups;
}
const GROUP_ORDER = ['图像', '设定图', '故事板', '视频', '音频'];
function orderedGroups() {
  const g = groupedCaps();
  return [...GROUP_ORDER.filter(k => g[k]), ...Object.keys(g).filter(k => !GROUP_ORDER.includes(k))].map(k => ({ name: k, caps: g[k] }));
}
function jobRuns(j) { return (j && j.runs) || []; }
// 历史里删掉的成品（文件已删）不该继续在对话卡片里显示破图
function doneRuns(j) { return jobRuns(j).filter(r => r.state === 'done' && !r.hidden && (r.files || []).some(f => !f.deleted)); }
const chatJobs = () => (S.jobs || []).filter(j => !j.sessionRemoved);
function pendRuns(j) { return jobRuns(j).filter(r => r.state === 'queued' || r.state === 'running'); }
function allRuns() { return (S.jobs || []).flatMap(j => (j.runs || []).map(r => ({ j, r }))); }
function runningEntry() { return allRuns().find(x => x.r.state === 'running') || null; }
function queuedEntries() { return allRuns().filter(x => x.r.state === 'queued').sort((a, b) => (a.r.seq || 0) - (b.r.seq || 0)); }
function queueAhead(run) { return allRuns().filter(x => (x.r.state === 'running' || x.r.state === 'queued') && (x.r.seq || 0) < (run.seq || 0)).length; }
function runSizeText(_j, r) { return r.size || r.grade || '默认'; }
function archivePathFor(_cap, run) { return (run && run.files && run.files[0] && run.files[0].path) || ''; }
function busyText(x) { return `「${x.j.capName} · ${x.r.grade || '默认'}」（${Math.round(x.r.pct || 0)}%）`; }
function imgUrl(runId, index) { return `/api/media?run=${encodeURIComponent(runId)}&i=${index}`; }
function refUrl(refId) { return `/api/media?ref=${encodeURIComponent(refId)}`; }

/* ------------------------------------------------------------ 通用渲染 */
function render() {
  const root = $('#root');
  syncHash();
  if (UI.route === 'studio') root.innerHTML = viewStudio();
  else if (UI.route === 'admin') root.innerHTML = viewAdmin();
  else root.innerHTML = viewGallery();
  bindGlobal();
  if (UI.route === 'studio') { bindStudio(); if (UI.autoScroll) { scrollChatBottom(); UI.autoScroll = false; } }
  if (UI.route === 'admin') bindAdmin();
  if (UI.route === 'gallery') bindGallery();
  // 初次使用引导只在还没有任何成品时出现
  if (!UI.wizardSeen && UI.route === 'studio' && !(S.jobs || []).length) renderWizard();
  if (UI.route === 'admin' && UI.adminPage === 'diag') autoDiag();
  // 组合页有自己的「生效」核对（按组合 id 拉 /api/sheet/combos），旧版按工作流档位查，别混用
  if (UI.route === 'admin' && UI.adminPage === 'grades' && !isSheetCap(currentCap())) showGradeEffective();
  _lastSignature = stateSignature();
}

function paintProgress() {
  // 只更新数字和进度条，不重建整块 DOM：多窗口/刷新都靠服务端状态，画面不闪
  if (!S || UI.route !== 'studio') return;
  (S.jobs || []).forEach(j => (j.runs || []).forEach(r => {
    const pct = Math.round(r.pct || 0), total = r.total || 8;
    $$(`[data-jobbar="${r.id}"]`).forEach(e => e.style.width = pct + '%');
    $$(`[data-jobpct="${r.id}"]`).forEach(e => e.textContent = pct + '%');
    $$(`[data-jobstep="${r.id}"]`).forEach(e => e.textContent = r.state === 'queued'
      ? 'ComfyUI 马上开始' : `正在采样 · ${Math.round(pct * total / 100)}/${total} 步`);
    $$(`[data-jobelapsed="${r.id}"]`).forEach(e => e.textContent = r.state === 'queued'
      ? '等待显卡空闲' : `已用 ${elapsed(r.t0)}`);
    $$(`[data-runbar="${r.id}"]`).forEach(e => e.style.width = pct + '%');
    $$(`[data-runpct="${r.id}"]`).forEach(e => e.textContent = pct + '%');
    $$(`[data-runstep="${r.id}"]`).forEach(e => e.textContent = r.state === 'running'
      ? '正在采样…' : '排队中 · 前面还有 ' + queueAhead(r) + ' 个');
  }));
  const running = runningEntry(), dr = $('[data-drawerpct]');
  if (dr) dr.textContent = running ? Math.round(running.r.pct || 0) + '%' : '';
}

function refreshDynamic() {
  // 配置红条出现/消失要整页重绘（横幅在页面骨架里，局部刷新插不进去）
  if (!!S.configError !== !!document.querySelector('.configerr')) { render(); return; }
  // 只重绘会变的区域，输入区保持焦点与已输入的内容（需求 #12）
  if (UI.route === 'studio') {
    const chat = $('#chatInner');
    if (chat) { chat.innerHTML = viewChat(); bindStudio(); }
    const drawer = $('#drawerBody'); if (drawer) { drawer.innerHTML = drawerInner(); bindDrawer(); }
    const top = $('.topbar'); if (top) top.outerHTML = viewTopbar(); bindGlobal();
    const comp = $('.composer');
    if (comp) {
      const focused = document.activeElement && document.activeElement.id === 'promptInput';
      if (focused) UI.prompt = serializeEditor();
      comp.outerHTML = viewComposer(); bindStudio();
      if (focused) { const t2 = $('#promptInput'); if (t2) t2.focus(); }
    }
  } else if (UI.route === 'admin') {
    const top = $('.topbar'); if (top) { top.outerHTML = viewTopbar(); bindGlobal(); }
  }
  _lastSignature = stateSignature();
}

/* 顶栏图标：手机上改用线性 SVG（emoji 在小尺寸下糊成一团，也认不出是什么），
   PC 上仍用原来的字符 —— 这一轮只动移动端。 */
const ICO_SVG = {
  gallery: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="3" y="4.5" width="18" height="15" rx="3"/><circle cx="8.4" cy="9.6" r="1.6"/>'
    + '<path d="M3.6 17.4l4.5-4.1a1.7 1.7 0 0 1 2.3 0l3.4 3.1"/><path d="M13.6 14.4l2.5-2.3a1.7 1.7 0 0 1 2.3 0l2 1.8"/></svg>',
  tasks: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M4 6.5h16"/><path d="M4 12h9.5"/><path d="M4 17.5h6.5"/><circle cx="18" cy="17.5" r="3.1"/><path d="M18 16v1.6l1.2.8"/></svg>',
};
function viewTopbar() {
  const on = UI.comfyOnline;
  const running = runningEntry();
  // 手机上只看状态（空闲 / 生成中），描述性文字留给抽屉里的那一块
  const statusWord = running ? '生成中' : '空闲';
  return `<div class="topbar">
    <div class="brand"><i></i><span class="bname">ComfyBridge</span></div>
    <div class="health ${on ? '' : 'off'}" id="healthBtn" title="重新检测">
      <span class="dot ${on ? 'g' : 'r'} pulse"></span>
      <span class="txt">${on ? '已连接' : 'ComfyUI 未连接，请先启动'}</span>
      <span class="txt-short">${on ? '已连接' : '未连接'}</span>
      <span class="hstat ${running ? 'busy' : ''}"><i class="hsd"></i>${esc(on ? statusWord : '离线')}</span>
      <span class="mono muted hurl">${esc(S.service.comfy.replace('http://', ''))}</span>
    </div>
    <div class="spacer"></div>
    ${(UI.route === 'studio' || UI.route === 'admin') ? '<button class="btn ghost sm navbtn" id="navToggle" title="菜单"><span class="ico">☰</span></button>' : ''}
    ${UI.route === 'studio' ? `<button class="btn ghost sm sheetbtn" id="sheetToggle" title="ComfyUI 状态与本会话任务"><span class="ico dico">☰</span><span class="ico mico">${ICO_SVG.tasks}</span><span class="lbl">任务</span></button>` : ''}
    ${UI.route === 'gallery'
      ? '<button class="btn ghost sm" data-nav="studio"><span class="ico">←</span><span class="lbl">返回创作台</span></button>'
      : `<button class="btn ghost sm" data-nav="gallery"><span class="ico dico">🗂</span><span class="ico mico">${ICO_SVG.gallery}</span><span class="lbl">历史</span></button>`}
    <button class="btn sm" data-nav="${UI.route === 'admin' ? 'studio' : 'admin'}"><span class="ico">${UI.route === 'admin' ? '←' : '⚙'}</span><span class="lbl">${UI.route === 'admin' ? '返回创作台' : '管理台'}</span></button>
  </div>`;
}

/* ------------------------------------------------------------ 创作台 */
function configBanner() {
  // 配置被手改坏时：顶部红条指出问题，并允许在页面上直接改（需求 04 章）
  if (!S || !S.configError) return '';
  return `<div class="configerr"><span class="msg"><b>配置有问题：</b>${esc(S.configError)}</span>
    <button class="btn sm danger" id="fixConfig">在页面上修改配置</button></div>`;
}
function viewStudio() {
  // sheet-open 控制手机端的「状态与任务 → 右侧抽屉」；nav-open 控制手机端的「能力 → 左侧抽屉」
  return `<div class="app ${UI.sheet ? 'sheet-open' : ''} ${UI.navOpen ? 'nav-open' : ''}">
    ${viewTopbar()}
    ${configBanner()}
    <div class="studio">
      ${viewRail()}
      <div class="chat">
        <div class="chat-scroll" id="chatScroll"><div class="chat-inner" id="chatInner">${viewChat()}</div></div>
        ${viewComposer()}
      </div>
      ${viewDrawer()}
    </div>
    <div class="side-mask" id="sideMask"></div>
    <div class="sheet-mask" id="sheetMask"></div>
  </div>`;
}
function viewRail() {
  let html = '<div class="rail"><div class="side-hd"><b>选择能力</b><button class="side-x" data-side-close title="收起">✕</button></div>', hasAny = false;
  orderedGroups().forEach(({ name: grp, caps }) => {
    html += `<div class="group">${grp}</div>`;
    caps.forEach(c => {
      // 已配置但停用：从创作台消失；未配置：灰着显示、点不动（需求 §2.1 / X45）
      if ((c.workflows || []).length && !c.enabled) return;
      const usable = capUsable(c) && c.enabled;
      if (usable) hasAny = true;
      html += `<div class="item ${c.id === UI.capId ? 'on' : ''} ${usable ? '' : 'dis'}" data-cap="${c.id}" title="${esc(c.desc || '')}">
        <span class="em">${c.em}</span><span class="nm">${esc(c.name)}</span>
        ${usable ? '' : '<span class="tag">未配置（下个版本开放）</span>'}
      </div>`;
    });
  });
  if (!hasAny) html += '<div class="tip">当前还没有可用能力</div>';
  html += '</div>';
  return html;
}
function viewChat() {
  const mine = chatJobs().filter(j => j.cap === UI.capId);
  if (!mine.length) return viewEmptyChat();
  return mine.map(j => `<div class="jobwrap">${jobCard(j)}
    <button class="jobdel" data-jobdel="${j.id}" title="删除这条对话（不删成品文件）">✕</button></div>`).join('');
}
function viewEmptyChat() {
  const cap = currentCap(), usable = cap && capUsable(cap) && cap.enabled;
  if (!usable) return `<div class="empty"><div style="font-size:34px">🧩</div>
    <div><b style="font-size:15px;color:#45505f">这个能力还没有配置工作流</b></div>
    <div class="tiny">去管理台看看</div><button class="btn primary" data-nav="admin">去管理台配置</button></div>`;
  return `<div class="empty" style="padding-top:36px"><div style="font-size:34px">✨</div>
    <div><b style="font-size:15px;color:#45505f">${esc(cap.em)} ${esc(cap.name)}</b></div>
    <div class="tiny" style="max-width:420px">${esc(cap.desc || '描述你想要的画面')}</div>
    <div class="row" style="flex-wrap:wrap;justify-content:center;max-width:520px;margin-top:4px">
      ${samplePrompts(cap).map(p => `<button class="chip" data-sample="${esc(p)}">${esc(p)}</button>`).join('')}</div></div>`;
}

function jobWfLabel(j) {
  // 设定图显示「组合名」（版式·画法·参考）；档位另有 chip，不重复摆工作流名
  return (j.sheet && j.sheet.comboName) || j.wf || '';
}

function jobCard(j) {
  const runs = jobRuns(j), base = runs[0];
  if (!base) return '';
  // 设定图：卡片上只放一句摘要（brief），完整提示词点「看完整提示词」再看
  const briefText = j.brief || j.prompt;
  const promptBox = `<div class="msg-u" style="background:#f6f8fb;color:#45505f;box-shadow:none;max-width:none;margin-bottom:10px">${esc(briefText)}</div>
    ${j.brief ? `<div class="promptmeta"><button class="linkbtn" data-seefull="${j.id}">看完整提示词</button>
      <span class="tiny muted">${esc(jobWfLabel(j))}${j.sheet ? ` · ${esc(j.sheet.ink === 'line' ? '线稿' : '彩色')}${j.sheet.ref === 'lock' ? ' · 锁同角色' : j.sheet.ref === 'style' ? ' · 借画风' : ''}` : ''} · ${String((j.prompt || '').length)} 字</span></div>` : ''}`;
  if (base.state === 'queued' || base.state === 'running') {
    const pct = Math.round(base.pct || 0);
    const step = Math.round(pct * (base.total || 8) / 100);
    return `<div class="job" id="${j.id}">
      <div class="head"><span class="dot a pulse"></span><b>${base.state === 'queued' ? '等待执行' : '生成中'}</b>
        <span class="wf">· ${esc(jobWfLabel(j))}</span><span class="gradechip">${esc(base.grade || '默认')}</span>
        <span style="flex:1"></span><span class="tiny muted">${fmtTime(j.t0)}</span></div>
      <div class="body">${promptBox}
        <div class="tiny muted" data-jobstep="${base.id}">${base.state === 'queued' ? 'ComfyUI 马上开始' : `正在采样 · ${step}/${base.total || 8} 步`}</div>
        <div class="progress"><i data-jobbar="${base.id}" style="width:${pct}%"></i></div>
        <div class="spread tiny muted"><span data-jobelapsed="${base.id}">${base.state === 'queued' ? '等待显卡空闲' : `已用 ${elapsed(base.t0)}`}</span><b style="color:#2f6bff" data-jobpct="${base.id}">${pct}%</b></div>
      </div>
      <div class="foot"><span class="grow">${base.state === 'running' ? '生成中' : '排队中'}</span>
        <button class="btn sm" data-cancel="${base.id}">取消</button></div></div>`;
  }
  if (base.state === 'canceled') {
    return `<div class="job" id="${j.id}"><div class="head"><span class="dot"></span><b>已取消</b><span class="wf">· ${esc(jobWfLabel(j))}</span></div>
      <div class="body">${promptBox}</div></div>`;
  }
  if (base.state === 'failed') {
    const e = base.err || {};
    return `<div class="job fail" id="${j.id}">
      <div class="head"><span class="dot r"></span><b>失败</b><span class="wf">· ${esc(jobWfLabel(j))}</span>
        <span class="gradechip">${esc(base.grade || '默认')}</span><span style="flex:1"></span>
        <span class="tiny muted">${esc(e.retriable ? '可以再试一次' : '')}</span></div>
      <div class="body" style="padding-bottom:0">${promptBox}</div>
      <div class="errbox" id="err-${j.id}"><div>${esc(e.msg || '')}</div><div class="raw">${esc(e.raw || '')}</div>
        ${base.archive_failed ? `<div style="color:#8a5a00">归档失败：${esc(base.archive_failed.reason)}</div>` : ''}</div>
      <div class="foot"><span class="grow"></span><button class="btn sm" data-raw="${j.id}">看原始报错</button>
        <button class="btn sm primary" data-retry="${j.id}">重试一次</button></div></div>`;
  }
  const ds = doneRuns(j), ps = pendRuns(j), fs = runs.filter(r => r.state === 'failed');
  const n = ds.length + ps.length, cols = n <= 1 ? 1 : n === 2 ? 2 : 3;
  const grades = [...new Set(runs.filter(r => r.state !== 'canceled').map(r => r.grade || '默认'))];
  const running = ps.find(r => r.state === 'running');
  const lastT = Math.max(j.t0 || 0, ...runs.filter(r => r.t1).map(r => r.t1));
  return `<div class="job done" id="${j.id}">
    <div class="head"><span class="dot g"></span><b>已完成</b><span class="wf">· ${esc(jobWfLabel(j))}</span>
      <span class="gradechip">${esc(grades.join(' / '))}</span><span style="flex:1"></span>
      ${ps.length ? `<span class="qchip"><span class="dot a pulse"></span>队列 ${ps.length} 个${running ? ` · 正在出成品 ${ds.length + 1}` : ''}</span>` : ''}
      <span class="tiny muted">用时 ${Math.round((lastT - (j.t0 || lastT)) / 1000)} 秒</span></div>
    <div class="body">${promptBox}
      <div class="grid tiles" style="grid-template-columns:repeat(${cols},minmax(0,1fr));max-width:${cols * 250 + (cols - 1) * 9}px">
        ${ds.map((r, i) => {
          const badge = (r.grade && r.grade !== base.grade) ? `<span class="badge">${esc(r.grade)}</span>` : '';
          return `<div class="tile"><img src="${imgUrl(r.id, 0)}" alt="成品 ${i + 1}" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain">${badge}
            <div class="acts"><button title="看大图" data-zoom="${j.id}|${r.id}">⤢</button>
            <button title="下载" data-dl="${r.id}">⬇</button>
            <button title="复制图片（可直接粘贴到输入框当参考图）" data-copyimg="${r.id}">⧉</button></div>
            <div class="tile-size">${esc(runSizeText(j, r))}</div></div>`;
        }).join('')}
        ${ps.map((r, k) => `<div class="tile q" data-runprog="${r.id}">
          <button class="qx" title="撤掉这一档" data-qcancel="${r.id}">✕</button>
          <div class="qwrap"><div class="qlb">成品 ${ds.length + k + 1}</div>
          <div class="progress"><i data-runbar="${r.id}" style="width:${Math.round(r.pct || 0)}%"></i></div>
          <div class="tiny muted" data-runstep="${r.id}">${r.state === 'running' ? '正在采样…' : '排队中 · 前面还有 ' + queueAhead(r) + ' 个'}</div>
          <div class="qpct" data-runpct="${r.id}">${Math.round(r.pct || 0)}%</div></div></div>`).join('')}
      </div>
      ${fs.length ? `<div class="jobfail">${fs.map(r => `<div>有一条队列任务没跑成（${esc(r.grade || '默认')}）：${esc((r.err || {}).msg || '未知错误')}</div>`).join('')}</div>` : ''}
      ${ds.some(r => r.archive_failed) ? `<div class="tiny" style="color:#8a5a00;margin-top:6px">归档失败，原路径：${esc(ds.find(r => r.archive_failed).archive_failed.original)}</div>` : ''}
    </div>
    <div class="foot"><span class="grow seedline"><i class="sdot"></i>种子 <b class="mono">${j.seed ?? base.seed ?? ''}</b></span>
      <button class="btn sm qbtn" data-qadd="${j.id}" title="挑一档排到这批后面，再出一张">🕒 加入队列生成</button>
      <button class="btn sm" data-again="${j.id}">↻ 再来一张</button>
      <button class="btn sm ghost" data-refine="${j.id}" title="把上一条的描述和档位带过来">↗ 基于上一条</button>
    </div></div>`;
}
const fmtTime = (t) => t ? new Date(t).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
const elapsed = (t) => { const s = Math.max(0, Math.floor((Date.now() - (t || Date.now())) / 1000)); return s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`; };

/* ------------------------------------------------------------ 输入区 */
function refThumbHTML(r) {
  const idx = UI.refs.findIndex(x => x.id === r.id);
  return `<div class="ref-thumb">
    <img src="${refUrl(r.id)}" alt="参考图${idx + 1}">
    <button class="at" data-refat="${r.id}" title="引用到文字里（@）" aria-label="引用到文字">@</button>
    <button class="x" data-rmref="${r.id}" title="移除这张参考图">✕</button></div>`;
}
function viewComposer() {
  const cap = currentCap(), usable = cap && capUsable(cap) && cap.enabled;
  if (!usable) return `<div class="composer"><div class="composer-inner">
    <div class="inputbox" style="border-color:var(--line);box-shadow:none;background:#fafbfe">
      <div class="tiny muted" style="padding:18px 4px;text-align:center">这个能力还没配好档位，暂时不能发送。</div></div></div></div>`;
  // 角色设定图有自己的表单（版式 / 画法 / 参考方式 / 角色行 / 风格）
  if (isSheetCap(cap)) return viewSheetComposer(cap);
  const grades = currentGrades(cap), curGrade = grades.find(g => g.name === UI.params.grade) || grades[0] || null;
  const i2i = UI.mode === 'i2i';
  const empty = !(UI.prompt || '').trim();
  const modeBtn = (m, label, dot) =>
    `<button class="${UI.mode === m ? 'on' : ''}" data-mode="${m}"><span class="${dot}"></span>${label}</button>`;
  return `<div class="composer"><div class="composer-inner">
    <div class="parambar">
      <div class="modepick" role="group" aria-label="生成模式">
        ${modeBtn('t2i', '文生图', 'dotg')}
        ${modeBtn('i2i', '图生图', 'dotb')}
      </div>
      <span class="modedis">${i2i ? '已开启参考图，可上传' : '纯文字描述，不上传图片'}</span>
      ${grades.length ? `<div class="grades" role="group" aria-label="画质档">
        ${grades.map(g => `<button class="grade ${curGrade && g.name === curGrade.name ? 'on' : ''}" data-grade="${esc(g.name)}" title="${esc(g.note || '')} · 出图 ${esc((g.effective || {}).size || '由工作流决定')}">${esc(g.name)}</button>`).join('')}
      </div><span class="grade-note">${esc((curGrade || {}).note || '')}${(curGrade || {}).effective ? ` · 出图 ${esc(curGrade.effective.size)}` : ''}</span>` : ''}
      <span class="scope-hint"></span>
    </div>
    <div class="inputbox" id="inputbox">
      <div class="sheetbody">
      ${i2i ? `<div class="refs" id="refsRow">${UI.refs.map(refThumbHTML).join('')}</div>` : ''}
      <div class="editor" id="promptInput" contenteditable="true" data-ph="${esc(placeholderFor(cap))}">${editorInnerHTML(UI.prompt)}</div>
      </div>
      <div class="foot">
        ${i2i
          ? `<button class="btn ghost sm" id="addRef">＋ 上传</button>`
          : `<span class="tiny muted">切到「图生图」可上传参考图</span>`}
        <span class="tiny muted" id="pickHint">${esc(i2i ? pickHintFor(cap) : '纯文字生图，无需参考图')}</span>
        <span class="tiny muted" id="emptyHint" style="${empty ? '' : 'display:none'}">写点描述再发送</span>
        <span style="flex:1"></span><span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>
        <button class="sendbtn" id="sendBtn" title="发送" ${empty ? 'disabled' : ''}>↑</button>
      </div>
    </div>
  </div></div>`;
}
function viewDrawer() {
  // 手机上这是底部抽屉：给一个看得见的标题 + 关闭按钮，别让人只能靠点遮罩
  return `<div class="drawer">
    <div class="sheet-hd"><b>状态与任务</b><button class="sheet-x" data-sheet-close title="收起">✕</button></div>
    <div id="drawerBody">${drawerInner()}</div></div>`;
}
function drawerInner() {
  const running = runningEntry(), queued = queuedEntries();
  // 会话任务删除后不再出现在右栏；成品文件仍在，历史页照样能管理
  const doneJobs = chatJobs().filter(j => doneRuns(j).length);
  const failJobs = chatJobs().filter(j => jobRuns(j)[0] && jobRuns(j)[0].state === 'failed');
  return `<h4>ComfyUI</h4>
    <div class="cstate ${running ? 'busy' : ''}">
      <span class="dot ${running ? 'a pulse' : 'g'}"></span>
      <div class="t"><b>${running ? '正在跑「' + esc(running.r.grade || '默认') + '」' : '空闲'}</b>
        <span>${running ? `${esc(running.j.capName)} · <i data-drawerpct="${running.r.id}">${Math.round(running.r.pct || 0)}%</i>` : '可以接新的生成'}</span></div>
    </div>
    ${queued.length ? `<h4 style="margin-top:14px">执行队列 ${queued.length} 个</h4>
      ${queued.map((x, i) => `<div class="q-item"><span class="qn">${i + 1}</span>
        <div class="t"><b>${esc(x.r.grade || '默认')}</b><span>${esc(x.j.capName)} · ${esc(x.r.wf)}</span></div></div>`).join('')}` : ''}
    <div class="task-head"><h4>本会话任务</h4>
      <label class="seltasks" title="全选"><input type="checkbox" id="tall" ${doneJobs.length && UI.selTasks.size === doneJobs.length ? 'checked' : ''}><span class="lbl">全选</span></label>
      <button class="task-del" id="tbatch" title="批量删除" ${UI.selTasks.size ? '' : 'disabled'}>
        <span class="lbl">批量删除（${UI.selTasks.size}）</span>
        <span class="mic">🗑${UI.selTasks.size ? `<i>${UI.selTasks.size}</i>` : ''}</span></button></div>
    ${doneJobs.length ? doneJobs.map(j => `<div class="task-item ${UI.selTasks.has(j.id) ? 'sel' : ''}" data-jump="${j.id}">
      <input type="checkbox" class="tsel" data-tsel="${j.id}" ${UI.selTasks.has(j.id) ? 'checked' : ''}>
      <span class="dot g"></span><div class="t"><b>${esc(j.capName)}</b><span>${doneRuns(j).length} 个成品 · ${esc(j.wf)}</span></div>
      <button class="tdel" data-jobdel="${j.id}" title="删除这条对话">✕</button></div>`).join('')
      : '<div class="tiny muted" style="padding:0 8px">还没有成品</div>'}
    ${failJobs.length ? `<h4 style="margin-top:14px">失败 ${failJobs.length}</h4>${failJobs.map(j => `<div class="task-item" data-jump="${j.id}">
      <span class="dot r"></span><div class="t"><b>${esc(j.capName)}</b><span>${esc(((jobRuns(j)[0].err) || {}).short || '没跑成')}</span></div>
      <button class="tdel" data-jobdel="${j.id}" title="删除这条对话">✕</button></div>`).join('')}` : ''}
    <div class="hr"></div>
    <div class="tiny muted" style="padding:0 8px;line-height:1.7">成品都存放在：<br><span class="mono" style="word-break:break-all">${esc(S.service.archiveDir)}</span></div>
    <div class="tiny muted" style="padding:8px;line-height:1.7">删对话只移记录、不删文件；要删文件去「历史记录」。</div>`;
}
function bindDrawer() {
  $$('[data-jump]').forEach(el => el.onclick = () => {
    UI.sheet = false;                                  // 手机端：从抽屉跳过去先把抽屉收起来，否则看不到目标卡片
    const app = $('.app'); if (app) app.classList.remove('sheet-open');
    const node = document.getElementById(el.dataset.jump);
    if (node) { node.scrollIntoView({ block: 'center', behavior: 'smooth' }); node.style.outline = '2px solid #2f6bff'; setTimeout(() => node.style.outline = 'none', 1200); }
  });
  // 本会话任务：批量删除
  const tall = $('#tall');
  if (tall) tall.onchange = () => {
    $$('[data-tsel]').forEach(el => { if (tall.checked) UI.selTasks.add(el.dataset.tsel); else UI.selTasks.delete(el.dataset.tsel); });
    refreshDrawer();
  };
  $$('[data-tsel]').forEach(el => {
    el.onclick = e => e.stopPropagation();           // 勾选不要触发跳转
    el.onchange = () => { if (el.checked) UI.selTasks.add(el.dataset.tsel); else UI.selTasks.delete(el.dataset.tsel); refreshDrawer(); };
  });
  const tbatch = $('#tbatch');
  if (tbatch) tbatch.onclick = async () => {
    if (!UI.selTasks.size) { toast('先勾选要删除的对话'); return; }
    const n = UI.selTasks.size;
    openModal(`<div class="m-head"><h3>批量删除选中的 ${n} 条对话？</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><div class="tiny" style="line-height:1.9">只移走对话记录，<b>成品文件不删除</b>，仍在「历史记录」里。<br>正在跑的任务会被跳过。</div></div>
      <div class="m-foot"><button class="btn sm" data-close>取消</button>
        <button class="btn sm danger" id="confirmBatchDel">确认删除</button></div>`);
    $('#confirmBatchDel').onclick = async () => {
      const res = await api('/api/session/tasks/delete', { body: { ids: [...UI.selTasks] } });
      if (!res.ok) { toast(res.reason || '没删成'); return; }
      closeModal(); UI.selTasks.clear(); await pull(); render();
      toast(`已移走 ${(res.data || {}).count || 0} 条对话`);
    };
  };
  bindJobDelete();
}
function refreshDrawer() {
  const d = $('#drawerBody'); if (d) { d.innerHTML = drawerInner(); bindDrawer(); }
}
function bindJobDelete() {
  $$('[data-jobdel]').forEach(el => el.onclick = async e => {
    e.stopPropagation();
    const id = el.dataset.jobdel;
    const j = (S.jobs || []).find(x => x.id === id);
    if (!j) return;
    openModal(`<div class="m-head"><h3>删除这条对话？</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><div class="tiny" style="line-height:1.9">右栏「本会话任务」和主页面里这条对话卡片会一起消失。<br>
        <b>成品文件不会删除</b>，仍能在「历史记录」里看到；只有历史记录页的删除才会删文件。<br>
        <span class="muted">${esc(j.prompt || '')}</span></div></div>
      <div class="m-foot"><button class="btn sm" data-close>取消</button>
        <button class="btn sm danger" id="confirmJobDel">确认删除</button></div>`);
    $('#confirmJobDel').onclick = async () => {
      const res = await api('/api/session/tasks/' + encodeURIComponent(id) + '/delete', { body: {} });
      if (!res.ok) { toast(res.reason || '删不掉'); return; }
      closeModal(); await pull(); render();
      toast('这条对话已移走，成品文件保留在历史里');
    };
  });
}
function scrollChatBottom() {
  const list = (S.jobs || []).filter(j => j.cap === UI.capId);
  const last = list.length ? document.getElementById(list[list.length - 1].id) : null;
  if (last) last.scrollIntoView({ block: 'end' }); else window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------ 创作台交互 */
function setMode(m) {
  // 文生图 / 图生图显式切换：模式决定走哪个子工作流，档位菜单跟着切到对应配置
  if (!['t2i', 'i2i'].includes(m) || UI.mode === m) return;
  UI.mode = m;
  if (m === 't2i') UI.refs = [];                                   // 文生图不传图，清掉已上传参考
  const g = currentGrades(currentCap());                            // 档位落到当前工作流的第一档（若当前档位不属于它）
  if (g.length && !g.find(x => x.name === UI.params.grade)) UI.params.grade = g[0].name;
  saveDraft(); refreshComposer();
  toast(m === 'i2i' ? '已切到「图生图」：可上传参考图' : '已切到「文生图」：纯文字生成');
}
function bindStudio() {
  $$('[data-cap]').forEach(el => el.onclick = () => {
    const target = (S.capabilities || []).find(x => x.id === el.dataset.cap);
    if (!target || !capUsable(target) || !target.enabled) { toast('这个能力还没配置，等下个版本开放'); return; }
    UI._userSwitched = true;
    UI.capId = el.dataset.cap; loadDraft();
    UI.navOpen = false;                   // 手机上选完能力把抽屉收起来
    saveDraft(); render(); window.scrollTo({ top: 0 });
  });
  $$('[data-sample]').forEach(el => el.onclick = () => {
    if (isSheetCap(currentCap())) {          // 设定图的示例是「角色描述」，不是整条提示词
      UI.sheetForm.character = el.dataset.sample;
      const sc = $('#sheetChar'); if (sc) { sc.value = UI.sheetForm.character; sc.focus(); }
      saveDraft(); refreshComposer(); return;
    }
    const ed = $('#promptInput'); if (!ed) return;
    UI.prompt = el.dataset.sample; ed.innerHTML = editorInnerHTML(UI.prompt);
    ed.focus(); onEditorInput();
  });
  $$('[data-mode]').forEach(el => el.onclick = () => setMode(el.dataset.mode));
  const send = $('#sendBtn'); if (send) send.onclick = doSend;
  const t = $('#promptInput');
  if (t) {
    t.onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doSend(); } };
    t.oninput = onEditorInput;
    t.onpaste = e => {
      // 剪贴板里有图片（截图 / 从成品图「复制图片」/ 资源管理器里复制的文件）→ 当参考图上传
      const images = clipboardImages(e);
      if (images.length) { e.preventDefault(); uploadFiles(images); return; }
      e.preventDefault();
      const cd = e.clipboardData || window.clipboardData;
      const text = cd ? cd.getData('text/plain') : '';              // contenteditable 默认带样式粘贴，转纯文本
      document.execCommand('insertText', false, text);
    };
    // 行内 chip 的 ✕ 用事件委托：chip 是运行时插入的（insertRefChip 不会重绑），逐元素绑定会漏绑新插入的那个
    t.onclick = e => {
      const x = e.target.closest && e.target.closest('[data-rmchip]'); if (!x) return;
      e.stopPropagation();
      const chip = x.closest('.refchip'); if (!chip) return;
      const id = chip.dataset.ref;
      chip.remove();
      if (id) UI.refs = UI.refs.filter(r => r.id !== id);
      UI.prompt = serializeEditor();          // DOM 已变，显式重算（程序化点击时编辑器未必聚焦，refreshComposer 不会自动序列化）
      saveDraft(); refreshComposer();
    };
  }
  const add = $('#addRef'); if (add) add.onclick = openFilePicker;
  const refBtn = $('#refBtn'); if (refBtn) refBtn.onclick = openFilePicker;
  $$('[data-rmref]').forEach(el => el.onclick = () => {
    // 只把这个图从输入框里摘掉，不删服务器上的副本：
    // 「基于上一条」要按本地副本把图片带回来，删了文件就再也引不过来了
    UI.refs = UI.refs.filter(r => r.id !== el.dataset.rmref);
    UI.prompt = serializeEditor();          // 参考图变少，chip 序号要重算；别让 refreshComposer 用旧 prompt 重建
    saveDraft(); refreshComposer();
  });
  $$('[data-refat]').forEach(el => el.onclick = e => { e.stopPropagation(); insertRefChip(el.dataset.refat); });
  $$('[data-grade]').forEach(el => el.onclick = () => {
    UI.params.grade = el.dataset.grade; saveDraft(); refreshComposer();
    toast('已切到「' + el.dataset.grade + '」');
  });
  $$('[data-cancel]').forEach(el => el.onclick = async () => {
    await api('/api/runs/' + el.dataset.cancel + '/cancel', { body: {} });
    await pull(); toast('已取消');
  });
  $$('[data-raw]').forEach(el => el.onclick = () => {
    const box = $('#err-' + el.dataset.raw); if (!box) return;
    box.classList.toggle('open');
    el.textContent = box.classList.contains('open') ? '收起详情' : '展开详情';
  });
  $$('[data-retry]').forEach(el => el.onclick = () => submitAction('/api/tasks/' + el.dataset.retry + '/retry', {}, '已重新提交'));
  $$('[data-again]').forEach(el => el.onclick = () => submitAction('/api/tasks/' + el.dataset.again + '/again', {}, ''));
  $$('[data-refine]').forEach(el => el.onclick = async () => {
    const j = (S.jobs || []).find(x => x.id === el.dataset.refine); if (!j) return;
    // 参考图按「上一条的本地副本」重新登记：旧 ref id 只是内存里的东西，
    // 用户可能已经把那张图从输入框删掉了（我们也确实不再删文件），所以重新要一组 id 最稳。
    let refs = [], missed = 0;
    if ((j.refs || []).length) {
      const res = await api('/api/tasks/' + j.id + '/refs', { body: {} });
      refs = ((res.ok && res.data && res.data.refs) || []).map(r => ({ id: r.id, kind: 'image', name: r.name }));
      missed = (j.refs || []).length - refs.length;
    }
    UI.refs = refs;
    UI.mode = refs.length ? 'i2i' : 't2i';          // 图没带回来就退回文生图，免得卡在「图生图没有参考图」发不出去
    if (j.sheet) {
      // 设定图：把整台表单（版式/画法/参考方式/档位/角色行/风格）带回来，而不是塞一整条 800 字提示词
      UI.sheetForm = { ...UI.sheetForm, ...j.sheet };
      // 老任务可能没记档位，按它当时用的那一档回填
      if (!UI.sheetForm.grade) UI.sheetForm.grade = (j.runs[0] || {}).grade || '默认';
      UI.sheetPreview = null;
    } else {
      UI.prompt = j.prompt || '';
    }
    if (!refs.length) {
      // 图片确实没了（文件被清理）：把 <imageN> 令牌一起去掉，提示词不能留下指空的引用
      UI.prompt = UI.prompt.replace(IMAGE_TOKEN, ' ').replace(/[ \t]{2,}/g, ' ').trim();
    }
    UI.params.grade = (j.runs[0] || {}).grade || '默认';
    const g = currentGrades(currentCap());
    if (g.length && !g.find(x => x.name === UI.params.grade)) UI.params.grade = g[0].name;
    const ed = $('#promptInput'); if (ed) ed.innerHTML = editorInnerHTML(UI.prompt);  // 先把回带的文字渲染进编辑器，saveDraft 才序列化到正确内容
    saveDraft(); refreshComposer();
    toast(missed > 0 ? `文字和档位带过来了，但有 ${missed} 张参考图已被清理` : '已把上一条整条带过来');
  });
  $$('[data-qadd]').forEach(el => el.onclick = () => openQueueModal(el.dataset.qadd));
  $$('[data-qcancel]').forEach(el => el.onclick = async () => {
    await api('/api/runs/' + el.dataset.qcancel + '/cancel', { body: {} }); await pull(); toast('已撤掉这一档');
  });
  $$('[data-seed]').forEach(el => el.onclick = async () => {
    const value = el.dataset.seed;
    try { await navigator.clipboard.writeText(String(value)); toast('种子 ' + value + ' 已复制'); }
    catch (e) { toast('种子：' + value); }
  });
  $$('[data-dl]').forEach(el => el.onclick = () => { window.location.href = '/api/download?run=' + encodeURIComponent(el.dataset.dl) + '&i=0'; });
  $$('[data-copyimg]').forEach(el => el.onclick = e => {
    e.stopPropagation();
    copyImageToClipboard(imgUrl(el.dataset.copyimg, 0), '成品图');
  });
  $$('[data-zoom]').forEach(el => el.onclick = () => openZoom(el.dataset.zoom));
  bindDrawer();
  bindSheetComposer();
  // 设定图卡片：正文 800 字，卡片只显示摘要，「看完整提示词」点开看原文
  $$('[data-seefull]').forEach(el => el.onclick = e => {
    e.stopPropagation();
    const j = (S.jobs || []).find(x => x.id === el.dataset.seefull); if (!j) return;
    openModal(`<div class="m-head"><h3>整条提示词</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><div class="tiny muted" style="margin-bottom:8px">共 ${String((j.prompt || '').length)} 字 · 提交给 ComfyUI 的就是这一段</div>
        <pre class="promptfull">${esc(j.prompt || '')}</pre></div>
      <div class="m-foot"><span class="tiny muted" style="flex:1">${esc((j.sheet || {}).layoutName || j.wf || '')}</span>
        <button class="btn sm" data-close>关闭</button><button class="btn sm primary" id="copyFullPrompt">复制</button></div>`);
    const cp = $('#copyFullPrompt');
    if (cp) cp.onclick = async () => {
      try { await navigator.clipboard.writeText(j.prompt || ''); toast('已复制'); }
      catch (err) { toast('复制失败，手动选中复制一下'); }
    };
  });
  const hb = $('#healthBtn'); if (hb) hb.onclick = async () => { await api('/api/v1/health'); await pull(); toast(UI.comfyOnline ? '已连接' : 'ComfyUI 未连接，请先启动'); };
  bindDropZone();
}
function refreshComposer() {
  const node = $('.composer'); if (!node) return;
  const focused = document.activeElement && document.activeElement.id === 'promptInput';
  if (focused) UI.prompt = serializeEditor();                 // 重绘前先存住编辑内容，viewComposer 会用它重建
  // 设定图的角色描述框同理：重绘前把正在打的字存进表单，否则切一下版式就白打了
  const sheetChar = document.activeElement && document.activeElement.id === 'sheetChar';
  if (sheetChar) UI.sheetForm.character = document.activeElement.value;
  node.outerHTML = viewComposer(); bindStudio();
  if (focused) { const t2 = $('#promptInput'); if (t2) t2.focus(); }
  if (sheetChar) { const c2 = $('#sheetChar'); if (c2) { c2.focus(); c2.setSelectionRange(c2.value.length, c2.value.length); } }
}
function openFilePicker() {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = 'image/*'; input.multiple = true;
  input.onchange = () => uploadFiles([...input.files]);
  input.click();
}
function bindDropZone() {
  const box = $('#inputbox'); if (!box || box._dropBound) return;
  box._dropBound = true;
  box.addEventListener('dragover', e => e.preventDefault());
  box.addEventListener('drop', e => {
    e.preventDefault();
    const files = [...(e.dataTransfer.files || [])].filter(f => (f.type || '').startsWith('image/'));
    if (files.length) uploadFiles(files);
  });
}
async function uploadFiles(files) {
  if (UI.mode !== 'i2i') { toast('文生图模式不带参考图，先切到「图生图」再上传'); return; }  // 需求 #1：文生图不传图
  const cap = currentCap(), maxRefs = cap.maxRefs || 3;
  const list = files.filter(f => f && (f.type || '').startsWith('image/'));
  if (!list.length) { toast('只支持常见图片格式（png / jpg / webp / gif / bmp）'); return; }
  for (const raw of list) {
    if (UI.refs.length >= maxRefs) { toast(`这个能力最多 ${maxRefs} 张参考图`); break; }
    const file = normalizeImageFile(raw);        // 剪贴板来的图可能没扩展名，按 MIME 补上
    const form = new FormData(); form.append('file', file, file.name);
    const res = await api('/api/uploads', { form });
    if (!res.ok) { toast(res.reason || '上传没成功'); continue; }
    UI.refs.push({ id: res.data.id, kind: 'image', name: res.data.name });
  }
  saveDraft(); refreshComposer();
}
async function submitAction(path, body, successMsg) {
  const res = await api(path, { body });
  if (!res.ok) { toastBlocked('这次没能发送', res.reason || '稍后再试'); return false; }
  if (path === '/api/tasks') {                  // 新发一条：输入框和参考图都清空，模式/档位保持用户的选择
    UI.prompt = ''; UI.refs = [];
    const ed = $('#promptInput'); if (ed) ed.innerHTML = '';   // 同时清 DOM，避免 saveDraft 又把旧文字序列化回去
    // 设定图：角色描述跟着清空（和「描述框发完清零」一致），版式/画法/风格保持不变
    if (isSheetCap(currentCap())) {
      UI.sheetForm.character = ''; UI.sheetPreview = null;
      const sc = $('#sheetChar'); if (sc) sc.value = '';
    }
    // 需求 #3：不再自动把模式切回文生图、档位切回默认。用户选了什么就保持什么，除非他自己切换。
  }
  saveDraft(); UI.autoScroll = true; await pull(); render();
  if (successMsg) toast(successMsg);
  return true;
}
async function doSend() {
  const cap = currentCap();
  if (isSheetCap(cap)) { await doSendSheet(cap); return; }
  const prompt = serializeEditor().trim();
  if (!prompt) { $('#inputbox').classList.add('err'); toast('写点描述再发送'); return; }
  // 模式 → 显式子工作流；图生图必须带参考图，否则直接拦下（后端虽会兜底，前端先挡更友好）
  const wf = currentWorkflowName(cap);
  if (UI.mode === 'i2i' && !UI.refs.length) {
    $('#inputbox').classList.add('err'); toast('图生图要先上传至少一张参考图'); return;
  }
  const wfObj = wfByName(cap, wf);
  if (!wfObj) { toast('这个能力还没有「' + wf + '」工作流'); return; }
  const grades = wfObj.grades || [];
  const grade = (grades.find(g => g.name === UI.params.grade) || grades[0] || {}).name || '默认';
  await submitAction('/api/tasks', { capId: cap.id, prompt, grade, refs: UI.refs.map(r => r.id), wf }, '');
}

async function doSendSheet(cap) {
  // 设定图只校验「必填项」：角色描述 + 参考方式与参考图是否配对；其余交给服务端组装
  const f = sheetForm();
  const box = $('#inputbox');
  if (!String(f.character || '').trim()) {
    if (box) box.classList.add('err');
    const ch = $('#sheetChar'); if (ch) ch.focus();
    toast('先写角色描述：年龄身份 + 五官 + 发型 + 服装 + 标志物');
    return;
  }
  if (UI.mode === 'i2i' && !UI.refs.length) {
    if (box) box.classList.add('err');
    toast('图生图要先上传一张参考图');
    return;
  }
  const grade = sheetGrade();
  const wf = currentWorkflowName(cap);
  if (!wf) { toast('这个能力还没有对应的工作流'); return; }
  if (!grade) { toast('这个组合还没有档位'); return; }
  const body = {
    capId: cap.id, grade: grade.name, wf,
    refs: UI.refs.map(r => r.id), sheet: sheetPayload(),
  };
  const sent = await submitAction('/api/tasks', body, '');
  if (sent) toast(`已提交「${(sheetCombo() || {}).name || ''}」· ${grade.name}`);
}
function openQueueModal(jobId) {
  const j = (S.jobs || []).find(x => x.id === jobId); if (!j) return;
  const cap = (S.capabilities || []).find(c => c.id === j.cap);
  const base = j.runs[0];
  /* 设定图：档位属于「这张卡所属的组合」，不是工作流。跨组合的档位一律不列——
     只有同组合的档位才保证提示词/参考/工作流完全一样，只换参数。 */
  const cid = (j.sheet || {}).combo || '';
  const combo = cid ? sheetCombos().find(c => c.id === cid) : null;
  const wf = (cap && (cap.workflows || []).find(w => w.name === base.wf)) || (cap && cap.workflows && cap.workflows[0]) || {};
  const grades = combo ? (combo.grades || []) : (wf.grades || []);
  if (!grades.length) { toast(cid ? '这个组合还没有档位' : '这个工作流还没有档位'); return; }
  // 确认框默认停在上一张那一档（需求 #22）
  const pick0 = grades.find(g => g.name === base.grade) || grades[0];
  const dnum = doneRuns(j).length, qn = queuedEntries().length, running = runningEntry();
  const intro = cid
    ? `同一个组合（<b>${esc(combo ? combo.name : cid)}</b>）的档位只改参数，提示词、参考图、工作流都一样。种子不变，新的接在<b>成品 ${dnum + 1}</b>。`
    : `挑一档，出同一张的更精细版。种子不变，已有的成品一张不动，新的接在<b>成品 ${dnum + 1}</b>。`;
  const html = `<div class="m-head"><h3>加入队列生成</h3><button class="btn ghost sm" data-close>✕</button></div>
    <div class="m-body"><div class="qintro">${intro}</div>
      <div class="qlabel">用哪一档</div><div class="qgrades">
        ${grades.map(g => `<div class="qg ${g.name === pick0.name ? 'on' : ''}" data-qg="${esc(g.name)}">
          <span class="qg-n">${esc(g.name)}</span><span class="qg-d">${esc(cid
            ? `${g.note ? g.note + ' · ' : ''}出图 ${g.size || ''}`
            : (gradeDetail(g, wf.fields, wf) || g.note || ''))}</span></div>`).join('')}
      </div>
      <div class="qrow"><span class="qn">种子</span><span class="qseed">${j.seed ?? base.seed ?? ''}</span><span class="tiny muted">跟这张一样</span></div>
      <div class="qstat">${running ? `正在跑${busyText(running)}，这一档排在它后面` : (qn ? `前面排了 ${qn} 个，这是第 ${qn + 1} 个` : '现在空着，点确认就开始')} · 一次只跑一个</div>
    </div><div class="m-foot"><span class="tiny muted" style="flex:1">可以连着加，一个一个跑</span>
      <button class="btn sm" data-close>取消</button><button class="btn sm primary" id="qGo">加入队列生成</button></div>`;
  UI.qJobId = j.id; let sel = pick0.name;
  const mask = openModal(html);
  $$('[data-qg]', mask).forEach(el => el.onclick = () => { sel = el.dataset.qg; $$('[data-qg]', mask).forEach(x => x.classList.toggle('on', x === el)); });
  $('#qGo', mask).onclick = async () => {
    const res = await api('/api/tasks/' + j.id + '/queue', { body: { grade: sel } });
    if (!res.ok) { toastBlocked('没能加进队列', res.reason || ''); closeModal(); return; }
    closeModal(); await pull(); render();
    toast(`已加入队列：前面还有 ${queuedEntries().length - 1} 个`);
  };
}
function openZoom(key) {
  const [jid, runId] = key.split('|');
  const j = (S.jobs || []).find(x => x.id === jid);
  const r = j && j.runs.find(x => x.id === runId); if (!r) return;
  const path = archivePathFor(null, r);
  const src = imgUrl(runId, 0);
  const mask = openModal(`<div class="m-head"><h3>成品预览</h3><button class="btn ghost sm" data-close>✕</button></div>
    <div class="m-body" style="padding:0"><img id="zoomImg" src="${src}" style="width:100%;display:block" title="Ctrl+C 复制这张图"></div>
    <div class="m-foot" style="flex-wrap:wrap">
      <span class="tiny muted mono" style="width:100%;margin-bottom:8px;word-break:break-all">${esc(path)}</span>
      <span class="tiny muted">种子 ${r.seed ?? j.seed ?? ''} · ${esc(r.grade || '默认')}<span class="zoomtip"> · Ctrl+C 复制图片</span></span><span style="flex:1"></span>
      <button class="btn sm" id="copyImg" title="复制图片本身，可直接粘贴到输入框当参考图">复制图片</button>
      <button class="btn sm" id="openDir" title="在资源管理器里打开并选中这个文件">打开所在目录</button>
      <button class="btn sm primary" id="zoomDl">下载 ⬇</button></div>`, true);
  $('#copyImg').onclick = () => copyImageToClipboard(src, '成品图');
  $('#openDir').onclick = async () => {
    const res = await api('/api/open-folder', { body: { path: path || S.service.archiveDir, select: true } });
    if (!res.ok) { toast(res.reason || '打不开所在目录'); return; }
    toast(res.data && res.data.focused ? '已在资源管理器里选中这张图' : '已打开所在目录');
  };
  $('#zoomDl').onclick = () => { window.location.href = '/api/download?run=' + encodeURIComponent(runId) + '&i=0'; };
  // 弹窗里 Ctrl+C 直接复制图片（焦点在输入框时让浏览器照常复制文字）
  const onKey = e => {
    if (e.key === 'Escape') { closeModal(); return; }
    if (!(e.ctrlKey || e.metaKey) || String(e.key).toLowerCase() !== 'c') return;
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    e.preventDefault();
    copyImageToClipboard(src, '成品图');
  };
  if (mask) { mask._onKey = onKey; document.addEventListener('keydown', onKey); }
}

/* ------------------------------------------------------------ 管理台 */
const ADMIN_PAGES = [['caps', '能力与工作流'], ['grades', '档位表'], ['sheet', '设定图模板'], ['service', '服务设置'], ['diag', '自检']];
/* 「档位表」下挂的子菜单：能配档位的能力（有工作流的能力），点了直接跳到它的档位表。
   能力清单在 config.yaml 里是固定的，所以这里按实际配置生成，不写死。 */
function gradesCaps() {
  return (S.capabilities || []).filter(c => (c.workflows || []).length);
}
function viewAdmin() {
  const cur = (S.capabilities || []).find(c => c.id === UI.capId) || S.capabilities[0];
  const subs = gradesCaps();
  return `<div class="admin ${UI.navOpen ? 'nav-open' : ''}">${viewTopbar()}${configBanner()}<div class="admin-body">
    <div class="admin-nav">
      <div class="side-hd"><b>管理台</b><button class="side-x" data-side-close title="收起">✕</button></div>
      ${ADMIN_PAGES.map(([k, n], i) => `<div class="item ${UI.adminPage === k ? 'on' : ''}" data-page="${k}"><span class="n">${i + 1}</span>${n}</div>
        ${k === 'grades' && subs.length ? `<div class="nav-sub">${subs.map(c => `<div class="sitem ${c.id === UI.capId && UI.adminPage === 'grades' ? 'on' : ''}" data-grades-cap="${esc(c.id)}">
          <span class="em">${c.em}</span><span class="sn">${esc(c.name)}</span></div>`).join('')}</div>` : ''}`).join('')}
      <div class="hr"></div><div class="tiny muted" style="padding:0 8px;line-height:1.7">${(S.capabilities || []).filter(c => capUsable(c) && c.enabled).length} 个能力可用</div>
    </div><div class="admin-main"><div class="inner">${adminPage(cur)}</div></div></div>
    <div class="side-mask" id="sideMask"></div></div>`;
}
function adminPage(cap) {
  if (UI.adminPage === 'caps') return pageCaps();
  // 设定图的档位挂在「组合」上，不是工作流上，所以档位表页要换一套界面
  if (UI.adminPage === 'grades') return isSheetCap(cap) ? pageComboGrades(cap) : pageGrades(cap);
  if (UI.adminPage === 'sheet') return pageSheet();
  if (UI.adminPage === 'service') return pageService();
  return pageDiag();
}
function pageCaps() {
  let list = '';
  orderedGroups().forEach(({ caps }) => caps.forEach(c => {
    const st = capStatus(c);
    const stChip = st === 'ok' ? '<span class="chip green">可用</span>'
      : st === 'partial' ? '<span class="chip amber">部分可用</span>'
      : st === 'err' ? '<span class="chip red">有问题</span>'
      : st === 'off' ? '<span class="chip gray">已停用</span>' : '<span class="chip gray">未配置</span>';
    const grades = capGrades(c), hasWf = c.workflows.length > 0;
    // 设定图的档位挂在组合上，直接数档位会显示「还没排档位」，要按组合口径说
    const comboCount = (c.combos || []).length;
    const gradeCount = comboCount
      ? (c.combos || []).reduce((n, x) => n + ((x.grades || []).length), 0)
      : grades.length;
    const gradeText = comboCount
      ? `${comboCount} 个组合 · ${gradeCount} 档`
      : (grades.length ? `${grades.length} 档可选` : '还没排档位');
    list += `<div class="cap-row ${hasWf ? '' : 'nocfg'}">
      <span class="em">${c.em}</span><div style="min-width:0;flex:1">
        <div class="row" style="gap:8px;flex-wrap:wrap"><b>${esc(c.name)}</b>${stChip}
          <span class="tiny muted">${esc(gradeText)}</span></div>
        ${c.desc ? `<div class="tiny muted" style="margin-top:2px">${esc(c.desc)}</div>` : ''}
      </div>
      <span class="switch ${c.enabled ? 'on' : ''}" data-toggle="${c.id}" style="width:34px;height:20px"></span>
      ${hasWf ? `<button class="btn sm" data-edit="${c.id}">配置</button>` : '<span class="tiny muted capoff">未配置（下个版本开放）</span>'}</div>`;
  }));
  return `<div class="spread" style="margin-bottom:14px"><div><h2>能力与工作流</h2>
    <div class="sub">开关能力、调整每个能力的档位（能力清单固定，改配置请改 config.yaml 后重启）</div></div></div>${list}`;
}

function ctlHtml(cls, field, value, attr, opt) {
  opt = opt || {};
  const kind = controlOf(cls, field), meta = fieldMeta(cls, field);
  const dis = opt.disabled ? 'disabled' : '';
  const v = value === undefined || value === null ? '' : value;
  const a = attr ? `${attr}="${attr.split('@')[0]}"` : '';
  if (kind === 'switch') {
    const on = v === true || v === 'true' || v === 1;
    return `<span class="sw ${on ? 'on' : ''} ${dis}" ${attr} data-switch="1" role="switch" aria-checked="${on}"><i></i><em>${on ? '开' : '关'}</em></span>`;
  }
  const ft = `data-ftype="${esc(meta.type || 'text')}"`;
  if (kind === 'select') {
    const opts = meta.options || [];
    const cur = String(v);
    const list = opts.includes(cur) ? opts : (cur !== '' ? [cur].concat(opts) : opts);
    return `<select class="input" ${ft} ${attr} ${dis}>${list.map(o => `<option value="${esc(o)}" ${String(o) === cur ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  }
  if (kind === 'multi') {
    const opts = meta.options || [], cur = Array.isArray(v) ? v.map(String) : [];
    return `<span class="multi ${dis}" ${ft} ${attr}>${[...new Set([...opts, ...cur])].map(o => `<label class="mopt"><input type="checkbox" value="${esc(o)}" ${cur.includes(String(o)) ? 'checked' : ''} ${dis}><span>${esc(o)}</span></label>`).join('')}</span>`;
  }
  if (kind === 'textarea') return `<textarea class="input" rows="2" ${ft} ${attr} ${dis}>${esc(v)}</textarea>`;
  const numeric = kind === 'number';
  const stepAttr = numeric ? `step="${meta.step || (meta.type === 'int' ? 1 : 0.05)}"` : '';
  const minAttr = meta.min !== undefined && meta.min !== null ? `min="${meta.min}"` : '';
  const maxAttr = meta.max !== undefined && meta.max !== null ? `max="${meta.max}"` : '';
  return `<input class="input ${numeric ? 'mono' : ''}" ${numeric ? 'type="number"' : 'type="text"'} ${stepAttr} ${minAttr} ${maxAttr} ${ft} value="${esc(v)}" ${attr} ${dis}>`;
}
function ctlRow(cls, field, value, attr, opt) {
  const kind = controlOf(cls, field);
  const wide = (kind === 'textarea' || (kind === 'text' && String(value ?? '').length > 16)) ? 'wide' : '';
  return `<div class="pfld ${wide}"><div class="plb"><span class="pn">${esc(fieldLabel(field))}</span>
    <i class="pk">${esc(field)}</i><span class="ptype t-${kind}">${esc(CONTROL_LABEL[kind] || kind)}</span></div>
    <div class="pctl">${ctlHtml(cls, field, value, attr, opt)}</div></div>`;
}
function nodeBlock(node, fieldList, getVal, attrOf, opt) {
  opt = opt || {};
  const live = fieldList.filter(f => nodeFields(node.id, opt.wfName).includes(f));
  return `<div class="node-edit ${opt.readonly ? 'ro' : ''}"><div class="ne-hd"><span class="cid">${node.id}</span>
    <b>${esc(node.cls)}</b><span class="tiny muted">${live.length} 个参数</span><span style="flex:1"></span>
    ${opt.readonly ? '<span class="rochip">只读 · 没改过</span>' : ''}${opt.hdExtra || ''}</div>
    <div class="ne-bd">${live.map(f => ctlRow(node.cls, f, getVal(f), attrOf(f), { disabled: opt.readonly, wfName: opt.wfName })).join('')}</div></div>`;
}
/* ============================================================ 设定图档位表（管理台）
   布局与交互严格对齐「图片生成」的档位表（pageGrades）：
     顶部三段式切「组合」（子功能 / 版式 / 画法 / 参考 —— 三个选完就是一个组合），
     左栏 ① 工作流文件 ② 可设置的参数节点 ③ 不用设的节点，
     右栏 ④ 新增档位 ⑤ 已有档位。
   组合 = 一条母版配方（工作流 + 提示词 + 画幅都定死），档位只改参数
   （百万像素 / 对齐倍数 / CFG / sigma）。宽高比由版式决定，不算「可设置项」，放 ③。 */
const COMBO_FIXED_FIELD = 'aspect_ratio';
// ④ 表单里给参数输入框固定 id（gfMegapixels…），测试和「保存」都按它取 DOM
const COMBO_FIELD_ID = { megapixels: 'Megapixels', multiple: 'Multiple', cfg: 'Cfg', sigmas: 'Sigmas' };

function comboById(cid) { return (S.sheetCombos || []).find(c => c.id === cid) || null; }
function comboFields(cid) { const c = comboById(cid); return (c && c.fields) || []; }
// 宽高比跟着版式走，档位里不出现（真要改画幅就换版式，这才是「组合」的语义）
function comboSettable(cid) { return comboFields(cid).filter(f => String(f.field) !== COMBO_FIXED_FIELD); }
function comboSetNodes(cid) {
  const out = [];
  comboSettable(cid).forEach(f => { const id = String(f.node); if (!out.includes(id)) out.push(id); });
  return out;
}
function comboFieldsOfNode(cid, nodeId) {
  return comboSettable(cid).filter(f => String(f.node) === String(nodeId)).map(f => String(f.field));
}
function comboHasFixedAspect(cid) { return comboFields(cid).some(f => String(f.field) === COMBO_FIXED_FIELD); }
function comboMasterParams(cid) {
  // 母版档（grades[0]）带着版式画幅参数，④⑤ 的输入框用它当「母版 X」占位
  const c = comboById(cid), g = (c && (c.grades || [])[0]) || {};
  return { ...(g.params || {}) };
}
function comboDetailOf(cid, name) {
  const d = UI.comboDetail && UI.comboDetail.cid === cid ? UI.comboDetail.rows : null;
  return ((d || []).find(r => r.name === name)) || null;
}
function comboPickedNodes(cid) {
  // ② 里勾了哪些节点：默认全勾；取消勾选只是过滤 ④ 的表单，不落盘
  UI.comboPicked = UI.comboPicked || {};
  const all = comboSetNodes(cid);
  const cur = UI.comboPicked[cid];
  if (!Array.isArray(cur)) { UI.comboPicked[cid] = all.slice(); return UI.comboPicked[cid]; }
  const keep = cur.filter(id => all.includes(id));
  UI.comboPicked[cid] = keep.length ? keep : all.slice();
  return UI.comboPicked[cid];
}
function comboCustomRows(cid) {
  // 已保存的自建档位（母版不算）：整体回写，所以先读出来
  const c = comboById(cid);
  return ((c && c.grades) || []).filter(g => !g.locked)
    .map(g => ({ name: g.name, note: g.note || '', params: { ...(g.params || {}) } }));
}
function comboDraft(cid) {
  const empty = { cid: '', name: '', note: '', values: {} };
  const f = UI.gradeForm = UI.gradeForm || { ...empty };
  if (f.cid !== cid) Object.assign(UI.gradeForm = { ...empty }, { cid });  // 换组合就清空
  return UI.gradeForm;
}
async function loadComboDetail(cid) {
  const res = await api('/api/sheet/combos');
  if (!res.ok) return;
  const item = ((res.data && res.data.combos) || []).find(c => c.id === cid);
  UI.comboDetail = { cid, rows: (item && item.grades) || [] };
  render();
}
async function saveComboGrades(cid, rows) {
  const res = await api('/api/sheet/combos/' + encodeURIComponent(cid) + '/grades',
                        { method: 'PUT', body: { grades: rows } });
  if (!res.ok) { toastBlocked('没保存', res.reason || ''); return false; }
  UI.comboDetail = null;
  await pull();
  render();
  await loadComboDetail(cid);
  return true;
}
function comboEffHTML(row, isDef) {
  // 逐参数「配置值 → 实际提交值」：不出图也能确认这一档有没有真的生效
  if (!row) return '<div class="tiny muted">正在核对参数…</div>';
  if (row.error) return `<div class="effhd bad">✕ 参数算不出来 <span class="tiny muted">${esc(row.error)}</span></div>`;
  const rows = (row.paramsRows || []).map(r => `<div class="effrow ${r.ok ? '' : 'bad'}">
    <span class="mono">${esc(r.field)}</span><span>${esc(String(r.want))} → ${esc(String(r.got))}</span>
    <b>${r.ok ? '✓' : '✕ ' + esc(r.reason || '')}</b></div>`).join('');
  return `<div class="effhd ${row.allOk ? 'ok' : 'bad'}">${row.allOk ? '✓ 参数全部生效' : '✕ 有参数没生效'}
      <span class="tiny muted">出图 ${esc(row.size || '')}</span></div>
    ${rows || `<div class="tiny muted">${isDef ? '这一档就是默认画幅，没有额外参数。' : '这一档没改参数，和默认一样。'}</div>`}`;
}
function comboPickBar(combos, combo) {
  const layouts = [], inks = [], refs = [];
  combos.forEach(c => {
    if (!layouts.some(l => l.id === c.layout)) layouts.push({ id: c.layout, name: c.layoutName, ratio: c.ratio });
    if (!inks.some(i => i.id === c.ink)) inks.push({ id: c.ink, name: c.inkName });
    if (!refs.some(r => r.id === c.ref)) refs.push({ id: c.ref, name: c.refName });
  });
  const t2i = combo.ref === 'none';
  const n2t = combos.filter(c => c.ref === 'none').length;
  const n2i = combos.length - n2t;
  return `<div class="panel" style="margin:0 0 12px;padding:4px 14px">
    <div class="sheetbar"><span class="slb">子功能</span>
      <div class="seg" role="group" aria-label="子功能">
        <button class="segb ${t2i ? 'on' : ''}" data-combo-mode="t2i">文生图<i class="segx">${n2t}</i></button>
        <button class="segb ${t2i ? '' : 'on'}" data-combo-mode="i2i">图生图<i class="segx">${n2i}</i></button>
      </div>
      <span class="tiny muted">共 ${combos.length} 个组合（文生图 ${n2t} 个组合 · 图生图 ${n2i} 个组合）· 每个组合一条默认档</span></div>
    <div class="sheetbar"><span class="slb">版式</span>
      <div class="seg" role="group" aria-label="版式">
        ${layouts.map(l => `<button class="segb ${combo.layout === l.id ? 'on' : ''}" data-combo-layout="${esc(l.id)}"
          title="${esc(l.name)} 出图 ${esc(combo.size || '')}">${esc(l.name)}<i class="segx">${esc(l.ratio || '')}</i></button>`).join('')}
      </div>
      <span class="tiny muted">版式决定画幅：${esc(combo.ratio)} ${esc(combo.size)}</span></div>
    <div class="sheetbar"><span class="slb">画法</span>
      <div class="seg" role="group" aria-label="画法">
        ${inks.map(i => `<button class="segb ${combo.ink === i.id ? 'on' : ''}" data-combo-ink="${esc(i.id)}">${esc(i.name)}</button>`).join('')}
      </div>
      <span class="tiny muted">线稿与彩色是两套提示词，参数节点完全一样</span></div>
    ${t2i ? '<div class="sheetbar"><span class="slb">参考</span><span class="tiny muted">文生图不吃参考图；要锁角色 / 借画风就切到「图生图」。</span></div>'
      : `<div class="sheetbar"><span class="slb">参考</span>
      <div class="seg" role="group" aria-label="参考方式">
        ${refs.filter(r => r.id !== 'none').map(r => `<button class="segb ${combo.ref === r.id ? 'on' : ''}" data-combo-ref="${esc(r.id)}">${esc(r.name)}</button>`).join('')}
      </div>
      <span class="tiny muted">子工作流：${esc(combo.wf)}</span></div>`}
    <div class="sheetbar"><span class="slb">当前</span>
      <b style="font-size:12.5px">${esc(combo.name)}</b>
      <span class="tag">${esc(combo.wf)}</span><span class="tag">${esc(combo.ratio)} ${esc(combo.size)}</span>
      <span style="flex:1"></span>
      <button class="btn sm ghost" data-combo-refresh>↻ 重读档位</button>
      <button class="btn sm" data-combo-studio>去创作台试这个组合</button></div>
  </div>`;
}

function pageComboGrades(cap) {
  const combos = cap.combos || [];
  if (!combos.length) {
    return `<div class="spread" style="margin-bottom:6px"><div><h2>档位表</h2><div class="sub">${esc(cap.em)} ${esc(cap.name)}</div></div>
      <button class="btn sm" data-page="caps">← 返回列表</button></div>
      <div class="panel"><div class="empty" style="padding:36px 24px"><div style="font-size:15px;font-weight:650">组合还没生成</div>
        <div class="tiny muted" style="line-height:1.95;max-width:520px;margin:0 auto">提示词库（prompts/character_sheet.json）读不到时会这样，去「设定图模板」看一眼。</div></div></div>`;
  }
  if (!combos.some(c => c.id === UI.comboSel)) UI.comboSel = combos[0].id;
  const cid = UI.comboSel;
  const combo = comboById(cid) || combos[0];
  const wfName = combo.wf;
  const nodes = wfLib(wfName).list;
  const settable = comboSettable(cid), setNodes = comboSetNodes(cid);
  const picked = comboPickedNodes(cid);
  const pickedFields = settable.filter(f => picked.includes(String(f.node))).map(f => String(f.field));
  const master = comboMasterParams(cid);
  const grades = combo.grades || [];
  const open = Math.min(UI.gOpen | 0, Math.max(grades.length - 1, 0));
  const draft = comboDraft(cid);
  const roleLabel = { prompt: '提示词', upload: '上传参考', output: '出片' };

  // ③ 不用设的节点：剩下的参数节点 + 提示词/上传/出片 + 宽高比（版式定）
  const rest = paramNodes(wfName).filter(n => !setNodes.includes(String(n.id)));
  const excluded = excludedNodes(wfName);

  return `<div class="spread" style="margin-bottom:6px"><div><h2>档位表</h2>
      <div class="sub">${esc(cap.em)} ${esc(cap.name)} / ${esc(combo.name)}</div></div>
    <div class="row"><button class="btn sm" data-page="caps">← 返回列表</button>
      <button class="btn primary sm" data-combo-save>保存</button></div></div>

    ${comboPickBar(combos, combo)}

    <div class="glayout" style="display:grid;grid-template-columns:296px minmax(0,1fr);gap:14px;align-items:start">
      <div class="panel" style="margin:0;position:sticky;top:12px"><h3>① 工作流文件</h3>
        <div class="wffile"><span class="wfname mono">${esc(wfLib(wfName).path || wfName)}</span><span class="wfauto">自动读取</span></div>
        <div class="tiny muted" style="margin:8px 0 14px;line-height:1.7">只读，不对它做任何改动。这个组合固定用这条工作流。</div>
        <h3 style="flex-wrap:wrap">② 参数节点 <span class="tag">${setNodes.length} 个</span></h3>
        <div class="tiny muted" style="margin:-2px 0 8px;line-height:1.7">勾上哪些节点，右边 ④ 就出现哪些参数。</div>
        <div class="node-list">${setNodes.length ? setNodes.map(id => {
          const n = nodes.find(x => String(x.id) === String(id)) || { id, cls: id };
          const on = picked.includes(String(id)), fs = comboFieldsOfNode(cid, id);
          return `<label class="node-row can ${on ? 'on' : ''}" data-picknode="${esc(String(id))}"><input type="checkbox" ${on ? 'checked' : ''}>
            <span class="id">${esc(String(id))}</span><span class="cls">${esc(n.cls)}</span>
            <span class="nf">${fs.map(f => esc(fieldLabel(f))).join(' · ')}</span></label>`;
        }).join('') : '<div class="tiny muted" style="padding:8px 2px">这个工作流没有可设置的参数节点。</div>'}</div>
        <div class="hr"></div><h3 style="flex-wrap:wrap">③ 不用设的节点 <span class="tag gray">${rest.length + excluded.length + (comboHasFixedAspect(cid) ? 1 : 0)} 个</span></h3>
        <div class="tiny muted" style="margin:-2px 0 8px;line-height:1.7">填词的、传素材的、出片的，还有跟着版式走的宽高比。</div>
        <div class="node-list dim">
          ${comboHasFixedAspect(cid) ? `<div class="node-row no"><span class="nochk"></span>
            <span class="id">—</span><span class="cls">宽高比 aspect_ratio</span><span class="role r-upload">版式定</span></div>` : ''}
          ${rest.map(n => `<div class="node-row no"><span class="nochk"></span>
            <span class="id">${esc(String(n.id))}</span><span class="cls">${esc(n.cls)}</span>
            <span class="role r-prompt">不用改</span></div>`).join('')}
          ${excluded.map(n => `<div class="node-row no"><span class="nochk"></span>
            <span class="id">${esc(String(n.id))}</span><span class="cls">${esc(n.cls)}</span>
            <span class="role r-${n.role}">${esc(roleLabel[n.role] || '')}</span></div>`).join('')}
        </div>
      </div>

      <div>
        <div class="panel build"><h3 style="flex-wrap:wrap">④ 新增档位 <span class="tag">勾 ${picked.length} 个节点 / ${pickedFields.length} 个参数</span></h3>
          <div class="tiny muted" style="margin:-2px 0 12px;line-height:1.75">填好值、起个名，点<b>【添加】</b>，创作台就多一个档位按钮。
            留空 = 跟随默认（提示词一个字都不变，只改速度与精度）。</div>
          ${picked.length ? `<div class="node-editors">${picked.map(id => {
            const n = nodes.find(x => String(x.id) === String(id));
            if (!n) return '';
            const fs = comboFieldsOfNode(cid, id);
            return nodeBlock(n, fs, f => (draft.values || {})[f] ?? '',
              f => `data-cv="${esc(f)}" id="gf${COMBO_FIELD_ID[f] || f}" placeholder="默认 ${esc(master[f] ?? '')}"`,
              { wfName, hdExtra: `<button class="btn sm ghost" data-unpicknode="${esc(String(id))}">移出</button>` });
          }).join('')}</div>` : '<div class="empty" style="padding:22px"><div class="tiny">还没勾节点，所以这里没有参数。</div><div class="tiny muted" style="margin-top:4px">左边 ② 勾一个就有。</div></div>'}
          <div class="addbar"><div class="addbar-lb"><span class="an">档位名称</span><span class="tiny muted">创作台上显示这几个字</span></div>
            <input class="input aname" id="gfName" value="${esc(draft.name || '')}" placeholder="例如：快速 / 精细" data-draftname>
            <button class="btn primary addbtn" data-gadd ${picked.length ? '' : 'disabled'}>＋ 添加</button></div>
          <div class="tiny muted" style="margin-top:8px;line-height:1.7"><b>2~3 档就够了</b>：档位只改参数，所以同一档出的是同一张图的草稿版 / 高清版。</div>
        </div>

        <div class="panel"><div class="row" style="margin-bottom:4px"><h3 style="margin:0">⑤ 已有档位 <span class="tag">${grades.length} 档</span></h3>
          <span style="flex:1"></span><span class="tiny muted">点行展开看参数</span></div>
          <div class="tiny muted" style="margin-bottom:10px;line-height:1.75">「默认」改不了；其余各档点 ✎ 就能改名字、说明和参数，改完点右上角<b>保存</b>。</div>
          ${grades.length ? `<div class="glist">${grades.map((g, i) => {
            const isOpen = i === open, isDef = !!g.locked;
            const row = comboDetailOf(cid, g.name);
            const changed = Object.keys(g.params || {}).length;
            return `<div class="grow ${isOpen ? 'open' : ''} ${isDef ? 'def' : ''}" data-grow="${i}">
              <div class="grow-hd" data-gtoggle="${i}"><span class="gnum">${i + 1}</span>
                ${isDef ? `<span class="gname-txt">${esc(g.name)}<i class="lock">🔒 原始</i></span>`
                  : `<input class="input gname" value="${esc(g.name)}" data-gfield="name|${i}" data-stop>`}
                ${isDef ? '<span class="gdefnote">只读</span>' : `<span class="gcopy">${changed} 项改动</span>`}
                <span class="geff ${row && row.allOk ? 'ok' : 'bad'}" title="参数生效自检">${esc(g.size || '')}</span>
                <span class="gsp" style="flex:1"></span>
                <span class="gedit ${isOpen ? 'on' : ''}" data-gtoggle="${i}">${isDef ? '👁 查看' : '✎ 编辑'}</span>
                ${isDef ? '<button class="gdel" disabled>✕</button>' : `<button class="gdel" data-gdel="${esc(g.name)}">✕</button>`}</div>
              ${isOpen ? `<div class="grow-bd"><div class="effbox" id="comboEff">${comboEffHTML(row, isDef)}</div>
                ${picked.length ? picked.map(id => {
                  const n = nodes.find(x => String(x.id) === String(id));
                  if (!n) return '';
                  return nodeBlock(n, comboFieldsOfNode(cid, id), f => (g.params || {})[f] ?? '',
                    f => isDef ? '' : `data-cv="${esc(f)}@${i}" placeholder="默认 ${esc(master[f] ?? '')}"`,
                    { wfName, readonly: isDef });
                }).join('') : '<div class="tiny muted" style="padding:4px 2px">左边 ② 还没勾节点。</div>'}
                ${isDef ? '' : `<div class="note-row"><span class="nm">一句话说明</span>
                  <input class="input" value="${esc(g.note || '')}" data-gfield="note|${i}" placeholder="例如：几十秒出一张"></div>`}</div>` : ''}</div>`;
          }).join('')}</div>` : '<div class="empty" style="padding:20px"><div class="tiny">还没有档位</div></div>'}
        </div>
      </div>
    </div>`;
}

function comboCollectFromDom(cid, i) {
  // 从展开那一档的控件读参数：空值 = 跟随母版（不写），与母版相同也不写，
  // 这样 params 只留「这一档真正改了什么」，表里显示才准。
  const master = comboMasterParams(cid), out = {};
  $$('[data-cv]').forEach(el => {
    const [field, idx] = splitGVal(el.dataset.cv);
    if (idx !== i || !field) return;
    const raw = String(el.value ?? '').trim();
    if (raw === '') return;
    if (master[field] !== undefined && String(master[field]) === raw) return;
    out[field] = (field === 'sigmas' || field === 'aspect_ratio') ? raw : Number(raw);
  });
  return out;
}
function comboCollectAll(cid) {
  // 保存 = 整表回写：展开那档用 DOM 实时值，其余档沿用已存参数（幂等重存）
  const combo = comboById(cid), open = UI.gOpen | 0;
  const grades = (combo && combo.grades) || [];
  return grades.map((g, i) => i).filter(i => !(grades[i] || {}).locked).map(i => {
    const g = grades[i];
    const nameEl = $('[data-gfield="name|' + i + '"]');
    const noteEl = $('[data-gfield="note|' + i + '"]');
    return {
      name: String((nameEl && nameEl.value) ?? '').trim() || g.name,
      note: String((noteEl && noteEl.value) ?? g.note ?? ''),
      params: i === open ? comboCollectFromDom(cid, i) : { ...(g.params || {}) },
    };
  });
}
function bindComboGrades() {
  const cid = UI.comboSel;
  const combo = comboById(cid);
  if (!combo) return;
  if (!(UI.comboDetail && UI.comboDetail.cid === cid)) {
    // 首次进这个组合：拉一次参数生效核对，拉到后自己重绘（不会再触发第二次）
    setTimeout(() => { loadComboDetail(cid); }, 0);
  }
  // ---- 组合切换：三段拼出 cid，版式/画法/参考都只是换配方 ----
  const go = (patch) => {
    const c = comboById(UI.comboSel) || combo;
    const next = { layout: c.layout, ink: c.ink, ref: c.ref, ...patch };
    if (next.ref !== 'none' && next.ref === undefined) next.ref = 'lock';
    UI.comboSel = [next.layout, next.ink, next.ref].join('.');
    UI.gOpen = 0;
    render();
  };
  $$('[data-combo-mode]').forEach(el => el.onclick = () => {
    const t2i = el.dataset.comboMode === 't2i';
    go({ ref: t2i ? 'none' : (combo.ref === 'none' ? 'lock' : combo.ref) });
  });
  $$('[data-combo-layout]').forEach(el => el.onclick = () => go({ layout: el.dataset.comboLayout }));
  $$('[data-combo-ink]').forEach(el => el.onclick = () => go({ ink: el.dataset.comboInk }));
  $$('[data-combo-ref]').forEach(el => el.onclick = () => go({ ref: el.dataset.comboRef }));
  $$('[data-combo-refresh]').forEach(el => el.onclick = async () => {
    await pull(); UI.comboDetail = null; render(); toast('已重读档位');
  });
  $$('[data-combo-studio]').forEach(el => el.onclick = async () => {
    // 把当前组合带到创作台：版式/画法/参考方式一次填好，档位停在母版
    const c = comboById(UI.comboSel) || combo;
    UI.capId = 'sheet.character';
    UI.mode = c.ref === 'none' ? 't2i' : 'i2i';
    UI.sheetForm = { ...UI.sheetForm, layout: c.layout, ink: c.ink,
                     ref: c.ref === 'none' ? 'lock' : c.ref, grade: '默认' };
    UI.sheetOpen = false; UI.sheetPreview = null;
    UI.route = 'studio';
    UI._navPending = true;
    await saveDraft({ route: 'studio', capId: 'sheet.character' });
    UI._navPending = false;
    render();
  });
  // ---- ② 勾节点：只影响 ④ 的表单 ----
  $$('[data-picknode] input[type=checkbox]').forEach(box => box.onchange = () => {
    const el = box.closest('[data-picknode]'); if (!el) return;
    const id = String(el.dataset.picknode);
    const cur = comboPickedNodes(cid);
    UI.comboPicked[cid] = box.checked ? [...new Set([...cur, id])] : cur.filter(x => x !== id);
    render();
  });
  $$('[data-unpicknode]').forEach(el => el.onclick = () => {
    const id = String(el.dataset.unpicknode);
    UI.comboPicked[cid] = comboPickedNodes(cid).filter(x => x !== id);
    render();
  });
  // ---- ④ 表单输入态：和「图片生成」一致，勾节点 / 切回重绘后不丢已填的值 ----
  const draft = comboDraft(cid);
  const dn = $('[data-draftname]');
  if (dn) dn.oninput = () => { draft.name = dn.value; };
  $$('.panel.build [data-cv]').forEach(el => {
    const field = String(el.dataset.cv || '');
    if (!field || field.includes('@')) return;
    const keep = () => { (draft.values = draft.values || {})[field] = el.value; };
    el.oninput = keep;
    el.onchange = keep;
  });
  // ---- ④ 新增档位 ----
  const ga = $('[data-gadd]');
  if (ga) ga.onclick = async () => {
    const draft = comboDraft(cid);
    const name = String(($('#gfName') || {}).value || '').trim();
    if (!name) { toast('先给档位起个名字'); const n = $('#gfName'); if (n) n.focus(); return; }
    if (name === '默认') { toast('「默认」是组合自带的，换个名字'); return; }
    if (comboCustomRows(cid).some(g => g.name === name)) { toast(`已经有「${name}」了，改个名字或直接去 ⑤ 编辑`); return; }
    const params = {};
    $$('[data-cv]').forEach(el => {
      const field = el.dataset.cv; if (String(field).includes('@')) return;
      const raw = String(el.value ?? '').trim(); if (raw === '') return;
      const master = comboMasterParams(cid);
      if (master[field] !== undefined && String(master[field]) === raw) return;
      params[field] = (field === 'sigmas' || field === 'aspect_ratio') ? raw : Number(raw);
    });
    const rows = comboCustomRows(cid);
    // 和「图片生成」的 ④ 一致：新增只填名字 + 参数，一句话说明在 ⑤ 展开后补
    rows.push({ name, note: '', params });
    if (await saveComboGrades(cid, rows)) {
      UI.gradeEdit = null;
      Object.assign(comboDraft(cid), { name: '', note: '', values: {} });
      toast(`已添加档位「${name}」`);
    }
  };
  // ---- ⑤ 展开 / 删除 ----
  $$('[data-gtoggle]').forEach(el => el.onclick = () => {
    UI.gOpen = +el.dataset.gtoggle; render();
  });
  $$('[data-gdel]').forEach(el => el.onclick = async e => {
    e.stopPropagation();
    const name = el.dataset.gdel;
    const rows = comboCustomRows(cid).filter(g => g.name !== name);
    if (await saveComboGrades(cid, rows)) toast(`已删除档位「${name}」`);
  });
  // ---- 保存（整表回写）----
  $$('[data-combo-save]').forEach(el => el.onclick = async () => {
    const rows = comboCollectAll(cid);
    if (!rows.length) { toast('没有可保存的档位（默认档是只读的）'); return; }
    for (const r of rows) {
      if (!r.name) { toast('档位名不能为空'); return; }
      if (r.name === '默认') { toast('档位不能叫「默认」'); return; }
    }
    if (await saveComboGrades(cid, rows)) toast(`已保存 ${rows.length} 档 · 出图见 ⑤ 的尺寸`);
  });
}

/* 「图片生成」这类能力：工作流就是它的「组合」。
   切换条必须和角色设定图那块长得一模一样（白框面板 + 分段按钮 + 当前 + 两个动作），
   否则两张档位表一眼看过去像两个模块。 */
function wfPickBar(cap, wf) {
  const wfs = cap.workflows || [], grades = wf.grades || [];
  const first = grades[0] || {};
  return `<div class="panel" style="margin:0 0 12px;padding:4px 14px">
    <div class="sheetbar"><span class="slb">子功能</span>
      <div class="seg" role="group" aria-label="子工作流">
        ${wfs.map((w, i) => `<button class="segb ${i === UI.editingWfIndex ? 'on' : ''}" data-wfswitch="${i}">${esc(w.name)}</button>`).join('')}
      </div>
      <span class="tiny muted">共 ${wfs.length} 个子工作流 · 每个工作流一条默认档</span></div>
    <div class="sheetbar"><span class="slb">当前</span>
      <b style="font-size:12.5px">${esc(wf.name)}</b>
      <span class="tag">${grades.length} 档</span>
      <span class="tag">${esc((first.effective || {}).size || '尺寸由工作流决定')}</span>
      <span style="flex:1"></span>
      <button class="btn sm ghost" data-wfrefresh>↻ 重读档位</button>
      <button class="btn sm" data-wfstudio>去创作台试这个组合</button></div>
  </div>`;
}

function pageGrades(cap) {
  if (!cap) return '';
  const wf = cap.workflows[UI.editingWfIndex] || cap.workflows[0];
  if (!wf) return `<div class="spread" style="margin-bottom:6px"><div><h2>档位表</h2><div class="sub">${esc(cap.em)} ${esc(cap.name)}</div></div>
    <button class="btn sm" data-page="caps">← 返回列表</button></div>
    <div class="panel"><div class="empty" style="padding:36px 24px"><div style="font-size:15px;font-weight:650">还没配置</div>
      <div class="tiny muted" style="line-height:1.95;max-width:540px;margin:0 auto">这个能力还排不了档位。</div></div></div>`;
  const nodes = wfLib(wf.name).list, pickable = paramNodes(wf.name), excluded = excludedNodes(wf.name);
  const picked = pickedNodeIds(wf), grades = wf.grades || [];
  const open = Math.min(UI.gOpen | 0, Math.max(grades.length - 1, 0));
  const draft = UI.draft || (UI.draft = { name: '', values: {} });
  const setFields = new Set(); picked.forEach(id => nodeFields(id, wf.name).forEach(f => setFields.add(f)));
  const draftVal = f => (draft.values || {})[f] !== undefined ? (draft.values || {})[f] : (wf.original || {})[f];
  const roleLabel = { prompt: '提示词', upload: '上传参考', output: '出片' };
  return `<div class="spread" style="margin-bottom:6px"><div><h2>档位表</h2><div class="sub">${esc(cap.em)} ${esc(cap.name)} / ${esc(wf.name)}</div></div>
    <div class="row"><button class="btn sm" data-page="caps">← 返回列表</button><button class="btn primary sm" data-savewf>保存</button></div></div>
    ${wfPickBar(cap, wf)}
    <div class="glayout" style="display:grid;grid-template-columns:296px minmax(0,1fr);gap:14px;align-items:start">
      <div class="panel" style="margin:0;position:sticky;top:12px"><h3>① 工作流文件</h3>
        <div class="wffile"><span class="wfname mono">${esc(wf.path)}</span><span class="wfauto">自动读取</span></div>
        <div class="tiny muted" style="margin:8px 0 14px;line-height:1.7">只读，不对它做任何改动。</div>
        <h3 style="flex-wrap:wrap">② 参数节点 <span class="tag">${pickable.length} 个</span></h3>
        <div class="tiny muted" style="margin:-2px 0 8px;line-height:1.7">勾上哪些节点，右边 ④ 就出现哪些参数。</div>
        <div class="node-list">${pickable.length ? pickable.map(n => {
          const on = picked.includes(String(n.id)), fs = nodeFields(n.id, wf.name);
          return `<label class="node-row can ${on ? 'on' : ''}" data-picknode="${n.id}"><input type="checkbox" ${on ? 'checked' : ''}>
            <span class="id">${n.id}</span><span class="cls">${esc(n.cls)}</span><span class="nf">${fs.map(f => fieldLabel(f)).join(' · ')}</span></label>`;
        }).join('') : '<div class="tiny muted" style="padding:8px 2px">这个工作流没有可设置的参数节点。</div>'}</div>
        <div class="hr"></div><h3 style="flex-wrap:wrap">③ 不用设的节点 <span class="tag gray">${excluded.length} 个</span></h3>
        <div class="tiny muted" style="margin:-2px 0 8px;line-height:1.7">这三类不用管：填词的、传素材的、出片的。</div>
        <div class="node-list dim">${excluded.map(n => `<div class="node-row no"><span class="nochk"></span>
          <span class="id">${n.id}</span><span class="cls">${esc(n.cls)}</span><span class="role r-${n.role}">${esc(roleLabel[n.role] || '')}</span></div>`).join('')}</div>
      </div>
      <div>
        <div class="panel build"><h3 style="flex-wrap:wrap">④ 新增档位 <span class="tag">勾 ${picked.length} 个节点 / ${setFields.size} 个参数</span></h3>
          <div class="tiny muted" style="margin:-2px 0 12px;line-height:1.75">填好值、起个名，点<b>【添加】</b>，创作台就多一个按钮。</div>
          ${picked.length ? `<div class="node-editors">${picked.map(id => {
            const n = nodes.find(x => String(x.id) === String(id)); if (!n) return '';
            return nodeBlock(n, nodeFields(id, wf.name), draftVal, f => `data-drf="${id}|${f}"`,
              { wfName: wf.name, hdExtra: `<button class="btn sm ghost" data-unpicknode="${id}">移出</button>` });
          }).join('')}</div>` : '<div class="empty" style="padding:22px"><div class="tiny">还没勾节点，所以这里没有参数。</div><div class="tiny muted" style="margin-top:4px">只有一个「默认」档也能用。</div></div>'}
          <div class="addbar"><div class="addbar-lb"><span class="an">档位名称</span><span class="tiny muted">创作台上显示这几个字</span></div>
            <input class="input aname" id="newGradeName" value="${esc(draft.name || '')}" placeholder="例如：快速 / 精细" data-draftname>
            <button class="btn primary addbtn" data-gadd ${picked.length ? '' : 'disabled'}>＋ 添加</button></div>
          <div class="tiny muted" style="margin-top:8px;line-height:1.7"><b>2~3 档就够了</b>，多了没人挑。</div>
        </div>
        <div class="panel"><div class="row" style="margin-bottom:4px"><h3 style="margin:0">⑤ 已有档位 <span class="tag">${grades.length} 档</span></h3><span style="flex:1"></span></div>
          <div class="tiny muted" style="margin-bottom:10px;line-height:1.75">「默认」改不了；其余各档点 ✎ 就能改。右侧显示这一档的实际出图尺寸。</div>
          ${grades.length ? `<div class="glist">${grades.map((g, i) => {
            const isOpen = i === open, isDef = !!g.locked;
            const pv = gradeParams(wf, g);
            const changed = Object.keys(g.params || {}).filter(k => String(g.params[k]) !== String((wf.original || {})[k]));
            return `<div class="grow ${isOpen ? 'open' : ''} ${isDef ? 'def' : ''}" data-grow="${i}">
              <div class="grow-hd" data-gtoggle="${i}">${isDef ? '' : `<span class="gdrag" draggable="true" data-gdrag="${i}" title="拖动调整创作台里的档位顺序" data-stop>⠿</span>`}<span class="gnum">${i + 1}</span>
                ${isDef ? `<span class="gname-txt">${esc(g.name)}<i class="lock">🔒 原始</i></span>` : `<input class="input gname" value="${esc(g.name)}" data-gfield="name|${i}" data-stop>`}
                ${isDef ? '<span class="gdefnote">只读</span>' : `<span class="gcopy">${changed.length} 项改动</span>`}
                <span class="geff ${g.effective && g.effective.allOk ? 'ok' : 'bad'}" title="参数生效自检">${esc((g.effective || {}).size || '')}</span>
                <span class="gsp" style="flex:1"></span>
                <span class="gedit ${isOpen ? 'on' : ''}" data-gtoggle="${i}">${isDef ? '👁 查看' : '✎ 编辑'}</span>
                ${isDef ? '<button class="gdel" disabled>✕</button>' : `<button class="gdel" data-gdel="${i}">✕</button>`}</div>
              ${isOpen ? `<div class="grow-bd"><div class="effbox" id="gradeEff">正在核对参数…</div>${picked.length ? picked.map(id => {
                const n = nodes.find(x => String(x.id) === String(id)); if (!n) return '';
                return nodeBlock(n, nodeFields(id, wf.name), f => pv[f], f => isDef ? '' : `data-gval="${f}@${i}"`, { wfName: wf.name, readonly: isDef });
              }).join('') : '<div class="tiny muted" style="padding:4px 2px">左边 ② 还没勾节点。</div>'}
                ${isDef ? '' : `<div class="note-row"><span class="nm">一句话说明</span><input class="input" value="${esc(g.note || '')}" data-gfield="note|${i}" placeholder="例如：几十秒出一张"></div>`}</div>` : ''}</div>`;
          }).join('')}</div>` : '<div class="empty" style="padding:20px"><div class="tiny">还没有档位</div></div>'}
        </div>
      </div>
    </div>`;
}
/* ---------- 管理台：设定图模板（提示词库） ---------- */
const SHEET_REF_NAME = { none: '无', lock: '锁同角色', style: '借画风' };
function sheetLayoutName(id) { const l = sheetLayoutOf(id); return l ? l.name : id; }

function pageSheet() {
  const lib = sheetLib();
  const layouts = lib.layouts || [];
  if (!layouts.length) {
    return `<h2>设定图模板</h2><div class="sub">角色设定图的提示词库</div>
      <div class="panel"><div class="brokenbox">读不到 <b>prompts/character_sheet.json</b>。
        具体原因看服务窗口日志或页面顶部红条；文件修好后点下面这个按钮重读。</div></div>
      <button class="btn primary" data-sheetlib-reload>↻ 重读提示词库</button>`;
  }
  const blocks = lib.blocks || {}, lines = lib.line_styles || [], styles = lib.styles || [];
  const presets = lib.presets || [], hints = lib.variable_hints || {};
  const cap = (S.capabilities || []).find(c => c.id === 'sheet.character');
  const wfs = (cap && cap.workflows) || [];
  return `<div class="spread" style="margin-bottom:6px">
      <div><h2>设定图模板</h2><div class="sub">${esc(lib.name || '角色设定图')} · 通用块改一次，所有版式同时生效 · 数据文件 ${esc(lib.file || '')}</div></div>
      <div class="row"><button class="btn sm" data-sheetlib-reload>↻ 重读提示词库</button>
        <button class="btn primary sm" data-sheetlib-save>保存</button></div></div>

    <div class="panel"><h3>档位与画幅 <span class="tag">${wfs.length} 个子工作流</span></h3>
      <div class="tiny muted" style="line-height:1.85">角色设定图的档位挂在<b>组合</b>上：版式 × 画法 × 参考方式 = 18 个组合，
        每个组合自带一条<b>默认</b>档（参数就是版式画幅），要「快速 / 精细」去「档位表」按组合建。
        <b>画幅由版式决定</b>（四视图横排 1536×864、宫格与单张 864×1536），档位只能改像素 / 倍数 / CFG / sigma。</div>
      ${wfs.length ? `<table class="map-table" style="margin-top:10px"><thead><tr><th>子工作流</th><th>组合数</th><th>默认画幅</th><th>档位</th></tr></thead><tbody>
        ${wfs.map(w => { const cs = (cap.combos || []).filter(c => c.wf === w.name);
          const sizes = [...new Set(cs.map(c => c.size))].join(' / ');
          const extra = cs.reduce((n, c) => n + Math.max((c.grades || []).length - 1, 0), 0);
          return `<tr><td class="tiny">${esc(w.name)}</td><td class="tiny">${cs.length} 个</td>
            <td class="mono tiny">${esc(sizes || '—')}</td>
            <td class="tiny ${extra ? '' : 'muted'}">${extra ? `默认 + ${extra} 档自建` : '只有默认档'}</td></tr>`; }).join('')}
        </tbody></table>
        <div class="row" style="margin-top:10px"><button class="btn sm" data-page="grades">去档位表调参数</button>
          <span class="tiny muted">档位表左边先选组合，再在右边加档。</span></div>`
        : '<div class="brokenbox">这个能力还没配工作流，先去「能力与工作流」里配好。</div>'}
    </div>

    <div class="panel"><h3>版式模板 <span class="tag">${layouts.length} 个</span></h3>
      <div class="sheet-cards">${layouts.map(l => `<div class="lcard">
        <b>${esc(l.name)}</b>
        <div class="lmeta"><span class="chip on">${esc(l.ratio)}</span><span class="tiny muted">${esc(l.size)}</span></div>
        <div class="tiny muted" style="margin:7px 0 9px;line-height:1.75">${esc(l.summary || '')}<br>组成：${esc(l.compose || '')}</div>
        <div class="row"><button class="btn sm" data-sheet-preview="${esc(l.id)}">预览整条提示词</button>
          <button class="btn sm ghost" data-sheet-use="${esc(l.id)}">填入创作台</button></div></div>`).join('')}</div>
      <div class="tiny muted" style="margin-top:10px;line-height:1.8">角色行写法：${esc(hints.character || '')}<br>
        <span style="color:#8a3a25">反例：${esc(hints.characterBad || '')}</span></div>
    </div>

    <div class="panel"><h3>通用块 <span class="tag">一字不改也能整段搬</span></h3>
      <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">${esc(lib.note || '')}</div>
      ${Object.entries(blocks).map(([key, block]) => `<div class="sheet-block">
        <div class="bh">${esc(block.name || key)}<span class="mono">${esc(key)}</span></div>
        <textarea data-blk="${esc(key)}" spellcheck="false" rows="4">${esc(block.text)}</textarea>
        ${block.hint ? `<div class="tiny muted" style="margin-top:4px;line-height:1.7">${esc(block.hint)}</div>` : ''}</div>`).join('')}
    </div>

    <div class="panel"><h3>线稿风格句 <span class="tag">${lines.length} 条</span></h3>
      <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">线稿版把「风格：」整行换成这一段；角色描述里别写颜色词。</div>
      ${lines.map(s => `<div class="sheet-block"><div class="bh">${esc(s.name)}<span class="mono">${esc(s.id)}</span></div>
        <textarea data-ls="${esc(s.id)}" spellcheck="false" rows="3">${esc(s.text)}</textarea></div>`).join('')}
    </div>

    <div class="panel"><h3>风格预设 <span class="tag">${styles.length} 类</span></h3>
      <div class="tiny muted" style="margin:-2px 0 8px;line-height:1.8">画种 + 材质笔触 + 色调 + 光线，一两句；不要写场景环境（会和纯白底打架）。</div>
      ${styles.map((s, i) => `<div class="style-row"><input class="input sname" data-stn="${i}" value="${esc(s.name)}">
        <input class="input" data-stt="${i}" value="${esc(s.text)}"></div>`).join('')}
    </div>

    <div class="panel"><h3>已实测组合 <span class="tag">${presets.length} 条</span></h3>
      <table class="map-table presets"><thead><tr><th>编号</th><th>组合</th><th>版式</th><th>画法</th><th>参考图</th><th>出图</th><th>实测</th><th></th></tr></thead>
        <tbody>${presets.map(p => `<tr><td class="mono" data-l="编号">${esc(p.id)}</td><td data-l="组合">${esc(p.name)}</td>
          <td class="tiny" data-l="版式">${esc(sheetLayoutName(p.layout))}</td><td class="tiny" data-l="画法">${p.ink === 'line' ? '线稿' : '彩色'}</td>
          <td class="tiny" data-l="参考图">${esc(SHEET_REF_NAME[p.ref] || p.ref)}</td><td class="mono tiny" data-l="出图">${esc(p.size)}</td>
          <td class="tiny muted" data-l="实测">${esc(p.tested || '')}</td>
          <td><button class="btn sm ghost" data-sheet-use="${esc(p.layout)}|${esc(p.ink)}|${esc(p.ref)}">填入</button></td></tr>`).join('')}
        </tbody></table>
    </div>

    <div class="row pfoot"><span class="tiny muted" style="flex:1;line-height:1.8">${esc(hints.lineWarning || '')}</span>
      <button class="btn primary" data-sheetlib-save>保存提示词库</button></div>`;
}

async function openSheetPreviewModal(layoutId) {
  // 管理台预览用文档里的示例角色 + 第一类画风，肉眼确认拼出来的顺序对不对
  const lib = sheetLib(), hints = lib.variable_hints || {};
  const style = (lib.styles || [])[0] || { name: '写实摄影' };
  const body = { layout: layoutId, character: hints.characterGood || '示例角色', style: style.name, ink: 'color', ref: 'none' };
  const res = await api('/api/sheet/preview', { body });
  const text = res.ok ? (res.data.prompt || '') : '';
  openModal(`<div class="m-head"><h3>${esc(sheetLayoutName(layoutId))} · 整条提示词</h3><button class="btn ghost sm" data-close>✕</button></div>
    <div class="m-body">${res.ok
      ? `<div class="tiny muted" style="margin-bottom:8px">${esc((res.data.size || ''))} · ${esc(res.data.wf || '')} · 共 ${String(text.length)} 字</div>
         <pre class="promptfull">${esc(text)}</pre>`
      : `<div class="brokenbox">组装失败：${esc(res.reason || '未知原因')}</div>`}</div>
    <div class="m-foot"><span class="tiny muted" style="flex:1">示例角色来自提示词库里的「好例子」</span>
      <button class="btn sm" data-close>关闭</button>
      ${res.ok ? '<button class="btn sm primary" id="copyLibPrompt">复制</button>' : ''}</div>`);
  const cp = $('#copyLibPrompt');
  if (cp) cp.onclick = async () => {
    try { await navigator.clipboard.writeText(text); toast('已复制'); } catch (e) { toast('复制失败，手动复制一下'); }
  };
}

function pageService() {
  const s = S.service;
  return `<h2>服务设置</h2><div class="sub">改完点保存，立刻生效</div>
    <div class="panel"><h3>连接</h3>
      <div class="field"><label>ComfyUI 地址</label><div class="row"><input class="input mono" value="${esc(s.comfy)}" data-s="comfy"><button class="btn sm" id="testConn">测试连接</button></div>
        <div class="hint">就是浏览器里打开 ComfyUI 的那个地址。</div></div>
      <div class="field"><label>本应用端口</label><input class="input" value="${esc(s.port)}" data-s="port" style="width:140px">
        <div class="hint">创作台和管理台的访问地址：http://127.0.0.1:${esc(s.port)}；端口改动重启后生效</div></div>
      <div class="field"><label class="row" style="gap:10px"><span class="switch ${s.bindLan ? 'on' : ''}" data-sw="bindLan"></span>允许本机以外访问（手机/局域网）</label></div>
    </div>
    <div class="panel"><h3>成品存放</h3>
      <div class="field"><label>成果目录</label>
        <div class="row"><input class="input mono" value="${esc(s.archiveDir)}" data-s="archiveDir">
          <button class="btn sm" id="openArchive">打开成果目录</button></div>
        <div class="hint">默认在 ComfyUI 的 output 下；可以指定任意有写权限的目录，生成成功后成品按能力名归拢进去。</div></div>
      <div class="field"><label>成品会这样放</label><div class="path-tree mono">
        <div><span class="d">output/</span></div><div>&nbsp;├─ <span class="d">ComfyBridgeOutput/</span></div>
        <div>&nbsp;│&nbsp;&nbsp; ├─ <span class="d">图片生成/</span></div><div>&nbsp;├─ <span class="muted">（原有的其他文件）</span></div>
      </div></div>
      <div class="tiny muted" style="line-height:1.85">子目录按能力自动建。生成成功后自动移进来；万一移不进去，图照样给你。</div>
    </div>
    <div class="panel"><h3>路径</h3>
      <div class="field"><label>工作流目录</label><input class="input mono" value="${esc(s.wfDir)}" data-s="wfDir"><div class="hint">工作流 json 放这个目录</div></div>
      <div class="field"><label>上传目录</label><input class="input mono" value="${esc(s.uploadDir)}" data-s="uploadDir"><div class="hint">参考图先存这儿，单个上限 ${esc(s.uploadMaxMB)}MB</div></div>
      <div class="field"><label>任务记录目录</label><input class="input mono" value="${esc(s.taskDir)}" data-s="taskDir"><div class="hint">每批生成的结果记在这儿</div></div>
    </div>
    <div class="panel"><h3>存储与清理</h3>
      <div class="row" style="gap:20px;flex-wrap:wrap">
        <div class="field" style="margin:0"><label>任务记录保留</label><input class="input" value="${esc(s.keepDays)}" data-s="keepDays" style="width:110px"></div>
        <div class="field" style="margin:0"><label>最多保留任务数</label><input class="input" value="${esc(s.maxRecords)}" data-s="maxRecords" style="width:110px"></div>
        <div class="field" style="margin:0"><label>当前占用</label><div class="tiny" id="storageText" style="padding-top:8px;line-height:1.7">读取中…</div></div>
      </div><div class="tiny muted" style="margin-top:10px">只清任务记录和上传缓存，<b>不删成品</b>。</div>
    </div>
    <div class="row"><span class="tiny muted" style="flex:1"></span>
      <button class="btn" data-discard>放弃修改</button><button class="btn primary" data-savecfg>保存并生效</button></div>`;
}
async function loadStorage() {
  // 需求 04 章：管理台显示各目录占用，不再是一句静态说明
  const box = $('#storageText'); if (!box) return;
  const res = await api('/api/storage');
  if (!res.ok) { box.textContent = '读取失败'; return; }
  const d = res.data || {}, t = (k) => esc((d[k] || {}).text || '—');
  box.innerHTML = `上传 ${t('upload')}<br>任务记录 ${t('tasks')}<br>档位拷贝 ${t('copies')}<br>成品 ${t('archive')}`;
}
function pageDiag() {
  const rows = S.diagRows || [];
  return `<div class="spread" style="margin-bottom:6px"><div><h2>自检</h2><div class="sub">查连接和存放目录</div></div>
    <button class="btn primary" id="rerun">重新自检</button></div>
    <div class="panel"><div class="row" style="gap:10px;margin-bottom:12px">
      <span class="chip ${rows.length && rows.every(r => r.ok) ? 'green' : 'gray'}">${rows.filter(r => r.ok).length} 项正常</span></div>
      ${rows.length ? rows.map(r => `<div class="diag-row ${r.ok ? 'ok' : 'warn'}">
        <span class="dot ${r.ok ? 'g' : 'r'}"></span><div style="flex:1;min-width:0"><b>${esc(r.name)}</b>
        <div class="tiny muted" style="margin-top:2px">${esc(r.detail)}</div></div></div>`).join('')
        : '<div class="tiny muted">点「重新自检」查一下。</div>'}</div>`;
}

/* ------------------------------------------------------------ 管理台交互 */
function bindAdmin() {
  // 当前正在看的子工作流（页面上切「子功能」就是切它）
  const wfForEditing = () => {
    const cap = currentCap();
    return cap && ((cap.workflows || [])[UI.editingWfIndex] || (cap.workflows || [])[0]);
  };
  $$('[data-page]').forEach(el => el.onclick = async () => {
    // 先立旗再动手。切页中间要拉接口、写草稿，都是 await；这期间每秒一次的轮询
    // 会拿服务端「还没改」的 adminPage 把页面拽回去。自检页要连 ComfyUI，那边一慢
    // （3 秒超时）就够轮询抢跑三次，表现为「点了自检又弹回服务设置」。
    UI._navPending = true;
    UI.adminPage = el.dataset.page;
    UI.navOpen = false;                    // 手机上选完页把抽屉收起来
    if (UI.adminPage === 'diag') { const res = await api('/api/diag'); S.diagRows = res.data || []; }
    await saveDraft({ adminPage: UI.adminPage });
    UI._navPending = false;
    render();
  });
  $$('[data-toggle]').forEach(el => el.onclick = async () => {
    const res = await api('/api/capabilities/toggle/' + el.dataset.toggle, { body: {} });
    if (!res.ok) toast(res.reason || '没改成'); await pull(); render();
  });
  // 从能力卡片的「配置」进来：必须等 adminPage 真的写回服务端再渲染，
  // 否则下一次 pull 会用旧值把页面拽回列表页。
  $$('[data-edit]').forEach(el => el.onclick = async () => {
    UI.capId = el.dataset.edit; UI.adminPage = 'grades'; UI.editingWfIndex = 0;
    UI._navPending = true;
    await saveDraft({ route: 'admin', adminPage: 'grades', editingWfIndex: 0 });
    UI._navPending = false;
    render();
  });
  // 侧栏「档位表」的子菜单：直接跳到某个能力的档位表
  $$('[data-grades-cap]').forEach(el => el.onclick = async () => {
    UI.capId = el.dataset.gradesCap;
    UI.adminPage = 'grades';
    UI.editingWfIndex = 0;
    UI.comboSel = '';                     // 换能力就别留着上一个能力的组合
    UI.gOpen = 0; UI.comboDetail = null;
    UI.navOpen = false;
    UI._navPending = true;
    await saveDraft({ route: 'admin', adminPage: 'grades', capId: UI.capId, editingWfIndex: 0 });
    UI._navPending = false;
    render();
  });
  $$('[data-wfswitch]').forEach(el => el.onclick = () => { UI.editingWfIndex = +el.dataset.wfswitch; render(); });
  // 与角色设定图那两块同语义：重读档位 = 重新拉一遍状态；去创作台 = 把这条工作流的默认档带过去
  $$('[data-wfrefresh]').forEach(el => el.onclick = async () => {
    UI.effCache = {};
    await pull(); render(); toast('已重读档位');
  });
  $$('[data-wfstudio]').forEach(el => el.onclick = async () => {
    const w = wfForEditing();
    const cap = currentCap();
    if (!w || !cap) return;
    const grades = w.grades || [];
    // 用「模式」把子工作流带过去（模式 → 工作流的映射在 config 的 rules 里，别在前端写死名字）
    UI.mode = /参考|编辑|图生/.test(w.name) ? 'i2i' : 't2i';
    UI.capId = cap.id;
    UI.params.grade = (grades[0] || {}).name || '默认';
    UI.route = 'studio';
    UI._userSwitched = true; UI._draftCap = cap.id;
    UI._navPending = true;
    await saveDraft({ route: 'studio', capId: cap.id });
    UI._navPending = false;
    render();
    toast(`已切到创作台：${cap.name} · ${w.name}`);
  });
  // 设定图能力走「组合 × 档位」页：下面这一整套是旧版「档位 = 工作流档位」的绑定，
  // 它的 data-gdel / data-gedit / data-gval 选择器会连带命中组合页上同名属性的按钮，
  // 把删除、编辑处理器覆盖成旧逻辑（点下去走的是另一套 API，表现就是点了没反应）。
  // 所以这里必须直接 return，不能让旧绑定跑下去。
  if (UI.adminPage === 'grades' && isSheetCap(currentCap())) { bindComboGrades(); return; }
  const wf = wfForEditing;
  // 事件必须绑到真正的 checkbox：label 没有 checked 属性，绑在 label 上会永远读到 undefined，
  // 表现就是「点了没反应」。
  $$('[data-picknode] input[type=checkbox]').forEach(box => box.onchange = async () => {
    const el = box.closest('[data-picknode]');
    const w = wf(); if (!w || !el) return;
    const id = String(el.dataset.picknode), fs = nodeFields(id, w.name);
    const fields = (w.fields || []).filter(f => String(f.node) !== id);
    if (box.checked) fs.forEach(f => fields.push({ node: id, field: f }));
    const res = await api('/api/workflows/' + encodeURIComponent(w.name), { body: { fields } });
    if (!res.ok) toast(res.reason || '没保存'); await pull(); render();
  });
  $$('[data-unpicknode]').forEach(el => el.onclick = async () => {
    const w = wf(); if (!w) return;
    const fields = (w.fields || []).filter(f => String(f.node) !== String(el.dataset.unpicknode));
    await api('/api/workflows/' + encodeURIComponent(w.name), { body: { fields } }); await pull(); render();
  });
  $$('[data-drf]').forEach(el => {
    const [node, field] = el.dataset.drf.split('|');
    const read = () => {
      UI.draft.values = UI.draft.values || {};
      if (el.dataset.switch) UI.draft.values[field] = el.classList.contains('on');
      else if (el.classList.contains('multi')) UI.draft.values[field] = $$('input:checked', el).map(x => x.value);
      else UI.draft.values[field] = coerce(el, field);
    };
    if (el.dataset.switch) el.onclick = () => { const on = !el.classList.contains('on'); el.classList.toggle('on', on); el.setAttribute('aria-checked', String(on)); const em = el.querySelector('em'); if (em) em.textContent = on ? '开' : '关'; read(); };
    else el.onchange = read;
  });
  const dn = $('[data-draftname]'); if (dn) dn.oninput = () => { UI.draft.name = dn.value; };
  $$('[data-stop]').forEach(el => el.onclick = e => e.stopPropagation());
  $$('[data-gtoggle]').forEach(el => el.onclick = e => { e.stopPropagation(); const i = +el.dataset.gtoggle; UI.gOpen = (UI.gOpen | 0) === i ? -1 : i; render(); });
  // 需求 #4：已有档位拖拽排序（默认档固定不可拖，只排非默认档；顺序即创作台档位按钮顺序）
  let dragFrom = -1;
  $$('[data-gdrag]').forEach(h => {
    h.ondragstart = e => {
      dragFrom = +h.dataset.gdrag;
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', String(dragFrom)); } catch (_) { /* 忽略 */ }
      const row = h.closest('.grow'); if (row) row.classList.add('dragging');
    };
    h.ondragend = () => { dragFrom = -1; $$('.grow').forEach(r => r.classList.remove('dragging', 'dropover')); };
  });
  $$('[data-grow]').forEach(row => {
    row.ondragover = e => { if (dragFrom < 0) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; row.classList.add('dropover'); };
    row.ondragleave = () => row.classList.remove('dropover');
    row.ondrop = async e => {
      e.preventDefault(); row.classList.remove('dropover');
      const w = wf(); if (!w || dragFrom < 0) return;
      const grades = (w.grades || []).slice();
      const fromName = grades[dragFrom] && grades[dragFrom].name;
      const movable = grades.filter(g => !g.locked).map(g => g.name);
      const fi = movable.indexOf(fromName); if (fi < 0) return;
      const to = +row.dataset.grow;
      const toName = grades[to] && grades[to].name;
      let ti = (grades[to] && grades[to].locked) ? 0 : movable.indexOf(toName);
      if (ti < 0) ti = movable.length - 1;
      dragFrom = -1;
      if (ti === fi && grades[to] && grades[to].name === fromName) return;
      movable.splice(fi, 1);
      movable.splice(Math.min(ti, movable.length), 0, fromName);
      const res = await api('/api/workflows/' + encodeURIComponent(w.name) + '/grades/reorder', { body: { order: movable } });
      if (!res.ok) { toast(res.reason || '排序没保存'); return; }
      UI.gOpen = -1; await pull(); render(); toast('档位顺序已更新');
    };
  });
  $$('[data-gfield]').forEach(el => el.onchange = async () => {
    const [k, i] = el.dataset.gfield.split('|'), w = wf(); if (!w || !w.grades[+i] || w.grades[+i].locked) return;
    const g = w.grades[+i];
    const params = collectGradeParams(w, g);
    // name 用来定位那一档，newName 才是新名字；混在一起会让后端找不到这一档
    const gp = { name: g.name, note: k === 'note' ? el.value : (g.note || ''), params };
    if (k === 'name') gp.newName = el.value;
    const res = await api('/api/workflows/' + encodeURIComponent(w.name), { body: { gradePatch: gp } });
    if (!res.ok) toast(res.reason || '没保存'); await pull(); render();
  });
  $$('[data-gval]').forEach(el => {
    const [field, i] = el.dataset.gval.split('@'), w = wf(); if (!w || !w.grades[+i] || w.grades[+i].locked) return;
    const commit = async () => {
      const g = w.grades[+i];
      const params = collectGradeParams(w, g);
      const picked = (w.fields || []).find(f => f.field === field);
      if (!picked) return;
      // 档位拷贝里的参数是按节点分组的 {node: {field: value}}，不能塞扁平键
      const nodeId = String(picked.node);
      params[nodeId] = params[nodeId] || {};
      params[nodeId][field] = coerce(el, field);
      const res = await api('/api/workflows/' + encodeURIComponent(w.name), { body: { gradePatch: { name: g.name, note: g.note || '', params } } });
      if (!res.ok) { toast(res.reason || '没保存'); return; }
      UI.effCache = {};
      await pull(); render();
      const eff = await api(`/api/workflows/${encodeURIComponent(w.name)}/grades/${encodeURIComponent(g.name)}/effective`);
      toast(eff.ok ? `已生效 · 出图 ${eff.data.size.label}` : '已保存');
    };
    // 开关是 span（没有 change 事件），多选容器的 change 会冒泡上来
    if (el.dataset.switch) {
      el.onclick = () => {
        const on = !el.classList.contains('on');
        el.classList.toggle('on', on);
        el.setAttribute('aria-checked', String(on));
        const em = el.querySelector('em'); if (em) em.textContent = on ? '开' : '关';
        commit();
      };
    } else el.onchange = commit;
  });
  $$('[data-gdel]').forEach(el => el.onclick = async e => {
    e.stopPropagation(); const w = wf(); if (!w) return;
    const i = +el.dataset.gdel; if (!w.grades[i] || w.grades[i].locked) return;
    const res = await api('/api/workflows/' + encodeURIComponent(w.name) + '/grades/' + encodeURIComponent(w.grades[i].name), { method: 'DELETE' });
    if (!res.ok) toast(res.reason || '删不掉'); UI.gOpen = Math.min(i, w.grades.length - 2); await pull(); render();
  });
  const ga = $('[data-gadd]'); if (ga) ga.onclick = async () => {
    const w = wf(); if (!w) return;
    const name = String(UI.draft.name || '').trim();
    if (!name) { toast('先给这一档起个名字'); const box = $('#newGradeName'); if (box) box.focus(); return; }
    const params = {};
    for (const [field, value] of Object.entries(UI.draft.values || {})) {
      const node = (w.fields || []).find(f => f.field === field);
      if (node) { (params[node.node] = params[node.node] || {})[field] = value; }
    }
    const res = await api('/api/workflows/' + encodeURIComponent(w.name) + '/grades', { body: { name, note: '', params } });
    if (!res.ok) { toast(res.reason || '没加上'); return; }
    UI.draft = { name: '', values: {} }; UI.gOpen = w.grades.length;
    await pull(); render(); toast('已添加档位「' + name + '」');
  };
  // 「保存」不能只是弹个提示：把当前展开那一档的所有控件值真正提交，
  // 再用服务端的参数自检结果告诉用户改了什么、出图多大。
  $$('[data-savewf]').forEach(el => el.onclick = async () => {
    const w = wf(); if (!w) return;
    const open = UI.gOpen | 0;
    // 需求 #2：只读的默认档不该拖累其它档保存。这里一次保存所有「可改档」，
    // 跳过锁定的默认档；当前展开的那档用 DOM 实时值，其余用已有参数（幂等重存）。
    const targets = (w.grades || []).map((g, i) => ({ g, i })).filter(x => !x.g.locked);
    if (!targets.length) { toast('没有可保存的档位（默认档是只读的）'); return; }
    let saved = 0, failed = 0;
    for (const { g, i } of targets) {
      const params = i === open ? collectOpenGrade(w, i) : collectGradeParams(w, g);
      const res = await api('/api/workflows/' + encodeURIComponent(w.name),
        { body: { gradePatch: { name: g.name, note: g.note || '', params } } });
      if (res.ok) saved++; else failed++;
    }
    await pull();
    UI.effCache = {};
    // 若当前展开的是可改档，用它自检结果告诉用户出图尺寸
    const cur = targets.find(x => x.i === open);
    let label = '';
    if (cur) {
      const eff = await api(`/api/workflows/${encodeURIComponent(w.name)}/grades/${encodeURIComponent(cur.g.name)}/effective`);
      if (eff.ok) label = eff.data.allOk ? ` · 出图 ${eff.data.size.label}` : ' · 有参数没生效';
    }
    toast(failed ? `已保存 ${saved} 档，${failed} 档没存上` : `已保存 ${saved} 档${label}`);
    render();
  });
  $$('[data-savecfg]').forEach(el => el.onclick = async () => {
    const patch = {};
    $$('[data-s]').forEach(input => {
      const k = input.dataset.s; let v = input.value;
      if (['port', 'uploadMaxMB', 'keepDays', 'maxRecords'].includes(k)) v = +v;
      patch[k] = v;
    });
    const res = await api('/api/config', { body: { patch } });
    if (!res.ok) { toastBlocked('没保存', res.reason || ''); return; }
    await pull(); render(); toast('已保存，改动立刻生效');
  });
  $$('[data-sw]').forEach(el => el.onclick = async () => {
    const k = el.dataset.sw, patch = { [k]: !S.service[k] };
    const res = await api('/api/config', { body: { patch } });
    if (!res.ok) { toast(res.reason || '没保存'); return; }
    await pull(); render();
  });
  $$('[data-discard]').forEach(el => el.onclick = () => { render(); toast('已恢复上次保存的样子'); });
  const oa = $('#openArchive');
  if (oa) oa.onclick = async () => {
    const res = await api('/api/open-folder', { body: { path: S.service.archiveDir } });
    if (!res.ok) toast(res.reason || '打不开这个目录');
  };
  // ---- 设定图模板页：保存提示词库 / 预览 / 填入创作台 ----
  $$('[data-sheetlib-save]').forEach(el => el.onclick = async () => {
    const lib = sheetLib();
    const blocks = {};
    $$('[data-blk]').forEach(t => {
      blocks[t.dataset.blk] = { ...((lib.blocks || {})[t.dataset.blk] || {}), text: t.value.trim() };
    });
    const line_styles = $$('[data-ls]').map(t => {
      const old = (lib.line_styles || []).find(x => x.id === t.dataset.ls) || {};
      return { id: t.dataset.ls, name: old.name || t.dataset.ls, text: t.value.trim() };
    });
    const styles = $$('[data-stn]').map(n => ({
      name: (n.value || '').trim(),
      text: (($('[data-stt="' + n.dataset.stn + '"]') || {}).value || '').trim(),
    })).filter(s => s.name && s.text);
    const res = await api('/api/sheet/library', { method: 'PUT', body: { blocks, line_styles, styles } });
    if (!res.ok) { toastBlocked('提示词库没保存', res.reason || ''); return; }
    await pull(); render();
    toast('提示词库已保存，创作台立刻生效');
  });
  $$('[data-sheetlib-reload]').forEach(el => el.onclick = async () => {
    await pull(); render(); toast('已重读提示词库');
  });
  $$('[data-sheet-preview]').forEach(el => el.onclick = () => openSheetPreviewModal(el.dataset.sheetPreview));
  $$('[data-sheet-use]').forEach(el => el.onclick = async () => {
    // 从管理台把某个版式/组合直接填进创作台：切能力 → 切模式 → 写真表单 → 存盘 → 跳页
    const [layout, ink, ref] = String(el.dataset.sheetUse || '').split('|');
    if (layout) UI.sheetForm.layout = layout;
    if (ink) UI.sheetForm.ink = ink;
    if (ref) UI.sheetForm.ref = ref;
    UI.mode = ref && ref !== 'none' ? 'i2i' : 't2i';
    UI.capId = 'sheet.character';
    UI._draftCap = 'sheet.character';        // 防止随后的轮询用旧草稿把表单覆盖回去
    UI._userSwitched = true;
    UI.route = 'studio';
    UI.sheetPreview = null;
    await saveDraft({ route: 'studio' });
    await pull(); render();
    toast('已按这个组合切到创作台');
  });
  if (UI.adminPage === 'service') loadStorage();
  $('#testConn') && ($('#testConn').onclick = async () => {
    const btn = $('#testConn'); btn.textContent = '连接中…';
    const res = await api('/api/v1/health'); btn.textContent = '测试连接';
    toast(res.comfy_online ? '连接成功：队列空闲' : '连接失败：请先启动 ComfyUI');
  });
  const rr = $('#rerun'); if (rr) rr.onclick = async () => { const res = await api('/api/diag'); S.diagRows = res.data || []; render(); };
}
function coerce(el, field) {
  if (el.dataset.switch) return el.classList.contains('on');
  if (el.classList.contains('multi')) return $$('input:checked', el).map(x => x.value);
  const t = el.dataset.ftype || fieldMeta('', field).type;
  const v = (el.value || '').trim();
  if (t === 'int') return v === '' ? '' : Math.round(+v);
  if (t === 'float') return v === '' ? '' : +v;
  if (t === 'bool') return v === 'true' || v === 'on';
  return v;
}
function collectGradeParams(w, g) {
  // 前端展示的是扁平参数，写回服务端时按被勾节点归类
  const out = {};
  for (const [field, value] of Object.entries(g.params || {})) {
    const node = (w.fields || []).find(f => f.field === field);
    const id = node ? String(node.node) : (w.fields[0] ? String(w.fields[0].node) : '');
    if (id) (out[id] = out[id] || {})[field] = value;
  }
  return out;
}
function splitGVal(v) {
  const at = String(v || '').lastIndexOf('@');
  return at < 0 ? ['', -1] : [v.slice(0, at), +v.slice(at + 1)];
}
function collectOpenGrade(w, i) {
  // 从当前展开的编辑区读取所有控件（含 select / 开关 / 多选），按节点归类。
  // 只保留「与母版不同」的值：档位的语义就是改动项。这样既不会把空值
  // （例如原本是连线的 width/height）写坏工作流，也不会让参数表无限膨胀。
  const params = {};
  const original = w.original || {};
  $$('[data-gval]').forEach(el => {
    const [field, gi] = splitGVal(el.dataset.gval);
    if (gi !== i || !field) return;
    const picked = (w.fields || []).find(f => f.field === field);
    if (!picked) return;
    const value = coerce(el, field);
    if (value === '' || value === undefined || value === null) return;
    if (original[field] !== undefined && String(value) === String(original[field])) return;
    const nodeId = String(picked.node);
    (params[nodeId] = params[nodeId] || {})[field] = value;
  });
  return params;
}
async function showGradeEffective() {
  // 逐项显示「配置值 → 实际提交值」，用户不用出图也能确认参数有没有生效
  if (UI.route !== 'admin' || UI.adminPage !== 'grades') return;
  const box = $('#gradeEff'); if (!box) return;
  const cap = currentCap();
  const w = cap && (cap.workflows[UI.editingWfIndex] || cap.workflows[0]);
  const g = w && (w.grades || [])[UI.gOpen | 0];
  if (!w || !g) return;
  const key = `${w.name}|${g.name}|${JSON.stringify(g.params || {})}`;
  UI.effCache = UI.effCache || {};
  let eff = UI.effCache[key];
  if (!eff) {
    const res = await api(`/api/workflows/${encodeURIComponent(w.name)}/grades/${encodeURIComponent(g.name)}/effective`);
    if (!res.ok) { box.textContent = '参数自检读不到'; return; }
    eff = res.data; UI.effCache[key] = eff;
  }
  if (!$('#gradeEff')) return;
  const rows = eff.rows.map(r => `<div class="effrow ${r.ok ? '' : 'bad'}"><span class="mono">${esc(r.field)}</span>
    <span>${esc(String(r.want))} → ${esc(String(r.got))}</span><b>${r.ok ? '✓' : '✕ ' + esc(r.reason || '')}</b></div>`).join('');
  box.innerHTML = `<div class="effhd ${eff.allOk ? 'ok' : 'bad'}">${eff.allOk ? '✓ 参数全部生效' : '✕ 有参数没生效'}
    <span class="tiny muted">出图 ${esc(eff.size.label)}</span></div>
    ${rows || '<div class="tiny muted">这一档没改参数，用工作流原值。</div>'}`;
}

/* ------------------------------------------------------------ 历史 */
function viewGallery() {
  const done = (S.jobs || []).filter(j => doneRuns(j).length);
  const caps = ['全部', ...new Set(done.map(j => j.capName))];
  const dates = ['全部', '今天', '近 7 天'];
  const day = 86400000;
  const shown = done.filter(j => (UI.galleryFilter === '全部' || j.capName === UI.galleryFilter)
    && (UI.galleryDate === '全部' || (UI.galleryDate === '今天' ? Date.now() - j.t0 < day : Date.now() - j.t0 < 7 * day)));
  const tiles = [];
  shown.forEach(j => doneRuns(j).forEach(r => tiles.push({ j, r })));
  const keys = tiles.map(({ r }) => r.id + '|0');
  const allSelected = keys.length > 0 && keys.every(k => UI.selected.has(k));
  return `<div class="admin">${viewTopbar()}${configBanner()}<div class="admin-main" style="padding-top:22px"><div class="inner" style="max-width:1100px">
    <div class="spread ghead"><div class="gtitle"><h2>历史记录</h2><div class="sub">生成过的成品都在这儿</div></div>
      <div class="row gfilters"><select class="input gf" id="gfilter">${caps.map(c => `<option ${c === UI.galleryFilter ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
        <select class="input gd" id="gdate">${dates.map(c => `<option ${c === UI.galleryDate ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
        <label class="gall"><input type="checkbox" id="gall" ${allSelected ? 'checked' : ''}> 全选</label>
        <button class="btn" id="batchDl">⬇ 批量下载（${UI.selected.size}）</button>
        <button class="btn danger" id="gDel">🗑 删除（${UI.selected.size}）</button></div></div>
    ${tiles.length ? `<div class="gallery">${tiles.map(({ j, r }) => {
      const key = r.id + '|0';
      return `<div class="card" style="overflow:hidden;cursor:pointer" data-zoom="${j.id}|${r.id}">
        <label class="gcheck" data-stop><input type="checkbox" data-sel="${key}" ${UI.selected.has(key) ? 'checked' : ''}></label>
        <button class="gdelone" data-delone="${key}" title="删除这张成品及本地文件">✕</button>
        <div class="art" style="border:0;border-radius:0;aspect-ratio:1/1;background:#f4f6fa"><img src="${imgUrl(r.id, 0)}" style="width:100%;height:100%;object-fit:contain"></div>
        <div style="padding:9px 11px"><div class="tiny" style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(j.brief || j.prompt)}</div>
          <div class="tiny muted" style="margin-top:2px">${esc(j.capName)}${!(S.capabilities || []).some(c => c.id === j.cap) ? ' · 能力已删除' : ''} · ${esc(r.grade || '默认')} · 种子 ${r.seed ?? j.seed ?? ''}</div></div></div>`;
    }).join('')}</div>` : '<div class="empty"><div style="font-size:30px">🗂</div><div class="tiny">这里还没有记录，先去创作台生成几张</div></div>'}
  </div></div></div>`;
}
function bindGallery() {
  const f = $('#gfilter'); if (f) f.onchange = () => { UI.galleryFilter = f.value; saveDraft({ galleryFilter: f.value }); render(); };
  const d = $('#gdate'); if (d) d.onchange = () => { UI.galleryDate = d.value; render(); };
  const visibleKeys = () => $$('[data-sel]').map(el => el.dataset.sel);
  const updateCounts = () => {
    const b = $('#batchDl'); if (b) b.textContent = `⬇ 批量下载（${UI.selected.size}）`;
    const g = $('#gDel'); if (g) g.textContent = `🗑 删除（${UI.selected.size}）`;
    const ga = $('#gall');
    if (ga) {
      const ks = visibleKeys(), n = ks.filter(k => UI.selected.has(k)).length;
      ga.checked = ks.length > 0 && n === ks.length;
      ga.indeterminate = n > 0 && n < ks.length;
    }
  };
  const ga = $('#gall');
  if (ga) ga.onchange = () => {
    $$('[data-sel]').forEach(el => {
      el.checked = ga.checked;
      if (ga.checked) UI.selected.add(el.dataset.sel); else UI.selected.delete(el.dataset.sel);
    });
    updateCounts();
  };
  $$('[data-sel]').forEach(el => el.onchange = () => {
    if (el.checked) UI.selected.add(el.dataset.sel); else UI.selected.delete(el.dataset.sel);
    updateCounts();
  });
  $$('[data-stop]').forEach(el => el.onclick = e => e.stopPropagation());
  $$('[data-zoom]').forEach(el => el.onclick = e => {
    if (e.target.tagName === 'INPUT' || e.target.closest('[data-delone]')) return;
    openZoom(el.dataset.zoom);
  });
  const toItems = keys => keys.map(k => {
    const [run, index] = k.split('|');
    return { run, index: +index };
  });
  const doDelete = async keys => {
    if (!keys.length) { toast('先选几张成品'); return; }
    openModal(`<div class="m-head"><h3>删除选中的 ${keys.length} 张成品？</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><div class="tiny" style="line-height:1.9">会<b>同时删除本地生成的对应文件</b>，删了就找不回来了。<br>
        主页面对话卡片里的这几张图也会一起移除；其余成品不受影响。<br>
        <span class="muted">（只想移走对话记录、保留文件，请到创作台右栏用「删除这条对话」。）</span></div></div>
      <div class="m-foot"><button class="btn sm" data-close>取消</button>
        <button class="btn sm danger" id="confirmHistoryDel">确认删除</button></div>`);
    $('#confirmHistoryDel').onclick = async () => {
      const res = await api('/api/history/delete', { body: { items: toItems(keys), deleteFiles: true } });
      if (!res.ok) { toast(res.reason || '没删成'); return; }
      closeModal();
      keys.forEach(k => UI.selected.delete(k));
      await pull(); render();
      const d = res.data || {};
      const parts = [`已移除 ${d.runs ?? keys.length} 条记录`];
      if (d.files) parts.push(`删掉 ${d.files} 个文件`);
      if (d.trashed) parts.push(`${d.trashed} 个移到了 data/trash`);
      if (d.blocked) parts.push(`${d.blocked} 个文件正被占用没删掉`);
      toast(parts.join(' · '));
    };
  };
  const gd = $('#gDel');
  if (gd) gd.onclick = () => {
    const visible = new Set(visibleKeys());
    doDelete([...UI.selected].filter(k => visible.has(k)));
  };
  $$('[data-delone]').forEach(el => el.onclick = e => { e.stopPropagation(); doDelete([el.dataset.delone]); });
  const b = $('#batchDl'); if (b) b.onclick = async () => {
    if (!UI.selected.size) { toast('先选几张成品'); return; }
    const items = [...UI.selected].map(k => { const [run, index] = k.split('|'); return { run, index: +index }; });
    const res = await fetch('/api/download/batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }) });
    if (!res.ok) { toast('打包没成功'); return; }
    const blob = await res.blob(), url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'ComfyBridge.zip'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };
  updateCounts();
}

/* ------------------------------------------------------------ 弹层 / 向导 */
function openModal(html, noMaskClose) {
  closeModal();
  const mask = document.createElement('div');
  mask.className = 'modal-mask'; mask.id = 'mask';
  mask.innerHTML = `<div class="modal ${html.length > 900 ? 'wide' : ''}">${html}</div>`;
  document.body.appendChild(mask);
  $$('[data-close]', mask).forEach(el => el.onclick = closeModal);
  mask.onclick = e => { if (e.target === mask && !noMaskClose) closeModal(); };
  return mask;
}
function closeModal() {
  const m = $('#mask');
  if (m) {
    if (m._onKey) document.removeEventListener('keydown', m._onKey);
    m.remove();
  }
}
function renderWizard() {
  const steps = [
    { t: '欢迎使用 ComfyBridge', d: '写一句描述就能出图。先确认两件事。', b: `<div class="field"><label>ComfyUI 地址</label><input class="input mono" value="${esc(S.service.comfy)}" readonly></div>
      <div class="field"><label>成果物放哪</label><input class="input mono" value="${esc(S.service.archiveDir)}" readonly></div>` },
    { t: '给能力排几档', d: '一个能力配几档，创作台上就只看到这几个按钮。', b: `<div class="step"><div class="num">1</div><div class="txt"><b>打开管理台 → 档位表</b><p>左边勾几个节点，右边就会出现这些参数。</p></div></div>
      <div class="step"><div class="num">2</div><div class="txt"><b>填一组值，起个名字，点「添加」</b><p>添加后创作台立刻多一个按钮。</p></div></div>` },
    { t: '可以开始出图了', d: '输入区只有描述框和一排档位。', b: '<div class="tiny muted">有参考图可以拖进输入框，也可以直接粘贴。</div>' },
  ];
  const st = steps[UI.wizardStep || 0];
  const html = `<div class="wizard-mask" id="wiz"><div class="wizard">
    <div class="w-head"><h3>${esc(st.t)}</h3><div class="tiny muted">${esc(st.d)}</div></div>
    <div class="w-body">${st.b}</div><div class="w-foot"><div class="dots">${steps.map((_, i) => `<i class="${i === (UI.wizardStep || 0) ? 'on' : ''}"></i>`).join('')}</div>
    <div class="row"><button class="btn sm ghost" id="wizSkip">跳过</button>
      ${(UI.wizardStep || 0) > 0 ? '<button class="btn sm" id="wizPrev">上一步</button>' : ''}
      <button class="btn sm primary" id="wizNext">${(UI.wizardStep || 0) < steps.length - 1 ? '下一步' : '开始使用'}</button></div></div></div></div>`;
  const exist = $('#wiz'); if (exist) exist.remove();
  document.body.insertAdjacentHTML('beforeend', html);
  const finish = async () => { UI.wizardSeen = true; await saveDraft({ wizardSeen: true }); $('#wiz') && $('#wiz').remove(); };
  $('#wizSkip').onclick = finish;
  const prev = $('#wizPrev'); if (prev) prev.onclick = () => { UI.wizardStep--; renderWizard(); };
  $('#wizNext').onclick = () => {
    if ((UI.wizardStep || 0) < steps.length - 1) { UI.wizardStep = (UI.wizardStep || 0) + 1; renderWizard(); }
    else { finish(); UI.route = 'admin'; UI.adminPage = 'caps'; render(); }
  };
}
function bindGlobal() {
  $$('[data-nav]').forEach(el => el.onclick = async () => {
    const t = el.dataset.nav;
    UI.sheet = false; UI.navOpen = false;
    if (t === 'admin') { UI.route = 'admin'; UI.adminPage = 'caps'; }
    else if (t === 'gallery') { UI.route = 'gallery'; UI.galleryFilter = '全部'; }
    else UI.route = 'studio';
    UI._navPending = true;
    await saveDraft({ route: UI.route });
    UI._navPending = false;
    render();
  });
  // 手机端：右栏（ComfyUI 状态 + 本会话任务）从右侧滑出；按钮/遮罩只切 class，不整页重绘
  const st = $('#sheetToggle');
  if (st) st.onclick = () => {
    UI.sheet = !UI.sheet;
    UI.navOpen = false;
    const app = $('.app'); if (app) { app.classList.toggle('sheet-open', UI.sheet); app.classList.remove('nav-open'); }
  };
  const sm = $('#sheetMask');
  if (sm) sm.onclick = () => {
    UI.sheet = false;
    const app = $('.app'); if (app) app.classList.remove('sheet-open');
  };
  $$('[data-sheet-close]').forEach(el => el.onclick = () => {
    UI.sheet = false;
    const app = $('.app'); if (app) app.classList.remove('sheet-open');
  });
  // 手机端：左侧抽屉（创作台的能力 / 管理台的菜单）
  const nt = $('#navToggle');
  if (nt) nt.onclick = () => {
    UI.navOpen = !UI.navOpen;
    UI.sheet = false;
    const box = $('.app') || $('.admin'); if (box) { box.classList.toggle('nav-open', UI.navOpen); box.classList.remove('sheet-open'); }
  };
  const sdm = $('#sideMask');
  if (sdm) sdm.onclick = () => {
    UI.navOpen = false;
    ($('.app') || $('.admin') || {}).classList?.remove('nav-open');
  };
  $$('[data-side-close]').forEach(el => el.onclick = () => {
    UI.navOpen = false;
    ($('.app') || $('.admin') || {}).classList?.remove('nav-open');
  });
  const hb = $('#healthBtn'); if (hb && UI.route !== 'studio') hb.onclick = async () => { await api('/api/v1/health'); await pull(); render(); };
  const fc = $('#fixConfig');
  if (fc) fc.onclick = async () => {
    const res = await api('/api/config/raw');
    if (!res.ok) { toast('读不到配置'); return; }
    openModal(`<div class="m-head"><h3>修改配置</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><textarea id="rawCfg" class="input" style="width:100%;height:360px;font-family:Consolas,monospace;font-size:12px">${esc(res.data)}</textarea></div>
      <div class="m-foot"><span class="tiny muted" style="flex:1">改完保存后会重新读取</span>
        <button class="btn sm" data-close>取消</button><button class="btn sm primary" id="saveRawCfg">保存并重读</button></div>`, true);
    $('#saveRawCfg').onclick = async () => {
      const r = await api('/api/config/raw', { method: 'PUT', body: { text: $('#rawCfg').value } });
      if (!r.ok) { toast(r.reason || '没保存'); return; }
      closeModal(); await pull(); render(); toast('配置已生效');
    };
  };
}
async function pull() { await fetchState(); }

async function autoDiag() {
  // 进自检页就查一次（两项结果 1 秒内出来），不用用户先点。
  // 判空必须看长度：空数组是 truthy，按 `S.diagRows` 判会让「上次查出来是空的」
  // 永久卡住，之后怎么进这一页都不再发起查询。
  if (!S || (S.diagRows && S.diagRows.length) || UI._diagLoading) return;
  UI._diagLoading = true;
  const res = await api('/api/diag');
  S.diagRows = res.data || [];
  UI._diagLoading = false;
  if (UI.route === 'admin' && UI.adminPage === 'diag') render();
}

/* ------------------------------------------------------------ 启动 */
// 地址栏改 hash / 浏览器前进后退时页面不会重载，必须自己响应，
// 否则输入 #admin/grades 看起来像「进不去那一页」。
window.addEventListener('hashchange', () => {
  if (!S) return;
  const locked = hashRoute();
  if (!locked) return;
  UI.route = locked.route;
  if (locked.adminPage) UI.adminPage = locked.adminPage;
  UI._navPending = true;
  saveDraft({ route: UI.route, adminPage: UI.adminPage })
    .finally(() => { UI._navPending = false; });
  render();
});

window.addEventListener('DOMContentLoaded', async () => {
  const ready = await fetchState();
  if (!ready) { document.body.innerHTML = '<div style="padding:40px;font-family:sans-serif">服务还没就绪，请稍后刷新。</div>'; return; }
  UI.route = S.ui.route || 'studio';
  // 允许用 #admin / #admin/grades / #gallery 直接进到某一页（不改变界面形态）
  const locked = hashRoute();
  if (locked) { UI.route = locked.route; if (locked.adminPage) UI.adminPage = locked.adminPage; }
  if (UI.route !== (S.ui.route || 'studio')) await saveDraft({ route: UI.route, adminPage: UI.adminPage });
  render();
  _pollTimer = setInterval(async () => {
    if (document.hidden) return;
    await fetchState();
    paintProgress();
    if (stateSignature() !== _lastSignature) refreshDynamic();
  }, 1000);
});
