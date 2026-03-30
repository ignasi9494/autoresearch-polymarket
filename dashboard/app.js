// ═══════════════════════════════════════════════════════════════════
// AutoResearch Dashboard v3 - Real-time monitoring
// Auto-refresh every 15 seconds via /api/data
// Falls back to demo data when API is unavailable (Vercel static)
// ═══════════════════════════════════════════════════════════════════

const API = '/api/data';
let charts = {};
let isDemo = false;

// ─── Helpers ──────────────────────────────────────────────────────
const fmt = (n, d=2) => n != null ? Number(n).toFixed(d) : '-';
const fmtUsd = n => n != null ? '$' + Number(n).toFixed(4) : '-';
const fmtUsd2 = n => n != null ? '$' + Number(n).toFixed(2) : '-';
const pct = n => n != null ? Number(n).toFixed(1) + '%' : '-';
const ts = s => s ? s.replace('T',' ').slice(0,19) : '-';
const chip = (text, color) => `<span class="chip chip-${color}">${text}</span>`;

// ─── Tab switching ────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('panel-' + t.dataset.tab).classList.add('active');
  };
});

// ─── Data fetching ────────────────────────────────────────────────
async function fetchData() {
  try {
    const r = await fetch(API);
    if (!r.ok) throw new Error('API error');
    isDemo = false;
    return await r.json();
  } catch(e) {
    // Fallback to demo data for Vercel static deploy
    if (!isDemo) console.log('API unavailable, using demo data');
    isDemo = true;
    return getDemoData();
  }
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

// ─── Chart defaults ───────────────────────────────────────────────
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 11;

// ─── KPIs ─────────────────────────────────────────────────────────
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

  const kpis = [
    {l:'Balance', v:fmtUsd2(bal), c:bal>=1000?'pos':'neg', s:'Inicio: $1,000'},
    {l:'PnL Neto', v:(pnl>=0?'+':'')+fmtUsd2(pnl), c:pnl>=0?'pos':'neg', s:`Fees: ${fmtUsd2(fees)}`},
    {l:'Win Rate', v:pct(wr), c:wr>=50?'pos':'neg', s:`${wins}W / ${losses}L de ${trades}`},
    {l:'ARB Fill Rate', v:pct(fillRate), c:fillRate>=20?'pos':'neg', s:`${arbFills} arbs de ${totalOrders}`},
    {l:'Experimentos', v:es.total||0, s:`✅${es.kept||0} kept · ❌${es.reverted||0} disc`},
    {l:'Polls', v:D.polls?.length||0, s:isDemo ? '⚡ Demo data' : '🔴 Live cada 30s'},
  ];

  document.getElementById('kpi-grid').innerHTML = kpis.map(k =>
    `<div class="kpi"><div class="label">${k.l}</div><div class="value ${k.c||''}">${k.v}</div><div class="sub">${k.s||''}</div></div>`
  ).join('');
}

// ─── Coin Cards ───────────────────────────────────────────────────
function renderCoinCards(D) {
  const lpc = D.latest_per_coin || {};
  const coins = ['BTC','ETH','SOL','XRP','DOGE'];
  const icons = {BTC:'₿',ETH:'Ξ',SOL:'◎',XRP:'✕',DOGE:'Ð'};

  document.getElementById('coin-cards').innerHTML = coins.map(c => {
    const p = lpc[c];
    if (!p) return `<div class="coin-card"><div class="coin-name"><span class="dot no-arb"></span>${icons[c]} ${c}</div><div class="row" style="justify-content:center;color:var(--muted)">Sin datos</div></div>`;

    const impliedUp = p.yes_mid || 0;
    const impliedDown = p.no_mid || 0;
    const impliedTotal = impliedUp + impliedDown;
    const rawEdge = (1.0 - impliedTotal) * 100;
    const hasEdge = rawEdge > 0.5;

    return `<div class="coin-card">
      <div class="coin-name"><span class="dot ${hasEdge?'arb':'no-arb'}"></span>${icons[c]} ${c} ${hasEdge ? chip('EDGE','green') : ''}</div>
      <div class="row"><span>Implied Up</span><span class="val">${fmt(impliedUp,3)}</span></div>
      <div class="row"><span>Implied Down</span><span class="val">${fmt(impliedDown,3)}</span></div>
      <div class="row"><span>Suma</span><span class="val ${impliedTotal<0.995?'pos':'neg'}">${fmt(impliedTotal,4)}</span></div>
      <div class="row"><span>Edge bruto</span><span class="val ${rawEdge>0?'pos':'neg'}">${rawEdge.toFixed(2)}¢</span></div>
      <div class="row"><span>Spread</span><span class="val">${fmt(p.spread_yes,3)} / ${fmt(p.spread_no,3)}</span></div>
      <div class="row"><span>Depth</span><span class="val">$${fmt(p.depth_yes_usd,0)} / $${fmt(p.depth_no_usd,0)}</span></div>
      <div class="row"><span>Binance</span><span class="val">${p.binance_price ? '$'+Number(p.binance_price).toLocaleString('en',{maximumFractionDigits:2}) : '-'}</span></div>
      <div class="row"><span>Vol 24h</span><span class="val">${pct((p.volatility_1h||0)*100)}</span></div>
    </div>`;
  }).join('');
}

// ─── Experiments ──────────────────────────────────────────────────
function renderExperiments(D) {
  const exps = D.experiments || [];
  const active = exps.find(e => e.status === 'baseline' || e.status === 'running');

  if (active) {
    document.getElementById('phase-badge').textContent = active.status.toUpperCase();
    document.getElementById('phase-badge').className = 'badge badge-' + (active.status==='running'?'blue':'yellow');
    document.getElementById('active-exp').innerHTML = `
      <div class="active-exp">
        <div class="exp-title">Exp #${active.id}: ${active.hypothesis || '?'}</div>
        <div class="exp-detail">Estado: ${chip(active.status, active.status==='running'?'blue':'yellow')} · Inicio: ${ts(active.started_at)}</div>
      </div>`;
  } else {
    document.getElementById('phase-badge').textContent = 'IDLE';
    document.getElementById('phase-badge').className = 'badge badge-blue';
    document.getElementById('active-exp').innerHTML = '<div class="empty-state">⏸️ Ninguno activo - esperando proximo ciclo</div>';
  }

  // LLM Reasoning
  const llmExps = exps.filter(e => e.hypothesis?.includes('[LLM]'));
  if (llmExps.length > 0) {
    document.getElementById('llm-reasoning').textContent = llmExps.map(e =>
      `🧪 Exp #${e.id} (${e.status}):\n   ${e.hypothesis}\n`
    ).join('\n');
  }

  // Table
  const completed = exps.filter(e => e.status !== 'proposed');
  document.getElementById('exp-count').textContent = completed.length;
  document.getElementById('exp-table').innerHTML = completed.length === 0
    ? '<div class="empty-state">Aun no hay experimentos completados</div>'
    : `<table><tr><th>#</th><th>Hipotesis</th><th>RAPR B.</th><th>RAPR T.</th><th>Mejora</th><th>p-val</th><th>Src</th><th>Estado</th></tr>
    ${completed.map(e => {
      const st = e.status === 'completed' ? chip('KEEP','green') :
                 e.status === 'reverted' ? chip('DISC','red') :
                 e.status === 'crashed' ? chip('CRASH','orange') :
                 chip(e.status,'blue');
      const src = e.hypothesis?.includes('[LLM]') ? chip('LLM','cyan') : chip('RNG','yellow');
      const imp = e.improvement_pct || 0;
      return `<tr><td>${e.id}</td><td title="${(e.hypothesis||'').replace(/"/g,'&quot;')}">${(e.hypothesis||'').slice(0,40)}${(e.hypothesis||'').length>40?'...':''}</td>
        <td>${fmt(e.baseline_rapr,6)}</td><td>${fmt(e.test_rapr,6)}</td>
        <td class="${imp>0?'pos':'neg'}">${imp>0?'+':''}${fmt(imp,1)}%</td>
        <td>${fmt(e.p_value,4)}</td><td>${src}</td><td>${st}</td></tr>`;
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
          {label:'Baseline', data:scored.map(e=>e.baseline_rapr), borderColor:'#64748b', borderDash:[5,5], pointRadius:4, tension:.3, borderWidth:2},
          {label:'Test', data:scored.map(e=>e.test_rapr), borderColor:'#06b6d4', backgroundColor:'rgba(6,182,212,.1)', fill:true, pointRadius:5, tension:.3, borderWidth:2.5},
        ]
      },
      options: chartOpts('RAPR Score'),
    });
  }
}

// ─── Trades ───────────────────────────────────────────────────────
function renderTrades(D) {
  const trades = D.trades || [];
  document.getElementById('trade-count').textContent = trades.length;

  document.getElementById('trades-table').innerHTML = trades.length === 0
    ? '<div class="empty-state">Aun no hay trades</div>'
    : `<table><tr><th>#</th><th>Coin</th><th>Size</th><th>Cost</th><th>Fees</th><th>PnL</th><th>Tipo</th><th>Fase</th><th>Hora</th></tr>
    ${trades.slice(0,60).map(t => {
      const type = t.filled === 1 ? chip('ARB','green') :
                   (t.net_pnl && t.net_pnl !== 0) ? chip('PART','yellow') :
                   chip('MISS','red');
      const pnlVal = t.net_pnl || 0;
      return `<tr>
      <td>${t.id}</td><td>${chip(t.coin,'purple')}</td>
      <td>${fmtUsd2(t.size_usd)}</td><td>${fmt(t.total_cost,4)}</td>
      <td>${fmtUsd(t.fees)}</td>
      <td class="${pnlVal>0?'pos':'neg'}">${pnlVal>0?'+':''}${fmtUsd(pnlVal)}</td>
      <td>${type}</td>
      <td>${chip(t.phase||'?','blue')}</td>
      <td>${ts(t.open_at)}</td></tr>`;
    }).join('')}</table>`;

  // PnL chart
  const filled = trades.filter(t => t.filled || (t.net_pnl && t.net_pnl !== 0));
  destroyChart('ch-pnl');
  if (filled.length > 0) {
    const recent = [...filled].reverse().slice(-30);
    charts['ch-pnl'] = new Chart(document.getElementById('ch-pnl'), {
      type:'bar', data:{labels:recent.map(t=>t.coin+' #'+t.id),
      datasets:[{label:'PnL',data:recent.map(t=>t.net_pnl||0),
        backgroundColor:recent.map(t=>(t.net_pnl||0)>0?'rgba(34,197,94,.7)':'rgba(239,68,68,.6)'),
        borderRadius:4, borderSkipped:false}]},
      options:{...chartOpts(),plugins:{legend:{display:false}},scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b',callback:v=>'$'+v.toFixed(4)}},x:{grid:{display:false},ticks:{color:'#475569',font:{size:8},maxRotation:45}}}},
    });
  }

  // Equity curve
  destroyChart('ch-equity');
  const port = (D.portfolio||[]).slice().reverse();
  if (port.length > 0) {
    charts['ch-equity'] = new Chart(document.getElementById('ch-equity'), {
      type:'line', data:{labels:port.map((_,i)=>i),
      datasets:[{label:'Balance ($)',data:port.map(p=>p.balance_usd),
        borderColor:'#06b6d4',backgroundColor:'rgba(6,182,212,.08)',fill:true,tension:.4,
        pointRadius:1,borderWidth:2.5}]},
      options:{...chartOpts(),scales:{y:{grid:{color:'#1e293b'},ticks:{color:'#64748b',callback:v=>'$'+v.toFixed(2)}},x:{display:false}}},
    });
  }
}

// ─── Fill Analysis ────────────────────────────────────────────────
function renderFills(D) {
  const trades = D.trades || [];
  if (!trades.length) return;

  const arbFills = trades.filter(t => t.filled === 1);
  const partials = trades.filter(t => t.filled === 0 && t.net_pnl && t.net_pnl !== 0);
  const misses = trades.filter(t => !t.filled && (!t.net_pnl || t.net_pnl === 0));

  // Doughnut
  destroyChart('ch-fill-types');
  charts['ch-fill-types'] = new Chart(document.getElementById('ch-fill-types'), {
    type: 'doughnut',
    data: {
      labels: [`ARB Fills (${arbFills.length})`, `Partial (${partials.length})`, `Miss (${misses.length})`],
      datasets: [{
        data: [arbFills.length, partials.length, misses.length],
        backgroundColor: ['rgba(34,197,94,.75)', 'rgba(234,179,8,.75)', 'rgba(239,68,68,.5)'],
        borderColor: ['#22c55e', '#eab308', '#ef4444'],
        borderWidth: 2, hoverOffset: 8,
      }]
    },
    options: {responsive:true,maintainAspectRatio:false,cutout:'60%',
      plugins:{legend:{position:'bottom',labels:{color:'#e2e8f0',font:{size:11},padding:12}}}},
  });

  // By coin stacked bar
  const byCoin = {};
  trades.forEach(t => {
    if (!byCoin[t.coin]) byCoin[t.coin] = {arb:0, partial:0, miss:0};
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
        {label:'Miss',data:coins.map(c=>byCoin[c].miss),backgroundColor:'rgba(239,68,68,.4)',borderRadius:3},
      ]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#e2e8f0',font:{size:11}}}},
        scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,grid:{color:'#1e293b'}}}},
    });
  }

  // Stats
  const arbPnl = arbFills.reduce((s,t) => s + (t.net_pnl||0), 0);
  const partPnl = partials.reduce((s,t) => s + (t.net_pnl||0), 0);
  const fr = trades.length ? (arbFills.length/trades.length*100) : 0;

  document.getElementById('fill-stats').innerHTML = `<table>
    <tr><td>Total ordenes</td><td><strong>${trades.length}</strong></td></tr>
    <tr><td>${chip('ARB','green')} fills (ambos lados)</td><td class="pos"><strong>${arbFills.length}</strong> (${pct(fr)})</td></tr>
    <tr><td>${chip('PART','yellow')} fills (un lado)</td><td>${partials.length}</td></tr>
    <tr><td>${chip('MISS','red')} (ninguno)</td><td>${misses.length}</td></tr>
    <tr><td colspan="2" style="border-top:2px solid var(--border)"></td></tr>
    <tr><td>PnL ARB total</td><td class="${arbPnl>=0?'pos':'neg'}"><strong>${fmtUsd2(arbPnl)}</strong></td></tr>
    <tr><td>PnL Partial total</td><td class="${partPnl>=0?'pos':'neg'}">${fmtUsd2(partPnl)}</td></tr>
    <tr><td>Avg PnL por ARB</td><td class="pos">${arbFills.length ? fmtUsd(arbPnl/arbFills.length) : '-'}</td></tr>
    <tr><td>Avg PnL por Partial</td><td class="${partPnl>=0?'pos':'neg'}">${partials.length ? fmtUsd(partPnl/partials.length) : '-'}</td></tr>
  </table>`;
}

// ─── Performance ──────────────────────────────────────────────────
function renderPerformance(D) {
  const trades = (D.trades||[]).filter(t=>t.filled || (t.net_pnl && t.net_pnl!==0));

  const byCoin = {};
  trades.forEach(t => {
    if (!byCoin[t.coin]) byCoin[t.coin] = {pnl:0,count:0,fees:0};
    byCoin[t.coin].pnl += t.net_pnl || 0;
    byCoin[t.coin].count++;
    byCoin[t.coin].fees += t.fees || 0;
  });
  const coins = Object.keys(byCoin).sort();

  destroyChart('ch-coin-pnl');
  if (coins.length) {
    charts['ch-coin-pnl'] = new Chart(document.getElementById('ch-coin-pnl'), {
      type:'bar',data:{labels:coins,
      datasets:[{label:'PnL',data:coins.map(c=>byCoin[c].pnl),
        backgroundColor:coins.map(c=>byCoin[c].pnl>=0?'rgba(34,197,94,.7)':'rgba(239,68,68,.6)'),
        borderRadius:6,borderSkipped:false}]},
      options:{...chartOpts(),plugins:{legend:{display:false}},
        scales:{y:{grid:{color:'#1e293b'},ticks:{callback:v=>'$'+v.toFixed(4)}},x:{grid:{display:false}}}},
    });
  }

  // Edge distribution
  destroyChart('ch-edges');
  const edges = (D.trades||[]).map(t=>t.total_cost&&t.total_cost>0?(1-t.total_cost)*100:null).filter(e=>e!=null);
  if (edges.length) {
    const buckets = {};
    edges.forEach(e => { const k=Math.round(e*2)/2; buckets[k.toFixed(1)+'¢']=(buckets[k.toFixed(1)+'¢']||0)+1; });
    const labels = Object.keys(buckets).sort((a,b)=>parseFloat(a)-parseFloat(b));
    charts['ch-edges'] = new Chart(document.getElementById('ch-edges'), {
      type:'bar',data:{labels,datasets:[{label:'Trades',data:labels.map(l=>buckets[l]),
        backgroundColor:'rgba(168,85,247,.6)',borderRadius:4}]},
      options:{...chartOpts(),plugins:{legend:{display:false}},
        scales:{y:{grid:{color:'#1e293b'}},x:{grid:{display:false}}}},
    });
  }

  // Cost breakdown
  const totalPnl = trades.reduce((s,t)=>s+(t.net_pnl||0),0);
  const totalFees = trades.reduce((s,t)=>s+(t.fees||0),0);
  const grossProfit = totalPnl + totalFees;
  document.getElementById('cost-breakdown').innerHTML = `<table>
    <tr><td>Profit bruto estimado</td><td class="pos">${fmtUsd2(grossProfit)}</td></tr>
    <tr><td>Fees Polymarket + Gas</td><td class="neg">-${fmtUsd2(totalFees)}</td></tr>
    <tr><td style="border-top:2px solid var(--border)"><strong>Profit neto</strong></td><td style="border-top:2px solid var(--border)" class="${totalPnl>=0?'pos':'neg'}"><strong>${fmtUsd2(totalPnl)}</strong></td></tr>
    <tr><td>Trades ejecutados</td><td>${trades.length}</td></tr>
    <tr><td>PnL medio por trade</td><td>${trades.length?fmtUsd(totalPnl/trades.length):'-'}</td></tr>
    <tr><td>Fee medio por trade</td><td>${trades.length?fmtUsd(totalFees/trades.length):'-'}</td></tr>
  </table>`;
}

// ─── Strategy / Research ──────────────────────────────────────────
function renderStrategy(D) {
  document.getElementById('results-tsv').textContent = D.results_tsv || 'No hay resultados todavia. Los experimentos aparecen aqui despues de completarse.';
  document.getElementById('strategy-code').textContent = D.strategy_code || 'strategy.py no disponible en modo estatico.';
}

// ─── Chart options helper ─────────────────────────────────────────
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

// ─── Main refresh ─────────────────────────────────────────────────
async function refresh() {
  const D = await fetchData();
  if (!D) return;

  document.getElementById('update-time').textContent = (isDemo ? '⚡ Demo' : '🔴 Live') + ' · ' + ts(D.generated_at);
  document.getElementById('status-badge').textContent = isDemo ? 'DEMO' : 'LIVE';
  document.getElementById('status-badge').className = 'badge ' + (isDemo ? 'badge-yellow' : 'badge-green');

  renderKPIs(D);
  renderCoinCards(D);
  renderExperiments(D);
  renderTrades(D);
  renderFills(D);
  renderPerformance(D);
  renderStrategy(D);
}

// ─── Demo data ────────────────────────────────────────────────────
function getDemoData() {
  const now = new Date().toISOString();
  const coins = ['BTC','ETH','SOL','XRP','DOGE'];
  const prices = {BTC:84250,ETH:1920,SOL:130,XRP:2.15,DOGE:0.168};

  const polls = coins.map(c => ({
    coin:c, yes_mid:0.495+Math.random()*0.02, no_mid:0.495+Math.random()*0.02,
    spread_yes:0.01+Math.random()*0.03, spread_no:0.01+Math.random()*0.03,
    depth_yes_usd:Math.random()*800, depth_no_usd:Math.random()*800,
    binance_price:prices[c], volatility_1h:0.02+Math.random()*0.04,
    yes_ask:0.99, no_ask:0.99, total_ask:1.98, gap:-0.98,
  }));

  const lpc = {};
  polls.forEach(p => lpc[p.coin] = p);

  const tradeTypes = ['ARB','PARTIAL','MISS'];
  const trades = [];
  for (let i=1; i<=35; i++) {
    const type = tradeTypes[Math.floor(Math.random()*3)];
    const filled = type==='ARB' ? 1 : 0;
    const pnl = type==='ARB' ? 0.005+Math.random()*0.015 : type==='PARTIAL' ? (Math.random()>0.5?0.02:-0.03) : 0;
    trades.push({
      id:i, coin:coins[i%5], size_usd:5, total_cost:0.96+Math.random()*0.03,
      fees:0.01+Math.random()*0.005, net_pnl:pnl, filled, slippage:0,
      phase:i%3===0?'test':'baseline', reason:type,
      open_at:new Date(Date.now()-i*120000).toISOString(),
    });
  }

  const experiments = [
    {id:1,hypothesis:'[LLM] Change BID_SPREAD from 2.0 to 1.5 | Lower spread = more fills, market has enough vol',status:'completed',baseline_rapr:0.0012,test_rapr:0.0018,improvement_pct:50,p_value:0.042,started_at:now},
    {id:2,hypothesis:'[LLM] Change MIN_EDGE_CENTS from 0.5 to 0.3 | Accept thinner trades for more volume',status:'reverted',baseline_rapr:0.0018,test_rapr:0.0008,improvement_pct:-55.6,p_value:0.12,started_at:now},
    {id:3,hypothesis:'[LLM] Change ORDER_SIZE_USD from 5 to 10 | Double size with proven edge',status:'completed',baseline_rapr:0.0018,test_rapr:0.0025,improvement_pct:38.9,p_value:0.067,started_at:now},
    {id:4,hypothesis:'[RANDOM] Change ASYMMETRY from 0.0 to 1.0',status:'reverted',baseline_rapr:0.0025,test_rapr:0.0015,improvement_pct:-40,p_value:0.23,started_at:now},
  ];

  const portfolio = [{balance_usd:1003.47,total_pnl:3.47,total_trades:35,total_fees:0.42,winning_trades:18,losing_trades:17,timestamp:now}];

  return {
    generated_at:now, polls, latest_per_coin:lpc, trades, portfolio, experiments,
    experiment_stats:{total:4,kept:2,reverted:2,crashed:0},
    strategy_code:'# Demo mode - strategy.py\nMAX_TOTAL_COST = 0.98\nBID_SPREAD = 1.5\nMIN_EDGE_CENTS = 0.5\nORDER_SIZE_USD = 10.0\nMAX_ORDERS_PER_POLL = 2\nMIN_SECS_LEFT = 30\nASYMMETRY = 0.0',
    results_tsv:'experiment\trapr\tp_value\timprovement\tstatus\thypothesis\n1\t0.001800\t0.0420\t+50.0%\tkeep\t[LLM] BID_SPREAD 2.0->1.5\n2\t0.000800\t0.1200\t-55.6%\tdiscard\t[LLM] MIN_EDGE 0.5->0.3\n3\t0.002500\t0.0670\t+38.9%\tkeep\t[LLM] ORDER_SIZE 5->10\n4\t0.001500\t0.2300\t-40.0%\tdiscard\t[RANDOM] ASYMMETRY 0->1',
  };
}

// ─── Init ─────────────────────────────────────────────────────────
refresh();
setInterval(refresh, 15000);
