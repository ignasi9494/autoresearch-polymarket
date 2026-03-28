// AutoResearch Dashboard - Auto-refresh every 15 seconds
const API = '/api/data';
let charts = {};

// Helpers
const fmt = (n, d=2) => n != null ? Number(n).toFixed(d) : '-';
const fmtUsd = n => n != null ? '$' + Number(n).toFixed(4) : '-';
const fmtUsd2 = n => n != null ? '$' + Number(n).toFixed(2) : '-';
const pct = n => n != null ? Number(n).toFixed(1) + '%' : '-';
const ts = s => s ? s.replace('T',' ').slice(0,19) : '-';
const chip = (text, color) => `<span class="chip chip-${color}">${text}</span>`;

// Tab switching
document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('panel-' + t.dataset.tab).classList.add('active');
  };
});

async function fetchData() {
  try {
    const r = await fetch(API);
    if (!r.ok) return null;
    return await r.json();
  } catch(e) {
    console.log('Fetch failed:', e);
    return null;
  }
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function renderKPIs(D) {
  const port = D.portfolio && D.portfolio.length > 0 ? D.portfolio[0] : {};
  const bal = port.balance_usd || 1000;
  const pnl = port.total_pnl || 0;
  const trades = port.total_trades || 0;
  const wins = port.winning_trades || 0;
  const wr = trades > 0 ? (wins/trades*100) : 0;
  const fees = port.total_fees || 0;
  const es = D.experiment_stats || {};

  document.getElementById('kpi-grid').innerHTML = [
    {l:'Balance',v:fmtUsd2(bal),c:bal>=1000?'pos':'neg'},
    {l:'PnL Total',v:fmtUsd2(pnl),c:pnl>=0?'pos':'neg'},
    {l:'Win Rate',v:pct(wr),s:`${wins}W / ${trades-wins}L`,c:wr>=50?'pos':'neg'},
    {l:'Trades',v:trades,s:`Fees: ${fmtUsd2(fees)}`},
    {l:'Experimentos',v:es.total||0,s:`${es.kept||0} kept, ${es.reverted||0} disc`},
    {l:'Polls',v:D.polls?D.polls.length:'0',s:'cada 30s'},
  ].map(k => `<div class="kpi"><div class="label">${k.l}</div><div class="value ${k.c||''}">${k.v}</div><div class="sub">${k.s||''}</div></div>`).join('');
}

function renderCoinCards(D) {
  const lpc = D.latest_per_coin || {};
  const coins = ['BTC','ETH','SOL','XRP','DOGE'];
  document.getElementById('coin-cards').innerHTML = coins.map(c => {
    const p = lpc[c];
    if (!p) return `<div class="coin-card"><div class="coin-name"><span class="dot no-arb"></span>${c}</div><div class="row">No data</div></div>`;
    const hasArb = p.gap > 0;
    const dotClass = hasArb ? 'arb' : 'no-arb';
    return `<div class="coin-card">
      <div class="coin-name"><span class="dot ${dotClass}"></span>${c} ${hasArb ? chip('ARB','green') : ''}</div>
      <div class="row"><span>YES ask</span><span class="val">${fmt(p.yes_ask,4)}</span></div>
      <div class="row"><span>NO ask</span><span class="val">${fmt(p.no_ask,4)}</span></div>
      <div class="row"><span>Total</span><span class="val ${p.total_ask<1?'pos':'neg'}">${fmt(p.total_ask,4)}</span></div>
      <div class="row"><span>Gap</span><span class="val ${p.gap>0?'pos':'neg'}">${(p.gap*100).toFixed(2)}%</span></div>
      <div class="row"><span>Spread Y/N</span><span class="val">${fmt(p.spread_yes,3)}/${fmt(p.spread_no,3)}</span></div>
      <div class="row"><span>Binance</span><span class="val">${fmtUsd2(p.binance_price)}</span></div>
    </div>`;
  }).join('');
}

function renderExperiments(D) {
  const exps = D.experiments || [];
  // Active experiment
  const active = exps.find(e => e.status === 'baseline' || e.status === 'running');
  if (active) {
    document.getElementById('phase-badge').textContent = active.status.toUpperCase();
    document.getElementById('active-exp').innerHTML = `
      <div class="active-exp">
        <div class="exp-title">Exp #${active.id}: ${active.hypothesis || '?'}</div>
        <div class="exp-detail">Estado: ${chip(active.status, active.status==='running'?'blue':'yellow')} | Inicio: ${ts(active.started_at)}</div>
      </div>`;
  } else {
    document.getElementById('phase-badge').textContent = 'IDLE';
    document.getElementById('active-exp').innerHTML = '<span style="color:var(--muted)">Ninguno activo</span>';
  }

  // Table
  const completed = exps.filter(e => e.status !== 'proposed');
  document.getElementById('exp-table').innerHTML = `<table><tr><th>#</th><th>Hipotesis</th><th>RAPR Base</th><th>RAPR Test</th><th>Mejora</th><th>p-value</th><th>Estado</th></tr>
    ${completed.map(e => {
      const st = e.status === 'completed' ? chip('KEEP','green') :
                 e.status === 'reverted' ? chip('DISCARD','red') :
                 e.status === 'crashed' ? chip('CRASH','orange') :
                 chip(e.status,'blue');
      return `<tr><td>${e.id}</td><td>${(e.hypothesis||'').slice(0,50)}</td>
        <td>${fmt(e.baseline_rapr,6)}</td><td>${fmt(e.test_rapr,6)}</td>
        <td class="${(e.improvement_pct||0)>0?'pos':'neg'}">${fmt(e.improvement_pct,1)}%</td>
        <td>${fmt(e.p_value,4)}</td><td>${st}</td></tr>`;
    }).join('')}</table>`;

  // RAPR chart
  destroyChart('ch-rapr');
  const scored = completed.filter(e => e.test_rapr != null).reverse();
  if (scored.length > 0) {
    charts['ch-rapr'] = new Chart(document.getElementById('ch-rapr'), {
      type: 'line',
      data: {
        labels: scored.map(e => '#' + e.id),
        datasets: [
          {label:'Baseline RAPR', data:scored.map(e=>e.baseline_rapr), borderColor:'#64748b', borderDash:[5,5], pointRadius:3, tension:.3},
          {label:'Test RAPR', data:scored.map(e=>e.test_rapr), borderColor:'#3b82f6', backgroundColor:'rgba(59,130,246,.1)', fill:true, pointRadius:4, tension:.3},
        ]
      },
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#e2e8f0',font:{size:11}}}},scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b'}},x:{grid:{display:false},ticks:{color:'#64748b'}}}}
    });
  }
}

function renderTrades(D) {
  const trades = D.trades || [];
  const filled = trades.filter(t => t.filled);

  document.getElementById('trades-table').innerHTML = `<table><tr><th>#</th><th>Moneda</th><th>Size</th><th>Cost</th><th>Fees</th><th>PnL</th><th>Filled</th><th>Fase</th><th>Hora</th></tr>
    ${trades.slice(0,50).map(t => `<tr>
      <td>${t.id}</td><td>${chip(t.coin,'purple')}</td>
      <td>${fmtUsd2(t.size_usd)}</td><td>${fmt(t.total_cost,4)}</td>
      <td>${fmtUsd(t.fees)}</td>
      <td class="${t.net_pnl>0?'pos':'neg'}">${fmtUsd(t.net_pnl)}</td>
      <td>${t.filled?chip('SI','green'):chip('NO','red')}</td>
      <td>${chip(t.phase||'?','blue')}</td>
      <td>${ts(t.open_at)}</td></tr>`).join('')}</table>`;

  // PnL chart
  destroyChart('ch-pnl');
  if (filled.length > 0) {
    const recent = [...filled].reverse().slice(-30);
    charts['ch-pnl'] = new Chart(document.getElementById('ch-pnl'), {
      type:'bar', data:{labels:recent.map(t=>t.coin+' #'+t.id),
      datasets:[{label:'PnL',data:recent.map(t=>t.net_pnl||0),
        backgroundColor:recent.map(t=>t.net_pnl>0?'rgba(34,197,94,.7)':'rgba(239,68,68,.7)'),borderRadius:3}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b'}},x:{grid:{display:false},ticks:{color:'#64748b',font:{size:9}}}}}
    });
  }

  // Equity curve
  destroyChart('ch-equity');
  const port = (D.portfolio||[]).slice().reverse();
  if (port.length > 0) {
    charts['ch-equity'] = new Chart(document.getElementById('ch-equity'), {
      type:'line', data:{labels:port.map(p=>ts(p.timestamp).slice(11,19)),
      datasets:[{label:'Balance ($)',data:port.map(p=>p.balance_usd),borderColor:'#06b6d4',backgroundColor:'rgba(6,182,212,.1)',fill:true,tension:.3,pointRadius:2}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#e2e8f0'}}},scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b',callback:v=>'$'+v}},x:{grid:{display:false},ticks:{color:'#64748b'}}}}
    });
  }
}

function renderPerformance(D) {
  const trades = (D.trades||[]).filter(t=>t.filled);

  // PnL by coin
  const byCoin = {};
  trades.forEach(t => {
    if (!byCoin[t.coin]) byCoin[t.coin] = {pnl:0, count:0, fees:0};
    byCoin[t.coin].pnl += t.net_pnl || 0;
    byCoin[t.coin].count++;
    byCoin[t.coin].fees += t.fees || 0;
  });
  const coins = Object.keys(byCoin);

  destroyChart('ch-coin-pnl');
  if (coins.length > 0) {
    charts['ch-coin-pnl'] = new Chart(document.getElementById('ch-coin-pnl'), {
      type:'bar', data:{labels:coins,
      datasets:[{label:'PnL',data:coins.map(c=>byCoin[c].pnl),
        backgroundColor:coins.map(c=>byCoin[c].pnl>=0?'rgba(34,197,94,.7)':'rgba(239,68,68,.7)'),borderRadius:4}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b'}},x:{grid:{display:false},ticks:{color:'#64748b'}}}}
    });
  }

  // Gaps histogram from polls
  destroyChart('ch-gaps');
  const polls = D.polls || [];
  const gaps = polls.map(p => p.gap * 100).filter(g => g !== 0);
  if (gaps.length > 0) {
    // Simple histogram: bucket into ranges
    const buckets = {};
    gaps.forEach(g => {
      const b = Math.round(g * 2) / 2; // Round to 0.5%
      const key = b.toFixed(1) + '%';
      buckets[key] = (buckets[key] || 0) + 1;
    });
    const labels = Object.keys(buckets).sort((a,b) => parseFloat(a)-parseFloat(b));
    charts['ch-gaps'] = new Chart(document.getElementById('ch-gaps'), {
      type:'bar', data:{labels, datasets:[{label:'Count',data:labels.map(l=>buckets[l]),
        backgroundColor:'rgba(168,85,247,.6)',borderRadius:3}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b'}},x:{grid:{display:false},ticks:{color:'#64748b'}}}}
    });
  }

  // Cost breakdown
  const totalPnl = trades.reduce((s,t) => s + (t.net_pnl||0), 0);
  const totalFees = trades.reduce((s,t) => s + (t.fees||0), 0);
  const totalSlip = trades.reduce((s,t) => s + (t.slippage||0), 0);
  const grossProfit = totalPnl + totalFees + totalSlip;
  document.getElementById('cost-breakdown').innerHTML = `<table>
    <tr><td>Profit bruto</td><td class="pos">${fmtUsd2(grossProfit)}</td></tr>
    <tr><td>Fees totales</td><td class="neg">-${fmtUsd2(totalFees)}</td></tr>
    <tr><td>Slippage total</td><td class="neg">-${fmtUsd2(totalSlip)}</td></tr>
    <tr><td><b>Profit neto</b></td><td class="${totalPnl>=0?'pos':'neg'}"><b>${fmtUsd2(totalPnl)}</b></td></tr>
    <tr><td>Trades</td><td>${trades.length}</td></tr>
    <tr><td>PnL medio/trade</td><td>${trades.length>0?fmtUsd(totalPnl/trades.length):'-'}</td></tr>
  </table>`;
}

function renderResearch(D) {
  document.getElementById('results-tsv').textContent = D.results_tsv || 'No results yet';
  document.getElementById('strategy-code').textContent = D.strategy_code || 'No strategy loaded';
}

async function refresh() {
  const D = await fetchData();
  if (!D) return;

  document.getElementById('update-time').textContent = 'Updated: ' + ts(D.generated_at);

  renderKPIs(D);
  renderCoinCards(D);
  renderExperiments(D);
  renderTrades(D);
  renderPerformance(D);
  renderResearch(D);
}

// Initial load + auto-refresh
refresh();
setInterval(refresh, 15000);
