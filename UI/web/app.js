/* ONAY Control UI */

const STATUS_INTERVAL = 2000;   // ms between status polls
const PREVIEW_INTERVAL = 700;   // ms between camera frame refreshes

const $ = s => document.querySelector(s);

let lastStatus = null;
let camPaths = [];
let outKeys = [];
let panelsSig = '';
let settingsRendered = false;

// ---------------------------------------------------------------- api

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function act(payload) {
  try {
    const res = await api('/api/action', { method: 'POST', body: JSON.stringify(payload) });
    if (!res.ok) console.warn('action failed:', res.error);
    pollStatus();
    return res;
  } catch (e) {
    console.error(e);
    return { ok: false, error: String(e) };
  }
}

// ---------------------------------------------------------------- status

async function pollStatus() {
  try {
    const s = await api('/api/status');
    lastStatus = s;
    $('#serverDot').className = 'dot ok';
    render(s);
  } catch (e) {
    $('#serverDot').className = 'dot';
    $('#projectMeta').textContent = 'disconnected — is TouchDesigner running?';
  }
}

function render(s) {
  // header
  $('#projectMeta').textContent = `${s.project} · TD ${s.tdVersion} · node ${s.settings.node}`;
  const fpsEl = $('#fpsVal');
  fpsEl.textContent = s.fps.toFixed ? s.fps.toFixed(1) : s.fps;
  fpsEl.className = s.fps >= s.targetFps * 0.9 ? 'good' : 'bad';
  $('#targetVal').textContent = s.targetFps;
  $('#clockVal').textContent = s.time;

  const pBtn = $('#performBtn');
  pBtn.classList.toggle('active', s.performMode);
  pBtn.textContent = s.performMode ? 'Perform Mode: ON' : 'Perform Mode: OFF';

  const tBtn = $('#patternBtn');
  if (s.testPattern === null || s.testPattern === undefined) {
    tBtn.style.display = 'none';
  } else {
    tBtn.style.display = '';
    tBtn.classList.toggle('active', s.testPattern);
    tBtn.textContent = s.testPattern ? 'Test Pattern: ON' : 'Test Pattern: OFF';
  }

  renderCameras(s.cameras);
  renderPanels(s.panels || []);
  renderOutputs(s.outputs || []);
  renderMonitors(s.monitors);
  renderWindows(s.windows);
  renderSettings(s.settings);

  $('#footerInfo').textContent =
    `${s.cameras.length} camera(s) · ${s.monitors.length} monitor(s) · ${s.windows.length} window(s) · realtime: ${s.realtime ? 'on' : 'off'}`;
}

// ---------------------------------------------------------------- cameras

function camKey(c) {
  return c.label || c.name || c.path;
}

function renderCameras(cams) {
  const wrap = $('#cameras');
  const newKeys = cams.map(camKey);

  // rebuild cards only when the camera list itself changes (keeps <img> stable)
  if (JSON.stringify(newKeys) !== JSON.stringify(camPaths)) {
    camPaths = newKeys;
    panelsSig = ''; // cards were rebuilt — in-card panels must re-mount
    wrap.innerHTML = '';
    for (const c of cams) {
      const card = document.createElement('div');
      card.className = 'cam-card';
      card.dataset.key = camKey(c);
      card.innerHTML = `
        <div class="preview"><img alt="preview"><span class="noimg">no signal</span></div>
        <div class="info">
          <div class="row1">
            <span class="name"><span class="dot"></span><span class="nm"></span></span>
            <button class="switch activeBtn"><span class="knob"></span></button>
          </div>
          <div class="devrow"><select class="devSel"></select></div>
          <div class="detail"></div>
          <div class="error"></div>
          <div class="panel-slot"></div>
        </div>`;
      card.querySelector('.activeBtn').addEventListener('click', () => {
        const cur = lastStatus.cameras.find(x => camKey(x) === camKey(c));
        if (cur && cur.controlPath)
          act({ action: 'cam_active', path: cur.controlPath, value: !cur.active });
      });
      card.querySelector('.devSel').addEventListener('change', ev => {
        const cur = lastStatus.cameras.find(x => camKey(x) === camKey(c));
        if (cur && cur.deviceMenu)
          act({ action: 'set_par', op: cur.deviceMenu.op, par: cur.deviceMenu.par, value: ev.target.value });
      });
      wrap.appendChild(card);
    }
    if (!cams.length) wrap.innerHTML = '<p class="meta">No cameras found in the project.</p>';
  }

  for (const c of cams) {
    const card = wrap.querySelector(`[data-key="${CSS.escape(camKey(c))}"]`);
    if (!card) continue;
    card.querySelector('.nm').textContent = camKey(c);
    card.querySelector('.dot').className = 'dot ' + (c.connected ? 'ok' : (c.active ? 'warn' : ''));
    const btn = card.querySelector('.activeBtn');
    btn.style.display = c.controlPath ? '' : 'none';
    btn.title = c.active ? 'camera active — click to stop' : 'camera inactive — click to start';
    btn.classList.toggle('active', c.active);

    const sel = card.querySelector('.devSel');
    if (c.deviceMenu && c.deviceMenu.menuNames.length) {
      sel.parentElement.style.display = '';
      const sig = JSON.stringify(c.deviceMenu.menuNames);
      if (sel.dataset.sig !== sig) {
        sel.dataset.sig = sig;
        sel.innerHTML = '';
        c.deviceMenu.menuNames.forEach((n, i) => {
          const o = document.createElement('option');
          o.value = n;
          o.textContent = c.deviceMenu.menuLabels[i] || n;
          sel.appendChild(o);
        });
      }
      if (sel !== document.activeElement) sel.value = c.deviceMenu.value;
    } else {
      sel.parentElement.style.display = 'none';
    }

    card.querySelector('.detail').textContent =
      `${c.width}×${c.height} · ${c.path || '?'}`;
    card.querySelector('.error').textContent = c.errors || c.warnings || '';
  }
}

// ---------------------------------------------------------------- param panels

const VEC_STYLES = ['XY', 'XYZ', 'XYZW', 'WH', 'UV', 'UVW', 'RGB', 'RGBA'];

function parDisplayLabel(p) {
  if (VEC_STYLES.includes(p.style)) return `${p.label} ${p.name.slice(-1).toUpperCase()}`;
  return p.label;
}

function makeSwitch(key, onClick) {
  const b = document.createElement('button');
  b.className = 'switch parCtl';
  if (key) b.dataset.par = key;
  b.innerHTML = '<span class="knob"></span>';
  b.addEventListener('click', onClick);
  return b;
}

function makeNumberInput(opPath, p) {
  const inp = document.createElement('input');
  inp.type = 'number';
  inp.step = p.style === 'Int' ? '1' : '0.01';
  inp.className = 'parCtl';
  inp.dataset.par = `${opPath}:${p.name}`;
  inp.title = parDisplayLabel(p);
  inp.addEventListener('change', () => {
    const v = p.style === 'Int' ? parseInt(inp.value, 10) : parseFloat(inp.value);
    if (!Number.isNaN(v)) act({ action: 'set_par', op: opPath, par: p.name, value: v });
  });
  return inp;
}

function makeControl(opPath, p) {
  const f = document.createElement('div');
  f.className = 'field ctl';
  const key = `${opPath}:${p.name}`;
  const label = document.createElement('label');
  label.textContent = parDisplayLabel(p);
  f.appendChild(label);

  if (p.style === 'Toggle') {
    f.appendChild(makeSwitch(key, () => {
      const cur = findPar(opPath, p.name);
      act({ action: 'set_par', op: opPath, par: p.name, value: !(cur && cur.value) });
    }));
  } else if (p.style === 'Pulse') {
    const b = document.createElement('button');
    b.className = 'btn small parCtl';
    b.dataset.par = key;
    b.textContent = 'PULSE';
    b.addEventListener('click', () => act({ action: 'set_par', op: opPath, par: p.name }));
    f.appendChild(b);
  } else if (p.style === 'Menu' || p.style === 'StrMenu') {
    const sel = document.createElement('select');
    sel.className = 'parCtl';
    sel.dataset.par = key;
    p.menuNames.forEach((n, i) => {
      const o = document.createElement('option');
      o.value = n;
      o.textContent = p.menuLabels[i] || n;
      sel.appendChild(o);
    });
    sel.addEventListener('change', () =>
      act({ action: 'set_par', op: opPath, par: p.name, value: sel.value }));
    f.appendChild(sel);
  } else {
    f.appendChild(makeNumberInput(opPath, p));
  }
  return f;
}

// vector pars (Padding X/Y/Z/W etc.) share one label and sit on one line
function makeVecControl(opPath, labelText, group) {
  const f = document.createElement('div');
  f.className = 'field ctl';
  const label = document.createElement('label');
  label.textContent = labelText;
  f.appendChild(label);
  const row = document.createElement('div');
  row.className = 'vecrow';
  for (const p of group) row.appendChild(makeNumberInput(opPath, p));
  f.appendChild(row);
  return f;
}

function buildControls(opPath, pars, row) {
  let i = 0;
  while (i < pars.length) {
    const p = pars[i];
    if (VEC_STYLES.includes(p.style)) {
      const group = [p];
      while (i + group.length < pars.length &&
             pars[i + group.length].style === p.style &&
             pars[i + group.length].label === p.label) {
        group.push(pars[i + group.length]);
      }
      row.appendChild(makeVecControl(opPath, p.label, group));
      i += group.length;
    } else {
      row.appendChild(makeControl(opPath, p));
      i += 1;
    }
  }
}

function findPar(opPath, name) {
  for (const panel of (lastStatus && lastStatus.panels) || []) {
    if (panel.op !== opPath) continue;
    const p = panel.pars.find(x => x.name === name);
    if (p) return p;
  }
  return null;
}

function renderPanels(panels) {
  const sig = JSON.stringify(panels.map(p => [p.label, p.camera, p.pars.map(x => x.name)]));

  if (sig !== panelsSig) {
    panelsSig = sig;
    $('#panels').innerHTML = '';
    document.querySelectorAll('#cameras .panel-slot').forEach(el => { el.innerHTML = ''; });
    for (const panel of panels) {
      // panels tied to a camera render inside that camera's card
      let host = $('#panels');
      if (panel.camera) {
        const card = $('#cameras').querySelector(`[data-key="${CSS.escape(panel.camera)}"]`);
        if (card) host = card.querySelector('.panel-slot');
      }
      const div = document.createElement('div');
      div.className = 'panel';
      const h = document.createElement('h3');
      h.textContent = panel.label;
      div.appendChild(h);
      if (panel.error) {
        const e = document.createElement('div');
        e.className = 'error';
        e.textContent = panel.error;
        div.appendChild(e);
      }
      const row = document.createElement('div');
      row.className = 'panel-row';
      buildControls(panel.op, panel.pars, row);
      div.appendChild(row);
      host.appendChild(div);
    }
  }

  // refresh values without clobbering whatever the user is editing
  for (const panel of panels) {
    for (const p of panel.pars) {
      const el = document.querySelector(`[data-par="${CSS.escape(`${panel.op}:${p.name}`)}"]`);
      if (!el || el === document.activeElement) continue;
      if (p.style === 'Toggle') {
        el.classList.toggle('active', !!p.value);
        el.title = `${p.label}: ${p.value ? 'on' : 'off'}`;
      } else if (el.tagName === 'SELECT' || el.tagName === 'INPUT') {
        if (p.style !== 'Pulse') el.value = p.value;
      }
    }
  }
}

// ---------------------------------------------------------------- outputs

function fillMonitorSelect(sel, current) {
  const mons = (lastStatus && lastStatus.monitors) || [];
  const sig = JSON.stringify(mons.map(m => [m.index, m.description]));
  if (sel.dataset.sig !== sig) {
    sel.dataset.sig = sig;
    sel.innerHTML = '';
    for (const m of mons) {
      const o = document.createElement('option');
      o.value = m.index;
      o.textContent = `mon ${m.index} — ${m.description || m.displayName || '?'}`;
      sel.appendChild(o);
    }
  }
  if (sel !== document.activeElement) sel.value = String(current);
}

function renderOutputs(outs) {
  const wrap = $('#outputs');
  const newKeys = outs.map(o => o.label);

  if (JSON.stringify(newKeys) !== JSON.stringify(outKeys)) {
    outKeys = newKeys;
    wrap.innerHTML = '';
    for (const o of outs) {
      const card = document.createElement('div');
      card.className = 'cam-card out-card';
      card.dataset.key = o.label;
      card.innerHTML = `
        <div class="preview"><img alt="preview"><span class="noimg">no signal</span></div>
        <div class="info">
          <div class="row1">
            <span class="name"><span class="pill off winpill" style="display:none"></span><span class="nm"></span></span>
            <span class="winbtns" style="display:none">
              <button class="btn small openBtn">Open</button>
              <button class="btn small danger closeBtn">Close</button>
            </span>
          </div>
          <div class="winrow">
            <span class="detail wininfo"></span>
            <select class="monSel" title="output monitor"></select>
          </div>
          <div class="detail"></div>
          <div class="error"></div>
        </div>`;
      card.querySelector('.openBtn').addEventListener('click', () => {
        const cur = (lastStatus.outputs || []).find(x => x.label === o.label);
        if (cur && cur.window) act({ action: 'open_window', path: cur.window.path });
      });
      card.querySelector('.closeBtn').addEventListener('click', () => {
        const cur = (lastStatus.outputs || []).find(x => x.label === o.label);
        if (cur && cur.window) act({ action: 'close_window', path: cur.window.path });
      });
      card.querySelector('.monSel').addEventListener('change', ev => {
        const cur = (lastStatus.outputs || []).find(x => x.label === o.label);
        if (cur && cur.window)
          act({ action: 'set_window_monitor', path: cur.window.path, monitor: parseInt(ev.target.value, 10) });
      });
      wrap.appendChild(card);
    }
  }

  for (const o of outs) {
    const card = wrap.querySelector(`.out-card[data-key="${CSS.escape(o.label)}"]`);
    if (!card) continue;
    card.querySelector('.nm').textContent = o.label;
    const pill = card.querySelector('.winpill');
    const btns = card.querySelector('.winbtns');
    const wininfo = card.querySelector('.wininfo');
    const monSel = card.querySelector('.monSel');
    if (o.window) {
      pill.style.display = '';
      pill.className = 'pill winpill ' + (o.window.isOpen ? 'ok' : 'off');
      pill.textContent = o.window.isOpen ? 'open' : 'closed';
      btns.style.display = '';
      wininfo.textContent = `${o.window.name} · src ${o.window.source || '?'}`;
      monSel.style.display = '';
      fillMonitorSelect(monSel, o.window.monitor);
    } else {
      pill.style.display = 'none';
      btns.style.display = 'none';
      wininfo.textContent = '';
      monSel.style.display = 'none';
    }
    card.querySelector('.detail:not(.wininfo)').textContent =
      o.path ? `${o.width}×${o.height} · ${o.path}` : '';
    card.querySelector('.error').textContent = o.error || '';
  }
}

function refreshPreviews() {
  if (!$('#previewToggle').checked || !lastStatus) return;
  for (const c of lastStatus.cameras) {
    const card = $('#cameras').querySelector(`[data-key="${CSS.escape(camKey(c))}"]`);
    if (!card) continue;
    const img = card.querySelector('img');
    const noimg = card.querySelector('.noimg');
    if (!c.path || !c.active || c.width <= 1) {
      img.style.display = 'none';
      noimg.style.display = '';
      continue;
    }
    noimg.style.display = 'none';
    img.style.display = '';
    img.src = `/api/cam?path=${encodeURIComponent(c.path)}&t=${Date.now()}`;
  }
  for (const o of (lastStatus.outputs || [])) {
    const card = $('#outputs').querySelector(`.out-card[data-key="${CSS.escape(o.label)}"]`);
    if (!card) continue;
    const img = card.querySelector('img');
    const noimg = card.querySelector('.noimg');
    if (!o.path || o.width <= 1) {
      img.style.display = 'none';
      noimg.style.display = '';
      continue;
    }
    noimg.style.display = 'none';
    img.style.display = '';
    img.src = `/api/cam?path=${encodeURIComponent(o.path)}&t=${Date.now()}`;
  }
}

// ---------------------------------------------------------------- displays

function renderMonitors(mons) {
  const tb = $('#monitorsTable tbody');
  tb.innerHTML = mons.map(m => `
    <tr>
      <td>${m.index}</td>
      <td>${m.description || m.displayName || '?'}</td>
      <td>${m.width}×${m.height}</td>
      <td>${m.left},${m.top}</td>
      <td>${m.refreshRate || '-'}</td>
      <td>${m.isPrimary ? '<span class="pill ok">primary</span>' : ''}</td>
    </tr>`).join('') || '<tr><td colspan="6">no monitor info</td></tr>';
}

function renderWindows(wins) {
  const tb = $('#windowsTable tbody');
  // don't rebuild while the user has a dropdown open in the table
  if (tb.contains(document.activeElement)) return;
  tb.innerHTML = '';
  if (!wins.length) {
    tb.innerHTML = '<tr><td colspan="5">No Window COMPs found.</td></tr>';
    return;
  }
  for (const w of wins) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="pill ${w.isOpen ? 'ok' : 'off'}">${w.isOpen ? 'open' : 'closed'}</span></td>
      <td title="${w.path}">${w.name}</td>
      <td><select class="monSel"></select></td>
      <td class="meta">${w.source || '-'}</td>
      <td>
        <button class="btn small openBtn">Open</button>
        <button class="btn small danger closeBtn">Close</button>
      </td>`;
    fillMonitorSelect(tr.querySelector('.monSel'), w.monitor);
    tr.querySelector('.monSel').addEventListener('change', ev =>
      act({ action: 'set_window_monitor', path: w.path, monitor: parseInt(ev.target.value, 10) }));
    tr.querySelector('.openBtn').addEventListener('click', () => act({ action: 'open_window', path: w.path }));
    tr.querySelector('.closeBtn').addEventListener('click', () => act({ action: 'close_window', path: w.path }));
    tb.appendChild(tr);
  }
}

// ---------------------------------------------------------------- settings

function renderSettings(st) {
  $('#nodeName').textContent = `· node "${st.node}"`;
  const form = $('#settingsForm');

  // only (re)build when empty, so typing is never clobbered by polling
  if (settingsRendered) return;
  if (!st.columns.length) {
    form.innerHTML = '<p class="meta">table_settings not found.</p>';
    return;
  }
  form.innerHTML = '';
  for (const col of st.columns) {
    const f = document.createElement('div');
    // path-like values get a full-width row so the whole path is visible
    const wide = /path|dir|file|folder/i.test(col) || String(st.values[col] ?? '').length > 24;
    f.className = 'field' + (wide ? ' wide' : '');
    f.innerHTML = `<label>${col}</label><input data-col="${col}">`;
    const input = f.querySelector('input');
    input.value = st.values[col] ?? '';
    input.dataset.original = input.value;
    input.addEventListener('input', () =>
      input.classList.toggle('dirty', input.value !== input.dataset.original));
    form.appendChild(f);
  }
  settingsRendered = true;
}

async function saveSettings() {
  const values = {};
  document.querySelectorAll('#settingsForm input').forEach(i => { values[i.dataset.col] = i.value; });
  const res = await act({ action: 'set_settings', values });
  const hint = $('#settingsHint');
  if (res.ok) {
    hint.textContent = 'Saved to table_settings and re-applied.';
    hint.className = 'hint ok';
    document.querySelectorAll('#settingsForm input').forEach(i => {
      i.dataset.original = i.value;
      i.classList.remove('dirty');
    });
  } else {
    hint.textContent = 'Save failed: ' + (res.error || 'unknown error');
    hint.className = 'hint bad';
  }
}

// ---------------------------------------------------------------- wiring

$('#performBtn').addEventListener('click', () =>
  act({ action: 'perform_mode', value: !(lastStatus && lastStatus.performMode) }));

$('#patternBtn').addEventListener('click', () =>
  act({ action: 'test_pattern', value: !(lastStatus && lastStatus.testPattern) }));

$('#saveSettingsBtn').addEventListener('click', saveSettings);

$('#reloadSettingsBtn').addEventListener('click', async () => {
  const res = await act({ action: 'reload_settings' });
  const hint = $('#settingsHint');
  hint.textContent = res.ok ? 'Settings re-applied (SETTINGS.Startup()).' : ('Failed: ' + res.error);
  hint.className = 'hint ' + (res.ok ? 'ok' : 'bad');
});

pollStatus();
setInterval(pollStatus, STATUS_INTERVAL);
setInterval(refreshPreviews, PREVIEW_INTERVAL);
