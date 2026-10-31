/* Deadzone Mapper frontend: session management, blueprint upload,
   click-to-mark position, Plotly heatmap over the floor plan. */
(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const POLL_MS = 3000;
  const STATUS_COLOR = { strong: '#2e7d32', moderate: '#f9c026', suspect: '#e67e22', deadzone: '#d32f2f' };
  const REASON_TEXT = { signal: 'weak signal', packet_loss: 'packet loss', latency: 'high latency' };

  const state = {
    sessions: [], id: null, session: null, analysis: null, ssids: [],
    pos: null, mark: true, timer: null, bpVersion: Date.now(), busy: false,
  };

  /* ---------------------------------------------------------------- helpers */
  async function api(path, opts = {}) {
    const res = await fetch('/api' + path, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }
  const post = (path, body) => api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  });

  let toastTimer;
  function toast(msg, isError = false) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'toast' + (isError ? ' error' : '');
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 2500);
  }
  const fail = (e) => toast(e.message || String(e), true);

  function el(tag, props = {}, ...kids) {
    const n = document.createElement(tag);
    Object.assign(n, props);
    kids.forEach((k) => n.append(k));
    return n;
  }
  const fmtDate = (iso) => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

  function thresholdQuery() {
    const q = new URLSearchParams();
    const set = (k, id) => { const v = $(id).value; if (v !== '') q.set(k, v); };
    set('rssi_thr', '#thr-rssi'); set('loss_thr', '#thr-loss');
    set('latency_thr', '#thr-lat'); set('min_readings', '#thr-min');
    if (state.ssid) q.set('ssid', state.ssid);
    q.set('metric', $('#metric').value);
    return q;
  }
  const analysisQuery = () => thresholdQuery().toString();

  /* --------------------------------------------------------------- sessions */
  async function loadSessions() {
    state.sessions = await api('/sessions');
    renderSessions();
  }

  function renderSessions() {
    const ul = $('#sessions');
    ul.replaceChildren();
    $('#sessions-empty').hidden = state.sessions.length > 0;
    state.sessions.forEach((s) => {
      const btn = el('button', { type: 'button', onclick: () => selectSession(s.id) },
        el('span', { className: 's-name' }, el('span', { className: 'dot ' + s.status }), s.name),
        el('span', { className: 's-meta' },
          `${fmtDate(s.created_at)} · ${s.measurement_count ?? 0} readings · ${s.status}`));
      ul.append(el('li', { className: s.id === state.id ? 'current' : '' }, btn));
    });
  }

  async function selectSession(id) {
    stopPolling();
    state.id = id;
    state.ssid = '';
    state.pos = null;
    state.analysis = null;
    state.bpVersion = Date.now();
    localStorage.setItem('lastSession', String(id));
    await refresh(true);
    renderSessions();
  }

  $('#new-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = $('#new-name').value.trim();
    const file = $('#new-file').files[0];
    if (!name || !file) return;
    try {
      const s = await post('/sessions', { name });
      state.id = s.id;
      await uploadBlueprint(s.id, file);
      $('#new-form').reset();
      await loadSessions();
      await selectSession(s.id);
      toast(`Session "${name}" created. Click the floor plan to mark where you are.`);
    } catch (err) {
      fail(err);
      if (state.id) { await loadSessions(); await selectSession(state.id); }
    }
  });

  async function uploadBlueprint(id, file) {
    const fd = new FormData();
    fd.append('file', file);
    await api(`/sessions/${id}/blueprint`, { method: 'POST', body: fd });
  }

  /* ----------------------------------------------------------- refresh/poll */
  async function refresh(full = false) {
    if (state.id == null || state.busy) return;
    state.busy = true;
    try {
      state.session = await api(`/sessions/${state.id}`);
      renderHeader();
      if (!state.session.has_blueprint) {
        state.analysis = null;
        showUploadPrompt();
        renderBelow();
        return;
      }
      const [analysis, ssids, pos] = await Promise.all([
        api(`/sessions/${state.id}/analysis?${analysisQuery()}`),
        api(`/sessions/${state.id}/ssids`),
        api(`/sessions/${state.id}/position`),
      ]);
      state.analysis = analysis;
      state.ssids = ssids;
      state.pos = pos.x == null ? null : { x: pos.x, y: pos.y };
      renderSsids();
      renderMap();
      renderBelow();
      updateStageMessage();
      if (full) renderSessions();
      if (state.session.status === 'active') startPolling(); else stopPolling();
    } catch (err) {
      stopPolling();
      fail(err);
    } finally {
      state.busy = false;
    }
  }

  function startPolling() {
    if (state.timer) return;
    state.timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  }
  function stopPolling() { clearInterval(state.timer); state.timer = null; }

  /* ----------------------------------------------------------------- render */
  function renderHeader() {
    const s = state.session;
    $('#title').textContent = s.name;
    const b = $('#status');
    b.hidden = false;
    b.textContent = s.status === 'active' ? 'Scanning' : 'Stopped';
    b.className = 'badge ' + s.status;
    $('#btn-stop').hidden = s.status !== 'active';
    $('#btn-resume').hidden = s.status === 'active';
    const t = $('#mark-toggle');
    t.disabled = s.status !== 'active';
    t.setAttribute('aria-pressed', String(state.mark && s.status === 'active'));
    t.textContent = state.mark && s.status === 'active' ? 'Marking position' : 'Not marking';
    $('#map').classList.toggle('no-mark', !(state.mark && s.status === 'active'));
    $('#btn-png').href = `/api/sessions/${s.id}/heatmap.png?download=1&${analysisQuery()}`;
    $('#btn-csv').href = `/api/sessions/${s.id}/export.csv`;
    $('#cmd').textContent = `python -m scanner.scanner --api ${location.origin} --session ${s.id}`;
  }

  function renderSsids() {
    const sel = $('#ssid');
    const keep = state.ssid;
    sel.replaceChildren(el('option', { value: '', textContent: 'All networks' }));
    state.ssids.forEach((n) => sel.append(el('option', {
      value: n.ssid,
      textContent: `${n.ssid} (${n.access_points} AP${n.access_points === 1 ? '' : 's'})`,
    })));
    sel.value = [...sel.options].some((o) => o.value === keep) ? keep : '';
  }

  function showUploadPrompt() {
    const box = $('#stage-msg');
    box.hidden = false;
    box.style.alignItems = 'center';
    box.style.justifyContent = 'center';
    $('.hint')?.remove();
    $('#map').style.visibility = 'hidden';
    const input = el('input', { type: 'file', accept: 'image/*' });
    const btn = el('button', { className: 'primary', type: 'button', textContent: 'Upload floor plan' });
    btn.addEventListener('click', async () => {
      if (!input.files[0]) return toast('Choose an image first.', true);
      try {
        await uploadBlueprint(state.id, input.files[0]);
        state.bpVersion = Date.now();
        await refresh(true);
      } catch (e) { fail(e); }
    });
    box.replaceChildren(el('div', {},
      el('p', {}, 'This session has no floor plan yet. Upload an image of the area you will walk through.'),
      el('div', { className: 'upload' }, input, btn)));
  }

  function updateStageMessage() {
    const box = $('#stage-msg');
    const a = state.analysis;
    $('#map').style.visibility = 'visible';
    $('.hint')?.remove();
    const active = state.session.status === 'active';
    if (!a || a.summary.locations === 0) {
      box.hidden = false;
      box.style.alignItems = 'flex-end';
      box.style.justifyContent = 'flex-start';
      box.replaceChildren(el('p', { className: 'hint', style: 'position:static' },
        active ? 'Click the floor plan where the laptop is. Then start the scanner (command below).'
               : 'This session was stopped before any data was recorded.'));
    } else {
      box.hidden = true;
      if (active && state.mark) {
        $('.stage').append(el('div', { className: 'hint' }, 'Click the floor plan to move your position.'));
      }
    }
  }

  function renderMap() {
    const a = state.analysis;
    if (!a) return;
    const W = a.blueprint.width, H = a.blueprint.height, spec = a.spec;

    const heat = {
      type: 'heatmap', x: a.grid.xs, y: a.grid.ys, z: a.grid.z,
      colorscale: spec.colorscale, zmin: spec.zmin, zmax: spec.zmax,
      opacity: 0.6, zsmooth: 'best', connectgaps: false, hoverinfo: 'skip',
      colorbar: { title: { text: spec.unit }, thickness: 10, len: 0.55, outlinewidth: 0 },
    };

    const pts = a.points;
    const points = {
      type: 'scatter', mode: 'markers', x: pts.map((p) => p.x), y: pts.map((p) => p.y),
      marker: {
        size: 9, color: pts.map((p) => STATUS_COLOR[p.status]),
        symbol: pts.map((p) => (p.status === 'deadzone' ? 'x' : 'circle')),
        line: { color: '#16222e', width: 1.5 },
      },
      text: pts.map((p) => {
        const parts = [`<b>${p.status}</b>`, `${p.avg_rssi} dBm (worst ${p.min_rssi})`, `${p.n} reading${p.n === 1 ? '' : 's'}`];
        if (p.avg_latency != null) parts.push(`latency ${p.avg_latency} ms`);
        if (p.avg_packet_loss != null) parts.push(`loss ${p.avg_packet_loss}%`);
        if (p.reasons.length) parts.push(p.reasons.map((r) => REASON_TEXT[r]).join(', '));
        return parts.join('<br>');
      }),
      hovertemplate: '%{text}<extra></extra>',
    };

    const traces = [heat, points];
    if (state.pos) {
      traces.push({
        type: 'scatter', mode: 'markers', x: [state.pos.x], y: [state.pos.y], hoverinfo: 'skip',
        marker: { size: 17, symbol: 'star', color: '#1d4e89', line: { color: '#fff', width: 2 } },
      });
    }

    const shapes = a.regions.map((r) => ({
      type: 'circle', xref: 'x', yref: 'y', x0: r.cx - r.radius, x1: r.cx + r.radius,
      y0: r.cy - r.radius, y1: r.cy + r.radius,
      line: { color: '#b00020', width: 2, dash: 'dot' }, fillcolor: 'rgba(211,47,47,0.10)',
    }));
    const annotations = a.regions.map((r) => ({
      x: r.cx, y: r.cy - r.radius, xref: 'x', yref: 'y', text: `Deadzone ${r.id}`,
      showarrow: false, yshift: 10, font: { size: 12, color: '#b00020' },
      bgcolor: 'rgba(255,255,255,0.85)', borderpad: 2,
    }));

    const layout = {
      margin: { l: 0, r: 0, t: 0, b: 0 },
      xaxis: { range: [0, W], visible: false, constrain: 'domain', fixedrange: true },
      yaxis: { range: [H, 0], visible: false, scaleanchor: 'x', scaleratio: 1, fixedrange: true },
      images: [{
        source: `/api/sessions/${state.id}/blueprint?v=${state.bpVersion}`,
        xref: 'x', yref: 'y', x: 0, y: 0, sizex: W, sizey: H,
        xanchor: 'left', yanchor: 'top', sizing: 'stretch', layer: 'below',
      }],
      shapes, annotations, showlegend: false, dragmode: false, hovermode: 'closest',
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    };
    Plotly.react('map', traces, layout, { displayModeBar: false, responsive: true, scrollZoom: false });
  }

  function renderBelow() {
    const a = state.analysis;
    $('#below').hidden = !state.session;
    if (!state.session) return;

    const dl = $('#summary');
    dl.replaceChildren();
    const rows = a ? [
      ['Spots without a deadzone', a.summary.coverage_pct == null ? 'no data yet' : `${a.summary.coverage_pct}%`],
      ['Spots measured', a.summary.locations],
      ['Scan cycles recorded', a.summary.readings],
      ['Average signal', a.summary.avg_rssi == null ? 'no data yet' : `${a.summary.avg_rssi} dBm`],
      ['Strong / moderate', `${a.summary.strong} / ${a.summary.moderate}`],
      ['Deadzone spots', a.summary.deadzone],
      ['Need more readings', a.summary.suspect],
    ] : [];
    rows.forEach(([k, v]) => dl.append(el('dt', {}, k), el('dd', {}, String(v))));

    const ol = $('#regions');
    const empty = $('#regions-empty');
    ol.replaceChildren();
    if (!a || a.summary.locations === 0) {
      empty.hidden = false; empty.textContent = 'Deadzones appear here once readings come in.';
    } else if (a.regions.length === 0) {
      empty.hidden = false;
      empty.textContent = a.summary.suspect
        ? `No confirmed deadzones. ${a.summary.suspect} spot(s) look weak but need more readings.`
        : 'No deadzones found with the current thresholds.';
    } else {
      empty.hidden = true;
      a.regions.forEach((r) => ol.append(el('li', {},
        `Around (${Math.round(r.cx)}, ${Math.round(r.cy)}): average ${r.avg_rssi} dBm, worst ${r.worst_rssi} dBm`,
        el('small', {}, `${r.points} spot${r.points === 1 ? '' : 's'} · ${r.reasons.map((x) => REASON_TEXT[x]).join(', ')}`))));
    }
  }

  /* ----------------------------------------------------- click-to-mark */
  $('#map').addEventListener('click', async (e) => {
    const s = state.session, a = state.analysis;
    if (!s || !a || !state.mark || s.status !== 'active') return;
    const gd = $('#map');
    const fl = gd._fullLayout;
    if (!fl || !fl.xaxis || !fl.yaxis) return;
    const box = gd.getBoundingClientRect();
    const px = e.clientX - box.left - fl.xaxis._offset;
    const py = e.clientY - box.top - fl.yaxis._offset;
    if (px < 0 || py < 0 || px > fl.xaxis._length || py > fl.yaxis._length) return;
    const [x0, x1] = fl.xaxis.range, [y0, y1] = fl.yaxis.range;   // y range is [H, 0] (flipped)
    const x = x0 + (px / fl.xaxis._length) * (x1 - x0);
    const y = y1 + (py / fl.yaxis._length) * (y0 - y1);
    try {
      const r = await post(`/sessions/${s.id}/position`, { x, y });
      state.pos = { x: r.x, y: r.y };
      renderMap();
      toast(`Position set to (${Math.round(r.x)}, ${Math.round(r.y)})`);
    } catch (err) { fail(err); }
  });

  /* ------------------------------------------------------------- controls */
  $('#mark-toggle').addEventListener('click', () => {
    state.mark = !state.mark;
    renderHeader();
    updateStageMessage();
  });
  $('#ssid').addEventListener('change', (e) => { state.ssid = e.target.value; refresh(); });
  $('#metric').addEventListener('change', () => refresh());
  $('#thr-form').addEventListener('submit', (e) => {
    e.preventDefault();
    localStorage.setItem('thresholds', JSON.stringify({
      rssi: $('#thr-rssi').value, loss: $('#thr-loss').value, lat: $('#thr-lat').value, min: $('#thr-min').value,
    }));
    refresh();
    toast('Thresholds applied.');
  });

  $('#btn-stop').addEventListener('click', async () => {
    try { await post(`/sessions/${state.id}/stop`); await loadSessions(); await refresh(true); toast('Scanning stopped.'); }
    catch (e) { fail(e); }
  });
  $('#btn-resume').addEventListener('click', async () => {
    try { await post(`/sessions/${state.id}/resume`); await loadSessions(); await refresh(true); toast('Scanning resumed.'); }
    catch (e) { fail(e); }
  });
  $('#btn-delete').addEventListener('click', async () => {
    if (!confirm(`Delete "${state.session.name}" and all its readings? This cannot be undone.`)) return;
    try {
      await api(`/sessions/${state.id}`, { method: 'DELETE' });
      stopPolling();
      state.id = state.session = state.analysis = null;
      localStorage.removeItem('lastSession');
      $('#title').textContent = 'No session selected';
      $('#status').hidden = true;
      $('#below').hidden = true;
      $('#map').style.visibility = 'hidden';
      $('#stage-msg').hidden = false;
      $('#stage-msg').replaceChildren(el('p', {}, 'Session deleted. Select another or create a new one.'));
      await loadSessions();
    } catch (e) { fail(e); }
  });
  $('#btn-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#cmd').textContent); toast('Command copied.'); }
    catch (_) { toast('Select the command and copy it manually.', true); }
  });

  /* ------------------------------------------------------------------ phone link */
  async function setupPhoneLink() {
    if (!['localhost', '127.0.0.1'].includes(location.hostname)) return;   // already on another device
    try {
      const n = await api('/network');
      if (!n.url) return;
      $('#phone-url').textContent = n.url;
      $('#qr').replaceChildren();
      if (window.QRCode) new QRCode($('#qr'), { text: n.url, width: 128, height: 128, correctLevel: QRCode.CorrectLevel.M });
      $('#phone-panel').hidden = false;
    } catch (_) { /* optional feature */ }
  }

  /* ------------------------------------------------------------------ boot */
  (async function init() {
    setupPhoneLink();
    try {
      const saved = JSON.parse(localStorage.getItem('thresholds') || 'null');
      if (saved) {
        $('#thr-rssi').value = saved.rssi; $('#thr-loss').value = saved.loss;
        $('#thr-lat').value = saved.lat; $('#thr-min').value = saved.min;
      }
    } catch (_) { /* ignore corrupt storage */ }
    try {
      await loadSessions();
      const last = Number(localStorage.getItem('lastSession'));
      const target = state.sessions.find((s) => s.id === last) || state.sessions[0];
      if (target) await selectSession(target.id);
    } catch (e) { fail(e); }
  })();
})();
