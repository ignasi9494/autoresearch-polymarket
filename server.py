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


def get_live_data() -> dict:
    """Fetch latest data directly from DB for the API."""
    conn = get_db()

    # Real trades (SOURCE OF TRUTH for the new dashboard)
    real_trades = []
    try:
        real_trades = [dict(r) for r in conn.execute(
            "SELECT * FROM real_trades ORDER BY id"
        ).fetchall()]
    except Exception:
        pass

    # Portfolio (latest snapshot)
    portfolio_row = conn.execute(
        "SELECT * FROM portfolio ORDER BY id DESC LIMIT 1"
    ).fetchone()
    portfolio = dict(portfolio_row) if portfolio_row else {}

    # Latest polls (for market cards)
    polls = [dict(r) for r in conn.execute(
        "SELECT * FROM polls ORDER BY id DESC LIMIT 50"
    ).fetchall()]

    latest_per_coin = {}
    for p in polls:
        if p["coin"] not in latest_per_coin:
            latest_per_coin[p["coin"]] = p

    # Legacy: trades table (for backwards compat with old dashboard)
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT 500"
    ).fetchall()]

    # Experiments
    experiments = [dict(r) for r in conn.execute(
        "SELECT * FROM experiments ORDER BY id DESC LIMIT 50"
    ).fetchall()]

    conn.close()

    # On-chain balance (best-effort, won't fail)
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
        "experiments": experiments,
    }


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/data":
            try:
                data = get_live_data()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data, default=str).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Error: {e}".encode())
        else:
            super().do_GET()

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
