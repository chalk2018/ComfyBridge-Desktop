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
  sheetForm: { layout: 'A', ink: 'color', lineStyle: '', ref: 'lock', grade: '', character: '', style: '', styleText: '', styleCustom: false },
  sheetOpen: false,                              // 「看完整提示词」面板是否展开
  sheetPreview: null,                            // 最近一次服务端组装结果
  // 1.03 场景/道具设定图表单：模式在 UI.mode 里，版式/参考图槽位/风格都在这里
  extForm: { kind: 'scene', layout: 'N', ink: 'color', nochar: 'yes', ref: 'none', grade: '默认',
    text: '', style: '', styleText: '', styleCustom: false, lineStyle: '', refSlots: {} },
  extOpen: false,
  extPreview: null,
  // 1.03 宫格分镜故事板：只有图生图，版式/画法/档位 + 逐格提示词
  boardForm: { layout: 'G4', ink: 'color', lineStyle: '', grade: '', sel: 0, cells: [] },
  boardOpen: false,
  boardPreview: null,
  comboSel: '',                                  // 管理台档位表当前选中的组合（C.color.lock 这种）
  comboDetail: null,                             // 该组合的档位参数生效核对结果
  gradeForm: null,                               // 新建/编辑档位的表单草稿
  selTasks: new Set(),                           // 本会话任务批量删除的勾选集合
  sheet: false,                                  // 手机端右栏抽屉（状态与任务）是否拉起
  navOpen: false,                                // 手机端左侧抽屉（能力 / 管理台菜单）是否拉起
  editingWfIndex: 0, gOpen: 0, draft: { name: '', values: {} },
  galleryFilter: '全部', galleryDate: '全部', selected: new Set(),
  wizardSeen: false, autoScroll: false, qJobId: null, qGrade: null,
  railMin: false, drawerMin: false,                // 左右栏折叠（任何尺寸都支持，手机以外默认展开）
  prevRoute: 'studio',                             // 进管理台之前在哪一页：返回时回到画布还是创作台
  _navPending: false, _draftPending: false,      // 导航/草稿写入在途标记（轮询别把旧值灌回来）
};
let _pollTimer = null;
let _pollBusy = false;                           // 上一次轮询没回来就跳过这一轮，别把请求堆起来
let _lastSignature = '';
let _lastJobCount = null;                        // 上一轮的任务数：多了一条就自动滚到最新块

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
  if (['studio', 'admin', 'gallery', 'canvas'].includes(h)) return { route: h };
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
  // 需求16：旧的四个音频能力 id 合并成 audio.generate，草稿里残留的旧 id 迁移过来
  if (/^audio\.(music|music\.ref|tts|clone)$/.test(UI.capId || '')) UI.capId = 'audio.generate';
  if (S.ui.galleryFilter) UI.galleryFilter = S.ui.galleryFilter;
  if (S.ui.wizardSeen) UI.wizardSeen = true;
  // 折叠状态跟着服务端走：换窗口 / 刷新后保持用户收起来的布局
  if (typeof S.ui.railMin === 'boolean') UI.railMin = S.ui.railMin;
  if (typeof S.ui.drawerMin === 'boolean') UI.drawerMin = S.ui.drawerMin;
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
  // 参考素材：1.04 起同一个能力里可能同时有图 / 视频 / 音频，草稿里带 kind；
  // 老草稿是纯 id 数组，按图片读（那时候只有图片）。
  UI.refs = Array.isArray(d.refs)
    ? d.refs.map(x => (typeof x === 'string' ? { id: x, kind: 'image' } : { id: x.id, kind: x.kind || 'image', name: x.name }))
    : [];
  UI.mode = (d.mode === 'i2i' || d.mode === 't2i' || d.mode === 'expand') ? d.mode : 't2i';
  UI.prompt = d.prompt || '';
  // 角色设定图：表单和「提示词面板」也一起恢复，切走再切回来不用重填。
  // 只在设定图能力下恢复——别的能力的草稿里没有 sheet 字段，
  // 拿它去覆盖会把「面板展开态」误清成 false、把表单拽回默认值。
  if (isSheetCap(currentCap())) {
    if (d.sheet && typeof d.sheet === 'object') UI.sheetForm = { ...UI.sheetForm, ...d.sheet };
    UI.sheetOpen = !!d.sheetOpen;
    UI.sheetPreview = null;
  }
  if (isExtSheetCap(currentCap())) {
    if (d.ext && typeof d.ext === 'object') UI.extForm = { ...UI.extForm, ...d.ext };
    UI.extOpen = !!d.extOpen;
    UI.extPreview = null;
    extForm();
  }
  if (isBoardCap(currentCap())) {
    if (d.board && typeof d.board === 'object') UI.boardForm = { ...UI.boardForm, ...d.board };
    UI.boardOpen = !!d.boardOpen;
    UI.boardPreview = null;
    boardForm();
  }
  // 1.04：视频 / 音频的表单也随草稿恢复（模式、比例、宫格、歌词都别丢）
  if (isVideoCap(currentCap()) && d.video && typeof d.video === 'object') {
    UI.video = { ...(UI.video || {}), ...d.video };
    videoForm();
  }
  if (isAudioCap(currentCap()) && d.audio && typeof d.audio === 'object') {
    UI.audio = { ...(UI.audio || {}), ...d.audio };
    audioForm();
  }
  if (currentCap() && currentCap().id === 'image.generate' && d.image && typeof d.image === 'object') {
    UI.imgForm = { ...(UI.imgForm || {}), ...d.image };
    imgForm();
  }
}
function currentDraftText() {
  const out = { prompt: serializeEditor(), refs: UI.refs.map(r => ({ id: r.id, kind: r.kind || 'image', name: r.name })),
                grade: UI.params.grade || '默认', mode: UI.mode };
  if (isSheetCap(currentCap())) {
    out.sheet = { ...UI.sheetForm };
    out.sheetOpen = !!UI.sheetOpen;
  }
  if (isExtSheetCap(currentCap())) {
    out.ext = { ...UI.extForm, refSlots: { ...(UI.extForm.refSlots || {}) } };
    out.extOpen = !!UI.extOpen;
  }
  if (isBoardCap(currentCap())) {
    out.board = { ...boardForm(), cells: [...boardForm().cells] };
    out.boardOpen = !!UI.boardOpen;
  }
  if (isVideoCap(currentCap())) out.video = { ...videoForm(), cells: [...videoForm().cells] };
  if (isAudioCap(currentCap())) out.audio = { ...audioForm() };
  if (currentCap() && currentCap().id === 'image.generate') out.image = { ...imgForm() };
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
/* 需求2：图片生成专用通用模板（主体无关），与角色设定图库严格分开 */
function imgStyleLib() { return (S && S.imageStyleLib) || { styles: [], line_styles: [] }; }
function imgStyles() { return (imgStyleLib().styles || []).map(s => s.name); }
function imgLineStyles() { return imgStyleLib().line_styles || []; }
function sheetLayoutOf(id) { return sheetLayouts().find(l => l.id === id) || null; }
function sheetForm() {
  // 库改过 / 档位被删时把表单拉回合法值，避免「版式不存在」这种低级报错
  const f = UI.sheetForm = UI.sheetForm || {};
  // 需求 #6：每次进入页面默认选「自定义…」风格。f.style 仍保留第一预设作为兜底，
  // 这样即使用户不填自定义句，sheetStyleValue() 也能回退到合法风格、不会提交空风格句。
  const defs = { layout: '', ink: 'color', lineStyle: '', ref: 'lock', grade: '', character: '', style: '', styleText: '', styleCustom: true, refChips: [] };
  Object.keys(defs).forEach(k => { if (f[k] === undefined || f[k] === null) f[k] = defs[k]; });
  const layouts = sheetLayouts();
  if (layouts.length && !layouts.some(l => l.id === f.layout)) f.layout = layouts[0].id;
  const lineIds = sheetLineStyles().map(s => s.id);
  if (!f.lineStyle || !lineIds.includes(f.lineStyle)) f.lineStyle = lineIds[0] || '';
  const styles = sheetStyles();
  // 空 style 可能只是「还没选过」，也可能是用户选了「自定义…」；后者靠 styleCustom 区分，
  // 不能再把空值悄悄改回第一个预设，否则下拉会自己跳回去、和实际提交的风格句对不上。
  if (!f.style && !f.styleCustom && styles.length) f.style = styles[0];
  return f;
}
function sheetLayout() { return sheetLayoutOf(sheetForm().layout) || sheetLayouts()[0] || null; }
/* 1.07：角色设定图顶部三种子功能。仍复用全局 UI.mode 的 t2i/i2i（上传门槛等共用逻辑不动），
   「锁定同角色 / 只借画风」用 sheetForm.ref 区分，不引入第三套全局状态。 */
function sheetMode() {
  const f = sheetForm();
  return UI.mode === 'i2i' ? (f.ref === 'style' ? 'style' : 'lock') : 't2i';
}
const SHEET_MODE_NAME = { t2i: '文生图', lock: '图生图-锁定同角色', style: '图生图-只借画风' };
/* 组合 = 版式 × 画法 × 参考方式：一个组合就是一条母版配方（提示词 + 工作流 + 画幅）。
   档位只在这个组合里可选，换组合时旧档位可能不存在，所以要回到「母版」。 */
function sheetCombos() { return (S && S.sheetCombos) || []; }
function sheetComboOf(layout, ink, ref) {
  return sheetCombos().find(c => c.layout === layout && c.ink === ink && c.ref === ref) || null;
}
function sheetCombo() {
  const f = sheetForm();
  return sheetComboOf(f.layout, f.ink, sheetMode() === 't2i' ? 'none' : f.ref);
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
  // 只有真的选了「自定义…」才用自由输入；没写内容时回退预设，别把空风格句提交出去。
  if (f.styleCustom && custom) return custom;
  return f.style || (sheetStyles()[0] || '');
}
function sheetPayload() {
  const f = sheetForm();
  return {
    layout: f.layout, character: String(f.character || '').trim(),
    // 风格只对文生图生效：文生图没有参考图，画风靠「风格」下拉；
    // 图生图两项靠参考图，服务端不会注入这里的风格（compose 按 ref 区分）。
    style: sheetStyleValue(), styleCustom: !!f.styleCustom, styleText: String(f.styleText || ''),
    ink: f.ink, lineStyle: f.ink === 'line' ? (f.lineStyle || null) : null,
    ref: sheetMode() === 't2i' ? 'none' : f.ref,
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
      去管理台 →「档位表 → 角色设定图」点「✎ 设定图模板」看一眼。</div></div></div></div>`;
  }
  const layout = sheetLayout();
  const grade = sheetGrade();
  const grades = sheetGrades();
  const smode = sheetMode(), i2i = smode !== 't2i';
  // 图生图纯转换允许空角色描述；文生图才要求写描述（需求 #8）
  const empty = !String(f.character || '').trim() && !i2i;
  const lineStyles = sheetLineStyles();
  // 风格下拉的当前值：选了「自定义…」时下拉落到自定义项、露出自由输入框
  const styles = sheetStyles();
  const customStyle = !!f.styleCustom;
  const curStyle = (!customStyle && styles.includes(f.style)) ? f.style : '';
  return `<div class="composer"><div class="composer-inner">
    <div class="parambar">
      <div class="modepick" role="group" aria-label="生成方式">
        <button class="${smode === 't2i' ? 'on' : ''}" data-sheet-mode="t2i"><span class="dotg"></span>文生图</button>
        <button class="${smode === 'lock' ? 'on' : ''}" data-sheet-mode="lock"><span class="dotb"></span>图生图-锁定同角色</button>
        <button class="${smode === 'style' ? 'on' : ''}" data-sheet-mode="style"><span class="dotb"></span>图生图-只借画风</button>
      </div>
      <span class="modedis">${smode === 't2i' ? '纯文字描述直接出设定图' : smode === 'lock' ? '图 1 就是这个人：不换人、不改性别' : '图 1 只借画风笔触，人物照文字新写'}</span>
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
            </select><span class="tiny muted">在原有画风基础上改成铅笔勾勒，风格保持不变</span>`
          : '<span class="tiny muted">彩色：风格行用下面的「风格」</span>'}
      </div>
      ${!i2i ? `<div class="sheetbar">
        <span class="slb">风格</span>
        <select class="input segsel" id="sheetStyle" style="min-width:148px" title="决定文生图的画风：12 类预设 + 自定义">
          ${sheetStyles().map(n => `<option value="${esc(n)}" ${n === curStyle ? 'selected' : ''}>${esc(n)}</option>`).join('')}
          <option value="" ${customStyle ? 'selected' : ''}>自定义…</option>
        </select>
        <input class="input segtext" id="sheetStyleText" placeholder="画种 + 材质笔触 + 色调 + 光线，一两句"
          value="${esc(f.styleText)}" ${customStyle ? '' : 'style="display:none"'}>
        <span class="tiny muted">${f.ink === 'line' ? '线稿：画风只借造型比例与线条气质，不上色' : '彩色：决定「风格」那一行的画风'}</span>
      </div>` : ''}
      ${i2i ? `<div class="refs" id="refsRow">${UI.refs.map(refThumbHTML).join('')}</div>
      <div class="refquoted" id="sheetQuoted"><span class="slb">已引用</span>
        ${(f.refChips || []).map(rid => {
          const ref = UI.refs.find(r => r.id === rid); const slot = UI.refs.findIndex(r => r.id === rid) + 1;
          return ref ? `<span class="qchipimg" data-ref="${esc(rid)}"><img src="${refUrl(rid)}" alt="图${slot}"><b>图${slot}</b><span class="x" data-unquote="${esc(rid)}" title="取消引用">✕</span></span>` : '';
        }).join('')}
        <span class="tiny muted">点参考图上的 @ 引用到这里；生成时按上方选中的子功能使用</span></div>` : ''}
      <textarea class="sheetchar" id="sheetChar" rows="3" placeholder="${i2i ? '角色描述（可留空）：只做图片转换时不用写，直接生成即可' : '角色描述：年龄身份 + 脸型五官 + 发型发色 + 服装分件（上衣/下装/外套/鞋）+ 2~3 个标志物'}">${esc(f.character)}</textarea>
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

/* 设定图输入区的 Ctrl+V：和普通能力输入框保持同一套语义。
   - 剪贴板里有图片（截图 / 成品图「复制图片」）→ 直接当参考图上传；
   - 纯文本 → 自己插到角色描述的光标处，并立刻把值同步进 UI.sheetForm。
     页面每秒会按状态签名局部重绘输入区，只靠浏览器默认插入的话，
     刚粘的文字/焦点容易在下一次重绘里被旧值冲掉，看起来就是「Ctrl+V 粘不上」；
   - 焦点在「自定义…」风格输入框里时，文字交给浏览器照常粘贴，不抢。 */
function sheetPasteInto(e, ch, fromFallback = false) {
  const dt = e.clipboardData || window.clipboardData;
  if (!dt) return;
  const images = clipboardImages(e);
  if (images.length) { e.preventDefault(); uploadFiles(images); return; }
  const tgt = e.target || {};
  if (tgt.id === 'sheetStyleText' || (tgt.tagName === 'INPUT' && tgt.closest && tgt.closest('.sheetbox'))) return;
  const text = dt.getData('text/plain') || '';
  if (!text || !ch) return;
  e.preventDefault();
  const start = ch.selectionStart == null ? ch.value.length : ch.selectionStart;
  const end = ch.selectionEnd == null ? start : ch.selectionEnd;
  ch.setRangeText(text, start, end, 'end');     // 保留选区语义；setRangeText 不会派发 input 事件
  UI.sheetForm.character = ch.value;            // 显式同步，别等 oninput，也别被轮询重绘冲掉
  const okToSend = !!ch.value.trim() || sheetMode() !== 't2i';
  const btn = $('#sendBtn'); if (btn) btn.disabled = !okToSend;
  const hint = $('#emptyHint'); if (hint) hint.style.display = okToSend ? 'none' : '';
  const box = $('#inputbox'); if (box) box.classList.remove('err');
  if (fromFallback) ch.focus();
  scheduleDraft();
}

/* 兜底：焦点被重绘抢走（activeElement 变成 body）时，Ctrl+V 仍然落到角色描述里。
   只在「创作台 + 设定图输入区存在 + 焦点不在任何可编辑元素」时接管，别的能力一律放行。 */
if (!window._sheetPasteFallbackBound) {
  window._sheetPasteFallbackBound = true;
  document.addEventListener('paste', e => {
    if (UI.route !== 'studio') return;
    const ch = $('#sheetChar'); if (!ch) return;        // 只有设定图表单才有这个框
    const ae = document.activeElement;
    if (ae && (ae.id || ae.isContentEditable)) return;  // 已有明确焦点就不抢
    sheetPasteInto(e, ch, true);
  });
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
  // 1.07：顶部三算子功能各走各的工作流；同步全局 UI.mode 只为复用上传/发送门槛
  $$('[data-sheet-mode]').forEach(el => el.onclick = () => {
    const m = el.dataset.sheetMode;
    if (!SHEET_MODE_NAME[m] || m === sheetMode()) return;
    UI.mode = m === 't2i' ? 't2i' : 'i2i';
    if (m !== 't2i') UI.sheetForm.ref = m;
    after();
  });
  const line = $('#sheetLine');
  if (line) line.onchange = () => { UI.sheetForm.lineStyle = line.value; after(); };
  // 风格下拉/自定义句（只对文生图显示）：选预设收起输入框、选「自定义…」露出输入框
  const st = $('#sheetStyle');
  if (st) st.onchange = () => {
    UI.sheetForm.styleCustom = !st.value;
    UI.sheetForm.style = st.value || UI.sheetForm.style;
    UI.sheetForm.styleText = '';
    after();
  };
  const stx = $('#sheetStyleText');
  if (stx) stx.oninput = () => { UI.sheetForm.styleText = stx.value; scheduleDraft(); };
  // 已引用参考图 chip 条：点 ✕ 取消引用（只影响 chip 展示，参考图本体仍在 refs 里）
  $$('[data-unquote]').forEach(el => el.onclick = () => {
    const f = sheetForm();
    f.refChips = (f.refChips || []).filter(x => x !== el.dataset.unquote);
    refreshComposer();
  });
  const ch = $('#sheetChar');
  if (ch) {
    ch.oninput = () => {
      UI.sheetForm.character = ch.value;
      const okToSend = !!ch.value.trim() || sheetMode() !== 't2i';   // 图生图纯转换允许空描述
      const btn = $('#sendBtn'); if (btn) btn.disabled = !okToSend;
      const hint = $('#emptyHint'); if (hint) hint.style.display = okToSend ? 'none' : '';
      const box = $('#inputbox'); if (box) box.classList.remove('err');
      scheduleDraft();
    };
    ch.onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doSend(); } };
    ch.onpaste = e => sheetPasteInto(e, ch);
  }
  // 点了输入区空白处（焦点不在 textarea）也能把文字/图片粘进来。
  // 用 onpaste 事件属性而不是 addEventListener：bindStudio() 在一次渲染里可能被调多次
  // （render / refreshComposer / refreshDynamic 里的 chat 重绘都会调它），事件属性天然不叠加。
  const sbox = $('#inputbox');
  if (sbox) sbox.onpaste = e => {
    // 图片生成等能力没有 #sheetChar：它们的编辑器自带 onpaste，这里再接管一次
    // 就会把同一张剪贴板图上传两遍（用户报的「Ctrl+V 一次粘两张」）。
    if (!ch) return;
    if (e.target && e.target.id === 'sheetChar') return;   // textarea 自己已处理
    sheetPasteInto(e, ch);
  };
  const pv = $('#sheetPvBtn');
  if (pv) pv.onclick = () => keepScroll(async () => {
    UI.sheetOpen = !UI.sheetOpen;
    saveDraft();
    // 先重绘一次：面板立刻以「正在组装提示词…」出现，不用等服务端返回才有反馈；
    // 组装完再刷一次内容（服务端忙时这一下可能要一两秒）。
    refreshComposer();
    if (UI.sheetOpen) { await refreshSheetPreview(); refreshComposer(); }
  });
  bindSheetPreviewButtons();
}

/* ============================================================ 1.03 场景 / 道具设定图
   两条新能力和角色设定图共用「结构化表单 → 服务端拼提示词」思路，但完全走
   /api/ext/* 与 prompts/scene_sheet.json、prompts/prop_sheet.json，不碰 1.02 的 sheet.* 路径。 */
const EXT_MODE_NAME = { t2i: '文生图', none: '文生图', i2i: '图生图', lock: '图生图-锁定同角色', style: '图生图-只借画风', expand: '扩图', color: '彩色', line: '线稿' };
function isExtSheetCap(cap) { return !!cap && (cap.id === 'sheet.scene' || cap.id === 'sheet.prop'); }
function extKindOf(cap) { return cap && cap.extKind ? cap.extKind : (cap && cap.id === 'sheet.scene' ? 'scene' : cap && cap.id === 'sheet.prop' ? 'prop' : ''); }
function extLib(kind) { return ((S && S.extLibs) || {})[kind] || {}; }
function extLayouts(kind, mode) {
  // 版式 = 母版的一部分：只列当前模式自己的版式。
  // 场景文生图/图生图各 5 个、扩图 3 个（推镜/拉镜/转镜头，短长与角度是子选项）。
  const list = extLib(kind).layouts || [];
  if (kind === 'prop') return list;
  const m = mode || UI.mode || 't2i';
  return list.filter(l => String(l.mode || '').split(',').includes(m));
}
function extSubs(kind, layoutId) {
  const lay = (extLib(kind).layouts || []).find(l => l.id === layoutId) || null;
  return (lay && lay.subs) || [];
}
function extBlockedSlots(kind, layoutId) {
  const map = extLib(kind).blockedSlots || {};
  return map[layoutId] || [];
}
function extStyles(kind) { return (extLib(kind).styles || []).map(s => s.name); }
function extLineStyles(kind) { return extLib(kind).line_styles || []; }
function extLayoutOf(kind, id) { return extLayouts(kind, UI.mode).find(l => l.id === id) || null; }
function extCombos(kind) { return ((S && S.extCombos) || {})[kind] || []; }
function extForm() {
  const cap = currentCap(), kind = extKindOf(cap) || (UI.extForm && UI.extForm.kind) || 'scene';
  const f = UI.extForm = UI.extForm || {};
  if (f.kind && f.kind !== kind) f.refSlots = {};   // 场景/道具槽位语义不同，别把上一能力的图带过去
  f.kind = kind;
  const defs = kind === 'scene'
    ? { layout: 'N', sub: '', ink: 'color', nochar: 'yes', ref: 'none', grade: '默认', text: '',
        style: '', styleText: '', styleCustom: false, lineStyle: '', refSlots: {} }
    : { layout: 'MV', sub: '', ink: 'color', nochar: 'yes', ref: 'lock', grade: '默认', text: '',
        style: '', styleText: '', styleCustom: false, lineStyle: '', refSlots: {} };
  Object.keys(defs).forEach(k => { if (f[k] === undefined || f[k] === null) f[k] = defs[k]; });
  if (!f.refSlots || typeof f.refSlots !== 'object') f.refSlots = {};
  const layouts = extLayouts(kind, UI.mode);
  if (!layouts.some(l => l.id === f.layout)) f.layout = (layouts[0] || {}).id || '';
  // 子选项（扩图的短/长、左右转角度）跟着版式走，换版式就落到它的第一个子选项
  const subs = extSubs(kind, f.layout);
  if (subs.length && !subs.some(s => s.id === f.sub)) f.sub = subs[0].id;
  if (!subs.length) f.sub = '';
  if (!f.lineStyle) f.lineStyle = (extLineStyles(kind)[0] || {}).id || '';
  const styles = extStyles(kind);
  if (!f.style && !f.styleCustom && styles.length) f.style = styles[0];
  // 图生图（场景/道具）只能自定义，切过去时不要留着预设下拉的假象
  if (UI.mode === 'i2i' && kind === 'scene') f.styleCustom = true;
  if (kind === 'prop') {
    if (UI.mode === 'i2i' && f.ref === 'none') f.ref = 'lock';
    if (UI.mode === 't2i' && f.ref === 'lock') f.ref = 'none';
  }
  // 版式自带限制：全景图生图不收全景图，鸟瞰图生图不收鸟瞰图。
  // 换成被禁版式时把槽位里那张图一起摘掉，否则会带着隐藏槽位去提交。
  if (kind === 'scene' && UI.mode !== 't2i') {
    extBlockedSlots(kind, f.layout).forEach(slot => {
      const rid = f.refSlots[slot];
      if (!rid) return;
      delete f.refSlots[slot];
      UI.refs = UI.refs.filter(r => r.id !== rid);
    });
  }
  return f;
}
function extLayout() { const f = extForm(); return extLayoutOf(f.kind, f.layout) || extLayouts(f.kind)[0] || null; }
function extCombo() {
  const f = extForm();
  let cid;
  if (f.kind === 'scene') cid = UI.mode === 'expand' ? `${f.layout}.expand.-.-` : `${f.layout}.${UI.mode}.${f.ink}.${f.nochar}`;
  else cid = `${f.layout}.${UI.mode}.${f.ink}.${f.ref}`;
  return extCombos(f.kind).find(c => c.id === cid) || null;
}
function extGrades() { return ((extCombo() || {}).grades) || []; }
function extGrade() {
  const f = extForm(), grades = extGrades();
  if (!grades.length) return null;
  if (!grades.some(g => g.name === f.grade)) f.grade = grades[0].name;
  return grades.find(g => g.name === f.grade) || grades[0];
}
function extStyleValue() {
  const f = extForm();
  if (UI.mode === 'expand') return '';
  const custom = String(f.styleText || '').trim();
  if (f.styleCustom && custom) return custom;
  if (f.styleCustom && UI.mode === 'i2i') return '';
  return f.style || (extStyles(f.kind)[0] || '');
}
function extPayload() {
  const f = extForm();
  return {
    kind: f.kind, mode: UI.mode, layout: f.layout, sub: f.sub || '', text: String(f.text || '').trim(),
    ink: f.ink, nochar: f.nochar, ref: f.ref, refSlots: { ...(f.refSlots || {}) },
    style: extStyleValue(), styleCustom: !!f.styleCustom, styleText: String(f.styleText || ''),
    lineStyle: (f.ink === 'line' && UI.mode !== 'expand') ? (f.lineStyle || null) : null,
    grade: (extGrade() || {}).name || '默认',
    refs: UI.refs.map(r => r.id),
  };
}
function extSlotDisabled(mode, layout, slot) {
  if (mode === 't2i') return true;
  return extBlockedSlots('scene', layout).includes(slot);
}
function extCanSend(cap) {
  const f = extForm(), kind = extKindOf(cap), text = String(f.text || '').trim();
  if (kind === 'scene') {
    if (UI.mode === 't2i') return !!text;
    if (UI.mode === 'i2i') return !!text && UI.refs.length > 0;
    return !!(f.refSlots.ref && UI.refs.some(r => r.id === f.refSlots.ref));
  }
  if (UI.mode === 't2i') return !!text;
  return UI.refs.length > 0;
}
function extPreviewHTML() {
  const p = UI.extPreview;
  if (!p) return '<div class="pvhead">正在组装提示词…</div>';
  if (p.error) return `<div class="pvhead bad">✕ ${esc(p.error)}</div>`;
  return `<div class="pvhead"><b>${esc(p.grade || '')}</b>
      <span class="tiny muted">${esc(p.size || '')} · ${esc(p.wf || '')} · 共 ${String((p.prompt || '').length)} 字</span></div>
    <pre>${esc(p.prompt || '')}</pre>`;
}
async function refreshExtPreview() {
  const res = await api('/api/ext/preview', { body: extPayload() });
  UI.extPreview = res.ok ? (res.data || {}) : { error: res.reason || '组装失败' };
  const box = $('#extPv');
  if (box) { box.innerHTML = extPreviewHTML(); bindExtPreviewButtons(); }
  else refreshComposer();
}
function bindExtPreviewButtons() {
  const cp = $('#extCopy');
  if (cp) cp.onclick = async () => {
    const text = (UI.extPreview && UI.extPreview.prompt) || '';
    try { await navigator.clipboard.writeText(text); toast('整条提示词已复制'); }
    catch (e) { toast('复制失败，手动选中复制一下'); }
  };
}
function extLayoutBarHTML(f) {
  const layouts = extLayouts(f.kind, UI.mode);
  const btn = l => `<button class="segb ${l.id === f.layout ? 'on' : ''}" data-ext-layout="${esc(l.id)}" title="${esc(l.summary || '')}">${esc(l.name)}<i class="segx">${esc(l.ratio || '')}</i></button>`;
  const subs = extSubs(f.kind, f.layout);
  const subRow = subs.length ? `<div class="sheetbar"><span class="slb">子选项</span>
      <div class="seg ext-seg" role="group" aria-label="子选项">${subs.map(s => `<button class="segb ${s.id === f.sub ? 'on' : ''}" data-ext-sub="${esc(s.id)}" title="${esc(s.summary || '')}">${esc(s.name)}</button>`).join('')}</div>
      <span class="tiny muted">子选项只换提示词里的机位句，母版与档位不变</span></div>` : '';
  const blocked = extBlockedSlots(f.kind, f.layout);
  const blockedName = blocked.map(s => (s === 'pano' ? '全景图' : s === 'bird' ? '鸟瞰布局图' : s)).join('、');
  return `<div class="sheetbar"><span class="slb">版式</span>
    <div class="seg ext-seg" role="group" aria-label="版式">${layouts.map(btn).join('')}</div>
    <span class="tiny muted">${blocked.length ? `本版式不接收${esc(blockedName)}槽位` : `整图 ${esc((extLayout() || {}).ratio || '16:9')}`}</span></div>${subRow}`;
}
function extSlotHTML(slot) {
  const f = extForm(), spec = (extLib('scene').refSlots || []).find(x => x.id === slot) || { id: slot, name: slot };
  const disabled = extSlotDisabled(UI.mode, f.layout, slot);
  const rid = f.refSlots[slot], ref = UI.refs.find(r => r.id === rid);
  const hint = slot === 'ref' ? (UI.mode === 'expand' ? '完整基准参考 · 扩图必传' : '完整基准参考，提示词里可自行引用')
    : slot === 'pano' ? '完整基准参考 · 按全景画面的场景生成'
      : '只取物品相对位置 · 不照搬文字、符号、边框与像素';
  return `<div class="ext-slot ${disabled ? 'disabled' : ''} ${ref ? 'on' : ''}" data-ext-slot="${esc(slot)}">
    <div class="slot-t"><b>${esc(spec.name)}</b><span class="tiny muted">${disabled ? '此版式不可用' : (ref ? '已上传' : '可留空')}</span></div>
    ${ref ? `<img src="${refUrl(ref.id)}" alt="${esc(spec.name)}">
      <div class="slot-acts"><button class="btn ghost sm" data-ext-slot-upload="${esc(slot)}">替换</button>
      <button class="btn ghost sm" data-ext-slot-rm="${esc(slot)}">移除</button></div>`
      : `<button class="btn ghost sm" data-ext-slot-upload="${esc(slot)}" ${disabled ? 'disabled' : ''}>＋ 上传</button>`}
    <div class="tiny muted">${esc(hint)}</div>
    <input type="file" accept="image/*" style="display:none" data-ext-slot-file="${esc(slot)}"></div>`;
}
function viewExtSheetComposer(cap) {
  const kind = extKindOf(cap), lib = extLib(kind), f = extForm();
  if (!(lib.layouts || []).length) {
    return `<div class="composer"><div class="composer-inner"><div class="inputbox" style="border-color:var(--line);box-shadow:none;background:#fafbfe">
      <div class="tiny muted" style="padding:18px 4px;text-align:center;line-height:1.9">提示词库读不到（prompts/${kind === 'scene' ? 'scene_sheet.json' : 'prop_sheet.json'}）。<br>
      去管理台 →「档位表 → ${esc(cap.name)}」点「✎ 设定图模板」看一眼。</div></div></div></div>`;
  }
  const grade = extGrade(), grades = extGrades(), layout = extLayout();
  const isExpand = UI.mode === 'expand', styles = extStyles(kind), lineStyles = extLineStyles(kind);
  const i2i = UI.mode === 'i2i', canSend = extCanSend(cap);
  const curStyle = (!f.styleCustom && styles.includes(f.style)) ? f.style : '';
  const custom = !!f.styleCustom;
  const modeBar = `<div class="parambar">
    <div class="modepick" role="group" aria-label="生成方式">
      <button class="${UI.mode === 't2i' ? 'on' : ''}" data-mode="t2i"><span class="dotg"></span>文生图</button>
      <button class="${i2i ? 'on' : ''}" data-mode="i2i"><span class="dotb"></span>图生图</button>
      ${kind === 'scene' ? `<button class="${isExpand ? 'on' : ''}" data-mode="expand"><span class="dota"></span>扩图</button>` : ''}
    </div><span class="modedis">${isExpand ? '以参考图为基准，重拍推近 / 拉远 / 转镜头后的同一场景' : i2i ? '按参考图重画设定图' : '纯文字描述直接出设定图'}</span>
    <span class="scope-hint"></span></div>`;
  const refRow = kind === 'scene' && UI.mode !== 't2i'
    ? `<div class="sheetbar"><span class="slb">参考图</span><span class="tiny muted">${UI.mode === 'expand' ? '参考图必传；全景 / 鸟瞰 / 草图只作位置参考' : '至少一张；全景 / 鸟瞰 / 草图按相对位置理解'}</span></div>
       <div class="ext-slots">${['ref', 'pano', 'bird', 'sketch'].filter(s => !extSlotDisabled(UI.mode, f.layout, s)).map(extSlotHTML).join('')}</div>` : '';
  const propRefRow = kind === 'prop' && i2i
    ? `<div class="refs" id="refsRow">${UI.refs.map(refThumbHTML).join('')}</div>
       <div class="sheetbar"><span class="slb">参考</span>
        <div class="seg" role="group" aria-label="参考方式">
          <button class="segb ${f.ref === 'none' ? 'on' : ''}" data-ext-ref="none">无参考图</button>
          <button class="segb ${f.ref === 'lock' ? 'on' : ''}" data-ext-ref="lock">锁定同道具</button>
        </div><span class="tiny muted">${f.ref === 'lock' ? '图 1 是同一件道具：造型、材质、配色不变' : '只按文字重新画，不锁定参考道具'}</span></div>` : '';
  const inkRow = !isExpand ? `<div class="sheetbar"><span class="slb">画法</span>
      <div class="seg" role="group" aria-label="画法">
        <button class="segb ${f.ink === 'line' ? '' : 'on'}" data-ext-ink="color">彩色</button>
        <button class="segb ${f.ink === 'line' ? 'on' : ''}" data-ext-ink="line">线稿</button></div>
      ${f.ink === 'line' ? `<select class="input segsel" id="extLine" title="线稿风格句">
        ${lineStyles.map(s => `<option value="${esc(s.id)}" ${s.id === f.lineStyle ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>`
        : '<span class="tiny muted">彩色：风格行用下面的「风格」</span>'}</div>` : '';
  const nocharRow = kind === 'scene' && !isExpand ? `<div class="sheetbar"><span class="slb">禁角色</span>
      <div class="seg" role="group" aria-label="禁止场景中出现角色">
        <button class="segb ${f.nochar === 'yes' ? 'on' : ''}" data-ext-nochar="yes">是</button>
        <button class="segb ${f.nochar === 'no' ? 'on' : ''}" data-ext-nochar="no">否</button></div>
      <span class="tiny muted">${f.nochar === 'yes' ? '提示词会重点强调画面里没有任何人物' : '不在提示词里强调，由你自己的描述控制是否出现人物'}</span></div>` : '';
  const styleRow = !isExpand ? `<div class="sheetbar"><span class="slb">风格</span>
      <select class="input segsel" id="extStyle" style="min-width:148px" title="${i2i ? '只能自定义；也可以不输入' : '12 类预设 + 自定义'}">
        ${styles.map(n => `<option value="${esc(n)}" ${n === curStyle ? 'selected' : ''} ${i2i ? 'disabled' : ''}>${esc(n)}</option>`).join('')}
        <option value="" ${custom || i2i ? 'selected' : ''}>自定义…</option></select>
      <input class="input segtext" id="extStyleText" placeholder="画种 + 材质笔触 + 色调 + 光线，可留空"
        value="${esc(f.styleText)}" ${custom || i2i ? '' : 'style="display:none"'}>
      <span class="tiny muted">${i2i ? '图生图：预设已禁用，只剩自定义（可留空）' : custom ? '自定义：留空则回退到当前预设' : '预设：决定「风格」那一行'}</span></div>` : '';
  return `<div class="composer ext"><div class="composer-inner">
    ${modeBar}
    <div class="inputbox sheetbox" id="inputbox">
      <div class="sheetbody">
      ${extLayoutBarHTML(f)}
      ${inkRow}
      ${nocharRow}
      ${refRow}
      ${propRefRow}
      <textarea class="sheetchar" id="extText" rows="3" placeholder="${isExpand ? '扩图描述（可留空）：例如保持原场景布局，左侧再露出半扇窗' : kind === 'scene' ? '场景描述：环境、方位、道具位置、光线与氛围' : '道具描述：写清道具本体即可；不写视角就默认正面/侧面/背面三个斜向下 30° 视角（圆球、圆罐这类对称道具只画一个）'}">${esc(f.text)}</textarea>
      <div class="sheetbar"><span class="slb">档位</span>
        <div class="seg ext-seg" role="group" aria-label="档位">
          ${grades.length ? grades.map(g => `<button class="segb ${grade && g.name === grade.name ? 'on' : ''}" data-ext-grade="${esc(g.name)}" title="${esc(g.note || '')} · 出图 ${esc(g.size || '')}">${esc(g.name)}</button>`).join('')
            : '<span class="tiny muted">这个组合还没有档位</span>'}</div>
        <span class="tiny muted">${grade ? `${esc(grade.locked ? '默认就是这个组合的完整配方' : '与默认同提示词、同参考，只改参数')} · 出图 ${esc(grade.size || '')}` : ''}</span></div>
      ${styleRow}
      ${isExpand ? '<div class="tiny muted" style="padding:0 2px;line-height:1.7">扩图完全基于参考图：不支持画法、风格和禁角色开关。</div>' : ''}
      ${UI.extOpen ? `<div class="pvbox" id="extPv">${extPreviewHTML()}</div>` : ''}
      </div>
      <div class="foot">
        ${kind === 'prop' && i2i ? '<button class="btn ghost sm" id="addRef">＋ 上传</button>' : ''}
        ${kind === 'scene' && UI.mode !== 't2i' ? '<span class="tiny muted ext-foot-hint">在上方四个槽位里上传参考图</span>' : ''}
        <button class="btn ghost sm" id="extPvBtn">${UI.extOpen ? '收起提示词' : '看完整提示词'}</button>
        ${UI.extOpen ? '<button class="btn ghost sm" id="extCopy">复制</button>' : ''}
        <span class="tiny muted" id="emptyHint" style="${canSend ? 'display:none' : ''}">${UI.mode === 'expand' ? '先在「参考图」槽位上传一张' : i2i ? '写点描述并上传参考图' : '写点描述再发送'}</span>
        <span style="flex:1"></span><span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>
        <button class="sendbtn" id="sendBtn" title="发送" ${canSend ? '' : 'disabled'}>↑</button>
      </div>
    </div>
  </div></div>`;
}

function extPasteInto(e, ch, fromFallback = false) {
  const dt = e.clipboardData || window.clipboardData;
  if (!dt) return;
  const images = clipboardImages(e);
  if (images.length) {
    e.preventDefault();
    const f = extForm();
    const order = UI.mode === 'expand' ? ['ref', 'pano', 'bird', 'sketch'] : ['ref', 'pano', 'bird', 'sketch'];
    const slot = order.find(s => !extSlotDisabled(UI.mode, f.layout, s) && !f.refSlots[s]) || 'ref';
    uploadFilesToSlot(images, slot);
    return;
  }
  const tgt = e.target || {};
  if (tgt.id === 'extStyleText' || (tgt.tagName === 'INPUT' && tgt.closest && tgt.closest('.sheetbox'))) return;
  const text = dt.getData('text/plain') || '';
  if (!text || !ch) return;
  e.preventDefault();
  const start = ch.selectionStart == null ? ch.value.length : ch.selectionStart;
  const end = ch.selectionEnd == null ? start : ch.selectionEnd;
  ch.setRangeText(text, start, end, 'end');
  UI.extForm.text = ch.value;
  const btn = $('#sendBtn'); if (btn) btn.disabled = !extCanSend(currentCap());
  const hint = $('#emptyHint'); if (hint) hint.style.display = extCanSend(currentCap()) ? 'none' : '';
  const box = $('#inputbox'); if (box) box.classList.remove('err');
  if (fromFallback) ch.focus();
  scheduleDraft();
}
if (!window._extPasteFallbackBound) {
  window._extPasteFallbackBound = true;
  document.addEventListener('paste', e => {
    if (UI.route !== 'studio') return;
    const ch = $('#extText'); if (!ch) return;
    const ae = document.activeElement;
    if (ae && (ae.id || ae.isContentEditable)) return;
    extPasteInto(e, ch, true);
  });
}

async function uploadFilesToSlot(files, slot) {
  const cap = currentCap(), list = files.filter(f => f && (f.type || '').startsWith('image/'));
  if (!list.length) { toast('只支持常见图片格式'); return; }
  if (extSlotDisabled(UI.mode, extForm().layout, slot)) { toast('这个版式不能用该参考图槽位'); return; }
  const f = extForm(), maxRefs = cap.maxRefs || 4;
  const slots = ['ref', 'pano', 'bird', 'sketch'];
  for (const raw of list) {
    if (UI.refs.length >= maxRefs) { toast(`最多上传 ${maxRefs} 张参考图`); break; }
    const file = normalizeImageFile(raw);
    const form = new FormData(); form.append('file', file, file.name);
    const res = await api('/api/uploads', { form });
    if (!res.ok) { toast(res.reason || '上传没成功'); continue; }
    const item = { id: res.data.id, kind: 'image', name: res.data.name };
    const old = f.refSlots[slot];
    if (old) {
      const at = UI.refs.findIndex(r => r.id === old);
      if (at >= 0) UI.refs[at] = item; else UI.refs.push(item);
    } else {
      UI.refs.push(item);
    }
    f.refSlots[slot] = item.id;
    const next = slots.find(s => !extSlotDisabled(UI.mode, f.layout, s) && !f.refSlots[s]);
    if (!next) break;
    slot = next;
  }
  UI.extPreview = null; saveDraft(); refreshComposer();
}

function bindExtSheetComposer() {
  const cap = currentCap();
  if (!isExtSheetCap(cap)) return;
  const f = extForm();
  const after = async (rerender = true) => {
    UI.extPreview = null; saveDraft();
    if (UI.extOpen) await refreshExtPreview();
    else if (rerender) refreshComposer();
  };
  $$('[data-ext-layout]').forEach(el => el.onclick = () => {
    f.layout = el.dataset.extLayout;
    const subs = extSubs(f.kind, f.layout);
    f.sub = subs.length ? subs[0].id : '';   // 换版式回到它的第一个子选项
    after();
  });
  $$('[data-ext-sub]').forEach(el => el.onclick = () => { f.sub = el.dataset.extSub; after(); });
  $$('[data-ext-grade]').forEach(el => el.onclick = () => { f.grade = el.dataset.extGrade; after(); });
  $$('[data-ext-ink]').forEach(el => el.onclick = () => { f.ink = el.dataset.extInk === 'line' ? 'line' : 'color'; after(); });
  $$('[data-ext-nochar]').forEach(el => el.onclick = () => { f.nochar = el.dataset.extNochar === 'no' ? 'no' : 'yes'; after(); });
  $$('[data-ext-ref]').forEach(el => el.onclick = () => { f.ref = el.dataset.extRef === 'none' ? 'none' : 'lock'; after(); });
  const ln = $('#extLine'); if (ln) ln.onchange = () => { f.lineStyle = ln.value; after(); };
  $$('[data-ext-slot-upload]').forEach(el => el.onclick = () => {
    const input = $(`[data-ext-slot-file="${el.dataset.extSlotUpload}"]`);
    if (input) input.click();
  });
  $$('[data-ext-slot-file]').forEach(el => el.onchange = () => {
    uploadFilesToSlot([...el.files || []], el.dataset.extSlotFile);
    el.value = '';
  });
  $$('[data-ext-slot-rm]').forEach(el => el.onclick = () => {
    const slot = el.dataset.extSlotRm, rid = f.refSlots[slot];
    delete f.refSlots[slot];
    if (rid) UI.refs = UI.refs.filter(r => r.id !== rid);
    after();
  });
  const ch = $('#extText');
  if (ch) {
    ch.oninput = () => {
      UI.extForm.text = ch.value;
      const ok = extCanSend(currentCap());
      const btn = $('#sendBtn'); if (btn) btn.disabled = !ok;
      const hint = $('#emptyHint'); if (hint) hint.style.display = ok ? 'none' : '';
      const box = $('#inputbox'); if (box) box.classList.remove('err');
      scheduleDraft();
    };
    ch.onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doSend(); } };
    ch.onpaste = e => extPasteInto(e, ch);
  }
  const sbox = $('#inputbox');
  if (sbox) sbox.onpaste = e => {
    if (e.target && e.target.id === 'extText') return;
    extPasteInto(e, ch);
  };
  const st = $('#extStyle');
  if (st) st.onchange = () => {
    f.styleCustom = !st.value;
    if (st.value) f.style = st.value;
    f.styleText = '';
    after();
  };
  const stx = $('#extStyleText');
  if (stx) stx.oninput = () => { UI.extForm.styleText = stx.value; scheduleDraft(); };
  const pv = $('#extPvBtn');
  if (pv) pv.onclick = () => keepScroll(async () => {
    UI.extOpen = !UI.extOpen;
    saveDraft();
    if (UI.extOpen) await refreshExtPreview();
    refreshComposer();
  });
  bindExtPreviewButtons();
}

/* ------------------------------------------------------------ 宫格分镜故事板（1.03）
 * 与角色 / 场景 / 道具设定图完全独立：只走 /api/board/*，不碰既有表单路径。
 */
function isBoardCap(cap) { return !!cap && cap.id === 'board.story'; }
function boardLib() { return (S && S.boardLib) || {}; }
function boardCombos() { return (S && S.boardCombos) || []; }
function boardForm() {
  const f = UI.boardForm = UI.boardForm || { layout: 'G4', ink: 'color', lineStyle: '', grade: '', sel: 0, cells: [] };
  const layouts = boardLib().layouts || [];
  if (!layouts.some(l => l.id === f.layout)) f.layout = (layouts[0] || {}).id || 'G4';
  if (f.ink !== 'line') f.ink = 'color';
  const n = Number((layouts.find(l => l.id === f.layout) || {}).cells || 4);
  // 注意：只有当格子数变化时才重建数组，否则每次调用都会换成一个新数组，
  // 导致 boardForm().cells[i] = x 这样的写入落在临时数组上、草稿永远存不住。
  if (!Array.isArray(f.cells) || f.cells.length !== n) {
    const old = Array.isArray(f.cells) ? f.cells : [];
    f.cells = Array.from({ length: n }, (_, i) => String(old[i] || '').slice(0, 1200));
  }
  f.sel = Math.max(0, Math.min(n - 1, Number(f.sel) || 0));
  UI.mode = 'i2i';                       // 这个能力只有图生图，但允许 0 张参考图
  return f;
}
function boardLayout() {
  const f = boardForm();
  return (boardLib().layouts || []).find(l => l.id === f.layout) || {};
}
function boardCombo() {
  const f = boardForm();
  return boardCombos().find(c => c.id === `${f.layout}.${f.ink}`) || null;
}
function boardGrades() {
  const c = boardCombo();
  return ((c && c.grades) || []);
}
function boardGrade() {
  const f = boardForm(), grades = boardGrades();
  if (!grades.some(g => g.name === f.grade)) f.grade = ((grades[0] || {}).name || '默认');
  return grades.find(g => g.name === f.grade) || null;
}
function boardPayload() {
  const f = boardForm();
  return { layout: f.layout, cells: f.cells.map(x => String(x || '')), ink: f.ink,
    lineStyle: f.ink === 'line' ? (f.lineStyle || '') : null,
    grade: boardGrade() ? boardGrade().name : '默认',
    refs: UI.refs.map(r => r.id) };
}
function boardCanSend() {
  const f = boardForm();
  return f.cells.some(x => String(x || '').trim()) && !!boardCombo();
}
function boardPreviewHTML() {
  const p = UI.boardPreview;
  if (!p) return '<div class="pvhead">正在组装提示词…</div>';
  if (p.error) return `<div class="pvhead bad">✕ ${esc(p.error)}</div>`;
  return `<div class="pvhead"><b>${esc(p.grade || '')}</b>
      <span class="tiny muted">${esc(p.size || '')} · ${esc(p.wf || '')} · 共 ${String((p.prompt || '').length)} 字</span></div>
    <pre>${esc(p.prompt || '')}</pre>`;
}
async function refreshBoardPreview() {
  const res = await api('/api/board/preview', { body: boardPayload() });
  UI.boardPreview = res.ok ? (res.data || {}) : { error: res.reason || '组装失败' };
  const box = $('#boardPv');
  if (box) { box.innerHTML = boardPreviewHTML(); bindBoardPreviewButtons(); }
  else refreshComposer();
}
function bindBoardPreviewButtons() {
  const cp = $('#boardCopy');
  if (cp) cp.onclick = async () => {
    const text = (UI.boardPreview && UI.boardPreview.prompt) || '';
    try { await navigator.clipboard.writeText(text); toast('整条提示词已复制'); }
    catch (e) { toast('复制失败，手动选中复制一下'); }
  };
}
function viewBoardComposer(cap) {
  const lib = boardLib(), f = boardForm(), lay = boardLayout();
  if (!(lib.layouts || []).length) {
    return `<div class="composer"><div class="composer-inner"><div class="inputbox" style="border-color:var(--line);box-shadow:none;background:#fafbfe">
      <div class="tiny muted" style="padding:18px 4px;text-align:center;line-height:1.9">提示词库读不到（prompts/board_story.json）。<br>
      重启服务或去管理台检查「档位表 → ${esc(cap.name)}」。</div></div></div></div>`;
  }
  const grades = boardGrades(), grade = boardGrade();
  const lineStyles = lib.line_styles || [];
  const n = Number(lay.cells || 4), cols = Number(lay.cols || 2);
  const cellsHTML = Array.from({ length: n }, (_, i) => {
    const pos = cols === 2 ? ['左上', '右上', '左下', '右下'][i] : `第${Math.floor(i / cols) + 1}行第${i % cols + 1}列`;
    return `<button class="board-cell ${f.sel === i ? 'on' : ''} ${String(f.cells[i] || '').trim() ? '' : 'blank'}" data-board-sel="${i}"
      title="第 ${i + 1} 格 · ${pos}">${i + 1}<small>${pos}</small></button>`;
  }).join('');
  const canSend = boardCanSend();
  return `<div class="composer"><div class="composer-inner">
    <div class="parambar"><span class="modedis">图生图 · 0~${cap.maxRefs || 6} 张参考图都可以生成</span>
      <span class="scope-hint"></span></div>
    <div class="inputbox sheetbox" id="inputbox">
      <div class="sheetbody">
      <div class="sheetbar"><span class="slb">版式</span>
        <div class="seg" role="group" aria-label="宫格版式">
          ${(lib.layouts || []).map(l => `<button class="segb ${l.id === f.layout ? 'on' : ''}" data-board-layout="${esc(l.id)}"
            title="${esc(l.name)} 出图 ${esc(l.size || '')}">${esc(l.name)}<i class="segx">${esc(l.ratio || '')}</i></button>`).join('')}
        </div><span class="tiny muted">出图 ${esc(lay.size || '')} · 每小格 16:9</span></div>
      <div class="sheetbar"><span class="slb">画法</span>
        <div class="seg" role="group" aria-label="画法">
          <button class="segb ${f.ink === 'color' ? 'on' : ''}" data-board-ink="color">彩色</button>
          <button class="segb ${f.ink === 'line' ? 'on' : ''}" data-board-ink="line">线稿</button></div>
        ${f.ink === 'line' ? `<select class="input segsel" id="boardLine" title="线稿风格句">
          ${lineStyles.map(s => `<option value="${esc(s.id)}" ${s.id === f.lineStyle ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>`
          : '<span class="tiny muted">彩色：统一电影质感</span>'}</div>
      <div class="refs" id="refsRow">${UI.refs.map(refThumbHTML).join('')}</div>
      <div class="sheetbar"><span class="slb">参考图</span><span class="tiny muted">最多 ${cap.maxRefs || 6} 张，可不传；在格描述里写「图 1」即锁定该图角色</span></div>
      <div class="board-wrap">
        <div class="board-grid cols${cols}" role="group" aria-label="选择宫格">${cellsHTML}</div>
        <div class="board-side">
          <div class="board-tip"><b>第 ${f.sel + 1} 格</b><span>· 输入这一格的构图描述；留空的格子在成图里保持纯白（不画空框，所以整行/整列都留空时，那块就是一片白底）</span></div>
          <textarea class="board-text" id="boardCellInput" rows="6" data-board-cell="${f.sel}"
            placeholder="例如：清晨的站台，短发女孩回头看向镜头，中景">${esc(f.cells[f.sel] || '')}</textarea>
        </div>
      </div>
      <div class="sheetbar"><span class="slb">档位</span>
        <div class="seg" role="group" aria-label="档位">
          ${grades.length ? grades.map(g => `<button class="segb ${grade && g.name === grade.name ? 'on' : ''}" data-board-grade="${esc(g.name)}"
            title="${esc(g.note || '')} · 出图 ${esc(g.size || '')}">${esc(g.name)}</button>`).join('')
            : '<span class="tiny muted">这个组合还没有档位</span>'}</div>
        <span class="tiny muted">${grade ? `${esc(grade.locked ? '默认就是这个组合的完整配方' : '同提示词、同参考，只改参数')} · 出图 ${esc(grade.size || '')}` : ''}</span></div>
      ${UI.boardOpen ? `<div class="pvbox" id="boardPv">${boardPreviewHTML()}</div>` : ''}
      </div>
      <div class="foot">
        <button class="btn ghost sm" id="addRef">＋ 上传参考图</button>
        <button class="btn ghost sm" id="boardPvBtn">${UI.boardOpen ? '收起提示词' : '看完整提示词'}</button>
        ${UI.boardOpen ? '<button class="btn ghost sm" id="boardCopy">复制</button>' : ''}
        <span class="tiny muted" id="emptyHint" style="${canSend ? 'display:none' : ''}">至少写一格的画面描述</span>
        <span style="flex:1"></span><span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>
        <button class="sendbtn" id="sendBtn" title="发送" ${canSend ? '' : 'disabled'}>↑</button>
      </div>
    </div>
  </div></div>`;
}
function bindBoardComposer() {
  const f = boardForm();
  $$('[data-board-layout]').forEach(el => el.onclick = () => {
    const old = boardForm();
    UI.boardForm = { ...old, layout: el.dataset.boardLayout, sel: 0, cells: [] };
    UI.boardOpen = false; UI.boardPreview = null;
    saveDraft(); refreshComposer();
  });
  $$('[data-board-ink]').forEach(el => el.onclick = () => {
    const cur = boardForm();
    UI.boardForm = { ...cur, ink: el.dataset.boardInk, lineStyle: cur.lineStyle || '' };
    UI.boardOpen = false; UI.boardPreview = null;
    saveDraft(); refreshComposer();
  });
  const ls = $('#boardLine');
  if (ls) ls.onchange = () => { boardForm().lineStyle = ls.value; UI.boardPreview = null; saveDraft(); };
  $$('[data-board-sel]').forEach(el => el.onclick = () => {
    const input = $('#boardCellInput');
    if (input) boardForm().cells[boardForm().sel] = input.value;
    boardForm().sel = Number(el.dataset.boardSel) || 0;
    saveDraft(); refreshComposer();
    const next = $('#boardCellInput'); if (next) next.focus();
  });
  const cell = $('#boardCellInput');
  if (cell) {
    cell.oninput = () => { boardForm().cells[boardForm().sel] = cell.value; scheduleDraft(); refreshBoardSendState(); };
    cell.onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doSend(); } };
    cell.onpaste = e => {
      const images = clipboardImages(e);
      if (images.length) { e.preventDefault(); uploadFiles(images); }
    };
  }
  $$('[data-board-grade]').forEach(el => el.onclick = () => {
    boardForm().grade = el.dataset.boardGrade; UI.boardPreview = null;
    saveDraft(); refreshComposer();
  });
  const pv = $('#boardPvBtn');
  if (pv) pv.onclick = () => keepScroll(async () => {
    UI.boardOpen = !UI.boardOpen; saveDraft();
    refreshComposer();                     // 面板先出现（显示「正在组装提示词…」）
    if (UI.boardOpen) { await refreshBoardPreview(); refreshComposer(); }
  });
  // 面板从草稿里恢复成展开态时没人去取提示词，会一直停在「正在组装提示词…」
  if (UI.boardOpen && !UI.boardPreview) setTimeout(() => refreshBoardPreview(), 0);
  bindBoardPreviewButtons();
}
function refreshBoardSendState() {
  const btn = $('#sendBtn'), hint = $('#emptyHint'), f = boardForm();
  const filled = f.cells.some(x => String(x || '').trim());
  if (btn) btn.disabled = !filled;
  if (hint) hint.style.display = filled ? 'none' : '';
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
  // 把纯文本里的 <imageN> 还原成可编辑的缩略图 chip（和画布共用同一份实现）
  return GenUI.editorHTML(text, UI.refs);
}
function serializeEditor() {
  const el = $('#promptInput');
  if (!el) return UI.prompt || '';
  let out = '';
  // 递归遍历：contenteditable 里回车会生成 <br> 或 <div>/<p> 块级节点，
  // 只取文本节点会把换行后的整段丢掉——这正是「提示词被截掉 / 基于上一条不全」的根因。
  const walk = nodes => {
    nodes.forEach(n => {
      if (n.nodeType === 3) { out += n.textContent; return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName;
      if (n.classList.contains('refchip')) {
        const id = n.dataset.ref, idx = UI.refs.findIndex(r => r.id === id);
        out += idx >= 0 ? `<image${idx + 1}>` : '';             // 顺序随 UI.refs 实时计算
        return;
      }
      if (tag === 'BR') { out += '\n'; return; }
      const block = tag === 'DIV' || tag === 'P';
      if (block && out && !out.endsWith('\n')) out += '\n';     // 块级节点前补一个换行
      walk(n.childNodes);
      if (block && !out.endsWith('\n')) out += '\n';
    });
  };
  walk(el.childNodes);
  return out.replace(/\n{3,}/g, '\n\n');                       // 收敛多余空行
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
    'sheet.scene': '场景描述：环境、方位、道具位置、光线与氛围',
    'sheet.prop': '道具描述：写清道具本体即可；不写视角就默认正面/侧面/背面三个斜向下 30° 视角，圆球圆罐这类对称道具只画一个',
    'board.story': '每格写一句镜头画面；留空的格在成图里保持纯白',
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
    'sheet.scene': [
      '雨夜旧码头，木栈桥向画面深处延伸，左侧仓库右侧灯塔，地面有积水反光',
      '山间书院庭院，正北是主殿，东侧竹林，西侧石阶与水渠，清晨薄雾',
      '废弃地铁站站台，中央立柱，左侧售票机，右侧长椅，顶部灯带忽明忽暗',
    ],
    'sheet.prop': [
      '油纸伞：撑开的样子、合起来的样子',
      '茶壶：正面、侧面、壶底',
      '铜香炉',
    ],
    'board.story': [
      '清晨站台，短发女孩回头看向镜头，中景',
      '同一女孩在旧书店里踮脚取书，近景',
      '雨夜巷口，女孩撑红伞走向灯光，全景',
    ],
    'video.firstlast': ['从白天到黑夜的延时摄影', '镜头从远推近，人物回头'],
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
// 画布不是一个"能力"，但服务端 ui.capId 可能残留成 canvas.video；
// 这时回到创作台会拿画布当能力去渲染对话区，轻则排版错乱重则整页空白。
// 渲染前统一纠正：非画布路由不允许把 capId 停在 canvas 类能力上。
function normalizeCapForRoute() {
  const caps = S.capabilities || [];
  const cur = caps.find(c => c.id === UI.capId);
  if (UI.route !== 'canvas' && cur && cur.kind === 'canvas') {
    const fb = caps.find(c => c.kind !== 'canvas' && capUsable(c) && c.enabled);
    if (fb) UI.capId = fb.id;
  }
}
/* 「看完整提示词 / 收起提示词」会重绘整个 #root，浏览器把滚动位置重置到顶部。
   创作台的滚动容器是 .chat-scroll（内部 div，不是 window），所以 window.scrollTo
   对它无效——必须把所有会滚的容器都记下来，重绘后用选择器重新找到元素再恢复。
   这类按钮统一用它包一层，不改 render 的通用行为，以免影响切换能力、路由跳转
   这些本来就该回到顶部的场景。 */
function keepScroll(fn) {
  const sels = ['.chat-scroll', '.rail', '.drawer-scroll', '#root'];
  const win = [window.pageXOffset || 0, window.pageYOffset || 0];
  const boxes = [];
  sels.forEach(s => {
    const el = document.querySelector(s);
    if (el && (el.scrollTop || el.scrollLeft)) boxes.push([s, el.scrollLeft, el.scrollTop]);
  });
  const restore = () => {
    window.scrollTo(win[0], win[1]);
    boxes.forEach(([s, l, t]) => {
      const el = document.querySelector(s);
      if (el) { el.scrollLeft = l; el.scrollTop = t; }
    });
  };
  let r;
  try { r = fn(); } finally { restore(); }
  if (r && typeof r.then === 'function') return r.then(v => { restore(); return v; });
  return r;
}

/* 需求6：移动端输入区「收起/展开」把手。
   统一给每个 .inputbox 注入一个把手（桌面端 CSS 隐藏），点击切换 .folded。
   关键：点击输入区里的档位/模式/时长等按钮会触发 refreshComposer()，
   它用 outerHTML 整块替换 composer，新 DOM 没有把手。因此用 MutationObserver
   监听 DOM 变化自动补注入，并按稳定 key 恢复折叠状态，保证把手不会「点一下就没」。 */
window.__foldState = window.__foldState || {};   // 稳定key -> 是否折叠
function foldKeyOf(box) {
  // 优先用 inputbox 的稳定 id；画布节点用所在 .cnode 的 data-id；兜底用对象上的标记
  if (box.id) return 'id:' + box.id;
  const cn = box.closest && box.closest('.cnode');
  if (cn) return 'node:' + (cn.getAttribute('data-id') || '');
  if (!box.__foldKey) box.__foldKey = 'fk' + (window.__foldKeySeq = (window.__foldKeySeq || 0) + 1);
  return box.__foldKey;
}
function enhanceFoldHandles() {
  if (typeof window === 'undefined') return;
  $$('.inputbox').forEach(box => {
    const folded = !!window.__foldState[foldKeyOf(box)];
    const wantTxt = folded ? '展开输入' : '收起输入';
    const existing = box.querySelector('.fold-handle');
    if (existing) {
      // 已有把手：只在状态确实不同时才写 DOM（避免触发 observer 死循环）
      if (box.classList.contains('folded') !== folded) box.classList.toggle('folded', folded);
      const t = existing.querySelector('.fold-txt');
      if (t && t.textContent !== wantTxt) t.textContent = wantTxt;
      return;
    }
    // 只对「有 sheetbody/输入内容」的输入块加折叠，纯展示块跳过
    if (!box.querySelector('.sheetbody') && !box.querySelector('textarea')) return;
    const h = document.createElement('button');
    h.type = 'button';
    h.className = 'fold-handle';
    h.setAttribute('data-fold', '1');
    h.innerHTML = '<i class="fold-bar"></i><span class="fold-txt">' + wantTxt + '</span>';
    box.insertBefore(h, box.firstChild);
    if (folded) box.classList.add('folded');
  });
}
if (typeof document !== 'undefined' && !window.__foldBound) {
  window.__foldBound = true;
  document.addEventListener('click', e => {
    const h = e.target.closest && e.target.closest('[data-fold]');
    if (!h) return;
    const box = h.closest('.inputbox');
    if (!box) return;
    const folded = box.classList.toggle('folded');
    window.__foldState[foldKeyOf(box)] = folded;
    const txt = h.querySelector('.fold-txt');
    if (txt) txt.textContent = folded ? '展开输入' : '收起输入';
  }, true);
  // DOM 变化自动补注入，覆盖 refreshComposer / repaintNode 等所有局部重绘。
  // 用 rAF 节流 + 执行期布尔锁，且 enhanceFoldHandles 内部已做「值不同才写」守卫，
  // 三重保证注入行为本身不会再触发新一轮回调（不会死循环）。
  if (typeof MutationObserver !== 'undefined') {
    let scheduled = false;
    const ob = new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; enhanceFoldHandles(); });
    });
    ob.observe(document.documentElement, { childList: true, subtree: true });
  }
}

function render() {
  const root = $('#root');
  syncHash();
  normalizeCapForRoute();
  if (UI.route === 'canvas') {
    // 画布只换中间那一栏：左边能力栏、右边「状态与任务」照旧保留，
    // 所以这里不能走 viewStudio，单独一套骨架 + 挂载画布模块。
    root.innerHTML = viewCanvas();
    bindGlobal();
    bindStudio();
    bindDrawer();
    if (window.CanvasModule) CanvasModule.mount($('#cvsRoot'));
    if (typeof enhanceFoldHandles === 'function') enhanceFoldHandles();
    _lastSignature = stateSignature();
    if (window.PromptLib) window.PromptLib.syncDock('canvas');
    return;
  }
  try {
    if (UI.route === 'studio') root.innerHTML = viewStudio();
    else if (UI.route === 'admin') root.innerHTML = viewAdmin();
    else root.innerHTML = viewGallery();
  } catch (err) {
    // 渲染里任何一处取不到字段都不该让整页变白：留一条能看见的错误和刷新按钮
    console.error('render failed', err);
    root.innerHTML = `<div style="padding:40px;font-family:system-ui,sans-serif">
      <h3 style="margin:0 0 8px">页面渲染出错</h3>
      <p class="tiny muted">${esc((err && err.message) || String(err))}</p>
      <button class="btn primary" onclick="location.reload()">刷新页面</button></div>`;
    return;
  }
  bindGlobal();
  if (UI.route === 'studio') { bindStudio(); if (UI.autoScroll) { scrollChatBottom(); UI.autoScroll = false; } }
  if (UI.route === 'admin') bindAdmin();
  if (UI.route === 'gallery') bindGallery();
  // 初次使用引导只在还没有任何成品时出现
  if (!UI.wizardSeen && UI.route === 'studio' && !(S.jobs || []).length) renderWizard();
  if (UI.route === 'admin' && UI.adminPage === 'diag') autoDiag();
  // 组合页有自己的「生效」核对（按组合 id 拉 /api/sheet/combos），旧版按工作流档位查，别混用
  if (UI.route === 'admin' && UI.adminPage === 'grades' && !isSheetCap(currentCap())
      && !isExtSheetCap(currentCap()) && !isBoardCap(currentCap())) showGradeEffective();
  _lastSignature = stateSignature();
  // 1.06：浮动小窗只在创作台 / 画布显示；管理台与历史页自动隐藏
  if (window.PromptLib) window.PromptLib.syncDock(UI.route);
  if (typeof enhanceFoldHandles === 'function') enhanceFoldHandles();
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
    // 任务多的时候（历史累积几百条）每秒整块重建右栏会很卡，内容没变就不动 DOM；
    // 注意固定头和滚动列表是两个容器，不能把 drawerInner 整个塞进 #drawerBody（会重复一份）
    const fixed = $('.drawer-fixed'), drawer = $('#drawerBody');
    let drawerDirty = false;
    if (fixed) {
      const html = drawerFixed();
      if (fixed.innerHTML !== html) { fixed.innerHTML = html; drawerDirty = true; }
    }
    if (drawer) {
      const html = drawerList();
      if (drawer.innerHTML !== html) { drawer.innerHTML = html; drawerDirty = true; }
    }
    if (drawerDirty) bindDrawer();
    const top = $('.topbar'); if (top) top.outerHTML = viewTopbar(); bindGlobal();
    // 输入区走统一的重绘入口：它会保住 contenteditable、角色描述框、
    // 自定义风格框的内容和光标，不会把用户正在输的字/焦点冲掉
    refreshComposer();
    // 新任务出现（点生成 / 队列接上）就把结果区滚到最新那一块，
    // 不然卡片在下面，用户以为「点了没反应」。
    const total = (S.jobs || []).length;
    if (_lastJobCount !== null && total > _lastJobCount) scrollChatBottom();
    _lastJobCount = total;
  } else if (UI.route === 'canvas') {
    // 画布只做增量刷新：卡片的进度、结果图、右栏任务列表；
    // 整块重建会把正在输入的提示词和拖拽状态一起冲掉。
    if (window.CanvasModule) CanvasModule.refresh();
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
  prompt: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.2l2.25 6.55L21 11l-6.75 2.25L12 19.8l-2.25-6.55L3 11l6.75-2.25z"/></svg>',
};
function viewTopbar() {
  const on = UI.comfyOnline;
  const running = runningEntry();
  // 手机上只看状态（空闲 / 生成中），描述性文字留给抽屉里的那一块
  const statusWord = running ? '生成中' : '空闲';
  // 「返回创作台」要回到进来之前那一页：从画布点进管理台，退回来还应该是画布，
  // 而不是把人扔回创作台（1.03 的 bug：画布进去就再也回不来）
  const backRoute = UI.prevRoute === 'canvas' ? 'canvas' : 'studio';
  const backLabel = backRoute === 'canvas' ? '返回画布' : '返回创作台';
  // 提示词模板开关：创作台/画布顶栏常驻，控制提示词速取窗的弹出与隐藏。
  // 手机端隐藏文字、把图标做进滑块里；开关状态直接读 ui.promptDock。
  const dockOn = !!(S.ui && S.ui.promptDock);
  const promptSwitch = (UI.route === 'studio' || UI.route === 'canvas')
    ? `<button type="button" id="ptTopDockSwitch" class="pt-top-switch${dockOn ? ' on' : ''}" role="switch" aria-checked="${dockOn}" aria-label="提示词模板" title="提示词模板">
        <span class="pts-track"><span class="pts-knob">${ICO_SVG.prompt}</span></span>
        <span class="pts-label">提示词模板</span></button>`
    : '';
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
    ${(UI.route === 'studio' || UI.route === 'admin' || UI.route === 'canvas') ? '<button class="btn ghost sm navbtn" id="navToggle" title="菜单"><span class="ico">☰</span></button>' : ''}
    ${(UI.route === 'studio' || UI.route === 'canvas') ? `<button class="btn ghost sm sheetbtn" id="sheetToggle" title="折叠 / 展开任务栏"><span class="ico dico">☰</span><span class="ico mico">${ICO_SVG.tasks}</span><span class="lbl">任务</span></button>` : ''}
    ${promptSwitch}
    ${UI.route === 'gallery'
      ? `<button class="btn ghost sm" data-nav="${backRoute}"><span class="ico">←</span><span class="lbl">${backLabel}</span></button>`
      : `<button class="btn ghost sm" data-nav="gallery"><span class="ico dico">🗂</span><span class="ico mico">${ICO_SVG.gallery}</span><span class="lbl">历史</span></button>`}
    <button class="btn sm" data-nav="${UI.route === 'admin' ? backRoute : 'admin'}"><span class="ico">${UI.route === 'admin' ? '←' : '⚙'}</span><span class="lbl">${UI.route === 'admin' ? backLabel : '管理台'}</span></button>
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
  // rail-min / drawer-min：桌面与平板的左右栏折叠（任何尺寸都支持，手机以外默认展开）
  return `<div class="app ${UI.sheet ? 'sheet-open' : ''} ${UI.navOpen ? 'nav-open' : ''}">
    ${viewTopbar()}
    ${configBanner()}
    <div class="studio ${UI.railMin ? 'rail-min' : ''} ${UI.drawerMin ? 'drawer-min' : ''}">
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
function viewCanvas() {
  // 三栏骨架和创作台同款：能力栏 + 画布 + 状态与任务，只是中间换成一整块画布
  return `<div class="app ${UI.sheet ? 'sheet-open' : ''} ${UI.navOpen ? 'nav-open' : ''}">
    ${viewTopbar()}
    ${configBanner()}
    <div class="studio cvs-mode ${UI.railMin ? 'rail-min' : ''} ${UI.drawerMin ? 'drawer-min' : ''}">
      ${viewRail()}
      <div class="chat"><div class="cvs" id="cvsRoot"></div></div>
      ${viewDrawer()}
    </div>
    <div class="side-mask" id="sideMask"></div>
    <div class="sheet-mask" id="sheetMask"></div>
  </div>`;
}
function viewRail() {
  let html = `<div class="rail"><div class="side-hd"><b>选择能力</b>
    <button class="rail-min-btn" data-collapse="rail" title="${UI.railMin ? '展开能力栏' : '收起能力栏'}">${UI.railMin ? '»' : '«'}</button>
    <button class="side-x" data-side-close title="收起">✕</button></div>`, hasAny = false;
  orderedGroups().forEach(({ name: grp, caps }) => {
    html += `<div class="group">${grp}</div>`;
    caps.forEach(c => {
      if (c.kind === 'canvas') {          // 画布入口：不是能力，点它切到画布页
        hasAny = true;
        html += `<div class="item ${UI.route === 'canvas' ? 'on' : ''}" data-cap="${c.id}" title="${esc(c.desc || '')}">
        <span class="em">${c.em}</span><span class="nm">${esc(c.name)}</span></div>`;
        return;
      }
      // 已配置但停用：从创作台消失；未配置：灰着显示、点不动（需求 §2.1 / X45）
      if ((c.workflows || []).length && !c.enabled) return;
      const usable = capUsable(c) && c.enabled;
      if (usable) hasAny = true;
      // 画布页只有「视频生成画布」那一项高亮，别把上次选的能力也点亮
      const lit = UI.route === 'canvas' ? '' : (c.id === UI.capId ? 'on' : '');
      html += `<div class="item ${lit} ${usable ? '' : 'dis'}" data-cap="${c.id}" title="${esc(c.desc || '')}">
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

/* 结果块统一由 GenUI.jobCard 产出：创作台对话、视频生成画布里的生成节点，
   用的是同一份 DOM 和同一套 class，所以两边看到的结果卡长得一模一样。 */
function jobCard(j, o) { return GenUI.jobCard(j, o); }
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
  // 角色设定图有自己的表单（版式 / 画法 / 参考方式 / 角色行）
  if (isSheetCap(cap)) return viewSheetComposer(cap);
  // 1.03 场景 / 道具设定图：结构不同，单独表单，但仍复用输入区骨架
  if (isExtSheetCap(cap)) return viewExtSheetComposer(cap);
  // 1.03 宫格分镜故事板：固定图生图 + 逐格提示词，独立表单
  if (isBoardCap(cap)) return viewBoardComposer(cap);
  // 1.04 视频生成（四种模式）与音频生成（四个功能）：表单在 media.js
  if (isVideoCap(cap)) return viewVideoComposer(cap);
  if (isAudioCap(cap)) return viewAudioComposer(cap);
  const grades = currentGrades(cap), curGrade = grades.find(g => g.name === UI.params.grade) || grades[0] || null;
  const i2i = UI.mode === 'i2i';
  const empty = !(UI.prompt || '').trim();
  const f = imgForm();
  const modeBtn = (m, label, dot) =>
    `<button class="${UI.mode === m ? 'on' : ''}" data-mode="${m}"><span class="${dot}"></span>${label}</button>`;
  // 输入块走共用组件 GenUI.imageInput：画布里的「图片生成节点」调用的就是同一份，
  // 两边的 DOM / class 完全一致，这边多出来的只有上传、模板、看完整提示词这些创作台专属入口。
  return `<div class="composer"><div class="composer-inner">
    <div class="parambar">
      <div class="modepick" role="group" aria-label="生成模式">
        ${modeBtn('t2i', '文生图', 'dotg')}
        ${modeBtn('i2i', '图生图', 'dotb')}
      </div>
      <span class="modedis">${i2i ? '已开启参考图，可上传' : '纯文字描述，不上传图片'}</span>
      <span class="scope-hint"></span>
    </div>
    ${GenUI.imageInput({
      // 图生图不显示风格行；设定图模板属于管理台功能，创作台不放入口
      show: { style: !i2i, template: false },
      refs: UI.refs.map(r => ({ id: r.id, kind: r.kind || 'image', name: r.name,
        src: r.kind === 'video' ? refUrl(r.id) : refUrl(r.id) })),
      prompt: UI.prompt, i2i,
      ink: f.ink, lineStyle: f.lineStyle, styles: imgStyles(), lineStyles: imgLineStyles(),
      style: f.style, styleCustom: f.styleCustom, styleText: f.styleText,
      grades, grade: (curGrade || {}).name,
      placeholder: placeholderFor(cap),
      uploadHint: i2i ? '' : '切到「图生图」可上传参考图',
      hint: i2i ? pickHintFor(cap) : '纯文字生图，无需参考图',
      previewOpen: !!UI.imgOpen,
      extra: UI.imgOpen ? `<div class="pvbox" id="imgPv">${imgPreviewHTML()}</div>` : '',
      ids: { box: 'inputbox', editor: 'promptInput', refsRow: 'refsRow', addRef: 'addRef',
        styleSel: 'imgStyle', styleText: 'imgStyleText', lineSel: 'imgLine',
        hint: 'pickHint', emptyHint: 'emptyHint', send: 'sendBtn',
        previewBtn: 'imgPvBtn', copyBtn: 'imgCopy' },
    })}
  </div></div>`;
}
/* 图片生成的「画法 + 风格」：和角色设定图同一套口径（线稿风格句、风格预设都复用提示词库），
   但只作用在这一次的提示词上——图片生成没有版式/组合，不需要整台表单。
   需求：图生图只允许自定义风格，所以切到图生图时预设一律不列。 */
function imgForm() {
  const f = UI.imgForm = UI.imgForm || {};
  const defs = { ink: 'color', lineStyle: '', style: '', styleCustom: true, styleText: '' };
  Object.keys(defs).forEach(k => { if (f[k] === undefined || f[k] === null) f[k] = defs[k]; });
  if (!f.lineStyle) f.lineStyle = (imgLineStyles()[0] || {}).id || '';
  return f;
}
/* 需求2：图片生成的「设定图模板」弹窗——布局与角色设定图模板一致，
   但只有「线稿风格句（只读）+ 风格预设（可编辑）」两部分，提示词主体无关。 */
function openImageTemplateModal() {
  const lib = imgStyleLib();
  const lines = lib.line_styles || [], styles = lib.styles || [];
  openModal(`<div class="m-head"><h3>设定图模板 · 图片生成通用</h3><button class="btn ghost sm" data-close>✕</button></div>
    <div class="m-body" style="max-height:70vh;overflow:auto">
      <div class="tiny muted" style="line-height:1.85;margin-bottom:10px">${esc(lib.note || '图片生成专用通用提示词，人物 / 动物 / 场景 / 物件都适用。')}</div>
      <div class="panel"><h3>线稿风格句 <span class="tag gray">只读 · ${lines.length} 条</span></h3>
        <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">线稿画法时整行换成这段；只勾轮廓与结构，不上色、不写颜色词。固定配方，不能改。</div>
        ${lines.map(s => `<div class="sheet-block"><div class="bh">${esc(s.name)}<span class="mono">${esc(s.id)}</span></div>
          <textarea spellcheck="false" rows="3" readonly>${esc(s.text)}</textarea></div>`).join('')}</div>
      <div class="panel"><h3>风格预设 <span class="tag">${styles.length} 类</span></h3>
        <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">可编辑。画种 + 材质笔触 + 色调 + 光线，一两句；对任意主体生效。可新增或删除。</div>
        <div id="imgStyleRows">${styles.map(s => `<div class="style-row" data-strow="1"><input class="input sname" data-istn value="${esc(s.name)}">
          <input class="input" data-istt value="${esc(s.text)}"><button class="btn sm ghost istdel" title="删除">✕</button></div>`).join('')}</div>
        <div class="row" style="margin-top:10px"><button class="btn sm ghost" id="imgTplAdd">＋ 新增预设</button>
          <span style="flex:1"></span><button class="btn primary sm" id="imgTplSave">保存</button></div></div>
    </div>`, true);
  $('#imgTplAdd').onclick = () => {
    const row = document.createElement('div'); row.className = 'style-row'; row.dataset.strow = '1';
    row.innerHTML = '<input class="input sname" data-istn placeholder="预设名"><input class="input" data-istt placeholder="画种+笔触+色调+光线"><button class="btn sm ghost istdel">✕</button>';
    $('#imgStyleRows').appendChild(row); bindDel();
  };
  function bindDel() {
    $$('.istdel').forEach(b => b.onclick = () => b.closest('.style-row').remove());
  }
  bindDel();
  $('#imgTplSave').onclick = async () => {
    const styles2 = $$('#imgStyleRows .style-row').map(row => ({
      name: ($('[data-istn]', row) || {}).value || '', text: ($('[data-istt]', row) || {}).value || '',
    })).filter(s => s.name.trim() && s.text.trim());
    if (!styles2.length) { toast('至少留一个风格预设'); return; }
    const res = await api('/api/image-style/library', { method: 'PUT', body: { ...lib, styles: styles2 } });
    if (!res.ok) { toast(res.reason || '没存成'); return; }
    S.imageStyleLib = res.data; closeModal(); refreshComposer(); toast('通用模板已保存，创作台立即生效');
  };
}
/* compose_image_prompt 的产物形如「画面风格：xxx。\n<用户原话>」，线稿还会在末尾加
   「（线稿硬性要求：…）」。基于上一条时若直接回填这段，再生成会被二次拼装，旧线稿句
   写死在开头（用户报的「4 个线稿选项切换无效」）。新任务服务端会另存 rawPrompt；这里只
   是给没有 rawPrompt 的老任务兜底还原。只剥我们自己固定的前后缀，绝不动用户原生输入。 */
function stripComposedPrompt(p) {
  if (typeof p !== 'string' || !p) return p;
  let s = p;
  const tail = '（线稿硬性要求：';
  const ti = s.lastIndexOf(tail);
  if (ti >= 0 && s.endsWith('）')) s = s.slice(0, ti);
  if (s.startsWith('画面风格：')) {
    const nl = s.indexOf('\n');
    if (nl >= 0) s = s.slice(nl + 1);
  }
  return s;
}
/* 需求1：和角色设定图一样给出「看完整提示词」。预览与提交共用服务端 compose_image_prompt，
   前端不自己拼一份，免得通用模板改了以后两边显示不一致。 */
function imgPayload() {
  const f = imgForm();
  // 图生图隐藏的是「风格」行（style），不是「画法」行（ink=彩色/线稿）。
  // 所以这里只清空草稿里残留的旧风格文字，画法与线稿风格句必须按用户选择保留——
  // 否则选了线稿也会被强制成彩色，线稿风格句拼不进提示词（用户报的回归）。
  if (UI.mode === 'i2i') {
    return { ink: f.ink, lineStyle: f.ink === 'line' ? f.lineStyle : null,
             styleCustom: true, style: '', styleText: '' };
  }
  return { ink: f.ink, lineStyle: f.ink === 'line' ? f.lineStyle : null,
           styleCustom: !!f.styleCustom, style: f.styleCustom ? '' : f.style,
           styleText: f.styleCustom ? f.styleText : '' };
}
function imgPreviewHTML() {
  const p = UI.imgPreview;
  if (!p) return '<div class="pvhead">正在组装提示词…</div>';
  if (p.error) return `<div class="pvhead bad">✕ ${esc(p.error)}</div>`;
  return `<div class="pvhead"><b>图片生成</b><span class="tiny muted">共 ${String((p.prompt || '').length)} 字 · 服务端实际提交的那条</span></div>
    <pre>${esc(p.prompt || '')}</pre>`;
}
async function refreshImgPreview() {
  const res = await api('/api/image/preview', { body: { prompt: serializeEditor(), image: imgPayload() } });
  UI.imgPreview = res.ok ? (res.data || {}) : { error: res.reason || '组装失败' };
  const box = $('#imgPv');
  if (box) { box.innerHTML = imgPreviewHTML(); bindImgPreviewButtons(); }
  else refreshComposer();
}
function bindImgPreviewButtons() {
  const cp = $('#imgCopy');
  if (cp) cp.onclick = async () => {
    try { await navigator.clipboard.writeText((UI.imgPreview && UI.imgPreview.prompt) || ''); toast('整条提示词已复制'); }
    catch (e) { toast('复制失败，手动选中复制一下'); }
  };
}
function viewDrawer() {
  // 手机上这是右侧抽屉：给一个看得见的标题 + 关闭按钮，别让人只能靠点遮罩
  const running = runningEntry();
  const n = chatJobs().filter(j => doneRuns(j).length).length;
  return `<div class="drawer">
    <div class="sheet-hd"><b>状态与任务</b>
      <button class="drawer-min-btn" data-collapse="drawer" title="${UI.drawerMin ? '展开任务栏' : '收起任务栏'}">${UI.drawerMin ? '«' : '»'}</button>
      <button class="sheet-x" data-sheet-close title="收起">✕</button></div>
    <div class="drawer-stub">
      <button class="drawer-min-btn" data-collapse="drawer" title="展开任务栏">«</button>
      <span class="ds-dot ${running ? 'busy pulse' : ''}"></span>
      <span class="ds-n">${n}</span>
      <span class="ds-lb">任务</span>
    </div>
    <div class="drawer-fixed">${drawerFixed()}</div>
    <div class="drawer-scroll" id="drawerBody">${drawerList()}</div></div>`;
}
/* 右栏固定头：ComfyUI 状态 + 执行队列 + 本会话任务的「全选/批量删除」。
   固定住，这样滚到任务列表中间也能看到状态、能点全选删除（需求 #10）。 */
function drawerFixed() {
  const running = runningEntry(), queued = queuedEntries();
  const rows = drawerVisible();
  return `<h4>ComfyUI</h4>
    <div class="cstate ${running ? 'busy' : ''}">
      <span class="dot ${running ? 'a pulse' : 'g'}"></span>
      <div class="t"><b>${running ? '正在跑「' + esc(running.r.grade || '默认') + '」' : '空闲'}</b>
        <span>${running ? `${esc(running.j.capName)} · <i data-drawerpct="${running.r.id}">${Math.round(running.r.pct || 0)}%</i>` : '可以接新的生成'}</span></div>
    </div>
    ${queued.length ? `<h4 style="margin-top:14px">执行队列 ${queued.length} 个</h4>
      ${queued.map((x, i) => `<div class="q-item"><span class="qn">${i + 1}</span>
        <div class="t"><b>${esc(x.r.grade || '默认')}</b><span>${esc(x.j.capName)} · ${esc(x.r.wf)}</span></div></div>`).join('')}` : ''}
    <div class="task-head"><h4>会话任务</h4>
      <label class="seltasks" title="全选"><input type="checkbox" id="tall" ${rows.length && UI.selTasks.size === rows.length ? 'checked' : ''}><span class="lbl">全选</span></label>
      <button class="task-del" id="tbatch" title="批量删除" ${UI.selTasks.size ? '' : 'disabled'}>
        <span class="lbl">批量删除（${UI.selTasks.size}）</span>
        <span class="mic">🗑${UI.selTasks.size ? `<i>${UI.selTasks.size}</i>` : ''}</span></button></div>`;
}
/* 右栏滚动区：会话任务列表 + 失败 + 归档说明。
   每条任务底部加一行缩略图（找不到原图用图标），高度一致（需求 #11）。
   1.04：整条都可以点（除删除 / 定位按钮）来勾选，勾选框和标题左对齐。 */
function taskThumbHTML(j) {
  const runs = doneRuns(j);
  const run = runs[0] || null;
  // 缩略图底部一行：有成品就显示成品图，没有就按能力类型给占位图标
  if (run) {
    const kind = ((run.files || [])[0] || {}).kind || run.media || 'image';
    if (kind === 'video') return `<div class="task-thumbs"><span class="tthumb-ph">🎬</span></div>`;
    if (kind === 'audio') return `<div class="task-thumbs"><span class="tthumb-ph">🎵</span></div>`;
    return `<div class="task-thumbs"><img src="${imgUrl(run.id, 0)}" alt="成品缩略图" loading="lazy"
      onerror="this.style.display='none';this.nextElementSibling.style.display='inline'"><span class="tthumb-ph" style="display:none">🖼</span></div>`;
  }
  const cap = j.cap || '';
  const icon = cap.startsWith('video') || cap === 'canvas.video' ? '🎬' : cap.startsWith('audio') ? '🎵' : '🖼';
  return `<div class="task-thumbs"><span class="tthumb-ph">${icon}</span></div>`;
}
function drawerJobs() {
  // 需求 4：会话任务里出现的每一条（成品 / 生成中 / 失败）都要有 checkbox，
  // 所以这里把「还有任务在跑、暂时没有成品」的对话也算进来。
  return chatJobs().filter(j => doneRuns(j).length || jobRuns(j).length);
}
/* 历史攒到几百上千条时，右栏一次性铺开会把浏览器渲染进程拖崩（实测 900+ 条会白屏）。
   默认只渲染最近 120 条，「加载更早」再往下翻，既保证每条都有勾选框，也不会卡死。 */
const DRAWER_PAGE = 120;
let _drawerShown = DRAWER_PAGE;
function drawerVisible() {
  const all = drawerJobs();
  return all.length > _drawerShown ? all.slice(all.length - _drawerShown) : all;
}
function drawerList() {
  // 需求 4：只要在任务栏里出现（成品 / 生成中 / 失败 / 已取消）就必须有 checkbox。
  // 之前 canceled 这类既不算 pending、也不算 done/failed，整条被漏渲染且没有勾选框。
  const all = drawerVisible();
  const waiting = all.filter(j => !doneRuns(j).length && pendRuns(j).length);
  const doneJobs = all.filter(j => doneRuns(j).length);
  const failJobs = all.filter(j => !doneRuns(j).length && !pendRuns(j).length && jobRuns(j)[0] && jobRuns(j)[0].state === 'failed');
  const otherJobs = all.filter(j => !doneRuns(j).length && !pendRuns(j).length
    && !(jobRuns(j)[0] && jobRuns(j)[0].state === 'failed'));
  // 需求 4/6：每条任务（含失败、生成中、已取消）左侧都有 checkbox；右侧定位与 ✕ 右对齐竖排对齐
  const row = (j, state) => `<div class="task-item ${UI.selTasks.has(j.id) ? 'sel' : ''}" data-taskrow="${j.id}">
      <div class="ti-top"><input type="checkbox" class="tsel" data-tsel="${j.id}" ${UI.selTasks.has(j.id) ? 'checked' : ''} aria-label="选中 ${esc(j.capName)}">
      <span class="dot ${state === 'fail' ? 'r' : state === 'wait' ? 'a pulse' : state === 'done' ? 'g' : 'x'}"></span><div class="t"><b>${esc(j.capName)}</b><span>${state === 'fail'
        ? esc(((jobRuns(j)[0].err) || {}).short || '没跑成')
        : state === 'wait' ? `生成中 · ${esc(j.wf)}${j.src === 'canvas' ? ' · 画布' : ''}`
        : state === 'done' ? `${doneRuns(j).length} 个成品 · ${esc(j.wf)}${j.src === 'canvas' ? ' · 画布' : ''}`
        : `已取消 · ${esc(j.wf || '')}${j.src === 'canvas' ? ' · 画布' : ''}`}</span></div>
      <span class="ti-acts"><button class="tjump" data-jump="${j.id}" title="定位到这条对话">⌖</button>
      <button class="tdel" data-jobdel="${j.id}" title="删除这条对话">✕</button></span></div>
      ${state === 'fail' ? '' : taskThumbHTML(j)}</div>`;
  const hidden = drawerJobs().length - all.length;
  return `${hidden > 0 ? `<div style="padding:4px 8px"><button class="btn ghost sm" data-drawer-more style="width:100%">加载更早的 ${Math.min(hidden, DRAWER_PAGE)} 条（还有 ${hidden} 条）</button></div>` : ''}
    ${waiting.length ? waiting.map(j => row(j, 'wait')).join('') : ''}
    ${doneJobs.length ? doneJobs.map(j => row(j, 'done')).join('')
      : (waiting.length ? '' : '<div class="tiny muted" style="padding:0 8px">还没有成品</div>')}
    ${failJobs.length ? `<h4 style="margin-top:14px">失败 ${failJobs.length}</h4>${failJobs.map(j => row(j, 'fail')).join('')}` : ''}
    ${otherJobs.length ? `<h4 style="margin-top:14px">已取消 / 未完成 ${otherJobs.length}</h4>${otherJobs.map(j => row(j, 'other')).join('')}` : ''}
    <div class="hr"></div>
    <div class="tiny muted" style="padding:0 8px;line-height:1.7">成品都存放在：<br><span class="mono" style="word-break:break-all">${esc(S.service.archiveDir)}</span></div>
    <div class="tiny muted" style="padding:8px;line-height:1.7">删对话只移记录、不删文件；要删文件去「历史记录」。</div>`;
}
function drawerInner() { return drawerFixed() + drawerList(); }
function bindDrawer() {
  // 整条任务都能点：点哪儿都算「选中这条」（除删除 / 定位按钮）。
  // 以前只有那个小勾选框能点，手指在手机上基本点不中。
  $$('[data-taskrow]').forEach(el => el.onclick = e => {
    if (e.target.closest('[data-jobdel]') || e.target.closest('[data-jump]')) return;
    if (e.target.closest('input[type=checkbox]')) return;      // 勾选框自己会 onchange
    const id = el.dataset.taskrow;
    if (UI.selTasks.has(id)) UI.selTasks.delete(id); else UI.selTasks.add(id);
    refreshDrawer();
  });
  $$('[data-jump]').forEach(el => el.onclick = async e => {
    e.stopPropagation();
    UI.sheet = false;                                  // 手机端：从抽屉跳过去先把抽屉收起来，否则看不到目标卡片
    const app = $('.app'); if (app) app.classList.remove('sheet-open');
    const id = el.dataset.jump;
    const job = (S.jobs || []).find(x => x.id === id);
    // 需求 5：画布任务不在对话流里，定位要切到画布、打开那个项目、把节点挪到视野中间
    if (job && job.canvas && job.canvas.project && window.CanvasModule) {
      if (UI.route !== 'canvas') {
        UI.route = 'canvas'; UI._navPending = true;
        await saveDraft({ route: 'canvas' });
        UI._navPending = false;
        render();
      }
      await CanvasModule.focusNode(job.canvas.project, job.canvas.node);
      toast('已定位到画布节点');
      return;
    }
    let node = document.getElementById(id);
    // 定位增强：聊天区只渲染当前能力的任务；跨能力定位先切到那条任务所属能力再找卡片
    if (!node) node = $(`[data-jobid="${id}"][data-jobcard]`) || $(`[data-job-id="${id}"]`);
    if (!node && job && job.cap && job.cap !== UI.capId) {
      UI.capId = job.cap; UI._userSwitched = true;
      await saveDraft({ capId: UI.capId });
      render();
      node = document.getElementById(id);
    }
    if (!node) { toast('这条对话不在当前列表里，去「历史」看看'); return; }
    // 需求 5：别只靠 scrollIntoView —— 聊天区自己是滚动容器，直接算位置更稳
    const box = $('#chatScroll');
    if (box && box.contains(node)) {
      const top = node.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
      box.scrollTo({ top: Math.max(0, top - box.clientHeight / 2 + node.clientHeight / 2), behavior: 'smooth' });
    } else {
      node.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    node.classList.remove('jump-flash'); void node.offsetWidth; node.classList.add('jump-flash');
    node.style.outline = '2px solid #2f6bff';
    setTimeout(() => { node.style.outline = 'none'; node.classList.remove('jump-flash'); }, 2200);
  });
  // 会话任务：加载更早的一页（默认只渲染最近 120 条，避免大历史把页面拖崩）
  $$('[data-drawer-more]').forEach(el => el.onclick = e => {
    e.stopPropagation();
    _drawerShown += DRAWER_PAGE;
    refreshDrawer();
  });
  // 会话任务：批量删除
  const tall = $('#tall');
  if (tall) tall.onchange = () => {
    $$('[data-tsel]').forEach(el => { if (tall.checked) UI.selTasks.add(el.dataset.tsel); else UI.selTasks.delete(el.dataset.tsel); });
    refreshDrawer();
  };
  $$('[data-tsel]').forEach(el => {
    el.onclick = e => e.stopPropagation();           // 勾选不要冒泡成「再切一次」
    el.onchange = () => { if (el.checked) UI.selTasks.add(el.dataset.tsel); else UI.selTasks.delete(el.dataset.tsel); refreshDrawer(); };
  });
  // 左右栏折叠：任何尺寸都能收起来，状态存进 UI 草稿
  $$('[data-collapse]').forEach(el => el.onclick = e => {
    e.stopPropagation();
    const which = el.dataset.collapse;
    if (which === 'rail') UI.railMin = !UI.railMin;
    else UI.drawerMin = !UI.drawerMin;
    saveDraft({ railMin: !!UI.railMin, drawerMin: !!UI.drawerMin });
    const box = $('.studio');
    if (box) {
      box.classList.toggle('rail-min', !!UI.railMin);
      box.classList.toggle('drawer-min', !!UI.drawerMin);
    }
    render();
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
  // 固定头（全选勾选/删除计数）和滚动列表都要更新，否则勾选后计数不刷新
  const f = $('.drawer-fixed'); if (f) f.innerHTML = drawerFixed();
  const d = $('#drawerBody'); if (d) d.innerHTML = drawerList();
  bindDrawer();
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
  // 需求 7：进页面直接落到「最后一条生成结果」，所以滚到底而不是滚到顶。
  const box = $('#chatScroll');
  if (box) {
    box.scrollTop = box.scrollHeight;
    // 缩略图是异步解码的，首帧算出来的高度不够，再补一次
    requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
    return;
  }
  const list = (S.jobs || []).filter(j => j.cap === UI.capId);
  const last = list.length ? document.getElementById(list[list.length - 1].id) : null;
  if (last) last.scrollIntoView({ block: 'end' }); else window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------ 创作台交互 */
function setMode(m) {
  // 文生图 / 图生图 / 扩图显式切换：模式决定走哪条子工作流与哪套版式
  const cap = currentCap();
  const canExpand = isExtSheetCap(cap) && extKindOf(cap) === 'scene';
  if (!(m === 't2i' || m === 'i2i' || (m === 'expand' && canExpand)) || UI.mode === m) return;
  UI.mode = m;
  if (m === 't2i') { UI.refs = []; }                               // 文生图不传图，清掉已上传参考
  // 需求 #7：图生图只允许自定义风格；切过去就强制选中「自定义…」
  if (m === 'i2i' && isSheetCap(cap)) { UI.sheetForm = UI.sheetForm || {}; UI.sheetForm.styleCustom = true; }
  if (isExtSheetCap(cap)) {
    const f = extForm();
    f.layout = '';                                                  // 版式按新模式重新选第一项
    f.grade = '默认';
    if (m === 'expand') { f.ink = 'color'; f.nochar = 'yes'; f.ref = 'none'; f.styleCustom = false; }
    else if (m === 'i2i') { f.styleCustom = true; if (extKindOf(cap) === 'prop') f.ref = 'lock'; }
    else { f.styleCustom = false; if (extKindOf(cap) === 'prop') f.ref = 'none'; }
    extForm();
  }
  const g = currentGrades(cap);                                     // 档位落到当前工作流的第一档（若当前档位不属于它）
  if (g.length && !g.find(x => x.name === UI.params.grade)) UI.params.grade = g[0].name;
  saveDraft(); refreshComposer();
  toast(m === 'expand' ? '已切到「扩图」：参考图必传，提示词可留空' : m === 'i2i' ? '已切到「图生图」：可上传参考图' : '已切到「文生图」：纯文字生成');
}
function bindStudio() {
  $$('[data-cap]').forEach(el => el.onclick = async () => {
    const target = (S.capabilities || []).find(x => x.id === el.dataset.cap);
    if (!target) return;
    if (target.kind === 'canvas') {
      // 进画布：左右两栏保留，只把中间换成画布；能力选择不动，方便来回切
      UI._userSwitched = true;
      UI.navOpen = false;
      UI.route = 'canvas';
      UI._navPending = true;
      await saveDraft({ route: 'canvas' });
      UI._navPending = false;
      render();
      return;
    }
    if (!capUsable(target) || !target.enabled) { toast('这个能力还没配置，等下个版本开放'); return; }
    if (UI.route === 'canvas') UI.route = 'studio';
    UI._userSwitched = true;
    // 参考图是按能力/上下文理解的：切能力先清掉上一张卡的图，再恢复目标能力自己的草稿。
    // 否则从场景设定图切到道具设定图时会带着场景的 4 张图，直接撞上 maxRefs=1。
    UI.refs = [];
    UI.capId = el.dataset.cap; loadDraft();
    UI.navOpen = false;                   // 手机上选完能力把抽屉收起来
    UI.autoScroll = true;                 // 需求 7：进这一页直接看到最后一条结果
    saveDraft(); render();
  });
  $$('[data-sample]').forEach(el => el.onclick = () => {
    if (isSheetCap(currentCap())) {          // 设定图的示例是「角色描述」，不是整条提示词
      UI.sheetForm.character = el.dataset.sample;
      const sc = $('#sheetChar'); if (sc) { sc.value = UI.sheetForm.character; sc.focus(); }
      saveDraft(); refreshComposer(); return;
    }
    if (isExtSheetCap(currentCap())) {        // 1.03 示例填到场景/道具描述框
      UI.extForm.text = el.dataset.sample;
      const et = $('#extText'); if (et) { et.value = UI.extForm.text; et.focus(); }
      UI.extPreview = null; saveDraft(); refreshComposer(); return;
    }
    if (isBoardCap(currentCap())) {           // 故事板示例填到当前选中的格子
      const f = boardForm(); f.cells[f.sel] = el.dataset.sample;
      const bc = $('#boardCellInput'); if (bc) { bc.value = f.cells[f.sel]; bc.focus(); }
      UI.boardPreview = null; saveDraft(); refreshComposer(); return;
    }
    const ed = $('#promptInput'); if (!ed) return;
    UI.prompt = el.dataset.sample; ed.innerHTML = editorInnerHTML(UI.prompt);
    ed.focus(); onEditorInput();
  });
  $$('[data-mode]').forEach(el => el.onclick = () => setMode(el.dataset.mode));
  // 图片生成的画法 / 风格（只在普通图片能力下存在）
  $$('[data-ink]').forEach(el => el.onclick = () => {
    imgForm().ink = el.dataset.ink; saveDraft(); refreshComposer();
  });
  const imgLine = $('#imgLine');
  if (imgLine) imgLine.onchange = () => { imgForm().lineStyle = imgLine.value; saveDraft(); refreshComposer(); };
  const imgStyle = $('#imgStyle');
  if (imgStyle) imgStyle.onchange = () => {
    const f = imgForm();
    if (imgStyle.value === '__custom') { f.styleCustom = true; f.style = ''; }
    else { f.styleCustom = false; f.style = imgStyle.value; }
    saveDraft(); refreshComposer();
  };
  const imgStyleText = $('#imgStyleText');
  if (imgStyleText) imgStyleText.oninput = () => {
    imgForm().styleText = imgStyleText.value; scheduleDraft();
    if (UI.imgOpen) refreshImgPreview();
  };
  const imgPvBtn = $('#imgPvBtn');
  if (imgPvBtn) imgPvBtn.onclick = () => keepScroll(async () => {
    UI.imgOpen = !UI.imgOpen; UI.imgPreview = null; render();
    if (UI.imgOpen) await refreshImgPreview();
  });
  // 需求2：图片生成「设定图模板」入口（通用模板，只有线稿风格句 + 风格预设两部分）
  $$('[data-sheet-template]').forEach(el => el.onclick = () => {
    if (currentCap() && currentCap().id === 'image.generate') openImageTemplateModal();
  });
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
    if (isExtSheetCap(currentCap())) {
      const slots = (UI.extForm.refSlots || {});
      Object.keys(slots).forEach(k => { if (slots[k] === el.dataset.rmref) delete slots[k]; });
      UI.extPreview = null;
    }
    UI.prompt = serializeEditor();          // 参考图变少，chip 序号要重算；别让 refreshComposer 用旧 prompt 重建
    saveDraft(); refreshComposer();
  });
  $$('[data-refat]').forEach(el => el.onclick = e => {
    e.stopPropagation();
    // 设定图：@ = 把这张参考图引用进「已引用」条（图生图靠 ref 模式 + refs 数组生效，
    // 不塞 <imageN> 令牌）；图片生成：@ = 往 prompt 编辑器插 chip（走 <imageN>）。
    if (isSheetCap(currentCap())) {
      UI.sheetForm = UI.sheetForm || {};
      UI.sheetForm.refChips = UI.sheetForm.refChips || [];
      if (!UI.sheetForm.refChips.includes(el.dataset.refat)) UI.sheetForm.refChips.push(el.dataset.refat);
      refreshComposer();
      toast('已引用到输入框，生成时按「参考」方式使用');
      return;
    }
    if (isBoardCap(currentCap())) {
      const f = boardForm(), idx = UI.refs.findIndex(r => r.id === el.dataset.refat) + 1;
      const token = `图 ${idx}`;
      const input = $('#boardCellInput');
      if (input) {
        const cur = input.value, p = input.selectionStart ?? cur.length;
        f.cells[f.sel] = cur.slice(0, p) + token + cur.slice(input.selectionEnd ?? p);
        input.value = f.cells[f.sel];
        input.focus();
        try { input.setSelectionRange(p + token.length, p + token.length); } catch (_) {}
      } else f.cells[f.sel] = String(f.cells[f.sel] || '') + token;
      UI.boardPreview = null; saveDraft();
      toast(`已在第 ${f.sel + 1} 格写入「${token}」`);
      return;
    }
    insertRefChip(el.dataset.refat);
  });
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
      if ((j.sheet || {}).kind === 'board') {
        const old = j.sheet;
        UI.mode = 'i2i';
        UI.boardForm = { ...UI.boardForm, layout: old.layout || 'G4', ink: old.ink || 'color',
          lineStyle: old.lineStyle || '', grade: old.grade || (j.runs[0] || {}).grade || '默认',
          cells: [...(old.cells || [])], sel: 0 };
        UI.boardOpen = false; UI.boardPreview = null; boardForm();
      } else if ((j.sheet || {}).kind === 'scene' || (j.sheet || {}).kind === 'prop') {
        // 1.03 场景/道具：整台表单带回来，槽位 id 按新旧参考图顺序重映射
        const old = j.sheet || {}, slots = {};
        const oldOrder = [...new Set(Object.values(old.refSlots || {}))];
        Object.entries(old.refSlots || {}).forEach(([slot, oldId]) => {
          const at = oldOrder.indexOf(oldId);
          if (refs[at]) slots[slot] = refs[at].id;
        });
        UI.mode = old.mode || UI.mode;
        UI.extForm = { ...UI.extForm, ...old, refSlots: slots,
                       grade: old.grade || (j.runs[0] || {}).grade || '默认' };
        extForm(); UI.extPreview = null;
      } else {
        // 设定图：把整台表单（版式/画法/参考方式/档位/角色行）带回来
        UI.sheetForm = { ...UI.sheetForm, ...j.sheet };
        // 老任务可能没记档位，按它当时用的那一档回填
        if (!UI.sheetForm.grade) UI.sheetForm.grade = (j.runs[0] || {}).grade || '默认';
        // 参考图重新登记后 id 变了：把新 refs 全部设为「已引用」，缩略图 chip 才能带回来
        UI.sheetForm.refChips = refs.map(r => r.id);
        UI.sheetPreview = null;
      }
    } else {
      // 普通图片生成：把「画法 / 线稿风格 / 风格」整条选项也带回来。
      // 提示词用服务端另存的 rawPrompt（compose 前的用户原话），没有则保守剥掉
      // 我们自己的前后缀，避免把已拼装的句子当原话回填造成二次拼装。
      UI.prompt = j.rawPrompt || stripComposedPrompt(j.prompt || '');
      if (j.image && typeof j.image === 'object') {
        UI.imgForm = { ...(UI.imgForm || {}), ...j.image };
        imgForm();  // 补默认值 / 校验 lineStyle 合法
      }
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
  bindExtSheetComposer();
  if (isBoardCap(currentCap())) bindBoardComposer();
  if (isVideoCap(currentCap()) || isAudioCap(currentCap())) bindMediaComposer();
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
/* 重绘输入区前把「正在编辑的内容 + 光标位置」拍照，重绘后原样还回去。
   普通能力的 contenteditable、设定图的角色描述框都要照顾到：
   页面每秒会按状态签名局部重绘，漏掉哪一个，那个框就会出现「刚打/刚粘的字或焦点消失」。 */
function snapshotComposerFocus() {
  const ae = document.activeElement;
  const snap = { prompt: false, sheet: null, style: null, ext: null, extStyle: null, boardCell: null };
  if (ae && ae.id === 'promptInput') { UI.prompt = serializeEditor(); snap.prompt = true; }
  if (ae && ae.id === 'sheetChar') {
    UI.sheetForm.character = ae.value;
    snap.sheet = { start: ae.selectionStart, end: ae.selectionEnd };
  }
  if (ae && ae.id === 'sheetStyleText') {
    UI.sheetForm.styleText = ae.value;
    snap.style = { start: ae.selectionStart, end: ae.selectionEnd };
  }
  if (ae && ae.id === 'extText') {
    UI.extForm.text = ae.value;
    snap.ext = { start: ae.selectionStart, end: ae.selectionEnd };
  }
  if (ae && ae.id === 'extStyleText') {
    UI.extForm.styleText = ae.value;
    snap.extStyle = { start: ae.selectionStart, end: ae.selectionEnd };
  }
  if (ae && ae.id === 'boardCellInput') {
    const f = boardForm();
    f.cells[f.sel] = ae.value;
    snap.boardCell = { sel: f.sel, start: ae.selectionStart, end: ae.selectionEnd };
  }
  return snap;
}
function restoreComposerFocus(snap) {
  if (snap.prompt) { const t2 = $('#promptInput'); if (t2) t2.focus(); }
  if (snap.sheet) {
    const c2 = $('#sheetChar'); if (c2) {
      c2.focus();
      const n = c2.value.length;
      try { c2.setSelectionRange(Math.min(snap.sheet.start ?? n, n), Math.min(snap.sheet.end ?? n, n)); } catch (_) {}
    }
  }
  if (snap.style) {
    const s2 = $('#sheetStyleText'); if (s2) {
      s2.focus();
      const n = (s2.value || '').length;
      try { s2.setSelectionRange(Math.min(snap.style.start ?? n, n), Math.min(snap.style.end ?? n, n)); } catch (_) {}
    }
  }
  if (snap.ext) {
    const e2 = $('#extText'); if (e2) {
      e2.focus();
      const n = (e2.value || '').length;
      try { e2.setSelectionRange(Math.min(snap.ext.start ?? n, n), Math.min(snap.ext.end ?? n, n)); } catch (_) {}
    }
  }
  if (snap.extStyle) {
    const s3 = $('#extStyleText'); if (s3) {
      s3.focus();
      const n = (s3.value || '').length;
      try { s3.setSelectionRange(Math.min(snap.extStyle.start ?? n, n), Math.min(snap.extStyle.end ?? n, n)); } catch (_) {}
    }
  }
  if (snap.boardCell) {
    const b = $('#boardCellInput'); if (b) {
      b.focus();
      const n = (b.value || '').length;
      try { b.setSelectionRange(Math.min(snap.boardCell.start ?? n, n), Math.min(snap.boardCell.end ?? n, n)); } catch (_) {}
    }
  }
}
function refreshComposer() {
  const node = $('.composer'); if (!node) return;
  const snap = snapshotComposerFocus();
  node.outerHTML = viewComposer(); bindStudio();
  restoreComposerFocus(snap);
  // outerHTML 整块替换后新 DOM 没有折叠把手，立即补注入并恢复折叠态
  if (typeof enhanceFoldHandles === 'function') enhanceFoldHandles();
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
  if (UI.mode !== 'i2i' && !isBoardCap(currentCap())) { toast('文生图模式不带参考图，先切到「图生图」再上传'); return; }  // 需求 #1：文生图不传图
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
    // 设定图：角色描述跟着清空（和「描述框发完清零」一致），版式/画法/参考方式保持不变
    if (isSheetCap(currentCap())) {
      UI.sheetForm.character = ''; UI.sheetPreview = null;
      UI.sheetForm.refChips = [];
      const sc = $('#sheetChar'); if (sc) sc.value = '';
    } else if (isExtSheetCap(currentCap())) {
      UI.extForm.text = ''; UI.extForm.refSlots = {}; UI.extPreview = null;
      const et = $('#extText'); if (et) et.value = '';
    } else if (isBoardCap(currentCap())) {
      boardForm().cells = boardForm().cells.map(() => ''); UI.boardPreview = null;
      const bc = $('#boardCellInput'); if (bc) bc.value = '';
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
  if (isExtSheetCap(cap)) { await doSendExtSheet(cap); return; }
  if (isBoardCap(cap)) { await doSendBoard(cap); return; }
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
  const body = { capId: cap.id, prompt, grade, refs: UI.refs.map(r => r.id), wf };
  // 图片生成：画法 / 风格作为结构化输入交给服务端拼在提示词前面
  if (cap.id === 'image.generate') body.image = imgPayload();
  await submitAction('/api/tasks', body, '');
}

async function doSendSheet(cap) {
  // 设定图只校验「必填项」：角色描述 + 参考方式与参考图是否配对；其余交给服务端组装
  const f = sheetForm();
  const box = $('#inputbox');
  // 需求 #8：图生图纯转换（单人图转四视图 / 彩色转线稿）允许不写角色描述；
  // 文生图仍要求写角色描述。
  if (!String(f.character || '').trim() && sheetMode() === 't2i') {
    if (box) box.classList.add('err');
    const ch = $('#sheetChar'); if (ch) ch.focus();
    toast('先写角色描述：年龄身份 + 五官 + 发型 + 服装 + 标志物');
    return;
  }
  if (sheetMode() !== 't2i' && !UI.refs.length) {
    if (box) box.classList.add('err');
    toast('图生图要先上传一张参考图');
    return;
  }
  const grade = sheetGrade();
  const wf = (sheetCombo() || {}).wf || currentWorkflowName(cap);
  if (!wf) { toast('这个能力还没有对应的工作流'); return; }
  if (!grade) { toast('这个组合还没有档位'); return; }
  const body = {
    capId: cap.id, grade: grade.name, wf,
    refs: UI.refs.map(r => r.id), sheet: sheetPayload(),
  };
  const sent = await submitAction('/api/tasks', body, '');
  if (sent) toast(`已提交「${(sheetCombo() || {}).name || ''}」· ${grade.name}`);
}

async function doSendExtSheet(cap) {
  const f = extForm(), kind = extKindOf(cap), box = $('#inputbox');
  if (!extCanSend(cap)) {
    if (box) box.classList.add('err');
    toast(kind === 'scene' && UI.mode === 'expand' ? '扩图必须先在「参考图」槽位上传一张'
      : UI.mode === 't2i' ? '先写点描述再发送' : '写点描述并上传至少一张参考图');
    return;
  }
  const combo = extCombo(), grade = extGrade();
  if (!combo) { toast('这个版式还没有对应的组合'); return; }
  if (!grade) { toast('这个组合还没有档位'); return; }
  const body = {
    capId: cap.id, prompt: String(f.text || '').trim(), grade: grade.name, wf: combo.wf,
    refs: UI.refs.map(r => r.id), ext: extPayload(),
  };
  const sent = await submitAction('/api/tasks', body, '');
  if (sent) toast(`已提交「${combo.name || ''}」· ${grade.name}`);
}
async function doSendBoard(cap) {
  const f = boardForm(), box = $('#inputbox');
  if (!boardCanSend()) {
    if (box) box.classList.add('err');
    toast('至少写一格的画面描述再发送');
    const bc = $('#boardCellInput'); if (bc) bc.focus();
    return;
  }
  const combo = boardCombo(), grade = boardGrade();
  if (!combo) { toast('这个版式还没有对应的组合'); return; }
  if (!grade) { toast('这个组合还没有档位'); return; }
  const body = {
    capId: cap.id, grade: grade.name, wf: combo.wf,
    prompt: f.cells.filter(x => String(x || '').trim()).join('\n'),
    refs: UI.refs.map(r => r.id), board: boardPayload(),
  };
  const sent = await submitAction('/api/tasks', body, '');
  if (sent) toast(`已提交「${combo.name || ''}」· ${grade.name}`);
}
function openQueueModal(jobId) {
  const j = (S.jobs || []).find(x => x.id === jobId); if (!j) return;
  const cap = (S.capabilities || []).find(c => c.id === j.cap);
  const base = j.runs[0];
  /* 设定图：档位属于「这张卡所属的组合」，不是工作流。跨组合的档位一律不列——
     只有同组合的档位才保证提示词/参考/工作流完全一样，只换参数。 */
  const cid = (j.sheet || {}).combo || '';
  const capKind = cap && cap.extKind;
  // 故事板的档位属于「版式×画法」组合，和设定图一样不能跨组合拿
  const comboPool = capKind ? (((S && S.extCombos) || {})[capKind] || [])
    : (cap && cap.id === 'board.story' ? boardCombos() : sheetCombos());
  const combo = cid ? comboPool.find(c => c.id === cid) : null;
  const wf = (cap && (cap.workflows || []).find(w => w.name === base.wf)) || (cap && cap.workflows && cap.workflows[0]) || {};
  // 视频 / 音频的档位按模式存在 S.video / S.audio 里，不在这条工作流的 grades 上
  const mediaGrades = mediaGradesFor(j);
  const grades = combo ? (combo.grades || []) : (mediaGrades || wf.grades || []);
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
            : mediaGrades
              ? (g.note || [g.ratio, g.res, g.steps ? g.steps + ' 步' : ''].filter(Boolean).join(' · '))
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
  // 需求7：视频成品在放大弹窗里要有真正的播放器；图片仍是 img
  const f0 = ((r.files || [])[0] || {});
  const isVideo = (f0.kind || r.media) === 'video';
  const isAudio = (f0.kind || r.media) === 'audio';
  const preview = isVideo
    ? `<video id="zoomMedia" src="${src}" controls autoplay playsinline style="width:100%;max-height:72vh;display:block;background:#0b0f16"></video>`
    : isAudio
      ? `<audio id="zoomMedia" src="${src}" controls style="width:100%;display:block"></audio>`
      : `<img id="zoomImg" src="${src}" style="width:100%;display:block" title="${isVideo || isAudio ? '成品预览' : 'Ctrl+C 复制这张图'}">`;
  const mask = openModal(`<div class="m-head"><h3>成品预览</h3>
      <div class="row"><button class="btn ghost sm" id="zoomExpand" title="放大预览（接近全屏）">⤢ 放大</button>
      <button class="btn ghost sm" data-close>✕</button></div></div>
    <div class="m-body" style="padding:0">${preview}</div>
    <div class="m-foot" style="flex-wrap:wrap">
      <span class="tiny muted mono" style="width:100%;margin-bottom:8px;word-break:break-all">${esc(path)}</span>
      <span class="tiny muted">种子 ${r.seed ?? j.seed ?? ''} · ${esc(r.grade || '默认')}${isVideo || isAudio ? '' : '<span class="zoomtip"> · Ctrl+C 复制图片</span>'}</span><span style="flex:1"></span>
      <button class="btn sm" id="openDir" title="在资源管理器里打开并选中这个文件">打开所在目录</button>
      <button class="btn sm primary" id="zoomDl">下载 ⬇</button></div>`, true);
  // 放大：给弹窗加全屏近似类（96vw/94vh），再点还原；图片随容器放大
  const modalEl = mask && mask.querySelector ? mask.querySelector('.modal') : null;
  const zoomBtn = $('#zoomExpand');
  if (zoomBtn) zoomBtn.onclick = () => {
    if (!modalEl) return;
    const on = modalEl.classList.toggle('zoomed');
    zoomBtn.textContent = on ? '⤡ 还原' : '⤢ 放大';
  };
  $('#openDir').onclick = async () => {
    const res = await api('/api/open-folder', { body: { path: path || S.service.archiveDir, select: true } });
    if (!res.ok) { toast(res.reason || '打不开所在目录'); return; }
    toast(res.data && res.data.focused ? '已在资源管理器里选中这张图' : '已打开所在目录');
  };
  $('#zoomDl').onclick = () => { window.location.href = '/api/download?run=' + encodeURIComponent(runId) + '&i=0'; };
  // 弹窗里 Ctrl+C 直接复制图片（焦点在输入框时让浏览器照常复制文字）
  const onKey = e => {
    if (e.key === 'Escape') { closeModal(); return; }
    if (isVideo || isAudio) return;                       // 音视频放大弹窗不提供复制图片
    if (!(e.ctrlKey || e.metaKey) || String(e.key).toLowerCase() !== 'c') return;
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    e.preventDefault();
    copyImageToClipboard(src, '成品图');
  };
  if (mask) { mask._onKey = onKey; document.addEventListener('keydown', onKey); }
}

/* ------------------------------------------------------------ 管理台 */
// 设定图模板不再是全局菜单项：它是角色设定图的子功能，入口挪到档位表里（模态框打开）。
// 保留 'sheet' 路由分支仅为兼容旧草稿里残留的 adminPage，不出现在菜单里。
const ADMIN_PAGES = [['caps', '能力与工作流'], ['grades', '档位表'], ['canvas', '视频生成画布'], ['prompts', '提示词模板库'], ['service', '服务设置'], ['diag', '自检']];
/* 「档位表」下挂的子菜单：能配档位的能力（有工作流的能力），点了直接跳到它的档位表。
   能力清单在 config.yaml 里是固定的，所以这里按实际配置生成，不写死。 */
function gradesCaps() {
  // 需求12：画布不设自己的挡位表，子菜单里也不该出现画布入口（画布节点共用创作台挡位）
  return (S.capabilities || []).filter(c => c.kind !== 'canvas' && (c.workflows || []).length);
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
  if (UI.adminPage === 'canvas') return pageCanvasAdmin();
  // 1.06：提示词模板库（独立模块，卡片列表 + 标签管理；速取窗由顶栏开关控制）
  if (UI.adminPage === 'prompts') return window.PromptLib ? window.PromptLib.viewAdmin() : '<div class="pt-loading">模板库正在加载…</div>';
  // 设定图 / 故事板的档位挂在「组合」上，不是工作流上，所以档位表页要换一套界面
  if (UI.adminPage === 'grades') {
    if (isSheetCap(cap) || isExtSheetCap(cap) || isBoardCap(cap)) return pageComboGrades(cap);
    if (isAudioCap(cap)) return pageAudioGrades(cap);
    return pageGrades(cap);
  }
  if (UI.adminPage === 'sheet') return pageSheet();
  if (UI.adminPage === 'service') return pageService();
  return pageDiag();
}
/* ------------------------------------------------------------ 管理台：视频生成画布 */
function fmtWhen(t) {
  if (!t) return '—';
  const d = new Date(t);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
/* 画布管理页的数据独立存放：S 每秒被 /api/state 整体替换，放 S 里会被冲掉 */
let CV_ADMIN = null;
const CV_VIDEO_MODES = ['t2v', 'fl2v', 'ref2v', 'board'];
const CV_AUDIO_MODES = ['music', 'cover', 'tts', 'clone'];

function cvLoadAdmin(force) {
  if (CV_ADMIN && !force) return;
  CV_ADMIN = { loading: true };
  Promise.all([api('/api/canvas'), api('/api/canvas/funcs'),
    api('/api/video/modes'), api('/api/audio/modes')]).then(async ([a, b, v, u]) => {
    const A = {
      projects: (a.data || {}).projects || [],
      funcs: (b.data || {}).funcs || [],
      workflows: (b.data || {}).workflows || [],
      video: v.data || {}, audio: u.data || {},
      vGrades: {}, aGrades: {},
    };
    const jobs = CV_VIDEO_MODES.map(m => api('/api/video/grades/' + m).then(r => { if (r.ok) A.vGrades[m] = r.data; }));
    CV_AUDIO_MODES.forEach(m => jobs.push(api('/api/audio/grades/' + m).then(r => { if (r.ok) A.aGrades[m] = r.data; })));
    await Promise.all(jobs);
    CV_ADMIN = A;
    // 只要画布管理页还在屏幕上就重绘一次。这里不能只看 UI.adminPage：
    // 每秒轮询会把服务端的 adminPage 盖回来，页面还停在加载中却不重绘。
    if (document.querySelector('.cvs-admin')) render();
  }).catch(() => { CV_ADMIN = null; toast('读取画布配置失败'); });
}

function cvVName(id) {
  const m = ((CV_ADMIN || {}).video || {}).modes || [];
  return (m.find(x => x.id === id) || {}).name || id;
}
function cvAName(id) {
  const m = ((CV_ADMIN || {}).audio || {}).modes || [];
  return (m.find(x => x.id === id) || {}).name || id;
}

function pageCanvasAdmin() {
  cvLoadAdmin();
  const A = CV_ADMIN;
  if (!A || A.loading) {
    return '<div class="cvs-admin"><div class="cv-card"><h3>视频生成画布</h3>'
      + '<div class="tiny muted">正在读取画布配置…</div></div></div>';
  }
  const V = A.video || {}, U = A.audio || {};
  const ratios = V.ratios || ['16:9', '9:16'];
  const reses = V.reses || ['480P', '768P'];
  const stepsList = V.steps || [4, 8, 20];
  const durs = U.durations || [10, 15, 24, 30, 45, 60, 90, 120];
  const vf = U.voiceFields || {};
  const wfOpts = sel => '<option value="">（不绑工作流）</option>'
    + (A.workflows || []).map(n => `<option value="${esc(n)}"${n === sel ? ' selected' : ''}>${esc(n)}</option>`).join('');

  /* ---- ① 生成子功能（需求13 / 1.05：画布上的每一个生成节点都在这里） ---- */
  const funcRow = f => {
    const on = f.enabled !== false;
    const target = f.capId || '';
    const hasTarget = !!(target && (S.capabilities || []).some(c => c.id === target));
    const gradeText = f.gradesFrom === 'combo'
      ? '档位按「版式 × 画法 × 参考」组合'
      : `节点档位：${(f.grades || []).map(g => esc(g.name)).join(' / ') || '—'}`;
    return `<div class="cv-row cv-fnode">
      <span class="em">${esc(f.em || '▩')}</span>
      <div class="t"><b>${esc(f.name || '')}</b><span>${esc(f.wf || '组合提交')} · ${gradeText}</span></div>
      <label class="cvsw" title="${on ? '点击停用这个节点' : '点击启用这个节点'}"><input type="checkbox" data-cvf-toggle="${f.id}" ${on ? 'checked' : ''}><i></i></label>
      <button class="btn sm" data-cvf-config="${f.id}" ${hasTarget ? '' : 'disabled title="还没有对应的子功能"'}>配置</button>
    </div>`;
  };
  // 管理台把「生成节点」按 图片 / 视频 / 音频 三组全部列出，和画布节点一一对应
  const funcGroups = ['图片', '视频', '音频'];
  const funcList = f => (A.funcs || []).filter(x => x.group === f);

  /* ---- ② 视频档位 ---- */
  const vTable = mode => {
    const rows = ((A.vGrades[mode] || {}).rows) || [];
    const body = rows.map((g, i) => {
      const lk = !!g.locked;
      return `<tr>
        <td>${lk ? `<b>${esc(g.name)}</b><span class="lock">🔒</span>` : `<input class="input" data-vr="name|${mode}|${i}" value="${esc(g.name)}">`}</td>
        <td>${lk ? esc(g.ratio || '') : `<select class="input" data-vr="ratio|${mode}|${i}">${ratios.map(x => `<option${x === g.ratio ? ' selected' : ''}>${x}</option>`).join('')}</select>`}</td>
        <td>${lk ? esc(g.res || '') : `<select class="input" data-vr="res|${mode}|${i}">${reses.map(x => `<option${x === g.res ? ' selected' : ''}>${x}</option>`).join('')}</select>`}</td>
        <td>${lk ? esc(String(g.steps || '')) : `<select class="input" data-vr="steps|${mode}|${i}">${stepsList.map(x => `<option value="${x}"${+g.steps === x ? ' selected' : ''}>${x}</option>`).join('')}</select>`}</td>
        <td class="sz">${esc(g.size || '')}</td>
        <td class="sz">${esc(g.lora ? 'LoRA ' + g.lora.replace(/^minimax_h3_/, '').replace(/_comfyui_bf16$/, '') : (g.steps >= 20 ? '不挂 LoRA' : ''))}</td>
        <td class="op">${lk ? '' : `<button class="gdel" data-vr-del="${mode}|${i}" title="删掉这一档">✕</button>`}</td>
      </tr>`;
    }).join('');
    return `<div class="cv-gsub">
      <div class="cv-gsub-hd"><b>${esc(cvVName(mode))}</b><span class="tiny muted">${rows.length} 档</span>
        <span class="gsp"></span>
        <button class="btn sm ghost" data-vr-add="${mode}">＋ 加一档</button>
        <button class="btn sm primary" data-vr-save="${mode}">保存</button></div>
      <div class="cv-tw"><table class="cv-gt">
        <thead><tr><th>档位</th><th>比例</th><th>分辨率</th><th>步数</th><th>实际尺寸</th><th>加速 LoRA</th><th></th></tr></thead>
        <tbody>${body || '<tr><td colspan="7" class="tiny muted">还没有档位</td></tr>'}</tbody>
      </table></div>
      <div class="tiny muted" style="margin-top:6px">4 步挂 4 步加速 LoRA、8 步挂 8 步、20 步不挂；参考/故事板用 ref2v 那套，文生/首尾帧用 fl2v 那套。</div>
    </div>`;
  };

  /* ---- ③ 音频档位 ---- */
  const aParams = (mode, i, g) => {
    const slots = Object.keys(g.params || {});
    if (!slots.length) return '<span class="tiny muted">—</span>';
    return slots.map(slot => {
      const obj = g.params[slot] || {};
      return Object.keys(obj).map(k => {
        const v = obj[k];
        if (vf[k] && Array.isArray(vf[k])) {
          return `<label class="cvf mini"><span>${esc(k)}</span><select class="input" data-ap="params|${mode}|${i}|${slot}|${k}">`
            + vf[k].map(x => `<option${String(x) === String(v) ? ' selected' : ''}>${esc(String(x))}</option>`).join('')
            + '</select></label>';
        }
        return `<label class="cvf mini"><span>${esc(k)}</span><input class="input" data-ap="params|${mode}|${i}|${slot}|${k}" value="${esc(String(v))}"></label>`;
      }).join('');
    }).join('');
  };
  const aTable = mode => {
    const rows = ((A.aGrades[mode] || {}).rows) || [];
    const hasDur = mode === 'music' || mode === 'cover';
    const body = rows.map((g, i) => {
      const lk = !!g.locked;
      return `<tr>
        <td>${lk ? `<b>${esc(g.name)}</b><span class="lock">🔒</span>` : `<input class="input" data-ar="name|${mode}|${i}" value="${esc(g.name)}">`}</td>
        ${hasDur ? `<td>${lk ? esc(String(g.duration || '')) + ' 秒' : `<select class="input" data-ar="duration|${mode}|${i}">${durs.map(x => `<option value="${x}"${+g.duration === x ? ' selected' : ''}>${x} 秒</option>`).join('')}</select>`}</td>` : ''}
        <td class="pp">${lk ? '<span class="tiny muted">—</span>' : aParams(mode, i, g)}</td>
        <td>${lk ? esc(g.note || '') : `<input class="input" data-ar="note|${mode}|${i}" value="${esc(g.note || '')}" placeholder="一句话说明">`}</td>
        <td class="op">${lk ? '' : `<button class="gdel" data-ar-del="${mode}|${i}" title="删掉这一档">✕</button>`}</td>
      </tr>`;
    }).join('');
    return `<div class="cv-gsub">
      <div class="cv-gsub-hd"><b>${esc(cvAName(mode))}</b><span class="tiny muted">${rows.length} 档</span>
        <span class="gsp"></span>
        <button class="btn sm ghost" data-ar-add="${mode}">＋ 加一档</button>
        <button class="btn sm primary" data-ar-save="${mode}">保存</button></div>
      <div class="cv-tw"><table class="cv-gt">
        <thead><tr><th>档位</th>${hasDur ? '<th>时长</th>' : ''}<th>参数</th><th>说明</th><th></th></tr></thead>
        <tbody>${body || `<tr><td colspan="${hasDur ? 5 : 4}" class="tiny muted">还没有档位</td></tr>`}</tbody>
      </table></div>
    </div>`;
  };

  // 需求：画布项目的新建/切换/删除已全部移到创作台画布顶部工具条，管理台不再保留这一块。

  return `<div class="cvs-admin">
    <div class="cv-card">
      <h3>视频生成画布 <span class="tiny muted">1.04</span></h3>
      <div class="tiny muted" style="line-height:1.9">
        画布上只有两种「生成节点」：<b>图片生成</b> 和 <b>视频生成</b>，各绑一条 ComfyUI 工作流。
        节点允许多张素材接进来（图片生成只能接图片），也可以一个线都不连、纯提示词直接出。
        成品统一落在成果目录的 <span class="mono">「${esc((S.canvasAdmin || {}).archiveFolder || '视频生成画布')}」</span> 文件夹。
      </div>
    </div>

    <div class="cv-card">
      <h3>生成子功能<span class="tiny muted">开关节点 · 配置跳转到对应子功能挡位</span></h3>
      ${funcGroups.map(g => {
        const rows = funcList(g);
        if (!rows.length) return '';
        return `<div class="cv-fgroup"><h4>${g}</h4>${rows.map(funcRow).join('')}</div>`;
      }).join('')}
    </div>

    <div class="cv-card">
      <h3>挡位设置<span class="tiny muted">画布不单独设挡位表</span></h3>
      <div class="tiny muted" style="line-height:1.85">
        画布上的每个生成节点都共用创作台对应子功能的挡位：文生图用「文生图」、图生图用「参考生图」、
        首尾帧用「首尾帧生视频」、参考生视频用「参考生视频」，三种设定图与故事板按「版式 × 画法 × 参考」组合。
        要改比例 / 分辨率 / 步数，点上方生成子功能的「配置」直接跳到对应挡位表。画布只决定节点、连线和素材，不维护第二套挡位。
      </div>
    </div>

  </div>`;
}

function bindCanvasAdmin() {
  const A = CV_ADMIN;
  if (!A || A.loading) return;

  // —— 生成子功能：节点开关 + 配置跳转（需求13）——
  $$('[data-cvf-toggle]').forEach(el => el.onchange = async () => {
    const key = el.dataset.cvfToggle, on = el.checked;
    const res = await api('/api/canvas/funcs', { method: 'PUT', body: { [key]: { enabled: on } } });
    if (!res.ok) { el.checked = !on; toast(res.reason || '没改成'); return; }
    A.funcs = res.data.funcs || A.funcs;
    toast(on ? '节点已启用' : '节点已停用');
  });
  $$('[data-cvf-config]').forEach(el => el.onclick = async () => {
    if (el.disabled) return;
    // 1.05：每个生成节点都跳到它自己的子功能挡位表——
    //   图片 → 工作流序号（文生图 0 / 图生图 1）；视频/音频 → 对应模式在已启用模式里的序号；
    //   设定图/故事板 → 组合页，并把子功能切到该节点的模式（文生图/图生图/扩图）
    const fn = (A.funcs || []).find(x => x.id === el.dataset.cvfConfig) || {};
    const target = fn.capId;
    if (!target) return;
    const cap = (S.capabilities || []).find(c => c.id === target);
    if (!cap) { toast('找不到对应的能力'); return; }
    let wfIdx = 0;
    if (target === 'image.generate') {
      // 图片能力的两个节点（文生图/图生图）mode 都是空，靠绑的工作流名区分
      const wfs = cap.workflows || [];
      const i = wfs.findIndex(w => (typeof w === 'string' ? w : (w.name || '')) === fn.wf);
      wfIdx = i >= 0 ? i : (fn.mode === 'i2i' ? 1 : 0);
    } else if (target === 'video.generate') {
      const modes = (((S.video || {}).modes) || []).filter(m => m.enabled !== false);
      const i = modes.findIndex(m => m.id === fn.mode);
      wfIdx = i >= 0 ? i : 0;
    } else if (target === 'audio.generate') {
      const modes = ((S.audio || {}).modes) || [];
      const i = modes.findIndex(m => m.id === fn.mode);
      wfIdx = i >= 0 ? i : 0;
    } else {
      const combos = isBoardCap(cap) ? (S.boardCombos || cap.combos || []) : (cap.combos || []);
      const match = (fn.mode ? combos.find(c => comboModeOf(c) === fn.mode) : null) || combos[0];
      UI.comboSel = match ? match.id : '';
    }
    UI.capId = target; UI.adminPage = 'grades'; UI.editingWfIndex = wfIdx;
    VIDEO_GRADE_CACHE = null;
    UI._navPending = true;
    await saveDraft({ route: 'admin', adminPage: 'grades', capId: target, editingWfIndex: wfIdx });
    UI._navPending = false;
    render();
  });

  // —— 子功能：保存 ——（旧版表单，保留兼容）
  $$('[data-cvf-save]').forEach(el => el.onclick = async () => {
    const key = el.dataset.cvfSave;
    const name = ($q(`[data-cvf-name="${key}"]`) || {}).value || '';
    const wf = ($q(`[data-cvf-wf="${key}"]`) || {}).value || '';
    el.disabled = true;
    const res = await api('/api/canvas/funcs', { method: 'PUT', body: { [key]: { name, wf } } });
    el.disabled = false;
    if (!res.ok) { toast(res.reason || '没保存上'); return; }
    A.funcs = res.data.funcs || A.funcs;
    await pull();
    render();
    toast(wf ? '已绑定，去画布试试' : '已解绑工作流');
  });

  // —— 视频档位：输入绑定 ——
  $$('[data-vr]').forEach(el => {
    const [field, mode, idx] = el.dataset.vr.split('|');
    const g = (((A.vGrades[mode] || {}).rows) || [])[+idx];
    if (!g) return;
    const put = () => { g[field] = field === 'steps' ? +el.value : el.value; };
    el.oninput = put; el.onchange = put;
  });
  $$('[data-vr-add]').forEach(el => el.onclick = () => {
    const mode = el.dataset.vrAdd;
    const rows = (A.vGrades[mode] || {}).rows || ((A.vGrades[mode] = { rows: [] }).rows);
    rows.push({ name: '新档位 ' + (rows.length + 1), ratio: '16:9', res: '768P', steps: 8, note: '' });
    render();
  });
  $$('[data-vr-del]').forEach(el => el.onclick = () => {
    const [mode, idx] = el.dataset.vrDel.split('|');
    const rows = ((A.vGrades[mode] || {}).rows) || [];
    rows.splice(+idx, 1);
    render();
  });
  $$('[data-vr-save]').forEach(el => el.onclick = async () => {
    const mode = el.dataset.vrSave;
    const rows = (((A.vGrades[mode] || {}).rows) || []).map(g => ({
      name: g.name, ratio: g.ratio, res: g.res, steps: +g.steps, note: g.note || '',
    }));
    el.disabled = true;
    const res = await api('/api/video/grades/' + mode, { method: 'PUT', body: { rows } });
    el.disabled = false;
    if (!res.ok) { toast(res.reason || '没保存上'); return; }
    const fresh = await api('/api/video/grades/' + mode);
    if (fresh.ok) A.vGrades[mode] = fresh.data;
    await pull();
    render();
    toast(cvVName(mode) + ' 的档位已保存');
  });

  // —— 音频档位 ——
  $$('[data-ar]').forEach(el => {
    const [field, mode, idx] = el.dataset.ar.split('|');
    const g = (((A.aGrades[mode] || {}).rows) || [])[+idx];
    if (!g) return;
    const put = () => { g[field] = field === 'duration' ? +el.value : el.value; };
    el.oninput = put; el.onchange = put;
  });
  $$('[data-ap]').forEach(el => {
    const [, mode, idx, slot, key] = el.dataset.ap.split('|');
    const g = (((A.aGrades[mode] || {}).rows) || [])[+idx];
    if (!g) return;
    el.onchange = () => {
      g.params = g.params || {};
      g.params[slot] = g.params[slot] || {};
      const v = el.value;
      g.params[slot][key] = (v !== '' && !isNaN(+v) && /^-?\d*\.?\d+$/.test(v)) ? +v : v;
    };
  });
  $$('[data-ar-add]').forEach(el => el.onclick = () => {
    const mode = el.dataset.arAdd;
    const rows = (A.aGrades[mode] || {}).rows || ((A.aGrades[mode] = { rows: [] }).rows);
    const base = rows[rows.length - 1] || { duration: 30, params: {} };
    rows.push({
      name: '新档位 ' + (rows.length + 1),
      duration: base.duration || 30,
      note: '',
      params: JSON.parse(JSON.stringify(base.params || {})),
    });
    render();
  });
  $$('[data-ar-del]').forEach(el => el.onclick = () => {
    const [mode, idx] = el.dataset.arDel.split('|');
    (((A.aGrades[mode] || {}).rows) || []).splice(+idx, 1);
    render();
  });
  $$('[data-ar-save]').forEach(el => el.onclick = async () => {
    const mode = el.dataset.arSave;
    const rows = ((((A.aGrades[mode] || {}).rows) || [])).map(g => ({
      name: g.name, duration: g.duration, note: g.note || '', params: g.params || {},
    }));
    el.disabled = true;
    const res = await api('/api/audio/grades/' + mode, { method: 'PUT', body: { rows } });
    el.disabled = false;
    if (!res.ok) { toast(res.reason || '没保存上'); return; }
    const fresh = await api('/api/audio/grades/' + mode);
    if (fresh.ok) A.aGrades[mode] = fresh.data;
    await pull();
    render();
    toast(cvAName(mode) + ' 的档位已保存');
  });

  // 画布项目的新建/切换/删除已移到创作台画布顶部工具条（canvas.js），管理台不再绑定这些按钮。
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

function comboSourceList() {
  // 档位表按「当前能力」取组合：1.02 角色设定图走 S.sheetCombos，1.03 走 S.extCombos[kind]。
  // 这样 pageComboGrades / bindComboGrades 一套逻辑三种能力共用，不必复制 280 行。
  const cap = currentCap();
  if (isBoardCap(cap)) return S && S.boardCombos || (cap && cap.combos) || [];
  if (isExtSheetCap(cap)) return (((S && S.extCombos) || {})[extKindOf(cap)] || (cap && cap.combos) || []);
  return (S && S.sheetCombos) || [];
}
function comboModeOf(c) { return c && c.mode ? c.mode : t2iFor((c || {}).ref); }
function comboGroupKey(c) {
  // 角色组合没有 mode 字段，只能按 layout + 参考方式推；1.03 组合自带 group/mode，直接用。
  return c && c.group && c.mode ? c.group : `${c.layout}.${comboModeOf(c)}`;
}
function representativeCombo(combos, layout, mode) {
  const pool = combos.filter(c => c.layout === layout && comboModeOf(c) === mode);
  return pool.find(c => c.ink === 'color' && c.nochar === 'yes' && c.ref === 'none')
    || pool.find(c => c.ink === 'color')
    || pool[0] || null;
}
function comboById(cid) { return comboSourceList().find(c => c.id === cid) || null; }
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
  const cap = currentCap();
  const path = isBoardCap(cap) ? '/api/board/combos'
    : isExtSheetCap(cap) ? `/api/ext/${cap.id}/combos` : '/api/sheet/combos';
  const res = await api(path);
  if (!res.ok) return;
  const item = ((res.data && res.data.combos) || []).find(c => c.id === cid);
  UI.comboDetail = { cid, rows: (item && item.grades) || [] };
  render();
}
async function saveComboGrades(cid, rows) {
  const cap = currentCap();
  const path = isBoardCap(cap)
    ? `/api/board/combos/${encodeURIComponent(cid)}/grades`
    : isExtSheetCap(cap)
      ? `/api/ext/${cap.id}/combos/${encodeURIComponent(cid)}/grades`
      : '/api/sheet/combos/' + encodeURIComponent(cid) + '/grades';
  const res = await api(path, { method: 'PUT', body: { grades: rows } });
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
/* 档位表顶部条：只按「子功能 × 版式」6 组切档位。
   画法/参考只改提示词、不改参数节点，所以档位按组共享——创作台里那 18 种选择照旧。 */
function comboPickBar(combos, combo) {
  const mode = comboModeOf(combo);
  const modes = [...new Set(combos.map(comboModeOf))].sort((a, b) => {
    // mode 由服务端给：文生图是 none（创作台沿用 t2i），两种都要排在最前，和创作台顺序一致。
    const order = { none: 0, t2i: 0, lock: 1, style: 2, i2i: 3, expand: 4 };
    return (order[a] ?? 9) - (order[b] ?? 9);
  });
  const layouts = [];
  combos.filter(c => comboModeOf(c) === mode).forEach(c => {
    if (!layouts.some(l => l.id === c.layout)) layouts.push({ id: c.layout, name: c.layoutName, ratio: c.ratio });
  });
  const groups = [...new Set(combos.map(comboGroupKey))].length;
  const board = isBoardCap(currentCap());
  return `<div class="panel" style="margin:0 0 12px;padding:4px 14px">
    <div class="sheetbar"><span class="slb">${board ? '画法' : '子功能'}</span>
      <div class="seg" role="group" aria-label="${board ? '画法' : '子功能'}">
        ${modes.map(m => `<button class="segb ${mode === m ? 'on' : ''}" data-combo-mode="${esc(m)}">${esc(EXT_MODE_NAME[m] || m)}</button>`).join('')}
      </div>
      <span class="tiny muted">共 ${groups} 组档位（每条配方自带一个「默认」档）·${board
        ? '画法只改提示词，版式决定画幅'
        : ' 画法、禁角色、扩图的短长与转角都只改提示词，在创作台选'}</span></div>
    <div class="sheetbar"><span class="slb">版式</span>
      <div class="seg admin-seg" role="group" aria-label="版式">
        ${layouts.map(l => `<button class="segb ${combo.layout === l.id ? 'on' : ''}" data-combo-layout="${esc(l.id)}"
          title="${esc(l.name)} 出图 ${esc(combo.size || '')}">${esc(l.name)}<i class="segx">${esc(l.ratio || '')}</i></button>`).join('')}
      </div>
      <span class="tiny muted">版式决定画幅：${esc(combo.ratio)} ${esc(combo.size)}</span></div>
    <div class="sheetbar"><span class="slb">当前</span>
      <b style="font-size:12.5px">${esc(combo.layoutName)} · ${esc(EXT_MODE_NAME[mode] || mode)}</b>
      <span class="tag">${esc(combo.wf)}</span><span class="tag">${esc(combo.ratio)} ${esc(combo.size)}</span>
      <span style="flex:1"></span>
      <button class="btn sm ghost" data-combo-refresh>↻ 重读档位</button>
      ${isBoardCap(currentCap()) ? '' : '<button class="btn sm ghost" data-sheet-template>✎ 设定图模板</button>'}
      <button class="btn sm" data-combo-studio>去创作台试这个组合</button></div>
  </div>`;
}
/* 参考方式 → 子功能短名，和后端 group_of(cid) 一致 */
function t2iFor(ref) { return ref === 'none' ? 't2i' : 'i2i'; }

function pageComboGrades(cap) {
  const combos = cap.combos || [];
  if (!combos.length) {
    return `<div class="spread" style="margin-bottom:6px"><div><h2>档位表</h2><div class="sub">${esc(cap.em)} ${esc(cap.name)}</div></div>
      <button class="btn sm" data-page="caps">← 返回列表</button></div>
      <div class="panel"><div class="empty" style="padding:36px 24px"><div style="font-size:15px;font-weight:650">组合还没生成</div>
        <div class="tiny muted" style="line-height:1.95;max-width:520px;margin:0 auto">提示词库读不到时会这样，点上方「✎ 设定图模板」看一眼。</div></div></div>`;
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
        <div class="wffile"><span class="wfname mono">${esc(wfName)}</span><span class="wfauto">自动读取</span></div>
        <div class="tiny muted" style="margin:8px 0 14px;line-height:1.7">文件：<span class="mono">${esc(wfLib(wfName).path || '—')}</span><br>
          只读，不对它做任何改动。这个组合固定用这条母版工作流；文生图 / 锁定同角色 / 只借画风各有独立工作流与档位。</div>
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
                ${isDef ? '' : `<span class="gdrag" draggable="true" data-gdrag="${i}" title="拖动改变档位顺序">⠿</span>`}
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
  // ---- 档位组切换：只按「子功能 × 版式」切；画法/禁角色只改提示词，不进档位表 ----
  const go = (patch) => {
    const c = comboById(UI.comboSel) || combo;
    const all = comboSourceList();
    const mode = patch.mode || comboModeOf(c);
    let layout = patch.layout || c.layout;
    let next = representativeCombo(all, layout, mode);
    // 切模式时版式可能不属于新模式（文生图 N → 扩图没有 N），落到该模式的第一个版式
    if (!next) {
      const first = all.find(x => comboModeOf(x) === mode);
      if (!first) return;
      layout = first.layout;
      next = representativeCombo(all, layout, mode);
    }
    UI.comboSel = next.id;
    UI.gOpen = 0; UI.comboDetail = null;
    render();
  };
  $$('[data-combo-mode]').forEach(el => el.onclick = () => go({ mode: el.dataset.comboMode }));
  $$('[data-combo-layout]').forEach(el => el.onclick = () => go({ layout: el.dataset.comboLayout }));
  $$('[data-combo-refresh]').forEach(el => el.onclick = async () => {
    await pull(); UI.comboDetail = null; render(); toast('已重读档位');
  });
  // 设定图模板：角色 / 场景 / 道具各有自己的库，从档位表里以模态框打开
  $$('[data-sheet-template]').forEach(el => el.onclick = () => {
    const cap = currentCap();
    if (isExtSheetCap(cap)) openExtTemplateModal(cap);
    else openSheetTemplateModal();
  });
  $$('[data-combo-studio]').forEach(el => el.onclick = async () => {
    // 把当前组合带到创作台：版式/模式/画法一次填好，档位停在母版
    const c = comboById(UI.comboSel) || combo, cap = currentCap(), mode = comboModeOf(c);
    UI.capId = cap.id;
    if (isBoardCap(cap)) {
      const f = boardForm();
      Object.assign(f, { layout: c.layout, ink: c.ink || 'color', grade: '默认', sel: 0,
                         cells: f.cells.map(() => '') });
      UI.mode = 'i2i'; UI.boardOpen = false; UI.boardPreview = null; boardForm();
    } else if (isExtSheetCap(cap)) {
      const f = extForm();
      Object.assign(f, { kind: extKindOf(cap), layout: c.layout, ink: c.ink || 'color',
        nochar: c.nochar || 'yes', ref: c.ref || 'none', grade: '默认' });
      UI.mode = mode;
      if (mode === 'expand') { f.ink = 'color'; f.nochar = 'yes'; f.ref = 'none'; f.styleCustom = false; }
      else if (mode === 'i2i') { f.styleCustom = true; if (f.kind === 'prop') f.ref = 'lock'; }
      else if (f.kind === 'prop') f.ref = 'none';
      extForm(); UI.extOpen = false; UI.extPreview = null;
    } else {
      // 服务端文生图的 mode 是 none（创作台内部沿用 t2i），两者都算文生图，别带成图生图。
      UI.mode = (mode === 't2i' || mode === 'none') ? 't2i' : 'i2i';
      UI.sheetForm = { ...UI.sheetForm, layout: c.layout, ink: c.ink,
                       ref: c.ref === 'none' ? 'lock' : c.ref, grade: '默认' };
      UI.sheetOpen = false; UI.sheetPreview = null;
    }
    UI.route = 'studio';
    UI._userSwitched = true; UI._draftCap = UI.capId;
    UI._navPending = true;
    await saveDraft({ route: 'studio', capId: UI.capId });
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
  // ---- ⑤ 档位拖拽排序（默认档固定，只排自建档；顺序即创作台按钮顺序）----
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
      if (dragFrom < 0) return;
      const grades = (comboById(cid) || combo).grades || [];
      const fromName = grades[dragFrom] && grades[dragFrom].name;
      const rows = comboCollectAll(cid);           // 自建档，顺序与 grades 中非默认档一致
      const fi = rows.findIndex(r => r.name === fromName);
      const to = +row.dataset.grow;
      const toName = grades[to] && grades[to].name;
      let ti = rows.findIndex(r => r.name === toName);
      if (fi < 0) { dragFrom = -1; return; }
      if (ti < 0) ti = rows.length - 1;            // 落在默认档上：排到末尾
      dragFrom = -1;
      if (ti === fi) return;
      const [moved] = rows.splice(fi, 1);
      rows.splice(ti, 0, moved);
      if (await saveComboGrades(cid, rows)) toast('档位顺序已更新');
    };
  });
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
  // 需求3：视频生成的子功能名对齐创作台短名，并把禁用的模式也列出来（灰显不可点）
  const isVideo = cap.id === 'video.generate';
  const vmodes = (S && S.video && S.video.modes) || [];
  const shortName = n => { const m = vmodes.find(x => n.indexOf(x.id) >= 0 || n.indexOf((x.name||'').replace('生视频','')) >= 0 || n === x.wf); return m ? m.name : n; };
  let segHtml = wfs.map((w, i) => `<button class="segb ${i === UI.editingWfIndex ? 'on' : ''}" data-wfswitch="${i}">${esc(isVideo ? shortName(w.name) : w.name)}</button>`).join('');
  let subNote = `共 ${wfs.length} 个子工作流 · 每个工作流一条默认档`;
  if (isVideo) {
    const disabled = vmodes.filter(m => m.enabled === false);
    segHtml += disabled.map(m => `<button class="segb dis" disabled title="这个模式暂未开放">${esc(m.name)}</button>`).join('');
    subNote = `共 ${wfs.length + disabled.length} 个子功能 · ${wfs.length} 个可用 / ${disabled.length} 个暂未开放`;
  }
  return `<div class="panel" style="margin:0 0 12px;padding:4px 14px">
    <div class="sheetbar"><span class="slb">子功能</span>
      <div class="seg" role="group" aria-label="子工作流">${segHtml}</div>
      <span class="tiny muted">${esc(subNote)}</span></div>
    <div class="sheetbar"><span class="slb">当前</span>
      <b style="font-size:12.5px">${esc(wf.name)}</b>
      <span class="tag">${grades.length} 档</span>
      <span class="tag">${esc((first.effective || {}).size || '尺寸由工作流决定')}</span>
      <span style="flex:1"></span>
      <button class="btn sm ghost" data-wfrefresh>↻ 重读档位</button>
      ${cap.id === 'image.generate' ? '<button class="btn sm ghost" data-sheet-template>✎ 设定图模板</button>' : ''}
      <button class="btn sm" data-wfstudio>去创作台试这个组合</button></div>
  </div>`;
}

/* 需求4：视频生成的档位表单独渲染——②参数节点恒为 0（都归到③不用设的节点），
   右侧新增/已有档位固定为「宽高比 / 采样步数 / 分辨率」三个下拉，点添加即自定义档。
   与「视频生成画布」管理页里的视频档位表同源（都走 /api/video/grades/{mode}）。 */
function pageVideoGrades(cap) {
  if (!cap) return '';
  const modes = (S && S.video && S.video.modes) || [];
  const ratios = (S && S.video && S.video.ratios) || ['16:9', '9:16'];
  const reses = (S && S.video && S.video.reses) || ['480P', '768P'];
  const stepsList = (S && S.video && S.video.steps) || [4, 8, 20];
  const mode = modes[UI.editingWfIndex] || modes[0] || null;
  const wfObj = (cap.workflows || [])[UI.editingWfIndex] || (cap.workflows || [])[0] || {};
  const wfName = typeof wfObj === 'string' ? wfObj : (wfObj.name || '');
  VIDEO_GRADE_CACHE = VIDEO_GRADE_CACHE || {};
  // 首次进入或切换模式时异步拉这一模式的档位；拉完重绘（带 _vgLoading 防重复）
  if (mode && !VIDEO_GRADE_CACHE[mode.id] && !VIDEO_GRADE_CACHE['_loading_' + mode.id]) {
    VIDEO_GRADE_CACHE['_loading_' + mode.id] = 1;
    cvLoadVideoGrades(mode.id).then(() => { delete VIDEO_GRADE_CACHE['_loading_' + mode.id]; if (currentCap() && currentCap().id === 'video.generate') render(); });
  }
  const rows = (VIDEO_GRADE_CACHE[mode && mode.id] || []);
  const loraTxt = g => g.lora ? 'LoRA ' + g.lora.replace(/^minimax_h3_/, '').replace(/_comfyui_bf16$/, '')
    : (g.steps >= 20 ? '不挂 LoRA' : '');
  const openV = Math.min(UI.gOpenV | 0, Math.max(rows.length - 1, 0));
  const glist = rows.map((g, i) => {
    const lk = !!g.locked, isOpen = i === openV;
    return `<div class="grow ${isOpen ? 'open' : ''} ${lk ? 'def' : ''}" data-grow="${i}">
      <div class="grow-hd" data-gtoggle-v="${i}"><span class="gnum">${i + 1}</span>
        ${lk ? `<span class="gname-txt">${esc(g.name)}<i class="lock">🔒 原始</i></span><span class="gdefnote">只读</span>`
          : `<input class="input gname" value="${esc(g.name)}" data-vg="name|${i}" data-stop>`}
        <span class="gcopy">${lk ? '默认参数只读' : `${esc(g.ratio || '')} · ${g.steps} 步 · ${esc(g.res || '')}`}</span>
        <span class="geff ${g.size ? 'ok' : 'bad'}" title="实际尺寸">${esc(g.size || '')}</span>
        <span class="gsp" style="flex:1"></span>
        <span class="gedit ${isOpen ? 'on' : ''}" data-gtoggle-v="${i}">${lk ? '👁 查看' : '✎ 编辑'}</span>
        ${lk ? '<button class="gdel" disabled>✕</button>' : `<button class="gdel" data-vg-del="${i}" title="删掉这一档">✕</button>`}</div>
      ${isOpen ? `<div class="grow-bd">
        <div class="vg-new">${lk
          ? `<span class="tiny muted">宽高比 ${esc(g.ratio || '')} · 采样步数 ${esc(String(g.steps || ''))} · 分辨率 ${esc(g.res || '')} · 实际尺寸 ${esc(g.size || '')} · ${esc(loraTxt(g) || '—')}</span>`
          : `<label class="cvf mini"><span>宽高比</span><select class="input" data-vg="ratio|${i}">${ratios.map(x => `<option${x === g.ratio ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
            <label class="cvf mini"><span>采样步数</span><select class="input" data-vg="steps|${i}">${stepsList.map(x => `<option value="${x}"${+g.steps === x ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
            <label class="cvf mini"><span>分辨率</span><select class="input" data-vg="res|${i}">${reses.map(x => `<option${x === g.res ? ' selected' : ''}>${x}</option>`).join('')}</select></label>`}</div>
        <div class="tiny muted" style="margin-top:10px;line-height:1.7">实际尺寸 <b>${esc(g.size || '—')}</b> · 加速 LoRA <b>${esc(loraTxt(g) || '—')}</b></div>
      </div>` : ''}</div>`;
  }).join('');
  const nodes = wfLib(wfName).list;
  return `<div class="spread" style="margin-bottom:6px"><div><h2>档位表</h2><div class="sub">${esc(cap.em)} ${esc(cap.name)} / ${esc(wfName)}</div></div>
    <div class="row"><button class="btn sm" data-page="caps">← 返回列表</button><button class="btn primary sm" data-vgsave>保存</button></div></div>
    ${wfPickBar(cap, { grades: rows, name: wfName })}
    <div class="glayout" style="display:grid;grid-template-columns:296px minmax(0,1fr);gap:14px;align-items:start">
      <div class="panel" style="margin:0;position:sticky;top:12px"><h3>① 工作流文件</h3>
        <div class="wffile"><span class="wfname mono">${esc(wfLib(wfName).path || wfName)}</span><span class="wfauto">自动读取</span></div>
        <div class="tiny muted" style="margin:8px 0 14px;line-height:1.7">只读，不对它做任何改动。</div>
        <h3 style="flex-wrap:wrap">② 参数节点 <span class="tag">0 个</span></h3>
        <div class="tiny muted" style="padding:8px 2px;line-height:1.7">视频生成的比例/分辨率/步数全部由右侧档位表统一管理，这里没有可设置的参数节点。</div>
        <div class="hr"></div><h3 style="flex-wrap:wrap">③ 不用设的节点 <span class="tag gray">${nodes.length} 个</span></h3>
        <div class="tiny muted" style="margin:-2px 0 8px;line-height:1.7">这三类不用管：填词的、传素材的、出片的。</div>
        <div class="node-list dim">${nodes.map(n => `<div class="node-row no"><span class="nochk"></span>
          <span class="id">${n.id}</span><span class="cls">${esc(n.cls)}</span></div>`).join('')}</div>
      </div>
      <div>
        <div class="panel build"><h3 style="flex-wrap:wrap">④ 新增档位</h3>
          <div class="tiny muted" style="margin:-2px 0 12px;line-height:1.75">三个固定的下拉（宽高比 / 采样步数 / 分辨率），选项由工作流决定、不能移出。起个名点<b>【添加】</b>，创作台就多一个档位按钮。</div>
          <div class="vg-new"><label class="cvf mini"><span>宽高比</span><select class="input" id="vgNewRatio">${ratios.map(x => `<option${x === '16:9' ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
            <label class="cvf mini"><span>采样步数</span><select class="input" id="vgNewSteps">${stepsList.map(x => `<option value="${x}"${x === 4 ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
            <label class="cvf mini"><span>分辨率</span><select class="input" id="vgNewRes">${reses.map(x => `<option${x === '480P' ? ' selected' : ''}>${x}</option>`).join('')}</select></label></div>
          <div class="addbar"><div class="addbar-lb"><span class="an">档位名称</span><span class="tiny muted">创作台上显示这几个字</span></div>
            <input class="input aname" id="vgNewName" placeholder="例如：快速 / 精细">
            <button class="btn primary addbtn" data-vgadd>＋ 添加</button></div>
          <div class="tiny muted" style="margin-top:8px;line-height:1.7"><b>4 步挂 4 步 LoRA、8 步挂 8 步 LoRA、20 步不挂</b>；文生/首尾帧用 fl2v 那套，参考/故事板用 ref2v 那套。</div>
        </div>
        <div class="panel"><div class="row" style="margin-bottom:4px"><h3 style="margin:0">⑤ 已有档位 <span class="tag">${rows.length} 档</span></h3>
          <span style="flex:1"></span><span class="tiny muted">点行展开看参数</span></div>
          <div class="tiny muted" style="margin-bottom:10px;line-height:1.75">「默认」改不了；其余各档点 ✎ 就能改名字、宽高比/步数/分辨率，改完点右上角<b>保存</b>。展开行里显示实际尺寸与加速 LoRA。</div>
          ${rows.length ? `<div class="glist">${glist}</div>` : '<div class="empty" style="padding:20px"><div class="tiny">还没有档位</div></div>'}
        </div>
      </div>
    </div>`;
}
let VIDEO_GRADE_CACHE = null;
function cvLoadVideoGrades(mode) {
  return api('/api/video/grades/' + mode).then(r => {
    if (r.ok) { VIDEO_GRADE_CACHE = VIDEO_GRADE_CACHE || {}; VIDEO_GRADE_CACHE[mode] = r.data.rows || []; }
    return r;
  });
}

/* 需求16：音频四个子功能合并到同一张档位表，用顶部 tab 切换；
   每个 tab 就是一条独立工作流、一套独立档位（数据源 /api/audio/grades/{mode}）。 */
let AUDIO_GRADE_CACHE = null;
function cvLoadAudioGrades(mode) {
  return api('/api/audio/grades/' + mode).then(r => {
    if (r.ok) { AUDIO_GRADE_CACHE = AUDIO_GRADE_CACHE || {}; AUDIO_GRADE_CACHE[mode] = r.data.rows || []; }
    return r;
  });
}
function audioGradeMode() {
  const ms = (S && S.audio && S.audio.modes) || [];
  return (ms[UI.editingWfIndex] || ms[0] || {}).id || '';
}
function pageAudioGrades(cap) {
  if (!cap) return '';
  const am = (S && S.audio) || {};
  const modes = am.modes || [];
  const mode = modes[UI.editingWfIndex] || modes[0] || null;
  const wfObj = (cap.workflows || [])[UI.editingWfIndex] || (cap.workflows || [])[0] || {};
  const wfName = typeof wfObj === 'string' ? wfObj : (wfObj.name || '');
  const durs = am.durations || [10, 15, 24, 30, 45, 60, 90, 120];
  const voiceFields = am.voiceFields || {};
  if (mode && !((AUDIO_GRADE_CACHE || {})[mode.id]) && !(AUDIO_GRADE_CACHE || {})['_loading_' + mode.id]) {
    AUDIO_GRADE_CACHE = AUDIO_GRADE_CACHE || {};
    AUDIO_GRADE_CACHE['_loading_' + mode.id] = 1;
    cvLoadAudioGrades(mode.id).then(() => {
      delete AUDIO_GRADE_CACHE['_loading_' + mode.id];
      if (currentCap() && currentCap().id === 'audio.generate') render();
    });
  }
  const rows = (AUDIO_GRADE_CACHE || {})[mode && mode.id] || [];
  const hasDur = !!(mode && mode.hasDuration);
  const paramsHTML = (g, i) => {
    const slots = Object.keys(g.params || {});
    if (!slots.length) return '<span class="tiny muted">这条工作流没有可调参数</span>';
    return slots.map(slot => Object.keys(g.params[slot] || {}).map(k => {
      const v = g.params[slot][k];
      const label = voiceFields[k] && Array.isArray(voiceFields[k])
        ? `<select class="input" data-agp="${esc(slot)}|${esc(k)}|${i}">${voiceFields[k].map(x => `<option${String(x) === String(v) ? ' selected' : ''}>${esc(String(x))}</option>`).join('')}</select>`
        : `<input class="input" data-agp="${esc(slot)}|${esc(k)}|${i}" value="${esc(String(v))}">`;
      return `<label class="cvf mini"><span>${esc(k)}</span>${label}</label>`;
    }).join('')).join('');
  };
  const openA = Math.min(UI.gOpenA | 0, Math.max(rows.length - 1, 0));
  const glist = rows.map((g, i) => {
    const lk = !!g.locked, isOpen = i === openA;
    const slotCount = Object.keys(g.params || {}).length;
    return `<div class="grow ${isOpen ? 'open' : ''} ${lk ? 'def' : ''}" data-grow="${i}">
      <div class="grow-hd" data-gtoggle-a="${i}"><span class="gnum">${i + 1}</span>
        ${lk ? `<span class="gname-txt">${esc(g.name)}<i class="lock">🔒 原始</i></span><span class="gdefnote">只读</span>`
          : `<input class="input gname" value="${esc(g.name)}" data-ag="name|${i}" data-stop>`}
        <span class="gcopy">${lk ? (hasDur ? `${esc(String(g.duration || ''))} 秒` : '默认档') : (hasDur ? `${esc(String(g.duration || ''))} 秒` : `${slotCount} 项参数`)}</span>
        <span class="geff ${g.note ? 'ok' : 'bad'}" title="一句话说明">${esc(g.note || (hasDur ? '' : `${slotCount} 项`))}</span>
        <span class="gsp" style="flex:1"></span>
        <span class="gedit ${isOpen ? 'on' : ''}" data-gtoggle-a="${i}">${lk ? '👁 查看' : '✎ 编辑'}</span>
        ${lk ? '<button class="gdel" disabled>✕</button>' : `<button class="gdel" data-ag-del="${i}" title="删掉这一档">✕</button>`}</div>
      ${isOpen ? `<div class="grow-bd">
        ${hasDur ? `<div class="note-row"><span class="nm">时长</span>${lk
          ? `<span class="tiny muted">${esc(String(g.duration || ''))} 秒</span>`
          : `<select class="input" data-ag="duration|${i}">${durs.map(x => `<option value="${x}"${+g.duration === +x ? ' selected' : ''}>${x} 秒</option>`).join('')}</select>`}</div>` : ''}
        ${lk ? '<div class="tiny muted" style="padding:2px">默认参数只读。</div>' : `<div class="cv-tw" style="margin:6px 0">${paramsHTML(g, i)}</div>`}
        ${lk ? '' : `<div class="note-row"><span class="nm">一句话说明</span>
          <input class="input" value="${esc(g.note || '')}" data-ag="note|${i}" placeholder="例如：适合短片头"></div>`}
      </div>` : ''}</div>`;
  }).join('');
  const tabs = modes.map((m, idx) => `<button class="segb ${m.id === (mode && mode.id) ? 'on' : ''}" data-wfswitch="${idx}"
      title="${esc(m.desc || '')}">${esc(m.name)}</button>`).join('');
  return `<div class="spread" style="margin-bottom:6px"><div><h2>档位表 · 音频生成</h2>
      <div class="sub">${esc(cap.em)} ${esc(cap.name)} / ${esc(wfName)}</div></div>
    <div class="row"><button class="btn sm" data-page="caps">← 返回列表</button><button class="btn primary sm" data-agsave>保存</button></div></div>
    <div class="panel" style="margin:0 0 12px;padding:4px 14px"><div class="sheetbar"><span class="slb">子功能</span>
      <div class="seg" role="group" aria-label="音频子功能">${tabs}</div>
      <span class="tiny muted">共 ${modes.length} 个子功能 · 每个子功能一条工作流、一套独立档位；创作台用顶部 tab 切换</span></div></div>
    <div class="glayout" style="display:grid;grid-template-columns:296px minmax(0,1fr);gap:14px;align-items:start">
      <div class="panel" style="margin:0;position:sticky;top:12px"><h3>① 工作流文件</h3>
        <div class="wffile"><span class="wfname mono">${esc(wfLib(wfName).path || wfName)}</span><span class="wfauto">自动读取</span></div>
        <div class="tiny muted" style="margin:8px 0 14px;line-height:1.7">只读，不对它做任何改动。</div>
        <h3>② 当前模式</h3>
        <div class="tiny muted" style="line-height:1.85">${esc((mode && mode.desc) || '')}<br>
          ${mode && mode.needsAudio ? '需要上传参考音频。' : '不需要参考音频。'}
          ${hasDur ? '这一档可设时长（秒）。' : '这一功能按时长以外的方式出片。'}</div>
      </div>
      <div>
        <div class="panel build"><h3 style="flex-wrap:wrap">③ 新增档位</h3>
          <div class="tiny muted" style="margin:-2px 0 12px;line-height:1.75">起个名、点<b>【添加】</b>，就复制默认档的参数变成新档，创作台音频页多一个档位按钮。</div>
          <div class="addbar"><div class="addbar-lb"><span class="an">档位名称</span><span class="tiny muted">创作台上显示这几个字</span></div>
            <input class="input aname" id="agNewName" placeholder="例如：深夜电台">
            <button class="btn primary addbtn" data-agadd>＋ 添加</button></div>
          <div class="tiny muted" style="margin-top:8px;line-height:1.7"><b>2~3 档就够了</b>：档位只改参数与时长，同一档出的是同一种风格的结果。</div>
        </div>
        <div class="panel"><div class="row" style="margin-bottom:4px"><h3 style="margin:0">④ 已有档位 <span class="tag">${rows.length} 档</span></h3>
          <span style="flex:1"></span><span class="tiny muted">点行展开看参数</span></div>
          <div class="tiny muted" style="margin-bottom:10px;line-height:1.75">「默认」改不了；其余各档点 ✎ 就能改名称${hasDur ? ' / 时长' : ''} / 参数 / 说明，改完点右上角<b>保存</b>。</div>
          ${rows.length ? `<div class="glist">${glist}</div>` : '<div class="empty" style="padding:20px"><div class="tiny">还没有档位</div></div>'}
        </div>
      </div>
    </div>`;
}

function pageGrades(cap) {
  if (!cap) return '';
  if (cap.id === 'video.generate') return pageVideoGrades(cap);
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

function pageSheet(inModal = false) {
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
      <div><h2>设定图模板</h2><div class="sub">${esc(lib.name || '角色设定图')} · 只需改「风格预设」；通用块与线稿风格句只读 · 数据文件 ${esc(lib.file || '')}</div></div>
      <div class="row">${inModal ? '' : '<button class="btn sm" data-page="grades">← 返回档位表</button>'}</div></div>

    <div class="panel"><h3>档位与画幅 <span class="tag">${wfs.length} 个子工作流</span></h3>
      <div class="tiny muted" style="line-height:1.85">创作台里版式 × 画法 × 参考方式 = <b>18 种选择</b>；档位按
        <b>6 组共享</b>（版式 × 文生图 / 图生图），因为画法与参考只改提示词、不改参数节点。
        每组都有一条<b>默认</b>档（参数就是版式画幅），要「快速 / 精细」去「档位表」按组建。
        <b>画幅由版式决定</b>（四视图横排 1536×864、宫格与单张 864×1536），档位只能改像素 / 倍数 / CFG / sigma。</div>
      ${wfs.length ? `<table class="map-table" style="margin-top:10px"><thead><tr><th>子工作流</th><th>创作台选择</th><th>档位组</th><th>默认画幅</th><th>档位</th></tr></thead><tbody>
        ${wfs.map(w => { const cs = (cap.combos || []).filter(c => c.wf === w.name);
          const sizes = [...new Set(cs.map(c => c.size))].join(' / ');
          // 档位按组共享：同一组里多个组合的 grades 是同一份，只能数一次，不能累加。
          // 组 key 必须和后端 combos.group_of() 一致（版式 × 文生图/图生图）；
          // 服务端下发的 c.group 是「文生图/图生图」大类，不能拿来数 6 组。
          const gkey = c => `${c.layout}.${c.ref === 'none' ? 't2i' : 'i2i'}`;
          const groups = [...new Set(cs.map(gkey))];
          const first = cs.find(c => gkey(c) === groups[0]);
          const extra = (first?.grades || []).filter(g => !g.locked).length;
          return `<tr><td class="tiny">${esc(w.name)}</td><td class="tiny">${cs.length} 种</td>
            <td class="tiny">${groups.length} 组</td>
            <td class="mono tiny">${esc(sizes || '—')}</td>
            <td class="tiny ${extra ? '' : 'muted'}">${extra ? `默认 + ${extra} 档自建（组内共享）` : '只有默认档'}</td></tr>`; }).join('')}
        </tbody></table>
        <div class="row" style="margin-top:10px"><button class="btn sm" data-page="grades">去档位表调参数</button>
          <span class="tiny muted">档位表顶部选子功能 + 版式，右边加档；同组的选择共用这些档。</span></div>`
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

    <div class="panel"><h3>通用块 <span class="tag gray">只读</span></h3>
      <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">${esc(lib.note || '')} 这一段是固定配方，不能在这里改。</div>
      ${Object.entries(blocks).map(([key, block]) => `<div class="sheet-block">
        <div class="bh">${esc(block.name || key)}<span class="mono">${esc(key)}</span></div>
        <textarea data-blk="${esc(key)}" spellcheck="false" rows="4" readonly>${esc(block.text)}</textarea>
        ${block.hint ? `<div class="tiny muted" style="margin-top:4px;line-height:1.7">${esc(block.hint)}</div>` : ''}</div>`).join('')}
    </div>

    <div class="panel"><h3>线稿风格句 <span class="tag gray">只读 · ${lines.length} 条</span></h3>
      <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">线稿版把「风格：」整行换成这一段；「风格」下拉另起一行只借造型气质、不上色。这一段是固定配方，不能改。</div>
      ${lines.map(s => `<div class="sheet-block"><div class="bh">${esc(s.name)}<span class="mono">${esc(s.id)}</span></div>
        <textarea data-ls="${esc(s.id)}" spellcheck="false" rows="3" readonly>${esc(s.text)}</textarea></div>`).join('')}
    </div>

    <div class="panel"><h3>风格预设 <span class="tag">${styles.length} 类</span></h3>
      <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">这是唯一可编辑的部分。画种 + 材质笔触 + 色调 + 光线，一两句；不要写场景环境（会和纯白底打架）。可新增或删除预设。</div>
      <div id="styleRows">${styles.map(s => `<div class="style-row" data-strow="1"><input class="input sname" data-stn value="${esc(s.name)}">
        <input class="input" data-stt value="${esc(s.text)}">
        <button class="btn sm ghost stdel" data-stdel title="删除这个预设">✕</button></div>`).join('')}</div>
      <div class="row" style="margin-top:10px"><button class="btn sm ghost" data-stadd>＋ 新增预设</button>
        <span style="flex:1"></span>
        <button class="btn sm" data-sheetlib-reload>↻ 重读提示词库</button>
        <button class="btn primary sm" data-sheetlib-save>保存</button></div>
    </div>

    <div class="panel"><h3>已实测组合 <span class="tag">${presets.length} 条</span></h3>
      <table class="map-table presets"><thead><tr><th>编号</th><th>组合</th><th>版式</th><th>画法</th><th>参考图</th><th>出图</th><th>实测</th><th></th></tr></thead>
        <tbody>${presets.map(p => `<tr><td class="mono" data-l="编号">${esc(p.id)}</td><td data-l="组合">${esc(p.name)}</td>
          <td class="tiny" data-l="版式">${esc(sheetLayoutName(p.layout))}</td><td class="tiny" data-l="画法">${p.ink === 'line' ? '线稿' : '彩色'}</td>
          <td class="tiny" data-l="参考图">${esc(SHEET_REF_NAME[p.ref] || p.ref)}</td><td class="mono tiny" data-l="出图">${esc(p.size)}</td>
          <td class="tiny muted" data-l="实测">${esc(p.tested || '')}</td>
          <td><button class="btn sm ghost" data-sheet-use="${esc(p.layout)}|${esc(p.ink)}|${esc(p.ref)}">填入</button></td></tr>`).join('')}
        </tbody></table>
    </div>`;
}

/* 设定图模板页的按钮绑定：管理台整页与「档位表 → 设定图模板」模态框共用。
   模态框插入 DOM 后调一次即可，不必重跑整个 bindAdmin()。 */
function bindSheetLib() {
  // 保存：通用块 / 线稿句 / 风格预设（整库 PUT）
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
    // 风格预设用稳定的 uid（data-stuid）配对，增删行后也不会串位
    const styles = $$('[data-strow]').map(row => ({
      name: ((row.querySelector('[data-stn]') || {}).value || '').trim(),
      text: ((row.querySelector('[data-stt]') || {}).value || '').trim(),
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
    const [layout, ink, ref] = String(el.dataset.sheetUse || '').split('|');
    if (layout) UI.sheetForm.layout = layout;
    if (ink) UI.sheetForm.ink = ink;
    if (ref) UI.sheetForm.ref = ref;
    UI.mode = ref && ref !== 'none' ? 'i2i' : 't2i';
    UI.capId = 'sheet.character';
    UI._draftCap = 'sheet.character';
    UI._userSwitched = true;
    UI.route = 'studio';
    UI.sheetPreview = null;
    closeModal();                          // 模态框里点「填入创作台」要先收起弹窗
    await saveDraft({ route: 'studio' });
    await pull(); render();
    toast('已按这个组合切到创作台');
  });
  // 第14条：风格预设新增 / 删除（只操作 DOM，保存时由上面的 handler 统一回写）
  $$('[data-stadd]').forEach(el => el.onclick = () => {
    const box = $('#styleRows'); if (!box) return;
    const row = document.createElement('div');
    row.className = 'style-row'; row.dataset.strow = '1';
    row.innerHTML = `<input class="input sname" data-stn placeholder="风格名" value="">
      <input class="input" data-stt placeholder="画种 + 材质笔触 + 色调 + 光线" value="">
      <button class="btn sm ghost stdel" data-stdel title="删除这个预设">✕</button>`;
    box.appendChild(row);
    row.querySelector('[data-stn]').focus();
  });
  $$('[data-stdel]').forEach(el => el.onclick = () => {
    const row = el.closest('[data-strow]'); if (row) row.remove();
  });
}

/* 第16条：档位表 →「设定图模板」以模态框打开（模板只是角色设定图的子功能）。
   复用 pageSheet() 整页渲染 + bindSheetLib() 绑定，功能与独立页完全一致。 */
function openSheetTemplateModal() {
  openModal(`<div class="m-head"><h3>设定图模板</h3><button class="btn ghost sm" data-close>✕</button></div>
    <div class="m-body" style="padding:14px 18px">${pageSheet(true)}</div>
    <div class="m-foot"><span class="tiny muted" style="flex:1">这是角色设定图的提示词库</span>
      <button class="btn sm" data-close>关闭</button></div>`, true);
  const m = $('.modal:last-child') || $('.modal');
  if (m) m.classList.add('sheetlib-modal');
  bindSheetLib();
}

/* 1.03 设定图模板：场景 / 道具各有独立提示词库，但界面继续沿用角色模板的
   「版式卡片 + 只读通用块 + 只读线稿句 + 可编辑风格预设」结构。 */
function pageExtSheet(cap, inModal = false) {
  const kind = extKindOf(cap), lib = extLib(kind), layouts = lib.layouts || [];
  if (!layouts.length) {
    return `<h2>设定图模板</h2><div class="sub">${esc(cap.name)}</div>
      <div class="panel"><div class="brokenbox">读不到提示词库（prompts/${kind === 'scene' ? 'scene_sheet.json' : 'prop_sheet.json'}）。</div></div>
      <button class="btn primary" data-extlib-reload>↻ 重读提示词库</button>`;
  }
  const blocks = lib.blocks || {}, lines = lib.line_styles || [], styles = lib.styles || [];
  const modes = lib.modes || [], hints = lib.variable_hints || {};
  const comboCount = combosForKind(kind).length;
  return `<div class="spread" style="margin-bottom:6px">
      <div><h2>${esc(cap.name)} · 设定图模板</h2>
        <div class="sub">${esc(lib.name || cap.name)} · 只需改「风格预设」；通用块与线稿风格句只读 · 数据文件 ${esc(lib.file || '')}</div></div>
      <div class="row">${inModal ? '' : '<button class="btn sm" data-page="grades">← 返回档位表</button>'}</div></div>
    <div class="panel"><h3>模式与组合 <span class="tag">${modes.length} 个模式 · ${comboCount} 个组合</span></h3>
      <div class="tiny muted" style="line-height:1.85">${modes.map(m => `<span class="chip on" style="margin-right:6px">${esc(m.name)}</span>`).join('')}
        <br>版式决定画幅：所有版式整图 16:9；档位只改像素 / 倍数 / CFG / sigma，提示词由组合决定。</div></div>
    <div class="panel"><h3>版式模板 <span class="tag">${layouts.length} 个</span></h3>
      <div class="sheet-cards">${layouts.map(l => `<div class="lcard"><b>${esc(l.name)}</b>
        <div class="lmeta"><span class="chip on">${esc(l.ratio || '16:9')}</span><span class="tiny muted">${esc(l.size || '1536 × 864')}</span></div>
        <div class="tiny muted" style="margin:7px 0 9px;line-height:1.75">${esc(l.summary || '')}</div>
        ${(l.subs || []).length ? `<div class="tiny muted" style="margin:-4px 0 9px;line-height:1.75">子选项（只改提示词）：${(l.subs || []).map(s => esc(s.name)).join(' / ')}</div>` : ''}
        <div class="row"><button class="btn sm" data-ext-preview="${esc(l.id)}">预览整条提示词</button>
          <button class="btn sm ghost" data-ext-use="${esc(l.id)}">填入创作台</button></div></div>`).join('')}</div>
      ${hints.scene || hints.sceneGood ? `<div class="tiny muted" style="margin-top:10px;line-height:1.8">写法参考：${esc(hints.scene || '')}<br>
        <span style="color:#8a3a25">好例子：${esc(hints.sceneGood || '')}</span></div>` : ''}</div>
    <div class="panel"><h3>通用块 <span class="tag gray">只读</span></h3>
      <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">${esc(lib.note || '')}</div>
      ${Object.entries(blocks).map(([key, b]) => {
        const text = typeof b === 'string' ? b : (b.text || '');
        const name = typeof b === 'string' ? key : (b.name || key);
        const hint = typeof b === 'string' ? '' : (b.hint || '');
        return `<div class="sheet-block"><div class="bh">${esc(name)}<span class="mono">${esc(key)}</span></div>
          <textarea data-blk="${esc(key)}" spellcheck="false" rows="3" readonly>${esc(text)}</textarea>
          ${hint ? `<div class="tiny muted" style="margin-top:4px">${esc(hint)}</div>` : ''}</div>`;
      }).join('')}</div>
    <div class="panel"><h3>线稿风格句 <span class="tag gray">只读 · ${lines.length} 条</span></h3>
      ${lines.map(s => `<div class="sheet-block"><div class="bh">${esc(s.name)}<span class="mono">${esc(s.id)}</span></div>
        <textarea data-ls="${esc(s.id)}" spellcheck="false" rows="3" readonly>${esc(s.text)}</textarea></div>`).join('')}</div>
    <div class="panel"><h3>风格预设 <span class="tag">${styles.length} 类</span></h3>
      <div class="tiny muted" style="margin:-2px 0 10px;line-height:1.8">这是唯一可编辑的部分。场景/道具库里的同名预设会覆盖角色库，避免人物向词污染画面。</div>
      <div id="styleRows">${styles.map(s => `<div class="style-row" data-strow="1"><input class="input sname" data-stn value="${esc(s.name)}">
        <input class="input" data-stt value="${esc(s.text)}">
        <button class="btn sm ghost stdel" data-stdel title="删除这个预设">✕</button></div>`).join('')}</div>
      <div class="row" style="margin-top:10px"><button class="btn sm ghost" data-stadd>＋ 新增预设</button>
        <span style="flex:1"></span><button class="btn sm" data-extlib-reload>↻ 重读提示词库</button>
        <button class="btn primary sm" data-extlib-save>保存</button></div></div>`;
}
function combosForKind(kind) { return (((S && S.extCombos) || {})[kind]) || []; }
function bindExtSheetLib(cap) {
  const kind = extKindOf(cap);
  $$('[data-extlib-save]').forEach(el => el.onclick = async () => {
    const styles = $$('[data-strow]').map(row => ({
      name: String(((row.querySelector('[data-stn]') || {}).value || '')).trim(),
      text: String(((row.querySelector('[data-stt]') || {}).value || '')).trim(),
    })).filter(s => s.name && s.text);
    const res = await api(`/api/ext/${cap.id}/library`, { method: 'PUT', body: { styles } });
    if (!res.ok) { toastBlocked('提示词库没保存', res.reason || ''); return; }
    await pull(); render(); toast('设定图模板已保存，创作台立刻生效');
  });
  $$('[data-extlib-reload]').forEach(el => el.onclick = async () => { await pull(); render(); toast('已重读提示词库'); });
  $$('[data-ext-preview]').forEach(el => el.onclick = async () => {
    const layoutId = el.dataset.extPreview, layout = (extLib(kind).layouts || []).find(l => l.id === layoutId) || {};
    const mode = String(layout.mode || '').split(',')[0] || 't2i';
    const hints = extLib(kind).variable_hints || {}, styles = extLib(kind).styles || [];
    const sample = kind === 'scene' ? (hints.sceneGood || '雨夜旧码头，左侧仓库右侧灯塔') : '雨伞：展开正面、顶部、伞底、合上';
    const refs = mode === 't2i' ? [] : ['preview'];
    const sub = (layout.subs && layout.subs[0] && layout.subs[0].id) || '';
    const body = { kind, mode, layout: layoutId, sub, text: sample, ink: 'color', nochar: 'yes', ref: 'lock',
      refSlots: mode === 't2i' ? {} : { ref: 'preview' }, refs,
      style: mode === 't2i' ? (styles[0] || {}).name || '' : '', styleCustom: mode !== 't2i', styleText: '', grade: '默认' };
    const res = await api('/api/ext/preview', { body });
    const text = res.ok ? (res.data.prompt || '') : '';
    openModal(`<div class="m-head"><h3>${esc(layout.name || layoutId)} · 整条提示词</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body">${res.ok ? `<div class="tiny muted" style="margin-bottom:8px">${esc(res.data.size || '')} · ${esc(res.data.wf || '')} · 共 ${String(text.length)} 字</div>
        <pre class="promptfull">${esc(text)}</pre>` : `<div class="brokenbox">组装失败：${esc(res.reason || '')}</div>`}</div>
      <div class="m-foot"><span style="flex:1"></span><button class="btn sm" data-close>关闭</button>
        ${res.ok ? '<button class="btn sm primary" id="copyExtLibPrompt">复制</button>' : ''}</div>`);
    const cp = $('#copyExtLibPrompt'); if (cp) cp.onclick = async () => { try { await navigator.clipboard.writeText(text); toast('已复制'); } catch (_) { toast('复制失败'); } };
  });
  $$('[data-ext-use]').forEach(el => el.onclick = async () => {
    const layoutId = el.dataset.extUse, layout = (extLib(kind).layouts || []).find(l => l.id === layoutId) || {};
    UI.capId = cap.id; UI.mode = String(layout.mode || '').split(',')[0] || 't2i';
    const f = extForm(); f.kind = kind; f.layout = layoutId; f.grade = '默认';
    if (UI.mode === 'expand') { f.ink = 'color'; f.nochar = 'yes'; f.ref = 'none'; }
    extForm(); UI.extOpen = false; UI.extPreview = null;
    UI.route = 'studio'; UI._userSwitched = true; UI._draftCap = cap.id;
    closeModal(); UI._navPending = true;
    await saveDraft({ route: 'studio', capId: cap.id });
    UI._navPending = false; render(); toast('已按这个版式切到创作台');
  });
  $$('[data-stadd]').forEach(el => el.onclick = () => {
    const box = $('#styleRows'); if (!box) return;
    const row = document.createElement('div');
    row.className = 'style-row'; row.dataset.strow = '1';
    row.innerHTML = `<input class="input sname" data-stn placeholder="风格名" value="">
      <input class="input" data-stt placeholder="画种 + 材质笔触 + 色调 + 光线" value="">
      <button class="btn sm ghost stdel" data-stdel title="删除这个预设">✕</button>`;
    box.appendChild(row); row.querySelector('[data-stn]').focus();
  });
  $$('[data-stdel]').forEach(el => el.onclick = () => { const row = el.closest('[data-strow]'); if (row) row.remove(); });
}
function openExtTemplateModal(cap) {
  openModal(`<div class="m-head"><h3>${esc(cap.name)} · 设定图模板</h3><button class="btn ghost sm" data-close>✕</button></div>
    <div class="m-body" style="padding:14px 18px">${pageExtSheet(cap, true)}</div>
    <div class="m-foot"><span class="tiny muted" style="flex:1">这是 ${esc(cap.name)} 的独立提示词库</span>
      <button class="btn sm" data-close>关闭</button></div>`, true);
  const m = $('.modal:last-child') || $('.modal');
  if (m) m.classList.add('sheetlib-modal');
  bindExtSheetLib(cap);
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
      <div class="field"><label>ComfyUI 输出目录</label>
        <div class="row"><input class="input mono" value="${esc(s.comfyOutputDir)}" data-s="comfyOutputDir" id="outDirInput">
          <button class="btn sm" id="pickOutput">添加目录</button></div>
        <div class="hint">ComfyUI 的 output 目录。点「添加目录」选文件夹即可，成果目录自动放在它的 ComfyBridgeOutput 子目录里。</div></div>
      <div class="field"><label>成果目录</label>
        <div class="row"><span class="mono muted" style="flex:1;word-break:break-all;font-size:12px">${esc(s.archiveDir)}</span></div>
        <div class="hint">固定是输出目录下的 ComfyBridgeOutput，自动生成，不用填。</div></div>
      <div class="field"><label>成品会这样放</label><div class="path-tree mono">
        <div><span class="d">output/</span></div><div>&nbsp;├─ <span class="d">ComfyBridgeOutput/</span></div>
        <div>&nbsp;│&nbsp;&nbsp; ├─ <span class="d">图片生成/</span></div>
        <div>&nbsp;│&nbsp;&nbsp; ├─ <span class="d">提示词模板库/</span> <span class="muted">（模板卡片的参考图片 / 视频 / 音频）</span></div>
        <div>&nbsp;├─ <span class="muted">（原有的其他文件）</span></div>
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
  bindCanvasAdmin();
  // 1.06 提示词模板库自己的事件（搜索、卡片编辑、标签、小窗开关）
  if (UI.adminPage === 'prompts' && window.PromptLib) window.PromptLib.bindAdmin();
  // 需求2：图片生成的档位表也有「设定图模板」，打开的是通用图片模板（不是角色设定图那套）
  $$('[data-sheet-template]').forEach(el => {
    if (currentCap() && currentCap().id === 'image.generate') el.onclick = openImageTemplateModal;
  });
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
    // 画布页的数据不在 /api/state 里：进这一页时单独拉一次
    if (UI.adminPage === 'canvas') { const res = await api('/api/canvas'); S.canvasAdmin = res.data || {}; }
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
  $$('[data-wfswitch]').forEach(el => el.onclick = () => {
    UI.editingWfIndex = +el.dataset.wfswitch;
    VIDEO_GRADE_CACHE = null;              // 视频档位表：切子功能=切模式，缓存作废重新拉
    AUDIO_GRADE_CACHE = null;              // 音频同理：四个子功能各一套档
    render();
  });
  // —— 需求4：视频生成档位表（三下拉）——
  const vgMode = () => { const ms = (S && S.video && S.video.modes) || []; return (ms[UI.editingWfIndex] || ms[0] || {}).id; };
  $$('[data-vg]').forEach(el => el.onchange = () => {
    const [field, i] = el.dataset.vg.split('|');
    const mode = vgMode(), rows = ((VIDEO_GRADE_CACHE || {})[mode] || []);
    if (rows[+i]) rows[+i][field] = field === 'steps' ? +el.value : el.value;
  });
  $$('[data-vg-del]').forEach(el => el.onclick = () => {
    const mode = vgMode(), rows = ((VIDEO_GRADE_CACHE || {})[mode] || []);
    rows.splice(+el.dataset.vgDel, 1); render();
  });
  const vgAdd = $('[data-vgadd]');
  if (vgAdd) vgAdd.onclick = () => {
    const name = (($('#vgNewName') || {}).value || '').trim();
    if (!name) { toast('先给这一档起个名字'); return; }
    const mode = vgMode();
    VIDEO_GRADE_CACHE = VIDEO_GRADE_CACHE || {};
    const rows = VIDEO_GRADE_CACHE[mode] || (VIDEO_GRADE_CACHE[mode] = []);
    if (rows.some(r => r.name === name)) { toast('档位名重复了'); return; }
    rows.push({ name, ratio: ($('#vgNewRatio') || {}).value || '16:9',
      steps: +($('#vgNewSteps') || {}).value || 4, res: ($('#vgNewRes') || {}).value || '480P', params: {} });
    render(); toast(`已加档位「${name}」，点右上角保存生效`);
  };
  const vgSave = $('[data-vgsave]');
  if (vgSave) vgSave.onclick = async () => {
    const mode = vgMode(), rows = ((VIDEO_GRADE_CACHE || {})[mode] || []);
    if (!rows.length) { toast('至少要留一个档位'); return; }
    const res = await api('/api/video/grades/' + mode, { method: 'PUT', body: { rows } });
    if (!res.ok) { toast(res.reason || '没存成'); return; }
    VIDEO_GRADE_CACHE[mode] = (await api('/api/video/grades/' + mode)).data.rows || [];
    render(); toast('档位已保存，创作台与画布节点立即生效');
  };
  // 视频/音频档位的展开收起，与其它能力的 ⑤ 已有档位同款交互
  $$('[data-gtoggle-v]').forEach(el => el.onclick = e => {
    e.stopPropagation(); const i = +el.dataset.gtoggleV;
    UI.gOpenV = (UI.gOpenV | 0) === i ? -1 : i; render();
  });
  $$('[data-gtoggle-a]').forEach(el => el.onclick = e => {
    e.stopPropagation(); const i = +el.dataset.gtoggleA;
    UI.gOpenA = (UI.gOpenA | 0) === i ? -1 : i; render();
  });
  // —— 需求16：音频四合一档位表（每个 tab 对应一条工作流）——
  $$('[data-ag]').forEach(el => el.onchange = () => {
    const [field, i] = el.dataset.ag.split('|');
    const rows = ((AUDIO_GRADE_CACHE || {})[audioGradeMode()] || []);
    if (rows[+i]) rows[+i][field] = field === 'duration' ? +el.value : el.value;
  });
  $$('[data-agp]').forEach(el => el.onchange = () => {
    const [slot, key, i] = el.dataset.agp.split('|');
    const rows = ((AUDIO_GRADE_CACHE || {})[audioGradeMode()] || []);
    if (rows[+i]) {
      rows[+i].params = rows[+i].params || {};
      rows[+i].params[slot] = rows[+i].params[slot] || {};
      rows[+i].params[slot][key] = el.value;
    }
  });
  $$('[data-ag-del]').forEach(el => el.onclick = () => {
    const rows = ((AUDIO_GRADE_CACHE || {})[audioGradeMode()] || []);
    rows.splice(+el.dataset.agDel, 1); render();
  });
  const agAdd = $('[data-agadd]');
  if (agAdd) agAdd.onclick = () => {
    const name = (($('#agNewName') || {}).value || '').trim();
    if (!name) { toast('先给这一档起个名字'); return; }
    const mode = audioGradeMode();
    AUDIO_GRADE_CACHE = AUDIO_GRADE_CACHE || {};
    const rows = AUDIO_GRADE_CACHE[mode] || (AUDIO_GRADE_CACHE[mode] = []);
    if (rows.some(r => r.name === name)) { toast('档位名重复了'); return; }
    const base = rows[0] || {};
    rows.push({ name, duration: base.duration || 30, note: '', params: JSON.parse(JSON.stringify(base.params || {})) });
    render(); toast(`已加档位「${name}」，点右上角保存生效`);
  };
  const agSave = $('[data-agsave]');
  if (agSave) agSave.onclick = async () => {
    const mode = audioGradeMode(), rows = ((AUDIO_GRADE_CACHE || {})[mode] || []);
    if (!rows.length) { toast('至少要留一个档位'); return; }
    const res = await api('/api/audio/grades/' + mode, { method: 'PUT', body: { rows } });
    if (!res.ok) { toast(res.reason || '没存成'); return; }
    AUDIO_GRADE_CACHE[mode] = (await api('/api/audio/grades/' + mode)).data.rows || [];
    render(); toast('音频档位已保存，创作台四个 tab 立即生效');
  };
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
  if (UI.adminPage === 'grades' && (isSheetCap(currentCap()) || isExtSheetCap(currentCap()) || isBoardCap(currentCap()))) { bindComboGrades(); return; }
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
  const op = $('#pickOutput');
  if (op) op.onclick = async () => {
    op.disabled = true;
    const res = await api('/api/pick-folder', { body: { title: '选择 ComfyUI 的 output 目录', initial: S.service.comfyOutputDir } });
    op.disabled = false;
    if (!res.ok) { if (res.reason !== '已取消选择') toast(res.reason || '没选到目录'); return; }
    const i = $('#outDirInput');
    if (i) { i.value = res.data; i.dispatchEvent(new Event('input')); }
    toast('已填入，记得点「保存并生效」');
  };
  // ---- 设定图模板页：保存 / 预览 / 填入创作台 / 预设增删（整页与模态框共用）----
  bindSheetLib();
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
  const ptSw = $('#ptTopDockSwitch');
  if (ptSw) ptSw.onclick = async () => {
    // 以 ui.promptDock 为准：请求失败时状态不变，连点不会把开关和浮窗点歪
    const next = !(S.ui && S.ui.promptDock);
    if (window.PromptLib) await window.PromptLib.setDockEnabled(next);
  };
  $$('[data-nav]').forEach(el => el.onclick = async () => {
    const t = el.dataset.nav;
    UI.sheet = false; UI.navOpen = false;
    if (t === 'admin') {
      // 记住从哪来：画布 → 管理台 → 返回，必须回到画布（1.03 的「回不去」bug）
      UI.prevRoute = UI.route === 'canvas' ? 'canvas' : 'studio';
      UI.route = 'admin'; UI.adminPage = 'caps';
    }
    else if (t === 'gallery') { UI.prevRoute = UI.route; UI.route = 'gallery'; UI.galleryFilter = '全部'; }
    else if (t === 'canvas') { UI.route = 'canvas'; }
    else UI.route = 'studio';
    // 需求 7：从任何一个菜单进创作台，都直接落到最后一条生成结果
    if (UI.route === 'studio') UI.autoScroll = true;
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
  UI.autoScroll = UI.route === 'studio';   // 需求 7：打开就是最后一条生成结果
  render();
  // 历史任务多了以后 /api/state 有几 MB，一次要一两秒；
  // 上一轮没回来就跳过；没任务在跑就把间隔拉长到 6 秒，别让空闲页面一直压着服务端。
  const tick = async () => {
    if (!document.hidden && !_pollBusy) {
      _pollBusy = true;
      try {
        await fetchState();
        paintProgress();
        if (stateSignature() !== _lastSignature) refreshDynamic();
      } finally {
        _pollBusy = false;
      }
    }
    const busy = (S.jobs || []).some(j => (j.runs || []).some(r =>
      r.state === 'running' || r.state === 'queued' || r.state === 'pending'));
    _pollTimer = setTimeout(tick, busy ? 2000 : 6000);
  };
  _pollTimer = setTimeout(tick, 2000);
});
