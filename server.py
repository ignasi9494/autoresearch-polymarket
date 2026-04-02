"""
AutoResearch Polymarket - Dashboard Server
Simple HTTP server that serves the dashboard and provides JSON API.
Run: python server.py
Open: http://localhost:8080
"""

import os
import json
import http.server
import socketserver
from datetime import datetime
from db import get_db

PORT = 8080
DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "dashboard")
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "dashboard_data.json")
STRATEGY_FILE = os.path.join(os.path.dirname(__file__), "strategy.py")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.tsv")


def get_onchain_balance() -> dict:
    """Fetch real on-chain USDC.e and POL balances."""
    try:
        from web3 import Web3
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        wallet = ""
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("WALLET_ADDRESS="):
                        wallet = line.strip().split("=", 1)[1]
        if not wallet:
            return {}
        w3 = Web3(Web3.HTTPProvider("https://polygon-pokt.nodies.app",
                                     request_kwargs={"timeout": 5}))
        if not w3.is_connected():
            return {}
        wallet = Web3.to_checksum_address(wallet)
        USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
        ABI = [{"constant": True, "inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                "type": "function"}]
        usdc = w3.eth.contract(address=USDC_E, abi=ABI)
        return {
            "usdc_e": usdc.functions.balanceOf(wallet).call() / 10**6,
            "pol": float(w3.from_wei(w3.eth.get_balance(wallet), "ether")),
        }
    except Exception:
        return {}


def get_env_config() -> dict:
    """Read trading mode from .env."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    config = {"trading_mode": "paper", "dry_run": True}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TRADING_MODE="):
                    config["trading_mode"] = line.split("=", 1)[1]
                elif line.startswith("DRY_RUN="):
                    config["dry_run"] = line.split("=", 1)[1].lower() == "true"
    return config


STARTING_BALANCE = 108.32


def get_live_data() -> dict:
    """Fetch latest data directly from DB for the API.
    Only returns VERIFIED real trades (no dry-run, no errors)."""
    conn = get_db()

    # Real trades: ONLY with real order IDs (source of truth)
    real_trades = []
    try:
        real_trades = [dict(r) for r in conn.execute("""
            SELECT * FROM real_trades
            WHERE order_id_up IS NOT NULL AND order_id_up != ''
                AND order_id_up NOT LIKE 'dry-%'
            ORDER BY id
        """).fetchall()]
    except Exception:
        pass

    # Calculate portfolio from real trades (NOT from portfolio table which can be stale)
    arbs = [t for t in real_trades if t.get("status") == "arb_complete"]
    partials = [t for t in real_trades if t.get("status") == "partial"]
    total_pnl = sum(t.get("net_pnl", 0) or 0 for t in real_trades)
    total_fees = sum(t.get("fees", 0) or 0 for t in real_trades)
    portfolio = {
        "balance_usd": STARTING_BALANCE + total_pnl,
        "total_pnl": total_pnl,
        "total_trades": len(arbs) + len(partials),
        "total_fees": total_fees,
        "winning_trades": len(arbs),
        "losing_trades": len(partials),
        "starting_balance": STARTING_BALANCE,
    }

    # Latest polls
    polls = [dict(r) for r in conn.execute(
        "SELECT * FROM polls ORDER BY id DESC LIMIT 50"
    ).fetchall()]
    latest_per_coin = {}
    for p in polls:
        if p["coin"] not in latest_per_coin:
            latest_per_coin[p["coin"]] = p

    # Legacy trades (for old dashboard compat)
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE reason LIKE 'REAL:%' ORDER BY id DESC LIMIT 200"
    ).fetchall()]

    conn.close()

    # On-chain balance (best-effort)
    onchain = get_onchain_balance()

    # Env config
    config = get_env_config()

    return {
        "generated_at": datetime.now().isoformat(),
        "trading_mode": config["trading_mode"],
        "dry_run": config["dry_run"],
        "real_trades": real_trades,
        "onchain": onchain,
        "portfolio": portfolio,
        "latest_per_coin": latest_per_coin,
        "trades": trades,
        "polls": polls[:20],
    }


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def do_GET(self):
        try:
            if self.path == "/api/data" or self.path.startswith("/api/data?"):
                data = get_live_data()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data, default=str).encode("utf-8"))
            else:
                super().do_GET()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # Browser closed connection, normal behavior
        except Exception as e:
            try:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Error: {e}".encode())
            except Exception:
                pass

    def log_message(self, format, *args):
        pass  # Suppress access logs


if __name__ == "__main__":
    print(f"Dashboard server starting on http://localhost:{PORT}")
    print(f"Serving files from: {DASHBOARD_DIR}")
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
