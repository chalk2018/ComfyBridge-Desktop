const CV_VIDEO_MODES = ['t2v', 'fl2v', 'ref2v', 'board'];
const CV_AUDIO_MODES = ['music', 'cover', 'tts', 'clone'];

function cvLoadAdmin(force) {
  if (S.cvAdmin && !force) return;
  S.cvAdmin = { loading: true };
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
    S.cvAdmin = A;
    if (UI.route === 'admin' && UI.adminPage === 'canvas') render();
  }).catch(() => { S.cvAdmin = null; toast('读取画布配置失败'); });
}

function cvVName(id) {
  const m = ((S.cvAdmin || {}).video || {}).modes || [];
  return (m.find(x => x.id === id) || {}).name || id;
}
function cvAName(id) {
  const m = ((S.cvAdmin || {}).audio || {}).modes || [];
  return (m.find(x => x.id === id) || {}).name || id;
}

function pageCanvasAdmin() {
  cvLoadAdmin();
  const A = S.cvAdmin;
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

  /* ---- ① 两个生成子功能 ---- */
  const funcRow = (f, title, desc, em) => `<div class="cv-func">
      <div class="cv-func-hd"><span class="em">${em}</span><b>${title}</b>
        <span class="tiny muted">${desc}</span></div>
      <div class="cv-func-bd">
        <label class="cvf"><span>显示名称</span><input class="input" data-cvf-name="${f.id}" value="${esc(f.name || '')}"></label>
        <label class="cvf grow"><span>绑定工作流</span><select class="input" data-cvf-wf="${f.id}">${wfOpts(f.wf)}</select></label>
        <button class="btn sm primary" data-cvf-save="${f.id}">保存</button>
      </div>
      <div class="tiny muted" style="margin-top:7px">节点上可选的档位：${(f.grades || []).map(g => esc(g.name)).join(' / ') || '—'}</div>
    </div>`;

  const fimg = (A.funcs || []).find(f => f.id === 'image') || { id: 'image', name: '图片生成', wf: '', grades: [] };
  const fvid = (A.funcs || []).find(f => f.id === 'video') || { id: 'video', name: '视频生成', wf: '', grades: [] };

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

  /* ---- ④ 画布项目 ---- */
  const ps = (A.projects || []).map(p => `<div class="cv-row">
      <span class="em">🎬</span>
      <div class="t"><b>${esc(p.name)}</b><span>${p.nodes} 个节点 · ${p.edges} 条连线 · 改于 ${fmtWhen(p.updated)}</span></div>
      <button class="btn sm" data-cvopen="${esc(p.id)}">打开</button>
      <button class="btn sm danger" data-cvdel="${esc(p.id)}" data-name="${esc(p.name)}">删除</button>
    </div>`).join('') || '<div class="tiny muted" style="padding:6px 0">还没有画布，点下面新建一个</div>';

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
      <h3>生成子功能<span class="tiny muted">各绑一个工作流</span></h3>
      ${funcRow(fimg, '图片生成', '画布上的图片节点，出图可继续往右接', '🖼')}
      ${funcRow(fvid, '视频生成', '画布上的视频节点，接素材当参考或纯提示词出片', '🎬')}
    </div>

    <div class="cv-card">
      <h3>视频生成档位<span class="tiny muted">${CV_VIDEO_MODES.filter(m => A.vGrades[m]).length} 个模式</span></h3>
      <div class="tiny muted" style="margin:-4px 0 10px;line-height:1.8">
        档位同时给「视频生成」创作台和画布的视频节点用。改比例/分辨率/步数就是改这一档的出片规格。
      </div>
      ${CV_VIDEO_MODES.map(vTable).join('')}
    </div>

    <div class="cv-card">
      <h3>音频生成档位<span class="tiny muted">${CV_AUDIO_MODES.filter(m => A.aGrades[m]).length} 个功能</span></h3>
      <div class="tiny muted" style="margin:-4px 0 10px;line-height:1.8">
        音乐/参考生成音乐按「时长」分档；语音/克隆按「音色参数」分档。
      </div>
      ${CV_AUDIO_MODES.map(aTable).join('')}
    </div>

    <div class="cv-card">
      <h3>画布项目</h3>
      ${ps}
      <div style="margin-top:10px"><button class="btn sm primary" data-cvnew>＋ 新建画布</button></div>
    </div>
  </div>`;
}

function bindCanvasAdmin() {
  const A = S.cvAdmin;
  if (!A || A.loading) return;

  // —— 子功能：保存 ——
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

  // —— 画布项目 ——
  $$('[data-cvopen]').forEach(el => el.onclick = async () => {
    try { localStorage.setItem('cb.canvas.pid', el.dataset.cvopen); } catch (e) { /* 隐私模式下写不进去也无妨 */ }
    UI.route = 'canvas';
    UI._navPending = true;
    await saveDraft({ route: 'canvas' });
    UI._navPending = false;
    render();
  });
  $$('[data-cvnew]').forEach(el => el.onclick = async () => {
    const res = await api('/api/canvas/project', { body: { name: '未命名画布' } });
    if (!res.ok || !res.data) { toast(res.reason || '新建失败'); return; }
    try { localStorage.setItem('cb.canvas.pid', res.data.id); } catch (e) { }
    UI.route = 'canvas';
    UI._navPending = true;
    await saveDraft({ route: 'canvas' });
    UI._navPending = false;
    render();
  });
  $$('[data-cvdel]').forEach(el => el.onclick = () => {
    const pid = el.dataset.cvdel, name = el.dataset.name || '这个画布';
    openModal(`<div class="m-head"><h3>删除「${esc(name)}」？</h3><button class="btn ghost sm" data-close>✕</button></div>
      <div class="m-body"><div class="tiny" style="line-height:1.9">只删这一张画布（节点和连线），<b>已生成的成品文件不删</b>，仍在历史记录里。</div></div>
      <div class="m-foot"><button class="btn sm" data-close>取消</button>
        <button class="btn sm danger" id="cvDelGo">删除画布</button></div>`);
    $('#cvDelGo').onclick = async () => {
      const res = await api('/api/canvas/project/' + encodeURIComponent(pid), { method: 'DELETE' });
      if (!res.ok) { toast(res.reason || '没删成'); return; }
      closeModal();
      const fresh = await api('/api/canvas'); S.canvasAdmin = fresh.data || {};
      S.cvAdmin = null; cvLoadAdmin(true);
      render();
      toast('画布已删除');
    };
  });
}
