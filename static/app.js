/**
 * app.js — OSTEP Builder frontend
 *
 * All API calls, WebSocket management, pipeline state handling,
 * sidebar asset tree, and file viewer are here.
 *
 * Architecture:
 *   Config → API map → State → Log → Timer → WebSocket
 *   → Pipeline updates → UI helpers → Viewers → Modals → Init
 */

'use strict';

/* ═══════════════════════════════════════════════════════════
   1. CONFIG
   Set BACKEND to your ngrok / server URL.
   Leave as '' to auto-detect from window.location (self-hosted).
═══════════════════════════════════════════════════════════ */
// const BACKEND = ''; // add ngrok URL here if not self-hosting
const BACKEND = "http://127.0.0.1:8000";

const BASE   = BACKEND || window.location.origin;
// const WS_URL = BASE.replace(/^http/, 'ws') + '/api/v1/ws/status';
// const WS_URL = `ws://${window.location.host}/api/v1/ws/status`;
const WS_URL = (BASE.startsWith("https") ? "wss" : "ws") +
               "://" + window.location.host +
               "/api/v1/ws/status";
// ngrok requires this header to bypass the browser interstitial page
const NGROK  = BACKEND ? { 'ngrok-skip-browser-warning': '1' } : {};
const JSON_H = { 'Content-Type': 'application/json', ...NGROK };

/* ═══════════════════════════════════════════════════════════
   2. API MAP — single source of truth for all endpoints
═══════════════════════════════════════════════════════════ */
const API = {
  // System
  health:         `${BASE}/health`,
  // Pipeline
  scrapeStart:    `${BASE}/api/v1/scrape/start`,
  scrapeStatus:   `${BASE}/api/v1/scrape/status`,
  mergeStart:     `${BASE}/api/v1/merge/start`,
  // Assets
  assetsList:     (cat) => `${BASE}/api/v1/chapters/assets${cat ? `?category=${cat}` : ''}`,
  // Preview — Content-Disposition: inline → renders in iframe/img
  previewAsset:   (cat, fn) => `${BASE}/api/v1/preview/asset/${cat}/${encodeURIComponent(fn)}`,
  previewMerged:  `${BASE}/api/v1/preview/merged`,
  // Download — Content-Disposition: attachment → Save-As dialog
  downloadAsset:  (cat, fn) => `${BASE}/api/v1/download/asset/${cat}/${encodeURIComponent(fn)}`,
  downloadMerged: `${BASE}/api/v1/download/merged`,
};

/* ═══════════════════════════════════════════════════════════
   3. STATE
═══════════════════════════════════════════════════════════ */
let ws            = null;   // active WebSocket
let pipeState     = {};     // last pipeline snapshot
let prevStatus    = null;   // previous status for transition detection
let prevPhase     = null;   // previous phase  for transition detection
let prevFetched   = -1;     // last logged chapters_fetched value
let prevMerged    = -1;     // last logged chapters_merged value
let timerRef      = null;   // elapsed timer interval
let timerStart    = null;   // timestamp when timer was started
let mergeReady    = false;  // true once merge completes
let autoMergeTriggered = false;  // guard against double auto-merge
let initComplete  = false;  // true after init() finishes


/* ═══════════════════════════════════════════════════════════
   4. LOG
═══════════════════════════════════════════════════════════ */
function log(type, html) {
  const body  = document.getElementById('logBody');
  const empty = document.getElementById('logEmpty');
  if (empty) empty.remove();

  const ts = new Date().toTimeString().slice(0, 8);
  const labels = { info: 'INFO', ok: ' OK ', warn: 'WARN', err: ' ERR', ws: ' WS ' };

  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML =
    `<span class="log-ts">${ts}</span>` +
    `<span class="log-tag ${type}">${labels[type] || 'INFO'}</span>` +
    `<span class="log-msg">${html}</span>`;

  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

document.getElementById('btnClearLog').addEventListener('click', () => {
  document.getElementById('logBody').innerHTML =
    `<div class="log-empty" id="logEmpty">
       <span class="log-empty-icon">_</span>Log cleared.
     </div>`;
});

/* ═══════════════════════════════════════════════════════════
   5. ELAPSED TIMER
═══════════════════════════════════════════════════════════ */
function startTimer() {
  timerStart = Date.now();
  const el = document.getElementById('stageElapsed');
  el.style.display = 'flex';
  clearInterval(timerRef);
  timerRef = setInterval(() => {
    const s = Math.floor((Date.now() - timerStart) / 1000);
    document.getElementById('elapsedTxt').textContent =
      s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  }, 1000);
}

function stopTimer() {
  clearInterval(timerRef);
}

/* ═══════════════════════════════════════════════════════════
   6. WEBSOCKET
═══════════════════════════════════════════════════════════ */
function connectWS() {
  // Clean up existing connection first
  if (ws) {
    ws.onclose = null;
    ws.onerror = null;
    ws.close();
    ws = null;
  }

  setWsIndicator('connecting');

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setWsIndicator('live');
  };

  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch { return; }

    // Server keepalive ping
    if (data.ping) { ws.send('ping'); return; }

    // Server-initiated close (terminal state) — just ignore
    if (data.type === 'close') return;

    onPipelineUpdate(data);
  };

  ws.onerror = () => {
    log('err', 'WebSocket error — check server & tunnel');
    setWsIndicator('err');
  };

  ws.onclose = (ev) => {
    ws = null;
    setWsIndicator('off');

    // WS closes in two expected cases:
    //   1. Scrape completed — we switch to REST polling for the merge phase
    //   2. Full pipeline done — nothing to do
    // If it was unexpected (mid-run, not clean), warn and retry.
    const expected = pipeState.status === 'completed' || pipeState.status === 'failed';
    if (!expected && !ev.wasClean) {
      log('warn', `WS dropped (code ${ev.code}) — reconnecting…`);
      setTimeout(connectWS, 3000);
    }
  };
}

function setWsIndicator(state) {
  const led = document.getElementById('wsLed');
  const lbl = document.getElementById('wsLbl');
  led.className = `ws-led${state === 'live' ? ' live' : state === 'err' ? ' err' : ''}`;
  lbl.textContent = {
    live: 'WS: live',
    connecting: 'WS: connecting…',
    off: 'WS: offline',
    err: 'WS: error',
  }[state] || 'WS: offline';
}

/* ═══════════════════════════════════════════════════════════
   7. PIPELINE UPDATE HANDLER
   Called on every WebSocket message and on init state restore.
═══════════════════════════════════════════════════════════ */
function onPipelineUpdate(snap) {
  pipeState = snap;
  const { phase, status } = snap;

  // Always update the visual UI (idempotent — safe to call on every push)
  updateSidebarStatus(snap);
  updateStages(snap);
  updateMetrics(snap);
  updateProgress(snap);

  // Detect true phase/status transitions to avoid duplicate log lines.
  // WS pushes the full snapshot on every store.update() call, so the same
  // status can arrive many times while a phase is running.
  const statusChanged = status !== prevStatus;
  const phaseChanged  = phase  !== prevPhase;

  // ── Log only on real transitions ─────────────────────────────
  if (statusChanged || phaseChanged) {
    if (status === 'pending' && phase === 'scrape')
      log('info', 'Scrape job queued — waiting for worker…');

    if (status === 'pending' && phase === 'merge')
      log('info', 'Merge job queued — waiting for worker…');

    if (status === 'running' && phase === 'scrape')
      log('info', 'Scrape worker started — fetching chapter index…');

    if (status === 'running' && phase === 'merge')
      log('info', 'Merge worker started…');
  }

  // ── Progress logs — throttled, only when value changes ───────
  if (status === 'running' && phase === 'scrape') {
    if (snap.chapters_found > 0 && snap.chapters_fetched !== prevFetched) {
      prevFetched = snap.chapters_fetched;
      log('info',
        `Scraping <b>${snap.chapters_fetched}/${snap.chapters_found}</b> chapters` +
        ` · <b>${snap.assets_downloaded}</b> assets`);
    }
  }

  if (status === 'running' && phase === 'merge') {
    if (snap.chapters_merged !== prevMerged) {
      prevMerged = snap.chapters_merged;
      log('info',
        `Merging <b>${snap.chapters_merged}</b> chapters` +
        ` · <b>${snap.total_pages}</b> pages`);
    }
  }

  // ── Terminal transitions — fire exactly once ──────────────────
  // Use a combined key (phase+status) so completed/scrape → completed/merge
  // is treated as a real transition even though status alone didn't change.
  const stateKey     = `${phase}:${status}`;
  const prevStateKey = `${prevPhase}:${prevStatus}`;
  const stateChanged = stateKey !== prevStateKey;

  if (status === 'completed' && phase === 'scrape' && stateChanged) {
    log('ok',
      `Scrape complete — <b>${snap.chapters_fetched}</b> chapters` +
      ` · <b>${snap.assets_downloaded}</b> assets`);
    stopTimer();
    setBtnState('idle');
    refreshAssets();
    if (!autoMergeTriggered) {
      autoMergeTriggered = true;
      log('info', '⟳ Auto-starting PDF merge…');
      setTimeout(autoMerge, 1200);
    }
  }

  if (status === 'completed' && phase === 'merge' && stateChanged) {
    log('ok',
      `Merge complete — <b>${snap.total_pages}</b> pages` +
      ` · <b>${snap.file_size_mb?.toFixed(1)} MB</b>`);
    stopTimer();
    setBtnState('idle');
    if (!mergeReady) markMergeReady(snap);
  }

  if (status === 'failed' && stateChanged) {
    log('err', `Pipeline failed: ${snap.error || 'unknown error'}`);
    stopTimer();
    setBtnState('idle');
    showError('Pipeline Failed', snap.error || 'An unknown error occurred.', null);
  }

  // Save previous values for next comparison
  prevStatus = status;
  prevPhase  = phase;
}

/* ═══════════════════════════════════════════════════════════
   8. AUTO MERGE + POLL
   After scrape completes the WS closes (backend design).
   We start the merge job via REST, then poll the status endpoint
   every 3 s until merge completes. No WS reconnect needed.
═══════════════════════════════════════════════════════════ */
async function autoMerge() {
  try {
    const r = await fetch(API.mergeStart, {
      method: 'POST',
      headers: JSON_H,
      body: JSON.stringify({ include_extras: true }),
    });
    const d = await r.json();

    if (r.status === 412) {
      log('warn', 'Merge precondition not met — retrying in 2s…');
      setTimeout(autoMerge, 2000);
      return;
    }
    if (r.status === 409) {
      // Already queued — just start polling
      startPollMerge();
      return;
    }
    if (!r.ok) {
      log('err', `Merge start failed (${r.status}): ${d.detail || '?'}`);
      return;
    }

    log('ok', `Merge job accepted — polling for progress…`);
    startTimer();
    startPollMerge();
  } catch (e) {
    log('err', `Auto-merge network error: ${e.message}`);
  }
}

let _pollRef = null;  // interval reference for merge polling

function startPollMerge() {
  // Poll every 3 s. Update UI with each snapshot.
  // Stop when merge completes or fails.
  if (_pollRef) return; // already polling
  _pollRef = setInterval(async () => {
    try {
      const r = await fetchWithTimeout(API.scrapeStatus, { headers: NGROK }, 5000);
      if (!r.ok) return;
      const snap = await r.json();

      // Update UI with polled state (reuse same handler, dedup guards apply)
      onPipelineUpdate(snap);

      // Stop polling once we reach a terminal merge state
      const done = (snap.phase === 'merge' && snap.status === 'completed')
                || snap.status === 'failed';
      if (done) {
        clearInterval(_pollRef);
        _pollRef = null;
      }
    } catch { /* ignore transient errors */ }
  }, 3000);
}

/* ═══════════════════════════════════════════════════════════
   9. START SCRAPE (user action)
═══════════════════════════════════════════════════════════ */
async function startScrape() {
  const url = document.getElementById('urlInput').value.trim();

  if (!url) {
    showError('Missing URL', 'Please enter a source URL before starting.', null);
    return;
  }

  try { new URL(url); } catch {
    showError('Invalid URL', `"${url}" is not a valid URL.`, null);
    return;
  }

  setBtnState('loading');
  log('info', `Starting scrape → <b>${url}</b>`);

  try {
    const r = await fetch(API.scrapeStart, {
      method: 'POST',
      headers: JSON_H,
      body: JSON.stringify({ index_url: url }),
    });
    const d = await r.json();

    // 409 = already running → just attach WS
    if (r.status === 409) {
      log('warn', 'Pipeline already active — attaching WebSocket to monitor…');
      setBtnState('running');
      connectWS();
      return;
    }

    if (!r.ok) {
      setBtnState('idle');
      showError(`API Error (${r.status})`, d.detail || 'Failed to start scrape.', JSON.stringify(d, null, 2));
      return;
    }

    // 202 Accepted — reset all dedup guards for fresh run
    log('ok', `Job accepted — ID: <b>${d.job_id}</b>`);
    document.getElementById('topbarJobId').textContent = d.job_id;
    prevStatus = null; prevPhase = null;
    prevFetched = -1;  prevMerged = -1;
    autoMergeTriggered = false;
    setBtnState('running');
    startTimer();
    connectWS(); // connect WS immediately
  } catch (e) {
    setBtnState('idle');
    showError('Network Error', 'Could not reach the API server. Check the tunnel is active.', e.message);
  }
}

document.getElementById('btnScrape').addEventListener('click', startScrape);

/* ═══════════════════════════════════════════════════════════
   10. BUTTON STATE
═══════════════════════════════════════════════════════════ */
function setBtnState(state) {
  const btn  = document.getElementById('btnScrape');
  const icon = document.getElementById('btnScrapeIcon');
  const txt  = document.getElementById('btnScrapeTxt');
  const inp  = document.getElementById('urlInput');

  if (state === 'loading' || state === 'running') {
    btn.disabled  = true;
    inp.disabled  = true;
    icon.innerHTML = '<div class="spin"></div>';
    txt.textContent = state === 'loading' ? 'Starting…' : 'In Progress…';
  } else {
    btn.disabled  = false;
    inp.disabled  = false;
    icon.textContent = '▶';
    txt.textContent  = 'Start Scraping';
  }
}

/* ═══════════════════════════════════════════════════════════
   11. MARK MERGE READY
   Enables download buttons and shows the inline strip.
═══════════════════════════════════════════════════════════ */
function markMergeReady(snap) {
  mergeReady = true;
  window.__mergeSnap = snap; // store for viewer

  // Enable topbar download button
  const btnDlTop = document.getElementById('btnDlTop');
  btnDlTop.disabled = false;
  btnDlTop.addEventListener('click', doDownloadMerged);

  // Update sidebar merged card
  const card = document.getElementById('mergedCard');
  card.className = 'merged-card ready';
  document.getElementById('mergedName').textContent = 'OSTEP_Complete_Book.pdf';

  const parts = [];
  if (snap.total_pages    > 0) parts.push(`${snap.total_pages} pages`);
  if (snap.file_size_mb   > 0) parts.push(`${snap.file_size_mb.toFixed(1)} MB`);
  if (snap.chapters_merged > 0) parts.push(`${snap.chapters_merged} chapters`);
  document.getElementById('mergedMeta').textContent = parts.join(' · ') || 'Ready';

  // Sidebar buttons
  const viewBtn = document.getElementById('mergedViewBtn');
  const dlBtn   = document.getElementById('mergedDlBtn');
  viewBtn.disabled = false;
  dlBtn.disabled   = false;
  viewBtn.addEventListener('click', openMergedViewer);
  dlBtn.addEventListener('click', doDownloadMerged);

  // Inline download strip inside scraper card
  const strip = document.getElementById('downloadStrip');
  strip.style.display = 'flex';
  const meta = document.getElementById('downloadStripMeta');
  meta.textContent = parts.length ? `(${parts.join(' · ')})` : '';
}

/* ═══════════════════════════════════════════════════════════
   12. DOWNLOAD HELPERS
   doDownloadMerged  — uses /download/merged (attachment)
   doDownloadAsset   — uses /download/asset/... (attachment)
═══════════════════════════════════════════════════════════ */
function doDownloadMerged() {
  log('info', 'Initiating download of merged PDF…');
  triggerAnchorDownload(API.downloadMerged, 'OSTEP_Complete_Book.pdf');
}

function doDownloadAsset(cat, filename) {
  log('info', `Downloading <b>${filename}</b>…`);
  triggerAnchorDownload(API.downloadAsset(cat, filename), filename);
}

function triggerAnchorDownload(href, filename) {
  const a = document.createElement('a');
  a.href     = href;
  a.download = filename;
  // Add ngrok header by fetching as blob (anchor alone can't send custom headers)
  if (BACKEND) {
    fetch(href, { headers: NGROK })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a2  = document.createElement('a');
        a2.href     = url;
        a2.download = filename;
        document.body.appendChild(a2);
        a2.click();
        document.body.removeChild(a2);
        URL.revokeObjectURL(url);
        log('ok', `Download started: <b>${filename}</b>`);
      })
      .catch(e => showError('Download Failed', `Could not download ${filename}`, e.message));
  } else {
    // Same-origin — simple anchor works fine
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    log('ok', `Download started: <b>${filename}</b>`);
  }
}

// Inline download strip button
document.getElementById('btnDlStrip').addEventListener('click', doDownloadMerged);

/* ═══════════════════════════════════════════════════════════
   13. UI HELPERS
═══════════════════════════════════════════════════════════ */

// Sidebar pipeline status
function updateSidebarStatus(snap) {
  const orb = document.getElementById('pipelineOrb');
  const lbl = document.getElementById('pipelineLabel');
  const pha = document.getElementById('pipelinePhase');

  orb.className = `pipeline-orb ${snap.status || 'idle'}`;
  lbl.textContent = {
    idle: 'Idle', pending: 'Queued',
    running: 'Running', completed: 'Complete', failed: 'Failed',
  }[snap.status] || 'Idle';
  pha.textContent = snap.phase || 'idle';
}

// Stage track
function updateStages(snap) {
  const stgIdle   = document.getElementById('stgIdle');
  const stgScrape = document.getElementById('stgScrape');
  const stgMerge  = document.getElementById('stgMerge');
  const lineS     = document.getElementById('lineScrape');
  const lineM     = document.getElementById('lineMerge');

  [stgIdle, stgScrape, stgMerge].forEach(el => {
    el.className = 'stage-step';
  });
  lineS.className = 'stage-line';
  lineM.className = 'stage-line';

  const { phase: ph, status: st } = snap;

  if (!ph || ph === 'idle' || st === 'idle') {
    stgIdle.classList.add('active');
    return;
  }

  if (ph === 'scrape') {
    stgIdle.classList.add('done');
    lineS.classList.add(st === 'completed' ? 'done' : 'active');
    stgScrape.classList.add(
      st === 'completed' ? 'done' : st === 'failed' ? 'failed' : 'active'
    );
  }

  if (ph === 'merge') {
    stgIdle.classList.add('done');
    stgScrape.classList.add('done');
    lineS.classList.add('done');
    lineM.classList.add(st === 'completed' ? 'done' : 'active');
    stgMerge.classList.add(
      st === 'completed' ? 'done' : st === 'failed' ? 'failed' : 'active'
    );
  }

  // Update job ID in topbar
  if (snap.job_id) {
    document.getElementById('topbarJobId').textContent = snap.job_id;
  }
}

// Metric tiles
function updateMetrics(snap) {
  const setMetric = (valId, cardId, val, isActive, isDone) => {
    const vEl = document.getElementById(valId);
    const cEl = document.getElementById(cardId);
    if (val !== undefined && val !== null)
      vEl.textContent = typeof val === 'number' ? val.toLocaleString() : val;
    cEl.className = `metric-card${isActive ? ' active' : isDone ? ' done' : ''}`;
  };

  const running = snap.status === 'running';
  const done    = snap.status === 'completed';

  setMetric('mvFound',   'mcFound',   snap.chapters_found,    running && snap.phase === 'scrape', done);
  setMetric('mvFetched', 'mcFetched', snap.chapters_fetched,  running && snap.phase === 'scrape', done);
  setMetric('mvAssets',  'mcAssets',  snap.assets_downloaded, running && snap.phase === 'scrape', done);

  if (snap.total_pages > 0) {
    setMetric('mvPages', 'mcPages', snap.total_pages, running && snap.phase === 'merge', done && snap.phase === 'merge');
  }
}

// Progress bar
function updateProgress(snap) {
  const fill  = document.getElementById('progFill');
  const pct   = document.getElementById('progPct');
  const desc  = document.getElementById('progDesc');
  const title = document.getElementById('progTitle');

  let percent = 0;

  if (snap.phase === 'scrape' && snap.chapters_found > 0) {
    percent = Math.round((snap.chapters_fetched / snap.chapters_found) * 100);
    title.textContent = 'Progress — Scraping';
    desc.textContent  = `${snap.chapters_fetched} / ${snap.chapters_found} chapters fetched`;
  } else if (snap.phase === 'merge') {
    const total = snap.chapters_found || 64;
    percent = Math.round((snap.chapters_merged / total) * 100);
    title.textContent = 'Progress — Merging';
    desc.textContent  = `${snap.chapters_merged} chapters · ${snap.total_pages} pages`;
  } else if (snap.status === 'idle') {
    desc.textContent = 'Waiting to start…';
  }

  if (snap.status === 'completed') {
    percent = 100;
    desc.textContent  = 'All phases complete.';
    title.textContent = 'Progress';
  }

  if (snap.status === 'failed') {
    desc.textContent = 'Pipeline failed.';
  }

  percent = Math.min(100, Math.max(0, percent));
  fill.style.width = `${percent}%`;
  pct.textContent  = `${percent}%`;

  const fillClass = snap.status === 'completed' ? 'done'
                  : snap.status === 'failed'    ? 'failed' : '';
  fill.className = `prog-track-inner${fillClass ? ` ${fillClass}` : ''}`;
  pct.className  = `prog-pct${fillClass ? ` ${fillClass}` : ''}`;
}

/* ═══════════════════════════════════════════════════════════
   14. ASSET SIDEBAR
═══════════════════════════════════════════════════════════ */
function fmtBytes(b) {
  if (b < 1024)       return `${b}B`;
  if (b < 1048576)    return `${(b / 1024).toFixed(0)}KB`;
  return `${(b / 1048576).toFixed(1)}MB`;
}

function fileExt(name) {
  return name.split('.').pop().toLowerCase();
}

function fileEmoji(cat, name) {
  if (cat === 'pdfs')   return '📄';
  if (cat === 'images') return /\.(svg)$/i.test(name) ? '◈' : '🖼';
  if (cat === 'code') {
    if (/\.py$/i.test(name))    return '🐍';
    if (/\.[ch]$/i.test(name))  return '⚙';
    if (/\.sh$/i.test(name))    return '⬡';
    return '{ }';
  }
  return '📦';
}

async function refreshAssets() {
  const cats = [
    { cat: 'pdfs',     badgeId: 'badge-pdfs',     listId: 'list-pdfs' },
    { cat: 'images',   badgeId: 'badge-images',   listId: 'list-images' },
    { cat: 'code',     badgeId: 'badge-code',     listId: 'list-code' },
    { cat: 'homework', badgeId: 'badge-homework',  listId: 'list-homework' },
  ];

  let grandTotal = 0;

  for (const { cat, badgeId, listId } of cats) {
    try {
      const r = await fetch(API.assetsList(cat), { headers: NGROK });
      if (!r.ok) continue;
      const data = await r.json();
      grandTotal += data.total;

      // Badge
      const badge = document.getElementById(badgeId);
      badge.textContent = data.total;
      badge.className   = `cat-badge${data.total > 0 ? ' has' : ''}`;

      // File list
      renderFileList(cat, data.assets || [], listId);
    } catch { /* non-fatal */ }
  }

  // Total count
  const tc = document.getElementById('sbTotalCount');
  tc.textContent = `${grandTotal} file${grandTotal !== 1 ? 's' : ''}`;
  tc.className   = `sb-total-count${grandTotal > 0 ? ' has' : ''}`;
}

function renderFileList(cat, assets, listId) {
  const ul = document.getElementById(listId);

  if (!assets.length) {
    ul.innerHTML = `<li class="cat-empty">no files yet</li>`;
    return;
  }

  const MAX   = 60;
  const shown = assets.slice(0, MAX);
  const extra = assets.length - shown.length;

  ul.innerHTML = shown.map(a => {
    // Safely encode for use in onclick attribute
    const safeAsset = JSON.stringify(a).replace(/'/g, '&#39;');
    return `<li class="file-item" onclick='openFileViewer(${JSON.stringify(cat)}, ${safeAsset})'>
      <span class="file-dot"></span>
      <span class="file-name" title="${a.filename}">${a.filename}</span>
      <span class="file-size">${fmtBytes(a.size_bytes)}</span>
    </li>`;
  }).join('') + (extra > 0 ? `<li class="cat-more">+${extra} more</li>` : '');
}

// Category toggle (expand / collapse)
document.querySelectorAll('.cat-toggle').forEach(btn => {
  btn.addEventListener('click', () => {
    const cat    = btn.dataset.cat;
    const list   = document.getElementById(`list-${cat}`);
    const isOpen = btn.classList.contains('open');
    btn.classList.toggle('open', !isOpen);
    list.classList.toggle('open', !isOpen);
  });
});

// Refresh button
document.getElementById('sbRefreshBtn').addEventListener('click', refreshAssets);

/* ═══════════════════════════════════════════════════════════
   15. FILE VIEWER MODAL
   click on file → preview only (inline, no download)
   download button → attachment download
═══════════════════════════════════════════════════════════ */
function openFileViewer(cat, asset) {
  const ext        = fileExt(asset.filename);
  const previewUrl = API.previewAsset(cat, asset.filename);
  const downloadUrl = API.downloadAsset(cat, asset.filename);

  // Header
  document.getElementById('viewerIcon').textContent    = fileEmoji(cat, asset.filename);
  document.getElementById('viewerFilename').textContent = asset.filename;

  const pills = document.getElementById('viewerPills');
  pills.innerHTML =
    `<span class="meta-pill">${fmtBytes(asset.size_bytes)}</span>` +
    `<span class="meta-pill">${cat}</span>` +
    `<span class="meta-pill">.${ext}</span>`;

  // Download button — triggers attachment download
  const dlBtn = document.getElementById('btnViewerDl');
  dlBtn.onclick = () => doDownloadAsset(cat, asset.filename);

  // Content — preview only, never auto-downloads
  const body = document.getElementById('viewerBody');
  body.innerHTML = '';

  if (cat === 'pdfs') {
    // PDF → inline iframe via /preview/asset/...
    body.innerHTML =
      `<div class="viewer-pdf">
         <iframe src="${previewUrl}" title="${asset.filename}"></iframe>
       </div>`;

  } else if (cat === 'images') {
    // Image → <img> tag
    body.innerHTML =
      `<div class="viewer-img">
         <img src="${previewUrl}" alt="${asset.filename}"
              onerror="this.closest('.viewer-img').innerHTML=
                '<p style=color:var(--t4);font-family:var(--f-mono);padding:24px>
                Could not load image.</p>'" />
       </div>`;

  } else if (['c','h','cpp','cc','py','sh','pl','s','asm'].includes(ext)) {
    // Code → fetch text and display in <pre>
    body.innerHTML = `<div class="viewer-code"><pre id="viewerPre">Loading…</pre></div>`;
    fetch(previewUrl, { headers: NGROK })
      .then(r => r.text())
      .then(text => {
        const pre = document.getElementById('viewerPre');
        if (pre) pre.textContent = text;
      })
      .catch(() => {
        const pre = document.getElementById('viewerPre');
        if (pre) pre.textContent = 'Could not load file content.';
      });

  } else {
    // No preview available
    body.innerHTML =
      `<div class="viewer-noprev">
         <div class="viewer-noprev-icon">📄</div>
         <div>No preview for <strong>.${ext}</strong> files</div>
         <div style="margin-top:6px;font-size:11px">
           Use the Download button above to save this file.
         </div>
       </div>`;
  }

  openBackdrop('viewerBackdrop');
}

/* ── Merged PDF viewer ── */
function openMergedViewer() {
  if (!mergeReady) return;
  const snap = window.__mergeSnap || {};

  document.getElementById('viewerIcon').textContent    = '📕';
  document.getElementById('viewerFilename').textContent = 'OSTEP_Complete_Book.pdf';

  const pills = document.getElementById('viewerPills');
  pills.innerHTML = [
    snap.total_pages    ? `<span class="meta-pill accent">${snap.total_pages} pages</span>` : '',
    snap.file_size_mb   ? `<span class="meta-pill accent">${snap.file_size_mb.toFixed(1)} MB</span>` : '',
    snap.chapters_merged ? `<span class="meta-pill">${snap.chapters_merged} chapters</span>` : '',
    `<span class="meta-pill">PDF</span>`,
  ].join('');

  // Download button
  document.getElementById('btnViewerDl').onclick = doDownloadMerged;

  // Preview via /preview/merged (inline, not download)
  const body = document.getElementById('viewerBody');
  body.innerHTML =
    `<div class="viewer-pdf">
       <iframe src="${API.previewMerged}" title="OSTEP Complete Book"></iframe>
     </div>`;

  openBackdrop('viewerBackdrop');
}

/* ── Viewer close ── */
document.getElementById('btnViewerClose').addEventListener('click', () => {
  closeBackdrop('viewerBackdrop');
  // Clear iframe to stop PDF rendering in background
  document.getElementById('viewerBody').innerHTML = '';
});

/* ═══════════════════════════════════════════════════════════
   16. ERROR MODAL
═══════════════════════════════════════════════════════════ */
function showError(title, message, detail) {
  document.getElementById('errTitle').textContent = title;
  document.getElementById('errMsg').textContent   = message;

  const det = document.getElementById('errDetails');
  const pre = document.getElementById('errDetailPre');
  if (detail) {
    det.style.display  = 'block';
    pre.textContent    = detail;
  } else {
    det.style.display = 'none';
  }

  openBackdrop('errBackdrop');
}

document.getElementById('btnErrClose').addEventListener('click', () => {
  closeBackdrop('errBackdrop');
});

/* ═══════════════════════════════════════════════════════════
   17. BACKDROP HELPERS
═══════════════════════════════════════════════════════════ */
function openBackdrop(id)  { document.getElementById(id).classList.add('open'); }
function closeBackdrop(id) { document.getElementById(id).classList.remove('open'); }

// Click outside modal to close
['viewerBackdrop', 'errBackdrop'].forEach(id => {
  document.getElementById(id).addEventListener('click', (e) => {
    if (e.target.id === id) {
      closeBackdrop(id);
      if (id === 'viewerBackdrop') {
        document.getElementById('viewerBody').innerHTML = '';
      }
    }
  });
});

// Escape key
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('.modal-backdrop.open').forEach(el => {
    closeBackdrop(el.id);
    if (el.id === 'viewerBackdrop') {
      document.getElementById('viewerBody').innerHTML = '';
    }
  });
});

/* ═══════════════════════════════════════════════════════════
   18. HELPERS — fetch with timeout
═══════════════════════════════════════════════════════════ */
/**
 * fetch() wrapper with a hard timeout.
 * Uses AbortController so the browser cancels the request and
 * doesn't leave it hanging indefinitely (important for ngrok tunnels
 * that may take a long time to respond or silently drop requests).
 */
async function fetchWithTimeout(url, options = {}, timeoutMs = 5000) {
  const ctrl = new AbortController();
  const tid  = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } finally {
    clearTimeout(tid);
  }
}

/* ═══════════════════════════════════════════════════════════
   19. INIT
   Runs exactly once per page load.
   Health check + pipeline restore + asset load.
═══════════════════════════════════════════════════════════ */
(async function init() {
  // ── Health check — 5 s timeout ─────────────────────────────────
  setHealthState('checking', 'Checking…');
  try {
    const r = await fetchWithTimeout(API.health, { headers: NGROK }, 5000);
    const d = await r.json();
    setHealthState('ok', `API v${d.version} ✓`);

    if (d.merged_pdf_ready) {
      markMergeReady({ total_pages: 0, file_size_mb: 0, chapters_merged: 0 });
    }
  } catch (e) {
    const reason = e.name === 'AbortError' ? 'timeout (5s)' : e.message;
    setHealthState('err', `Unreachable (${reason})`);
    // Still attempt state restore below — API may be up even if health is slow.
  }

  // ── Restore pipeline state ────────────────────────────────────
  // Called once on load. If a job is already running we re-attach
  // the WebSocket without showing "initialising" again.
  try {
    const r = await fetchWithTimeout(API.scrapeStatus, { headers: NGROK }, 5000);
    if (r.ok) {
      const snap = await r.json();
      pipeState = snap;

      // Seed dedup guards from restored snapshot so the first WS
      // message doesn't look like a status transition and spam the log.
      prevStatus  = snap.status;
      prevPhase   = snap.phase;
      prevFetched = snap.chapters_fetched ?? -1;
      prevMerged  = snap.chapters_merged  ?? -1;

      // Silently update UI (no log calls during restore)
      updateSidebarStatus(snap);
      updateStages(snap);
      updateMetrics(snap);
      updateProgress(snap);

      if (['pending', 'running'].includes(snap.status)) {
        setBtnState('running');
        startTimer();
        connectWS();
      }

      if (snap.phase === 'merge' && snap.status === 'completed') {
        if (!mergeReady) markMergeReady(snap);
        autoMergeTriggered = true;
      }

      if (snap.phase === 'scrape' && snap.status === 'completed') {
        autoMergeTriggered = true;
      }
    }
  } catch { /* API may not be reachable yet — safe to ignore */ }

  // ── Load asset sidebar ────────────────────────────────────────
  await refreshAssets();

  initComplete = true;
})();

/* ── Health indicator helper ── */
function setHealthState(state, text) {
  const dot = document.getElementById('healthDot');
  const lbl = document.getElementById('healthTxt');
  dot.className = `health-dot${state === 'ok' ? ' ok' : state === 'err' ? ' err' : ''}`;
  lbl.textContent = text;
}