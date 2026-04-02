"""Quick audit: cross-reference on-chain vs DB vs graph."""
import os, requests, json
from db import get_db
from web3 import Web3
from datetime import datetime

# Load env
with open('.env', 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

# ON-CHAIN
w3 = Web3(Web3.HTTPProvider('https://polygon-pokt.nodies.app', request_kwargs={'timeout': 10}))
WALLET = Web3.to_checksum_address(os.environ['WALLET_ADDRESS'])
USDC_E = Web3.to_checksum_address('0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174')
ABI = [{'constant':True,'inputs':[{'name':'account','type':'address'}],
        'name':'balanceOf','outputs':[{'name':'','type':'uint256'}],'type':'function'}]
usdc_c = w3.eth.contract(address=USDC_E, abi=ABI)
wallet_usdc = usdc_c.functions.balanceOf(WALLET).call() / 10**6
pol = float(w3.from_wei(w3.eth.get_balance(WALLET), 'ether'))

# Tokens in Polymarket
CTF = Web3.to_checksum_address('0x4D97DCd97eC945f40cF65F87097ACe5EA0476045')
CTF_ABI = [{'constant':True,'inputs':[{'name':'account','type':'address'},
            {'name':'id','type':'uint256'}],'name':'balanceOf',
            'outputs':[{'name':'','type':'uint256'}],'type':'function'}]
ctf_c = w3.eth.contract(address=CTF, abi=CTF_ABI)
r = requests.get('https://data-api.polymarket.com/positions',
                  params={'user': os.environ['WALLET_ADDRESS']}, timeout=10)
positions = r.json()
tokens_val = 0
for p in positions:
    try:
        bal = ctf_c.functions.balanceOf(WALLET, int(p.get('asset',''))).call() / 10**6
        if bal > 0:
            tokens_val += bal * float(p.get('curPrice', 0) or 0)
    except:
        pass

onchain_total = wallet_usdc + tokens_val

# DB
conn = get_db()
all_rt = conn.execute(
    "SELECT * FROM real_trades WHERE order_id_up NOT LIKE 'dry-%' "
    "AND order_id_up IS NOT NULL AND order_id_up != '' ORDER BY id"
).fetchall()
conn.close()

trades = [dict(t) for t in all_rt]
arbs = [t for t in trades if t['status'] == 'arb_complete']
partials = [t for t in trades if t['status'] == 'partial']
arb_pnl = sum(t.get('net_pnl', 0) or 0 for t in arbs)
partial_pnl = sum(t.get('net_pnl', 0) or 0 for t in partials)
db_pnl = arb_pnl + partial_pnl
starting = 108.32
real_pnl = onchain_total - starting
graph_final = starting + db_pnl
diff = onchain_total - graph_final

print("=" * 70)
print("  AUDITORIA COMPLETA")
print("=" * 70)
print(f"  Wallet USDC.e:      ${wallet_usdc:.2f}")
print(f"  Tokens Polymarket:  ${tokens_val:.2f}")
print(f"  TOTAL ON-CHAIN:     ${onchain_total:.2f}")
print(f"  POL:                {pol:.2f}")
print()
print(f"  Capital inicial:    ${starting}")
print(f"  PnL REAL (on-chain): ${real_pnl:+.2f} ({real_pnl/starting*100:+.1f}% ROI)")
print()
print(f"  Trades reales:      {len(trades)} ({len(arbs)} arbs, {len(partials)} partials)")
print(f"  PnL arbs (DB):      ${arb_pnl:+.4f}")
print(f"  PnL gas partials:   ${partial_pnl:+.4f}")
print(f"  PnL total DB:       ${db_pnl:+.4f}")
print()
print(f"  Grafica muestra:    ${graph_final:.2f}")
print(f"  On-chain real:      ${onchain_total:.2f}")
print(f"  DIFERENCIA:         ${diff:+.2f}")
print()

if abs(diff) > 0.5:
    print(f"  >>> LA GRAFICA ESTA MAL POR ${diff:+.2f}")
    print(f"  >>> Los partials generan PnL real cuando resuelven")
    print(f"  >>> pero el bot solo registra -$0.005 gas.")
    print(f"  >>> Los ${diff:+.2f} extra son ganancias de partials")
    print(f"  >>> que resolvieron a favor.")
else:
    print(f"  >>> Datos correctos (diferencia < $0.50)")

# Per hour
print()
print("  EVOLUCION POR HORA:")
by_hour = {}
for t in trades:
    h = (t.get('created_at', '') or '')[:13]
    if not h:
        continue
    if h not in by_hour:
        by_hour[h] = {'a': 0, 'p': 0, 'n': 0, 'pnl': 0}
    by_hour[h]['n'] += 1
    by_hour[h]['pnl'] += t.get('net_pnl', 0) or 0
    if t['status'] == 'arb_complete':
        by_hour[h]['a'] += 1
    elif t['status'] == 'partial':
        by_hour[h]['p'] += 1

cum = 0
for h in sorted(by_hour.keys()):
    d = by_hour[h]
    cum += d['pnl']
    fr = d['a'] / d['n'] * 100 if d['n'] > 0 else 0
    bar = "+" * int(max(0, d['pnl']) * 10)
    print(f"  {h} | {d['a']:>2d}arb {d['p']:>2d}part /{d['n']:>2d} | "
          f"fill={fr:>3.0f}% | pnl=${d['pnl']:+.3f} cum=${cum:+.3f} {bar}")

if arbs:
    avg = arb_pnl / len(arbs)
    print(f"\n  Avg arb profit: ${avg:.4f}")
    print(f"  Fill rate: {len(arbs)/len(trades)*100:.0f}%")
