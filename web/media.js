/* ============================================================ 1.04 视频生成 / 音频生成
   为什么单独一个文件：这两块和「图片生成」的表单性质不同——
   视频是「模式 + 三个档位参数 + 一组按类型分开的上传位」，音频是「模式 + 时长 / 歌词 / 音色」，
   硬塞进 viewComposer 会把那条已经够长的分支再撑长一倍。这里只依赖 app.js 的
   全局（UI / S / api / esc / render / pull / saveDraft ...），不反向依赖。 */

/* ------------------------------------------------------------ 通用小工具 */
const V_TOKEN = /\[(图|视频|音频)\s*(\d+)\]/g;

function videoMeta() { return (S && S.video) || { modes: [], ratios: ['16:9'], reses: ['768P'], steps: [8] }; }
function audioMeta() { return (S && S.audio) || { modes: [], durations: [30] }; }
function vModeList() { return videoMeta().modes || []; }
function aModeList() { return (audioMeta().modes || []).filter(m => m.enabled !== false); }
function isVideoCap(cap) { return !!cap && cap.id === 'video.generate'; }
function isAudioCap(cap) { return !!cap && String(cap.id || '').startsWith('audio.'); }
function vModeOf(id) { return vModeList().find(m => m.id === id) || vModeList()[0] || null; }
function aModeOf(id) { return aModeList().find(m => m.id === id) || aModeList()[0] || null; }

function videoForm() {
  const f = UI.video = UI.video || {};
  // 时长（秒）：4~15，精度 0.01；生成时以这个值为准，后端换算成帧数注入 H3 length。
  // ratio/res/steps 不再在画面上选，全部由档位表决定；这里保留读取以兼容旧草稿。
  const defs = { mode: 't2v', ratio: '16:9', res: '480P', steps: 8, duration: 5.2, length: 124, grid: 4, cells: [] };
  Object.keys(defs).forEach(k => { if (f[k] === undefined || f[k] === null) f[k] = defs[k]; });
  const mode = vModeOf(f.mode);
  if (mode && !mode.enabled) f.mode = 't2v';
  if (!f.cells || f.cells.length !== 9) {
    const old = Array.isArray(f.cells) ? f.cells : [];
    f.cells = Array.from({ length: 9 }, (_, i) => old[i] || '');
  }
  const sizes = videoMeta().sizes || {};
  if (!sizes[`${f.ratio}|${f.res}`]) { f.ratio = '16:9'; f.res = '768P'; }
  if (!(videoMeta().steps || []).includes(+f.steps)) f.steps = 8;
  return f;
}
function audioForm() {
  const f = UI.audio = UI.audio || {};
  const defs = { mode: 'music', lyrics: '', duration: null };
  Object.keys(defs).forEach(k => { if (f[k] === undefined || f[k] === null || f[k] === '') f[k] = defs[k]; });
  // 需求16：四个子功能合并成「音频生成」一个页面，用 tab 切换模式（各自一个工作流）。
  // 兼容旧草稿里残留的 audio.music / audio.tts 等 cap 记忆。
  if (f.mode === 'music' && UI._audioMigrated !== true) {
    UI._audioMigrated = true;
  }
  if (!aModeOf(f.mode)) f.mode = (aModeList()[0] || {}).id || 'music';
  return f;
}
function vGrades() { const m = vModeOf(videoForm().mode); return (m && m.grades) || []; }
function vGrade() {
  const gs = vGrades(), f = videoForm();
  return gs.find(g => g.name === UI.params.grade) || gs[0] || null;
}
function aGrades() { const m = aModeOf(audioForm().mode); return (m && m.grades) || []; }
function aGrade() {
  const gs = aGrades();
  return gs.find(g => g.name === UI.params.grade) || gs[0] || null;
}
function vSizeLabel() {
  const g = vGrade() || {}, s = (videoMeta().sizes || {})[`${g.ratio}|${g.res}`];
  return s ? `${s.w}×${s.h}` : '';
}
function refsOfKind(kind) { return (UI.refs || []).filter(r => (r.kind || 'image') === kind); }

/* ------------------------------------------------------------ 视频表单 */
function vModeChip(m) {
  return `<button class="vmode ${m.id === videoForm().mode ? 'on' : ''} ${m.enabled === false ? 'dis' : ''}"
    data-vmode="${m.id}" title="${esc(m.desc || '')}" ${m.enabled === false ? 'data-vdis="1"' : ''}>${esc(m.name)}</button>`;
}
function vOptGroup(label, key, values, cur, suffix = '') {
  return `<div class="vopt"><span class="vopt-n">${label}</span>
    ${values.map(v => `<button class="vseg ${String(cur) === String(v) ? 'on' : ''}" data-vopt="${key}" data-vval="${v}">${v}${suffix}</button>`).join('')}</div>`;
}

function vRefSlot(label, kind, ref, extra = '') {
  // 素材槽的 DOM 由共用组件出：画布里视频生成节点的参考位用的是同一个 .vslot
  const idx = ref ? refsOfKind(kind).findIndex(r => r.id === ref.id) + 1 : 1;
  return GenUI.vRefSlot({
    label, kind,
    ref: ref ? { id: ref.id, src: refUrl(ref.id) } : null,
    idx, upload: true,
  });
}

function viewVideoComposer(cap) {
  const f = videoForm(), mode = vModeOf(f.mode), gs = vGrades(), g = vGrade();
  const images = refsOfKind('image'), videos = refsOfKind('video'), audios = refsOfKind('audio');
  const meta = videoMeta();
  const supported = (mode && mode.enabled !== false);
  const empty = !(UI.prompt || '').trim() && !(mode && mode.id === 'board' && f.cells.some(c => String(c).trim()));
  let slots = '';
  if (mode && mode.id === 'fl2v') {
    slots = `<div class="vslots two">${vRefSlot('首帧', 'image', images[0])}${vRefSlot('尾帧', 'image', images[1])}</div>`;
  } else if (mode && mode.id === 'ref2v') {
    slots = `<div class="vslots">
      ${images.map((r, i) => vRefSlot(`图${i + 1}`, 'image', r)).join('')}
      ${videos.map((r, i) => vRefSlot(`视频${i + 1}`, 'video', r)).join('')}
      ${audios.map((r, i) => vRefSlot(`音频${i + 1}`, 'audio', r)).join('')}
      <div class="vadds">
        ${images.length < (meta.maxImages || 6) ? `<button class="vadd" data-vup="image">＋ 图片</button>` : ''}
        ${videos.length < (meta.maxVideos || 1) ? `<button class="vadd" data-vup="video">＋ 视频</button>` : ''}
        ${audios.length < (meta.maxAudios || 2) ? `<button class="vadd" data-vup="audio">＋ 音频</button>` : ''}
      </div></div>`;
  } else if (mode && mode.id === 'board') {
    const grid = +f.grid || 4;
    slots = `<div class="vslots two">${vRefSlot('故事板', 'image', images[0])}
      <div class="vgrid"><span class="vopt-n">宫格</span>
        ${[4, 6, 9].map(n => `<button class="vseg ${grid === n ? 'on' : ''}" data-vgrid="${n}">${n} 格</button>`).join('')}
        <div class="vgrid-hint">按从左到右、从上到下阅读；不写提示词也能按宫格生成</div></div></div>
      <div class="vcells">${Array.from({ length: grid }, (_, i) => `
        <label class="vcell"><span>第 ${i + 1} 格</span>
          <input class="input" data-vcell="${i}" value="${esc(f.cells[i] || '')}" placeholder="这一格拍什么（可留空）"></label>`).join('')}</div>`;
  }
  // 需求6：挡位切换与角色设定图同款，放进 sheetbox 内、提示词上方（不再单独一条 parambar）；
  // 比例/分辨率/步数由挡位表决定，画面上不再出现选择器（需求5）。
  // 整块输入区由共用组件 GenUI.videoInput 产出：画布里的「视频生成节点」调的是同一份。
  const dur = Math.min(15, Math.max(4, +f.duration || 5.2));
  return `<div class="composer"><div class="composer-inner">
    <div class="parambar vparambar">
      <div class="vmodes" role="group" aria-label="视频模式">${vModeList().map(vModeChip).join('')}</div>
      <span class="modedis">${esc((mode && mode.desc) || '')}</span>
    </div>
    ${GenUI.videoInput({
      grades: gs, grade: (g || {}).name, duration: dur,
      sizeLabel: vSizeLabel(),
      boxClass: supported ? '' : 'err',
      slots,
      prompt: UI.prompt,
      placeholder: mode && mode.id === 't2v' ? '描述镜头：谁、在哪、做什么、怎么运镜'
        : mode && mode.id === 'board' ? '（可留空）想看重点就写一两句'
          : '描述这一镜的画面；用 @ 引用左侧素材',
      hint: vHint(mode, images.length, videos.length, audios.length),
      sendDisabled: (empty && !(mode && mode.id === 'board')) || !supported,
      ids: { box: 'inputbox', editor: 'promptInput', durNum: 'vDurNum', durRange: 'vDurRange',
        hint: 'pickHint', emptyHint: 'emptyHint', send: 'sendBtn' },
    })}
  </div></div>`;
}

function vHint(mode, images, videos, audios) {
  const id = mode && mode.id;
  if (id === 't2v') return '纯提示词出片，不需要任何素材';
  if (id === 'fl2v') return images >= 2 ? '首帧 + 尾帧已就位' : images === 1 ? '只有首帧也行，尾帧交给模型' : '先传一张首帧图';
  if (id === 'ref2v') return images + videos + audios ? `已接 ${images} 图 / ${videos} 视频 / ${audios} 音频` : '可以只写提示词，也可以接素材';
  if (id === 'board') return '传一张四/六/九宫格故事板，按格出镜头';
  return '';
}

/* ------------------------------------------------------------ 音频表单 */
function viewAudioComposer(cap) {
  const f = audioForm(), mode = aModeOf(f.mode), gs = aGrades(), g = aGrade();
  const audios = refsOfKind('audio');
  const empty = !(UI.prompt || '').trim();
  const needs = mode && mode.needsAudio;
  return `<div class="composer"><div class="composer-inner">
    <div class="parambar">
      <div class="modepick" role="group" aria-label="音频模式">
        ${aModeList().map(m => `<button class="${m.id === f.mode ? 'on' : ''}" data-amode="${m.id}" title="${esc(m.desc || '')}">
          <span class="${m.id === f.mode ? 'dotb' : 'dot'}"></span>${esc(m.name)}</button>`).join('')}
      </div>
      <span class="modedis">${esc((mode && mode.desc) || '')}</span>
      ${gs.length ? `<div class="grades">${gs.map(x => `<button class="grade ${g && x.name === g.name ? 'on' : ''}"
        data-grade="${esc(x.name)}" title="${esc(x.note || '')}">${esc(x.name)}</button>`).join('')}</div>
        <span class="grade-note">${esc((g || {}).note || '')}</span>` : ''}
    </div>
    <div class="inputbox" id="inputbox">
      <div class="sheetbody">
        ${needs ? `<div class="refs" id="refsRow">${audios.length
          ? vRefSlot('参考音频', 'audio', audios[0])
          : `<button class="vslot" data-vup="audio"><span class="vs-ic">🎵</span><span class="vs-lb">参考音频</span><span class="vs-hint">点击上传</span></button>`}</div>` : ''}
        <div class="editor" id="promptInput" contenteditable="true"
          data-ph="${esc(mode && mode.id === 'tts' ? '要念的台词' : mode && mode.id === 'clone' ? '要克隆音色念的台词' : '描述风格：曲风、乐器、情绪、人声')}">${editorInnerHTML(UI.prompt)}</div>
        ${mode && mode.hasLyrics ? `<textarea class="vlyrics" id="aLyrics" rows="2"
          placeholder="歌词（可留空，留空就按纯音乐生成）">${esc(f.lyrics || '')}</textarea>` : ''}
      </div>
      <div class="foot">
        ${mode && mode.hasDuration ? `<span class="tiny muted">时长</span>
          <select class="input vsmall" id="aDuration">
            ${(audioMeta().durations || [30]).map(d => `<option value="${d}" ${(+f.duration === +d || (!f.duration && +(g || {}).duration === +d)) ? 'selected' : ''}>${d} 秒</option>`).join('')}
          </select>` : ''}
        <span class="tiny muted" id="pickHint">${esc(needs ? (audios.length ? '已接参考音频' : '先上传一段参考音频') : '文字 / 风格描述直接生成')}</span>
        <span class="tiny muted" id="emptyHint" style="${empty ? '' : 'display:none'}">写点描述再发送</span>
        <span style="flex:1"></span><span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>
        <button class="sendbtn" id="sendBtn" title="发送" ${empty ? 'disabled' : ''}>↑</button>
      </div>
    </div>
  </div></div>`;
}

/* ------------------------------------------------------------ 结果卡（视频 / 音频） */
function mediaTileHTML(j, r, i, o) {
  const files = (r.files || []).filter(f => !f.deleted);
  const f0 = files[0] || {};
  const kind = f0.kind || r.media || 'image';
  // 画布节点里的结果块用自己的 data-* 名，其余（创作台）走默认，肉眼看到的完全一样
  const A = (o && o.acts) || {};
  const ATTR = (k, def) => `${A[k] || def}="${r.id}"`;
  const ZOOM = `${A.zoom || 'data-zoom'}="${j.id}|${r.id}"`;
  const badge = (r.grade && r.grade !== ((jobRuns(j)[0] || {}).grade)) ? `<span class="badge">${esc(r.grade)}</span>` : '';
  const dl = `<button title="下载" ${ATTR('dl', 'data-dl')}>⬇</button>`;
  if (kind === 'audio') {
    const secs = f0.duration ? `${Number(f0.duration).toFixed(1)} 秒` : (r.duration ? `${r.duration} 秒` : '');
    return `<div class="tile audio-tile">
      <div class="atile-hd"><span>🎵</span><span class="aname">${esc(f0.filename || '音频')}</span></div>
      <audio controls preload="metadata" src="${imgUrl(r.id, 0)}"></audio>
      <div class="atile-ft"><span class="tiny muted">${esc(secs)}</span><span style="flex:1"></span>${dl}</div></div>`;
  }
  const isVideo = kind === 'video';
  const src = imgUrl(r.id, 0);
  // 需求7：视频的放大/下载按钮固定到右上角小工具条，不覆盖底部原生播放控件；
  // 图片沿用原来的整块 hover 遮罩 .acts。
  return `<div class="tile ${isVideo ? 'vid' : ''}">
    ${isVideo
      ? `<video src="${src}" controls playsinline preload="metadata" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#0b0f16"></video>`
      : `<img src="${src}" alt="成品 ${i + 1}" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain">`}
    ${badge}
    ${isVideo
      ? `<div class="vtop"><button title="放大播放" ${ZOOM}>⤢</button>${dl}</div>`
      : `<div class="acts">
          <button title="看大图" ${ZOOM}>⤢</button>
          ${dl}
          <button title="复制图片（可直接粘贴到输入框当参考图）" ${ATTR('copy', 'data-copyimg')}>⧉</button></div>`}
    <div class="tile-size">${esc(runSizeText(j, r))}${isVideo && f0.duration ? ` · ${Number(f0.duration).toFixed(1)} 秒` : ''}</div></div>`;
}

/* ------------------------------------------------------------ 交互 */
function insertMediaToken(refId) {
  const ed = $('#promptInput'); if (!ed) return;
  const kind = (UI.refs.find(r => r.id === refId) || {}).kind || 'image';
  const list = refsOfKind(kind);
  const idx = list.findIndex(r => r.id === refId) + 1;
  if (idx <= 0) return;
  const token = `[${kind === 'image' ? '图' : kind === 'video' ? '视频' : '音频'} ${idx}]`;
  ed.focus();
  const sel = window.getSelection();
  if (sel && sel.rangeCount && ed.contains(sel.anchorNode)) {
    const range = sel.getRangeAt(0);
    range.deleteContents();
    range.insertNode(document.createTextNode(token));
    range.collapse(false);
    sel.removeAllRanges(); sel.addRange(range);
  } else {
    ed.textContent = (ed.textContent || '') + token;
  }
  onEditorInput();
  toast(`已插入「${token}」`);
}

let _vUploadKind = 'image';
function mediaPicker(kind) {
  _vUploadKind = kind;
  const accept = kind === 'video' ? 'video/*' : kind === 'audio' ? 'audio/*' : 'image/*';
  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = accept; inp.multiple = kind === 'image';
  inp.onchange = () => { const files = [...inp.files]; inp.remove(); if (files.length) uploadMediaFiles(files, kind); };
  document.body.appendChild(inp); inp.click();
}
async function uploadMediaFiles(files, kind) {
  const meta = videoMeta();
  const caps = { image: meta.maxImages || 6, video: meta.maxVideos || 1, audio: meta.maxAudios || 2 };
  let n = refsOfKind(kind).length;
  for (const file of files) {
    if (n >= caps[kind]) { toast(`最多 ${caps[kind]} 个${kind === 'image' ? '图片' : kind === 'video' ? '视频' : '音频'}参考`); break; }
    const form = new FormData();
    form.append('file', file, file.name);
    const res = await api(`/api/uploads?kind=${kind}`, { form });
    if (!res.ok) { toast(res.reason || '上传没成功'); continue; }
    UI.refs.push({ id: res.data.id, kind: res.data.kind || kind, name: res.data.name });
    n += 1;
  }
  saveDraft(); refreshComposer();
}

function bindMediaComposer() {
  const cap = currentCap();
  if (isVideoCap(cap)) {
    const f = videoForm();
    $$('[data-vmode]').forEach(el => el.onclick = () => {
      if (el.dataset.vdis) { toast('这个模式下个版本开放'); return; }
      f.mode = el.dataset.vmode;
      const gs = vGrades();
      if (gs.length && !gs.find(g => g.name === UI.params.grade)) UI.params.grade = gs[0].name;
      saveDraft(); refreshComposer();
    });
    $$('[data-vopt]').forEach(el => el.onclick = () => {
      const key = el.dataset.vopt, val = el.dataset.vval;
      f[key] = key === 'steps' ? +val : val;
      saveDraft(); refreshComposer();
    });
    $$('[data-vgrid]').forEach(el => el.onclick = () => { f.grid = +el.dataset.vgrid; saveDraft(); refreshComposer(); });
    $$('[data-vcell]').forEach(el => el.oninput = () => { f.cells[+el.dataset.vcell] = el.value; scheduleDraft(); });
    // 需求6：时长滑条(min4/max15/step0.1) 与数值框(step0.01) 双向实时同步；
    // 数值框精度更高，滑条位置取数值的小数点后 1 位；生成以数值框为准。
    const vnum = $('#vDurNum'), vrange = $('#vDurRange');
    const clampDur = v => Math.min(15, Math.max(4, v));
    if (vnum) vnum.oninput = () => {
      const v = clampDur(parseFloat(vnum.value));
      if (isNaN(v)) return;
      // 不抓闭包里的 f：轮询里的 loadDraft 可能已替换 UI.video，写旧对象会让时长静默回退
      videoForm().duration = v;
      if (vrange) vrange.value = (Math.round(v * 10) / 10).toFixed(1);
      saveDraft();
    };
    if (vrange) vrange.oninput = () => {
      const v = clampDur(parseFloat(vrange.value));
      if (isNaN(v)) return;
      videoForm().duration = v;
      if (vnum) vnum.value = v.toFixed(2);
      saveDraft();
    };
  }
  if (isAudioCap(cap)) {
    const f = audioForm();
    $$('[data-amode]').forEach(el => el.onclick = () => {
      f.mode = el.dataset.amode;
      const gs = aGrades();
      if (gs.length && !gs.find(g => g.name === UI.params.grade)) UI.params.grade = gs[0].name;
      // 换模式就换能力页（四个音频能力各对应一个模式，工作流不同）
      saveDraft(); refreshComposer();
    });
    const ly = $('#aLyrics'); if (ly) ly.oninput = () => { f.lyrics = ly.value; scheduleDraft(); };
    const du = $('#aDuration'); if (du) du.onchange = () => { f.duration = +du.value; saveDraft(); };
  }
  if (isVideoCap(cap) || isAudioCap(cap)) {
    $$('[data-vup]').forEach(el => el.onclick = () => mediaPicker(el.dataset.vup));
    $$('[data-vat]').forEach(el => el.onclick = e => { e.stopPropagation(); insertMediaToken(el.dataset.vat); });
    $$('[data-vrm]').forEach(el => el.onclick = e => {
      e.stopPropagation();
      const id = el.dataset.vrm;
      UI.refs = UI.refs.filter(r => r.id !== id);
      UI.prompt = serializeEditor();
      saveDraft(); refreshComposer();
    });
    const t = $('#promptInput');
    if (t) {
      t.onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doSendMedia(); } };
      t.oninput = onEditorInput;
      t.onpaste = e => {
        const cd = e.clipboardData || window.clipboardData;
        const imgs = clipboardImages(e);
        if (imgs.length) { e.preventDefault(); uploadMediaFiles(imgs, 'image'); return; }
        e.preventDefault();
        document.execCommand('insertText', false, cd ? cd.getData('text/plain') : '');
      };
    }
    const send = $('#sendBtn'); if (send) send.onclick = doSendMedia;
  }
}

async function doSendMedia() {
  const cap = currentCap();
  if (isVideoCap(cap)) {
    const f = videoForm(), mode = vModeOf(f.mode);
    if (!mode || mode.enabled === false) { toast('这个模式下个版本开放'); return; }
    const prompt = serializeEditor().trim();
    const images = refsOfKind('image');
    if (mode.id !== 'board' && !prompt) { $('#inputbox').classList.add('err'); toast('写点描述再发送'); return; }
    if (mode.id === 'fl2v' && !images.length) { toast('首尾帧生视频至少要传一张首帧图'); return; }
    if (mode.id === 'board' && !images.length) { toast('先传一张故事板'); return; }
    // 文生视频不吃素材。草稿里可能还留着上一个模式传的图/视频/音频，
    // 以前这里直接拦下来，用户看不见素材却怎么都发不出去；现在按模式丢掉就行。
    const useRefs = mode.id === 't2v' ? [] : UI.refs.map(r => r.id);
    const g = vGrade();
    const body = {
      capId: cap.id, prompt, grade: g ? g.name : '默认', wf: mode.wf,
      refs: useRefs,
      // 需求5/6：比例 / 分辨率 / 步数只能来自当前选中的档位，画面上的旧草稿默认值
      // 不能覆盖用户在管理台里配的档；时长取数值框（滑条只负责把它拖到 0.1 精度）。
      video: { mode: mode.id, ratio: g ? g.ratio : '16:9', res: g ? g.res : '480P', steps: +(g ? g.steps : 8),
               duration: Math.min(15, Math.max(4, +f.duration || 5.2)),
               grid: +f.grid, cells: f.cells.slice(0, +f.grid) },
    };
    const sent = await submitAction('/api/tasks', body, '');
    if (sent) {
      UI.prompt = ''; if (mode.id === 'board') f.cells = f.cells.map(() => '');
      UI.refs = []; saveDraft();
      toast(`已提交「${mode.name}」· ${g ? g.ratio : '16:9'} · ${g ? g.res : '480P'} · ${g ? g.steps : 8} 步`);
    }
    return;
  }
  if (isAudioCap(cap)) {
    const f = audioForm(), mode = aModeOf(f.mode);
    const prompt = serializeEditor().trim();
    if (!prompt) { $('#inputbox').classList.add('err'); toast('写点描述再发送'); return; }
    if (mode && mode.needsAudio && !refsOfKind('audio').length) { toast('先上传一段参考音频'); return; }
    if (mode && !mode.needsAudio && refsOfKind('audio').length) { toast('这个功能不需要参考音频'); return; }
    const g = aGrade();
    const body = {
      capId: cap.id, prompt, grade: g ? g.name : '默认', wf: mode.wf,
      refs: UI.refs.map(r => r.id),
      audio: { mode: mode.id, lyrics: f.lyrics || '', duration: f.duration || (g || {}).duration || null },
    };
    const sent = await submitAction('/api/tasks', body, '');
    if (sent) { UI.prompt = ''; f.lyrics = ''; UI.refs = []; saveDraft(); toast(`已提交「${mode.name}」`); }
  }
}

/* 队列弹窗里的档位：视频 / 音频的档位按模式取，不在工作流的 grades 里 */
function mediaGradesFor(job) {
  if (job && job.video) {
    const m = vModeList().find(x => x.id === job.video.mode);
    return (m && m.grades) || [];
  }
  if (job && job.audio) {
    const m = aModeList().find(x => x.id === job.audio.mode);
    return (m && m.grades) || [];
  }
  return null;
}
