/* ============================================================
   TradeBot — Frontend SPA
   ============================================================ */

/* ── State ─────────────────────────────────────────────────── */
const State = {
  route: 'dashboard',
  params: {},
  account: null,
  positions: [],
  rules: [],
  trades: [],
  signals: [],      // last 20 real-time signals received via WS
  status: { ibkr_connected: false, bot_running: false, is_paper: true },
  marketSymbol: 'AAPL',
  marketBars: [],
  chartInstance: null,   // TradingView Lightweight Charts instance
  chartSeries: null,     // Candlestick series reference
  rsiChart: null,
  rsiSeries: null,
  macdChart: null,
  stochChart: null,
  indicatorSeries: {},   // key → series/object
  activeIndicators: new Set(),
  // Watchlist  (null = never fetched, [] = fetching/empty)
  watchlist: null,
  watchlistSymbols: ['BTC-USD', 'ETH-USD', 'AAPL', 'TSLA', 'SPY', 'QQQ', 'NVDA'],
  // Trade journal
  selectedTrade: null,
  tradeDetailChart: null,
};

const API = '/api';

/* ── Utilities ─────────────────────────────────────────────── */
async function apiFetch(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function fmtMoney(n) {
  if (n == null) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(n);
}

function fmtNum(n, dp = 2) {
  if (n == null) return '—';
  return Number(n).toFixed(dp);
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function pnlClass(n) { return n >= 0 ? 'pos' : 'neg'; }

/* ── Toast ─────────────────────────────────────────────────── */
let _toastTimer;
function showToast(msg, duration = 3000) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.add('hidden'), duration);
}

/* ── Modal ─────────────────────────────────────────────────── */
function showModal(html) {
  document.getElementById('modal-content').innerHTML = html;
  document.getElementById('modal').classList.remove('hidden');
  document.getElementById('modal-backdrop').classList.remove('hidden');
}
function hideModal() {
  document.getElementById('modal').classList.add('hidden');
  document.getElementById('modal-backdrop').classList.add('hidden');
}

/* ── Router ─────────────────────────────────────────────────── */
function navigate(route, params = {}) {
  // Cleanup when leaving views
  if (State.route === 'market' && route !== 'market') {
    destroyChart();
    apiFetch(`/market/${State.marketSymbol}/unsubscribe`, { method: 'POST' }).catch(() => {});
  }
  if (State.route === 'trade-detail' && route !== 'trade-detail') {
    if (State.tradeDetailChart) { State.tradeDetailChart.remove(); State.tradeDetailChart = null; }
  }
  State.route = route;
  State.params = params;
  render();
  window.scrollTo(0, 0);
  updateNav();
}

function updateNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    // trade-detail is a sub-view of trades; keep trades tab highlighted
    const effectiveRoute = State.route === 'trade-detail' ? 'trades' : State.route;
    btn.classList.toggle('active', btn.dataset.route === effectiveRoute);
  });
  const backBtn = document.getElementById('back-btn');
  backBtn.classList.toggle('hidden', !['rule-builder', 'trade-detail'].includes(State.route));
}

function render() {
  const view = document.getElementById('view');
  const title = document.getElementById('page-title');

  switch (State.route) {
    case 'dashboard':
      title.textContent = 'TradeBot';
      view.innerHTML = renderDashboard();
      bindDashboard();
      break;
    case 'rules':
      title.textContent = 'Rules';
      view.innerHTML = renderRules();
      bindRules();
      break;
    case 'rule-builder':
      title.textContent = State.params.rule ? 'Edit Rule' : 'New Rule';
      view.innerHTML = renderRuleBuilder(State.params.rule || null);
      bindRuleBuilder();
      break;
    case 'market':
      title.textContent = 'Market';
      view.innerHTML = renderMarket();
      bindMarket();
      break;
    case 'positions':
      title.textContent = 'Positions';
      view.innerHTML = renderPositions();
      bindPositions();
      // Refresh positions data in background
      refreshAccount().then(() => {
        if (State.route === 'positions') { view.innerHTML = renderPositions(); bindPositions(); }
      });
      break;
    case 'watchlist':
      title.textContent = 'Watchlist';
      view.innerHTML = renderWatchlist();
      bindWatchlist();
      break;
    case 'trades':
      title.textContent = 'Trade Log';
      view.innerHTML = renderTrades();
      bindTrades();
      break;
    case 'trade-detail':
      title.textContent = State.selectedTrade ? `${State.selectedTrade.symbol} Trade` : 'Trade Detail';
      view.innerHTML = renderTradeDetail(State.selectedTrade);
      bindTradeDetail();
      break;
  }
}

/* ══════════════════════════════════════════════════════════════
   DASHBOARD VIEW
══════════════════════════════════════════════════════════════ */
function renderDashboard() {
  const a = State.account;
  const botRunning = State.status.bot_running;
  const ibkrOk = State.status.ibkr_connected;

  const balanceHTML = a
    ? `<div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">NET LIQUIDATION</div>
          <div class="stat-value">${fmtMoney(a.balance)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">CASH</div>
          <div class="stat-value">${fmtMoney(a.cash)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">UNREALIZED P&amp;L</div>
          <div class="stat-value ${pnlClass(a.unrealized_pnl)}">${fmtMoney(a.unrealized_pnl)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">REALIZED P&amp;L</div>
          <div class="stat-value ${pnlClass(a.realized_pnl)}">${fmtMoney(a.realized_pnl)}</div>
        </div>
      </div>`
    : `<div class="card" style="text-align:center;color:var(--text2);padding:20px;">
        ${ibkrOk ? 'Loading account…' : 'Connect to IB Gateway to see account data.'}
      </div>`;

  const posHTML = State.positions.length
    ? `<div class="card">
        <div class="card-title">Open Positions</div>
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>Symbol</th><th>Qty</th><th>Avg Cost</th><th>P&amp;L</th></tr></thead>
            <tbody>
              ${State.positions.map(p => `<tr>
                <td class="fw-600">${p.symbol}</td>
                <td>${p.qty}</td>
                <td class="td-mono">${fmtMoney(p.avg_cost)}</td>
                <td class="td-mono ${pnlClass(p.unrealized_pnl)}">${fmtMoney(p.unrealized_pnl)}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>`
    : '';

  const signalsHTML = State.signals.length
    ? `<div class="card">
        <div class="card-title">Live Signals</div>
        ${State.signals.slice(0, 10).map(s => `
          <div class="signal-item">
            <div class="signal-dot ${s.action === 'BUY' ? 'buy' : 'sell'}"></div>
            <div>
              <span class="fw-600">${s.action}</span> ${s.qty} ${s.symbol}
              <span class="text-muted" style="font-size:12px;"> — ${s.rule_name}</span>
            </div>
            <div class="signal-time">${fmtTime(s.ts)}</div>
          </div>`).join('')}
      </div>`
    : '';

  return `
    <div class="bot-control">
      <div class="bot-control-info">
        <div class="bot-control-label">Automated Trading</div>
        <div class="bot-control-sub">${botRunning ? 'Bot is running' : 'Bot is stopped'} · IBKR ${ibkrOk ? 'connected' : 'disconnected'}</div>
      </div>
      <label class="toggle">
        <input type="checkbox" id="bot-toggle" ${botRunning ? 'checked' : ''} />
        <div class="toggle-track"></div>
      </label>
    </div>

    ${!ibkrOk ? `<div class="card" style="text-align:center;">
      <p class="text-muted mb-8">IB Gateway is not connected.</p>
      <button class="btn btn-primary" id="connect-ibkr-btn">Connect to IB Gateway</button>
    </div>` : ''}

    ${balanceHTML}
    ${posHTML}
    ${signalsHTML}

    <div class="card">
      <div class="card-title">Recent Trades</div>
      ${State.trades.slice(0, 5).length
        ? `<div class="tbl-wrap"><table>
            <thead><tr><th>Time</th><th>Symbol</th><th>Action</th><th>Qty</th><th>Status</th></tr></thead>
            <tbody>
              ${State.trades.slice(0, 5).map(t => `<tr>
                <td class="td-muted">${fmtTime(t.timestamp)}</td>
                <td class="fw-600">${t.symbol}</td>
                <td class="${t.action === 'BUY' ? 'td-buy' : 'td-sell'}">${t.action}</td>
                <td>${t.quantity}</td>
                <td><span class="pill pill-${t.status.toLowerCase()}">${t.status}</span></td>
              </tr>`).join('')}
            </tbody>
          </table></div>`
        : '<div class="empty"><div class="empty-text">No trades yet.</div></div>'}
    </div>
  `;
}

function bindDashboard() {
  document.getElementById('bot-toggle')?.addEventListener('change', async (e) => {
    try {
      if (e.target.checked) {
        await apiFetch('/bot/start', { method: 'POST' });
        State.status.bot_running = true;
        showToast('Bot started');
      } else {
        await apiFetch('/bot/stop', { method: 'POST' });
        State.status.bot_running = false;
        showToast('Bot stopped');
      }
      updateBadges();
    } catch (err) {
      showToast('Error: ' + err.message);
      e.target.checked = !e.target.checked;
    }
  });

  document.getElementById('connect-ibkr-btn')?.addEventListener('click', async () => {
    try {
      await apiFetch('/ibkr/connect', { method: 'POST' });
      showToast('Connected to IB Gateway');
      await refreshAll();
      render();
    } catch (err) {
      showToast('Could not connect: ' + err.message, 5000);
    }
  });
}

/* ══════════════════════════════════════════════════════════════
   RULES VIEW
══════════════════════════════════════════════════════════════ */
function renderRules() {
  if (!State.rules.length) {
    return `
      <div class="section-header">
        <h2>Automation Rules</h2>
        <button class="btn btn-primary btn-sm" id="new-rule-btn">+ New Rule</button>
      </div>
      <div class="empty">
        <div class="empty-icon">📋</div>
        <div class="empty-text">No rules yet. Create one to get started.</div>
      </div>`;
  }

  return `
    <div class="section-header">
      <h2>Automation Rules</h2>
      <button class="btn btn-primary btn-sm" id="new-rule-btn">+ New Rule</button>
    </div>
    ${State.rules.map(rule => `
      <div class="rule-item" data-id="${rule.id}">
        <div class="rule-body">
          <div class="rule-name">${rule.name}</div>
          <div class="rule-meta">
            <span class="fw-600">${rule.symbol}</span> ·
            <span class="${rule.action.type === 'BUY' ? 'text-accent' : 'text-danger'}">${rule.action.type}</span>
            ${rule.action.quantity} · ${rule.action.order_type} ·
            ${rule.conditions.length} condition${rule.conditions.length !== 1 ? 's' : ''}
          </div>
          <div class="rule-conditions">
            ${rule.conditions.map(c => `
              <span class="rule-cond-tag">${c.indicator}(${Object.values(c.params)[0] || ''}) ${c.operator} ${c.value}</span>
            `).join(` <span style="color:var(--text2);font-size:11px;">${rule.logic}</span> `)}
          </div>
          ${rule.last_triggered ? `<div class="text-muted mt-4" style="font-size:11px;">Last triggered: ${fmtDate(rule.last_triggered)}</div>` : ''}
          <div class="rule-actions">
            <button class="btn btn-ghost btn-sm edit-rule-btn" data-id="${rule.id}">Edit</button>
            <button class="btn btn-danger btn-sm delete-rule-btn" data-id="${rule.id}">Delete</button>
          </div>
        </div>
        <label class="toggle">
          <input type="checkbox" class="rule-toggle" data-id="${rule.id}" ${rule.enabled ? 'checked' : ''} />
          <div class="toggle-track"></div>
        </label>
      </div>
    `).join('')}
  `;
}

function bindRules() {
  document.getElementById('new-rule-btn')?.addEventListener('click', () => {
    navigate('rule-builder');
  });

  document.querySelectorAll('.rule-toggle').forEach(tog => {
    tog.addEventListener('change', async (e) => {
      const id = e.target.dataset.id;
      try {
        const res = await apiFetch(`/rules/${id}/toggle`, { method: 'POST' });
        const rule = State.rules.find(r => r.id === id);
        if (rule) rule.enabled = res.enabled;
        showToast(res.enabled ? 'Rule enabled' : 'Rule disabled');
      } catch (err) {
        showToast('Error: ' + err.message);
        e.target.checked = !e.target.checked;
      }
    });
  });

  document.querySelectorAll('.edit-rule-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const rule = State.rules.find(r => r.id === btn.dataset.id);
      navigate('rule-builder', { rule });
    });
  });

  document.querySelectorAll('.delete-rule-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const rule = State.rules.find(r => r.id === btn.dataset.id);
      showDeleteConfirm(rule);
    });
  });
}

function showDeleteConfirm(rule) {
  showModal(`
    <h3 style="margin-bottom:10px;">Delete Rule</h3>
    <p class="text-muted">Are you sure you want to delete <strong>${rule.name}</strong>? This cannot be undone.</p>
    <div style="display:flex;gap:10px;margin-top:16px;">
      <button class="btn btn-ghost btn-full" id="cancel-delete-btn">Cancel</button>
      <button class="btn btn-danger btn-full" id="confirm-delete-btn">Delete</button>
    </div>
  `);
  document.getElementById('cancel-delete-btn').addEventListener('click', hideModal);
  document.getElementById('confirm-delete-btn').addEventListener('click', async () => {
    try {
      await apiFetch(`/rules/${rule.id}`, { method: 'DELETE' });
      State.rules = State.rules.filter(r => r.id !== rule.id);
      hideModal();
      render();
      showToast('Rule deleted');
    } catch (err) {
      showToast('Error: ' + err.message);
    }
  });
}

/* ══════════════════════════════════════════════════════════════
   RULE BUILDER VIEW
══════════════════════════════════════════════════════════════ */
const INDICATORS = ['RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH', 'PRICE'];
const OPERATORS  = ['crosses_above', 'crosses_below', '>', '<', '>=', '<=', '=='];

function renderConditionRow(cond, idx) {
  const indOpts = INDICATORS.map(i => `<option ${cond.indicator === i ? 'selected' : ''}>${i}</option>`).join('');
  const opOpts  = OPERATORS.map(o => `<option ${cond.operator === o ? 'selected' : ''}>${o}</option>`).join('');
  const paramVal = cond.params ? Object.values(cond.params)[0] ?? '' : '';
  return `
    <div class="condition-row" data-idx="${idx}">
      <div class="form-group">
        <label class="form-label">Indicator</label>
        <select class="form-control cond-indicator">${indOpts}</select>
      </div>
      <div class="form-group">
        <label class="form-label">Period/Param</label>
        <input class="form-control cond-param" type="number" value="${paramVal}" placeholder="14" />
      </div>
      <div class="form-group">
        <label class="form-label">Operator</label>
        <select class="form-control cond-operator">${opOpts}</select>
      </div>
      <div class="form-group">
        <label class="form-label">Value</label>
        <input class="form-control cond-value" type="text" value="${cond.value ?? ''}" placeholder="30 or SMA_200" />
      </div>
      <button class="remove-cond-btn" type="button" data-idx="${idx}">×</button>
    </div>`;
}

function renderRuleBuilder(existing = null) {
  const r = existing || {
    name: '', symbol: 'AAPL', enabled: false,
    conditions: [{ indicator: 'RSI', params: { length: 14 }, operator: 'crosses_below', value: 30 }],
    logic: 'AND',
    action: { type: 'BUY', asset_type: 'STK', quantity: 100, order_type: 'MKT', limit_price: null },
    cooldown_minutes: 60,
  };

  return `
    <form id="rule-form">
      <div class="form-group">
        <label class="form-label">Rule Name</label>
        <input class="form-control" id="rf-name" type="text" value="${r.name}" placeholder="My Rule" required />
      </div>
      <div class="form-group">
        <label class="form-label">Symbol</label>
        <input class="form-control" id="rf-symbol" type="text" value="${r.symbol}" placeholder="AAPL" style="text-transform:uppercase;" required />
      </div>

      <hr class="divider" />
      <div class="section-header">
        <div class="fw-600" style="font-size:13px;">Conditions</div>
        <div style="display:flex;align-items:center;gap:8px;">
          <label class="form-label" style="margin:0;">Logic:</label>
          <select class="form-control" id="rf-logic" style="width:auto;padding:5px 8px;">
            <option ${r.logic === 'AND' ? 'selected' : ''}>AND</option>
            <option ${r.logic === 'OR'  ? 'selected' : ''}>OR</option>
          </select>
        </div>
      </div>

      <div id="conditions-container">
        ${r.conditions.map((c, i) => renderConditionRow(c, i)).join('')}
      </div>
      <button type="button" class="btn btn-ghost btn-full mt-8" id="add-cond-btn">+ Add Condition</button>

      <hr class="divider" />
      <div class="fw-600 mt-8 mb-8" style="font-size:13px;">Action</div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div class="form-group">
          <label class="form-label">Side</label>
          <select class="form-control" id="rf-action-type">
            <option ${r.action.type === 'BUY'  ? 'selected' : ''}>BUY</option>
            <option ${r.action.type === 'SELL' ? 'selected' : ''}>SELL</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Asset Type</label>
          <select class="form-control" id="rf-asset-type">
            <option ${r.action.asset_type === 'STK' ? 'selected' : ''}>STK</option>
            <option ${r.action.asset_type === 'OPT' ? 'selected' : ''}>OPT</option>
            <option ${r.action.asset_type === 'FUT' ? 'selected' : ''}>FUT</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Quantity</label>
          <input class="form-control" id="rf-qty" type="number" min="1" value="${r.action.quantity}" required />
        </div>
        <div class="form-group">
          <label class="form-label">Order Type</label>
          <select class="form-control" id="rf-order-type">
            <option ${r.action.order_type === 'MKT' ? 'selected' : ''}>MKT</option>
            <option ${r.action.order_type === 'LMT' ? 'selected' : ''}>LMT</option>
          </select>
        </div>
        <div class="form-group" id="limit-price-group" style="${r.action.order_type !== 'LMT' ? 'display:none' : ''}">
          <label class="form-label">Limit Price</label>
          <input class="form-control" id="rf-limit-price" type="number" step="0.01" value="${r.action.limit_price ?? ''}" />
        </div>
      </div>

      <div class="form-group">
        <label class="form-label">Cooldown (minutes)</label>
        <input class="form-control" id="rf-cooldown" type="number" min="1" value="${r.cooldown_minutes}" />
      </div>

      <button type="submit" class="btn btn-primary btn-full mt-12">
        ${existing ? 'Save Changes' : 'Create Rule'}
      </button>
    </form>
  `;
}

function bindRuleBuilder() {
  document.getElementById('back-btn')?.addEventListener('click', () => navigate('rules'));

  let condCount = document.querySelectorAll('.condition-row').length;

  document.getElementById('add-cond-btn')?.addEventListener('click', () => {
    const container = document.getElementById('conditions-container');
    const newRow = renderConditionRow(
      { indicator: 'RSI', params: { length: 14 }, operator: '>', value: 50 },
      condCount
    );
    container.insertAdjacentHTML('beforeend', newRow);
    condCount++;
    bindRemoveCond();
  });

  bindRemoveCond();

  document.getElementById('rf-order-type')?.addEventListener('change', (e) => {
    document.getElementById('limit-price-group').style.display =
      e.target.value === 'LMT' ? '' : 'none';
  });

  document.getElementById('rule-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const conditions = [];
    document.querySelectorAll('.condition-row').forEach(row => {
      const ind = row.querySelector('.cond-indicator').value;
      const param = row.querySelector('.cond-param').value;
      const op = row.querySelector('.cond-operator').value;
      const val = row.querySelector('.cond-value').value;
      const paramKey = ['RSI','SMA','EMA','ATR','STOCH'].includes(ind) ? 'length' : 'period';
      conditions.push({
        indicator: ind,
        params: param ? { [paramKey]: parseInt(param) } : {},
        operator: op,
        value: isNaN(Number(val)) ? val : Number(val),
      });
    });

    const orderType = document.getElementById('rf-order-type').value;
    const payload = {
      name:     document.getElementById('rf-name').value.trim(),
      symbol:   document.getElementById('rf-symbol').value.trim().toUpperCase(),
      enabled:  false,
      conditions,
      logic:    document.getElementById('rf-logic').value,
      action: {
        type:        document.getElementById('rf-action-type').value,
        asset_type:  document.getElementById('rf-asset-type').value,
        quantity:    parseInt(document.getElementById('rf-qty').value),
        order_type:  orderType,
        limit_price: orderType === 'LMT'
          ? parseFloat(document.getElementById('rf-limit-price').value) || null
          : null,
      },
      cooldown_minutes: parseInt(document.getElementById('rf-cooldown').value),
    };

    try {
      if (State.params.rule) {
        const updated = await apiFetch(`/rules/${State.params.rule.id}`, {
          method: 'PUT', body: JSON.stringify(payload),
        });
        const idx = State.rules.findIndex(r => r.id === State.params.rule.id);
        if (idx >= 0) State.rules[idx] = updated;
        showToast('Rule updated');
      } else {
        const created = await apiFetch('/rules', { method: 'POST', body: JSON.stringify(payload) });
        State.rules.push(created);
        showToast('Rule created');
      }
      navigate('rules');
    } catch (err) {
      showToast('Error: ' + err.message, 5000);
    }
  });
}

function bindRemoveCond() {
  document.querySelectorAll('.remove-cond-btn').forEach(btn => {
    btn.replaceWith(btn.cloneNode(true));  // remove old listener
  });
  document.querySelectorAll('.remove-cond-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const rows = document.querySelectorAll('.condition-row');
      if (rows.length <= 1) { showToast('At least one condition required'); return; }
      btn.closest('.condition-row').remove();
    });
  });
}

/* ══════════════════════════════════════════════════════════════
   MARKET VIEW
══════════════════════════════════════════════════════════════ */

const CHART_INDICATORS = {
  // ── Moving Averages (overlay on main chart) ──
  SMA9:   { label:'SMA 9',   color:'#ffd700', type:'sma',  period:9,   pane:'main', cat:'Moving Averages' },
  SMA20:  { label:'SMA 20',  color:'#e6b800', type:'sma',  period:20,  pane:'main', cat:'Moving Averages' },
  SMA50:  { label:'SMA 50',  color:'#00d4aa', type:'sma',  period:50,  pane:'main', cat:'Moving Averages' },
  SMA200: { label:'SMA 200', color:'#ff6b81', type:'sma',  period:200, pane:'main', cat:'Moving Averages' },
  EMA9:   { label:'EMA 9',   color:'#74b9ff', type:'ema',  period:9,   pane:'main', cat:'Moving Averages' },
  EMA20:  { label:'EMA 20',  color:'#a29bfe', type:'ema',  period:20,  pane:'main', cat:'Moving Averages' },
  EMA50:  { label:'EMA 50',  color:'#fd79a8', type:'ema',  period:50,  pane:'main', cat:'Moving Averages' },
  EMA200: { label:'EMA 200', color:'#e17055', type:'ema',  period:200, pane:'main', cat:'Moving Averages' },
  // ── Overlays ──
  BB:     { label:'BB 20',   color:'#6c5ce7', type:'bb',   period:20,  pane:'main', cat:'Overlays' },
  VWAP:   { label:'VWAP',    color:'#fdcb6e', type:'vwap', period:0,   pane:'main', cat:'Overlays' },
  // ── Oscillators (sub-panes) ──
  RSI14:  { label:'RSI 14',  color:'#fd79a8', type:'rsi',  period:14,  pane:'rsi',  cat:'Oscillators' },
  MACD:   { label:'MACD',    color:'#00cec9', type:'macd', period:0,   pane:'macd', cat:'Oscillators' },
  STOCH:  { label:'Stoch',   color:'#0984e3', type:'stoch',period:14,  pane:'stoch',cat:'Oscillators' },
};

function renderMarket() {
  const watchlistChips = State.positions.length
    ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;align-items:center;">
        <span style="font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;">Positions</span>
        ${State.positions.map(p => `
          <button class="btn btn-ghost btn-sm watchlist-chip" data-sym="${p.symbol}"
            style="padding:3px 10px;${p.symbol === State.marketSymbol ? 'background:var(--accent);color:#000;border-color:var(--accent);' : ''}">
            ${p.symbol}
          </button>`).join('')}
      </div>`
    : '';

  return `
    ${watchlistChips}
    <div style="display:flex;gap:8px;margin-bottom:8px;">
      <input class="form-control" id="market-symbol-input" type="text"
             value="${State.marketSymbol}" placeholder="AAPL" style="text-transform:uppercase;flex:1;" />
      <button class="btn btn-primary" id="market-search-btn">Go</button>
    </div>
    ${State.marketBars.length ? `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px;">
      <button class="btn btn-full" style="background:rgba(0,212,170,.15);color:var(--accent);border:1.5px solid var(--accent);"
        id="market-buy-btn">BUY ${State.marketSymbol}</button>
      <button class="btn btn-full" style="background:rgba(255,71,87,.12);color:var(--danger);border:1.5px solid var(--danger);"
        id="market-sell-btn">SELL ${State.marketSymbol}</button>
    </div>` : ''}

    <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;">
      <button class="btn btn-ghost btn-sm" id="ind-picker-btn" style="gap:5px;">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z"/></svg>
        Indicators${State.activeIndicators.size ? ` (${State.activeIndicators.size})` : ''}
      </button>
      ${[...State.activeIndicators].map(k => {
        const c = CHART_INDICATORS[k];
        return `<span style="font-size:11px;padding:2px 7px;border-radius:10px;border:1px solid ${c.color};color:${c.color};">${c.label}</span>`;
      }).join('')}
    </div>

    <div id="tv-chart" style="width:100%;height:500px;border-radius:8px 8px 0 0;overflow:hidden;"></div>
    <div id="rsi-pane"  style="width:100%;height:110px;overflow:hidden;margin-top:2px;display:${State.activeIndicators.has('RSI14') ? 'block' : 'none'};background:#0d1117;"></div>
    <div id="macd-pane" style="width:100%;height:110px;overflow:hidden;margin-top:2px;display:${State.activeIndicators.has('MACD')  ? 'block' : 'none'};background:#0d1117;"></div>
    <div id="stoch-pane" style="width:100%;height:110px;overflow:hidden;margin-top:2px;display:${State.activeIndicators.has('STOCH') ? 'block' : 'none'};background:#0d1117;border-radius:0 0 8px 8px;"></div>
    <div class="chart-meta" id="chart-meta">
      ${State.marketBars.length
        ? `${State.marketBars.length} bars · last close: ${fmtMoney(State.marketBars.at(-1)?.close)}`
        : 'Enter a symbol and press Go.'}
    </div>

    ${State.marketBars.length ? `
    <div class="card mt-12">
      <div class="card-title">Price Table (last 10 bars)</div>
      <div class="tbl-wrap">
        <table>
          <thead><tr><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Vol</th></tr></thead>
          <tbody>
            ${State.marketBars.slice(-10).reverse().map(b => `<tr>
              <td class="td-muted">${new Date(b.time * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</td>
              <td class="td-mono">${fmtNum(b.open)}</td>
              <td class="td-mono text-accent">${fmtNum(b.high)}</td>
              <td class="td-mono text-danger">${fmtNum(b.low)}</td>
              <td class="td-mono fw-600">${fmtNum(b.close)}</td>
              <td class="td-muted">${Number(b.volume).toLocaleString()}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>` : ''}
  `;
}

function bindMarket() {
  document.getElementById('market-search-btn')?.addEventListener('click', loadMarketData);
  document.getElementById('market-symbol-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loadMarketData();
  });

  document.querySelectorAll('.watchlist-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const sym = btn.dataset.sym;
      document.getElementById('market-symbol-input').value = sym;
      State.marketSymbol = sym;
      loadMarketData();
    });
  });

  document.getElementById('ind-picker-btn')?.addEventListener('click', showIndicatorPicker);

  document.getElementById('market-buy-btn')?.addEventListener('click', () =>
    showOrderModal(State.marketSymbol, 'BUY'));
  document.getElementById('market-sell-btn')?.addEventListener('click', () =>
    showOrderModal(State.marketSymbol, 'SELL'));

  if (State.marketBars.length) {
    initLWChart(State.marketBars);
    apiFetch(`/market/${State.marketSymbol}/subscribe`, { method: 'POST' }).catch(() => {});
  }
}

async function loadMarketData() {
  const sym = document.getElementById('market-symbol-input')?.value.trim().toUpperCase();
  if (!sym) return;

  if (State.marketSymbol && State.marketSymbol !== sym) {
    apiFetch(`/market/${State.marketSymbol}/unsubscribe`, { method: 'POST' }).catch(() => {});
  }

  State.marketSymbol = sym;
  try {
    const bars = await apiFetch(`/market/${sym}/bars?bar_size=1D&duration=60+D`);
    State.marketBars = bars;
    render();
  } catch (err) {
    showToast(err.message.includes('connected') ? 'IBKR not connected' : 'Error: ' + err.message);
  }
}

function destroyChart() {
  [State.chartInstance, State.rsiChart, State.macdChart, State.stochChart].forEach(c => {
    try { if (c) c.remove(); } catch (_) {}
  });
  State.chartInstance = State.chartSeries = null;
  State.rsiChart = State.rsiSeries = null;
  State.macdChart = null;
  State.stochChart = null;
  State.indicatorSeries = {};
}

/* ── Indicator math ──────────────────────────────────────── */

function calcSMA(bars, period) {
  const result = [];
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].close;
    result.push({ time: bars[i].time, value: sum / period });
  }
  return result;
}

function calcEMA(bars, period) {
  if (bars.length < period) return [];
  const k = 2 / (period + 1);
  let ema = bars.slice(0, period).reduce((s, b) => s + b.close, 0) / period;
  const result = [{ time: bars[period - 1].time, value: ema }];
  for (let i = period; i < bars.length; i++) {
    ema = bars[i].close * k + ema * (1 - k);
    result.push({ time: bars[i].time, value: ema });
  }
  return result;
}

function calcRSI(bars, period = 14) {
  if (bars.length < period + 1) return [];
  const closes = bars.map(b => b.close);
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    avgGain += d > 0 ? d : 0;
    avgLoss += d < 0 ? -d : 0;
  }
  avgGain /= period;
  avgLoss /= period;
  const result = [];
  for (let i = period; i < closes.length; i++) {
    if (i > period) {
      const d = closes[i] - closes[i - 1];
      avgGain = (avgGain * (period - 1) + (d > 0 ? d : 0)) / period;
      avgLoss = (avgLoss * (period - 1) + (d < 0 ? -d : 0)) / period;
    }
    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    result.push({ time: bars[i].time, value: parseFloat((100 - 100 / (1 + rs)).toFixed(2)) });
  }
  return result;
}

function calcBollingerBands(bars, period = 20, mult = 2) {
  const upper = [], middle = [], lower = [];
  for (let i = period - 1; i < bars.length; i++) {
    const slice = bars.slice(i - period + 1, i + 1);
    const mean = slice.reduce((s, b) => s + b.close, 0) / period;
    const std = Math.sqrt(slice.reduce((s, b) => s + Math.pow(b.close - mean, 2), 0) / period);
    upper.push({ time: bars[i].time, value: parseFloat((mean + mult * std).toFixed(4)) });
    middle.push({ time: bars[i].time, value: parseFloat(mean.toFixed(4)) });
    lower.push({ time: bars[i].time, value: parseFloat((mean - mult * std).toFixed(4)) });
  }
  return { upper, middle, lower };
}

function calcMACD(bars, fast = 12, slow = 26, signalPeriod = 9) {
  const emaFast = calcEMA(bars, fast);
  const emaSlow = calcEMA(bars, slow);
  const slowMap = new Map(emaSlow.map(d => [d.time, d.value]));
  const macdLine = emaFast
    .filter(d => slowMap.has(d.time))
    .map(d => ({ time: d.time, value: parseFloat((d.value - slowMap.get(d.time)).toFixed(4)) }));
  if (macdLine.length < signalPeriod) return { macd: macdLine, signal: [], histogram: [] };
  const k = 2 / (signalPeriod + 1);
  let sigEma = macdLine.slice(0, signalPeriod).reduce((s, d) => s + d.value, 0) / signalPeriod;
  const signalLine = [{ time: macdLine[signalPeriod - 1].time, value: parseFloat(sigEma.toFixed(4)) }];
  for (let i = signalPeriod; i < macdLine.length; i++) {
    sigEma = macdLine[i].value * k + sigEma * (1 - k);
    signalLine.push({ time: macdLine[i].time, value: parseFloat(sigEma.toFixed(4)) });
  }
  const sigMap = new Map(signalLine.map(d => [d.time, d.value]));
  const histogram = macdLine
    .filter(d => sigMap.has(d.time))
    .map(d => ({ time: d.time, value: parseFloat((d.value - sigMap.get(d.time)).toFixed(4)) }));
  return { macd: macdLine, signal: signalLine, histogram };
}

function calcStoch(bars, period = 14, smoothK = 3, smoothD = 3) {
  const rawK = [];
  for (let i = period - 1; i < bars.length; i++) {
    const slice = bars.slice(i - period + 1, i + 1);
    const lowest = Math.min(...slice.map(b => b.low));
    const highest = Math.max(...slice.map(b => b.high));
    const range = highest - lowest;
    rawK.push({ time: bars[i].time, value: parseFloat((range > 0 ? ((bars[i].close - lowest) / range) * 100 : 50).toFixed(2)) });
  }
  const kLine = [];
  for (let i = smoothK - 1; i < rawK.length; i++) {
    const avg = rawK.slice(i - smoothK + 1, i + 1).reduce((s, d) => s + d.value, 0) / smoothK;
    kLine.push({ time: rawK[i].time, value: parseFloat(avg.toFixed(2)) });
  }
  const dLine = [];
  for (let i = smoothD - 1; i < kLine.length; i++) {
    const avg = kLine.slice(i - smoothD + 1, i + 1).reduce((s, d) => s + d.value, 0) / smoothD;
    dLine.push({ time: kLine[i].time, value: parseFloat(avg.toFixed(2)) });
  }
  return { k: kLine, d: dLine };
}

function calcVWAP(bars) {
  let cumPV = 0, cumVol = 0;
  return bars.map(b => {
    const typical = (b.high + b.low + b.close) / 3;
    cumPV += typical * (b.volume || 1);
    cumVol += b.volume || 1;
    return { time: b.time, value: parseFloat((cumPV / cumVol).toFixed(4)) };
  }).filter(d => isFinite(d.value));
}

/* ── Indicator toggle ────────────────────────────────────── */

function toggleIndicator(key) {
  const cfg = CHART_INDICATORS[key];
  if (!cfg) return;

  if (State.activeIndicators.has(key)) {
    // ── Remove ──
    State.activeIndicators.delete(key);
    if (key === 'RSI14') {
      if (State.rsiChart) { State.rsiChart.remove(); State.rsiChart = null; State.rsiSeries = null; }
      const pane = document.getElementById('rsi-pane');
      if (pane) pane.style.display = 'none';
    } else if (key === 'MACD') {
      if (State.macdChart) { State.macdChart.remove(); State.macdChart = null; }
      const pane = document.getElementById('macd-pane');
      if (pane) pane.style.display = 'none';
    } else if (key === 'STOCH') {
      if (State.stochChart) { State.stochChart.remove(); State.stochChart = null; }
      const pane = document.getElementById('stoch-pane');
      if (pane) pane.style.display = 'none';
    } else if (key === 'BB') {
      ['BB_upper', 'BB_middle', 'BB_lower'].forEach(k => {
        if (State.indicatorSeries[k]) { State.chartInstance?.removeSeries(State.indicatorSeries[k]); delete State.indicatorSeries[k]; }
      });
    } else {
      if (State.indicatorSeries[key]) { State.chartInstance?.removeSeries(State.indicatorSeries[key]); delete State.indicatorSeries[key]; }
    }
  } else {
    // ── Add ──
    State.activeIndicators.add(key);
    if (key === 'RSI14') {
      const pane = document.getElementById('rsi-pane');
      if (pane) { pane.style.display = 'block'; initRSIChart(State.marketBars); }
    } else if (key === 'MACD') {
      const pane = document.getElementById('macd-pane');
      if (pane) { pane.style.display = 'block'; initMACDChart(State.marketBars); }
    } else if (key === 'STOCH') {
      const pane = document.getElementById('stoch-pane');
      if (pane) { pane.style.display = 'block'; initStochChart(State.marketBars); }
    } else if (key === 'BB' && State.chartInstance && State.marketBars.length) {
      const { upper, middle, lower } = calcBollingerBands(State.marketBars, cfg.period);
      const opts = { color: cfg.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false };
      const upperS  = State.chartInstance.addLineSeries({ ...opts, lineStyle: 2, title: 'BB Upper' });
      const middleS = State.chartInstance.addLineSeries({ ...opts, title: 'BB Mid' });
      const lowerS  = State.chartInstance.addLineSeries({ ...opts, lineStyle: 2, title: 'BB Lower' });
      upperS.setData(upper); middleS.setData(middle); lowerS.setData(lower);
      State.indicatorSeries['BB_upper'] = upperS;
      State.indicatorSeries['BB_middle'] = middleS;
      State.indicatorSeries['BB_lower'] = lowerS;
    } else if (key === 'VWAP' && State.chartInstance && State.marketBars.length) {
      const vwapS = State.chartInstance.addLineSeries({ color: cfg.color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, title: 'VWAP' });
      vwapS.setData(calcVWAP(State.marketBars));
      State.indicatorSeries[key] = vwapS;
    } else if (State.chartInstance && State.marketBars.length) {
      const lineSeries = State.chartInstance.addLineSeries({ color: cfg.color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, title: cfg.label });
      lineSeries.setData(cfg.type === 'ema' ? calcEMA(State.marketBars, cfg.period) : calcSMA(State.marketBars, cfg.period));
      State.indicatorSeries[key] = lineSeries;
    }
  }

  updateIndicatorChips();
}

function updateIndicatorChips() {
  const btn = document.getElementById('ind-picker-btn');
  if (!btn) return;
  const row = btn.parentElement;
  // Remove all chip spans (siblings after the button)
  [...row.children].forEach(c => { if (c !== btn) c.remove(); });
  // Update button count text
  btn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z"/></svg>
    Indicators${State.activeIndicators.size ? ` (${State.activeIndicators.size})` : ''}`;
  // Add chips for active indicators
  for (const k of State.activeIndicators) {
    const c = CHART_INDICATORS[k];
    const chip = document.createElement('span');
    chip.style.cssText = `font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid ${c.color};color:${c.color};cursor:pointer;white-space:nowrap;`;
    chip.textContent = c.label;
    chip.title = 'Click to remove';
    chip.addEventListener('click', () => toggleIndicator(k));
    row.appendChild(chip);
  }
}

function showIndicatorPicker() {
  if (!State.chartInstance) { showToast('Load a chart first'); return; }

  const categories = {};
  for (const [key, c] of Object.entries(CHART_INDICATORS)) {
    if (!categories[c.cat]) categories[c.cat] = [];
    categories[c.cat].push({ key, ...c });
  }

  const catHTML = Object.entries(categories).map(([cat, inds]) => `
    <div style="margin-bottom:18px;">
      <div style="font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px;">${cat}</div>
      <div style="display:flex;flex-wrap:wrap;gap:7px;">
        ${inds.map(ind => {
          const active = State.activeIndicators.has(ind.key);
          return `<button class="ind-pick-btn" data-ind="${ind.key}"
            style="padding:6px 14px;border-radius:20px;font-size:13px;cursor:pointer;transition:.15s;
                   border:1px solid ${active ? ind.color : 'var(--border)'};
                   background:${active ? ind.color + '22' : 'transparent'};
                   color:${active ? ind.color : 'var(--text2)'};">${ind.label}</button>`;
        }).join('')}
      </div>
    </div>`).join('');

  showModal(`
    <h3 style="margin-bottom:16px;font-size:17px;">Indicators</h3>
    ${catHTML}
    <button class="btn btn-ghost btn-full mt-8" onclick="hideModal()">Done</button>
  `);

  document.querySelectorAll('.ind-pick-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.ind;
      toggleIndicator(key);
      const active = State.activeIndicators.has(key);
      const c = CHART_INDICATORS[key];
      btn.style.borderColor = active ? c.color : 'var(--border)';
      btn.style.background = active ? c.color + '22' : 'transparent';
      btn.style.color = active ? c.color : 'var(--text2)';
    });
  });
}

/* ── RSI sub-pane ────────────────────────────────────────── */

function initRSIChart(bars) {
  const container = document.getElementById('rsi-pane');
  if (!container || !bars.length) return;
  if (State.rsiChart) { State.rsiChart.remove(); State.rsiChart = null; State.rsiSeries = null; }

  const rsiChart = LightweightCharts.createChart(container, {
    width: container.clientWidth || window.innerWidth - 32,
    height: 120,
    layout: { background: { type: 'solid', color: '#0d1117' }, textColor: '#c9d1d9' },
    grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  const rsiSeries = rsiChart.addLineSeries({
    color: CHART_INDICATORS.RSI14.color, lineWidth: 1.5,
    priceLineVisible: false, lastValueVisible: true, title: 'RSI 14',
  });
  rsiSeries.createPriceLine({ price: 70, color: '#ff4757', lineWidth: 1, lineStyle: 2, title: '70' });
  rsiSeries.createPriceLine({ price: 30, color: '#00d4aa', lineWidth: 1, lineStyle: 2, title: '30' });
  rsiSeries.setData(calcRSI(bars, 14));
  rsiChart.timeScale().fitContent();

  State.rsiChart = rsiChart;
  State.rsiSeries = rsiSeries;

  // Sync scroll/zoom with main chart
  if (State.chartInstance) {
    let _syncing = false;
    State.chartInstance.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (_syncing || !range || !State.rsiChart) return;
      _syncing = true;
      State.rsiChart.timeScale().setVisibleLogicalRange(range);
      _syncing = false;
    });
    rsiChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (_syncing || !range || !State.chartInstance) return;
      _syncing = true;
      State.chartInstance.timeScale().setVisibleLogicalRange(range);
      _syncing = false;
    });
  }

  new ResizeObserver(() => {
    if (State.rsiChart) State.rsiChart.applyOptions({ width: container.clientWidth });
  }).observe(container);
}

/* ── MACD sub-pane ───────────────────────────────────────── */

function initMACDChart(bars) {
  const container = document.getElementById('macd-pane');
  if (!container || !bars.length) return;
  if (State.macdChart) { State.macdChart.remove(); State.macdChart = null; }

  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth || window.innerWidth - 32,
    height: 110,
    layout: { background: { type: 'solid', color: '#0d1117' }, textColor: '#c9d1d9' },
    grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  const { macd, signal, histogram } = calcMACD(bars);

  const histSeries = chart.addHistogramSeries({
    priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
    priceLineVisible: false,
  });
  histSeries.setData(histogram.map(d => ({
    time: d.time, value: d.value,
    color: d.value >= 0 ? 'rgba(0,212,170,0.55)' : 'rgba(255,71,87,0.55)',
  })));

  const macdS = chart.addLineSeries({ color: '#00cec9', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, title: 'MACD' });
  macdS.setData(macd);

  const signalS = chart.addLineSeries({ color: '#fdcb6e', lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: 'Signal' });
  signalS.setData(signal);

  chart.timeScale().fitContent();
  State.macdChart = chart;

  if (State.chartInstance) {
    let _syncing = false;
    State.chartInstance.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (_syncing || !range || !State.macdChart) return;
      _syncing = true; State.macdChart.timeScale().setVisibleLogicalRange(range); _syncing = false;
    });
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (_syncing || !range || !State.chartInstance) return;
      _syncing = true; State.chartInstance.timeScale().setVisibleLogicalRange(range); _syncing = false;
    });
  }

  new ResizeObserver(() => {
    if (State.macdChart) State.macdChart.applyOptions({ width: container.clientWidth });
  }).observe(container);
}

/* ── Stochastic sub-pane ─────────────────────────────────── */

function initStochChart(bars) {
  const container = document.getElementById('stoch-pane');
  if (!container || !bars.length) return;
  if (State.stochChart) { State.stochChart.remove(); State.stochChart = null; }

  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth || window.innerWidth - 32,
    height: 110,
    layout: { background: { type: 'solid', color: '#0d1117' }, textColor: '#c9d1d9' },
    grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  const { k, d } = calcStoch(bars);

  const kSeries = chart.addLineSeries({ color: '#0984e3', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, title: '%K' });
  kSeries.setData(k);
  kSeries.createPriceLine({ price: 80, color: '#ff4757', lineWidth: 1, lineStyle: 2, title: '80' });
  kSeries.createPriceLine({ price: 20, color: '#00d4aa', lineWidth: 1, lineStyle: 2, title: '20' });

  const dSeries = chart.addLineSeries({ color: '#fdcb6e', lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: '%D' });
  dSeries.setData(d);

  chart.timeScale().fitContent();
  State.stochChart = chart;

  if (State.chartInstance) {
    let _syncing = false;
    State.chartInstance.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (_syncing || !range || !State.stochChart) return;
      _syncing = true; State.stochChart.timeScale().setVisibleLogicalRange(range); _syncing = false;
    });
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (_syncing || !range || !State.chartInstance) return;
      _syncing = true; State.chartInstance.timeScale().setVisibleLogicalRange(range); _syncing = false;
    });
  }

  new ResizeObserver(() => {
    if (State.stochChart) State.stochChart.applyOptions({ width: container.clientWidth });
  }).observe(container);
}

/* ── Main LW chart ───────────────────────────────────────── */

function initLWChart(bars) {
  const container = document.getElementById('tv-chart');
  if (!container || !bars.length) return;

  destroyChart();

  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth || window.innerWidth - 32,
    height: 500,
    layout: { background: { type: 'solid', color: '#161b22' }, textColor: '#c9d1d9' },
    grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
  });

  const series = chart.addCandlestickSeries({
    upColor: '#00d4aa', downColor: '#ff4757',
    borderUpColor: '#00d4aa', borderDownColor: '#ff4757',
    wickUpColor: '#00d4aa', wickDownColor: '#ff4757',
  });

  series.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
  chart.timeScale().fitContent();

  State.chartInstance = chart;
  State.chartSeries = series;

  // Re-apply any active indicators after chart recreation
  for (const key of State.activeIndicators) {
    const indCfg = CHART_INDICATORS[key];
    if (key === 'RSI14') {
      const pane = document.getElementById('rsi-pane');
      if (pane) { pane.style.display = 'block'; initRSIChart(bars); }
    } else if (key === 'MACD') {
      const pane = document.getElementById('macd-pane');
      if (pane) { pane.style.display = 'block'; initMACDChart(bars); }
    } else if (key === 'STOCH') {
      const pane = document.getElementById('stoch-pane');
      if (pane) { pane.style.display = 'block'; initStochChart(bars); }
    } else if (key === 'BB') {
      const { upper, middle, lower } = calcBollingerBands(bars, indCfg.period);
      const opts = { color: indCfg.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false };
      const upperS  = chart.addLineSeries({ ...opts, lineStyle: 2, title: 'BB Upper' });
      const middleS = chart.addLineSeries({ ...opts, title: 'BB Mid' });
      const lowerS  = chart.addLineSeries({ ...opts, lineStyle: 2, title: 'BB Lower' });
      upperS.setData(upper); middleS.setData(middle); lowerS.setData(lower);
      State.indicatorSeries['BB_upper'] = upperS;
      State.indicatorSeries['BB_middle'] = middleS;
      State.indicatorSeries['BB_lower'] = lowerS;
    } else if (key === 'VWAP') {
      const vwapS = chart.addLineSeries({ color: indCfg.color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, title: 'VWAP' });
      vwapS.setData(calcVWAP(bars));
      State.indicatorSeries[key] = vwapS;
    } else {
      const lineSeries = chart.addLineSeries({ color: indCfg.color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, title: indCfg.label });
      lineSeries.setData(indCfg.type === 'ema' ? calcEMA(bars, indCfg.period) : calcSMA(bars, indCfg.period));
      State.indicatorSeries[key] = lineSeries;
    }
  }

  new ResizeObserver(() => {
    if (State.chartInstance) State.chartInstance.applyOptions({ width: container.clientWidth });
  }).observe(container);
}

/* ══════════════════════════════════════════════════════════════
   POSITIONS VIEW
══════════════════════════════════════════════════════════════ */
function renderPositions() {
  if (!State.status.ibkr_connected) {
    return `
      <div class="card" style="text-align:center;padding:30px;">
        <p class="text-muted mb-8">Connect to IB Gateway to see positions.</p>
        <button class="btn btn-primary" id="pos-connect-btn">Connect to IB Gateway</button>
      </div>`;
  }

  if (!State.positions.length) {
    return `<div class="empty"><div class="empty-icon">📂</div><div class="empty-text">No open positions.</div></div>`;
  }

  // Summary row
  const totalValue   = State.positions.reduce((s, p) => s + p.market_value, 0);
  const totalUnreal  = State.positions.reduce((s, p) => s + p.unrealized_pnl, 0);
  const totalReal    = State.positions.reduce((s, p) => s + p.realized_pnl, 0);
  const totalCost    = State.positions.reduce((s, p) => s + (p.avg_cost * p.qty), 0);
  const totalPnlPct  = totalCost ? (totalUnreal / totalCost * 100) : 0;

  const summaryBar = `
    <div class="stats-grid" style="margin-bottom:14px;">
      <div class="stat-card">
        <div class="stat-label">MARKET VALUE</div>
        <div class="stat-value" style="font-size:17px;">${fmtMoney(totalValue)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">UNREALIZED P&amp;L</div>
        <div class="stat-value ${pnlClass(totalUnreal)}" style="font-size:17px;">${fmtMoney(totalUnreal)}</div>
        <div style="font-size:11px;color:var(--text2);">${totalPnlPct >= 0 ? '+' : ''}${fmtNum(totalPnlPct)}%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">REALIZED P&amp;L</div>
        <div class="stat-value ${pnlClass(totalReal)}" style="font-size:17px;">${fmtMoney(totalReal)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">POSITIONS</div>
        <div class="stat-value" style="font-size:17px;">${State.positions.length}</div>
      </div>
    </div>`;

  // Sort: largest absolute P&L first
  const sorted = [...State.positions].sort((a, b) => Math.abs(b.unrealized_pnl) - Math.abs(a.unrealized_pnl));

  const cards = sorted.map(p => {
    const costBasis = p.avg_cost * p.qty;
    const pnlPct    = costBasis ? (p.unrealized_pnl / costBasis * 100) : 0;
    const sign      = p.unrealized_pnl >= 0;
    return `
      <div class="pos-card" data-sym="${p.symbol}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
          <div>
            <div class="pos-card-sym">${p.symbol}</div>
            <div class="pos-card-type">${p.asset_type} · ${p.qty >= 0 ? 'LONG' : 'SHORT'}</div>
          </div>
          <div style="text-align:right;">
            <div class="pos-card-price">${fmtMoney(p.market_price)}</div>
            <div style="font-size:11px;color:var(--text2);">avg ${fmtMoney(p.avg_cost)}</div>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin:10px 0 0;">
          <div>
            <div style="font-size:10px;color:var(--text2);margin-bottom:2px;">QTY</div>
            <div style="font-weight:700;">${Math.abs(p.qty).toLocaleString()}</div>
          </div>
          <div>
            <div style="font-size:10px;color:var(--text2);margin-bottom:2px;">MKT VALUE</div>
            <div style="font-weight:700;">${fmtMoney(p.market_value)}</div>
          </div>
          <div>
            <div style="font-size:10px;color:var(--text2);margin-bottom:2px;">COST BASIS</div>
            <div style="font-weight:700;">${fmtMoney(costBasis)}</div>
          </div>
        </div>

        <div style="margin-top:10px;padding:8px 10px;border-radius:6px;background:${sign ? 'rgba(0,212,170,.08)' : 'rgba(255,71,87,.08)'};border:1px solid ${sign ? 'rgba(0,212,170,.2)' : 'rgba(255,71,87,.2)'};">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:11px;color:var(--text2);">Unrealized P&amp;L</span>
            <span style="font-size:11px;color:var(--text2);">${pnlPct >= 0 ? '+' : ''}${fmtNum(pnlPct)}%</span>
          </div>
          <div class="pos-card-pnl ${pnlClass(p.unrealized_pnl)}" style="font-size:20px;font-weight:800;margin-top:2px;">
            ${p.unrealized_pnl >= 0 ? '+' : ''}${fmtMoney(p.unrealized_pnl)}
          </div>
        </div>

        <div class="pos-card-actions">
          <button class="btn btn-sm" style="background:rgba(0,212,170,.12);color:var(--accent);border:1px solid var(--accent);justify-content:center;"
            data-sym="${p.symbol}" data-act="BUY">+ Add</button>
          <button class="btn btn-sm" style="background:rgba(255,71,87,.1);color:var(--danger);border:1px solid var(--danger);justify-content:center;"
            data-sym="${p.symbol}" data-act="SELL">− Close</button>
        </div>
      </div>`;
  }).join('');

  return `${summaryBar}<div class="pos-summary">${cards}</div>`;
}

function bindPositions() {
  document.getElementById('pos-connect-btn')?.addEventListener('click', async () => {
    try {
      await apiFetch('/ibkr/connect', { method: 'POST' });
      await refreshAll();
      render();
    } catch (err) { showToast('Could not connect: ' + err.message, 5000); }
  });

  document.querySelectorAll('.pos-card-actions button').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      showOrderModal(btn.dataset.sym, btn.dataset.act);
    });
  });

  document.querySelectorAll('.pos-card').forEach(card => {
    card.addEventListener('click', () => {
      State.marketSymbol = card.dataset.sym;
      navigate('market');
      loadMarketData();
    });
  });
}

/* ══════════════════════════════════════════════════════════════
   WATCHLIST VIEW
══════════════════════════════════════════════════════════════ */
function renderWatchlist() {
  // Cards are populated by renderWatchlistCards() after bind — avoid re-entry loop
  const loadingHtml = State.watchlist === null
    ? `<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text2);">Loading market data…</div>`
    : '';
  return `
    <div class="wl-toolbar">
      <input class="form-control" id="wl-add-input" type="text" placeholder="Add symbol (e.g. AMZN, BTC-USD)" style="flex:1;text-transform:uppercase;" />
      <button class="btn btn-primary btn-sm" id="wl-add-btn">Add</button>
      <button class="btn btn-ghost btn-sm" id="wl-refresh-btn" title="Refresh prices">↻</button>
    </div>
    <div class="wl-grid">${loadingHtml}</div>
  `;
}

// Guard flag — prevents bindWatchlist() from re-triggering refreshWatchlist() mid-fetch
let _wlFetching = false;

function bindWatchlist() {
  document.getElementById('wl-add-btn')?.addEventListener('click', addWatchlistSymbol);
  document.getElementById('wl-add-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') addWatchlistSymbol();
  });
  document.getElementById('wl-refresh-btn')?.addEventListener('click', () => {
    loadWatchlistData();
  });

  document.querySelectorAll('.wl-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sym = btn.dataset.sym;
      State.watchlistSymbols = State.watchlistSymbols.filter(s => s !== sym);
      if (Array.isArray(State.watchlist))
        State.watchlist = State.watchlist.filter(q => q.symbol !== sym);
      renderWatchlistCards();   // partial DOM update — no re-bind
    });
  });

  document.querySelectorAll('.wl-card').forEach(card => {
    card.addEventListener('click', () => showWatchlistModal(card.dataset.sym));
  });

  // Only fetch if never loaded and not already fetching
  if (State.watchlist === null && !_wlFetching) loadWatchlistData();
}

// Update just the card grid without re-running bindWatchlist (avoids infinite loop)
function renderWatchlistCards() {
  const grid = document.querySelector('.wl-grid');
  if (!grid) return;
  if (State.watchlist === null) {
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text2);">Loading market data…</div>`;
  } else if (!State.watchlist.length) {
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text2);">Add symbols above to get started.</div>`;
  } else {
    grid.innerHTML = State.watchlist.map(q => {
      const lo  = q.year_low  || 0;
      const hi  = q.year_high || 0;
      const pct = (hi - lo) > 0 ? Math.round(((q.price - lo) / (hi - lo)) * 100) : 50;
      const capStr = q.market_cap
        ? q.market_cap >= 1e12 ? `${(q.market_cap/1e12).toFixed(2)}T`
        : q.market_cap >= 1e9  ? `${(q.market_cap/1e9).toFixed(1)}B`
        : `${(q.market_cap/1e6).toFixed(0)}M`
        : '—';
      return `
        <div class="wl-card" data-sym="${q.symbol}">
          <button class="wl-remove" data-sym="${q.symbol}" title="Remove">×</button>
          <div class="wl-sym">${q.symbol}</div>
          <div class="wl-price">${q.price >= 1 ? fmtMoney(q.price) : q.price.toFixed(4)}</div>
          <div class="wl-chg ${q.change >= 0 ? 'pos' : 'neg'}">
            ${q.change >= 0 ? '+' : ''}${fmtNum(q.change_pct)}%
            <span style="opacity:.7;">(${q.change >= 0 ? '+' : ''}${fmtNum(q.change, q.price > 10 ? 2 : 4)})</span>
          </div>
          <div class="wl-range">
            <div class="wl-range-labels">
              <span>52W ${lo ? fmtNum(lo, 0) : '—'}</span>
              <span>Cap ${capStr}</span>
              <span>${hi ? fmtNum(hi, 0) : '—'}</span>
            </div>
            <div class="wl-range-track"><div class="wl-range-fill" style="width:${pct}%;"></div></div>
          </div>
        </div>`;
    }).join('');

    // Re-bind card events without re-running full bindWatchlist
    grid.querySelectorAll('.wl-remove').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const sym = btn.dataset.sym;
        State.watchlistSymbols = State.watchlistSymbols.filter(s => s !== sym);
        if (Array.isArray(State.watchlist))
          State.watchlist = State.watchlist.filter(q => q.symbol !== sym);
        renderWatchlistCards();
      });
    });
    grid.querySelectorAll('.wl-card').forEach(card => {
      card.addEventListener('click', () => showWatchlistModal(card.dataset.sym));
    });
  }
}

async function loadWatchlistData() {
  if (_wlFetching) return;
  _wlFetching = true;
  State.watchlist = null;
  renderWatchlistCards();   // show "Loading…" immediately, no full re-render
  try {
    const syms = State.watchlistSymbols.join(',');
    State.watchlist = await apiFetch(`/watchlist?symbols=${encodeURIComponent(syms)}`);
  } catch (err) {
    State.watchlist = [];
    showToast('Watchlist error: ' + err.message);
  }
  _wlFetching = false;
  renderWatchlistCards();   // show cards
}

async function addWatchlistSymbol() {
  const input = document.getElementById('wl-add-input');
  const sym = input?.value.trim().toUpperCase();
  if (!sym || State.watchlistSymbols.includes(sym)) {
    if (sym) showToast(`${sym} already in watchlist`);
    return;
  }
  State.watchlistSymbols.push(sym);
  input.value = '';
  showToast(`Adding ${sym}…`);
  await loadWatchlistData();
}

// Keep refreshWatchlist as an alias so other call sites still work
async function refreshWatchlist() { await loadWatchlistData(); }

function showWatchlistModal(sym) {
  const q = State.watchlist.find(w => w.symbol === sym);
  if (!q) return;

  const capStr = q.market_cap
    ? q.market_cap >= 1e12 ? `$${(q.market_cap/1e12).toFixed(2)}T`
    : q.market_cap >= 1e9  ? `$${(q.market_cap/1e9).toFixed(1)}B`
    : `$${(q.market_cap/1e6).toFixed(0)}M`
    : '—';

  showModal(`
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
      <h2 style="font-size:20px;">${sym}</h2>
      <span class="wl-chg ${q.change >= 0 ? 'pos' : 'neg'}" style="font-size:14px;">
        ${q.change >= 0 ? '+' : ''}${fmtNum(q.change_pct)}%
      </span>
    </div>
    <div style="font-size:28px;font-weight:700;font-family:monospace;margin-bottom:12px;">
      ${q.price >= 1 ? fmtMoney(q.price) : q.price.toFixed(4)}
    </div>
    <div class="stats-grid" style="margin-bottom:12px;">
      <div class="stat-card"><div class="stat-label">52W HIGH</div><div class="stat-value" style="font-size:16px;">${fmtNum(q.year_high)}</div></div>
      <div class="stat-card"><div class="stat-label">52W LOW</div><div class="stat-value" style="font-size:16px;">${fmtNum(q.year_low)}</div></div>
      <div class="stat-card"><div class="stat-label">MARKET CAP</div><div class="stat-value" style="font-size:16px;">${capStr}</div></div>
      <div class="stat-card"><div class="stat-label">3M AVG VOL</div><div class="stat-value" style="font-size:16px;">${q.avg_volume ? (q.avg_volume/1e6).toFixed(1)+'M' : '—'}</div></div>
    </div>
    <div id="wl-modal-chart" style="width:100%;height:200px;border-radius:8px;overflow:hidden;"></div>
    <div style="text-align:center;padding:10px;font-size:12px;color:var(--text2);" id="wl-chart-status">Loading chart…</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px;">
      <button class="btn btn-full" style="background:rgba(0,212,170,.15);color:var(--accent);border:1.5px solid var(--accent);"
        onclick="hideModal();showOrderModal('${sym}','BUY')">BUY</button>
      <button class="btn btn-full" style="background:rgba(255,71,87,.12);color:var(--danger);border:1.5px solid var(--danger);"
        onclick="hideModal();showOrderModal('${sym}','SELL')">SELL</button>
    </div>
    <button class="btn btn-ghost btn-full mt-8" onclick="navigate('market');State.marketSymbol='${sym}';loadMarketData();hideModal();">
      Open in Market Chart
    </button>
  `);

  // Fetch daily bars and render mini LW chart
  apiFetch(`/yahoo/${encodeURIComponent(sym)}/bars?period=1mo&interval=1d`)
    .then(bars => {
      const el = document.getElementById('wl-modal-chart');
      const status = document.getElementById('wl-chart-status');
      if (!el || !bars.length) { if (status) status.textContent = 'No chart data'; return; }
      if (status) status.style.display = 'none';

      const mc = LightweightCharts.createChart(el, {
        width: el.clientWidth || 400, height: 200,
        layout: { background: { type: 'solid', color: '#161b22' }, textColor: '#c9d1d9' },
        grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
        rightPriceScale: { borderColor: '#30363d' },
        timeScale: { borderColor: '#30363d', timeVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      });
      const s = mc.addAreaSeries({
        lineColor: q.change >= 0 ? '#00d4aa' : '#ff4757',
        topColor:  q.change >= 0 ? 'rgba(0,212,170,.3)' : 'rgba(255,71,87,.3)',
        bottomColor: 'rgba(0,0,0,0)',
        lineWidth: 2,
      });
      s.setData(bars.map(b => ({ time: b.time, value: b.close })));
      mc.timeScale().fitContent();
    })
    .catch(() => {
      const status = document.getElementById('wl-chart-status');
      if (status) status.textContent = 'Chart unavailable';
    });
}

/* ══════════════════════════════════════════════════════════════
   TRADES VIEW  (journal-style list)
══════════════════════════════════════════════════════════════ */
function renderTrades() {
  if (!State.trades.length) {
    return `<div class="empty"><div class="empty-icon">📄</div><div class="empty-text">No trades logged yet.</div></div>`;
  }
  return `
    <div style="font-size:12px;color:var(--text2);margin-bottom:10px;">${State.trades.length} trades · tap to view detail</div>
    ${State.trades.map(t => {
      const posVal = t.fill_price ? t.quantity * t.fill_price : null;
      return `
        <div class="tj-item" data-id="${t.id}">
          <div class="tj-item-sym" style="color:${t.action === 'BUY' ? 'var(--accent)' : 'var(--danger)'}">
            ${t.symbol}
          </div>
          <div class="tj-item-meta">
            <div><span class="${t.action === 'BUY' ? 'td-buy' : 'td-sell'}">${t.action}</span>
              <span class="text-muted"> · ${t.quantity} shares · ${t.order_type}</span>
            </div>
            <div class="text-muted" style="font-size:11px;">${t.rule_name}</div>
            <div class="text-muted" style="font-size:11px;">${fmtDate(t.timestamp)}</div>
          </div>
          <div class="tj-item-right">
            <div class="fw-600 font-mono">${t.fill_price ? fmtMoney(t.fill_price) : '—'}</div>
            ${posVal ? `<div class="text-muted">${fmtMoney(posVal)}</div>` : ''}
            <span class="pill pill-${t.status.toLowerCase()}">${t.status}</span>
          </div>
        </div>`;
    }).join('')}
  `;
}

function bindTrades() {
  document.querySelectorAll('.tj-item').forEach(el => {
    el.addEventListener('click', () => {
      const trade = State.trades.find(t => t.id === el.dataset.id);
      if (trade) {
        State.selectedTrade = trade;
        navigate('trade-detail');
      }
    });
  });
}

/* ══════════════════════════════════════════════════════════════
   TRADE DETAIL VIEW  (photo-style journal card layout)
══════════════════════════════════════════════════════════════ */
function renderTradeDetail(trade) {
  if (!trade) return `<div class="empty"><div class="empty-text">No trade selected.</div></div>`;

  const posVal  = trade.fill_price ? trade.quantity * trade.fill_price : 0;
  const estComm = (trade.quantity * 0.005).toFixed(2);   // $0.005/share estimate
  const entryMethod = trade.rule_name.toLowerCase().includes('manual') ? 'Manual Entry' : 'Bot Entry';

  return `
    <!-- Mini chart (Yahoo Finance) -->
    <div id="trade-chart-wrap" style="width:100%;height:240px;border-radius:8px;overflow:hidden;margin-bottom:10px;background:var(--bg2);">
      <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text2);font-size:13px;" id="trade-chart-loading">
        Loading chart…
      </div>
    </div>

    <!-- 3-column stat grid -->
    <div class="tj-stat-grid">

      <!-- Symbol -->
      <div class="tj-stat-card">
        <div class="tj-stat-label">Symbol</div>
        <div class="tj-stat-icon">⏱</div>
        <div class="tj-stat-val lg" style="color:${trade.action === 'BUY' ? 'var(--accent)' : 'var(--danger)'}">
          $${trade.symbol}
        </div>
        <div class="tj-stat-sub">Start: ${fmtDate(trade.timestamp)}</div>
      </div>

      <!-- Position Value -->
      <div class="tj-stat-card">
        <div class="tj-stat-label">Position Value</div>
        <div class="tj-stat-icon">📊</div>
        <div class="tj-stat-val">${fmtMoney(posVal)}</div>
        <div class="tj-stat-sub">${trade.quantity} shares @ ${fmtMoney(trade.fill_price)}</div>
      </div>

      <!-- Net P&L -->
      <div class="tj-stat-card">
        <div class="tj-stat-label">Net P&amp;L</div>
        <div class="tj-stat-icon">💰</div>
        <div class="tj-stat-val" style="color:var(--text2)">—</div>
        <div class="tj-stat-sub">Open position</div>
      </div>

      <!-- Core Position -->
      <div class="tj-stat-card">
        <div class="tj-stat-label">Core Position</div>
        <div class="tj-stat-icon">🎯</div>
        <div class="tj-stat-val">${fmtMoney(posVal)}</div>
        <div class="tj-stat-sub">${trade.quantity} shares, entry ${fmtMoney(trade.fill_price)}</div>
      </div>

      <!-- Max Position -->
      <div class="tj-stat-card">
        <div class="tj-stat-label">Max Position</div>
        <div class="tj-stat-icon">📈</div>
        <div class="tj-stat-val">${fmtMoney(posVal)}</div>
        <div class="tj-stat-sub">${trade.quantity} shares, entry ${fmtMoney(trade.fill_price)}</div>
      </div>

      <!-- Entry Method -->
      <div class="tj-stat-card">
        <div class="tj-stat-label">Entry Method</div>
        <div class="tj-stat-icon">⚡</div>
        <div class="tj-stat-val" style="font-size:15px;">${entryMethod}</div>
        <div class="tj-stat-sub">${trade.order_type} order</div>
      </div>

      <!-- Commissions -->
      <div class="tj-stat-card">
        <div class="tj-stat-label">Commissions</div>
        <div class="tj-stat-icon">🏦</div>
        <div class="tj-stat-val" style="color:var(--danger)">$${estComm}</div>
        <div class="tj-stat-sub danger">est. $0.005/share · ${trade.quantity} shares</div>
      </div>

      <!-- Notes (wider) -->
      <div class="tj-stat-card span2">
        <div class="tj-stat-label">Notes / Rule</div>
        <div class="tj-stat-icon">📝</div>
        <div class="tj-stat-val" style="font-size:14px;word-break:break-word;">${trade.rule_name}</div>
        <div class="tj-stat-sub">${trade.asset_type} · <span class="pill pill-${trade.status.toLowerCase()}">${trade.status}</span></div>
      </div>
    </div>
  `;
}

function bindTradeDetail() {
  if (!State.selectedTrade) return;
  const sym = State.selectedTrade.symbol;

  // Fetch 5-day 5-min bars from Yahoo Finance for this symbol
  apiFetch(`/yahoo/${sym}/bars?period=5d&interval=5m`)
    .then(bars => {
      const wrap = document.getElementById('trade-chart-wrap');
      if (!wrap || !bars.length) return;
      wrap.innerHTML = '';  // clear loading text

      if (State.tradeDetailChart) { State.tradeDetailChart.remove(); State.tradeDetailChart = null; }

      const chart = LightweightCharts.createChart(wrap, {
        width: wrap.clientWidth || 400,
        height: 240,
        layout: { background: { type: 'solid', color: '#161b22' }, textColor: '#c9d1d9' },
        grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#30363d' },
        timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
      });

      const series = chart.addCandlestickSeries({
        upColor: '#00d4aa', downColor: '#ff4757',
        borderUpColor: '#00d4aa', borderDownColor: '#ff4757',
        wickUpColor: '#00d4aa', wickDownColor: '#ff4757',
      });
      series.setData(bars);
      chart.timeScale().fitContent();

      // Mark the trade entry time with a vertical line
      const tradeTs = Math.floor(new Date(State.selectedTrade.timestamp).getTime() / 1000);
      const color = State.selectedTrade.action === 'BUY' ? '#00d4aa' : '#ff4757';
      series.createPriceLine({ price: State.selectedTrade.fill_price || 0, color, lineWidth: 1, lineStyle: 2, title: `${State.selectedTrade.action} @ ${fmtMoney(State.selectedTrade.fill_price)}` });

      State.tradeDetailChart = chart;
      new ResizeObserver(() => { if (State.tradeDetailChart) State.tradeDetailChart.applyOptions({ width: wrap.clientWidth }); }).observe(wrap);
    })
    .catch(() => {
      const loading = document.getElementById('trade-chart-loading');
      if (loading) loading.textContent = 'Chart unavailable';
    });
}

/* ══════════════════════════════════════════════════════════════
   MANUAL ORDER MODAL
══════════════════════════════════════════════════════════════ */
function showOrderModal(sym = '', defaultAction = 'BUY') {
  const latestBar = State.marketBars.length && State.marketSymbol === sym
    ? State.marketBars.at(-1) : null;
  const latestPrice = latestBar ? latestBar.close : null;

  showModal(`
    <h3 style="margin-bottom:14px;font-size:17px;">Place Order</h3>

    <div class="form-group">
      <label class="form-label">Symbol</label>
      <input class="form-control" id="mo-sym" type="text" value="${sym}"
             placeholder="AAPL" style="text-transform:uppercase;" />
    </div>

    <div class="order-side-btns">
      <button class="order-side-btn ${defaultAction === 'BUY' ? 'buy-active' : ''}" id="mo-buy-btn">BUY</button>
      <button class="order-side-btn ${defaultAction === 'SELL' ? 'sell-active' : ''}" id="mo-sell-btn">SELL</button>
    </div>
    <input type="hidden" id="mo-action" value="${defaultAction}" />

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      <div class="form-group">
        <label class="form-label">Quantity</label>
        <input class="form-control" id="mo-qty" type="number" min="1" value="1" />
      </div>
      <div class="form-group">
        <label class="form-label">Order Type</label>
        <select class="form-control" id="mo-type">
          <option value="MKT">Market (MKT)</option>
          <option value="LMT">Limit (LMT)</option>
        </select>
      </div>
    </div>

    <div class="form-group" id="mo-limit-group" style="display:none;">
      <label class="form-label">Limit Price${latestPrice ? ` (last: ${fmtMoney(latestPrice)})` : ''}</label>
      <input class="form-control" id="mo-limit" type="number" step="0.01"
             value="${latestPrice ? latestPrice.toFixed(2) : ''}" placeholder="0.00" />
    </div>

    ${!State.status.ibkr_connected ? `
      <div style="background:rgba(255,71,87,.1);border:1px solid rgba(255,71,87,.3);border-radius:8px;padding:10px;margin-bottom:12px;font-size:12px;color:var(--danger);">
        IBKR not connected — connect in Dashboard first.
      </div>` : ''}
    ${State.status.is_paper ? `
      <div style="background:rgba(255,215,0,.08);border:1px solid rgba(255,215,0,.2);border-radius:8px;padding:10px;margin-bottom:12px;font-size:12px;color:var(--gold);">
        Paper trading mode — no real money at risk.
      </div>` : ''}

    <button class="btn btn-primary btn-full" id="mo-submit-btn"
      ${!State.status.ibkr_connected ? 'disabled style="opacity:.4;cursor:not-allowed;"' : ''}>
      Submit Order
    </button>
  `);

  // Side toggle
  document.getElementById('mo-buy-btn').addEventListener('click', () => {
    document.getElementById('mo-action').value = 'BUY';
    document.getElementById('mo-buy-btn').className = 'order-side-btn buy-active';
    document.getElementById('mo-sell-btn').className = 'order-side-btn';
  });
  document.getElementById('mo-sell-btn').addEventListener('click', () => {
    document.getElementById('mo-action').value = 'SELL';
    document.getElementById('mo-sell-btn').className = 'order-side-btn sell-active';
    document.getElementById('mo-buy-btn').className = 'order-side-btn';
  });

  // Show/hide limit price
  document.getElementById('mo-type').addEventListener('change', (e) => {
    document.getElementById('mo-limit-group').style.display =
      e.target.value === 'LMT' ? '' : 'none';
  });

  // Submit
  document.getElementById('mo-submit-btn')?.addEventListener('click', async () => {
    const symbol    = document.getElementById('mo-sym').value.trim().toUpperCase();
    const action    = document.getElementById('mo-action').value;
    const quantity  = parseInt(document.getElementById('mo-qty').value);
    const orderType = document.getElementById('mo-type').value;
    const limitPrice = orderType === 'LMT'
      ? parseFloat(document.getElementById('mo-limit').value) || null : null;

    if (!symbol || !quantity || quantity < 1) {
      showToast('Fill in symbol and quantity'); return;
    }
    if (orderType === 'LMT' && !limitPrice) {
      showToast('Enter a limit price'); return;
    }

    const btn = document.getElementById('mo-submit-btn');
    btn.textContent = 'Placing order…';
    btn.disabled = true;

    try {
      const trade = await apiFetch('/orders/manual', {
        method: 'POST',
        body: JSON.stringify({ symbol, action, quantity, order_type: orderType, limit_price: limitPrice }),
      });
      hideModal();
      showToast(`${action} ${quantity} ${symbol} submitted (id: ${trade.order_id ?? '—'})`);
      await refreshTrades();
    } catch (err) {
      showToast('Order failed: ' + err.message, 6000);
      btn.textContent = 'Submit Order';
      btn.disabled = false;
    }
  });
}

/* ══════════════════════════════════════════════════════════════
   DATA REFRESH
══════════════════════════════════════════════════════════════ */
async function refreshStatus() {
  try {
    const s = await apiFetch('/status');
    State.status.ibkr_connected = s.ibkr_connected;
    State.status.bot_running    = s.bot_running;
    State.status.is_paper       = s.is_paper;
    updateBadges();
  } catch (_) {}
}

async function refreshAccount() {
  if (!State.status.ibkr_connected) return;
  try { State.account = await apiFetch('/account'); } catch (_) {}
  try { State.positions = await apiFetch('/positions'); } catch (_) {}
}

async function refreshRules() {
  try { State.rules = await apiFetch('/rules'); } catch (_) {}
}

async function refreshTrades() {
  try { State.trades = await apiFetch('/trades?limit=100'); } catch (_) {}
}

async function refreshAll() {
  await refreshStatus();
  await Promise.all([refreshAccount(), refreshRules(), refreshTrades()]);
}

function updateBadges() {
  const connBadge = document.getElementById('conn-badge');
  const botBadge  = document.getElementById('bot-badge');
  if (connBadge) {
    connBadge.className = `badge ${State.status.ibkr_connected ? 'badge-online' : 'badge-offline'}`;
  }
  if (botBadge) {
    botBadge.className = `badge ${State.status.bot_running ? 'badge-running' : 'badge-stopped'}`;
  }
}

/* ══════════════════════════════════════════════════════════════
   WEBSOCKET
══════════════════════════════════════════════════════════════ */
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => console.log('[WS] connected');

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }

    switch (msg.type) {
      case 'bot':
        State.status.bot_running = msg.status === 'running';
        updateBadges();
        if (State.route === 'dashboard') render();
        break;

      case 'signal':
        State.signals.unshift({ ...msg, ts: new Date().toISOString() });
        if (State.signals.length > 20) State.signals.pop();
        showToast(`Signal: ${msg.action} ${msg.qty} ${msg.symbol} (${msg.rule_name})`);
        if (State.route === 'dashboard') render();
        break;

      case 'filled':
        showToast(`Order filled: ${msg.action} ${msg.qty} ${msg.symbol} @ ${fmtMoney(msg.price)}`);
        refreshTrades().then(() => { if (State.route === 'trades') render(); });
        break;

      case 'account':
        if (State.account) {
          State.account.balance = msg.balance;
          State.account.unrealized_pnl = msg.pnl;
        }
        if (State.route === 'dashboard') render();
        break;

      case 'bar':
        // Real-time 5-second bar from reqRealTimeBars
        if (State.chartSeries && msg.symbol === State.marketSymbol) {
          State.chartSeries.update({
            time: msg.time,
            open: msg.open,
            high: msg.high,
            low: msg.low,
            close: msg.close,
          });
          const meta = document.getElementById('chart-meta');
          if (meta) {
            meta.textContent = `Live · ${msg.symbol} · ${new Date(msg.time * 1000).toLocaleTimeString()} · close: ${fmtMoney(msg.close)}`;
          }
        }
        break;

      case 'error':
        showToast('Bot error: ' + msg.message, 5000);
        break;
    }
  };

  ws.onclose = () => {
    console.log('[WS] disconnected — reconnecting in 3s');
    setTimeout(connectWS, 3000);
  };

  // Keepalive ping every 30s
  setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('ping'); }, 30000);
}

/* ══════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════ */
async function init() {
  // Nav click handlers
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => navigate(btn.dataset.route));
  });

  // Floating trade button
  document.getElementById('fab-trade')?.addEventListener('click', () => showOrderModal(State.marketSymbol));

  // Modal backdrop close
  document.getElementById('modal-backdrop')?.addEventListener('click', hideModal);

  // Back button — context-aware
  document.getElementById('back-btn')?.addEventListener('click', () => {
    if (State.route === 'trade-detail') navigate('trades');
    else navigate('rules');
  });

  // Load initial data
  await refreshAll();

  // Initial render
  render();

  // Connect WebSocket
  connectWS();

  // Poll status every 30s
  setInterval(async () => {
    await refreshStatus();
    if (State.route === 'dashboard') {
      await refreshAccount();
      render();
    }
  }, 30000);

  // Poll trades every 60s
  setInterval(async () => {
    await refreshTrades();
    if (State.route === 'trades') render();
  }, 60000);

  // Auto-refresh watchlist every 30s (keeps crypto 24/7 prices current)
  setInterval(() => {
    if (State.route === 'watchlist' && !_wlFetching) loadWatchlistData();
  }, 30000);
}

init();
