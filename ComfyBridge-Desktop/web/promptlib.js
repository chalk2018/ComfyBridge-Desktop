/* ComfyBridge 1.06 · 提示词模板库
 * 独立模块：只依赖 app.js 暴露的全局工具（api / esc / toast / openModal / render / UI / S），
 * 不修改任何现有页面的行为。挂载点：
 *   - 管理台「提示词模板库」页 → window.PromptLib.viewAdmin() / bindAdmin()
 *   - render() 末尾调用 window.PromptLib.syncDock(UI.route) 控制浮动小窗
 */
(function () {
  'use strict';

  const API = '/api/prompt-templates';
  const CATS = [
    { id: 'image', name: '图片', icon: '🖼' },
    { id: 'video', name: '视频', icon: '🎬' },
    { id: 'audio', name: '音频', icon: '🎵' },
  ];
  const MAX_TAGS = 10;

  const PT = {
    templates: [], tags: [], loaded: false, loading: false,
    filters: { q: '', cats: [], tags: [], notTags: [], tagMode: 'all' },  // 多标签默认全部满足；notTags=命中任一即隐藏
    selected: new Set(),                     // 管理台批量删除勾选的模板 id
    drafts: new Map(),           // id / __new_xxx -> 可编辑副本
    expanded: new Set(),         // 小窗里展开的卡片
    dock: { el: null, route: '', enabled: false, collapsed: false, editor: '', dragging: false, listReady: false },
    shown: 20,                   // 管理台「滚动加载」当前显示条数（搜索/筛选时重置为 20）
    _io: null,                   // 管理台加载更多哨兵的 IntersectionObserver
  };
  const LOAD_STEP = 20;          // 每次滚动加载的条数

  /* ------------------------------------------------------------ 小工具 */
  function catName(id) { const c = CATS.find(x => x.id === id); return c ? c.name : '未分类'; }
  function catIcon(id) { const c = CATS.find(x => x.id === id); return c ? c.icon : '·'; }
  function tagById(id) { return PT.tags.find(t => t.id === id); }
  function esc2(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function E(s) { return (typeof esc === 'function' ? esc : esc2)(s); }

  async function A(path, opts) {
    if (typeof api === 'function') return api(path, opts);
    const r = await fetch(path, opts || {});
    return { ok: r.ok, data: await r.json() };
  }
  function T(msg) { if (typeof toast === 'function') toast(msg); }
  function OM(html, noClose) {
    if (typeof openModal === 'function') return openModal(html, noClose);
    const m = document.createElement('div'); m.className = 'modal-mask'; m.innerHTML = `<div class="modal">${html}</div>`;
    document.body.appendChild(m); return m;
  }
  function CM() { if (typeof closeModal === 'function') closeModal(); }
  function Q(sel, root) { return (root || document).querySelector(sel); }
  function QA(sel, root) { return [...(root || document).querySelectorAll(sel)]; }

  /* ------------------------------------------------------------ 参考素材
   * 一条模板可以挂图片 / 视频 / 音频：缩略图排在提示词框上面，点开是 16:9 的查看窗。
   * 素材本体存在成果目录的「提示词模板库」里，md 只记文件名。
   */
  const MEDIA_KINDS = [
    { id: 'image', name: '图片' },
    { id: 'video', name: '视频' },
    { id: 'audio', name: '音频' },
  ];
  const MAX_MEDIA = 20;
  const MEDIA_API = API + '/media';
  const ICON_COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const ICON_DL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

  function kindName(k) { const x = MEDIA_KINDS.find(v => v.id === k); return x ? x.name : '素材'; }
  function mediaUrl(m) { return MEDIA_API + '/' + encodeURIComponent(m.file); }
  function mediaDlUrl(m) {
    return mediaUrl(m) + '?download=1&name=' + encodeURIComponent(m.name || m.file);
  }
  // 序号按「同一种素材」单独排：第 1 个视频、第 2 个视频，和图片的 1 号互不影响
  function mediaNo(d, m) {
    const same = (d.media || []).filter(x => x.kind === m.kind);
    return same.findIndex(x => x.id === m.id) + 1;
  }

  function thumbInner(m) {
    if (m.kind === 'image') return `<img src="${E(mediaUrl(m))}" alt="" loading="lazy">`;
    if (m.kind === 'video') return `<span class="pt-thumb-ico" data-vsrc="${E(mediaUrl(m))}">▶</span>`;
    return `<span class="pt-thumb-ico">♪</span>`;
  }

  function mediaRowHtml(d, compact) {
    const list = d.media || [];
    const sfx = compact ? ' sm' : '';
    if (!list.length) {
      // 没有素材时只留一行淡淡的提示 + 上传口，不占卡片高度也不显得空
      return `<span class="pt-media-tip${sfx}">参考素材：图片 / 视频 / 音频</span>
        <button type="button" class="pt-madd empty${sfx}" data-madd title="上传图片 / 视频 / 音频">＋ 上传</button>`;
    }
    const thumbs = list.map(m => {
      const tip = E(m.name) + (m.duration ? ' · ' + Math.round(m.duration) + ' 秒' : '');
      return `<div class="pt-thumb${compact ? ' sm' : ''}" data-mid="${E(m.id)}" role="button" tabindex="0" title="${tip}">
        <div class="pt-thumb-body">${thumbInner(m)}</div>
        <i class="pt-thumb-kind">${kindName(m.kind)}</i>
        <b class="pt-thumb-num">${mediaNo(d, m)}</b>
        <button type="button" class="pt-thumb-x" data-mdel="${E(m.id)}" title="移除这个素材">×</button>
      </div>`;
    }).join('');
    return thumbs + `<button type="button" class="pt-madd${compact ? ' sm' : ''}" data-madd
      title="上传图片 / 视频 / 音频">＋</button>`;
  }

  // 视频缩略图：抓一帧画到 canvas 上，抓不到就留着 ▶ 图标（不支持解码也不难看）
  function paintVideoThumbs(root) {
    QA('[data-vsrc]', root).forEach(ico => {
      const box = ico.parentElement;
      if (!box || box.dataset.painted) return;
      box.dataset.painted = '1';
      const v = document.createElement('video');
      v.preload = 'auto'; v.muted = true; v.playsInline = true;
      let done = false;
      const stop = () => { try { v.removeAttribute('src'); v.load(); } catch (_) { } };
      const fail = () => { done = true; stop(); };
      v.addEventListener('error', fail);
      v.addEventListener('loadeddata', () => {
        try { v.currentTime = Math.min(0.2, (v.duration || 1) / 2); } catch (_) { draw(); }
      });
      v.addEventListener('seeked', draw);
      function draw() {
        if (done) return;
        done = true;
        try {
          const w = 176, h = Math.max(1, Math.round(w * (v.videoHeight || 9) / (v.videoWidth || 16)));
          const c = document.createElement('canvas'); c.width = w; c.height = h;
          c.getContext('2d').drawImage(v, 0, 0, w, h);
          const img = new Image();
          img.src = c.toDataURL('image/jpeg', 0.72);
          img.alt = ''; img.className = 'pt-thumb-canvas';
          box.appendChild(img);
        } catch (_) { /* 画不出来就保留图标 */ }
        stop();
      }
      setTimeout(() => { if (!done) fail(); }, 4000);
      v.src = ico.dataset.vsrc;
    });
  }

  function bindMediaRow(row, d, redraw) {
    QA('.pt-thumb', row).forEach(el => {
      const open = e => {
        if (e && e.target.closest('[data-mdel]')) return;
        const m = (d.media || []).find(x => x.id === el.dataset.mid);
        if (m) openMediaViewer(m);
      };
      el.onclick = open;
      el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    });
    QA('[data-mdel]', row).forEach(b => b.onclick = e => {
      e.stopPropagation();
      d.media = (d.media || []).filter(x => x.id !== b.dataset.mdel);
      redraw();
    });
    const add = Q('[data-madd]', row);
    if (add) add.onclick = e => {
      e.stopPropagation();
      pickMediaFiles(files => addMedia(files, d, redraw));
    };
    paintVideoThumbs(row);
  }

  let mediaInput = null;
  function pickMediaFiles(cb) {
    if (!mediaInput) {
      mediaInput = document.createElement('input');
      mediaInput.type = 'file';
      mediaInput.id = 'ptMediaInput';
      mediaInput.multiple = true;
      mediaInput.accept = 'image/*,video/*,audio/*';
      mediaInput.style.cssText = 'position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0';
      document.body.appendChild(mediaInput);
      mediaInput.addEventListener('change', () => {
        const files = [...(mediaInput.files || [])];
        mediaInput.value = '';
        const job = mediaInput._cb;
        mediaInput._cb = null;
        if (files.length && job) job(files);
      });
    }
    mediaInput._cb = cb;
    mediaInput.click();
  }

  async function addMedia(files, d, redraw) {
    let ok = 0;
    for (const f of files) {
      if ((d.media || []).length >= MAX_MEDIA) { T(`一个模板最多 ${MAX_MEDIA} 个素材`); break; }
      const fd = new FormData();
      fd.append('file', f);
      const r = await A(MEDIA_API, { form: fd });
      if (!r.ok || !r.data) { T(r.reason || `「${f.name}」没传上去`); continue; }
      d.media = [...(d.media || []), r.data];
      // 记下这次草稿自己传的素材：卡片没保存就丢掉时，要把这些文件一起清掉
      d.uploads = [...(d.uploads || []), r.data.file];
      ok++;
    }
    if (ok) { T(`已添加 ${ok} 个素材，点保存写进卡片`); if (redraw) redraw(); }
    return ok;
  }

  /* ------------------------------------------------------------ 素材查看窗（16:9） */
  let viewer = null;
  function closeViewer() {
    if (!viewer) return;
    QA('video,audio', viewer).forEach(el => { try { el.pause(); } catch (_) { } });
    viewer.remove(); viewer = null;
    document.removeEventListener('keydown', onViewerKey, true);
  }
  function onViewerKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); closeViewer(); }
  }
  function openMediaViewer(m) {
    closeViewer();
    const url = mediaUrl(m);
    let body = '';
    if (m.kind === 'image') {
      body = `<img class="pt-viewer-img" src="${E(url)}" alt="${E(m.name)}">`;
    } else if (m.kind === 'video') {
      body = `<video class="pt-viewer-video" src="${E(url)}" controls autoplay playsinline></video>`;
    } else {
      body = `<div class="pt-viewer-audio"><div class="pt-viewer-audio-ico">♪</div>
        <div class="pt-viewer-audio-name">${E(m.name)}${m.duration ? ' · ' + Math.round(m.duration) + ' 秒' : ''}</div>
        <audio src="${E(url)}" controls autoplay></audio></div>`;
    }
    const copyBtn = m.kind === 'image'
      ? `<button type="button" data-vcopy title="复制图片">${ICON_COPY}</button>` : '';
    const el = document.createElement('div');
    el.className = 'pt-viewer';
    // 文件名放左上角（关闭按钮右边）：视频/音频的原生控件都压在窗口底部，
    // 名字压在那儿会挡住播放按钮
    const nameTag = m.kind === 'audio' ? '' :
      `<div class="pt-viewer-name"><i>${kindName(m.kind)}</i>${E(m.name)}</div>`;
    el.innerHTML = `<div class="pt-viewer-box">
      <button type="button" class="pt-viewer-close" data-vclose title="关闭">✕</button>
      ${nameTag}
      <div class="pt-viewer-tools">${copyBtn}
        <button type="button" data-vdl title="下载">${ICON_DL}</button></div>
      ${body}
    </div>`;
    document.body.appendChild(el);
    viewer = el;
    el.onclick = e => { if (e.target === el) closeViewer(); };
    Q('[data-vclose]', el).onclick = closeViewer;
    Q('[data-vdl]', el).onclick = () => downloadMedia(m);
    const cp = Q('[data-vcopy]', el);
    if (cp) cp.onclick = () => copyMediaImage(m);
    document.addEventListener('keydown', onViewerKey, true);
  }

  function downloadMedia(m) {
    const a = document.createElement('a');
    a.href = mediaDlUrl(m);
    a.download = m.name || m.file;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function copyMediaImage(m) {
    try {
      if (!navigator.clipboard || typeof window.ClipboardItem === 'undefined') {
        throw new Error('这个浏览器不支持写剪贴板');
      }
      const png = typeof imageToPngBlob === 'function'
        ? await imageToPngBlob(mediaUrl(m))
        : await (await fetch(mediaUrl(m), { cache: 'no-store' })).blob();
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
      T('图片已复制到剪贴板');
    } catch (err) {
      T('复制失败：' + ((err && err.message) || err) + '，可以改用下载');
    }
  }

  // 卡片没保存就被移除时，把这次自己传上去、还没被任何模板引用的文件删掉
  async function dropOrphanUploads(d) {
    const files = (d.uploads || []).filter(Boolean);
    d.uploads = [];
    for (const f of files) {
      try { await A(MEDIA_API + '/' + encodeURIComponent(f), { method: 'DELETE' }); } catch (_) { /* 删不掉就留着，不影响主流程 */ }
    }
  }

  function ensureLoaded() {
    if (PT.loaded || PT.loading) return;
    PT.loading = true;
    A(API).then(r => {
      PT.loading = false;
      if (!r.ok) { T(r.reason || '提示词模板库读取失败'); return; }
      PT.templates = (r.data || {}).templates || [];
      PT.tags = (r.data || {}).tags || [];
      PT.mediaDir = (r.data || {}).mediaDir || '';
      PT.loaded = true;
      refreshAll();
    }).catch(() => { PT.loading = false; T('提示词模板库读取失败'); });
  }

  /* ------------------------------------------------------------ 草稿 / 过滤 */
  function draftFor(t) {
    let d = PT.drafts.get(t.id);
    if (!d) {
      d = {
        id: t.id, title: t.title, content: t.content, category: t.category,
        tags: [...(t.tags || [])], media: (t.media || []).map(m => ({ ...m })),
        uploads: [], isNew: false,
      };
      PT.drafts.set(t.id, d);
    }
    return d;
  }
  function newDraft() {
    const id = '__new_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const d = { id, title: '', content: '', category: '', tags: [], media: [], uploads: [], isNew: true };
    PT.drafts.set(id, d);
    return d;
  }
  function dirty(d) {
    if (!d) return false;
    const media = JSON.stringify((d.media || []).map(m => [m.id, m.file]));
    if (d.isNew) return !!(d.title.trim() || d.content.trim() || d.category || d.tags.length
      || (d.media || []).length);
    const t = PT.templates.find(x => x.id === d.id);
    if (!t) return true;
    const old = JSON.stringify((t.media || []).map(m => [m.id, m.file]));
    return d.title !== t.title || d.content !== t.content || d.category !== t.category
      || JSON.stringify(d.tags) !== JSON.stringify(t.tags || []) || media !== old;
  }
  function allRows() {
    const news = [...PT.drafts.values()].filter(d => d.isNew);
    const rest = PT.templates.map(t => draftFor(t));
    return [...news, ...rest];
  }
  function filteredRows() {
    const q = (PT.filters.q || '').trim().toLowerCase();
    return allRows().filter(d => {
      if (PT.filters.cats.length && !PT.filters.cats.includes(d.category)) return false;
      if (PT.filters.tags.length) {
        const has = id => (d.tags || []).includes(id);
        const hit = (PT.filters.tagMode === 'any') ? PT.filters.tags.some(has) : PT.filters.tags.every(has);
        if (!hit) return false;
      }
      if ((PT.filters.notTags || []).some(id => (d.tags || []).includes(id))) return false;
      const tagHit = q && (d.tags || []).some(id => (tagById(id)?.name || '').toLowerCase().includes(q));
      if (q && !(d.title.toLowerCase().includes(q) || d.content.toLowerCase().includes(q) || tagHit)) return false;
      return true;
    });
  }

  async function saveOne(id) {
    const d = PT.drafts.get(id);
    if (!d) return;
    if (!d.category) { T('请先选择分类'); return; }
    if (!d.title.trim()) { T('标题不能为空'); return; }
    if ((d.tags || []).length > MAX_TAGS) { T(`最多 ${MAX_TAGS} 个标签`); return; }
    if ((d.media || []).length > MAX_MEDIA) { T(`最多 ${MAX_MEDIA} 个素材`); return; }
    const payload = {
      title: d.title, content: d.content, category: d.category, tags: d.tags,
      media: (d.media || []).map(m => ({
        id: m.id, kind: m.kind, name: m.name, file: m.file, size: m.size,
        ...(m.duration ? { duration: m.duration } : {}),
      })),
    };
    const r = d.isNew
      ? await A(API, { body: payload })
      : await A(API + '/item/' + encodeURIComponent(id), { method: 'PUT', body: payload });
    if (!r.ok) { T(r.reason || '没保存成'); return; }
    const t = r.data;
    const i = PT.templates.findIndex(x => x.id === t.id);
    if (i >= 0) PT.templates[i] = t; else PT.templates.unshift(t);
    d.uploads = [];              // 已经写进卡片，这些素材不再算「孤儿」
    PT.drafts.delete(id);
    T('已保存');
    refreshAll();
  }

  function confirmDel(title, detail, onOk) {
    const m = OM(`<div class="m-head"><h3>${E(title)}</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><div style="font-size:13px;line-height:1.8">${E(detail)}</div></div>
      <div class="m-foot"><button class="btn sm" data-close>取消</button>
        <button class="btn sm danger" id="ptDelYes">确认删除</button></div>`, true);
    Q('#ptDelYes', m).onclick = () => { CM(); onOk(); };
  }

  // 标签管理里的删除确认：用独立遮罩，不 closeModal，避免把底下的标签管理一起关掉
  function confirmDelKeep(title, detail, onOk) {
    const mask = document.createElement('div');
    mask.className = 'modal-mask'; mask.id = 'mask-confirm';
    mask.innerHTML = `<div class="modal"><div class="m-head"><h3>${E(title)}</h3><button class="btn ghost sm" data-x>✕</button></div>
      <div class="m-body"><div style="font-size:13px;line-height:1.8">${E(detail)}</div></div>
      <div class="m-foot"><button class="btn sm" data-x>取消</button>
        <button class="btn sm danger" data-yes>确认删除</button></div></div>`;
    document.body.appendChild(mask);
    const close = () => mask.remove();
    QA('[data-x]', mask).forEach(b => b.onclick = close);
    Q('[data-yes]', mask).onclick = () => { close(); onOk(); };
    mask.onclick = e => { if (e.target === mask) close(); };
  }

  async function deleteOne(id) {
    const d = PT.drafts.get(id);
    if (d && d.isNew) {
      PT.drafts.delete(id); refreshAll(); T('已移除未保存的卡片');
      dropOrphanUploads(d);
      return;
    }
    confirmDel('确认删除这个模板？', '删除后无法恢复，对应的 MD 文件内容也会同步移除；卡片上的参考素材文件会一起删掉。', async () => {
      const r = await A(API + '/item/' + encodeURIComponent(id), { method: 'DELETE' });
      if (!r.ok) { T(r.reason || '没删成'); return; }
      PT.templates = PT.templates.filter(x => x.id !== id);
      PT.drafts.delete(id); PT.expanded.delete(id);
      if (PT.dock.editor === id) closeDockEditor();
      T('已删除'); refreshAll();
    });
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); T('提示词已拷贝'); return; }
    } catch (_) { /* 落到下面的兜底 */ }
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); T('提示词已拷贝'); } catch (_) { T('拷贝失败，请手动选择文字'); }
    ta.remove();
  }

  /* ------------------------------------------------------------ 标签浮层 */
  let tagPop = null;
  function closeTagPop() {
    if (tagPop) {
      tagPop.remove(); tagPop = null;
      document.removeEventListener('pointerdown', onPopOutside, true);
    }
    // 注意：不在这里关筛选弹层。scroll 事件捕获到 document 时也会走这里，
    // 若在弹层内滚动就把它关掉 —— 筛选弹层的关闭只交给「完成 / 点外部 / 切页」。
  }
  function onPopOutside(e) { if (tagPop && !tagPop.contains(e.target)) closeTagPop(); }
  function openTagPop(anchor, d, after) {
    closeTagPop();
    const r = anchor.getBoundingClientRect();
    const pop = document.createElement('div');
    pop.className = 'pt-tagpop';
    pop.innerHTML = `<div class="pt-tagpop-hd">选择标签 <span class="pt-tagpop-num">${(d.tags || []).length}/${MAX_TAGS}</span></div>
      <div class="pt-tagpop-q"><input class="input" data-tagpopq placeholder="输入标签名检索…">
        <button type="button" class="pt-qclear" data-tagpopclear title="清空检索">×</button></div>
      <div class="pt-tagpop-list"></div>`;
    document.body.appendChild(pop);
    const qInp = Q('[data-tagpopq]', pop), qClear = Q('[data-tagpopclear]', pop);
    const render = () => {
      const kw = (qInp.value || '').trim().toLowerCase();
      const list = PT.tags.filter(t => !kw || t.name.toLowerCase().includes(kw));
      Q('.pt-tagpop-list', pop).innerHTML = list.length ? list.map(t => `<button type="button" class="pt-tagpop-item" data-tid="${E(t.id)}">
        <i style="background:${E(t.color)}"></i><span>${E(t.name)}</span><b>${(d.tags || []).includes(t.id) ? '✓' : ''}</b></button>`).join('')
        : '<div class="pt-empty-sm">没有匹配的标签</div>';
      qClear.style.display = kw ? '' : 'none';
      QA('[data-tid]', pop).forEach(btn => btn.onclick = () => {
        const id = btn.dataset.tid;
        const i = (d.tags || []).indexOf(id);
        if (i >= 0) d.tags.splice(i, 1);
        else {
          if ((d.tags || []).length >= MAX_TAGS) { T(`最多 ${MAX_TAGS} 个标签`); return; }
          d.tags.push(id);
        }
        QA('[data-tid]', pop).forEach(b => Q('b', b).textContent = (d.tags || []).includes(b.dataset.tid) ? '✓' : '');
        Q('.pt-tagpop-num', pop).textContent = d.tags.length + '/' + MAX_TAGS;
        if (after) after();
      });
    };
    render();
    qInp.oninput = render;
    qClear.onclick = () => { qInp.value = ''; render(); qInp.focus(); };
    const w = pop.offsetWidth || 210, h = pop.offsetHeight || 260;
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    pop.style.top = (r.bottom + h + 12 > innerHeight ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
    tagPop = pop;
    document.addEventListener('pointerdown', onPopOutside, true);
  }
  /* ------------------------------------------------------------ 标签 chip */
  function tagRowHtml(d, compact) {
    const chips = (d.tags || []).map(id => {
      const t = tagById(id); if (!t) return '';
      return `<span class="pt-chip ${compact ? 'sm' : ''}" style="--chip:${E(t.color)}">${E(t.name)}<button type="button" data-untag="${E(id)}" title="移除标签">×</button></span>`;
    }).join('');
    return chips + `<button type="button" class="pt-chip-add ${compact ? 'sm' : ''}" data-addtag title="添加标签">＋</button>`;
  }
  function bindTagRow(row, d, onChange) {
    QA('[data-untag]', row).forEach(b => b.onclick = () => {
      d.tags = (d.tags || []).filter(x => x !== b.dataset.untag);
      onChange();
    });
    const add = Q('[data-addtag]', row);
    if (add) add.onclick = e => { e.stopPropagation(); openTagPop(add, d, onChange); };
  }

  /* ------------------------------------------------------------ 标签管理 */
  function openTagManager() {
    ensureLoaded();
    const sel = PT.tagMgrSel || (PT.tagMgrSel = new Set());
    const html = `<div class="m-head"><h3>标签管理</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body pt-tagmgr"><div class="tiny muted" style="margin-bottom:12px">标签保存在 标签.md。颜色互相不重复，最多给一个模板挂 ${MAX_TAGS} 个标签。改完名称或颜色后，点右侧 ✓ 保存。</div>
        <div class="pt-tagmgr-q"><input class="input" id="ptTagMgrQ" placeholder="输入标签名检索…" value="${E(PT.tagMgrQ || '')}">
          <button type="button" class="pt-qclear" id="ptTagMgrQClear" title="清空检索">×</button></div>
        <div class="pt-tagmgr-bulkbar"><label><input type="checkbox" id="ptTagMgrAll"> 全选当前结果</label>
          <span id="ptTagMgrSelInfo" class="tiny muted">已选 0 个</span></div>
        <div id="ptTagList" class="pt-tagmgr-list"></div>
        <div class="pt-tagmgr-add"><input class="input" id="ptNewTagName" placeholder="新标签名称" maxlength="16">
          <input type="color" id="ptNewTagColor" value="#2f6bff" title="标签颜色" class="pt-color">
          <button class="btn primary sm" id="ptNewTagBtn">添加</button></div></div>
      <div class="m-foot"><span class="tiny muted" style="flex:1">初始标签：角色 / 场景 / 道具 / 故事板 / 首尾帧 / 角色替换 / 音乐复刻 / 声音克隆</span>
        <button class="btn sm danger" id="ptTagBulkDel" disabled>批量删除</button>
        <button class="btn sm" data-close>关闭</button></div>`;
    const m = OM(html, true);
    const visibleTags = () => {
      const kw = (PT.tagMgrQ || '').trim().toLowerCase();
      return PT.tags.filter(t => !kw || t.name.toLowerCase().includes(kw));
    };
    const dropLocal = id => {
      PT.tags = PT.tags.filter(x => x.id !== id);
      PT.templates.forEach(x => { x.tags = (x.tags || []).filter(y => y !== id); });
      PT.drafts.forEach(x => { x.tags = (x.tags || []).filter(y => y !== id); });
      PT.filters.tags = PT.filters.tags.filter(x => x !== id);
      PT.filters.notTags = PT.filters.notTags.filter(x => x !== id);
      sel.delete(id);
    };
    const updateBulk = () => {
      const info = Q('#ptTagMgrSelInfo', m), btn = Q('#ptTagBulkDel', m);
      if (info) info.textContent = `已选 ${sel.size} 个`;
      if (btn) { btn.disabled = sel.size === 0; btn.textContent = sel.size ? `批量删除（${sel.size}）` : '批量删除'; }
    };
    const renderList = () => {
      const list = visibleTags();
      Q('#ptTagList', m).innerHTML = list.length ? list.map(t => `<div class="pt-tagmgr-row" data-id="${E(t.id)}">
        <input type="checkbox" class="pt-tagmgr-check" data-sel="${E(t.id)}" ${sel.has(t.id) ? 'checked' : ''} title="选中以便批量删除">
        <i style="background:${E(t.color)}"></i>
        <input class="input pt-tagname" data-tagid="${E(t.id)}" data-oldname="${E(t.name)}" value="${E(t.name)}" maxlength="16">
        <input type="color" class="pt-color" data-tagcolor="${E(t.id)}" data-oldcolor="${E(t.color)}" value="${E(t.color)}" title="改颜色">
        <button class="pt-tagmgr-save" data-tagsave="${E(t.id)}" title="保存修改" disabled>✓</button>
        <button class="pt-ico danger" data-tagdel="${E(t.id)}" title="删除标签">✕</button></div>`).join('')
        : `<div class="pt-empty-sm">${PT.tags.length ? '没有匹配的标签' : '还没有标签'}</div>`;
      QA('.pt-tagmgr-row', m).forEach(row => {
        const id = row.dataset.id;
        const nameInp = Q('.pt-tagname', row), colorInp = Q('.pt-color', row), save = Q('[data-tagsave]', row);
        const sync = () => {
          const changed = nameInp.value.trim() !== nameInp.dataset.oldname
            || colorInp.value.toLowerCase() !== colorInp.dataset.oldcolor.toLowerCase();
          save.disabled = !changed;
          save.classList.toggle('ready', changed);
        };
        nameInp.oninput = sync; colorInp.oninput = sync;
        save.onclick = async () => {
          const name = nameInp.value.trim();
          if (!name) { T('标签名不能为空'); return; }
          const r = await A(API + '/tags/' + encodeURIComponent(id), { method: 'PUT', body: { name, color: colorInp.value } });
          if (!r.ok) { T(r.reason || '没改成'); return; }
          const t = PT.tags.find(x => x.id === id);
          Object.assign(t, r.data);
          nameInp.dataset.oldname = r.data.name; colorInp.dataset.oldcolor = r.data.color;
          Q('i', row).style.background = r.data.color;
          sync(); T('标签已更新'); refreshAll();
        };
        Q('[data-tagdel]', row).onclick = () => {
          const t = PT.tags.find(x => x.id === id);
          confirmDelKeep('是否删除标签「' + t.name + '」？', '删除后无法恢复，这个标签会从所有模板卡片上一起移除。', async () => {
            const r = await A(API + '/tags/' + encodeURIComponent(id), { method: 'DELETE' });
            if (!r.ok) { T(r.reason || '没删成'); return; }
            dropLocal(id); T('标签已删除'); refreshAll(); renderList();
          });
        };
        const chk = Q('[data-sel]', row);
        chk.onchange = () => { chk.checked ? sel.add(id) : sel.delete(id); updateBulk(); };
      });
      const all = Q('#ptTagMgrAll', m), vis = visibleTags();
      if (all) {
        all.checked = vis.length > 0 && vis.every(t => sel.has(t.id));
        all.indeterminate = vis.some(t => sel.has(t.id)) && !all.checked;
      }
      updateBulk();
    };
    renderList();
    Q('#ptTagMgrAll', m).onchange = e => {
      visibleTags().forEach(t => e.target.checked ? sel.add(t.id) : sel.delete(t.id));
      renderList();
    };
    Q('#ptTagBulkDel', m).onclick = () => {
      if (!sel.size) return;
      const ids = [...sel];
      const names = ids.map(id => (PT.tags.find(t => t.id === id) || {}).name).filter(Boolean);
      confirmDelKeep('是否删除这 ' + ids.length + ' 个标签？', names.join('、') + ' —— 删除后无法恢复，会从所有模板卡片上一起移除。', async () => {
        let done = 0;
        for (const id of ids) {
          const r = await A(API + '/tags/' + encodeURIComponent(id), { method: 'DELETE' });
          if (r.ok) { dropLocal(id); done++; }
        }
        T(`已删除 ${done} 个标签`); refreshAll(); renderList();
      });
    };
    const qClear = Q('#ptTagMgrQClear', m);
    const syncClear = () => { qClear.style.display = (PT.tagMgrQ || '').trim() ? '' : 'none'; };
    Q('#ptTagMgrQ', m).oninput = e => { PT.tagMgrQ = e.target.value; syncClear(); renderList(); };
    qClear.onclick = () => {
      PT.tagMgrQ = ''; Q('#ptTagMgrQ', m).value = ''; syncClear(); renderList(); Q('#ptTagMgrQ', m).focus();
    };
    syncClear();
    Q('#ptNewTagBtn', m).onclick = async () => {
      const name = Q('#ptNewTagName', m).value.trim();
      if (!name) { T('先写标签名'); return; }
      const r = await A(API + '/tags', { body: { name, color: Q('#ptNewTagColor', m).value } });
      if (!r.ok) { T(r.reason || '没加成'); return; }
      PT.tags.push(r.data); Q('#ptNewTagName', m).value = '';
      PT.tagMgrQ = ''; Q('#ptTagMgrQ', m).value = ''; syncClear();   // 清掉检索，保证新标签立即可见
      T('标签已添加'); refreshAll(); renderList();
    };
  }

  /* ------------------------------------------------------------ 管理台页面 */
  function viewAdmin() {
    ensureLoaded();
    return `<div class="pt-page">
      <div class="pt-head">
        <div class="pt-head-t"><h2>提示词模板库</h2>
          <div class="sub">图片 / 视频 / 音频的提示词集中管理。带参考素材的模板存「-含参」那份，素材本体放在成果目录下的「提示词模板库」文件夹。</div></div>
        <div class="pt-head-a">
          <button class="btn sm" id="ptTagMgr">标签管理</button>
          <button class="btn primary sm" id="ptAdd">＋ 新增模板</button>
        </div>
      </div>
      <div class="pt-toolbar">
        <div class="pt-searchbox"><span>⌕</span><input class="input" id="ptQ" placeholder="搜索标题或内容…" value="${E(PT.filters.q)}"></div>
        <button type="button" class="pt-filter-btn" id="ptCatFilter" data-filter="cats">
          <span class="pt-filter-label">分类</span><span class="pt-filter-cur" id="ptCatCur"></span><span class="pt-filter-caret">▾</span></button>
        <button type="button" class="pt-filter-btn" id="ptTagFilter" data-filter="tags">
          <span class="pt-filter-label">标签</span><span class="pt-filter-cur" id="ptTagCur"></span><span class="pt-filter-caret">▾</span></button>
        <span class="pt-count" id="ptCount"></span>
      </div>
      <div class="pt-bulkbar">
        <label class="pt-checkall"><input type="checkbox" id="ptCheckAll"><span>全选当前结果</span></label>
        <span class="pt-selinfo" id="ptSelInfo">已选 0 条</span>
        <button type="button" class="btn sm danger" id="ptBulkDel" disabled>删除所选</button>
        <span class="pt-bulk-hint">勾选后批量删除；筛选后「全选」只作用于当前筛选结果</span>
      </div>
      <div class="pt-path">数据文件：<code>data/prompt_templates/图片.md</code> · <code>图片-含参.md</code> · <code>视频.md</code> · <code>视频-含参.md</code> · <code>音频.md</code> · <code>音频-含参.md</code> · <code>标签.md</code>
        <br>参考素材：<code class="pt-media-dir">${E(PT.mediaDir || '（读取中…）')}</code></div>
      <div class="pt-grid" id="ptGrid">${PT.loaded ? '' : '<div class="pt-loading">正在读取模板…</div>'}</div>
    </div>`;
  }

  function cardHtml(d) {
    const t = d.isNew ? null : PT.templates.find(x => x.id === d.id);
    const catOpts = `<option value=""${d.category ? '' : ' selected'} disabled>选择分类</option>`
      + CATS.map(c => `<option value="${c.id}"${d.category === c.id ? ' selected' : ''}>${c.name}</option>`).join('');
    return `<div class="pt-card${d.isNew ? ' is-new' : ''}${PT.selected.has(d.id) ? ' picked' : ''}" data-id="${E(d.id)}">
      <div class="pt-card-top">
        <label class="pt-pick" title="勾选以批量删除"><input type="checkbox" data-pick ${PT.selected.has(d.id) ? 'checked' : ''}></label>
        <select class="input pt-cat" data-f="category">${catOpts}</select>
        <input class="input pt-title" data-f="title" placeholder="模板标题" maxlength="80" value="${E(d.title)}">
        <div class="pt-ops">
          <button class="pt-ico pt-save" data-save title="保存" ${dirty(d) ? '' : 'disabled'}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></button>
          <button class="pt-ico danger" data-del title="删除"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/></svg></button>
        </div>
      </div>
      <div class="pt-tags">${tagRowHtml(d, false)}</div>
      <div class="pt-media" data-mrow>${mediaRowHtml(d, false)}</div>
      <textarea class="input pt-content" data-f="content" rows="3" placeholder="在这里写提示词模板内容…">${E(d.content)}</textarea>
      <div class="pt-card-foot">
        <span class="pt-updated">${d.isNew ? '未保存的新卡片' : '更新于 ' + E((t && t.updated) || '—')}</span>
        <div class="pt-card-acts"><span class="pt-len">${d.content.length} 字</span>
          <button class="btn ghost sm pt-copy" data-copy>拷贝内容</button></div>
      </div>
    </div>`;
  }

  // 给单张管理台卡片绑定交互（分批渲染时逐批调用）
  function bindAdminCard(card) {
    const d = PT.drafts.get(card.dataset.id); if (!d) return;
    const pick = Q('[data-pick]', card);
    if (pick) pick.onchange = () => {
      if (pick.checked) PT.selected.add(d.id); else PT.selected.delete(d.id);
      card.classList.toggle('picked', pick.checked);
      syncBulkBar();
    };
    QA('[data-f]', card).forEach(inp => {
      const apply = () => {
        d[inp.dataset.f] = inp.value;
        Q('.pt-save', card).disabled = !dirty(d);
        if (inp.dataset.f === 'content') Q('.pt-len', card).textContent = d.content.length + ' 字';
      };
      inp.addEventListener('input', apply);
      if (inp.tagName === 'SELECT') inp.addEventListener('change', apply);
    });
    Q('[data-save]', card).onclick = () => saveOne(d.id);
    Q('[data-del]', card).onclick = () => deleteOne(d.id);
    Q('[data-copy]', card).onclick = () => copyText(d.content);
    const bindMedia = () => {
      const row = Q('[data-mrow]', card);
      bindMediaRow(row, d, () => {
        row.innerHTML = mediaRowHtml(d, false);
        bindMedia();
        Q('.pt-save', card).disabled = !dirty(d);
      });
    };
    bindMedia();
    const bindTags = () => {
      const row = Q('.pt-tags', card);
      bindTagRow(row, d, () => {
        row.innerHTML = tagRowHtml(d, false);
        bindTags();
        Q('.pt-save', card).disabled = !dirty(d);
      });
    };
    bindTags();
  }

  // 瀑布流：卡片按 DOM 顺序轮流进两列；某列里卡片被拉高时只撑长自己这一列，
  // 相邻列完全不动，自然形成错落、横排不必对齐。
  function gridColCount() { return window.innerWidth <= 760 ? 1 : 2; }
  function gridCols() { return QA('.pt-col', Q('#ptGrid') || document); }
  function gridColsHtml() {
    return Array.from({ length: gridColCount() }, (_, i) => `<div class="pt-col" data-col="${i}"></div>`).join('');
  }
  function appendCardToGrid(grid, d) {
    const cols = gridCols();
    // 放到当前较矮的一列，两列高度自动保持大致均衡
    let target = cols[0];
    for (const c of cols) {
      if (c.offsetHeight < target.offsetHeight) target = c;
    }
    target.insertAdjacentHTML('beforeend', cardHtml(d));
    return target.lastElementChild;
  }

  // 管理台哨兵：滚到列表底部时，从「全库过滤结果」里再取 20 条追加渲染
  function setupAdminLoader() {
    if (PT._io) { PT._io.disconnect(); PT._io = null; }
    const sentinel = Q('#ptMore');
    if (!sentinel) return;
    if (typeof IntersectionObserver === 'undefined') {
      // 降级：不支持时直接显示全部
      PT.shown = 1e9; refreshAdminGrid(); return;
    }
    PT._io = new IntersectionObserver(entries => {
      if (!entries.some(e => e.isIntersecting)) return;
      const rows = filteredRows();
      if (PT.shown >= rows.length) { sentinel.remove(); PT._io.disconnect(); PT._io = null; return; }
      const grid = Q('#ptGrid'); if (!grid) return;
      const next = rows.slice(PT.shown, PT.shown + LOAD_STEP);
      next.forEach(d => {
        const card = appendCardToGrid(grid, d);
        if (card) bindAdminCard(card);
      });
      PT.shown += next.length;
      Q('#ptCount', document).textContent = `已显示 ${PT.shown} / ${rows.length} 条`;
      // 加载条上的数字跟着一起走，别停在上一批
      sentinel.textContent = `向下滚动加载更多 · 已显示 ${PT.shown} / ${rows.length}`;
      if (PT.shown >= rows.length) { sentinel.remove(); PT._io.disconnect(); PT._io = null; }
    }, { rootMargin: '40px' });
    PT._io.observe(sentinel);
  }

  function refreshAdminGrid() {
    const grid = Q('#ptGrid');
    if (!grid) return;
    const rows = filteredRows();          // 全库过滤：搜索/分类/标签都基于全部模板
    // 数据尚未加载完（rows 为空）时不要 clamp，否则会把 shown 误设为 0
    if (PT.shown < 1) PT.shown = LOAD_STEP;
    if (rows.length && PT.shown > rows.length) PT.shown = rows.length;
    const slice = rows.slice(0, PT.shown);
    // 先清空列，再逐张放进当前较矮的一列（窄屏只有一列）
    grid.innerHTML = gridColsHtml();
    slice.forEach(d => appendCardToGrid(grid, d));
    if (rows.length > slice.length) {
      grid.insertAdjacentHTML('beforeend', `<div id="ptMore" class="pt-more">向下滚动加载更多 · 已显示 ${slice.length} / ${rows.length}</div>`);
    }
    if (!slice.length) {
      const multiAnd = PT.filters.tags.length > 1 && PT.filters.tagMode !== 'any';
      const tip = !PT.loaded ? '稍等一下'
        : multiAnd ? '没有同时满足所有标签的模板，可在标签弹层左下角切换为「任一即可」。'
        : '换个搜索词，或者点右上角「＋ 新增模板」。';
      grid.insertAdjacentHTML('beforeend', `<div class="pt-empty"><div class="pt-empty-ico">✎</div><b>${PT.loaded ? '没有匹配的模板' : '正在读取模板…'}</b>
         <span>${tip}</span></div>`);
    }
    Q('#ptCount', document).textContent = rows.length > slice.length
      ? `已显示 ${slice.length} / ${rows.length} 条` : `共 ${rows.length} 条`;
    QA('.pt-card', grid).forEach(bindAdminCard);
    setupAdminLoader();
    syncBulkBar();
  }

  // 筛选条件变化时：重置为首批 20 条（搜索仍然是全库搜索）
  function resetAdminPaging() { PT.shown = LOAD_STEP; refreshAdminGrid(); }

  // 多选筛选按钮上显示的当前条件（命中几个 / 名称）
  function filterSummary(kind) {
    const ids = PT.filters[kind];
    if (kind === 'cats') return ids.length ? ids.map(id => (CATS.find(c => c.id === id) || {}).name || id).join('、') : '全部分类';
    const exc = PT.filters.notTags || [];
    if (!ids.length && !exc.length) return '全部标签';
    const fmt = arr => arr.slice(0, 2).map(id => (tagById(id) || {}).name || id).join('、')
      + (arr.length > 2 ? ` +${arr.length - 2}` : '');
    const mode = ids.length > 1 ? (PT.filters.tagMode === 'any' ? '（任一）' : '（全部）') : '';
    if (!exc.length) return fmt(ids) + mode;
    if (!ids.length) return '不含 ' + fmt(exc);
    return fmt(ids) + mode + ' · 不含 ' + fmt(exc);
  }
  function updateToolbarMeta() {
    const cc = Q('#ptCatCur'); if (cc) cc.textContent = filterSummary('cats');
    const tc = Q('#ptTagCur'); if (tc) tc.textContent = filterSummary('tags');
    Q('#ptCatFilter')?.classList.toggle('on', PT.filters.cats.length > 0);
    Q('#ptTagFilter')?.classList.toggle('on', PT.filters.tags.length > 0 || PT.filters.notTags.length > 0);
    // 素材目录要等数据回来才知道，页面骨架先占位，拿到后回填
    const md = Q('.pt-media-dir');
    if (md && PT.mediaDir && md.textContent !== PT.mediaDir) md.textContent = PT.mediaDir;
  }

  // 批量删除条：全选/半选状态 + 已选数量 + 删除按钮可用性
  function syncBulkBar() {
    const all = Q('#ptCheckAll'); if (!all) return;
    const rows = filteredRows();
    const ids = rows.map(d => d.id);
    const picked = ids.filter(id => PT.selected.has(id)).length;
    all.checked = ids.length > 0 && picked === ids.length;
    all.indeterminate = picked > 0 && picked < ids.length;
    const info = Q('#ptSelInfo'); if (info) info.textContent = `已选 ${PT.selected.size} 条`;
    const del = Q('#ptBulkDel'); if (del) del.disabled = PT.selected.size === 0;
  }

  async function bulkDelete() {
    const ids = [...PT.selected].filter(id => {
      const d = PT.drafts.get(id); return d && !d.isNew;   // 未保存的新卡片本地移除即可
    });
    const newIds = [...PT.selected].filter(id => (PT.drafts.get(id) || {}).isNew);
    if (!ids.length && !newIds.length) return;
    const m = OM(`<div class="m-head"><h3>确认批量删除？</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><div style="font-size:13px;line-height:1.8">
        将删除选中的 <b>${PT.selected.size}</b> 条模板，对应 MD 文件内容会同步移除，<b>删除后无法恢复</b>。</div></div>
      <div class="m-foot"><button class="btn sm" data-close>取消</button>
        <button class="btn sm danger" id="ptBulkYes">确认删除 ${PT.selected.size} 条</button></div>`, true);
    Q('#ptBulkYes', m).onclick = async () => {
      CM();
      let done = 0;
      if (ids.length) {
        const r = await A(API + '/items/bulk-delete', { body: { ids } });
        if (!r.ok) { T(r.reason || '批量删除失败'); return; }
        done = (r.data || {}).deleted || 0;
        PT.templates = PT.templates.filter(x => !ids.includes(x.id));
        ids.forEach(id => { PT.drafts.delete(id); PT.expanded.delete(id); });
      }
      newIds.forEach(id => {
        const nd = PT.drafts.get(id);
        PT.drafts.delete(id);
        if (nd) dropOrphanUploads(nd);
      });
      PT.selected.clear();
      T(`已删除 ${done + newIds.length} 条`);
      refreshAll();
    };
  }

  function refreshAll() {
    refreshAdminGrid(); updateToolbarMeta();
    // 数据变了才把列表标脏：窗口开着就重画，关着就等下次打开时重建。
    // 单纯开合窗口不标脏，避免每次弹出都重建 20 张长卡片（实测这是卡顿主因）。
    if (PT.dock.el && !PT.dock.el.hidden) { renderDockTools(); renderDockList(true); }
    else PT.dock.listReady = false;
  }

  function bindAdmin() {
    // render() 每次重建骨架，网格是空的：这里先把已加载的数据画回去
    refreshAdminGrid(); updateToolbarMeta();
    const add = Q('#ptAdd');
    if (add) add.onclick = () => {
      PT.filters = { q: '', cats: [], tags: [], notTags: [], tagMode: 'all' };
      const q = Q('#ptQ'); if (q) q.value = '';
      newDraft(); refreshAll();
      const first = Q('.pt-card.is-new .pt-title'); if (first) first.focus();
    };
    const q = Q('#ptQ');
    if (q) q.oninput = () => { PT.filters.q = q.value; resetAdminPaging(); };
    const tm = Q('#ptTagMgr'); if (tm) tm.onclick = openTagManager;
    // 多选筛选弹层
    QA('.pt-toolbar [data-filter]').forEach(btn => {
      btn.onclick = e => { e.stopPropagation(); openAdminFilter(btn, btn.dataset.filter); };
    });
    const all = Q('#ptCheckAll');
    if (all) all.onchange = () => {
      const ids = filteredRows().map(d => d.id);
      if (all.checked) ids.forEach(id => PT.selected.add(id));
      else ids.forEach(id => PT.selected.delete(id));
      refreshAdminGrid();
    };
    const bd = Q('#ptBulkDel'); if (bd) bd.onclick = bulkDelete;
  }

  // 管理台分类/标签筛选弹层：命中任一项即显示，可连续多选
  let adminFilterPop = null;
  function closeAdminFilter() {
    if (adminFilterPop) {
      adminFilterPop.remove(); adminFilterPop = null;
      document.removeEventListener('pointerdown', onAdminFilterOutside, true);
    }
  }
  function onAdminFilterOutside(e) {
    if (adminFilterPop && !adminFilterPop.contains(e.target) && !e.target.closest('[data-filter]')) closeAdminFilter();
  }
  function openAdminFilter(anchor, kind) {
    closeAdminFilter();
    const pop = document.createElement('div');
    pop.className = 'pt-tagfilter-pop';
    pop.innerHTML = `<div class="pt-tagfilter-hd">${kind === 'cats' ? '按分类筛选（可多选）' : '按标签筛选（左键包含 · ⊘ 排除）'}
        <button type="button" data-afclose>✕</button></div>
      <div class="pt-tagfilter-search"><input class="input" data-afq placeholder="${kind === 'cats' ? '查找分类…' : '查找标签…'}"></div>
      <div class="pt-tagfilter-list"></div>
      <div class="pt-tagfilter-ft">${kind === 'tags' ? '<button type="button" class="pt-tagfilter-mode" data-afmode></button>' : ''}
        <button type="button" class="btn sm" data-afclear>清空</button>
        <button type="button" class="btn primary sm" data-afdone>完成</button></div>`;
    document.body.appendChild(pop);
    adminFilterPop = pop;
    const renderItems = () => {
      const list = Q('.pt-tagfilter-list', pop);
      const kw = (Q('[data-afq]', pop)?.value || '').trim().toLowerCase();
      const items = (kind === 'cats'
        ? CATS.map(c => ({ id: c.id, name: c.name, color: '#2f6bff' }))
        : PT.tags.map(t => ({ id: t.id, name: t.name, color: t.color })))
        .filter(x => !kw || x.name.toLowerCase().includes(kw));
      const cur = PT.filters[kind], exc = PT.filters.notTags || [];
      const top = list.scrollTop;
      list.innerHTML = items.map(x => {
        const on = cur.includes(x.id), no = kind === 'tags' && exc.includes(x.id);
        return `<div class="pt-tagfilter-item${on ? ' on' : ''}${no ? ' no' : ''}" style="--chip:${E(x.color)}">
          <button type="button" class="pt-tagfilter-main" data-afid="${E(x.id)}"><i></i><span>${E(x.name)}</span><b>${on ? '✓' : no ? '⊘' : ''}</b></button>
          ${kind === 'tags' ? `<button type="button" class="pt-tagfilter-ex" data-afno="${E(x.id)}" title="排除这个标签">⊘</button>` : ''}</div>`;
      }).join('') || '<div class="pt-empty-sm">没有匹配的项目</div>';
      list.scrollTop = top;
      QA('[data-afid]', pop).forEach(b => b.onclick = () => {
        const id = b.dataset.afid, i = cur.indexOf(id);
        if (i >= 0) cur.splice(i, 1); else cur.push(id);
        if (kind === 'tags') { const j = exc.indexOf(id); if (j >= 0) exc.splice(j, 1); }
        renderItems(); updateToolbarMeta();
        if (PT.dock.el && !PT.dock.el.hidden) { renderDockTools(); renderDockList(true); }
      });
      QA('[data-afno]', pop).forEach(b => b.onclick = () => {
        const id = b.dataset.afno, j = exc.indexOf(id);
        if (j >= 0) exc.splice(j, 1); else exc.push(id);
        const i = cur.indexOf(id); if (i >= 0) cur.splice(i, 1);
        renderItems(); updateToolbarMeta();
        if (PT.dock.el && !PT.dock.el.hidden) { renderDockTools(); renderDockList(true); }
      });
      const n = cur.length + (kind === 'tags' ? exc.length : 0);
      Q('[data-afdone]', pop).textContent = n ? `完成（${n}）` : '完成';
      const mb = Q('[data-afmode]', pop);
      if (mb) mb.textContent = PT.filters.tagMode === 'any' ? '任一即可' : '全部满足';
    };
    renderItems();
    Q('[data-afq]', pop).oninput = renderItems;
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth || 216, h = pop.offsetHeight || 260;
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    pop.style.top = (r.bottom + h + 8 > innerHeight ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
    Q('[data-afclose]', pop).onclick = closeAdminFilter;
    Q('[data-afdone]', pop).onclick = () => { closeAdminFilter(); resetAdminPaging(); };
    const modeBtn = Q('[data-afmode]', pop);
    if (modeBtn) modeBtn.onclick = () => {
      PT.filters.tagMode = PT.filters.tagMode === 'any' ? 'all' : 'any';
      renderItems(); updateToolbarMeta(); resetAdminPaging();
      if (PT.dock.el && !PT.dock.el.hidden) { renderDockTools(); renderDockList(true); }
    };
    Q('[data-afclear]', pop).onclick = () => {
      PT.filters[kind] = [];
      if (kind === 'tags') PT.filters.notTags = [];
      renderItems(); updateToolbarMeta(); resetAdminPaging();
      if (PT.dock.el && !PT.dock.el.hidden) { renderDockTools(); renderDockList(true); }
    };
    setTimeout(() => document.addEventListener('pointerdown', onAdminFilterOutside, true), 0);
  }

  /* ------------------------------------------------------------ 浮动小窗 */
  // 顶栏「提示词模板」开关与速取窗是同一个开关：任何一侧改动都要同步另一侧。
  function syncTopSwitch(on) {
    const v = !!on;
    QA('#ptTopDockSwitch').forEach(b => {
      b.classList.toggle('on', v);
      b.setAttribute('aria-checked', v ? 'true' : 'false');
    });
  }
  async function setDockEnabled(on) {
    const v = !!on;
    // 先本地立刻切换（开关与窗口不等网络，点下去就有反馈），再异步落盘。
    S.ui.promptDock = v;
    syncTopSwitch(v);
    syncDock(typeof UI !== 'undefined' ? UI.route : 'studio');
    T(v ? '提示词速取已开启' : '提示词速取已隐藏');
    // 落盘留在后台：失败时回滚到切换前的状态，不让界面停在错的位置。
    try {
      const r = await A('/api/ui', { body: { patch: { promptDock: v } } });
      if (!r.ok) throw new Error(r.reason || '没改成');
    } catch (e) {
      const back = !v;
      S.ui.promptDock = back;
      syncTopSwitch(back);
      syncDock(typeof UI !== 'undefined' ? UI.route : 'studio');
      T('开关保存失败，已恢复');
    }
  }
  function dockSize() {
    const g = dockGap();
    const w = Math.min(320, Math.max(260, innerWidth - g * 2));
    return { w, h: Math.min(w * 2, innerHeight - g * 2) };
  }
  // 浮窗与屏幕边缘的留白：桌面 24px、窄屏 12px。默认位置、历史位置、拖拽边界全用它，
  // 避免贴边或拖动时能顶到边角。
  function dockGap() { return innerWidth < 600 ? 12 : 24; }
  const DOCK_HEAD_H = 40;   // 与 .pt-dock-head / .pt-dock.collapsed 的高度保持一致
  // 按当前展开/收起状态钳制位置：收起时实际只有标题栏高度，可以一直拖到屏幕底部留白处；
  // 展开时恢复按完整高度钳制，保证恢复后下沿不会超出屏幕。
  function dockFit(x, y) {
    const s = dockSize(), g = dockGap();
    const h = PT.dock.collapsed ? DOCK_HEAD_H : s.h;
    return { x: Math.max(g, Math.min(x, innerWidth - s.w - g)),
             y: Math.max(g, Math.min(y, innerHeight - h - g)) };
  }
  function defaultPos() {
    const s = dockSize(), g = dockGap();
    return { x: Math.max(g, innerWidth - s.w - g), y: Math.max(g, innerHeight - s.h - g) };
  }
  function currentPos() {
    const p = (S.ui && S.ui.promptDockPos);
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return dockFit(p.x, p.y);
    return defaultPos();
  }
  function applyPos(el) {
    const p = currentPos(); el.style.left = p.x + 'px'; el.style.top = p.y + 'px';
  }
  function persistPos(el) {
    const p = { x: Math.round(el.getBoundingClientRect().left), y: Math.round(el.getBoundingClientRect().top) };
    S.ui.promptDockPos = p;
    A('/api/ui', { body: { patch: { promptDockPos: p } } });
  }
  function ensureDock() {
    if (PT.dock.el) return PT.dock.el;
    const el = document.createElement('div');
    el.className = 'pt-dock';
    el.innerHTML = `<div class="pt-dock-head" data-drag>
      <span class="pt-grip">⠿</span><b>提示词速取</b>
      <div class="pt-dock-head-a">
        <button type="button" data-dock-min title="隐藏速取窗">−</button>
      </div></div>
      <div class="pt-dock-body"><div class="pt-dock-tools"></div><div class="pt-dock-list"></div></div>
      <div class="pt-dock-editor" hidden></div>`;
    document.body.appendChild(el);
    PT.dock.el = el;
    bindDockHead(el);
    return el;
  }
  function bindDockHead(el) {
    // 最小化 = 隐藏速取窗，同时把顶栏开关切成关闭态；再点开关即可弹出。
    Q('[data-dock-min]', el).onclick = e => {
      e.stopPropagation();
      setDockEnabled(false);
    };
    const head = Q('[data-drag]', el);
    let sx = 0, sy = 0, ox = 0, oy = 0, moved = false;
    head.addEventListener('pointerdown', e => {
      if (e.target.closest('button')) return;
      PT.dock.dragging = true; moved = false;
      const r = el.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
      try { head.setPointerCapture(e.pointerId); } catch (_) { }
      el.classList.add('dragging');
    });
    head.addEventListener('pointermove', e => {
      if (!PT.dock.dragging) return;
      const p = dockFit(ox + e.clientX - sx, oy + e.clientY - sy);
      if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 3) moved = true;
      el.style.left = p.x + 'px'; el.style.top = p.y + 'px';
    });
    const stop = e => {
      if (!PT.dock.dragging) return;
      PT.dock.dragging = false; el.classList.remove('dragging');
      try { head.releasePointerCapture(e.pointerId); } catch (_) { }
      if (moved) persistPos(el);
    };
    head.addEventListener('pointerup', stop);
    head.addEventListener('pointercancel', stop);
  }

  // 按钮上显示的已选标签：最多 2 个彩色 chip，超出用 +N 表示，hover 出 tip 看全部
  function tagFilterCurHtml() {
    const tags = PT.filters.tags.map(tagById).filter(Boolean);
    const exc = (PT.filters.notTags || []).map(tagById).filter(Boolean);
    if (!tags.length && !exc.length) return '<span class="pt-tagfilter-all">全部</span>';
    const chip = (t, no) => `<span class="pt-tagfilter-chip${no ? ' no' : ''}" style="--chip:${E(t.color)}">${no ? '⊘ ' : ''}${E(t.name)}</span>`;
    const all = tags.map(t => chip(t, false)).concat(exc.map(t => chip(t, true)));
    return all.slice(0, 2).join('') + (all.length > 2 ? `<span class="pt-tagfilter-more">+${all.length - 2}</span>` : '');
  }
  function renderDockTools() {
    const el = PT.dock.el, box = Q('.pt-dock-tools', el); if (!box) return;
    closeTagTip();
    box.innerHTML = `<div class="pt-dock-search"><span>⌕</span><input id="ptDockQ" placeholder="搜标题 / 内容 / 标签" value="${E(PT.filters.q)}"></div>
      <div class="pt-dock-cats"><button type="button" class="pt-dchip${PT.filters.cats.length === 0 ? ' on' : ''}" data-dcat="">全部</button>
        ${CATS.map(c => `<button type="button" class="pt-dchip${PT.filters.cats.includes(c.id) ? ' on' : ''}" data-dcat="${c.id}">${c.name}</button>`).join('')}</div>
      <button type="button" class="pt-tagfilter-btn${PT.filters.tags.length || PT.filters.notTags.length ? ' on' : ''}" data-dtagfilter>
        <span class="pt-tagfilter-label">标签筛选</span>
        <span class="pt-tagfilter-cur">${tagFilterCurHtml()}</span>
        <span class="pt-tagfilter-caret">▾</span></button>
      <div class="pt-dock-actions"><button type="button" class="btn primary sm" data-dadd>＋ 新增模板</button>
        <button type="button" class="btn sm" data-dtags>标签管理</button><span class="pt-dock-count" id="ptDockCount"></span></div>`;
    Q('#ptDockQ', box).oninput = e => { PT.filters.q = e.target.value; renderDockList(true); const i = Q('#ptQ'); if (i) i.value = e.target.value; };
    QA('[data-dcat]', box).forEach(b => b.onclick = () => {
      const id = b.dataset.dcat;
      if (!id) PT.filters.cats = [];
      else {
        const i = PT.filters.cats.indexOf(id);
        if (i >= 0) PT.filters.cats.splice(i, 1); else PT.filters.cats.push(id);
      }
      renderDockTools(); renderDockList(true); updateToolbarMeta();
      const c = Q('#ptCatCur'); if (c) c.textContent = filterSummary('cats');
    });
    const tfBtn = Q('[data-dtagfilter]', box);
    tfBtn.onclick = e => { e.stopPropagation(); closeTagTip(); openTagFilter(tfBtn); };
    tfBtn.onmouseenter = showTagTip;
    tfBtn.onmouseleave = closeTagTip;
    Q('[data-dadd]', box).onclick = () => { openDockEditor(newDraft().id); };
    Q('[data-dtags]', box).onclick = openTagManager;
  }

  // hover 提示：列出全部已选标签，带各自颜色
  let tagTip = null;
  function closeTagTip() { if (tagTip) { tagTip.remove(); tagTip = null; } }
  function showTagTip(e) {
    closeTagTip();
    const tags = PT.filters.tags.map(tagById).filter(Boolean);
    const exc = (PT.filters.notTags || []).map(tagById).filter(Boolean);
    if (!tags.length && !exc.length) return;
    const anchor = e.currentTarget;
    const tip = document.createElement('div');
    tip.className = 'pt-tag-tip';
    tip.innerHTML = `<div class="pt-tag-tip-hd">包含 ${tags.length} 个${exc.length ? ` · 排除 ${exc.length} 个` : ''}</div>
      <div class="pt-tag-tip-list">${tags.map(t => `<span class="pt-tag-tip-chip" style="--chip:${E(t.color)}">${E(t.name)}</span>`).join('')}
      ${exc.map(t => `<span class="pt-tag-tip-chip no" style="--chip:${E(t.color)}">⊘ ${E(t.name)}</span>`).join('')}</div>`;
    document.body.appendChild(tip);
    const r = anchor.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(r.left, innerWidth - tip.offsetWidth - 8)) + 'px';
    tip.style.top = (r.top - tip.offsetHeight - 8 > 8 ? r.top - tip.offsetHeight - 8 : r.bottom + 8) + 'px';
    tagTip = tip;
  }

  // 标签筛选弹层：标签再多也不占工具栏高度，全部标签在带滚动的面板里可见可选
  let tagFilterPop = null;
  function closeTagFilter() {
    if (tagFilterPop) { tagFilterPop.remove(); tagFilterPop = null; document.removeEventListener('pointerdown', onTagFilterOutside, true); }
  }
  function onTagFilterOutside(e) {
    if (tagFilterPop && !tagFilterPop.contains(e.target) && !e.target.closest('[data-dtagfilter]')) closeTagFilter();
  }
  function tagFilterItemHtml(t) {
    const on = t.id === '' ? !PT.filters.tags.length && !PT.filters.notTags.length : PT.filters.tags.includes(t.id);
    const no = t.id !== '' && PT.filters.notTags.includes(t.id);
    return `<div class="pt-tagfilter-item${on ? ' on' : ''}${no ? ' no' : ''}" style="--chip:${E(t.color)}">
      <button type="button" class="pt-tagfilter-main" data-tfid="${E(t.id)}"><i></i><span>${E(t.name)}</span><b>${on ? '✓' : no ? '⊘' : ''}</b></button>
      ${t.id === '' ? '' : `<button type="button" class="pt-tagfilter-ex" data-tfno="${E(t.id)}" title="排除这个标签">⊘</button>`}</div>`;
  }
  function syncTagFilterPop() {
    if (!tagFilterPop) return;
    const list = Q('.pt-tagfilter-list', tagFilterPop);
    if (list) {
      const kw = (Q('[data-tfq]', tagFilterPop)?.value || '').trim().toLowerCase();
      const all = [{ id: '', name: '全部标签', color: '#9aa5b1' }].concat(PT.tags)
        .filter(t => !t.id || !kw || t.name.toLowerCase().includes(kw));
      const top = list.scrollTop;   // 多选时保留滚动位置，别选一个就跳回顶部
      list.innerHTML = all.map(tagFilterItemHtml).join('')
        || '<div class="pt-empty-sm">没有匹配的标签</div>';
      list.scrollTop = top;
    }
    QA('[data-tfid]', tagFilterPop).forEach(b => b.onclick = toggleTagFilterItem);
    QA('[data-tfno]', tagFilterPop).forEach(b => b.onclick = toggleTagFilterEx);
    const n = PT.filters.tags.length + PT.filters.notTags.length;
    const done = Q('[data-tfdone]', tagFilterPop);
    if (done) done.textContent = n ? `完成（${n}）` : '完成';
    const mb = Q('[data-tfmode]', tagFilterPop);
    if (mb) mb.textContent = PT.filters.tagMode === 'any' ? '任一即可' : '全部满足';
  }
  function toggleTagFilterItem(e) {
    const id = e.currentTarget.dataset.tfid;
    if (id === '') { PT.filters.tags = []; PT.filters.notTags = []; }
    else {
      const i = PT.filters.tags.indexOf(id);
      if (i >= 0) PT.filters.tags.splice(i, 1); else PT.filters.tags.push(id);
      const j = PT.filters.notTags.indexOf(id); if (j >= 0) PT.filters.notTags.splice(j, 1);
    }
    syncTagFilterPop();          // 只重画弹层内容，不关闭 —— 可以连续多选
    renderDockTools(); renderDockList(true); updateToolbarMeta();
  }
  function toggleTagFilterEx(e) {
    const id = e.currentTarget.dataset.tfno;
    const j = PT.filters.notTags.indexOf(id);
    if (j >= 0) PT.filters.notTags.splice(j, 1); else PT.filters.notTags.push(id);
    const i = PT.filters.tags.indexOf(id); if (i >= 0) PT.filters.tags.splice(i, 1);
    syncTagFilterPop(); renderDockTools(); renderDockList(true); updateToolbarMeta();
  }
  function openTagFilter(anchor) {
    closeTagFilter();
    const pop = document.createElement('div');
    pop.className = 'pt-tagfilter-pop';
    pop.innerHTML = `<div class="pt-tagfilter-hd">按标签筛选（左键包含 · ⊘ 排除） <button type="button" data-tfclose>✕</button></div>
      <div class="pt-tagfilter-search"><input class="input" data-tfq placeholder="查找标签…"></div>
      <div class="pt-tagfilter-list"></div>
      <div class="pt-tagfilter-ft"><button type="button" class="pt-tagfilter-mode" data-tfmode></button>
        <button type="button" class="btn sm" data-tfclear>清空</button>
        <button type="button" class="btn primary sm" data-tfdone>完成</button></div>`;
    document.body.appendChild(pop);
    tagFilterPop = pop;              // 必须先赋值：syncTagFilterPop 依赖它
    syncTagFilterPop();
    Q('[data-tfq]', pop).oninput = syncTagFilterPop;
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth || 220, h = pop.offsetHeight || 260;
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    pop.style.top = (r.bottom + h + 8 > innerHeight ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
    Q('[data-tfclose]', pop).onclick = closeTagFilter;
    Q('[data-tfdone]', pop).onclick = closeTagFilter;
    Q('[data-tfmode]', pop).onclick = () => {
      PT.filters.tagMode = PT.filters.tagMode === 'any' ? 'all' : 'any';
      syncTagFilterPop(); renderDockTools(); renderDockList(true); updateToolbarMeta();
      const c = Q('#ptTagCur'); if (c) c.textContent = filterSummary('tags');
    };
    Q('[data-tfclear]', pop).onclick = () => { PT.filters.tags = []; PT.filters.notTags = []; syncTagFilterPop(); renderDockTools(); renderDockList(true); updateToolbarMeta(); };
    setTimeout(() => document.addEventListener('pointerdown', onTagFilterOutside, true), 0);
  }

  function dockCardHtml(d) {
    const open = PT.expanded.has(d.id);
    const media = (d.media || []).length
      ? `<div class="pt-dcard-media">${d.media.map(m => `<div class="pt-dthumb" data-mid="${E(m.id)}" role="button"
          tabindex="0" title="${E(m.name)} · 点击查看">${thumbInner(m)}<i>${kindName(m.kind)}</i><b>${mediaNo(d, m)}</b></div>`).join('')}</div>`
      : '';
    return `<div class="pt-dcard${open ? ' open' : ''}" data-id="${E(d.id)}">
      <div class="pt-dcard-hd">
        <i class="pt-dot" style="background:${d.category === 'video' ? '#7c6bd6' : d.category === 'audio' ? '#2fa36b' : '#2f6bff'}"></i>
        <b title="${E(d.title)}">${E(d.title || '未命名模板')}</b>
        <span class="pt-dcard-ops">
          <button type="button" data-dcopy title="拷贝">⧉</button>
          <button type="button" data-dedit title="编辑">✎</button>
          <button type="button" data-ddel title="删除">✕</button>
        </span></div>
      <div class="pt-dcard-meta">${catName(d.category)} · ${(d.tags || []).map(id => { const t = tagById(id); return t ? `<em style="--chip:${E(t.color)}">${E(t.name)}</em>` : ''; }).join('')}</div>
      ${media}
      <div class="pt-dcard-body" data-dbody>${E(d.content || '（还没有内容）')}</div>
    </div>`;
  }

  function bindDockCard(card) {
    const d = PT.drafts.get(card.dataset.id); if (!d) return;
    Q('[data-dcopy]', card).onclick = e => { e.stopPropagation(); copyText(d.content); };
    Q('[data-dedit]', card).onclick = e => { e.stopPropagation(); openDockEditor(d.id); };
    Q('[data-ddel]', card).onclick = e => { e.stopPropagation(); deleteOne(d.id); };
    QA('.pt-dthumb', card).forEach(el => {
      el.onclick = e => {
        e.stopPropagation();
        const m = (d.media || []).find(x => x.id === el.dataset.mid);
        if (m) openMediaViewer(m);
      };
    });
    paintVideoThumbs(card);
    Q('.pt-dcard-hd', card).onclick = e => {
      if (e.target.closest('button')) return;
      if (PT.expanded.has(d.id)) PT.expanded.delete(d.id); else PT.expanded.add(d.id);
      card.classList.toggle('open');
    };
  }

  function renderDockList(force) {
    const el = PT.dock.el;
    if (!el || el.hidden) return;
    const list = Q('.pt-dock-list', el); if (!list) return;
    const rows = filteredRows();
    const count = Q('#ptDockCount', el); if (count) count.textContent = rows.length + ' 条';
    // 滚动加载：force / 搜索变化时从头渲染首批，滚动到底追加下一批
    if (force) { PT.dock.shown = LOAD_STEP; list.scrollTop = 0; }
    if (!PT.dock.shown) PT.dock.shown = LOAD_STEP;
    const shown = Math.min(PT.dock.shown, rows.length);
    const slice = rows.slice(0, shown);
    const more = shown < rows.length
      ? '<div class="pt-empty-sm pt-dock-more">向下滚动加载更多</div>' : '';
    list.innerHTML = slice.length ? slice.map(dockCardHtml).join('') + more
      : '<div class="pt-empty-sm">没有匹配的模板<br>点「＋ 新增模板」建一个</div>';
    QA('.pt-dcard', list).forEach(bindDockCard);
    PT.dock.listReady = true;   // 列表已画好；之后开合窗口直接显示，不再重建
    if (!PT.dock._scrollBound) {
      list.addEventListener('scroll', () => {
        if (list.scrollTop + list.clientHeight < list.scrollHeight - 60) return;
        const all = filteredRows();
        if (PT.dock.shown >= all.length) return;
        const next = all.slice(PT.dock.shown, PT.dock.shown + LOAD_STEP);
        const moreEl = Q('.pt-dock-more', list);
        const anchor = document.createElement('div');
        anchor.innerHTML = next.map(dockCardHtml).join('');
        while (anchor.firstChild) list.insertBefore(anchor.firstChild, moreEl || null);
        next.forEach(d => {
          const card = QA('.pt-dcard', list).find(c => c.dataset.id === d.id);
          if (card) bindDockCard(card);
        });
        PT.dock.shown += next.length;
        if (PT.dock.shown >= all.length && moreEl) moreEl.remove();
      });
      PT.dock._scrollBound = true;
    }
  }

  function openDockEditor(id) {
    const d = PT.drafts.get(id); if (!d) return;
    const el = PT.dock.el, editor = Q('.pt-dock-editor', el);
    PT.dock.editor = id;
    el.classList.add('editing');
    editor.hidden = false;
    const catOpts = `<option value="" disabled${d.category ? '' : ' selected'}>选择分类</option>`
      + CATS.map(c => `<option value="${c.id}"${d.category === c.id ? ' selected' : ''}>${c.name}</option>`).join('');
    editor.innerHTML = `<div class="pt-ed-hd"><button type="button" data-ed-back>←</button><b>${d.isNew ? '新增模板' : '编辑模板'}</b>
        <button type="button" data-ed-save class="pt-ed-save" ${dirty(d) ? '' : 'disabled'}>保存</button></div>
      <div class="pt-ed-body">
        <label>标题</label><input class="input" data-ed-f="title" value="${E(d.title)}" placeholder="给模板起个名字" maxlength="80">
        <label>分类 <span class="pt-req">必选</span></label><select class="input" data-ed-f="category">${catOpts}</select>
        <label>标签 <span class="pt-req">最多 ${MAX_TAGS} 个</span></label><div class="pt-ed-tags">${tagRowHtml(d, true)}</div>
        <label>参考素材 <span class="pt-req">图片 / 视频 / 音频，最多 ${MAX_MEDIA} 个</span></label>
        <div class="pt-media sm" data-mrow>${mediaRowHtml(d, true)}</div>
        <label>提示词内容</label><textarea class="input" data-ed-f="content" rows="4" placeholder="在这里写提示词…">${E(d.content)}</textarea>
        <button type="button" class="btn danger sm pt-ed-del" data-ed-del>${d.isNew ? '移除这张卡片' : '删除这个模板'}</button>
      </div>`;
    const syncSave = () => { Q('.pt-ed-save', editor).disabled = !dirty(d); };
    QA('[data-ed-f]', editor).forEach(inp => inp.oninput = () => { d[inp.dataset.edF] = inp.value; syncSave(); });
    QA('[data-ed-f]', editor).forEach(inp => { if (inp.tagName === 'SELECT') inp.onchange = () => { d[inp.dataset.edF] = inp.value; syncSave(); }; });
    const rebuildTags = () => {
      Q('.pt-ed-tags', editor).innerHTML = tagRowHtml(d, true);
      bindTagRow(Q('.pt-ed-tags', editor), d, () => { rebuildTags(); syncSave(); });
      syncSave();
    };
    bindTagRow(Q('.pt-ed-tags', editor), d, () => { rebuildTags(); syncSave(); });
    const bindMedia = () => {
      const row = Q('[data-mrow]', editor);
      bindMediaRow(row, d, () => {
        row.innerHTML = mediaRowHtml(d, true);
        bindMedia();
        syncSave();
      });
    };
    bindMedia();
    Q('[data-ed-back]', editor).onclick = closeDockEditor;
    Q('.pt-ed-save', editor).onclick = async () => { await saveOne(d.id); if (!PT.drafts.has(d.id)) closeDockEditor(); };
    Q('[data-ed-del]', editor).onclick = () => { closeDockEditor(); deleteOne(d.id); };
  }
  function closeDockEditor() {
    if (!PT.dock.el) return;
    PT.dock.editor = '';
    const editor = Q('.pt-dock-editor', PT.dock.el);
    if (editor) { editor.hidden = true; editor.innerHTML = ''; }
    PT.dock.el.classList.remove('editing');
  }

  function syncDock(route) {
    closeTagPop();   // 任何页面重绘都把旧位置的标签浮层收掉
    closeTagFilter(); closeTagTip();
    syncTopSwitch(!!(S.ui && S.ui.promptDock));
    const enabled = !!(S.ui && S.ui.promptDock) && (route === 'studio' || route === 'canvas');
    // 关键：首次进创作台/画布时，模板数据还没拉过（ensureLoaded 只在管理台页被调用）。
    // 浮窗一旦要显示就必须主动取数据，不能等用户去管理台拨一次开关。
    if (enabled) ensureLoaded();
    const el = PT.dock.el;
    if (!enabled) {
      if (el) { el.hidden = true; closeDockEditor(); }
      PT.dock.route = route; PT.dock.enabled = false;
      return;
    }
    const e = ensureDock();
    // 只有「数据变了 / 首次还没画 / 换了页面」才重建列表；普通开合只是显示缓存的 DOM。
    const changed = !PT.dock.listReady || (PT.dock.route !== route);
    e.hidden = false;
    PT.dock.enabled = true; PT.dock.route = route;
    const s = dockSize(); e.style.width = s.w + 'px'; e.style.height = s.h + 'px';
    applyPos(e);
    if (changed) { renderDockTools(); renderDockList(true); }
  }

  window.addEventListener('resize', () => {
    const el = PT.dock.el;
    if (el && !el.hidden) { const s = dockSize(); el.style.width = s.w + 'px'; el.style.height = s.h + 'px'; applyPos(el); }
    // 跨过 760px 断点时，瀑布流列数变了，重排卡片保证顺序与列数正确
    if (typeof UI !== 'undefined' && UI.route === 'admin' && UI.adminPage === 'prompts') {
      const have = gridCols().length, want = gridColCount();
      if (have && have !== want) refreshAdminGrid();
    }
    closeTagPop();
  });
  function onDocScroll(e) {
    // 浮层内部列表滚动（滚轮 / 拖拽滚动条）不能把浮层关掉；只有页面本身滚动时才收起。
    if (tagPop && e.target instanceof Node && tagPop.contains(e.target)) return;
    closeTagPop();
  }
  document.addEventListener('scroll', onDocScroll, true);

  window.PromptLib = {
    viewAdmin, bindAdmin, syncDock, setDockEnabled,
    openTagManager,
    ensureLoaded,
    // 给自动化测试用的只读快照
    _state: PT,
  };
})();
