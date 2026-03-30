"""
AutoResearch Polymarket - Upload Dashboard Data to Vercel
Exports live data from DB to dashboard/api/data.json and pushes to GitHub.
Vercel auto-deploys on push, so the dashboard updates automatically.

Usage:
  python upload_data.py          # Export + git push
  python upload_data.py --local  # Export only (no git push)
"""

import os
import sys
import json
import subprocess
from datetime import datetime

# Fix Windows encoding
if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_JSON_PATH = os.path.join(PROJECT_DIR, "dashboard", "api", "data.json")


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] [UPLOAD] {msg}")


def export_data() -> dict:
    """Export live data from DB to dict (reuses server.py logic)."""
    sys.path.insert(0, PROJECT_DIR)
    from server import get_live_data
    return get_live_data()


def save_json(data: dict):
    """Save data to dashboard/api/data.json."""
    os.makedirs(os.path.dirname(DATA_JSON_PATH), exist_ok=True)
    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str, indent=1)
    size_kb = os.path.getsize(DATA_JSON_PATH) / 1024
    log(f"Saved {DATA_JSON_PATH} ({size_kb:.1f} KB)")


def git_push():
    """Commit and push dashboard data to GitHub."""
    try:
        # Check if there are actual changes
        result = subprocess.run(
            ["git", "diff", "--stat", "dashboard/api/data.json"],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=10
        )
        if not result.stdout.strip():
            log("No changes to push")
            return False

        subprocess.run(
            ["git", "add", "dashboard/api/data.json"],
            cwd=PROJECT_DIR, capture_output=True, timeout=10
        )

        ts = datetime.now().strftime('%Y-%m-%d %H:%M')
        subprocess.run(
            ["git", "commit", "-m", f"auto: dashboard data update {ts}"],
            cwd=PROJECT_DIR, capture_output=True, timeout=10
        )

        result = subprocess.run(
            ["git", "push"],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            log("Pushed to GitHub -> Vercel will auto-deploy")
            return True
        else:
            log(f"Git push failed: {result.stderr[:200]}")
            return False

    except Exception as e:
        log(f"Git push error: {e}")
        return False


def upload():
    """Full pipeline: export DB -> JSON -> git push."""
    log("Starting data export...")
    try:
        data = export_data()
        save_json(data)

        # Summary
        n_polls = len(data.get("polls", []))
        n_trades = len(data.get("trades", []))
        n_experiments = len(data.get("experiments", []))
        portfolio = data.get("portfolio", [{}])
        balance = portfolio[0].get("balance_usd", 0) if portfolio else 0
        log(f"Data: {n_polls} polls, {n_trades} trades, "
            f"{n_experiments} experiments, balance=${balance:.2f}")

        return data
    except Exception as e:
        log(f"Export failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def upload_and_push():
    """Export and push to GitHub/Vercel."""
    data = upload()
    if data:
        git_push()
    return data


if __name__ == "__main__":
    local_only = "--local" in sys.argv
    if local_only:
        upload()
    else:
        upload_and_push()
