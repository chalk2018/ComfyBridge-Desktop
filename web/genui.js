/* ============================================================ 生成功能「共用块」
   创作台的图片生成 / 视频生成，和视频生成画布里的生成节点，用的是同一份：
     · GenUI.imageInput  —— 图片生成的输入块（画法 / 参考图 / 描述 / 档位 / 风格 / 底部）
     · GenUI.videoInput  —— 视频生成的输入块（档位 / 时长 / 参考槽 / 描述 / 底部）
     · GenUI.jobCard     —— 生成结果块（.job 那张卡，创作台对话就是它）
   两边都调用这里，DOM 结构和 class 完全一致，样式只维护 index.html 那一套，
   画布里不再另造一套节点 UI。差异只通过 o.show / o.acts / o.ids 这几个开关表达：
     show = {mode, upload, template, seeFull, hints, kbd, emptyHint, slotUpload}
     acts = {ink:'data-ink', grade:'data-grade', ...}   各交互的 data-* 名
     ids  = {editor:'promptInput', box:'inputbox', ...} 元素 id（创作台用，画布不传）
   调用方负责把 data-* 绑上事件；这里只产出 HTML。 */

window.GenUI = (function () {
  'use strict';

  var E = function (s) {
    if (typeof esc === 'function') return esc(s == null ? '' : s);
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  };

  var ACT = {
    mode: 'data-mode', ink: 'data-ink', line: 'data-line', grade: 'data-grade',
    styleSel: 'data-stylesel', styleText: 'data-styletext',
    refAt: 'data-refat', refRm: 'data-rmref', addRef: 'data-addref',
    gen: 'data-gen', seeFull: 'data-seefull', template: 'data-sheet-template',
    preview: 'data-preview', again: 'data-again', refine: 'data-refine', queue: 'data-qadd',
    zoom: 'data-zoom', dl: 'data-dl', copy: 'data-copyimg', retry: 'data-retry', raw: 'data-raw',
    zoomMat: 'data-zoommat', vmode: 'data-vmode', vopt: 'data-vopt',
    vgrid: 'data-vgrid', vcell: 'data-vcell', vup: 'data-vup', vrm: 'data-vrm', vat: 'data-vat',
    duration: 'data-vduration', qcancel: 'data-qcancel',
    dlref: 'data-dlref', copyref: 'data-copyref',
    // 通用参数条：分段（layout/ink/ref/nochar/grid…）/ 下拉 / 文本框 / 多行框
    cfgseg: 'data-cseg', cfgsel: 'data-csel', cfgtext: 'data-ctext', cfgextra: 'data-cextra',
  };
  function A(o, k) { return (o && o.acts && o.acts[k]) || ACT[k]; }
  function attr(o, k, v) { return A(o, k) + '="' + E(v) + '"'; }
  function idAttr(o, k) { return (o && o.ids && o.ids[k]) ? ' id="' + o.ids[k] + '"' : ''; }
  function on(o, k) { return !o.show || o.show[k] !== false; }

  /* ------------------------------------------------------------ 编辑器 */
  /* src 可以外部给（画布：上游生成节点的成品图，id 是节点 id，按 ref 取会 404） */
  function refChip(id, slot, src) {
    var s = src || (typeof refUrl === 'function' ? refUrl(id) : '');
    return '<span class="refchip" contenteditable="false" data-ref="' + E(id) + '" data-chipref="' + E(id) + '" title="第 ' + slot + ' 张参考图">'
      + '<img src="' + E(s) + '" alt="参考图' + slot + '">'
      + '<b>图片 ' + slot + '</b><span class="x" data-rmchip title="移除引用">✕</span></span>';
  }
  /* 把纯文本里的 <imageN> 还原成可编辑的缩略图 chip。
     N 只数图片类参考（视频 / 音频另算），跟界面上的「图 1 / 视频 1」编号一致。 */
  function editorHTML(text, refs) {
    var imgs = (refs || []).filter(function (r) { return (r.kind || 'image') === 'image'; });
    return String(text || '').split(/(<image\d+>)/g).map(function (p) {
      var m = p.match(/^<image(\d+)>$/);
      if (m) {
        var slot = +m[1], ref = imgs[slot - 1];
        return ref ? refChip(ref.id, slot, ref.src)
          : '<span class="refchip miss" contenteditable="false" data-miss="1" data-token="' + E(p) + '" title="第 ' + slot + ' 张参考图（已移除）">'
            + '<b>图片 ' + slot + '</b><span class="x" data-rmchip>✕</span></span>';
      }
      return E(p).replace(/\n/g, '<br>');
    }).join('');
  }
  /* contenteditable → 纯文本；chip 按「第几张图片」还原成 <imageN> */
  function editorText(el, refs) {
    if (!el) return '';
    var imgs = (refs || []).filter(function (r) { return (r.kind || 'image') === 'image'; });
    var out = '';
    var walk = function (nodes) {
      Array.prototype.forEach.call(nodes, function (n) {
        if (n.nodeType === 3) { out += n.textContent; return; }
        if (n.nodeType !== 1) return;
        if (n.classList && n.classList.contains('refchip')) {
          var idx = imgs.findIndex(function (r) { return r.id === n.dataset.ref; });
          if (idx >= 0) out += '<image' + (idx + 1) + '>';
          return;
        }
        if (n.tagName === 'BR') { out += '\n'; return; }
        var block = n.tagName === 'DIV' || n.tagName === 'P';
        if (block && out && out.slice(-1) !== '\n') out += '\n';
        walk(n.childNodes);
        if (block && out.slice(-1) !== '\n') out += '\n';
      });
    };
    walk(el.childNodes);
    return out.replace(/\n{3,}/g, '\n\n');
  }

  /* ------------------------------------------------------------ 参考图缩略 */
  function refThumbs(o) {
    var refs = o.refs || [];
    if (!refs.length) return o.emptyRefs || '';
    return refs.map(function (r, i) {
      // r.total / r.idx：上游生成节点有几个成品、这次用的是第几张（画布用，创作台没有）
      var n = r.total || 0, cur = r.runIdx || n;
      return '<div class="ref-thumb" data-refthumb="' + E(r.id) + '">'
        + (r.kind === 'video'
          ? '<video src="' + E(r.src) + '" muted playsinline preload="metadata"></video>'
          : '<img src="' + E(r.src) + '" alt="参考图' + (i + 1) + '">')
        + '<span class="rt-n" data-refn="' + E(r.id) + '"' + (n > 1 ? '' : ' style="display:none"')
        + ' title="上游这张节点的成品编号">' + E(cur + '/' + n) + '</span>'
        + '<button class="at" ' + attr(o, 'refAt', r.id) + ' title="引用到文字里（@）" aria-label="引用到文字">@</button>'
        + '<button class="x" ' + attr(o, 'refRm', r.id) + ' title="移除这张参考图">✕</button></div>';
    }).join('');
  }

  /* ------------------------------------------------------------ 图片：三行参数 */
  function inkRow(o) {
    var f = o, lines = o.lineStyles || [];
    var extra = '';
    if (f.ink === 'line' && lines.length) {
      extra = '<select class="input segsel"' + idAttr(o, 'lineSel') + ' ' + attr(o, 'line', '1') + ' title="线稿风格句">'
        + lines.map(function (l) {
          return '<option value="' + E(l.id) + '"' + (f.lineStyle === l.id ? ' selected' : '') + '>' + E(l.name) + '</option>';
        }).join('') + '</select>'
        + (on(o, 'hints') ? '<span class="warnhint">线稿别写颜色词 · 画风只借造型</span>' : '');
    } else if (on(o, 'hints') && on(o, 'style')) {
      extra = '<span class="tiny muted">彩色：风格行用下面的「风格」</span>';
    }
    return '<div class="sheetbar"><span class="slb">画法</span>'
      + '<div class="seg" role="group" aria-label="画法">'
      + '<button class="segb ' + (f.ink === 'color' ? 'on' : '') + '" ' + attr(o, 'ink', 'color') + '>彩色</button>'
      + '<button class="segb ' + (f.ink === 'line' ? 'on' : '') + '" ' + attr(o, 'ink', 'line') + '>线稿</button>'
      + '</div>' + extra + '</div>';
  }
  function gradeRow(o) {
    var gs = o.grades || [], cur = null;
    gs.forEach(function (g) { if (g.name === o.grade) cur = g; });
    if (!cur) cur = gs[0] || null;
    if (!gs.length) return '';
    var note = (cur || {}).note || '';
    var size = ((cur || {}).effective || {}).size || '';
    return '<div class="sheetbar"><span class="slb">档位</span>'
      + '<div class="seg" role="group" aria-label="画质档">'
      + gs.map(function (g) {
        return '<button class="segb ' + (g.name === (cur || {}).name ? 'on' : '') + '" ' + attr(o, 'grade', g.name)
          + ' title="' + E(g.note || '') + ' · 出图 ' + E((g.effective || {}).size || '') + '">' + E(g.name) + '</button>';
      }).join('')
      + '</div>'
      + (on(o, 'hints') ? '<span class="tiny muted">' + E(note) + (size ? ' · 出图 ' + E(size) : '') + '</span>' : '')
      + '</div>';
  }
  function styleRow(o) {
    // 图生图不展示风格行：show.style=false 时整行不渲染。
    if (!on(o, 'style')) return '';
    var styles = o.styles || [], i2i = o.i2i !== false;
    var custom = i2i || o.styleCustom !== false;
    return '<div class="sheetbar"><span class="slb">风格</span>'
      + '<select class="input segsel"' + idAttr(o, 'styleSel') + ' ' + attr(o, 'styleSel', '1')
      + ' style="min-width:148px" title="' + (i2i ? '图生图只能用自定义风格' : '12 类实测画风') + '">'
      + styles.map(function (s) {
        return '<option value="' + E(s) + '"' + ((!custom && o.style === s) ? ' selected' : '') + (i2i ? ' disabled' : '') + '>' + E(s) + '</option>';
      }).join('')
      + '<option value="__custom"' + (custom ? ' selected' : '') + '>自定义…</option></select>'
      + '<input class="input segtext"' + idAttr(o, 'styleText') + ' ' + attr(o, 'styleText', '1')
      + ' placeholder="画种 + 材质笔触 + 色调 + 光线，一两句" value="' + E(o.styleText || '') + '"'
      + (custom ? '' : ' style="display:none"') + '>'
      + (on(o, 'hints')
        ? '<span class="tiny muted">' + (i2i ? '图生图：只能用自定义风格（预设已禁用）'
          : o.ink === 'line' ? '线稿：画风只借造型比例与线条气质，不上色' : '彩色：决定「风格」那一行的画风') + '</span>'
        : '')
      + '</div>';
  }

  /* ------------------------------------------------------------ 图片输入块 */
  function imageInput(o) {
    o = o || {};
    var i2i = o.i2i !== false;
    var empty = !String(o.prompt || '').trim();
    var body = '<div class="sheetbody">'
      + inkRow(o)
      + (i2i ? '<div class="refs"' + idAttr(o, 'refsRow') + '>' + refThumbs(o) + '</div>' : '')
      + '<div class="editor"' + idAttr(o, 'editor') + (o.editorAttrs ? ' ' + o.editorAttrs : '')
      + ' contenteditable="true" data-ph="' + E(o.placeholder || '') + '">'
      + editorHTML(o.prompt, o.refs) + '</div>'
      + gradeRow(o) + styleRow(o)
      + (o.extra || '')
      + '</div>';
    var foot = '<div class="foot">';
    if (on(o, 'upload')) {
      foot += '<button class="btn ghost sm"' + idAttr(o, 'addRef') + ' ' + attr(o, 'addRef', '1') + '>＋ 上传</button>';
    } else if (o.uploadHint) {
      foot += '<span class="tiny muted">' + E(o.uploadHint) + '</span>';
    }
    if (on(o, 'template')) foot += '<button class="btn ghost sm"' + attr(o, 'template', '1') + '>✎ 设定图模板</button>';
    if (on(o, 'seeFull')) foot += '<button class="btn ghost sm"' + idAttr(o, 'previewBtn') + ' ' + attr(o, 'preview', '1') + '>' + (o.previewOpen ? '收起提示词' : '看完整提示词') + '</button>';
    if (o.previewOpen) foot += '<button class="btn ghost sm"' + idAttr(o, 'copyBtn') + ' ' + attr(o, 'copy', '1') + '>复制</button>';
    if (on(o, 'hints') && o.hint) foot += '<span class="tiny muted"' + idAttr(o, 'hint') + '>' + E(o.hint) + '</span>';
    if (on(o, 'emptyHint')) {
      foot += '<span class="tiny muted" id="' + (o.ids && o.ids.emptyHint ? o.ids.emptyHint : 'emptyHint') + '"'
        + (empty ? '' : ' style="display:none"') + '>写点描述再发送</span>';
    }
    foot += '<span style="flex:1"></span>';
    if (on(o, 'kbd')) foot += '<span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>';
    foot += '<button class="sendbtn"' + idAttr(o, 'send') + ' ' + attr(o, 'gen', o.target || '1') + ' title="生成"'
      + ((o.sendDisabled === undefined ? empty : o.sendDisabled) ? ' disabled' : '') + '>↑</button>';
    foot += '</div>';
    return '<div class="inputbox sheetbox' + (o.boxClass ? ' ' + o.boxClass : '') + '"' + idAttr(o, 'box') + '>' + body + foot + '</div>';
  }

  /* ------------------------------------------------------------ 视频：素材槽 */
  function vRefSlot(o) {
    var kind = o.kind, ref = o.ref;
    var icon = kind === 'image' ? '🖼' : kind === 'video' ? '🎞' : '🎵';
    if (!ref) {
      if (o.upload === false) {
        return '<div class="vslot" style="pointer-events:none"><span class="vs-ic">' + icon + '</span>'
          + '<span class="vs-lb">' + E(o.label) + '</span><span class="vs-hint">接素材</span></div>';
      }
      return '<button class="vslot" ' + attr(o, 'vup', kind) + '>'
        + '<span class="vs-ic">' + icon + '</span><span class="vs-lb">' + E(o.label) + '</span><span class="vs-hint">点击上传</span></button>';
    }
    var token = '[' + (kind === 'image' ? '图' : kind === 'video' ? '视频' : '音频') + ' ' + (o.idx || 1) + ']';
    var n = o.total || 0, cur = o.runIdx || n;
    return '<div class="vslot has" data-refthumb="' + E(ref.id) + '"'
      + (o.acts && o.acts.vslot ? ' ' + o.acts.vslot + '="' + E(kind + '|' + ref.id) + '"' : '') + '>'
      + (kind === 'image' ? '<img src="' + E(ref.src) + '" alt="' + E(o.label) + '">'
        : kind === 'video' ? '<video src="' + E(ref.src) + '" muted playsinline preload="metadata"></video>'
          : '<span class="vs-audio">🎵</span>')
      + '<span class="vs-lb2">' + E(o.label) + '</span>'
      + (n > 1
        ? '<span class="vs-n" data-refn="' + E(ref.id) + '" title="用的是这张节点的最新成品（第 ' + cur + ' 张 / 共 ' + n + ' 张）">' + E(cur + '/' + n) + '</span>'
        : '<span class="vs-n" data-refn="' + E(ref.id) + '" style="display:none"></span>')
      + '<button class="at" ' + attr(o, 'vat', ref.id) + ' title="引用到提示词">@</button>'
      + '<button class="x" ' + attr(o, 'vrm', ref.id) + ' title="移除">✕</button>'
      + '<span class="vs-token">' + E(token) + '</span></div>';
  }

  /* ------------------------------------------------------------ 视频输入块 */
  function videoInput(o) {
    o = o || {};
    var gs = o.grades || [], cur = null;
    gs.forEach(function (g) { if (g.name === o.grade) cur = g; });
    if (!cur) cur = gs[0] || null;
    var dur = Math.min(15, Math.max(4, +o.duration || 5.2));
    var empty = !String(o.prompt || '').trim();
    var meta = o.sizeLabel ? o.sizeLabel + ' · ' : '';
    meta += (+((cur || {}).steps) || 8) + ' 步 · ' + ((+((cur || {}).steps) || 8) >= 20 ? '不挂 LoRA' : 'LoRA 加速');

    var body = '<div class="sheetbody">';
    if (gs.length) {
      body += '<div class="sheetbar"><span class="slb">档位</span>'
        + '<div class="seg" role="group" aria-label="档位">'
        + gs.map(function (g) {
          return '<button class="segb ' + (cur && g.name === cur.name ? 'on' : '') + '" ' + attr(o, 'grade', g.name)
            + ' title="' + E(g.note || '') + '">' + E(g.name) + '</button>';
        }).join('') + '</div>'
        + (on(o, 'hints') ? '<span class="tiny muted">' + E((cur || {}).note || '') + '</span>' : '')
        + '</div>';
    }
    if (o.duration !== false) {
      body += '<div class="sheetbar vdurbar"><span class="slb">时长</span>'
        + '<input type="number" class="input vdur-num"' + idAttr(o, 'durNum') + ' ' + attr(o, 'duration', 'num')
        + ' min="4" max="15" step="0.01" value="' + dur.toFixed(2) + '" aria-label="视频时长（秒）">'
        + '<span class="tiny muted vdur-unit">秒</span>'
        + '<input type="range" class="vdur-range"' + idAttr(o, 'durRange') + ' ' + attr(o, 'duration', 'range')
        + ' min="4" max="15" step="0.1" value="' + dur + '">'
        + (on(o, 'hints') ? '<span class="tiny muted vdur-meta">' + E(meta) + '</span>' : '')
        + '</div>';
    }
    body += (o.slots || '');
    body += '<div class="editor"' + idAttr(o, 'editor') + (o.editorAttrs ? ' ' + o.editorAttrs : '')
      + ' contenteditable="true" data-ph="' + E(o.placeholder || '') + '">'
      + editorHTML(o.prompt, o.refs) + '</div>';
    body += '</div>';

    var foot = '<div class="foot">';
    if (on(o, 'hints') && o.hint) foot += '<span class="tiny muted">' + E(o.hint) + '</span>';
    if (on(o, 'emptyHint')) {
      foot += '<span class="tiny muted" id="' + (o.ids && o.ids.emptyHint ? o.ids.emptyHint : 'emptyHint') + '"'
        + (empty ? '' : ' style="display:none"') + '>写点描述再发送</span>';
    }
    foot += '<span style="flex:1"></span>';
    if (on(o, 'kbd')) foot += '<span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>';
    foot += '<button class="sendbtn"' + idAttr(o, 'send') + ' ' + attr(o, 'gen', o.target || '1') + ' title="生成"'
      + ((o.sendDisabled === undefined ? empty : o.sendDisabled) ? ' disabled' : '') + '>↑</button>';
    foot += '</div>';
    return '<div class="inputbox sheetbox"' + idAttr(o, 'box') + '>' + body + foot + '</div>';
  }

  /* ------------------------------------------------------------ 通用参数条
     各子功能的输入区（设定图 / 故事板 / 视频 / 音频）控件不一样，但每一行都是
     「标签 + 一组按钮 / 一个下拉 / 一个输入框 + 灰色说明」，所以把这行抽出来，
     各节点只给一份 spec，DOM 仍是创作台那套 .sheetbar / .segb / .segsel，
     样式天然同源。
       spec = { label, key, type:'seg'|'sel'|'text', act, value, ph, hint, width, cls,
                options:[{v,label,title,disabled}] } */
  function barHTML(o, spec) {
    spec = spec || {};
    var k = spec.key || spec.label;
    var act = spec.act || (spec.type === 'sel' ? 'cfgsel' : spec.type === 'text' ? 'cfgtext' : 'cfgseg');
    var inner = '';
    if (spec.type === 'sel') {
      inner = '<select class="input segsel"' + idAttr(o, spec.key) + ' ' + attr(o, act, k)
        + (spec.width ? ' style="min-width:' + spec.width + 'px"' : '') + '>'
        + (spec.options || []).map(function (op) {
          return '<option value="' + E(op.v) + '"' + (String(op.v) === String(spec.value) ? ' selected' : '')
            + (op.disabled ? ' disabled' : '') + '>' + E(op.label) + '</option>';
        }).join('') + '</select>';
    } else if (spec.type === 'text') {
      inner = '<input class="input segtext"' + idAttr(o, spec.key) + ' ' + attr(o, act, k)
        + ' value="' + E(spec.value || '') + '" placeholder="' + E(spec.ph || '') + '">';
    } else {
      inner = '<div class="seg" role="group" aria-label="' + E(spec.label) + '">'
        + (spec.options || []).map(function (op) {
          // 档位用的是结果/提交都在用的 data-cgrade，值就是档位名，不带 key 前缀
          var v = act === 'grade' ? op.v : k + '|' + op.v;
          return '<button class="segb ' + (String(op.v) === String(spec.value) ? 'on' : '') + '"'
            + ' ' + attr(o, act, v)
            + (op.title ? ' title="' + E(op.title) + '"' : '')
            + (op.disabled ? ' disabled' : '') + '>' + E(op.label) + (op.sub ? '<i class="segx">' + E(op.sub) + '</i>' : '')
            + '</button>';
        }).join('') + '</div>';
    }
    return '<div class="sheetbar"' + (spec.cls ? ' ' + spec.cls : '') + '>'
      + '<span class="slb">' + E(spec.label) + '</span>' + inner + (spec.extra || '')
      + (spec.hint && on(o, 'hints') ? '<span class="tiny muted">' + E(spec.hint) + '</span>' : '')
      + '</div>';
  }

  /* ------------------------------------------------------------ 通用输入块
     给「除图片生成 / 视频生成以外的子功能节点」用（设定图 / 故事板 / 音频），
     也给以后新加的子功能用：before / after 两段 HTML 由调用方按该子功能的
     控件拼（参考槽、版式、画法、档位、风格、歌词……），描述框与底部栏在这里统一产出，
     所以画布节点和创作台页面永远同一套 class。 */
  function genInput(o) {
    o = o || {};
    var empty = !String(o.prompt || '').trim() && !o.allowEmpty;
    var body = '<div class="sheetbody">'
      + (o.before || '')
      + '<div class="editor"' + idAttr(o, 'editor') + (o.editorAttrs ? ' ' + o.editorAttrs : '')
      + ' contenteditable="true" data-ph="' + E(o.placeholder || '') + '">'
      + editorHTML(o.prompt, o.refs) + '</div>'
      + (o.after || '')
      + '</div>';
    var foot = '<div class="foot">';
    if (on(o, 'hints') && o.hint) foot += '<span class="tiny muted">' + E(o.hint) + '</span>';
    if (on(o, 'emptyHint')) {
      foot += '<span class="tiny muted"' + idAttr(o, 'emptyHint') + (empty ? '' : ' style="display:none"') + '>'
        + E(o.emptyText || '写点描述再发送') + '</span>';
    }
    foot += '<span style="flex:1"></span>';
    if (on(o, 'kbd')) foot += '<span class="tiny muted kbdhint">Ctrl/⌘ + Enter</span>';
    foot += '<button class="sendbtn"' + idAttr(o, 'send') + ' ' + attr(o, 'gen', o.target || '1') + ' title="生成"'
      + ((o.sendDisabled === undefined ? empty : o.sendDisabled) ? ' disabled' : '') + '>↑</button>';
    foot += '</div>';
    return '<div class="inputbox sheetbox' + (o.boxClass ? ' ' + o.boxClass : '') + '"' + idAttr(o, 'box') + '>'
      + body + foot + '</div>';
  }

  /* ------------------------------------------------------------ 结果块（.job 那张卡） */
  function jobCard(j, o) {
    o = o || {};
    var runs = jobRuns(j), base = runs[0];
    if (!base) return '';
    var promptText = j.prompt || '';
    var promptBox = '';
    if (o.prompt !== false) {
      promptBox = '<div class="msg-u" style="background:#f6f8fb;color:#45505f;box-shadow:none;max-width:none;margin-bottom:10px">' + E(promptText) + '</div>'
        + '<div class="promptmeta"><button class="linkbtn" ' + attr(o, 'seeFull', j.id) + '>看完整提示词</button>'
        + '<span class="tiny muted">' + E(jobWfLabel(j)) + (j.sheet
          ? ' · ' + E(j.sheet.ink === 'line' ? '线稿' : '彩色') + (j.sheet.ref === 'lock' ? ' · 锁同角色' : j.sheet.ref === 'style' ? ' · 借画风' : '')
          : '') + ' · ' + String(promptText.length) + ' 字</span></div>';
    }
    if (base.state === 'queued' || base.state === 'running') {
      var pct = Math.round(base.pct || 0);
      var step = Math.round(pct * (base.total || 8) / 100);
      return '<div class="job" id="' + E(j.id) + '">'
        + '<div class="head"><span class="dot a pulse"></span><b>' + (base.state === 'queued' ? '等待执行' : '生成中') + '</b>'
        + '<span class="wf">· ' + E(jobWfLabel(j)) + '</span><span class="gradechip">' + E(base.grade || '默认') + '</span>'
        + '<span style="flex:1"></span><span class="tiny muted">' + fmtTime(j.t0) + '</span></div>'
        + '<div class="body">' + promptBox
        + '<div class="tiny muted" data-jobstep="' + E(base.id) + '">' + (base.state === 'queued' ? 'ComfyUI 马上开始' : '正在采样 · ' + step + '/' + (base.total || 8) + ' 步') + '</div>'
        + '<div class="progress"><i data-jobbar="' + E(base.id) + '" style="width:' + pct + '%"></i></div>'
        + '<div class="spread tiny muted"><span data-jobelapsed="' + E(base.id) + '">' + (base.state === 'queued' ? '等待显卡空闲' : '已用 ' + elapsed(base.t0)) + '</span>'
        + '<b style="color:#2f6bff" data-jobpct="' + E(base.id) + '">' + pct + '%</b></div>'
        + '</div>'
        + '<div class="foot"><span class="grow">' + (base.state === 'running' ? '生成中' : '排队中') + '</span>'
        + '<button class="btn sm" ' + attr(o, 'qcancel', base.id) + '>取消</button></div></div>';
    }
    if (base.state === 'canceled') {
      return '<div class="job" id="' + E(j.id) + '"><div class="head"><span class="dot"></span><b>已取消</b>'
        + '<span class="wf">· ' + E(jobWfLabel(j)) + '</span></div>'
        + '<div class="body">' + promptBox + '</div></div>';
    }
    if (base.state === 'failed') {
      var e = base.err || {};
      return '<div class="job fail" id="' + E(j.id) + '">'
        + '<div class="head"><span class="dot r"></span><b>失败</b><span class="wf">· ' + E(jobWfLabel(j)) + '</span>'
        + '<span class="gradechip">' + E(base.grade || '默认') + '</span><span style="flex:1"></span>'
        + '<span class="tiny muted">' + E(e.retriable ? '可以再试一次' : '') + '</span></div>'
        + '<div class="body" style="padding-bottom:0">' + promptBox + '</div>'
        + '<div class="errbox" id="err-' + E(j.id) + '"><div>' + E(e.msg || '') + '</div><div class="raw">' + E(e.raw || '') + '</div>'
        + (base.archive_failed ? '<div style="color:#8a5a00">归档失败：' + E(base.archive_failed.reason) + '</div>' : '') + '</div>'
        + '<div class="foot"><span class="grow"></span>'
        + '<button class="btn sm" ' + attr(o, 'raw', j.id) + '>看原始报错</button>'
        + '<button class="btn sm primary" ' + attr(o, 'retry', j.id) + '>重试一次</button></div></div>';
    }
    var ds = doneRuns(j), ps = pendRuns(j), fs = runs.filter(function (r) { return r.state === 'failed'; });
    var n = ds.length + ps.length, cols = n <= 1 ? 1 : n === 2 ? 2 : 3;
    var grades = [];
    runs.filter(function (r) { return r.state !== 'canceled'; }).forEach(function (r) {
      var g = r.grade || '默认'; if (grades.indexOf(g) < 0) grades.push(g);
    });
    var running = ps.filter(function (r) { return r.state === 'running'; })[0];
    var lastT = j.t0 || 0;
    runs.filter(function (r) { return r.t1; }).forEach(function (r) { if (r.t1 > lastT) lastT = r.t1; });
    return '<div class="job done" id="' + E(j.id) + '">'
      + '<div class="head"><span class="dot g"></span><b>已完成</b><span class="wf">· ' + E(jobWfLabel(j)) + '</span>'
      + '<span class="gradechip">' + E(grades.join(' / ')) + '</span><span style="flex:1"></span>'
      + (ps.length ? '<span class="qchip"><span class="dot a pulse"></span>队列 ' + ps.length + ' 个'
        + (running ? ' · 正在出成品 ' + (ds.length + 1) : '') + '</span>' : '')
      + (o.time === false ? '' : '<span class="tiny muted">用时 ' + Math.round((lastT - (j.t0 || lastT)) / 1000) + ' 秒</span>')
      + '</div>'
      + '<div class="body">' + promptBox
      + '<div class="grid tiles" style="grid-template-columns:repeat(' + cols + ',minmax(0,1fr));max-width:' + (o.full ? '100%' : cols * 250 + (cols - 1) * 9 + 'px') + '">'
      + ds.map(function (r, i) {
        if ((r.media && r.media !== 'image') || ((r.files || [])[0] || {}).kind === 'video'
          || ((r.files || [])[0] || {}).kind === 'audio') return mediaTileHTML(j, r, i, o);
        var badge = (r.grade && r.grade !== base.grade) ? '<span class="badge">' + E(r.grade) + '</span>' : '';
        return '<div class="tile"><img src="' + imgUrl(r.id, 0) + '" alt="成品 ' + (i + 1) + '" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain">' + badge
          + '<div class="acts"><button title="看大图" ' + attr(o, 'zoom', j.id + '|' + r.id) + '>⤢</button>'
          + '<button title="下载" ' + attr(o, 'dl', r.id) + '>⬇</button>'
          + '<button title="复制图片（可直接粘贴到输入框当参考图）" ' + attr(o, 'copy', r.id) + '>⧉</button></div>'
          + '<div class="tile-size">' + E(runSizeText(j, r)) + '</div></div>';
      }).join('')
      + ps.map(function (r, k) {
        return '<div class="tile q" data-runprog="' + E(r.id) + '">'
          + '<button class="qx" title="撤掉这一档" ' + attr(o, 'qcancel', r.id) + '>✕</button>'
          + '<div class="qwrap"><div class="qlb">成品 ' + (ds.length + k + 1) + '</div>'
          + '<div class="progress"><i data-runbar="' + E(r.id) + '" style="width:' + Math.round(r.pct || 0) + '%"></i></div>'
          + '<div class="tiny muted" data-runstep="' + E(r.id) + '">' + (r.state === 'running' ? '正在采样…' : '排队中 · 前面还有 ' + queueAhead(r) + ' 个') + '</div>'
          + '<div class="qpct" data-runpct="' + E(r.id) + '">' + Math.round(r.pct || 0) + '%</div></div></div>';
      }).join('')
      + '</div>'
      + (fs.length ? '<div class="jobfail">' + fs.map(function (r) {
        return '<div>有一条队列任务没跑成（' + E(r.grade || '默认') + '）：' + E((r.err || {}).msg || '未知错误') + '</div>';
      }).join('') + '</div>' : '')
      + (ds.some(function (r) { return r.archive_failed; })
        ? '<div class="tiny" style="color:#8a5a00;margin-top:6px">归档失败，原路径：' + E(ds.filter(function (r) { return r.archive_failed; })[0].archive_failed.original) + '</div>' : '')
      + '</div>'
      + '<div class="foot"><span class="grow seedline"><i class="sdot"></i>种子 <b class="mono">' + E(j.seed == null ? (base.seed == null ? '' : base.seed) : j.seed) + '</b></span>'
      + '<button class="btn sm qbtn" ' + attr(o, 'queue', j.id) + ' title="挑一档排到这批后面，再出一张">🕒 加入队列生成</button>'
      + (o.again === false ? '' : '<button class="btn sm" ' + attr(o, 'again', j.id) + '>↻ 再来一张</button>')
      + (o.refine === false ? '' : '<button class="btn sm ghost" ' + attr(o, 'refine', j.id) + ' title="把上一条的描述和档位带过来">↗ 基于上一条</button>')
      + '</div></div>';
  }

  /* ------------------------------------------------------------ 素材卡
     和 jobCard 的「已完成」态同一套骨架：.job > .head + .body > .grid.tiles > .tile。
     区别只有：头里写「素材」而不是工作流、没有提示词块 / 种子 / 队列 / 再来一张，
     媒体框上是同一组 ⤢ ⬇ ⧉ 和右下角尺寸角标（尺寸由调用方量出来回填 [data-asize]）。 */
  function assetCard(o) {
    o = o || {};
    var kind = o.kind || 'image', src = o.src || '';
    var label = kind === 'audio' ? '音频素材' : kind === 'video' ? '视频素材' : '图片素材';
    var sizeId = E(o.id || '');
    var tile;
    var dl = '<button title="下载" ' + attr(o, 'dlref', o.id) + '>⬇</button>';
    if (kind === 'audio') {
      tile = '<div class="tile audio-tile">'
        + '<div class="atile-hd"><span>🎵</span><span class="aname">' + E(o.name || '音频') + '</span></div>'
        + '<audio controls preload="metadata" src="' + E(src) + '"></audio>'
        + '<div class="atile-ft"><span class="tiny muted" data-asize="' + sizeId + '"></span><span style="flex:1"></span>'
        + dl + '</div></div>';
    } else if (kind === 'video') {
      tile = '<div class="tile vid">'
        + '<video src="' + E(src) + '" controls playsinline preload="metadata" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#0b0f16"></video>'
        + '<div class="vtop"><button title="放大播放" ' + attr(o, 'zoomMat', o.id) + '>⤢</button>' + dl + '</div>'
        + '<div class="tile-size" data-asize="' + sizeId + '"></div></div>';
    } else {
      tile = '<div class="tile"><img src="' + E(src) + '" alt="素材图" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain">'
        + '<div class="acts"><button title="看大图" ' + attr(o, 'zoomMat', o.id) + '>⤢</button>' + dl
        + '<button title="复制图片（可直接粘贴到输入框当参考图）" ' + attr(o, 'copyref', o.id) + '>⧉</button></div>'
        + '<div class="tile-size" data-asize="' + sizeId + '"></div></div>';
    }
    return '<div class="job asset-job">'
      + '<div class="head"><span class="dot g"></span><b>已完成</b><span class="wf">· ' + label + '</span>'
      + '<span style="flex:1"></span></div>'
      + '<div class="body"><div class="grid tiles" style="grid-template-columns:minmax(0,1fr);max-width:100%">'
      + tile + '</div></div></div>';
  }
  /* 空结果占位。o 可以是字符串（附加 class）或 { cls, err }；画布里用 .ph-tile 压成矮条，别占半屏 */
  function emptyTile(text, o) {
    if (typeof o === 'string') o = { cls: o };
    o = o || {};
    return '<div class="tile' + (o.cls ? ' ' + o.cls : '') + '"><div class="ph"'
      + (o.err ? ' style="color:#c0392b;background:#fdecea"' : '') + '>' + E(text) + '</div></div>';
  }

  return {
    editorHTML: editorHTML, editorText: editorText, refThumbs: refThumbs,
    imageInput: imageInput, videoInput: videoInput, genInput: genInput,
    barHTML: barHTML, vRefSlot: vRefSlot,
    jobCard: jobCard, assetCard: assetCard, emptyTile: emptyTile,
    acts: ACT,
  };
})();
