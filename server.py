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


def get_live_data() -> dict:
    """Fetch latest data directly from DB for the API."""
    conn = get_db()

    # Latest polls (last 100)
    polls = [dict(r) for r in conn.execute(
        "SELECT * FROM polls ORDER BY id DESC LIMIT 100"
    ).fetchall()]

    # ALL trades (full history for dashboard)
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades ORDER BY id DESC"
    ).fetchall()]

    # Portfolio history (full for equity curve)
    portfolio = [dict(r) for r in conn.execute(
        "SELECT * FROM portfolio ORDER BY id DESC"
    ).fetchall()]

    # ALL experiments
    experiments = [dict(r) for r in conn.execute(
        "SELECT * FROM experiments ORDER BY id DESC"
    ).fetchall()]

    # Markets
    markets = [dict(r) for r in conn.execute(
        "SELECT * FROM markets WHERE active=1"
    ).fetchall()]

    # Latest poll per coin (for live cards)
    latest_per_coin = {}
    for p in polls:
        if p["coin"] not in latest_per_coin:
            latest_per_coin[p["coin"]] = p

    # Strategy code
    strategy_code = ""
    try:
        with open(STRATEGY_FILE, "r", encoding="utf-8") as f:
            strategy_code = f.read()
    except Exception:
        pass

    # Results TSV
    results_tsv = ""
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            results_tsv = f.read()
    except Exception:
        pass

    # Stats
    exp_stats = {"total": 0, "kept": 0, "reverted": 0, "crashed": 0}
    for row in conn.execute(
        "SELECT status, COUNT(*) as cnt FROM experiments GROUP BY status"
    ).fetchall():
        if row["status"] == "completed":
            exp_stats["kept"] = row["cnt"]
        elif row["status"] == "reverted":
            exp_stats["reverted"] = row["cnt"]
        elif row["status"] == "crashed":
            exp_stats["crashed"] = row["cnt"]
        exp_stats["total"] += row["cnt"]

    conn.close()

    return {
        "generated_at": datetime.now().isoformat(),
        "polls": polls[:50],  # Limit for JSON size
        "latest_per_coin": latest_per_coin,
        "trades": trades,
        "portfolio": portfolio,
        "experiments": experiments,
        "markets": markets,
        "experiment_stats": exp_stats,
        "strategy_code": strategy_code,
        "results_tsv": results_tsv,
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
