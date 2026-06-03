// ============================================================
// MultiAgent FX Engine — Dashboard
// ============================================================

const API = 'http://localhost:8080';
const WS_URL = 'ws://localhost:8080/ws';
const REFRESH_INTERVAL = 30_000; // 30 s

let equityChart = null;
let pnlChart = null;
let wsConn = null;
let wsReconnectDelay = 1000;
let currentPage = 0;
const PAGE_SIZE = 20;

// ─── Bootstrap ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await refreshAll();
  setInterval(refreshAll, REFRESH_INTERVAL);
  connectWebSocket();
  setInterval(updateClock, 1000);
  updateClock();
});

async function refreshAll() {
  const el = document.getElementById('last-update');
  if (el) el.textContent = 'Updated ' + new Date().toLocaleTimeString();

  await Promise.allSettled([
    loadSystemStatus(),
    loadOpenPositions(),
    loadEquityCurve(),
    loadDailyPnl(),
    loadPerformance(),
    loadParams(),
    loadLessons(),
    loadEvents(),
    loadTradeHistory(currentPage),
  ]);
}

// ─── System Status ───────────────────────────────────────────
async function loadSystemStatus() {
  const data = await apiFetch('/api/system/status');

  // Balance
  const balEl = document.getElementById('balance');
  if (balEl) {
    balEl.textContent = data ? '$' + formatMoney(data.balance) : '—';
  }

  // Daily PnL
  const pnlEl = document.getElementById('daily-pnl');
  if (pnlEl) {
    if (data) {
      const sign = data.daily_pnl >= 0 ? '+' : '';
      pnlEl.textContent = sign + '$' + formatMoney(data.daily_pnl);
      pnlEl.className = 'header-stat-value price ' +
        (data.daily_pnl >= 0 ? 'pnl-positive' : 'pnl-negative');
    } else {
      pnlEl.textContent = '—';
      pnlEl.className = 'header-stat-value price';
    }
  }

  // Drawdown
  const ddEl = document.getElementById('drawdown');
  if (ddEl) {
    ddEl.textContent = data ? formatPct(data.drawdown_pct / 100) : '—';
    if (data && data.drawdown_pct > 5) {
      ddEl.className = 'header-stat-value price pnl-negative';
    } else {
      ddEl.className = 'header-stat-value price';
    }
  }

  // Mode badge
  const badgeEl = document.getElementById('mode-badge');
  if (badgeEl && data) {
    if (data.mode === 'LIVE') {
      badgeEl.className = 'badge-live';
      badgeEl.textContent = 'LIVE';
    } else {
      badgeEl.className = 'badge-paper';
      badgeEl.textContent = 'PAPER';
    }
  }

  // Kill switch banner
  const banner = document.getElementById('kill-switch-banner');
  if (banner) {
    banner.style.display = (data && data.kill_switch_active) ? 'block' : 'none';
  }
}

// ─── Open Positions ──────────────────────────────────────────
async function loadOpenPositions() {
  const data = await apiFetch('/api/trades/open');
  const container = document.getElementById('open-positions-list');
  const countEl = document.getElementById('open-count');

  if (!container) return;

  const trades = Array.isArray(data) ? data : [];

  if (countEl) countEl.textContent = String(trades.length);

  if (trades.length === 0) {
    container.innerHTML = '<p class="empty-state">No open positions</p>';
    return;
  }

  container.innerHTML = trades.map(t => {
    const sideClass = t.side === 'BUY' ? 'side-buy' : 'side-sell';
    const slText   = t.stop_loss   ? formatPrice(t.stop_loss)   : '—';
    const tpText   = t.take_profit ? formatPrice(t.take_profit) : '—';

    return `
      <div class="position-card">
        <div class="position-header">
          <span class="position-symbol">${escHtml(t.symbol)}</span>
          <span class="${sideClass}">${escHtml(t.side)}</span>
        </div>
        <div class="position-row">
          <span>Units</span>
          <span>${t.units != null ? t.units.toLocaleString() : '—'}</span>
        </div>
        <div class="position-row">
          <span>Entry</span>
          <span>${formatPrice(t.entry_price)}</span>
        </div>
        <div class="position-row">
          <span>SL / TP</span>
          <span>${slText} / ${tpText}</span>
        </div>
        <div class="position-row">
          <span>Opened</span>
          <span>${timeAgo(t.open_time)}</span>
        </div>
      </div>`;
  }).join('');
}

// ─── Equity Curve ────────────────────────────────────────────
async function loadEquityCurve() {
  const data = await apiFetch('/api/metrics/equity-curve?days=30');
  const canvas = document.getElementById('equity-chart');
  if (!canvas) return;

  // Expect: { dates: [...], values: [...] }  OR  array of { date, equity }
  let labels = [];
  let values = [];

  if (data) {
    if (Array.isArray(data)) {
      labels = data.map(d => d.date || d.ts || '');
      values = data.map(d => d.equity || d.value || 0);
    } else if (data.dates && data.values) {
      labels = data.dates;
      values = data.values;
    }
  }

  if (equityChart) {
    equityChart.destroy();
    equityChart = null;
  }

  const ctx = canvas.getContext('2d');

  // Gradient fill under curve
  const gradient = ctx.createLinearGradient(0, 0, 0, 180);
  gradient.addColorStop(0,   'rgba(0, 255, 136, 0.25)');
  gradient.addColorStop(1,   'rgba(0, 255, 136, 0)');

  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: '#00ff88',
        borderWidth: 2,
        backgroundColor: gradient,
        fill: true,
        tension: 0.35,
        pointRadius: values.length > 15 ? 0 : 3,
        pointHoverRadius: 5,
        pointBackgroundColor: '#00ff88',
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a2235',
          borderColor: 'rgba(255,255,255,0.08)',
          borderWidth: 1,
          titleColor: '#94a3b8',
          bodyColor: '#e2e8f0',
          callbacks: {
            label: ctx => '$' + ctx.parsed.y.toFixed(2),
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#94a3b8', maxTicksLimit: 6, maxRotation: 0 },
          grid:  { color: 'rgba(255,255,255,0.05)' },
        },
        y: {
          ticks: { color: '#94a3b8', callback: v => '$' + v.toFixed(0) },
          grid:  { color: 'rgba(255,255,255,0.05)' },
        },
      },
    },
  });

  if (values.length === 0) {
    // Render placeholder message on canvas
    ctx.fillStyle = '#475569';
    ctx.font = '13px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No equity data available', canvas.width / 2, 90);
  }
}

// ─── Daily P&L ───────────────────────────────────────────────
async function loadDailyPnl() {
  const data = await apiFetch('/api/metrics/daily-pnl?days=30');
  const canvas = document.getElementById('pnl-chart');
  if (!canvas) return;

  let labels = [];
  let values = [];

  if (data) {
    if (Array.isArray(data)) {
      labels = data.map(d => d.date || d.day || '');
      values = data.map(d => d.pnl || d.value || 0);
    } else if (data.dates && data.values) {
      labels = data.dates;
      values = data.values;
    }
  }

  if (pnlChart) {
    pnlChart.destroy();
    pnlChart = null;
  }

  const ctx = canvas.getContext('2d');

  pnlChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: values.map(v => v >= 0 ? 'rgba(0, 255, 136, 0.6)' : 'rgba(255, 68, 85, 0.6)'),
        borderColor:     values.map(v => v >= 0 ? 'rgba(0, 255, 136, 0.9)' : 'rgba(255, 68, 85, 0.9)'),
        borderWidth: 1,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a2235',
          borderColor: 'rgba(255,255,255,0.08)',
          borderWidth: 1,
          titleColor: '#94a3b8',
          bodyColor: '#e2e8f0',
          callbacks: {
            label: ctx => (ctx.parsed.y >= 0 ? '+' : '') + '$' + ctx.parsed.y.toFixed(2),
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#94a3b8', maxTicksLimit: 8, maxRotation: 0 },
          grid:  { color: 'rgba(255,255,255,0.05)' },
        },
        y: {
          ticks: { color: '#94a3b8', callback: v => (v >= 0 ? '+' : '') + '$' + v.toFixed(0) },
          grid:  { color: 'rgba(255,255,255,0.05)' },
        },
      },
    },
  });

  if (values.length === 0) {
    ctx.fillStyle = '#475569';
    ctx.font = '13px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No P&L data available', canvas.width / 2, 90);
  }
}

// ─── Performance Stats ───────────────────────────────────────
async function loadPerformance() {
  const data = await apiFetch('/api/metrics/performance?days=30');
  const container = document.getElementById('performance-stats');
  if (!container) return;

  if (!data) {
    container.innerHTML = '<p class="empty-state">No performance data</p>';
    return;
  }

  const winRate    = data.win_rate    != null ? (data.win_rate * 100).toFixed(1) + '%' : '—';
  const profitFact = data.profit_factor != null ? data.profit_factor.toFixed(2) : '—';
  const totalPnl   = data.total_pnl   != null ? formatPnl(data.total_pnl) : '—';
  const totalTrades = data.total_trades != null ? String(data.total_trades) : '—';
  const avgWin     = data.avg_win     != null ? '+$' + data.avg_win.toFixed(2) : '—';
  const avgLoss    = data.avg_loss    != null ? '-$' + Math.abs(data.avg_loss).toFixed(2) : '—';
  const sharpe     = data.sharpe_ratio != null ? data.sharpe_ratio.toFixed(2) : '—';
  const maxDD      = data.max_drawdown != null ? formatPct(data.max_drawdown) : '—';

  const pnlClass = data.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
  const pfClass  = (data.profit_factor || 0) >= 1 ? 'pnl-positive' : 'pnl-negative';

  container.innerHTML = `
    <div class="stat-row">
      <span class="stat-label">Win Rate</span>
      <span class="stat-value">${winRate}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Profit Factor</span>
      <span class="stat-value ${pfClass}">${profitFact}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Total PnL (30d)</span>
      <span class="stat-value ${pnlClass}">${totalPnl}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Total Trades</span>
      <span class="stat-value">${totalTrades}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Avg Win</span>
      <span class="stat-value pnl-positive">${avgWin}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Avg Loss</span>
      <span class="stat-value pnl-negative">${avgLoss}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Sharpe Ratio</span>
      <span class="stat-value">${sharpe}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Max Drawdown</span>
      <span class="stat-value pnl-negative">${maxDD}</span>
    </div>`;
}

// ─── Dynamic Params ──────────────────────────────────────────
async function loadParams() {
  const data = await apiFetch('/api/params/');
  const container = document.getElementById('params-list');
  if (!container) return;

  const params = Array.isArray(data) ? data : [];

  if (params.length === 0) {
    container.innerHTML = '<p class="empty-state">No parameters found</p>';
    return;
  }

  container.innerHTML = params.map(p => {
    const updatedBy = p.updated_by ? escHtml(p.updated_by) : '—';
    const reason    = p.change_reason ? escHtml(p.change_reason.substring(0, 40)) + (p.change_reason.length > 40 ? '…' : '') : '';
    const val       = p.param_value != null ? p.param_value : '';

    return `
      <div class="param-item" data-param="${escHtml(p.param_name)}">
        <div>
          <div class="param-name" title="${escHtml(p.description || p.param_name)}">${escHtml(p.param_name)}</div>
          <div class="param-meta">by ${updatedBy}${reason ? ' — ' + reason : ''}</div>
        </div>
        <input
          class="param-value-input"
          type="number"
          step="any"
          value="${val}"
          data-original="${val}"
          data-param-name="${escHtml(p.param_name)}"
          aria-label="Value for ${escHtml(p.param_name)}"
        />
        <button
          class="btn-save"
          onclick="handleSaveParam('${escHtml(p.param_name)}', this)"
          aria-label="Save ${escHtml(p.param_name)}"
        >Save</button>
      </div>`;
  }).join('');
}

function handleSaveParam(paramName, btnEl) {
  const item  = btnEl.closest('.param-item');
  const input = item ? item.querySelector('.param-value-input') : null;
  if (!input) return;

  const value = parseFloat(input.value);
  if (isNaN(value)) {
    showToast('Invalid value — must be a number', 'error');
    return;
  }

  const reason = 'Manual update via dashboard';
  saveParam(paramName, value, reason);
}

async function saveParam(paramName, value, reason) {
  const result = await apiFetch(`/api/params/${encodeURIComponent(paramName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value, reason }),
  });

  if (result !== null) {
    showToast(`Parameter "${paramName}" saved (${value})`, 'success');
    await loadParams();
  } else {
    showToast(`Failed to save "${paramName}"`, 'error');
  }
}

// ─── Critic Lessons ──────────────────────────────────────────
async function loadLessons() {
  const data = await apiFetch('/api/system/lessons?limit=8');
  const container = document.getElementById('lessons-list');
  if (!container) return;

  const lessons = Array.isArray(data) ? data : [];

  if (lessons.length === 0) {
    container.innerHTML = '<p class="empty-state">No lessons yet</p>';
    return;
  }

  container.innerHTML = lessons.map(l => {
    const outcome   = l.outcome || 'UNKNOWN';
    const outClass  = outcome === 'WIN' ? 'pnl-positive' : 'pnl-negative';
    const errType   = l.error_type || l.type || 'UNKNOWN';
    const lessonTxt = l.lesson || l.description || l.notes || '—';
    const pnl       = l.pnl != null ? formatPnl(l.pnl) : '—';
    const pnlClass  = (l.pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';

    return `
      <div class="lesson-card">
        <div class="lesson-header">
          <span class="lesson-error-type">${escHtml(errType)}</span>
          <span class="lesson-pnl ${pnlClass}">${pnl}</span>
        </div>
        <p class="lesson-text">${escHtml(lessonTxt)}</p>
        <div style="margin-top:5px; font-size:10px;">
          <span class="${outClass}" style="font-weight:700;">${escHtml(outcome)}</span>
          ${l.symbol ? '<span style="color:var(--text-muted); margin-left:6px;">' + escHtml(l.symbol) + '</span>' : ''}
        </div>
      </div>`;
  }).join('');
}

// ─── System Events ───────────────────────────────────────────
async function loadEvents() {
  const data = await apiFetch('/api/system/events?limit=10');
  const container = document.getElementById('events-list');
  if (!container) return;

  const events = Array.isArray(data) ? data : [];

  if (events.length === 0) {
    container.innerHTML = '<p class="empty-state">No events</p>';
    return;
  }

  container.innerHTML = events.map(e => {
    const severity = (e.severity || e.level || 'info').toLowerCase();
    const iconMap  = { info: '&#9432;', warning: '&#9888;', error: '&#9888;', success: '&#10003;' };
    const icon     = iconMap[severity] || '&#9432;';
    const typeText = e.event_type || e.type || e.message || 'Event';
    const ts       = timeAgo(e.timestamp || e.ts || e.created_at);

    return `
      <div class="event-item">
        <span class="event-icon ${severity}">${icon}</span>
        <div class="event-body">
          <div class="event-type">${escHtml(typeText)}</div>
          <div class="event-time">${ts}</div>
        </div>
      </div>`;
  }).join('');
}

// ─── Trade History ───────────────────────────────────────────
async function loadTradeHistory(page = 0) {
  const offset = page * PAGE_SIZE;
  const data   = await apiFetch(`/api/trades/?limit=${PAGE_SIZE}&offset=${offset}&status=CLOSED`);
  const tbody  = document.getElementById('trade-history-body');
  const info   = document.getElementById('pagination-info');
  const prevBtn = document.getElementById('prev-page');
  const nextBtn = document.getElementById('next-page');

  if (!tbody) return;

  const trades = Array.isArray(data) ? data : (data && Array.isArray(data.items) ? data.items : []);
  const total  = data && data.total != null ? data.total : trades.length;

  if (prevBtn) prevBtn.disabled = page === 0;
  if (nextBtn) nextBtn.disabled = trades.length < PAGE_SIZE;

  if (info) {
    const from = offset + 1;
    const to   = offset + trades.length;
    info.textContent = trades.length > 0 ? `Showing ${from}–${to} trades` : 'No trades';
  }

  if (trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty-row">No closed trades found</td></tr>';
    return;
  }

  tbody.innerHTML = trades.map(t => {
    const sideClass = t.side === 'BUY' ? 'side-buy' : 'side-sell';
    const pnlClass  = (t.pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
    const statusBadge = t.status === 'OPEN'
      ? '<span class="status-open">OPEN</span>'
      : '<span class="status-closed">CLOSED</span>';

    return `
      <tr class="trade-row">
        <td>${formatDate(t.open_time)}</td>
        <td>${escHtml(t.symbol || '—')}</td>
        <td class="${sideClass}">${escHtml(t.side || '—')}</td>
        <td>${t.units != null ? t.units.toLocaleString() : '—'}</td>
        <td>${formatPrice(t.entry_price)}</td>
        <td>${formatPrice(t.exit_price)}</td>
        <td>${formatPrice(t.stop_loss)}</td>
        <td>${formatPrice(t.take_profit)}</td>
        <td class="${pnlClass}">${formatPnl(t.pnl)}</td>
        <td class="${pnlClass}">${formatPct(t.pnl_pct)}</td>
        <td>${statusBadge}</td>
      </tr>`;
  }).join('');
}

document.getElementById('prev-page')?.addEventListener('click', () => {
  if (currentPage > 0) { currentPage--; loadTradeHistory(currentPage); }
});
document.getElementById('next-page')?.addEventListener('click', () => {
  currentPage++; loadTradeHistory(currentPage);
});

// ─── WebSocket ───────────────────────────────────────────────
function connectWebSocket() {
  try {
    wsConn = new WebSocket(WS_URL);
  } catch (e) {
    console.error('[WS] Could not create WebSocket:', e.message);
    setWsStatus(false);
    scheduleReconnect();
    return;
  }

  wsConn.addEventListener('open', () => {
    setWsStatus(true);
    wsReconnectDelay = 1000;
    console.info('[WS] Connected');
  });

  wsConn.addEventListener('message', event => {
    try {
      const msg = JSON.parse(event.data);
      handleWsMessage(msg);
    } catch (e) {
      console.error('[WS] Parse error:', e.message);
    }
  });

  wsConn.addEventListener('close', () => {
    setWsStatus(false);
    console.warn('[WS] Disconnected. Reconnecting in', wsReconnectDelay, 'ms');
    scheduleReconnect();
  });

  wsConn.addEventListener('error', err => {
    console.error('[WS] Error:', err.message || 'unknown error');
  });
}

function scheduleReconnect() {
  setTimeout(() => {
    connectWebSocket();
  }, wsReconnectDelay);
  wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000); // exponential backoff, cap 30s
}

function setWsStatus(connected) {
  const dot   = document.getElementById('connection-status');
  const label = document.getElementById('conn-label');
  if (dot) {
    dot.className = 'status-dot ' + (connected ? 'connected' : 'disconnected');
    dot.title     = connected ? 'WebSocket connected' : 'WebSocket disconnected';
  }
  if (label) label.textContent = connected ? 'Live' : 'Disconnected';
}

function handleWsMessage(msg) {
  const type = msg.type || '';
  const data = msg.data || {};

  switch (type) {
    case 'trade_opened': {
      const side   = data.side || '?';
      const symbol = data.symbol || '?';
      const price  = data.entry_price != null ? formatPrice(data.entry_price) : '?';
      showToast(`TRADE OPENED: ${side} ${symbol} @ ${price}`, 'success');
      loadOpenPositions();
      loadTradeHistory(currentPage);
      loadSystemStatus();
      break;
    }

    case 'trade_closed': {
      const pnl  = data.pnl != null ? data.pnl : null;
      const type2 = pnl != null && pnl >= 0 ? 'success' : 'error';
      const pnlTxt = pnl != null ? formatPnl(pnl) : '—';
      showToast(`TRADE CLOSED: PnL ${pnlTxt}`, type2);
      loadOpenPositions();
      loadTradeHistory(currentPage);
      loadSystemStatus();
      loadPerformance();
      break;
    }

    case 'kill_switch': {
      showToast('KILL SWITCH ACTIVATED — Trading halted', 'error', 8000);
      const banner = document.getElementById('kill-switch-banner');
      if (banner) banner.style.display = 'block';
      loadSystemStatus();
      break;
    }

    case 'alert': {
      const alertMsg = data.message || data.msg || 'Alert received';
      showToast(alertMsg, 'warning');
      break;
    }

    default:
      console.info('[WS] Unknown message type:', type);
  }
}

// ─── Utils ───────────────────────────────────────────────────
function showToast(message, type = 'success', duration = 5000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  // Limit to 5 simultaneous toasts
  const existing = container.querySelectorAll('.toast');
  if (existing.length >= 5) {
    existing[0].remove();
  }

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  const labelMap = { success: 'Success', error: 'Error', warning: 'Warning' };
  toast.innerHTML = `
    <div class="toast-title">${labelMap[type] || 'Notice'}</div>
    <div class="toast-body">${escHtml(message)}</div>`;

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('toast-exit');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    // Fallback removal in case animation doesn't fire
    setTimeout(() => toast.remove(), 400);
  }, duration);
}

function formatPrice(price) {
  return price != null ? price.toFixed(5) : '—';
}

function formatMoney(val) {
  if (val == null) return '—';
  return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPnl(pnl) {
  if (pnl == null) return '—';
  const sign = pnl >= 0 ? '+' : '';
  return `${sign}${pnl.toFixed(2)}`;
}

function formatPct(pct) {
  if (pct == null) return '—';
  return `${pct >= 0 ? '+' : ''}${(pct * 100).toFixed(2)}%`;
}

function formatDate(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
           d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
  } catch {
    return '—';
  }
}

function timeAgo(isoStr) {
  if (!isoStr) return '—';
  try {
    const diff = Date.now() - new Date(isoStr).getTime();
    if (diff < 0) return 'just now';
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return '—';
  }
}

function updateClock() {
  const el = document.getElementById('utc-clock');
  if (el) el.textContent = new Date().toUTCString().slice(17, 25) + ' UTC';
}

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function apiFetch(path, options = {}) {
  try {
    const res = await fetch(API + path, { ...options, signal: AbortSignal.timeout(10000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.error(`[API] ${path}:`, e.message);
    }
    return null;
  }
}
