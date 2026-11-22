/* ================= 视频生成画布（1.04 重做） =================
   五类节点：图片 / 视频 / 音频（素材）、图片生成 / 视频生成（生成）。
   生成仍然走 /api/canvas/tasks → TaskService.submit_normal，
   所以忙闲判定、右栏任务栏、历史、归档和创作台是同一套规则，这里不另造。

   依赖 app.js 的全局：api / S / UI / esc / toast / toastBlocked / pull /
   refreshDrawer / openZoom / imgUrl / refUrl / queueAhead。
*/
(function () {
  'use strict';

  var LIM = { minK: 0.3, maxK: 2.2 };
  var W = 300;                       // 素材节点默认宽度（CSS 里也是 300）
  var GEN_W = 420;                   // 生成节点宽度：创作台输入区的实际版心，DOM 才能原样搬进来
  var CS = {
    host: null, stage: null, world: null, svg: null,
    pid: '', project: null, projects: [],
    view: { x: 60, y: 60, k: 1 },
    funcs: [], kinds: [], sel: null,
    drag: null, link: null, menu: null, scissor: null, menuAt: null,
    projMenu: null,
    saveTimer: 0, dirty: false, saving: false, mounted: false, tipTimer: 0,
    hasView: false,
  };
  var $q = function (s, r) { return (r || document).querySelector(s); };
  var $a = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var newId = function () { return 'n' + Date.now().toString(36).slice(-5) + Math.random().toString(36).slice(2, 5); };
  var clamp = function (v, a, b) { return v < a ? a : (v > b ? b : v); };
  var LETTERS = 'ABCDEFGH';

  /* ------------------------------------------------------------ 查表 */
  function funcOf(id) { return (CS.funcs || []).filter(function (f) { return f.id === id; })[0] || null; }
  function kindOf(n) { return (n && n.kind) || 'image'; }
  /* 生成节点 = 一个子功能；素材节点 = 图片 / 视频 / 音频。清单由后端 nodeKinds 给，
     所以以后加子功能只要后端那张表里加一行，这里不用改。 */
  function isGen(n) {
    var k = kindOf(n), m = kindMeta(k);
    return m.gen === undefined ? (k === 'imagegen' || k === 'videogen') : !!m.gen;
  }
  function kindMeta(k) { return (CS.kinds || []).filter(function (x) { return x.id === k; })[0] || {}; }
  function funcFor(kind) { return funcOf(kind) || {}; }
  /* 每种子功能节点能接什么素材：
     none = 不收参考（文生图 / 文生视频 / 音乐生成 / 语音生成）
     image / video / audio = 只收这一类；any = 图片 / 视频 / 音频都收（参考生视频） */
  var REF_KIND = {
    t2i: 'none', imagegen: 'image',
    sheetchar: 'none', sheetchar_lock: 'image', sheetchar_style: 'image',
    sheetscene: 'none', sheetscene_i2i: 'image', sheetscene_expand: 'image',
    sheetprop: 'none', sheetprop_i2i: 'image', board: 'image',
    t2v: 'none', fl2v: 'image', videogen: 'any', board2v: 'image',
    music: 'none', refmusic: 'audio', tts: 'none', clone: 'audio',
  };
  /* 多连接点节点：一个上传位一条线，且每个位只能接一个图片节点。
     槽位名和创作台提交的 refSlots / 视频参考顺序一一对应。 */
  var PORTS = {
    sheetscene_i2i: [{ slot: 'ref', label: '参考图' }, { slot: 'pano', label: '全景图' },
      { slot: 'bird', label: '鸟瞰图' }, { slot: 'sketch', label: '草图' }],
    sheetscene_expand: [{ slot: 'ref', label: '参考图' }, { slot: 'pano', label: '全景图' },
      { slot: 'bird', label: '鸟瞰图' }, { slot: 'sketch', label: '草图' }],
    fl2v: [{ slot: 'first', label: '首帧' }, { slot: 'last', label: '尾帧' }],
  };
  function portsOf(kind) { return PORTS[kind] || [{ slot: '', label: '素材' }]; }
  function isMultiPort(kind) { return portsOf(kind).length > 1; }
  function slotIndexOf(kind, slot) {
    var ps = portsOf(kind), i = -1;
    ps.forEach(function (p, idx) { if (p.slot === (slot || '')) i = idx; });
    return i < 0 ? ps.length : i;
  }
  /* 子功能固定模式：图生图 / 扩图拆成独立节点后，节点本身不再切模式 */
  function fixedMode(kind) {
    var f = funcFor(kind);
    return (f && f.mode) || '';
  }
  function nodeById(id) { return ((CS.project && CS.project.nodes) || []).filter(function (n) { return n.id === id; })[0] || null; }
  function jobOf(id) { return ((S && S.jobs) || []).filter(function (j) { return j.id === id; })[0] || null; }
  function runsOf(n) { var j = jobOf(n.jobId); return (j && j.runs) || []; }
  function doneRunsOf(n) {
    return runsOf(n).filter(function (r) {
      return r.state === 'done' && !r.hidden && (r.files || []).some(function (f) { return !f.deleted; });
    });
  }
  function bestRun(n) { var d = doneRunsOf(n); return d.length ? d[d.length - 1] : null; }
  /* 左侧接进来的节点；多连接点节点按端口顺序（参考图 → 全景图 → 鸟瞰图 → 草图 /
     首帧 → 尾帧）排，后端拿到的 refs 顺序和槽位就自然对齐了。 */
  function upstreamOf(n) {
    var es = (CS.project && CS.project.edges) || [];
    var kind = kindOf(n), multi = isMultiPort(kind);
    var mine = es.map(function (e, i) { return { e: e, i: i }; })
      .filter(function (x) { return x.e.to === n.id; });
    if (multi) {
      mine.sort(function (a, b) {
        return slotIndexOf(kind, a.e.slot) - slotIndexOf(kind, b.e.slot) || a.i - b.i;
      });
    }
    return mine.map(function (x) { return nodeById(x.e.from); }).filter(Boolean);
  }
  /* 上游 → 连线（含槽位），给场景 4 槽位 / 首尾帧 2 槽位按位取值 */
  function edgesInto(n) {
    var es = (CS.project && CS.project.edges) || [];
    var kind = kindOf(n);
    return es.filter(function (e) { return e.to === n.id; })
      .sort(function (a, b) { return slotIndexOf(kind, a.slot) - slotIndexOf(kind, b.slot); });
  }
  /* 素材的编号：图 / 视频 / 音频各自从 1 开始，和提示词里的 [图 1] 对应 */
  function refIndexOf(g, nd) {
    var list = upstreamOf(g).filter(function (u) { return kindMeta(kindOf(u)).media === kindMeta(kindOf(nd)).media; });
    return list.findIndex(function (u) { return u.id === nd.id; }) + 1;
  }
  function tokenOf(g, nd) {
    var media = kindMeta(kindOf(nd)).media;
    var label = media === 'video' ? '视频' : media === 'audio' ? '音频' : '图';
    return '[' + label + ' ' + Math.max(1, refIndexOf(g, nd)) + ']';
  }
  /* 按连线顺序把参考位摆成一个数组：资产节点 → { ref }，上游生成节点 → { run }。
     后端的 refs 顺序就是提示词里 [图 N] / [视频 N] 的编号，
     所以 ref 和 run 必须混排在一起传，分成两段拼会在中间夹资产节点时错位。 */
  function inputsOf(g) {
    return upstreamOf(g).map(inputItemOf).filter(Boolean);
  }
  function inputItemOf(u) {
    if (!u) return null;
    if (u.refId) return { ref: u.refId };
    var r = bestRun(u);
    if (!r) return null;
    var f = (r.files || [])[0];
    if (!f || f.deleted) return null;
    return { run: r.id, i: 0 };
  }
  /* 多槽位节点：inputs 顺序 = 槽位顺序，refSlots 记「第几个输入」交给后端换成 ref id */
  function inputsWithSlots(g) {
    var inputs = [], slots = {};
    edgesInto(g).forEach(function (e) {
      var item = inputItemOf(nodeById(e.from));
      if (!item) return;
      inputs.push(item);
      if (e.slot) slots[e.slot] = '#' + inputs.length;
      else if (!slots.ref) slots.ref = '#' + inputs.length;
    });
    return { inputs: inputs, slots: slots };
  }
  /* 芯片缩略图：上传素材用 refId，生成节点用它的成品图 */
  function chipSrc(u) {
    if (u.refId) return refUrl(u.refId);
    var r = bestRun(u);
    return r ? imgUrl(r.id, 0) : '';
  }
  function canConnect(from, to, slot) {
    if (!from || !to || from.id === to.id) return false;
    if (!isGen(to)) return false;                       // 只有生成节点有左侧输入
    var allow = REF_KIND[kindOf(to)] || 'any';
    if (allow === 'none') return false;                 // 文生图 / 文生视频 不收素材
    var media = kindMeta(kindOf(from)).media;
    if (allow === 'any') { if (!media) return false; }
    else if (media !== allow) return false;
    // 需求1：多连接点节点，每个上传位只能被一个图片节点占用
    var kind = kindOf(to);
    if (isMultiPort(kind) && slot) {
      if (slotIndexOf(kind, slot) >= portsOf(kind).length) return false;
      var edges = (CS.project && CS.project.edges) || [];
      if (edges.some(function (e) { return e.to === to.id && e.slot === slot; })) return false;
    }
    return true;
  }

  /* ------------------------------------------------------------ 模板 */
  function shellHTML() {
    return '' +
      '<div class="cvs-bar">' +
        '<button class="cvs-projbtn" id="cvsProjBtn" title="切换 / 管理画布"><span id="cvsProjLabel"></span><span class="caret">▾</span></button>' +
        '<button class="btn sm ghost cvs-newproj" id="cvsNewProj" title="新建画布">＋ <span class="lbl">新建画布</span></button>' +
        '<span class="cvs-count" id="cvsCount"></span>' +
        '<span class="sep"></span>' +
        '<button class="btn sm primary" id="cvsAddNode">＋ <span class="lbl">创建节点</span></button>' +
        '<button class="btn sm ghost" id="cvsFit" title="适应视图">⤢ <span class="lbl">适应</span></button>' +
        '<span class="grow"></span>' +
        '<span class="cvs-save" id="cvsSave">已保存</span>' +
      '</div>' +
      '<div class="cvs-stage" id="cvsStage">' +
        '<div class="cvs-grid" id="cvsGrid"></div>' +
        '<div class="cvs-world" id="cvsWorld"><svg class="cvs-links" id="cvsLinks"></svg></div>' +
        '<div class="cvs-empty" id="cvsEmpty">' +
          '<div class="em">🎬</div>' +
          '<div class="t">从「创建节点」开始</div>' +
          '<div class="d">点右上角「创建节点」，选图片 / 视频 / 音频素材节点，或图片生成 / 视频生成节点；' +
          '建好素材节点后点节点里的「点击上传」放素材。把素材右侧的圆点拖到生成节点左侧，就能在提示词里 @ 引用它。</div>' +
          '<div class="d"><button class="btn sm primary" id="cvsEmptyAdd">＋ 创建节点</button></div>' +
        '</div>' +
        '<div class="cvs-zoom">' +
          '<button id="cvsZo" title="缩小">−</button>' +
          '<span class="pct" id="cvsPct">100%</span>' +
          '<button id="cvsZi" title="放大">＋</button>' +
        '</div>' +
        '<div class="cvs-tip" id="cvsTip"></div>' +
      '</div>' +
      '<input type="file" id="cvsFile" hidden multiple>';
  }

  /* ---------- 图片生成节点 = 图片生成页「图生图」搬进画布 ----------
     结果块 / 输入区的版式与创作台一致：提示词预览、种子、队列、再来一张、基于上一条；
     输入区有模式切换、画法、档位、风格、看完整提示词与上传入口。 */
  function imgMode(n) { return n.mode === 't2i' ? 't2i' : 'i2i'; }
  /* 后端节点白名单还没加这些字段，先借 note 字段把图生图的模式/画法/风格一起存盘；
     等服务端放开白名单后，这段仍兼容直接写在节点上的同名字段。 */
  var EXTRA_KEY = '__cbgen_imggen__';
  function loadImgExtras(n) {
    if (!n.note || n.note.indexOf(EXTRA_KEY) !== 0) return;
    try {
      var o = JSON.parse(n.note.slice(EXTRA_KEY.length));
      ['mode', 'ink', 'lineStyle', 'style', 'styleCustom', 'styleText', 'hideResult'].forEach(function (k) {
        if (o[k] !== undefined) n[k] = o[k];
      });
      if (o.text) n.note = o.text; else delete n.note;
    } catch (e) { }
  }
  function saveImgExtras(n) {
    if (kindOf(n) !== 'imagegen') return;
    var o = {
      mode: imgMode(n), ink: n.ink || 'color', lineStyle: n.lineStyle || '',
      style: n.style || '', styleCustom: n.styleCustom !== false, styleText: n.styleText || '',
      hideResult: !!n.hideResult,
    };
    if (n.note) o.text = n.note;
    n.note = EXTRA_KEY + JSON.stringify(o);
  }
  function imageStyleLib() { return (typeof S !== 'undefined' && S.imageStyleLib) || { styles: [], line_styles: [] }; }
  function imageStyleBits(n) {
    var lib = imageStyleLib();
    var mode = imgMode(n), ink = n.ink || 'color', bits = [];
    if (ink === 'line') {
      var ls = (lib.line_styles || []).filter(function (x) { return x.id === n.lineStyle; })[0];
      bits.push(ls ? ls.text : '纯黑白线稿，只有黑色线条与纯白底色，不上色、不做灰调');
    } else if (mode === 'i2i' || n.styleCustom !== false) {
      if (String(n.styleText || '').trim()) bits.push(String(n.styleText).trim());
    } else {
      var st = (lib.styles || []).filter(function (x) { return x.name === n.style; })[0];
      if (st && st.text) bits.push(st.text);
    }
    return bits;
  }
  function composedImagePrompt(n) {
    var bits = imageStyleBits(n), base = String(n.prompt || '');
    return bits.length ? '画面风格：' + bits.join('；') + '。\n' + base : base;
  }
  /* ------------------------------------------------------------ 生成节点
     节点 = 对应子功能页面的「结果块 + 输入块」，两块都直接调 web/genui.js：
     创作台的图片生成 / 视频生成调的是同一份函数、同一套 class，所以画布里的节点
     和功能页面长得完全一样，样式也只维护 index.html 那一套。
     画布这边只是把「上传 / 设定图模板 / 看完整提示词 / 基于上一条 / 提示词预览块」关掉，
     参考图靠连线接进来，所以生成节点没有上传按钮。 */
  var ACTS = {
    ink: 'data-cink', line: 'data-cline', grade: 'data-cgrade',
    styleSel: 'data-cstylesel', styleText: 'data-cstyle',
    gen: 'data-gen', refAt: 'data-at', refRm: 'data-crmref',
    vat: 'data-at', vrm: 'data-crmref', vup: 'data-cvup',
    zoom: 'data-czoom', dl: 'data-cdl', copy: 'data-ccopy',
    again: 'data-cagain', queue: 'data-cqueue', qcancel: 'data-cqcancel',
    retry: 'data-cretry', raw: 'data-craw', seeFull: 'data-cseefull',
    zoomMat: 'data-zoommat', duration: 'data-cdur',
    dlref: 'data-dlref', copyref: 'data-copyref',
    // 1.06 子功能节点的通用参数条（版式 / 画法 / 参考方式 / 禁角色 / 宫格 / 风格…）
    cfgseg: 'data-cseg', cfgsel: 'data-csel', cfgtext: 'data-ctext', cfgextra: 'data-cextra',
  };
  /* 连线接进来的素材：id 用上游节点 id，缩略图上 @ / ✕ 直接按节点找回去 */
  function nodeRefs(n) {
    return upstreamOf(n).map(function (u) {
      var media = kindMeta(kindOf(u)).media || 'image';
      var d = doneRunsOf(u);
      // 上游是生成节点时，参考永远取它「最后一张成品」（含「加入队列生成」新出的），
      // total / idx 用来在缩略图上标出用的是第几张，省得不知道新旧。
      return {
        id: u.id, nodeId: u.id, kind: media, src: chipSrc(u), name: u.name || '',
        total: d.length, runIdx: d.length,
      };
    }).filter(function (r) { return !!r.src; });
  }
  function imageInputFor(n) {
    var func = funcFor('imagegen'), lib = imageStyleLib();
    var refs = nodeRefs(n);
    return GenUI.imageInput({
      refs: refs, i2i: true, target: n.id, acts: ACTS,
      prompt: n.prompt || '',
      ink: n.ink || 'color', lineStyle: n.lineStyle || '',
      styles: (lib.styles || []).map(function (x) { return x.name; }),
      lineStyles: lib.line_styles || [],
      style: n.style || '', styleCustom: true, styleText: n.styleText || '',
      grades: (func && func.grades) || [], grade: n.grade || '',
      placeholder: '描述画面；接了素材可以用 @ 引用',
      // 和创作台图片生成的图生图一致：隐藏「风格」行（模板入口也不放），
      // 只留画法/线稿/档位。参考图靠连线接入，所以上传也关掉。
      show: { upload: false, template: false, style: false, seeFull: false, hints: false, kbd: false, emptyHint: false },
      editorAttrs: 'data-prompt="1"',
    });
  }
  function videoInputFor(n) {
    var func = funcFor('videogen');
    var groups = { image: [], video: [], audio: [] };
    upstreamOf(n).forEach(function (u) {
      var m = kindMeta(kindOf(u)).media || 'image';
      if (!chipSrc(u)) return;   // 上游还没成品（比如刚建的空生成节点）：不占参考位，免得出现空图
      (groups[m] || groups.image).push(u);
    });
    var slots = '', refs = [];
    ['image', 'video', 'audio'].forEach(function (k) {
      groups[k].forEach(function (u, i) {
        var d = doneRunsOf(u);
        refs.push({ id: u.id, kind: k, src: chipSrc(u), name: u.name || '' });
        slots += GenUI.vRefSlot({
          label: (k === 'image' ? '图' : k === 'video' ? '视频' : '音频') + (i + 1),
          kind: k, ref: { id: u.id, src: chipSrc(u) }, idx: i + 1, upload: false, acts: ACTS,
          total: d.length, runIdx: d.length,
        });
      });
    });
    if (!slots) {
      slots = ['image', 'video', 'audio'].map(function (k) {
        return GenUI.vRefSlot({ label: k === 'image' ? '图' : k === 'video' ? '视频' : '音频', kind: k, ref: null, upload: false });
      }).join('');
    }
    return GenUI.videoInput({
      acts: ACTS, target: n.id,
      grades: (func && func.grades) || [], grade: n.grade || '',
      duration: (n.video && n.video.duration) || 5.2,
      slots: '<div class="vslots">' + slots + '</div>',
      refs: refs,
      prompt: n.prompt || '',
      placeholder: n.video && n.video.mode === 't2v' ? '描述镜头：谁、在哪、做什么、怎么运镜'
        : '描述这一镜的画面；用 @ 引用左侧素材',
      show: { hints: false, kbd: false, emptyHint: false },
      editorAttrs: 'data-prompt="1"',
    });
  }

  /* ------------------------------------------------------------ 子功能节点的输入块
     每种生成节点 = 创作台里的一个子功能，输入块照它那一页的控件排：
       · 文生图 / 图生图：画法 → 参考 → 描述 → 档位 → 风格
       · 角色 / 场景 / 道具设定图：版式 → 画法（→ 参考方式 / 禁角色）→ 描述 → 档位 → 风格
       · 宫格分镜故事板：版式 → 画法 → 参考 → 逐格描述 → 档位
       · 视频四模式：档位 → 时长（→ 宫格）→ 参考槽 → 描述
       · 音频四功能：参考槽 → 档位 → 描述 → 歌词
     控件仍是创作台那套 .sheetbar / .segb / .segsel（GenUI.genInput 产出），
     动作名换成画布自己的 data-c*，所以样式和功能页面完全同步。 */
  var CCTX = { acts: ACTS };
  function cfgOf(n) { if (!n.cfg || typeof n.cfg !== 'object') n.cfg = {}; return n.cfg; }
  function cfv(n, k, d) { var c = n.cfg || {}; return c[k] === undefined || c[k] === '' ? d : c[k]; }
  function cbar(spec) { return GenUI.barHTML(CCTX, spec); }
  function cseg(key, value, options, label, hint) {
    return cbar({ label: label, key: key, type: 'seg', value: value, options: options, hint: hint });
  }
  function csel(key, value, options, label, hint, width) {
    return cbar({ label: label, key: key, type: 'sel', value: value, options: options, hint: hint, width: width });
  }

  /* 档位：一条 .sheetbar，点击走 data-cgrade（和结果卡上的档位胶囊同一条道） */
  function gradeBar(n, grades, hint) {
    grades = grades || [];
    if (!grades.length) return '';
    var cur = grades.some(function (g) { return g.name === n.grade; }) ? n.grade : grades[0].name;
    var g0 = grades.filter(function (g) { return g.name === cur; })[0] || grades[0];
    return cbar({
      label: '档位', key: 'grade', act: 'grade', type: 'seg', value: cur,
      options: grades.map(function (g) {
        var d = g.note || [g.ratio, g.res, g.steps ? g.steps + ' 步' : '',
          g.duration ? g.duration + ' 秒' : ''].filter(Boolean).join(' · ');
        return { v: g.name, label: g.name, title: d };
      }),
      hint: hint === undefined ? (g0.note || '') : hint,
    });
  }
  /* 档位来源：wf 类（文生图/图生图）后端已经算好；combo 类（设定图/场景/道具/故事板）
     按「版式 × 画法 × 参考」现查组合，和功能页面同一份档位表。 */
  function gradesFor(n) {
    var kind = kindOf(n), f = funcFor(kind);
    if (f.gradesFrom !== 'combo') return f.grades || [];
    var c = cfgOf(n);
    // 角色设定图三项子功能各自固定参考方式：文生图 none / 锁定同角色 lock / 只借画风 style
    if (kind === 'sheetchar' || kind === 'sheetchar_lock' || kind === 'sheetchar_style') {
      var ref = kind === 'sheetchar_lock' ? 'lock' : kind === 'sheetchar_style' ? 'style' : 'none';
      var sc = (typeof sheetCombos === 'function' ? sheetCombos() : []).filter(function (x) {
        return x.layout === c.layout && x.ink === (c.ink || 'color') && x.ref === ref;
      })[0];
      return (sc && sc.grades) || [];
    }
    if (kind === 'board') {
      var bc = (typeof boardCombos === 'function' ? boardCombos() : []).filter(function (x) {
        return x.layout === c.layout && x.ink === (c.ink || 'color');
      })[0];
      return (bc && bc.grades) || [];
    }
    // 场景 / 道具：三种方式拆成独立节点，方式由节点类型固定
    var scene = kind === 'sheetscene' || kind === 'sheetscene_i2i' || kind === 'sheetscene_expand';
    var k = scene ? 'scene' : 'prop';
    var mode = fixedMode(kind) || c.mode || 't2i';
    var lay = c.layout || '';
    var cid = scene
      ? (mode === 'expand' ? (lay + '.expand.-.-')
        : (lay + '.' + mode + '.' + (c.ink || 'color') + '.' + (c.nochar || 'yes')))
      : (lay + '.' + mode + '.' + (c.ink || 'color') + '.'
        + (mode === 'i2i' ? (c.ref || 'lock') : (c.ref || 'none')));
    var all = (typeof extCombos === 'function' ? (extCombos(k) || []) : []);
    var ce = all.filter(function (x) { return x.id === cid; })[0];
    return (ce && ce.grades) || [];
  }
  /* 换版式 / 画法 / 模式后旧档位可能不存在了，回到新组合的第一档 */
  function fixGrade(n) {
    var gs = gradesFor(n);
    if (!gs.length) { n.grade = ''; return; }
    if (!gs.some(function (g) { return g.name === n.grade; })) n.grade = gs[0].name;
  }
  function currentGrade(n) {
    var gs = gradesFor(n);
    return gs.filter(function (g) { return g.name === n.grade; })[0] || gs[0] || {};
  }

  function inkBar(n, lib) {
    lib = lib || {};
    var c = cfgOf(n);
    var ink = c.ink || 'color', ls = lib.line_styles || [];
    var extra = (ink === 'line' && ls.length)
      ? '<select class="input segsel" data-csel="lineStyle">'
      + ls.map(function (l) {
        return '<option value="' + esc(l.id) + '"' + (c.lineStyle === l.id ? ' selected' : '') + '>'
          + esc(l.name) + '</option>';
      }).join('') + '</select>'
      : '';
    return cbar({
      label: '画法', key: 'ink', type: 'seg', value: ink, extra: extra,
      options: [{ v: 'color', label: '彩色' }, { v: 'line', label: '线稿' }],
      hint: ink === 'line' ? '线稿别写颜色词' : '',
    });
  }
  function styleBars(n, lib) {
    lib = lib || {};
    var c = cfgOf(n);
    var styles = (lib.styles || []).map(function (s) { return s.name; });
    var custom = c.styleCustom !== false;
    var cur = custom ? '__custom' : (c.style || styles[0] || '');
    var opts = styles.map(function (s) { return { v: s, label: s }; })
      .concat([{ v: '__custom', label: '自定义…' }]);
    var extra = custom
      ? '<input class="input segtext" data-ctext="styleText" value="' + esc(c.styleText || '')
      + '" placeholder="画种 + 材质笔触 + 色调 + 光线，一两句">'
      : '';
    return cbar({
      label: '风格', key: 'style', type: 'sel', value: cur, options: opts, extra: extra, width: 148,
      hint: custom ? '' : (c.ink === 'line' ? '线稿：画风只借造型' : ''),
    });
  }
  function layoutBar(n, layouts, label, hint) {
    layouts = layouts || [];
    if (!layouts.length) return '';
    var c = cfgOf(n);
    if (!c.layout || !layouts.some(function (l) { return l.id === c.layout; })) c.layout = layouts[0].id;
    return cseg('layout', c.layout, layouts.map(function (l) {
      return { v: l.id, label: l.name, sub: l.ratio || '', title: l.summary || '' };
    }), label || '版式', hint);
  }
  /* 连线接进来的素材槽（和参考生视频那套 .vslot 一致，只是按子功能限制类型）
     labels 给固定槽位名（首尾帧的「首帧 / 尾帧」），没接满就补空槽提示 */
  function slotsFor(n, allow, labels) {
    var groups = { image: [], video: [], audio: [] };
    upstreamOf(n).forEach(function (u) {
      var m = kindMeta(kindOf(u)).media || 'image';
      if (allow && allow !== 'any' && m !== allow) return;
      if (!chipSrc(u)) return;                 // 上游还没有成品：不占位
      (groups[m] || groups.image).push(u);
    });
    var html = '', refs = [];
    ['image', 'video', 'audio'].forEach(function (k) {
      groups[k].forEach(function (u, i) {
        var d = doneRunsOf(u);
        refs.push({ id: u.id, kind: k, src: chipSrc(u), name: u.name || '' });
        html += GenUI.vRefSlot({
          label: (k === 'image' ? '图' : k === 'video' ? '视频' : '音频') + (i + 1),
          kind: k, ref: { id: u.id, src: chipSrc(u) }, idx: i + 1, upload: false,
          acts: ACTS, total: d.length, runIdx: d.length,
        });
      });
    });
    if (labels && labels.length) {
      labels.forEach(function (lb, i) {
        if (i < groups.image.length) return;
        html += GenUI.vRefSlot({ label: lb, kind: 'image', ref: null, upload: false });
      });
    } else if (!html) {
      html = GenUI.vRefSlot({
        label: allow === 'audio' ? '参考音频' : allow === 'video' ? '参考视频' : '图',
        kind: allow === 'audio' ? 'audio' : allow === 'video' ? 'video' : 'image',
        ref: null, upload: false,
      });
    }
    return { html: '<div class="vslots">' + html + '</div>', refs: refs };
  }
  /* 图片类子功能（设定图 / 故事板）用创作台那种缩略图行：带 @ 引用与 ✕ 断开 */
  function refsRow(n) {
    var refs = nodeRefs(n);
    var inner = GenUI.refThumbs({ refs: refs, acts: ACTS });
    if (!refs.length) {
      inner = '<span class="tiny muted cref-empty">左侧连线接素材就能当参考（也可以不接）</span>';
    }
    return { refs: refs, html: '<div class="refs" id="refsRow">' + inner + '</div>' };
  }
  function durationBar(n) {
    var d = +((n.video || {}).duration) || 5.2;
    d = Math.min(15, Math.max(4, d));
    return '<div class="sheetbar vdurbar"><span class="slb">时长</span>'
      + '<input type="number" class="input vdur-num" data-cdur="num" min="4" max="15" step="0.01" value="'
      + d.toFixed(2) + '" aria-label="视频时长（秒）">'
      + '<span class="tiny muted vdur-unit">秒</span>'
      + '<input type="range" class="vdur-range" data-cdur="range" min="4" max="15" step="0.1" value="' + d + '">'
      + '</div>';
  }
  function lyricsBox(n) {
    return '<textarea class="vlyrics" data-cextra="lyrics" rows="2"'
      + ' placeholder="歌词（可留空，留空就按纯音乐生成）">' + esc(cfv(n, 'lyrics', '')) + '</textarea>';
  }

  var GEN_INPUT_OPTS = {
    show: { hints: false, kbd: false, emptyHint: false },
    editorAttrs: 'data-prompt="1"',
  };
  function withBase(o, n) {
    var out = {};
    Object.keys(GEN_INPUT_OPTS).forEach(function (k) { out[k] = GEN_INPUT_OPTS[k]; });
    Object.keys(o).forEach(function (k) { out[k] = o[k]; });
    // 发送按钮 = data-gen="节点 id"：没有它点了不会生成（画布靠 id 找回节点）
    out.target = n.id;
    return out;
  }

  function t2iInput(n) {
    var lib = imageStyleLib();
    return GenUI.genInput(withBase({
      before: inkBar(n, lib),
      after: gradeBar(n, gradesFor(n)) + styleBars(n, lib),
      prompt: n.prompt || '', refs: [],
      placeholder: '描述画面：主体 + 构图 + 光线 + 氛围',
    }, n));
  }
  /* 角色设定图：和创作台一致，三项子功能各是一个独立节点，参考方式由节点类型固定
     （文生图 none / 锁定同角色 lock / 只借画风 style），节点内不再切「参考」。
     三者共用同一条版式 × 画法 × 参考 组合的档位表。 */
  function sheetInput(n) {
    var kind = kindOf(n), lib = typeof sheetLib === 'function' ? sheetLib() : {};
    var layouts = lib.layouts || [];
    var c = cfgOf(n);
    var ref = kind === 'sheetchar_lock' ? 'lock' : kind === 'sheetchar_style' ? 'style' : 'none';
    var i2i = ref !== 'none';
    c.ref = ref;                       // 节点类型即参考方式，不让旧配置里的 ref 影响提交
    var rr = i2i ? refsRow(n) : { refs: [], html: '' };
    var before = layoutBar(n, layouts) + inkBar(n, lib);
    if (i2i) {
      // 和创作台子功能说明同一句话，让用户一眼知道这个节点怎么用参考图
      before += '<div class="sheetbar"><span class="slb">参考</span><span class="tiny muted">'
        + (ref === 'style' ? '图 1 只借画风笔触，人物照文字新写' : '图 1 就是这个人：不换人、不改性别')
        + '</span></div>' + rr.html;
    }
    return GenUI.genInput(withBase({
      before: before,
      // 文生图没有参考图，画风靠风格下拉；图生图两项画风来自参考图，不显示风格。
      after: gradeBar(n, gradesFor(n)) + (i2i ? '' : styleBars(n, lib)),
      prompt: n.prompt || '', refs: rr.refs,
      placeholder: i2i ? '角色描述（可留空）：只做图片转换时不用写，直接生成即可'
        : '角色描述：年龄身份 + 脸型五官 + 发型发色 + 服装分件 + 2~3 个标志物',
    }, n));
  }
  /* 多连接点节点的固定槽位行：4 个上传位 / 2 个上传位始终都摆出来，
     接了线的那一位显示缩略图，空位显示「接素材」，一眼能看出哪个槽没接。 */
  function slotRowFor(n) {
    var kind = kindOf(n), ps = portsOf(kind);
    var bySlot = {};
    edgesInto(n).forEach(function (e) { bySlot[e.slot || ''] = nodeById(e.from); });
    var html = '', refs = [];
    ps.forEach(function (p) {
      var u = bySlot[p.slot];
      if (!u || !chipSrc(u)) {
        html += GenUI.vRefSlot({ label: p.label, kind: 'image', ref: null, upload: false });
        return;
      }
      var d = doneRunsOf(u);
      refs.push({ id: u.id, kind: 'image', src: chipSrc(u), name: u.name || '' });
      html += GenUI.vRefSlot({
        label: p.label, kind: 'image', ref: { id: u.id, src: chipSrc(u) }, idx: refs.length,
        upload: false, acts: ACTS, total: d.length, runIdx: d.length,
      });
    });
    return { html: '<div class="vslots">' + html + '</div>', refs: refs };
  }
  function extInput(n, kind) {
    var lib = (typeof extLib === 'function' ? extLib(kind) : {}) || {};
    var c = cfgOf(n);
    // 1.05：文生图 / 图生图 / 扩图拆成独立节点，节点固定一种提交方式，输入区不再有「方式」切换
    var mode = fixedMode(n.kind) || 't2i';
    if (mode !== 'i2i' && mode !== 'expand') mode = 't2i';
    c.mode = mode;
    var layouts = (lib.layouts || []).filter(function (l) {
      if (kind === 'prop') return String(l.mode || 't2i').split(',').indexOf(mode) >= 0 || !l.mode;
      return String(l.mode || '').split(',').indexOf(mode) >= 0;
    });
    // 扩图只支持 PUSH / PULL / TURN：从「文生图」默认的普通版式切过来时自动落到可用版式
    if (layouts.length && !layouts.some(function (l) { return l.id === c.layout; })) c.layout = layouts[0].id;
    var sl = mode === 't2i' ? { html: '', refs: [] } : slotRowFor(n);
    var before = layoutBar(n, layouts);
    if (mode !== 'expand') before += inkBar(n, lib);
    if (kind === 'scene' && mode !== 'expand') {
      before += cseg('nochar', cfv(n, 'nochar', 'yes'),
        [{ v: 'yes', label: '是' }, { v: 'no', label: '否' }], '禁角色', '强调画面里没有人物');
    }
    if (kind === 'prop' && mode === 'i2i') {
      before += cseg('ref', cfv(n, 'ref', 'lock'),
        [{ v: 'lock', label: '锁定同道具' }], '参考', '左侧上传位接的那张就是道具本体');
    }
    before += sl.html;
    return GenUI.genInput(withBase({
      before: before,
      after: gradeBar(n, gradesFor(n)) + (mode === 'expand' ? '' : styleBars(n, lib)),
      prompt: n.prompt || '', refs: sl.refs,
      placeholder: mode === 'expand' ? '扩图描述（可留空）：例如保持原场景布局，左侧再露出半扇窗'
        : kind === 'scene' ? '场景描述：环境、方位、道具位置、光线与氛围'
          : '道具描述：写清道具本体；不写视角就默认三个斜向下 30° 视角',
    }, n));
  }
  function boardInput(n) {
    var lib = typeof boardLib === 'function' ? boardLib() : {};
    var rr = refsRow(n);
    return GenUI.genInput(withBase({
      before: layoutBar(n, lib.layouts || []) + inkBar(n, lib) + rr.html,
      after: gradeBar(n, gradesFor(n)),
      prompt: n.prompt || '', refs: rr.refs,
      placeholder: '逐格描述，一行一格（第 1 行 = 第 1 格；留空的行留白）',
      allowEmpty: true,
    }, n));
  }
  function vidInput(n, kind) {
    var c = cfgOf(n);
    var allow = REF_KIND[kind] || 'any';
    // 首尾帧节点固定两个上传位（首帧 / 尾帧），其它视频节点按素材类型排
    var sl = allow === 'none'
      ? { html: '', refs: [] }
      : (kind === 'fl2v' ? slotRowFor(n) : slotsFor(n, allow));
    var before = gradeBar(n, gradesFor(n)) + durationBar(n);
    if (kind === 'board2v') {
      before += cseg('grid', cfv(n, 'grid', 4),
        [{ v: 4, label: '四宫格' }, { v: 6, label: '六宫格' }, { v: 9, label: '九宫格' }], '宫格');
    }
    before += sl.html;
    var ph = kind === 'board2v' ? '逐格描述，一行一格（可留空，留空就按故事板原图动起来）'
      : kind === 'fl2v' ? '描述这一镜怎么动；左侧图1 = 首帧，图2 = 尾帧'
        : kind === 't2v' ? '描述镜头：谁、在哪、做什么、怎么运镜'
          : '描述这一镜的画面；用 @ 引用左侧素材';
    return GenUI.genInput(withBase({
      before: before, after: '', prompt: n.prompt || '', refs: sl.refs, placeholder: ph,
      allowEmpty: kind === 'board2v',
    }, n));
  }
  function audioInput(n, kind) {
    var f = funcFor(kind);
    var allow = REF_KIND[kind] || 'none';
    var sl = allow === 'audio' ? slotsFor(n, 'audio') : { html: '', refs: [] };
    var before = sl.html + gradeBar(n, gradesFor(n));
    var after = (kind === 'music' || kind === 'refmusic') ? lyricsBox(n) : '';
    var ph = f.mode === 'tts' ? '要念的台词'
      : f.mode === 'clone' ? '要克隆音色念的台词' : '描述风格：曲风、乐器、情绪、人声';
    return GenUI.genInput(withBase({
      before: before, after: after, prompt: n.prompt || '', refs: sl.refs, placeholder: ph,
    }, n));
  }
  /* 生成节点 → 输入块：按 kind 分派。以后新加子功能，只在这里加一条。 */
  function inputFor(n) {
    var kind = kindOf(n);
    if (kind === 'imagegen') return imageInputFor(n);
    if (kind === 'videogen') return videoInputFor(n);
    if (kind === 't2i') return t2iInput(n);
    if (kind === 'sheetchar' || kind === 'sheetchar_lock' || kind === 'sheetchar_style') return sheetInput(n);
    if (kind === 'sheetscene' || kind === 'sheetscene_i2i' || kind === 'sheetscene_expand') return extInput(n, 'scene');
    if (kind === 'sheetprop' || kind === 'sheetprop_i2i') return extInput(n, 'prop');
    if (kind === 'board') return boardInput(n);
    if (kind === 'music' || kind === 'refmusic' || kind === 'tts' || kind === 'clone') return audioInput(n, kind);
    return vidInput(n, kind);
  }
  /* 节点 → 提交体：和创作台 /api/tasks 收同一套结构化输入（sheet / ext / board / video / audio / image），
     所以画布节点提交出去的东西和功能页面点发送完全一样。 */
  function taskBody(n) {
    var kind = kindOf(n), f = funcFor(kind), c = cfgOf(n);
    var g = currentGrade(n);
    var prompt = String(n.prompt || '').trim();
    var body = {
      capId: f.capId || 'image.generate', prompt: prompt, grade: n.grade || '',
      inputs: inputsOf(n), wf: f.wf || null, projectId: CS.pid, nodeId: n.id,
    };
    var lines = prompt.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
    if (kind === 't2i' || kind === 'imagegen') {
      // 图生图节点沿用 1.05 的字段（ink/style/styleText 直接挂在节点上），
      // 其它图片类节点统一用 cfg；两者最终都变成同一份 ImageStylePayload。
      var st = kind === 'imagegen' ? n : c;
      // 画布「图片-参考生图」和创作台图生图是同一个能力：界面隐藏了风格行，
      // 提交也得按 imgPayload 的图生图口径——只保留画法/线稿，风格字段清空。
      // 否则节点里残留的旧风格文字会被服务端 compose 进提示词，和创作台出图不一致。
      body.image = kind === 'imagegen'
        ? { ink: st.ink || 'color', lineStyle: st.ink === 'line' ? (st.lineStyle || '') : null,
            styleCustom: true, style: '', styleText: '' }
        : { ink: st.ink || 'color', lineStyle: st.lineStyle || '',
            style: st.style || '', styleCustom: st.styleCustom !== false, styleText: st.styleText || '' };
    } else if (kind === 'sheetchar' || kind === 'sheetchar_lock' || kind === 'sheetchar_style') {
      // 参考方式由节点类型固定，和创作台三项子功能走同一条提交链路
      var ref = kind === 'sheetchar_lock' ? 'lock' : kind === 'sheetchar_style' ? 'style' : 'none';
      body.sheet = {
        layout: c.layout, character: prompt,
        // 文生图没有参考图，风格由风格下拉决定；图生图两项画风靠参考图，不提交风格
        style: ref === 'none' ? (c.style || '') : '',
        styleCustom: ref === 'none' ? (c.styleCustom !== false) : false,
        styleText: ref === 'none' ? (c.styleText || '') : '',
        ink: c.ink || 'color', lineStyle: c.lineStyle || '',
        ref: ref, grade: n.grade || '',
      };
    } else if (kind === 'sheetscene' || kind === 'sheetscene_i2i' || kind === 'sheetscene_expand'
      || kind === 'sheetprop' || kind === 'sheetprop_i2i') {
      var scene = kind.indexOf('sheetscene') === 0;
      var mode = fixedMode(kind) || (c.mode || 't2i');
      var ws = mode === 't2i' ? { inputs: [], slots: {} } : inputsWithSlots(n);
      body.inputs = ws.inputs;
      body.ext = {
        kind: scene ? 'scene' : 'prop', mode: mode,
        layout: c.layout, sub: c.sub || '', text: prompt,
        ink: c.ink || 'color', nochar: c.nochar || 'yes', ref: c.ref || 'lock',
        refSlots: ws.slots, style: c.style || '', styleCustom: c.styleCustom !== false,
        styleText: c.styleText || '', lineStyle: c.lineStyle || '', grade: n.grade || '',
      };
    } else if (kind === 'board') {
      body.board = {
        layout: c.layout || 'G4', cells: lines,
        ink: c.ink || 'color', lineStyle: c.lineStyle || '', grade: n.grade || '',
      };
    } else if (kind === 't2v' || kind === 'fl2v' || kind === 'videogen' || kind === 'board2v') {
      body.video = {
        mode: f.mode, ratio: g.ratio || '16:9', res: g.res || '480P', steps: +g.steps || 8,
        duration: Math.min(15, Math.max(4, +((n.video || {}).duration) || 5.2)),
        grid: +(c.grid || 4), cells: kind === 'board2v' ? lines : [],
      };
    } else {
      body.audio = { mode: f.mode, lyrics: c.lyrics || '', duration: g.duration || null };
    }
    return body;
  }

  /* 结果块单独出一份：轮询刷新时只换这一块，输入区（正在打的字、光标）不动 */
  function resultHTML(n) {
    var kind = kindOf(n), job = jobOf(n.jobId);
    var media = kindMeta(kind).media || 'image';
    if (!job) {
      return GenUI.emptyTile(media === 'video' ? '还没有视频' : media === 'audio' ? '还没有音频' : '还没有图片', 'ph-tile');
    }
    // 画布节点：不要「再来一张」（重跑走输入区的 ↑ 生成）；「加入队列生成」的成品也回到这块里
    return GenUI.jobCard(job, { prompt: false, refine: false, again: false, acts: ACTS, full: true });
  }

  function bodyHTML(n) {
    var kind = kindOf(n), media = kindMeta(kind).media || 'image';
    // 资产节点 = 结果块那块卡（.job）里的内容：媒体框 + 放大。没有提示词、种子、队列、再来一张、基于上一条。
    if (!isGen(n)) {
      if (!n.refId) {
        return '<div class="job asset-job"><div class="body asset-body">'
          + '<button class="cmat-add" data-pick="' + kind + '">'
          + (media === 'video' ? '🎞 点击上传视频' : media === 'audio' ? '🎵 点击上传音频' : '🖼 点击上传图片')
          + '</button></div></div>';
      }
      // 素材卡和结果块同一套骨架，只有内容 + 放大 / 下载 / 复制
      return GenUI.assetCard({
        kind: media, src: refUrl(n.refId), id: n.id, acts: ACTS,
        name: media === 'audio' ? '' : n.name,   // 文件名已经在节点头里，音频卡里不重复
      });
    }
    // 换过版式 / 画法后旧档位可能不在新组合里：渲染前先归位，
    // 保证档位条有选中态、提交时档位名一定合法
    fixGrade(n);
    // 输入块：按 kind 分派到对应子功能的版式（文生图 / 设定图 / 故事板 / 视频 / 音频…）
    return '<div class="cgen-stack"><div class="cres">' + resultHTML(n) + '</div>' + inputFor(n) + '</div>';
  }

  /* 输入端口：普通节点一个；场景图生图/扩图四个上传位、首尾帧两个，竖排在卡片左侧，
     端口旁边写清槽位名，方便用户知道哪条线代表哪张参考。 */
  function portsHTML(n, kind) {
    var ps = portsOf(kind);
    if (ps.length <= 1) return '<span class="port in" data-in="' + n.id + '" title="接素材"></span>';
    return ps.map(function (p, i) {
      var top = 28 + i * (44 / Math.max(1, ps.length - 1));
      return '<span class="port in" data-in="' + n.id + '" data-slot="' + p.slot + '" style="top:' + top + '%"'
        + ' title="' + esc(p.label) + '：这个上传位只能接一个图片节点"></span>'
        + '<span class="port-lab" style="top:' + top + '%">' + esc(p.label) + '</span>';
    }).join('');
  }
  function nodeHTML(n) {
    var kind = kindOf(n), gen = isGen(n);
    var meta = kindMeta(kind), media = meta.media || 'image';
    var em = meta.em || (media === 'video' ? '🎞' : media === 'audio' ? '🎵' : '🖼');
    var cls = 'cnode' + (gen ? ' gen' : ' mat') + (CS.sel === n.id ? ' sel' : '');
    var w = gen ? GEN_W : (n.w || W);
    return '<div class="' + cls + '" data-id="' + n.id + '" data-kind="' + kind + '" style="left:' + n.x + 'px;top:' + n.y + 'px;width:' + w + 'px">'
      + '<div class="cnode-hd" data-hd="1"><span class="em">' + em + '</span>'
      + '<span class="nm">' + esc(n.title || (gen ? (meta.name || '生成') : (n.name || meta.name || '素材'))) + '</span>'
      + '<button class="x" data-del="' + n.id + '" title="删除节点">✕</button></div>'
      + '<div class="cnode-bd">' + bodyHTML(n) + '</div>'
      + (gen ? portsHTML(n, kind) : '')
      + '<span class="port out" data-out="' + n.id + '" title="拖出去连线"></span>'
      + '</div>';
  }

  /* ------------------------------------------------------------ 绘制 */
  function applyView() {
    if (!CS.world) return;
    CS.world.style.transform = 'translate(' + CS.view.x + 'px,' + CS.view.y + 'px) scale(' + CS.view.k + ')';
    var pct = $q('#cvsPct'); if (pct) pct.textContent = Math.round(CS.view.k * 100) + '%';
    var grid = $q('#cvsGrid');
    if (grid) grid.style.backgroundPosition = CS.view.x + 'px ' + CS.view.y + 'px';
  }
  function portPos(id, which, slot) {
    var sel = '.cnode[data-id="' + id + '"] .port.' + which;
    if (slot) sel += '[data-slot="' + slot + '"]';
    var el = $q(sel, CS.world) || $q('.cnode[data-id="' + id + '"] .port.' + which, CS.world);
    if (!el) return null;
    var w = CS.world.getBoundingClientRect();
    var r = el.getBoundingClientRect();
    return { x: (r.left + r.width / 2 - w.left) / CS.view.k, y: (r.top + r.height / 2 - w.top) / CS.view.k };
  }
  function edgePath(a, b) {
    var dx = Math.max(40, Math.abs(b.x - a.x) * 0.45);
    return 'M' + a.x + ',' + a.y + ' C' + (a.x + dx) + ',' + a.y + ' ' + (b.x - dx) + ',' + b.y + ' ' + b.x + ',' + b.y;
  }
  function paintLinks() {
    if (!CS.svg) return;
    var edges = (CS.project && CS.project.edges) || [];
    var html = edges.map(function (e, i) {
      var a = portPos(e.from, 'out'), b = portPos(e.to, 'in', e.slot);
      if (!a || !b) return '';
      var d = edgePath(a, b);
      // 细线只有 2px，单独垫一条透明的粗线做点击热区，才点得中「线中间」
      return '<path class="hit" data-edge="' + i + '" d="' + d + '"></path>'
        + '<path data-edge="' + i + '" d="' + d + '" class="' + (CS.scissor === i ? 'hot' : '') + '"></path>';
    }).join('');
    if (CS.link) {
      var from = portPos(CS.link.from, 'out');
      if (from) html += '<path class="tmp" d="' + edgePath(from, { x: CS.link.x, y: CS.link.y }) + '"></path>';
    }
    CS.svg.innerHTML = html;
    paintScissor();
  }
  function paintScissor() {
    var old = $q('.cvs-scissor'); if (old) old.remove();
    if (CS.scissor === null) return;
    var edges = (CS.project && CS.project.edges) || [];
    var e = edges[CS.scissor]; if (!e) return;
    var a = portPos(e.from, 'out'), b = portPos(e.to, 'in', e.slot); if (!a || !b) return;
    var mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    var btn = document.createElement('button');
    btn.className = 'cvs-scissor';
    btn.title = '剪断这条连线';
    btn.textContent = '✂';
    btn.style.left = (mx * CS.view.k + CS.view.x) + 'px';
    btn.style.top = (my * CS.view.k + CS.view.y) + 'px';
    btn.onclick = function (ev) { ev.stopPropagation(); cutEdge(CS.scissor); };
    CS.stage.appendChild(btn);
  }
  // 需求6：给画布节点的输入块加移动端「收起/展开」把手（复用 app.js 的全局注入逻辑）
  function foldNodeBoxes() {
    if (typeof window.enhanceFoldHandles === 'function') window.enhanceFoldHandles();
  }
  function paintAll() {
    if (!CS.world) return;
    var nodes = (CS.project && CS.project.nodes) || [];
    $a('.cnode', CS.world).forEach(function (el) { el.remove(); });
    var keep = $q('svg', CS.world);
    nodes.forEach(function (n) {
      var box = document.createElement('div');
      box.innerHTML = nodeHTML(n);
      CS.world.insertBefore(box.firstChild, keep ? null : null);
    });
    var empty = $q('#cvsEmpty');
    if (empty) empty.style.display = nodes.length ? 'none' : 'flex';
    paintLinks();
    updateBar();
    syncAssetSizes();
    foldNodeBoxes();
  }
  function repaintNode(n) {
    var old = $q('.cnode[data-id="' + n.id + '"]', CS.world);
    if (!old) return;
    var box = document.createElement('div');
    box.innerHTML = nodeHTML(n);
    old.replaceWith(box.firstChild);
    paintLinks();
    syncAssetSizes();
    foldNodeBoxes();
  }

  /* 资产卡右下角的尺寸角标：结果块的尺寸是任务给的，素材得自己量（图片看 naturalSize，
     视频看 videoWidth/Height + duration）。媒体还没加载完就等它加载完再回填。 */
  function mediaSizeText(m) {
    if (!m) return '';
    if (m.tagName === 'IMG') return m.naturalWidth ? m.naturalWidth + '×' + m.naturalHeight : '';
    var bits = [], w = m.videoWidth || 0, h = m.videoHeight || 0;
    if (w && h) bits.push(w + '×' + h);
    if (isFinite(m.duration) && m.duration > 0) bits.push(m.duration.toFixed(1) + ' 秒');
    return bits.join(' · ');
  }
  function syncAssetSizes() {
    $a('.cnode.mat [data-asize]', CS.world).forEach(function (badge) {
      var card = badge.closest('.job');
      var m = card && $q('img,video,audio', card);
      if (!m) return;
      var put = function () {
        var t = mediaSizeText(m);
        if (t) badge.textContent = t;
      };
      var loaded = m.tagName === 'IMG' ? m.complete && m.naturalWidth : m.readyState >= 1 && (m.videoWidth || m.duration);
      if (loaded) { put(); return; }
      // 元数据可能分几次到（duration 先 NaN 再有值），几个事件都听一遍，取到就写
      ['load', 'loadedmetadata', 'loadeddata', 'durationchange', 'canplay']
        .forEach(function (ev) { m.addEventListener(ev, put); });
    });
  }
  function updateBar() {
    var nodes = (CS.project && CS.project.nodes) || [];
    var edges = (CS.project && CS.project.edges) || [];
    var c = $q('#cvsCount'); if (c) c.textContent = nodes.length + ' 个节点 · ' + edges.length + ' 条连线';
    var label = $q('#cvsProjLabel');
    if (label) label.textContent = (CS.project && CS.project.name) || '画布';
    var sv = $q('#cvsSave');
    if (sv) { sv.className = 'cvs-save ' + (CS.dirty ? 'dirty' : 'ok'); sv.textContent = CS.dirty ? '未保存' : '已保存'; }
  }
  function tip(text) {
    var el = $q('#cvsTip'); if (!el) return;
    el.textContent = text; el.classList.add('on');
    clearTimeout(CS.tipTimer);
    CS.tipTimer = setTimeout(function () { el.classList.remove('on'); }, 2200);
  }

  /* ------------------------------------------------------------ 存盘 */
  function snapshot() {
    return {
      name: (CS.project && CS.project.name) || '未命名画布',
      view: CS.view,
      // 存盘副本里把图生图表单状态压进 note，别把运行时的内存节点直接交出去
      nodes: ((CS.project && CS.project.nodes) || []).map(function (n) {
        var c = {}; Object.keys(n).forEach(function (k) { c[k] = n[k]; });
        saveImgExtras(c);
        return c;
      }),
      edges: (CS.project && CS.project.edges) || [],
    };
  }
  function saveSoon() {
    CS.dirty = true; updateBar();
    clearTimeout(CS.saveTimer);
    CS.saveTimer = setTimeout(flushSave, 600);
  }
  async function flushSave() {
    if (!CS.pid || !CS.project || CS.saving || !CS.dirty) return;
    CS.saving = true;
    var res = await api('/api/canvas/project/' + encodeURIComponent(CS.pid), { method: 'PUT', body: snapshot() });
    CS.saving = false;
    if (!res.ok) { toast(res.reason || '画布没保存上'); return; }
    CS.dirty = false; updateBar();
  }

  /* ------------------------------------------------------------ 节点操作 */
  /* 新节点别压在人家的卡上：同一块地方有节点就往右下顺一格 */
  // 落点如果压住已有卡片就往下/往右挪，别让两张卡叠在一起看不清
  function nodeBox(o) {
    var el = $q('.cnode[data-id="' + o.id + '"]', CS.world);
    return { x: o.x, y: o.y, w: o.w || (isGen(o) ? GEN_W : W), h: (el && el.offsetHeight) || 260 };
  }
  function freeSpot(x, y, w) {
    var nodes = (CS.project && CS.project.nodes) || [];
    var nw = w || W;
    for (var i = 0; i < 60; i++) {
      var hit = nodes.some(function (o) {
        var b = nodeBox(o);
        return x < b.x + b.w + 10 && x + nw + 10 > b.x && y < b.y + b.h + 10 && y + 120 > b.y;
      });
      if (!hit) break;
      y += 40;                       // 优先向下让位，保持横向阅读顺序
      if (i % 3 === 2) x += 44;      // 让不开再往右挪一点
    }
    return { x: x, y: y };
  }
  function addNode(kind, x, y, extra) {
    var meta = kindMeta(kind);
    var gen = meta.gen !== undefined ? !!meta.gen : (kind === 'imagegen' || kind === 'videogen');
    var at = freeSpot(Math.round(x), Math.round(y), (extra && extra.w) || (gen ? GEN_W : W));
    var n = {
      id: newId(), kind: kind, x: at.x, y: at.y, w: gen ? GEN_W : W,
      title: gen ? (meta.name || '生成') : '',
      prompt: '', grade: '', refId: '', jobId: '',
      // 图片生成节点本身就是图片生成页的「图生图」，默认就在图生图模式；
      // 场景 / 道具的图生图·扩图节点类型已经固定方式，mode 一起写进节点方便排错
      mode: kind === 'imagegen' ? 'i2i' : (fixedMode(kind) || ''),
      styleCustom: true,
      // 1.06：子功能节点的控件值统一放 cfg（版式 / 画法 / 参考方式 / 宫格 / 歌词…）
      cfg: gen ? { ink: 'color' } : null,
      video: (gen && (meta.media === 'video')) ? { mode: meta.mode || 'ref2v', ratio: '16:9', res: '768P', steps: 8, duration: 5.2 } : null,
    };
    if (extra) Object.keys(extra).forEach(function (k) { n[k] = extra[k]; });
    CS.project.nodes.push(n);
    CS.sel = n.id;
    paintAll(); saveSoon();
    return n;
  }
  function removeNode(id) {
    CS.project.nodes = CS.project.nodes.filter(function (n) { return n.id !== id; });
    CS.project.edges = CS.project.edges.filter(function (e) { return e.from !== id && e.to !== id; });
    if (CS.sel === id) CS.sel = null;
    CS.scissor = null;
    paintAll(); saveSoon();
  }
  function connect(fromId, toId, slot) {
    var from = nodeById(fromId), to = nodeById(toId);
    if (!to) return false;
    var kind = kindOf(to), edges = CS.project.edges;
    if (isMultiPort(kind) && !slot) {
      // 从卡片空白处松手：自动找第一个还没接线的上传位
      slot = (portsOf(kind).filter(function (p) {
        return !edges.some(function (e) { return e.to === toId && e.slot === p.slot; });
      })[0] || {}).slot;
      if (!slot) { tip('这个节点的 ' + portsOf(kind).length + ' 个上传位都接满了，先剪断一条'); return false; }
    }
    if (!canConnect(from, to, slot)) {
      var lab = (portsOf(kind).filter(function (p) { return p.slot === slot; })[0] || {}).label;
      if (isMultiPort(kind) && slot) toast('「' + lab + '」这个上传位已经接了别的节点，一个位只能接一张图');
      else toast(kind === 'imagegen' ? '图片生成节点左侧只能接图片' : '这条线接不上');
      return false;
    }
    if (isMultiPort(kind)) {
      if (edges.some(function (e) { return e.from === fromId && e.to === toId && e.slot === slot; })) return false;
      edges.push({ from: fromId, to: toId, slot: slot });
    } else {
      if (edges.some(function (e) { return e.from === fromId && e.to === toId; })) return false;
      edges.push({ from: fromId, to: toId });
    }
    paintAll(); saveSoon();
    return true;
  }
  function cutEdge(idx) {
    var edges = CS.project.edges;
    if (!edges[idx]) return;
    edges.splice(idx, 1);
    CS.scissor = null;
    paintAll(); saveSoon();
    tip('已剪断这条连线');
  }

  /* ------------------------------------------------------------ 菜单 */
  function closeMenu() {
    if (CS.menu) { CS.menu.remove(); CS.menu = null; }
  }
  function openGenMenu(clientX, clientY, linkFrom) {
    closeMenu();
    var rect = CS.stage.getBoundingClientRect();
    var x = clientX - rect.left, y = clientY - rect.top;
    var menu = document.createElement('div');
    menu.className = 'cvs-menu';
    // 菜单 = 素材节点 + 全部子功能节点。子功能节点由后端 nodeKinds 给（funcs 同一张表），
    // 所以以后加功能只改后端 NODE_FUNCS，菜单自动跟上。
    var groups = [];
    if (!linkFrom) {
      groups.push({ name: '素材', items: [
        { kind: 'image', name: '图片节点', em: '🖼' },
        { kind: 'audio', name: '音频节点', em: '🎵' },
        { kind: 'video', name: '视频节点', em: '🎞' },
      ] });
    }
    var byGroup = {};
    (CS.kinds || []).forEach(function (k) {
      if (!k.gen) return;
      var f = funcOf(k.id) || {};
      if (f.enabled === false) return;                 // 管理台关掉的子功能不出现
      if (linkFrom) {
        // 从端口拖出来：只列这个上游接得进去的节点（文生图 / 文生视频不收素材）
        var from = nodeById(linkFrom);
        var allow = REF_KIND[k.id] || 'any';
        var media = from ? kindMeta(kindOf(from)).media : '';
        if (allow === 'none' || (allow !== 'any' && media !== allow)) return;
      }
      var g = k.group || '生成';
      (byGroup[g] = byGroup[g] || []).push({ kind: k.id, name: k.name || '生成节点', em: k.em || '✨' });
    });
    ['图片', '视频', '音频'].forEach(function (g) {
      if (byGroup[g]) { groups.push({ name: g, items: byGroup[g] }); delete byGroup[g]; }
    });
    Object.keys(byGroup).forEach(function (g) { groups.push({ name: g, items: byGroup[g] }); });

    var total = 0;
    menu.innerHTML = groups.map(function (grp) {
      if (!grp.items.length) return '';
      return '<div class="cvs-menu-grp">' + esc(grp.name) + '</div>'
        + grp.items.map(function (it) {
          total += 1;
          return '<button data-mk="' + it.kind + '"><span class="em">' + it.em + '</span>'
            + '<span class="tx"><b>' + esc(it.name) + '</b></span></button>';
        }).join('');
    }).join('');
    var menuH = total * 30 + groups.length * 24 + 14;
    menu.style.left = Math.max(6, Math.min(x, rect.width - 208)) + 'px';
    menu.style.top = Math.max(6, Math.min(y, rect.height - menuH)) + 'px';
    CS.stage.appendChild(menu);
    CS.menu = menu;
    CS.menuAt = { x: x, y: y, linkFrom: linkFrom || null };
    $a('[data-mk]', menu).forEach(function (b) {
      b.onclick = function (ev) {
        ev.stopPropagation();
        createFromMenu(b.getAttribute('data-mk'));
      };
    });
  }
  function createFromMenu(kind) {
    var at = CS.menuAt || { x: 120, y: 120 };
    var p = stageToWorld(at.x, at.y);
    closeMenu();
    var n = addNode(kind, p.x - 60, p.y - 24);
    if (at.linkFrom) connect(at.linkFrom, n.id);
    CS.link = null; paintLinks();
  }
  function stageToWorld(sx, sy) { return { x: (sx - CS.view.x) / CS.view.k, y: (sy - CS.view.y) / CS.view.k }; }

  /* ------------------------------------------------------------ 上传素材 */
  var pickKind = 'image';
  var pickAt = null;
  var pickOwner = null;   // 节点内「点击上传」时记住来源节点，上传后填回它，不另建空节点
  var pickTargetGen = null; // 图片生成节点里的「＋ 上传」：上传后建素材节点并自动接到该节点
  function openPicker(kind, at, ownerId, targetGenId) {
    pickKind = kind;
    pickAt = at || null;
    pickOwner = ownerId || null;
    pickTargetGen = targetGenId || null;
    var f = $q('#cvsFile');
    f.accept = kind === 'video' ? 'video/*' : kind === 'audio' ? 'audio/*' : 'image/*';
    f.multiple = kind !== 'video' && kind !== 'audio';
    f.value = '';
    f.click();
  }
  var uploading = false;
  // 资产节点两种上传方式：① 菜单建空节点后点节点内「点击上传」；② 本地文件直接拖到画布上
  function mediaKindOfFile(file) {
    var t = String((file && file.type) || '').toLowerCase();
    if (t.indexOf('image/') === 0) return 'image';
    if (t.indexOf('video/') === 0) return 'video';
    if (t.indexOf('audio/') === 0) return 'audio';
    var ext = String((file && file.name) || '').split('.').pop().toLowerCase();
    if (/^(png|jpe?g|webp|gif|bmp|avif)$/.test(ext)) return 'image';
    if (/^(mp4|mov|webm|mkv|avi|m4v)$/.test(ext)) return 'video';
    if (/^(mp3|wav|m4a|aac|flac|ogg|opus)$/.test(ext)) return 'audio';
    return '';
  }
  // 拖进来的文件按类型各建一个资产节点，落到放手的位置附近
  async function dropFiles(files, at) {
    if (uploading || !files.length) return;
    uploading = true;
    var made = 0, skipped = 0;
    for (var i = 0; i < files.length; i++) {
      var kind = mediaKindOfFile(files[i]);
      if (!kind) { skipped++; continue; }
      var form = new FormData();
      form.append('file', files[i], files[i].name);
      var res = await api('/api/uploads?kind=' + kind, { form: form });
      if (!res.ok) { skipped++; continue; }
      var base = at || stageToWorld(CS.stage.clientWidth / 2, 120);
      addNode(kind, base.x + (made % 2) * 330, base.y + Math.floor(made / 2) * 250, {
        refId: res.data.id, name: res.data.name, kind: kind,
      });
      made++;
    }
    uploading = false;
    if (made) { paintAll(); saveSoon(); tip(made > 1 ? '已放上 ' + made + ' 个素材节点' : '已放上素材节点'); }
    if (skipped) toast('有 ' + skipped + ' 个文件类型不支持，先没放上来');
  }
  async function uploadFiles(files) {
    if (uploading) return;         // 同一次选择只跑一遍，change 偶尔会重复触发
    uploading = true;
    var made = [];
    for (var i = 0; i < files.length; i++) {
      var form = new FormData();
      form.append('file', files[i], files[i].name);
      var res = await api('/api/uploads?kind=' + pickKind, { form: form });
      if (!res.ok) { toast(res.reason || '上传没成功'); continue; }
      var owner = pickOwner && nodeById(pickOwner);
      var target = pickTargetGen && nodeById(pickTargetGen);
      var n;
      if (owner && i === 0) {
        // 填回节点内点「点击上传」的那个空素材节点
        owner.refId = res.data.id; owner.name = res.data.name; owner.kind = pickKind;
        n = owner;
      } else {
        var base = pickAt || stageToWorld(CS.stage.clientWidth / 2, 120);
        n = addNode(pickKind, base.x + i * 40, base.y + i * 30, {
          refId: res.data.id, name: res.data.name, kind: pickKind,
        });
        if (target) connect(n.id, target.id);
      }
      made.push(n);
      pickAt = null;
    }
    uploading = false;
    if (made.length) { paintAll(); saveSoon(); tip(pickOwner ? '素材已放上节点' : target ? '参考图已接到图片生成节点' : '已放上素材节点'); }
    pickOwner = null;
    pickTargetGen = null;
  }

  /* ------------------------------------------------------------ 生成 */
  async function generate(n, btn) {
    if (!n || n._busy) return;
    var kind = kindOf(n), func = funcFor(kind);
    if (!func.capId) { toast('这个节点类型还没配子功能，先去管理台'); return; }
    // combo 类（设定图 / 场景 / 道具 / 故事板）的工作流由后端按组合推导，
    // 前端没有 wf 是正常的；只有 wf 类（文生图 / 图生图 / 视频 / 音频）才要求有绑定。
    if (!func.wf && func.gradesFrom !== 'combo') { toast('这个子功能还没配工作流，先去管理台'); return; }
    fixGrade(n);
    // 提交体和创作台 /api/tasks 完全同一套结构化输入（见 taskBody）
    var body = taskBody(n);
    // 图生图 / 扩图 / 首尾帧必须有参考图：没接素材直接提醒，别让后端白白拒一次
    var needsRef = kind === 'sheetchar_lock' || kind === 'sheetchar_style' || kind === 'sheetscene_i2i'
      || kind === 'sheetscene_expand' || kind === 'sheetprop_i2i' || kind === 'fl2v';
    if (needsRef && !body.inputs.length) {
      tip(kind === 'fl2v' ? '先在左侧「首帧」接一张图' : '先在左侧上传位接一张参考图');
      return;
    }
    // 槽位的「主参考」不能缺：场景图生图/扩图必须有参考图位，首尾帧必须有首帧，
    // 否则后端会把全景图当成主参考、把尾帧当成首帧
    var hasSlot = function (s) {
      return edgesInto(n).some(function (e) { return e.slot === s && inputItemOf(nodeById(e.from)); });
    };
    if ((kind === 'sheetscene_i2i' || kind === 'sheetscene_expand') && !hasSlot('ref')) {
      tip('「参考图」这个上传位还没接图'); return;
    }
    if (kind === 'fl2v' && !hasSlot('first')) {
      tip('「首帧」这个上传位还没接图'); return;
    }
    // 允许「零描述」的情况：故事板逐格可留空、扩图只靠参考图、图生图纯转换
    var noPromptOk = kind === 'board' || kind === 'board2v'
      || kind === 'sheetchar_lock' || kind === 'sheetchar_style' || kind === 'sheetscene_expand'
      || (kind === 'sheetprop_i2i' && body.inputs.length);
    if (!body.prompt && !body.inputs.length && !noPromptOk) {
      tip('写点描述，或者先接一个素材再生成'); return;
    }
    n._busy = true;
    if (btn) { btn.disabled = true; btn.classList.add('busy'); }
    var res = await api('/api/canvas/tasks', { body: body });
    n._busy = false;
    if (btn) { btn.disabled = false; btn.classList.remove('busy'); btn.innerHTML = '↑'; }
    if (!res.ok) { toastBlocked('这次没能生成', res.reason || '稍后再试'); return; }
    n.jobId = res.data.job_id;
    saveSoon();
    await pull();
    repaintNode(n);
    toast('已提交，右栏可以看到进度');
  }

  /* 「加入队列生成」在画布里只服务本节点：档位取本节点所属子功能的挡位表，
     新成品加在同一个 job 上，跑完自然回到这个节点的结果块里（功能页面那套弹窗不动）。 */
  function openQueueForNode(n) {
    var job = jobOf(n.jobId);
    if (!job) { tip('还没有成品，先点 ↑ 生成一张'); return; }
    var func = funcFor(kindOf(n));
    var grades = gradesFor(n).length ? gradesFor(n) : (func.grades || []);
    if (!grades.length) { tip('这个子功能还没有档位'); return; }
    var base = (job.runs || [])[0] || {};
    var pick = grades.filter(function (g) { return g.name === (base.grade || n.grade); })[0] || grades[0];
    var done = (job.runs || []).filter(function (r) { return r.state === 'done' && !r.hidden; }).length;
    var html = '<div class="m-head"><h3>加入队列生成</h3><button class="btn ghost sm" data-close>✕</button></div>'
      + '<div class="m-body"><div class="qintro">挑一档，出同一张的另一个版本。种子不变，新的排在<b>成品 '
      + (done + 1) + '</b>，跑完会回到这个节点上。</div>'
      + '<div class="qlabel">用哪一档</div><div class="qgrades">'
      + grades.map(function (g) {
        var d = g.note || [g.ratio, g.res, g.steps ? g.steps + ' 步' : ''].filter(Boolean).join(' · ');
        return '<div class="qg' + (g.name === pick.name ? ' on' : '') + '" data-cqg="' + esc(g.name) + '">'
          + '<span class="qg-n">' + esc(g.name) + '</span><span class="qg-d">' + esc(d) + '</span></div>';
      }).join('') + '</div></div>'
      + '<div class="m-foot"><span class="tiny muted" style="flex:1">可以连着加，一个一个跑</span>'
      + '<button class="btn sm" data-close>取消</button><button class="btn sm primary" id="cqGo">加入队列生成</button></div>';
    var mask = openModal(html), sel = pick.name;
    $a('[data-cqg]', mask).forEach(function (el) {
      el.onclick = function () {
        sel = el.getAttribute('data-cqg');
        $a('[data-cqg]', mask).forEach(function (x) { x.classList.toggle('on', x === el); });
      };
    });
    var go = $q('#cqGo', mask);
    if (go) go.onclick = async function () {
      go.disabled = true;
      var res = await api('/api/tasks/' + encodeURIComponent(job.id) + '/queue', { body: { grade: sel } });
      closeModal();
      if (!res.ok) { toastBlocked('没能加进队列', res.reason || ''); return; }
      await pull(); refresh();
      tip('已加入队列，跑完会出现在这个节点');
    };
  }

  async function againNode(n) {
    // 需求8：「重新生成」在当前节点重跑，不新建节点。沿用同一 prompt / 档位 / 连线。
    var btn = $q('.cnode[data-id="' + n.id + '"] [data-gen]', CS.world);
    await generate(n, btn);
  }

  /* ------------------------------------------------------------ 交互 */
  function onPointerDown(e) {
    if (!CS.project) return;
    var t = e.target;
    // 剪刀 / 弹出菜单是浮在画布上的按钮：按下去不能当成「点空白」把状态清掉，
    // 否则按钮会在 pointerdown 里被移除，click 永远到不了它
    if (t.closest && (t.closest('.cvs-scissor') || t.closest('.cvs-menu'))) return;
    if (CS.menu) closeMenu();
    var port = t.closest && t.closest('.port.out');
    var hd = t.closest && t.closest('.cnode-hd');
    var card = t.closest && t.closest('.cnode');

    if (port) {
      e.preventDefault();
      var fromId = port.getAttribute('data-out');
      var p = stageToWorld(e.clientX - CS.stage.getBoundingClientRect().left,
        e.clientY - CS.stage.getBoundingClientRect().top);
      CS.link = { from: fromId, x: p.x, y: p.y };
      CS.drag = { mode: 'link' };
      $a('.cnode.gen', CS.world).forEach(function (el) { el.classList.add('linkable'); });
      paintLinks();
      return;
    }
    if (card) {
      // 点卡片任何位置都先选中：@ 插入、Delete 删除都靠它
      CS.sel = card.getAttribute('data-id');
      $a('.cnode', CS.world).forEach(function (el) { el.classList.toggle('sel', el === card); });
      var n0 = nodeById(CS.sel);
      if (!n0) return;
      // 只有拖标题栏才移动；点正文（输入框、下拉、按钮）保持原生行为
      if (hd && !(t.closest('button') || t.closest('select') || t.closest('textarea'))) {
        var sp = stageToWorld(e.clientX - CS.stage.getBoundingClientRect().left,
          e.clientY - CS.stage.getBoundingClientRect().top);
        CS.drag = { mode: 'node', id: n0.id, dx: sp.x - n0.x, dy: sp.y - n0.y };
      }
      return;
    }
    CS.drag = { mode: 'pan', sx: e.clientX, sy: e.clientY, ox: CS.view.x, oy: CS.view.y };
    CS.stage.classList.add('panning');
    CS.sel = null;
    $a('.cnode', CS.world).forEach(function (el) { el.classList.remove('sel'); });
    CS.scissor = null; paintScissor();
  }
  function onPointerMove(e) {
    if (!CS.drag) return;
    var rect = CS.stage.getBoundingClientRect();
    if (CS.drag.mode === 'pan') {
      CS.view.x = CS.drag.ox + (e.clientX - CS.drag.sx);
      CS.view.y = CS.drag.oy + (e.clientY - CS.drag.sy);
      applyView(); paintLinks();
    } else if (CS.drag.mode === 'node') {
      var n = nodeById(CS.drag.id); if (!n) return;
      var p = stageToWorld(e.clientX - rect.left, e.clientY - rect.top);
      n.x = Math.round(p.x - CS.drag.dx);
      n.y = Math.round(p.y - CS.drag.dy);
      var el = $q('.cnode[data-id="' + n.id + '"]', CS.world);
      if (el) { el.style.left = n.x + 'px'; el.style.top = n.y + 'px'; }
      paintLinks();
    } else if (CS.drag.mode === 'link' && CS.link) {
      var q = stageToWorld(e.clientX - rect.left, e.clientY - rect.top);
      CS.link.x = q.x; CS.link.y = q.y;
      paintLinks();
    }
  }
  function onPointerUp(e) {
    if (!CS.drag) return;
    var mode = CS.drag.mode;
    CS.stage.classList.remove('panning');
    if (mode === 'link' && CS.link) {
      $a('.cnode.gen', CS.world).forEach(function (el) { el.classList.remove('linkable'); });
      var target = document.elementFromPoint(e.clientX, e.clientY);
      var card = target && target.closest && target.closest('.cnode.gen');
      // 落在哪个上传位就接哪个槽；落在卡片其它地方让 connect 自动挑第一个空位
      var inPort = target && target.closest && target.closest('.port.in');
      var slot = (inPort && inPort.getAttribute('data-slot')) || '';
      var fromId = CS.link.from;
      CS.link = null;
      if (card) { connect(fromId, card.getAttribute('data-id'), slot); paintLinks(); }
      else {
        // 拖到空白处：弹同一个菜单，选完自动接到新节点左侧
        paintLinks();
        openGenMenu(e.clientX, e.clientY, fromId);
      }
    } else if (mode === 'node') {
      saveSoon();
    } else if (mode === 'pan') {
      saveSoon();          // 需求3：平移也要记住，切页面回来还停在原处
    }
    CS.drag = null;
  }
  function onDblClick(e) {
    if (e.target.closest('.cnode')) return;      // 节点内双击（改字）不算
    openGenMenu(e.clientX, e.clientY, null);
  }
  function onStageClick(e) {
    var path = e.target.closest && e.target.closest('path[data-edge]');
    if (path) {
      CS.scissor = +path.getAttribute('data-edge');
      paintLinks();
      return;
    }
    if (e.target.closest && (e.target.closest('.cvs-scissor') || e.target.closest('.cvs-menu'))) return;
    if (CS.scissor !== null) { CS.scissor = null; paintScissor(); }
    // 菜单只在「下一次按下」时关，不在这里关：拖线到空白是在 pointerup 里弹的菜单，
    // 紧跟着的那个 click 会把它立刻关掉
  }
  function onWheel(e) {
    e.preventDefault();
    var rect = CS.stage.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    var k = clamp(CS.view.k * (e.deltaY < 0 ? 1.12 : 0.89), LIM.minK, LIM.maxK);
    CS.view.x = mx - (mx - CS.view.x) * (k / CS.view.k);
    CS.view.y = my - (my - CS.view.y) * (k / CS.view.k);
    CS.view.k = k;
    applyView(); paintLinks();
    saveSoon();            // 需求3：滚轮缩放后立刻排队存盘（防抖 600ms）
  }
  function fitView() {
    var nodes = (CS.project && CS.project.nodes) || [];
    if (!nodes.length) { CS.view = { x: 60, y: 60, k: 1 }; applyView(); paintLinks(); return; }
    var minX = Math.min.apply(null, nodes.map(function (n) { return n.x; }));
    var minY = Math.min.apply(null, nodes.map(function (n) { return n.y; }));
    var maxX = Math.max.apply(null, nodes.map(function (n) { return n.x + (n.w || (isGen(n) ? GEN_W : W)); }));
    var maxY = Math.max.apply(null, nodes.map(function (n) { return n.y + 320; }));
    var w = CS.stage.clientWidth, h = CS.stage.clientHeight;
    var k = clamp(Math.min((w - 60) / Math.max(1, maxX - minX), (h - 60) / Math.max(1, maxY - minY)), LIM.minK, 1.2);
    CS.view.k = k;
    CS.view.x = (w - (maxX - minX) * k) / 2 - minX * k;
    CS.view.y = Math.max(20, (h - (maxY - minY) * k) / 2 - minY * k);
    applyView(); paintLinks();
    saveSoon();            // 需求3：适配后的视角也存下来
  }

  /* 元素级事件（在 host 上委托，重绘后不用重绑） */
  function onHostClick(e) {
    var t = e.target;
    var cardEl = t.closest && t.closest('.cnode');
    var cardId = cardEl && cardEl.getAttribute('data-id');
    var cink = t.closest && t.closest('[data-cink]');
    if (cink) {
      e.stopPropagation();
      var card0 = t.closest('.cnode'), n0 = card0 && nodeById(card0.getAttribute('data-id'));
      if (n0 && n0.ink !== cink.getAttribute('data-cink')) {
        n0.ink = cink.getAttribute('data-cink'); repaintNode(n0); saveSoon();
      }
      return;
    }
    // 子功能节点（设定图 / 故事板 / 视频 / 音频）的通用参数条：
    // data-cseg="版式|A" / "画法|line" / "grid|6" ……，值统一落到 n.cfg
    var csegEl = t.closest && t.closest('[data-cseg]');
    if (csegEl) {
      e.stopPropagation();
      var nc = nodeById(cardId);
      var rawSeg = csegEl.getAttribute('data-cseg') || '';
      var cut = rawSeg.indexOf('|');
      if (nc && cut > 0) {
        var cfgKey = rawSeg.slice(0, cut), cfgVal = rawSeg.slice(cut + 1);
        var cfgNow = cfgOf(nc);
        cfgNow[cfgKey] = cfgKey === 'grid' ? +cfgVal : cfgVal;
        // 换版式 / 画法 / 模式 / 参考方式后旧档位可能没了，回到新组合的第一档
        fixGrade(nc); repaintNode(nc); saveSoon();
      }
      return;
    }
    // 图片生成节点：结果卡 / 输入区的全部按钮都在这里接住，重绘后不用重绑
    var cgrade = t.closest && t.closest('[data-cgrade]');
    if (cgrade) {
      e.stopPropagation();
      var ng = nodeById(cardId);
      if (ng && ng.grade !== cgrade.getAttribute('data-cgrade')) {
        ng.grade = cgrade.getAttribute('data-cgrade'); repaintNode(ng); saveSoon();
      }
      return;
    }
    var del = t.closest && t.closest('[data-del]');
    if (del) { e.stopPropagation(); removeNode(del.getAttribute('data-del')); return; }
    var gen = t.closest && t.closest('[data-gen]');
    if (gen) {
      e.stopPropagation();
      var n1 = nodeById(gen.getAttribute('data-gen'));
      if (n1) generate(n1, gen);
      return;
    }
    // 结果卡上的动作：和创作台同一张卡，只是 data-* 换成画布自己的名字（重绘后靠委托）
    var again = t.closest && t.closest('[data-cagain]');
    if (again) {
      e.stopPropagation();
      var ajid = again.getAttribute('data-cagain');
      api('/api/tasks/' + encodeURIComponent(ajid) + '/again', { body: {} }).then(function (res) {
        if (!res.ok) { toastBlocked('这次没能重新生成', res.reason || '稍后再试'); return; }
        pull(); tip('已排进这一批的队列');
      });
      return;
    }
    var zoom = t.closest && t.closest('[data-czoom]');
    if (zoom) {
      e.stopPropagation();
      if (typeof openZoom === 'function') openZoom(zoom.getAttribute('data-czoom'));
      return;
    }
    var dlr = t.closest && t.closest('[data-cdl]');
    if (dlr) {
      e.stopPropagation();
      window.location.href = '/api/download?run=' + encodeURIComponent(dlr.getAttribute('data-cdl')) + '&i=0';
      return;
    }
    var cpy = t.closest && t.closest('[data-ccopy]');
    if (cpy) {
      e.stopPropagation();
      if (typeof copyImageToClipboard === 'function') copyImageToClipboard(imgUrl(cpy.getAttribute('data-ccopy'), 0), '成品图');
      return;
    }
    // 资产节点的下载 / 复制：素材没有 run，直接拿 /api/media?ref= 这一份
    var dlref = t.closest && t.closest('[data-dlref]');
    if (dlref) {
      e.stopPropagation();
      downloadAsset(nodeById(dlref.getAttribute('data-dlref')));
      return;
    }
    var cpref = t.closest && t.closest('[data-copyref]');
    if (cpref) {
      e.stopPropagation();
      var cn = nodeById(cpref.getAttribute('data-copyref'));
      if (cn && cn.refId && typeof copyImageToClipboard === 'function') copyImageToClipboard(refUrl(cn.refId), '素材图');
      return;
    }
    var q = t.closest && t.closest('[data-cqueue]');
    if (q) {
      e.stopPropagation();
      var qjid = q.getAttribute('data-cqueue');
      var qn = ((CS.project && CS.project.nodes) || []).filter(function (x) { return x.jobId === qjid; })[0];
      if (qn) openQueueForNode(qn);
      return;
    }
    var qc = t.closest && t.closest('[data-cqcancel]');
    if (qc) {
      e.stopPropagation();
      api('/api/runs/' + encodeURIComponent(qc.getAttribute('data-cqcancel')) + '/cancel', { body: {} })
        .then(function () { pull(); toast('已撤掉这一档'); });
      return;
    }
    var retry = t.closest && t.closest('[data-cretry]');
    if (retry) {
      e.stopPropagation();
      api('/api/tasks/' + encodeURIComponent(retry.getAttribute('data-cretry')) + '/retry', { body: {} })
        .then(function (res) {
          if (!res.ok) { toastBlocked('没能重试', res.reason || ''); return; }
          pull(); tip('已重新提交');
        });
      return;
    }
    var raw = t.closest && t.closest('[data-craw]');
    if (raw) {
      e.stopPropagation();
      var box = $q('#err-' + raw.getAttribute('data-craw'), CS.world); if (!box) return;
      box.classList.toggle('open');
      raw.textContent = box.classList.contains('open') ? '收起详情' : '展开详情';
      return;
    }
    var zm = t.closest && t.closest('[data-zoommat]');
    if (zm) {
      e.stopPropagation();
      var zn = nodeById(zm.getAttribute('data-zoommat'));
      if (zn && zn.refId) zoomMaterial(zn);
      return;
    }
    // 参考缩略图上的 ✕：断开这条连线（素材节点本身不动）
    var rmref = t.closest && t.closest('[data-crmref]');
    if (rmref) {
      e.stopPropagation();
      var fromId = rmref.getAttribute('data-crmref');
      var toId = cardId;
      CS.project.edges = CS.project.edges.filter(function (x) { return !(x.from === fromId && x.to === toId); });
      paintAll(); saveSoon(); tip('已断开这张参考');
      return;
    }
    var pick = t.closest && t.closest('[data-pick]');
    if (pick) {
      e.stopPropagation();
      var pickCard = pick.closest('.cnode');
      openPicker(pick.getAttribute('data-pick'), null, pickCard ? pickCard.getAttribute('data-id') : null);
      return;
    }
    var up = t.closest && t.closest('[data-up]');
    if (up) {
      e.stopPropagation();
      var kind = up.getAttribute('data-up');
      var world = stageToWorld(CS.stage.clientWidth / 2, 140);
      openPicker(kind, world);
      return;
    }
    var at = t.closest && t.closest('[data-at]');
    if (at) {
      e.stopPropagation();
      var genCard = at.closest('.cnode');
      insertToken(genCard ? genCard.getAttribute('data-id') : CS.sel, at.getAttribute('data-at'));
      return;
    }
    // 点到的常是按钮内部的 <span>/文字，必须用 closest 找按钮本身，不能比 e.target.id
    var projBtn = t.closest && t.closest('#cvsProjBtn');
    if (projBtn) { e.stopPropagation(); toggleProjMenu(projBtn); return; }
    var newBtn = t.closest && t.closest('#cvsNewProj');
    if (newBtn) { e.stopPropagation(); closeProjMenu(); askNewProject(); return; }
    var addBtn = t.closest && t.closest('#cvsAddNode');
    if (addBtn) { closeProjMenu(); var rc = addBtn.getBoundingClientRect(); openGenMenu(rc.left, rc.bottom + 6, null); return; }
    var emptyBtn = t.closest && t.closest('#cvsEmptyAdd');
    if (emptyBtn) {
      var erc = emptyBtn.getBoundingClientRect();
      openGenMenu(erc.left + erc.width / 2, erc.bottom + 6, null);
      return;
    }
    var fitBtn = t.closest && t.closest('#cvsFit');
    if (fitBtn) { fitView(); return; }
    var zo = t.closest && t.closest('#cvsZo'), zi = t.closest && t.closest('#cvsZi');
    if (zo || zi) {
      var k = clamp(CS.view.k * (zi ? 1.15 : 0.87), LIM.minK, LIM.maxK);
      CS.view.k = k; applyView(); paintLinks(); saveSoon(); return;
    }
  }
  function zoomMaterial(n) {
    var media = kindMeta(kindOf(n)).media || 'image';
    var src = refUrl(n.refId);
    var body = media === 'video'
      ? '<video src="' + src + '" controls autoplay playsinline></video>'
      : '<img src="' + src + '" alt="素材图">';
    var box = document.createElement('div');
    box.className = 'cvs-lightbox';
    box.innerHTML = '<div class="lb-box"><button class="lb-x" title="关闭">✕</button>' + body + '</div>';
    box.onclick = function (e) { if (!e.target.closest('.lb-box') || e.target.closest('.lb-x')) box.remove(); };
    document.body.appendChild(box);
    $q('.lb-x', box).onclick = function (e) { e.stopPropagation(); box.remove(); };
    document.addEventListener('keydown', function esc(e) {
      if (e.key === 'Escape') { box.remove(); document.removeEventListener('keydown', esc); }
    });
  }
  /* 素材下载：没有 run 记录，取 /api/media?ref= 的原始字节，用节点名（或补个扩展名）存下来 */
  async function downloadAsset(n) {
    if (!n || !n.refId) return;
    try {
      var res = await fetch(refUrl(n.refId));
      if (!res.ok) { toast('下载失败'); return; }
      var blob = await res.blob();
      var name = n.name || ('素材-' + n.id);
      if (!/\.[a-z0-9]{2,5}$/i.test(name)) {
        var ext = (blob.type.split('/')[1] || 'bin').replace('mpeg', 'mp3').replace('quicktime', 'mov');
        name += '.' + ext;
      }
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
    } catch (err) { toast('下载失败'); }
  }
  function insertToken(genId, nodeId) {
    var card = genId && $q('.cnode[data-id="' + genId + '"]', CS.world);
    if (!card) { toast('先点一下要写入的生成节点'); return; }
    // 输入块是创作台同款 contenteditable 编辑器，按选区插纯文本就行
    var ed = $q('.editor[data-prompt]', card);
    var g = nodeById(genId), u = nodeById(nodeId);
    if (!ed || !g || !u) return;
    var token = tokenOf(g, u);
    ed.focus();
    var sel = window.getSelection();
    if (sel && sel.rangeCount && ed.contains(sel.anchorNode)) {
      var range = sel.getRangeAt(0);
      range.deleteContents();
      range.insertNode(document.createTextNode(token));
      range.collapse(false);
      sel.removeAllRanges(); sel.addRange(range);
    } else {
      ed.appendChild(document.createTextNode(token));
    }
    g.prompt = GenUI.editorText(ed, nodeRefs(g));
    CS.sel = g.id;
    saveSoon();
    tip('已插入 ' + token);
  }
  function onHostInput(e) {
    var t = e.target;
    var card = t.closest && t.closest('.cnode');
    if (!card) return;
    var n = nodeById(card.getAttribute('data-id'));
    if (!n) return;
    if (t.matches('.editor[data-prompt]')) {
      n.prompt = GenUI.editorText(t, nodeRefs(n));
      // 和创作台一致：没写字的时候发送按钮是灰的
      var sb = $q('.sendbtn', card);
      if (sb) sb.disabled = !String(n.prompt || '').trim();
      saveSoon(); return;
    }
    if (t.matches('[data-cstyle]')) { n.styleText = t.value; saveSoon(); return; }
    // 通用参数条里的文本框 / 多行框（风格句、歌词）：直接写 n.cfg，不重绘，光标不跳
    if (t.matches('[data-ctext]')) { cfgOf(n)[t.getAttribute('data-ctext')] = t.value; saveSoon(); return; }
    if (t.matches('[data-cextra]')) { cfgOf(n)[t.getAttribute('data-cextra')] = t.value; saveSoon(); return; }
    if (t.matches('[data-cdur]')) {
      // 时长滑条和数值框双向同步：数值框精度 0.01，滑条 0.1（和创作台一致）
      var v = Math.min(15, Math.max(4, parseFloat(t.value)));
      if (isNaN(v)) return;
      n.video = n.video || {};
      n.video.duration = v;
      var isRange = t.getAttribute('data-cdur') === 'range';
      var num = $q('[data-cdur="num"]', card), rng = $q('[data-cdur="range"]', card);
      if (isRange && num) num.value = v.toFixed(2);
      if (!isRange && rng) rng.value = (Math.round(v * 10) / 10).toFixed(1);
      saveSoon(); return;
    }
  }
  function onHostChange(e) {
    var t = e.target;
    if (t.matches('[data-cdur="num"]')) { onHostInput(e); return; }
    if (t.matches('[data-cstylesel]')) {
      var card = t.closest && t.closest('.cnode'), n = card && nodeById(card.getAttribute('data-id'));
      if (n) {
        n.style = t.value;
        n.styleCustom = t.value === '__custom';
        repaintNode(n); saveSoon();
      }
      return;
    }
    // 图生图节点的「线稿风格句」下拉（GenUI inkRow 的 data-cline）：和创作台 imgLine.onchange
    // 一样写回节点。之前画布这边没处理，选 G1–G4 存不住、提交永远是默认线稿。
    if (t.matches('[data-cline]')) {
      var cardL = t.closest && t.closest('.cnode'), nL = cardL && nodeById(cardL.getAttribute('data-id'));
      if (nL) { nL.lineStyle = t.value; repaintNode(nL); saveSoon(); }
      return;
    }
    // 子功能节点的下拉（风格预设 / 线稿风格句）
    if (t.matches('[data-csel]') && !t.matches('[data-cdur="num"]')) {
      var card3 = t.closest && t.closest('.cnode');
      var n3 = card3 && nodeById(card3.getAttribute('data-id'));
      if (!n3) return;
      var key3 = t.getAttribute('data-csel'), c3 = cfgOf(n3);
      if (key3 === 'style') {
        // 选「自定义…」只是打开风格句输入框，预设名留着做兜底
        if (t.value === '__custom') c3.styleCustom = true;
        else { c3.styleCustom = false; c3.style = t.value; }
      } else {
        c3[key3] = t.value;
      }
      fixGrade(n3); repaintNode(n3); saveSoon();
      return;
    }
    if (t.id === 'cvsFile') {
      var files = Array.prototype.slice.call(t.files || []);
      if (files.length) uploadFiles(files);
    }
  }
  /* Chromium 在标题栏上按下再拖动时会顺手起一个原生拖拽（拖拽源是 .cnode-hd），
     它一触发就 pointercancel，节点拖到一半就断。画布里不需要任何原生拖拽，一律拦掉。 */
  function onDragStart(e) { e.preventDefault(); }

  /* 本地文件拖进画布：按类型直接上传并建资产节点 */
  var dropHint = null;
  function showDropHint(on) {
    if (on) {
      if (dropHint || !CS.stage) return;
      dropHint = document.createElement('div');
      dropHint.className = 'cvs-drop';
      dropHint.innerHTML = '<b>松手就把素材放上画布</b><span>支持图片 / 视频 / 音频，按文件类型自动建节点</span>';
      CS.stage.appendChild(dropHint);
    } else if (dropHint) { dropHint.remove(); dropHint = null; }
  }
  function dragHasFiles(e) {
    var dt = e.dataTransfer;
    if (!dt) return false;
    var types = dt.types ? Array.prototype.slice.call(dt.types) : [];
    return types.indexOf('Files') >= 0;
  }
  function onStageDragOver(e) {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    showDropHint(true);
  }
  function onStageDragLeave(e) {
    if (dropHint && !(e.relatedTarget && CS.stage.contains(e.relatedTarget))) showDropHint(false);
  }
  async function onStageDrop(e) {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    showDropHint(false);
    var files = Array.prototype.slice.call((e.dataTransfer && e.dataTransfer.files) || []);
    if (!files.length) return;
    var rect = CS.stage.getBoundingClientRect();
    await dropFiles(files, stageToWorld(e.clientX - rect.left, e.clientY - rect.top));
  }

  function isTyping(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'input' || tag === 'select') return true;
    if (el.isContentEditable) return true;
    return !!(el.closest && el.closest('.editor,[contenteditable="true"]'));
  }
  function onKeyDown(e) {
    if (!CS.mounted) return;
    // 光标在节点里的输入框 / 可编辑区时，退格键归输入框，不能删节点
    if (isTyping(e.target) || isTyping(document.activeElement)) return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && CS.sel) { removeNode(CS.sel); e.preventDefault(); }
    if (e.key === 'Escape') { closeMenu(); closeProjMenu(); CS.scissor = null; paintScissor(); }
  }

  /* ------------------------------------------------------------ 项目：下拉 / 新建 / 删除 */
  function closeProjMenu() {
    if (CS.projMenu) { CS.projMenu.remove(); CS.projMenu = null; }
  }
  function toggleProjMenu(anchor) {
    if (CS.projMenu) { closeProjMenu(); return; }
    closeMenu();                                   // 先关掉「生成节点」菜单，两个菜单不同时开
    var menu = document.createElement('div');
    menu.className = 'cvs-projmenu';
    menu.innerHTML = (CS.projects || []).map(function (p) {
      var cur = p.id === CS.pid;
      return '<div class="pm-row' + (cur ? ' on' : '') + '" data-popen="' + esc(p.id) + '">'
        + '<span class="pm-em">🎬</span><span class="pm-name">' + esc(p.name) + '</span>'
        + '<button class="pm-del" title="删除画布" data-pdel="' + esc(p.id) + '" data-name="' + esc(p.name) + '">✕</button>'
        + '</div>';
    }).join('') || '<div class="pm-empty">还没有画布</div>';
    document.body.appendChild(menu);
    var r = anchor.getBoundingClientRect();
    menu.style.left = Math.max(6, Math.min(r.left, window.innerWidth - menu.offsetWidth - 6)) + 'px';
    menu.style.top = (r.bottom + 6) + 'px';
    CS.projMenu = menu;

    $a('[data-popen]', menu).forEach(function (row) {
      row.onclick = function (ev) {
        if (ev.target.closest && ev.target.closest('[data-pdel]')) return;  // 删除键单独处理
        ev.stopPropagation();
        closeProjMenu();
        var pid = row.getAttribute('data-popen');
        if (pid !== CS.pid) openProject(pid);
      };
    });
    $a('[data-pdel]', menu).forEach(function (b) {
      b.onclick = function (ev) {
        ev.stopPropagation();
        var pid = b.getAttribute('data-pdel'), name = b.getAttribute('data-name') || '这个画布';
        closeProjMenu();
        askDeleteProject(pid, name);
      };
    });
  }
  // 点击页面其它地方 / Esc 关闭项目下拉
  function onDocClickProj(e) {
    if (!CS.projMenu) return;
    if (e.target.closest && (e.target.closest('.cvs-projmenu') || e.target.closest('#cvsProjBtn'))) return;
    closeProjMenu();
  }

  function askNewProject() {
    if (typeof openModal !== 'function') { createProject(); return; }
    openModal('<div class="m-head"><h3>新建画布</h3><button class="btn ghost sm" data-close>✕</button></div>'
      + '<div class="m-body"><div class="field"><label>画布名称</label>'
      + '<input class="input" id="cvNewName" value="未命名画布" maxlength="60" placeholder="给画布起个名字"></div></div>'
      + '<div class="m-foot"><button class="btn sm" data-close>取消</button>'
      + '<button class="btn sm primary" id="cvNewGo">新建并打开</button></div>');
    var inp = $q('#cvNewName');
    if (inp) { inp.focus(); inp.select(); }
    var go = $q('#cvNewGo');
    if (go) go.onclick = async function () {
      var name = ((inp && inp.value) || '未命名画布').trim().slice(0, 60) || '未命名画布';
      var res = await api('/api/canvas/project', { body: { name: name } });
      if (!res.ok || !res.data) { toast(res.reason || '新建失败'); return; }
      if (typeof closeModal === 'function') closeModal();
      CS.projects = (CS.projects || []).concat([{ id: res.data.id, name: res.data.name }]);
      await openProject(res.data.id);
      toast('已新建画布：' + res.data.name);
    };
  }

  function askDeleteProject(pid, name) {
    if (typeof openModal !== 'function') return;
    openModal('<div class="m-head"><h3>删除「' + esc(name) + '」？</h3><button class="btn ghost sm" data-close>✕</button></div>'
      + '<div class="m-body"><div class="tiny" style="line-height:1.9">删掉这张画布的节点、连线和本地配置文件，<b>不可恢复</b>；已生成的成品文件不删，仍在成果目录和任务历史里。</div></div>'
      + '<div class="m-foot"><button class="btn sm" data-close>取消</button>'
      + '<button class="btn sm danger" id="cvDelGo">删除画布</button></div>');
    var go = $q('#cvDelGo');
    if (go) go.onclick = async function () {
      var res = await api('/api/canvas/project/' + encodeURIComponent(pid), { method: 'DELETE' });
      if (!res.ok) { toast(res.reason || '没删成'); return; }
      if (typeof closeModal === 'function') closeModal();
      var wasCurrent = pid === CS.pid;
      CS.projects = (CS.projects || []).filter(function (p) { return p.id !== pid; });
      if (wasCurrent) {
        var next = (CS.projects[0] || {}).id;
        if (next) await openProject(next);
        else await createProject();                 // 删光了就自动建一张空的，画布不会卡死
      } else updateBar();
      toast('画布已删除');
    };
  }

  /* ------------------------------------------------------------ 项目 */
  async function loadProjects() {
    var res = await api('/api/canvas');
    if (!res.ok || !res.data) return false;
    CS.projects = res.data.projects || [];
    CS.funcs = res.data.funcs || [];
    CS.kinds = res.data.nodeKinds || [];
    return true;
  }
  async function openProject(pid) {
    var res = await api('/api/canvas/project/' + encodeURIComponent(pid));
    if (!res.ok || !res.data) { toast(res.reason || '这个画布打不开'); return false; }
    CS.pid = pid;
    CS.project = res.data;
    CS.project.nodes = (res.data.nodes || []).map(function (n) {
      // 老画布（1.03 的 stage 节点）统一收成素材/生成两类，别让它们变成空白卡
      if (!n.kind) n.kind = n.refId ? 'image' : 'imagegen';
      if (n.kind === 'imagegen') {
        loadImgExtras(n);
        n.mode = 'i2i';                 // 图片生成节点固定就是图生图，不提供切换
        n.styleCustom = true;
      }
      if (n.kind === 'imagegen' || n.kind === 'videogen') n.w = n.w || W;
      return n;
    });
    CS.project.edges = res.data.edges || [];
    migrateProject(CS.project);
    var v = res.data.view || {};
    CS.hasView = !!(res.data.view && (Number(v.k) !== 1 || Number(v.x) || Number(v.y)));
    CS.view = { x: Number(v.x) || 60, y: Number(v.y) || 60, k: clamp(Number(v.k) || 1, LIM.minK, LIM.maxK) };
    CS.sel = null; CS.scissor = null;
    try { localStorage.setItem('cb.canvas.pid', pid); } catch (e) { }
    applyView(); paintAll();
    return true;
  }
  async function createProject() {
    var res = await api('/api/canvas/project', { body: { name: '未命名画布' } });
    if (!res.ok || !res.data) { toast(res.reason || '新建失败'); return false; }
    CS.projects = (CS.projects || []).concat([{ id: res.data.id, name: res.data.name }]);
    return await openProject(res.data.id);
  }

  /* 旧画布兼容：
     - 场景 / 道具的「文生图·图生图·扩图」原来是同一个节点靠 cfg.mode 切换，现在拆成三个节点
     - 角色设定图原来是「文生图 + 一个图生图节点内切参考」，现在和创作台一致拆成三项：
       老图生图节点按它保存的 cfg.ref 归到锁定 / 只借画风；老「文生图」节点接了线的也算图生图
     - 多连接点节点（场景图生图/扩图 4 位、首尾帧 2 位）的老连线没有槽位，按顺序补 */
  function migrateProject(p) {
    var edges = p.edges || [];
    var hollow = ['文生图', '图生图', '图片生成', '视频生成', '角色设定图', '角色设定图-图生图',
      '场景设定图', '道具设定图',
      '文生视频', '首尾帧生视频', '参考生视频', '故事板生视频', '音乐生成', '参考生成音乐', '语音生成', '声音克隆'];
    p.nodes.forEach(function (n) {
      var c = n.cfg || {};
      if (n.kind === 'sheetscene' && c.mode === 'i2i') n.kind = 'sheetscene_i2i';
      if (n.kind === 'sheetscene' && c.mode === 'expand') n.kind = 'sheetscene_expand';
      if (n.kind === 'sheetprop' && c.mode === 'i2i') n.kind = 'sheetprop_i2i';
      // 老图生图节点 → 按原参考方式归到两个新节点之一（默认锁定同角色）
      if (n.kind === 'sheetchar_i2i') n.kind = c.ref === 'style' ? 'sheetchar_style' : 'sheetchar_lock';
      // 更老的版本：一个「角色设定图」节点靠连线区分文生图 / 图生图
      if (n.kind === 'sheetchar' && edges.some(function (e) { return e.to === n.id; })) {
        n.kind = c.ref === 'style' ? 'sheetchar_style' : 'sheetchar_lock';
      }
      if (c && c.mode) delete c.mode;
      var f = funcFor(n.kind);
      if (f.name && (!n.title || hollow.indexOf(n.title) >= 0)) n.title = f.name;
    });
    edges.forEach(function (e) {
      var to = p.nodes.filter(function (x) { return x.id === e.to; })[0];
      if (!to) return;
      var kind = kindOf(to);
      if (!isMultiPort(kind)) { if (e.slot) delete e.slot; return; }
      if (e.slot && slotIndexOf(kind, e.slot) < portsOf(kind).length) return;
      var used = edges.filter(function (x) { return x.to === e.to && x !== e; })
        .map(function (x) { return x.slot; });
      var free = portsOf(kind).filter(function (pp) { return used.indexOf(pp.slot) < 0; })[0];
      if (free) e.slot = free.slot;
    });
  }

  /* ------------------------------------------------------------ 挂载 */
  async function mount(host) {
    CS.host = host;
    CS.mounted = false;
    host.innerHTML = shellHTML();
    CS.stage = $q('#cvsStage');
    CS.world = $q('#cvsWorld');
    CS.svg = $q('#cvsLinks');

    CS.stage.addEventListener('pointerdown', onPointerDown);
    CS.stage.addEventListener('pointermove', onPointerMove);
    CS.stage.addEventListener('pointerup', onPointerUp);
    CS.stage.addEventListener('pointercancel', onPointerUp);
    CS.stage.addEventListener('wheel', onWheel, { passive: false });
    CS.stage.addEventListener('dblclick', onDblClick);
    CS.stage.addEventListener('click', onStageClick);
    CS.stage.addEventListener('dragstart', onDragStart);
    CS.stage.addEventListener('dragover', onStageDragOver);
    CS.stage.addEventListener('dragleave', onStageDragLeave);
    CS.stage.addEventListener('drop', onStageDrop);
    host.addEventListener('click', onHostClick);
    host.addEventListener('input', onHostInput);
    host.addEventListener('change', onHostChange);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('click', onDocClickProj);

    var ok = await loadProjects();
    if (!ok) { toast('画布打不开'); CS.mounted = true; return; }
    var saved = '';
    try { saved = localStorage.getItem('cb.canvas.pid') || ''; } catch (e) { }
    var pid = (CS.projects.some(function (p) { return p.id === saved; }) ? saved : '') || (CS.projects[0] || {}).id || '';
    var opened = pid ? await openProject(pid) : false;
    if (!opened) opened = await createProject();
    CS.mounted = true;
    if (opened) {
      // 需求3：缩放/平移状态存服务端，切到别的页面再回来还停在原来的视角；
      // 只有第一次打开（服务端还没有视角）才自动适配一次
      if (window.innerWidth <= 820 && !CS.hasView) {
        var first = (CS.project.nodes || [])[0];
        CS.view.k = 0.82;
        CS.view.x = 14 - (first ? first.x * 0.82 : 0);
        CS.view.y = 48 - (first ? first.y * 0.82 : 0);
        applyView(); paintLinks(); saveSoon();
      } else if (!CS.hasView) fitView();
    }
  }
  function unmount() {
    // 需求3：离开画布前把还没落盘的视角 / 连线先存了，别等 600ms 的防抖
    if (CS.dirty) flushSave();
    if (CS.host) {
      CS.host.removeEventListener('click', onHostClick);
      CS.host.removeEventListener('input', onHostInput);
      CS.host.removeEventListener('change', onHostChange);
    }
    if (CS.stage) {
      CS.stage.removeEventListener('pointerdown', onPointerDown);
      CS.stage.removeEventListener('pointermove', onPointerMove);
      CS.stage.removeEventListener('pointerup', onPointerUp);
      CS.stage.removeEventListener('pointercancel', onPointerUp);
      CS.stage.removeEventListener('wheel', onWheel);
      CS.stage.removeEventListener('dblclick', onDblClick);
      CS.stage.removeEventListener('click', onStageClick);
      CS.stage.removeEventListener('dragstart', onDragStart);
      CS.stage.removeEventListener('dragover', onStageDragOver);
      CS.stage.removeEventListener('dragleave', onStageDragLeave);
      CS.stage.removeEventListener('drop', onStageDrop);
    }
    showDropHint(false);
    document.removeEventListener('keydown', onKeyDown);
    document.removeEventListener('click', onDocClickProj);
    closeProjMenu();
    clearTimeout(CS.saveTimer);
    CS.mounted = false; CS.drag = null; CS.link = null;
  }
  /* 上游节点的成品变了（典型：「加入队列生成」新出了一张），下游节点里的参考缩略图 /
     芯片要跟着换成新的那张 —— 否则显示旧图、提交新图，人会以为参考错了。 */
  function syncRefThumbs() {
    (CS.project.nodes || []).forEach(function (n) {
      if (!isGen(n)) return;
      var el = $q('.cnode[data-id="' + n.id + '"]', CS.world);
      if (!el) return;
      upstreamOf(n).forEach(function (u) {
        var d = doneRunsOf(u), last = d.length ? d[d.length - 1] : null;
        var src = last ? imgUrl(last.id, 0) : (u.refId ? refUrl(u.refId) : '');
        if (!src) return;
        $a('[data-refthumb="' + u.id + '"] img,[data-refthumb="' + u.id + '"] video', el).forEach(function (m) {
          if (m.getAttribute('src') !== src) m.setAttribute('src', src);
        });
        $a('[data-chipref="' + u.id + '"] img', el).forEach(function (m) {
          if (m.getAttribute('src') !== src) m.setAttribute('src', src);
        });
        $a('[data-refn="' + u.id + '"]', el).forEach(function (b) {
          if (d.length > 1) {
            b.textContent = d.length + '/' + d.length;
            b.title = '用的是这张节点的最新成品（第 ' + d.length + ' 张 / 共 ' + d.length + ' 张）';
            b.style.display = '';
          } else {
            b.style.display = 'none';
          }
        });
      });
    });
  }

  function refresh() {
    if (!CS.mounted || !CS.project) return;
    // 只刷新结果块，输入区（正在打字的编辑器、选中的档位）保持不动
    (CS.project.nodes || []).forEach(function (n) {
      if (!isGen(n)) return;
      var el = $q('.cnode[data-id="' + n.id + '"]', CS.world);
      if (!el) return;
      var slot = $q('.cres', el);
      if (!slot) return;
      var job = jobOf(n.jobId);
      if (job) {
        (job.runs || []).forEach(function (r) {
          var pct = Math.round(r.pct || 0), total = r.total || 8;
          $a('[data-jobbar="' + r.id + '"]', slot).forEach(function (e) { e.style.width = pct + '%'; });
          $a('[data-jobpct="' + r.id + '"]', slot).forEach(function (e) { e.textContent = pct + '%'; });
          $a('[data-jobstep="' + r.id + '"]', slot).forEach(function (e) {
            e.textContent = r.state === 'queued' ? 'ComfyUI 马上开始'
              : '正在采样 · ' + Math.round(pct * total / 100) + '/' + total + ' 步';
          });
          $a('[data-jobelapsed="' + r.id + '"]', slot).forEach(function (e) {
            e.textContent = r.state === 'queued' ? '等待显卡空闲' : '已用 ' + elapsed(r.t0);
          });
          $a('[data-runbar="' + r.id + '"]', slot).forEach(function (e) { e.style.width = pct + '%'; });
          $a('[data-runpct="' + r.id + '"]', slot).forEach(function (e) { e.textContent = pct + '%'; });
          $a('[data-runstep="' + r.id + '"]', slot).forEach(function (e) {
            e.textContent = r.state === 'running' ? '正在采样…' : '排队中 · 前面还有 ' + queueAhead(r) + ' 个';
          });
        });
      }
      // 结构变了（跑完 / 失败 / 队列数变了）才重绘这一块
      var sig = job
        ? job.id + '|' + (job.runs || []).map(function (r) { return r.state + ':' + (r.files || []).length; }).join(',')
        : 'none';
      if (slot.getAttribute('data-sig') !== sig) {
        slot.setAttribute('data-sig', sig);
        slot.innerHTML = resultHTML(n);
      }
    });
    syncRefThumbs();
    paintLinks();
  }

  /* 需求5：右栏「定位」对画布任务要切到画布、打开项目、把节点挪到视野中央 */
  async function focusNode(pid, nodeId) {
    if (!CS.mounted) {                       // 刚切到画布时 mount 还在异步加载，等它一下
      for (var i = 0; i < 40 && !CS.mounted; i++) {
        await new Promise(function (r) { setTimeout(r, 100); });
      }
    }
    if (!CS.mounted) return false;
    if (pid && pid !== CS.pid) { await openProject(pid); }
    var n = nodeById(nodeId);
    if (!n) return false;
    var w = n.w || (isGen(n) ? GEN_W : W);
    CS.view.k = clamp(CS.view.k || 1, LIM.minK, LIM.maxK);
    CS.view.x = CS.stage.clientWidth / 2 - (n.x + w / 2) * CS.view.k;
    CS.view.y = CS.stage.clientHeight / 2 - (n.y + 180) * CS.view.k;
    applyView(); paintLinks(); saveSoon();
    var el = $q('.cnode[data-id="' + nodeId + '"]', CS.world);
    if (el) {
      el.classList.add('focus-flash');
      el.style.outline = '2px solid #2f6bff';
      setTimeout(function () { el.classList.remove('focus-flash'); el.style.outline = ''; }, 2400);
    }
    return true;
  }

  window.CanvasModule = {
    mount: function (host) { if (CS.mounted) unmount(); return mount(host); },
    unmount: unmount,
    refresh: refresh,
    focusNode: focusNode,
    isMounted: function () { return !!(CS.mounted && CS.host && document.body.contains(CS.host)); },
    flush: function () { if (CS.dirty) return flushSave(); },
  };
})();
