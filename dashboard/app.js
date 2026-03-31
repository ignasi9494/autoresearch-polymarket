// AutoResearch Dashboard v4 - Full history, filters, pagination
const API = '/api/data';
let charts = {};
let isDemo = false;
let DATA = null; // Global data cache

// Pagination state
let tradePages = { page: 1, perPage: 50 };
let expPages = { page: 1, perPage: 30 };

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

// Data fetching
async function fetchData() {
  try {
    const r = await fetch(API);
    if (!r.ok) throw new Error('API error');
    isDemo = false;
    return await r.json();
  } catch(e) {
    if (!isDemo) console.log('API unavailable, using demo data');
    isDemo = true;
    return getDemoData();
  }
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

// Chart defaults
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 11;

// ─── KPIs ────────────────────────────────────────────────────────
function renderKPIs(D) {
  const port = D.portfolio?.[0] || {};
  const bal = port.balance_usd || 1000;
  const pnl = port.total_pnl || 0;
  const trades = port.total_trades || 0;
  const wins = port.winning_trades || 0;
  const losses = port.losing_trades || (trades - wins);
  const wr = trades > 0 ? (wins/trades*100) : 0;
  const fees = port.total_fees || 0;
  const es = D.experiment_stats || {};

  const allTrades = D.trades || [];
  const arbFills = allTrades.filter(t => t.filled === 1).length;
  const totalOrders = allTrades.length;
  const fillRate = totalOrders > 0 ? (arbFills / totalOrders * 100) : 0;

  // Calculate total ARB profit and partial costs
  const arbPnl = allTrades.filter(t => t.filled === 1).reduce((s,t) => s + (t.net_pnl||0), 0);
  const roi = bal > 0 ? ((bal - 1000) / 1000 * 100) : 0;

  const kpis = [
    {l:'Balance', v:fmtUsd2(bal), c:bal>=1000?'pos':'neg', s:`ROI: ${roi>=0?'+':''}${roi.toFixed(1)}%`},
    {l:'PnL Neto', v:(pnl>=0?'+':'')+fmtUsd2(pnl), c:pnl>=0?'pos':'neg', s:`ARB profit: ${fmtUsd2(arbPnl)}`},
    {l:'Win Rate', v:pct(wr), c:wr>=30?'pos':'neg', s:`${wins}W / ${losses}L de ${trades}`},
    {l:'ARB Fill Rate', v:pct(fillRate), c:fillRate>=10?'pos':'neg', s:`${arbFills} arbs de ${totalOrders}`},
    {l:'Experimentos', v:es.total||0, s:`KEPT ${es.kept||0} / DISC ${es.reverted||0}`},
    {l:'Total Trades', v:totalOrders.toLocaleString(), s:isDemo ? 'Demo data' : 'Live'},
  ];

  document.getElementById('kpi-grid').innerHTML = kpis.map(k =>
    `<div class="kpi"><div class="label">${k.l}</div><div class="value ${k.c||''}">${k.v}</div><div class="sub">${k.s||''}</div></div>`
  ).join('');
}

// ─── Coin Cards ──────────────────────────────────────────────────
function renderCoinCards(D) {
  const lpc = D.latest_per_coin || {};
  const coins = ['BTC','ETH','SOL','XRP','DOGE'];
  const icons = {BTC:'B',ETH:'E',SOL:'S',XRP:'X',DOGE:'D'};

  // Calculate per-coin stats from all trades
  const coinStats = {};
  (D.trades||[]).forEach(t => {
    if (!coinStats[t.coin]) coinStats[t.coin] = {arb:0,total:0,pnl:0};
    coinStats[t.coin].total++;
    if (t.filled === 1) coinStats[t.coin].arb++;
    coinStats[t.coin].pnl += t.net_pnl || 0;
  });

  document.getElementById('coin-cards').innerHTML = coins.map(c => {
    const p = lpc[c];
    const st = coinStats[c] || {arb:0,total:0,pnl:0};
    const fr = st.total > 0 ? (st.arb/st.total*100) : 0;

    if (!p) return `<div class="coin-card"><div class="coin-name"><span class="dot no-arb"></span><span class="coin-letter">${icons[c]}</span> ${c}</div><div class="row" style="justify-content:center;color:var(--muted)">Sin datos</div></div>`;

    const impliedUp = p.yes_mid || 0;
    const impliedDown = p.no_mid || 0;
    const impliedTotal = impliedUp + impliedDown;
    const rawEdge = (1.0 - impliedTotal) * 100;
    const hasEdge = rawEdge > 0.5;

    return `<div class="coin-card">
      <div class="coin-name"><span class="dot ${hasEdge?'arb':'no-arb'}"></span><span class="coin-letter">${icons[c]}</span> ${c} ${hasEdge ? chip('EDGE','green') : ''}</div>
      <div class="row"><span>Implied Up/Down</span><span class="val">${fmt(impliedUp,3)} / ${fmt(impliedDown,3)}</span></div>
      <div class="row"><span>Suma</span><span class="val ${impliedTotal<0.995?'pos':'neg'}">${fmt(impliedTotal,4)}</span></div>
      <div class="row"><span>Edge bruto</span><span class="val ${rawEdge>0?'pos':'neg'}">${rawEdge.toFixed(2)}c</span></div>
      <div class="row"><span>Depth</span><span class="val">$${fmt(p.depth_yes_usd,0)} / $${fmt(p.depth_no_usd,0)}</span></div>
      <div class="row"><span>Vol</span><span class="val">${pct((p.volatility_1h||0)*100)}</span></div>
      <div class="coin-stats">
        <span>ARBs: ${st.arb}</span>
        <span>Fill: ${pct(fr)}</span>
        <span class="${st.pnl>=0?'pos':'neg'}">PnL: ${fmtUsd2(st.pnl)}</span>
      </div>
    </div>`;
  }).join('');
}

// ─── Experiments ─────────────────────────────────────────────────
function renderExperiments(D) {
  const exps = D.experiments || [];
  const active = exps.find(e => e.status === 'baseline' || e.status === 'running');
  const filter = document.getElementById('exp-filter').value;

  if (active) {
    document.getElementById('phase-badge').textContent = active.status.toUpperCase();
    document.getElementById('phase-badge').className = 'badge badge-' + (active.status==='running'?'blue':'yellow');
    document.getElementById('active-exp').innerHTML = `
      <div class="active-exp">
        <div class="exp-title">Exp #${active.id}: ${active.hypothesis || '?'}</div>
        <div class="exp-detail">Estado: ${chip(active.status, active.status==='running'?'blue':'yellow')} &middot; Inicio: ${ts(active.started_at)}</div>
      </div>`;
  } else {
    document.getElementById('phase-badge').textContent = 'IDLE';
    document.getElementById('phase-badge').className = 'badge badge-blue';
    document.getElementById('active-exp').innerHTML = '<div class="empty-state">Ninguno activo - esperando proximo ciclo</div>';
  }

  // Parameter evolution from kept experiments
  const kept = exps.filter(e => e.status === 'completed').reverse();
  if (kept.length > 0) {
    document.getElementById('param-evolution').textContent = kept.map(e =>
      `#${e.id} KEPT: ${(e.hypothesis||'').slice(0,70)}\n  test=$${(e.test_pnl||0).toFixed(3)} base=$${(e.baseline_pnl||0).toFixed(3)} p=${(e.p_value||1).toFixed(3)}`
    ).join('\n\n');
  } else {
    document.getElementById('param-evolution').textContent = 'Aun no hay experimentos mantenidos (kept).';
  }

  // Filter experiments
  let filtered = exps.filter(e => e.status !== 'proposed');
  if (filter !== 'all') filtered = filtered.filter(e => e.status === filter);

  document.getElementById('exp-count').textContent = filtered.length + '/' + exps.length;

  // Paginate
  const totalPages = Math.ceil(filtered.length / expPages.perPage);
  if (expPages.page > totalPages) expPages.page = 1;
  const start = (expPages.page - 1) * expPages.perPage;
  const pageExps = filtered.slice(start, start + expPages.perPage);

  document.getElementById('exp-table').innerHTML = pageExps.length === 0
    ? '<div class="empty-state">No hay experimentos con este filtro</div>'
    : `<table><tr><th>#</th><th>Hipotesis</th><th>Test PnL</th><th>Base PnL</th><th>Mejora</th><th>p-val</th><th>Estado</th></tr>
    ${pageExps.map(e => {
      const st = e.status === 'completed' ? chip('KEPT','green') :
                 e.status === 'reverted' ? chip('DISC','red') :
                 e.status === 'crashed' ? chip('CRASH','orange') :
                 chip(e.status,'blue');
      const imp = e.improvement_pct || 0;
      const testPnl = e.test_pnl || 0;
      const basePnl = e.baseline_pnl || 0;
      return `<tr class="${e.status === 'completed' ? 'row-kept' : ''}">
        <td>${e.id}</td>
        <td title="${(e.hypothesis||'').replace(/"/g,'&quot;')}">${(e.hypothesis||'').slice(0,50)}${(e.hypothesis||'').length>50?'...':''}</td>
        <td class="${testPnl>0?'pos':'neg'}">${fmtUsd2(testPnl)}</td>
        <td class="${basePnl>0?'pos':'neg'}">${fmtUsd2(basePnl)}</td>
        <td class="${imp>0?'pos':'neg'}">${imp>0?'+':''}${fmt(imp,1)}%</td>
        <td>${fmt(e.p_value,4)}</td>
        <td>${st}</td></tr>`;
    }).join('')}</table>`;

  // Pagination controls
  document.getElementById('exp-pagination').innerHTML = totalPages > 1
    ? renderPagination(expPages.page, totalPages, 'expPages')
    : '';

  // RAPR chart
  destroyChart('ch-rapr');
  const scored = exps.filter(e => e.test_rapr != null && e.status !== 'proposed').reverse();
  if (scored.length > 0) {
    const keptIds = new Set(kept.map(e => e.id));
    charts['ch-rapr'] = new Chart(document.getElementById('ch-rapr'), {
      type: 'line',
      data: {
        labels: scored.map(e => '#' + e.id),
        datasets: [
          {label:'Baseline', data:scored.map(e=>e.baseline_rapr), borderColor:'#64748b', borderDash:[5,5], pointRadius:3, tension:.3, borderWidth:1.5},
          {label:'Test', data:scored.map(e=>e.test_rapr), borderColor:'#06b6d4', backgroundColor:'rgba(6,182,212,.1)', fill:true, pointRadius:scored.map(e=>keptIds.has(e.id)?8:3), pointBackgroundColor:scored.map(e=>keptIds.has(e.id)?'#22c55e':'#06b6d4'), tension:.3, borderWidth:2},
        ]
      },
      options: chartOpts('RAPR Score (green dots = KEPT)'),
    });
  }
}

// ─── Trades ──────────────────────────────────────────────────────
function renderTrades(D) {
  const allTrades = D.trades || [];
  const filterType = document.getElementById('trade-filter-type').value;
  const filterCoin = document.getElementById('trade-filter-coin').value;

  // Apply filters
  let filtered = allTrades;
  if (filterType === 'arb') filtered = filtered.filter(t => t.filled === 1);
  else if (filterType === 'partial') filtered = filtered.filter(t => t.filled !== 1 && t.net_pnl && t.net_pnl !== 0);
  else if (filterType === 'miss') filtered = filtered.filter(t => !t.filled && (!t.net_pnl || t.net_pnl === 0));
  if (filterCoin !== 'all') filtered = filtered.filter(t => t.coin === filterCoin);

  document.getElementById('trade-count').textContent = filtered.length + '/' + allTrades.length;

  // Paginate
  const totalPages = Math.ceil(filtered.length / tradePages.perPage);
  if (tradePages.page > totalPages) tradePages.page = Math.max(1, totalPages);
  const start = (tradePages.page - 1) * tradePages.perPage;
  const pageTrades = filtered.slice(start, start + tradePages.perPage);

  document.getElementById('trades-table').innerHTML = pageTrades.length === 0
    ? '<div class="empty-state">No hay trades con estos filtros</div>'
    : `<table><tr><th>#</th><th>Coin</th><th>Size</th><th>Cost</th><th>Fees</th><th>PnL</th><th>Tipo</th><th>Edge</th><th>Hora</th></tr>
    ${pageTrades.map(t => {
      const type = t.filled === 1 ? chip('ARB','green') :
                   (t.net_pnl && t.net_pnl !== 0) ? chip('PART','yellow') :
                   chip('MISS','red');
      const pnlVal = t.net_pnl || 0;
      const edge = t.total_cost ? ((1 - t.total_cost) * 100).toFixed(2) + 'c' : '-';
      return `<tr>
      <td>${t.id}</td><td>${chip(t.coin,'purple')}</td>
      <td>${fmtUsd2(t.size_usd)}</td><td>${fmt(t.total_cost,4)}</td>
      <td>${fmtUsd(t.fees)}</td>
      <td class="${pnlVal>0?'pos':pnlVal<0?'neg':''}">${pnlVal>0?'+':''}${fmtUsd(pnlVal)}</td>
      <td>${type}</td>
      <td>${edge}</td>
      <td>${ts(t.window_end||t.open_at)}</td></tr>`;
    }).join('')}</table>`;

  // Pagination
  document.getElementById('trades-pagination').innerHTML = totalPages > 1
    ? renderPagination(tradePages.page, totalPages, 'tradePages')
    : '';

  // PnL chart (last 50 filled trades)
  const filled = allTrades.filter(t => t.filled || (t.net_pnl && t.net_pnl !== 0));
  destroyChart('ch-pnl');
  if (filled.length > 0) {
    const recent = [...filled].reverse().slice(-50);
    charts['ch-pnl'] = new Chart(document.getElementById('ch-pnl'), {
      type:'bar', data:{labels:recent.map(t=>t.coin+' #'+t.id),
      datasets:[{label:'PnL',data:recent.map(t=>t.net_pnl||0),
        backgroundColor:recent.map(t=>(t.net_pnl||0)>0?'rgba(34,197,94,.7)':'rgba(239,68,68,.6)'),
        borderRadius:3, borderSkipped:false}]},
      options:{...chartOpts(),plugins:{legend:{display:false}},scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b',callback:v=>'$'+v.toFixed(4)}},x:{grid:{display:false},ticks:{color:'#475569',font:{size:7},maxRotation:45}}}},
    });
  }

  // Equity curve (full portfolio history)
  destroyChart('ch-equity');
  const port = (D.portfolio||[]).slice().reverse();
  if (port.length > 0) {
    // Sample if too many points
    const maxPoints = 200;
    const step = Math.max(1, Math.floor(port.length / maxPoints));
    const sampled = port.filter((_, i) => i % step === 0 || i === port.length - 1);
    charts['ch-equity'] = new Chart(document.getElementById('ch-equity'), {
      type:'line', data:{labels:sampled.map((_,i)=>i*step),
      datasets:[
        {label:'Balance ($)',data:sampled.map(p=>p.balance_usd),
        borderColor:'#06b6d4',backgroundColor:'rgba(6,182,212,.08)',fill:true,tension:.4,
        pointRadius:0,borderWidth:2},
        {label:'$1,000 baseline',data:sampled.map(()=>1000),
        borderColor:'#64748b',borderDash:[5,5],pointRadius:0,borderWidth:1,fill:false},
      ]},
      options:{...chartOpts(),scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b',callback:v=>'$'+v.toFixed(0)}},x:{display:false}},plugins:{legend:{labels:{color:'#e2e8f0',font:{size:10}}}}},
    });
  }
}

// ─── Fill Analysis ───────────────────────────────────────────────
function renderFills(D) {
  const trades = D.trades || [];
  if (!trades.length) return;

  const arbFills = trades.filter(t => t.filled === 1);
  const partials = trades.filter(t => t.filled === 0 && t.net_pnl && t.net_pnl !== 0);
  const misses = trades.filter(t => !t.filled && (!t.net_pnl || t.net_pnl === 0));

  // Separate old vs new partial fills
  const oldPartials = partials.filter(t => (t.net_pnl||0) < -0.01);
  const newPartials = partials.filter(t => (t.net_pnl||0) >= -0.01);

  destroyChart('ch-fill-types');
  charts['ch-fill-types'] = new Chart(document.getElementById('ch-fill-types'), {
    type: 'doughnut',
    data: {
      labels: [`ARB (${arbFills.length})`, `Partial-gas (${newPartials.length})`, `Partial-old (${oldPartials.length})`, `Miss (${misses.length})`],
      datasets: [{
        data: [arbFills.length, newPartials.length, oldPartials.length, misses.length],
        backgroundColor: ['rgba(34,197,94,.75)', 'rgba(234,179,8,.5)', 'rgba(249,115,22,.7)', 'rgba(239,68,68,.35)'],
        borderColor: ['#22c55e', '#eab308', '#f97316', '#ef4444'],
        borderWidth: 2, hoverOffset: 8,
      }]
    },
    options: {responsive:true,maintainAspectRatio:false,cutout:'60%',
      plugins:{legend:{position:'bottom',labels:{color:'#e2e8f0',font:{size:10},padding:10}}}},
  });

  // By coin
  const byCoin = {};
  trades.forEach(t => {
    if (!byCoin[t.coin]) byCoin[t.coin] = {arb:0, partial:0, miss:0, total:0};
    byCoin[t.coin].total++;
    if (t.filled === 1) byCoin[t.coin].arb++;
    else if (t.net_pnl && t.net_pnl !== 0) byCoin[t.coin].partial++;
    else byCoin[t.coin].miss++;
  });
  const coins = Object.keys(byCoin).sort();

  destroyChart('ch-fill-coin');
  if (coins.length) {
    charts['ch-fill-coin'] = new Chart(document.getElementById('ch-fill-coin'), {
      type:'bar',
      data:{labels:coins, datasets:[
        {label:'ARB',data:coins.map(c=>byCoin[c].arb),backgroundColor:'rgba(34,197,94,.7)',borderRadius:3},
        {label:'Partial',data:coins.map(c=>byCoin[c].partial),backgroundColor:'rgba(234,179,8,.7)',borderRadius:3},
        {label:'Miss',data:coins.map(c=>byCoin[c].miss),backgroundColor:'rgba(239,68,68,.3)',borderRadius:3},
      ]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#e2e8f0',font:{size:11}}}},
        scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,grid:{color:'#1e293b'}}}},
    });
  }

  // Stats table
  const arbPnl = arbFills.reduce((s,t) => s + (t.net_pnl||0), 0);
  const oldPartPnl = oldPartials.reduce((s,t) => s + (t.net_pnl||0), 0);
  const newPartPnl = newPartials.reduce((s,t) => s + (t.net_pnl||0), 0);
  const fr = trades.length ? (arbFills.length/trades.length*100) : 0;

  document.getElementById('fill-stats').innerHTML = `<table>
    <tr><td>Total ordenes</td><td><strong>${trades.length.toLocaleString()}</strong></td></tr>
    <tr><td>${chip('ARB','green')} fills</td><td class="pos"><strong>${arbFills.length}</strong> (${pct(fr)})</td></tr>
    <tr><td>${chip('PART','yellow')} gas only</td><td>${newPartials.length}</td></tr>
    <tr><td>${chip('PART','orange')} old (directional)</td><td>${oldPartials.length}</td></tr>
    <tr><td>${chip('MISS','red')} (ninguno)</td><td>${misses.length}</td></tr>
    <tr><td colspan="2" style="border-top:2px solid var(--border)"></td></tr>
    <tr><td>PnL ARB total</td><td class="pos"><strong>${fmtUsd2(arbPnl)}</strong></td></tr>
    <tr><td>PnL Partial (gas)</td><td class="neg">${fmtUsd2(newPartPnl)}</td></tr>
    <tr><td>PnL Partial (old)</td><td class="neg">${fmtUsd2(oldPartPnl)}</td></tr>
    <tr><td>Avg PnL por ARB</td><td class="pos">${arbFills.length ? fmtUsd(arbPnl/arbFills.length) : '-'}</td></tr>
    <tr><td>Sin legacy damage</td><td class="pos"><strong>${fmtUsd2(arbPnl + newPartPnl)}</strong></td></tr>
  </table>`;
}

// ─── Performance ─────────────────────────────────────────────────
function renderPerformance(D) {
  const trades = D.trades || [];
  const filled = trades.filter(t=>t.filled || (t.net_pnl && t.net_pnl!==0));

  const byCoin = {};
  trades.forEach(t => {
    if (!byCoin[t.coin]) byCoin[t.coin] = {pnl:0,count:0,fees:0,arb:0,total:0};
    byCoin[t.coin].total++;
    if (t.filled === 1) byCoin[t.coin].arb++;
    byCoin[t.coin].pnl += t.net_pnl || 0;
    byCoin[t.coin].count++;
    byCoin[t.coin].fees += t.fees || 0;
  });
  const coins = Object.keys(byCoin).sort();

  // PnL by coin
  destroyChart('ch-coin-pnl');
  if (coins.length) {
    charts['ch-coin-pnl'] = new Chart(document.getElementById('ch-coin-pnl'), {
      type:'bar',data:{labels:coins,
      datasets:[{label:'PnL',data:coins.map(c=>byCoin[c].pnl),
        backgroundColor:coins.map(c=>byCoin[c].pnl>=0?'rgba(34,197,94,.7)':'rgba(239,68,68,.6)'),
        borderRadius:6,borderSkipped:false}]},
      options:{...chartOpts(),plugins:{legend:{display:false}},
        scales:{y:{grid:{color:'#1e293b'},ticks:{callback:v=>'$'+v.toFixed(2)}},x:{grid:{display:false}}}},
    });
  }

  // Fill rate by coin
  destroyChart('ch-coin-fillrate');
  if (coins.length) {
    charts['ch-coin-fillrate'] = new Chart(document.getElementById('ch-coin-fillrate'), {
      type:'bar',data:{labels:coins,
      datasets:[{label:'ARB Fill Rate %',data:coins.map(c=>byCoin[c].total>0?(byCoin[c].arb/byCoin[c].total*100):0),
        backgroundColor:'rgba(6,182,212,.6)',borderRadius:6}]},
      options:{...chartOpts(),plugins:{legend:{display:false}},
        scales:{y:{grid:{color:'#1e293b'},ticks:{callback:v=>v+'%'},max:30},x:{grid:{display:false}}}},
    });
  }

  // Edge distribution
  destroyChart('ch-edges');
  const edges = trades.map(t=>t.total_cost&&t.total_cost>0?(1-t.total_cost)*100:null).filter(e=>e!=null);
  if (edges.length) {
    const buckets = {};
    edges.forEach(e => { const k=Math.round(e*2)/2; buckets[k.toFixed(1)+'c']=(buckets[k.toFixed(1)+'c']||0)+1; });
    const labels = Object.keys(buckets).sort((a,b)=>parseFloat(a)-parseFloat(b));
    charts['ch-edges'] = new Chart(document.getElementById('ch-edges'), {
      type:'bar',data:{labels,datasets:[{label:'Trades',data:labels.map(l=>buckets[l]),
        backgroundColor:'rgba(168,85,247,.6)',borderRadius:4}]},
      options:{...chartOpts(),plugins:{legend:{display:false}},
        scales:{y:{grid:{color:'#1e293b'}},x:{grid:{display:false}}}},
    });
  }

  // Cost breakdown
  const totalPnl = filled.reduce((s,t)=>s+(t.net_pnl||0),0);
  const totalFees = filled.reduce((s,t)=>s+(t.fees||0),0);
  const grossProfit = totalPnl + totalFees;
  const arbOnly = trades.filter(t=>t.filled===1);
  const arbPnlTotal = arbOnly.reduce((s,t)=>s+(t.net_pnl||0),0);
  document.getElementById('cost-breakdown').innerHTML = `<table>
    <tr><td>Revenue ARB fills</td><td class="pos">${fmtUsd2(arbPnlTotal)}</td></tr>
    <tr><td>Partial fill costs (gas)</td><td class="neg">${fmtUsd2(totalPnl - arbPnlTotal)}</td></tr>
    <tr><td style="border-top:2px solid var(--border)"><strong>Profit neto</strong></td><td style="border-top:2px solid var(--border)" class="${totalPnl>=0?'pos':'neg'}"><strong>${fmtUsd2(totalPnl)}</strong></td></tr>
    <tr><td>Trades con fill</td><td>${filled.length.toLocaleString()}</td></tr>
    <tr><td>PnL medio por trade</td><td>${filled.length?fmtUsd(totalPnl/filled.length):'-'}</td></tr>
    <tr><td>ARB fills</td><td>${arbOnly.length} (avg ${arbOnly.length?fmtUsd(arbPnlTotal/arbOnly.length):'-'}/trade)</td></tr>
  </table>`;
}

// ─── Strategy ────────────────────────────────────────────────────
function renderStrategy(D) {
  document.getElementById('results-tsv').textContent = D.results_tsv || 'No hay resultados todavia.';
  document.getElementById('strategy-code').textContent = D.strategy_code || 'strategy.py no disponible.';
}

// ─── Pagination helper ───────────────────────────────────────────
function renderPagination(current, total, stateVar) {
  let html = '<div class="page-controls">';
  html += `<button onclick="${stateVar}.page=1;rerender()" ${current===1?'disabled':''}>&#171;</button>`;
  html += `<button onclick="${stateVar}.page=${current-1};rerender()" ${current===1?'disabled':''}>&#8249;</button>`;
  html += `<span class="page-info">Pagina ${current} de ${total}</span>`;
  html += `<button onclick="${stateVar}.page=${current+1};rerender()" ${current===total?'disabled':''}>&#8250;</button>`;
  html += `<button onclick="${stateVar}.page=${total};rerender()" ${current===total?'disabled':''}>&#187;</button>`;
  html += '</div>';
  return html;
}

// ─── Chart options helper ────────────────────────────────────────
function chartOpts(title) {
  return {
    responsive:true, maintainAspectRatio:false,
    plugins:{
      legend:{labels:{color:'#e2e8f0',font:{size:11},boxWidth:12,padding:12}},
      ...(title ? {title:{display:true,text:title,color:'#94a3b8',font:{size:12}}} : {}),
    },
    scales:{
      y:{grid:{color:'#1e293b'},ticks:{color:'#64748b'}},
      x:{grid:{display:false},ticks:{color:'#64748b'}},
    },
  };
}

// ─── Re-render with current data ─────────────────────────────────
function rerender() {
  if (!DATA) return;
  renderExperiments(DATA);
  renderTrades(DATA);
}

// ─── Main refresh ────────────────────────────────────────────────
async function refresh() {
  DATA = await fetchData();
  if (!DATA) return;

  document.getElementById('update-time').textContent = (isDemo ? 'Demo' : 'Live') + ' - ' + ts(DATA.generated_at);
  document.getElementById('status-badge').textContent = isDemo ? 'DEMO' : 'LIVE';
  document.getElementById('status-badge').className = 'badge ' + (isDemo ? 'badge-yellow' : 'badge-green');

  renderKPIs(DATA);
  renderCoinCards(DATA);
  renderExperiments(DATA);
  renderTrades(DATA);
  renderFills(DATA);
  renderPerformance(DATA);
  renderStrategy(DATA);
}

// ─── Filter change handlers ──────────────────────────────────────
document.getElementById('exp-filter').onchange = () => { expPages.page = 1; rerender(); };
document.getElementById('trade-filter-type').onchange = () => { tradePages.page = 1; rerender(); };
document.getElementById('trade-filter-coin').onchange = () => { tradePages.page = 1; rerender(); };

// ─── Demo data ───────────────────────────────────────────────────
function getDemoData() {
  const now = new Date().toISOString();
  const coins = ['BTC','ETH','SOL','XRP','DOGE'];
  const polls = coins.map(c => ({
    coin:c, yes_mid:0.495+Math.random()*0.02, no_mid:0.495+Math.random()*0.02,
    spread_yes:0.01+Math.random()*0.03, spread_no:0.01+Math.random()*0.03,
    depth_yes_usd:Math.random()*800, depth_no_usd:Math.random()*800,
    binance_price:0, volatility_1h:0.02+Math.random()*0.04,
  }));
  const lpc = {}; polls.forEach(p => lpc[p.coin] = p);
  const trades = [];
  for (let i=1; i<=50; i++) {
    const filled = Math.random() > 0.85 ? 1 : 0;
    const pnl = filled ? 0.3+Math.random()*0.8 : (Math.random()>0.6 ? -0.008 : 0);
    trades.push({id:i, coin:coins[i%5], size_usd:20, total_cost:0.96+Math.random()*0.03,
      fees:0.01, net_pnl:pnl, filled, phase:'test', window_end:new Date(Date.now()-i*120000).toISOString()});
  }
  return {generated_at:now, polls, latest_per_coin:lpc, trades, portfolio:[{balance_usd:1050,total_pnl:50,total_trades:50,total_fees:5,winning_trades:12,losing_trades:38}],
    experiments:[{id:1,hypothesis:'[RANDOM] Change ORDER_SIZE_USD from 15 to 20',status:'completed',baseline_rapr:0.001,test_rapr:0.002,test_pnl:8.5,baseline_pnl:0.2,improvement_pct:100,p_value:0.05,started_at:now}],
    experiment_stats:{total:1,kept:1,reverted:0,crashed:0}, strategy_code:'# Demo', results_tsv:'# Demo'};
}

// ─── Init ────────────────────────────────────────────────────────
refresh();
setInterval(refresh, 30000);
