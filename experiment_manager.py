"""
AutoResearch Polymarket - Experiment Manager
Manages the full lifecycle: propose -> baseline -> test -> evaluate -> keep/discard.
Stores strategy code versions, handles hot-reload and git operations.
"""

import os
import hashlib
import importlib
import json
import shutil
import subprocess
import time
from datetime import datetime

from db import get_db
from scorer import compare_experiments, calculate_rapr, format_comparison

STRATEGY_PATH = os.path.join(os.path.dirname(__file__), "strategy.py")
STRATEGY_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "strategy_default.py")
VERSIONS_DIR = os.path.join(os.path.dirname(__file__), "data", "strategy_versions")
RESULTS_TSV = os.path.join(os.path.dirname(__file__), "results.tsv")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [EXP] {msg}")


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()[:12]


def _read_strategy_code() -> str:
    with open(STRATEGY_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _write_strategy_code(code: str):
    with open(STRATEGY_PATH, "w", encoding="utf-8") as f:
        f.write(code)


def save_strategy_version(experiment_id: int, description: str = ""):
    """Save current strategy.py as a versioned snapshot."""
    os.makedirs(VERSIONS_DIR, exist_ok=True)
    code = _read_strategy_code()
    code_hash = _hash_code(code)

    # Save file copy
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    version_path = os.path.join(VERSIONS_DIR, f"exp{experiment_id}_{ts}_{code_hash}.py")
    shutil.copy2(STRATEGY_PATH, version_path)

    # Save to DB
    conn = get_db()
    conn.execute("""
        INSERT INTO strategy_versions (experiment_id, code, code_hash, description)
        VALUES (?, ?, ?, ?)
    """, (experiment_id, code, code_hash, description))
    conn.commit()
    conn.close()

    return code_hash


def revert_strategy():
    """Revert strategy.py to the default version."""
    shutil.copy2(STRATEGY_DEFAULT_PATH, STRATEGY_PATH)
    log("Strategy reverted to default")


def reload_strategy():
    """Hot-reload strategy.py module. Returns True if successful."""
    import strategy
    try:
        importlib.reload(strategy)
        # Verify the decide function exists and is callable
        assert hasattr(strategy, "decide") and callable(strategy.decide)
        log("Strategy hot-reloaded successfully")
        return True
    except Exception as e:
        log(f"Strategy reload FAILED: {e}")
        revert_strategy()
        importlib.reload(strategy)
        return False


def git_commit(message: str):
    """Create a git commit with strategy.py and strategy_default.py."""
    try:
        project_dir = os.path.dirname(__file__)
        subprocess.run(["git", "add", "strategy.py", "strategy_default.py"],
                        cwd=project_dir, capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", message], cwd=project_dir,
                        capture_output=True, timeout=10)
        log(f"Git commit: {message[:60]}")
    except Exception as e:
        log(f"Git commit failed: {e}")


def git_reset_last():
    """Revert the last git commit (discard experiment)."""
    try:
        project_dir = os.path.dirname(__file__)
        subprocess.run(["git", "reset", "HEAD~1", "--hard"], cwd=project_dir,
                        capture_output=True, timeout=10)
        log("Git reset: last commit reverted")
    except Exception as e:
        log(f"Git reset failed: {e}")


def init_results_tsv():
    """Initialize results.tsv with header if it doesn't exist."""
    if not os.path.exists(RESULTS_TSV):
        with open(RESULTS_TSV, "w", encoding="utf-8") as f:
            f.write("experiment\trapr\tp_value\timprovement\tstatus\thypothesis\n")


def log_result(experiment_id: int, status: str, result: dict, hypothesis: str = ""):
    """Append a row to results.tsv."""
    init_results_tsv()
    rapr = result.get("rapr_test", 0)
    p_value = result.get("p_value", 1)
    improvement = result.get("improvement_pct", 0)

    with open(RESULTS_TSV, "a", encoding="utf-8") as f:
        f.write(f"{experiment_id}\t{rapr:.6f}\t{p_value:.4f}\t"
                f"{improvement:+.1f}%\t{status}\t{hypothesis}\n")


class ExperimentManager:
    """Manages the experiment lifecycle."""

    def __init__(self):
        self.active_experiment = None
        self._load_active()

    def _load_active(self):
        """Load any active experiment from DB."""
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM experiments WHERE status IN ('baseline','running') LIMIT 1"
        ).fetchone()
        if row:
            self.active_experiment = dict(row)
        conn.close()

    def create_experiment(self, hypothesis: str) -> dict:
        """Create a new experiment proposal."""
        code = _read_strategy_code()
        code_hash = _hash_code(code)

        conn = get_db()
        conn.execute("""
            INSERT INTO experiments (hypothesis, strategy_code, strategy_hash, status)
            VALUES (?, ?, ?, 'proposed')
        """, (hypothesis, code, code_hash))
        conn.commit()

        exp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM experiments WHERE id=?", (exp_id,)).fetchone()
        conn.close()

        exp = dict(row)
        log(f"Experiment #{exp_id} created: {hypothesis[:60]}")
        return exp

    def start_experiment(self, experiment: dict) -> dict:
        """Start an experiment (enter baseline phase)."""
        conn = get_db()
        conn.execute("""
            UPDATE experiments SET status='baseline', started_at=datetime('now')
            WHERE id=?
        """, (experiment["id"],))
        conn.commit()
        conn.close()

        experiment["status"] = "baseline"
        self.active_experiment = experiment
        save_strategy_version(experiment["id"], f"baseline: {experiment['hypothesis']}")
        log(f"Experiment #{experiment['id']} -> BASELINE phase")
        return experiment

    def transition_to_test(self, experiment: dict) -> bool:
        """Transition from baseline to test: hot-reload modified strategy."""
        success = reload_strategy()
        if not success:
            log(f"Experiment #{experiment['id']} CRASHED on reload - aborting")
            self.abort_experiment(experiment, "strategy_crash")
            return False

        conn = get_db()
        conn.execute("UPDATE experiments SET status='running' WHERE id=?",
                      (experiment["id"],))
        conn.commit()
        conn.close()

        experiment["status"] = "running"
        self.active_experiment = experiment
        save_strategy_version(experiment["id"], f"test: {experiment['hypothesis']}")
        git_commit(f"exp-{experiment['id']}: {experiment['hypothesis'][:50]}")
        log(f"Experiment #{experiment['id']} -> TEST phase (strategy reloaded)")
        return True

    def evaluate_experiment(self, experiment: dict,
                            baseline_trades: list, test_trades: list,
                            baseline_hours: float, test_hours: float) -> dict:
        """Evaluate the experiment and decide keep/discard."""
        result = compare_experiments(
            baseline_trades, test_trades, baseline_hours, test_hours
        )

        # Update experiment in DB
        conn = get_db()
        conn.execute("""
            UPDATE experiments SET
                baseline_trades=?, baseline_rapr=?, baseline_pnl=?,
                test_trades=?, test_rapr=?, test_pnl=?,
                p_value=?, improvement_pct=?, result=?,
                status=?, completed_at=datetime('now')
            WHERE id=?
        """, (
            result["baseline_trades"], result["rapr_baseline"], result["baseline_pnl"],
            result["test_trades"], result["rapr_test"], result["test_pnl"],
            result.get("p_value"), result.get("improvement_pct"), result["result"],
            "completed" if result.get("keep") else "reverted",
            experiment["id"],
        ))
        conn.commit()
        conn.close()

        # Log to results.tsv
        status = "keep" if result.get("keep") else "discard"
        if result["result"] == "confirm_needed":
            status = "confirm"
        log_result(experiment["id"], status, result, experiment.get("hypothesis", ""))

        # Print report
        report = format_comparison(result)
        log(f"\n{report}")

        return result

    def finalize(self, experiment: dict, keep: bool):
        """Finalize: keep the strategy change or revert."""
        if keep:
            log(f"Experiment #{experiment['id']}: KEEP - strategy advanced")
            # Update the default to current (so future experiments baseline from here)
            shutil.copy2(STRATEGY_PATH, STRATEGY_DEFAULT_PATH)
            # CRITICAL: commit default so git reset --hard doesn't lose KEPT changes
            git_commit(f"kept-{experiment['id']}: update strategy_default.py")
        else:
            log(f"Experiment #{experiment['id']}: DISCARD - reverting")
            revert_strategy()
            reload_strategy()
            git_reset_last()

        self.active_experiment = None

    def abort_experiment(self, experiment: dict, reason: str):
        """Abort a crashed/failed experiment."""
        conn = get_db()
        conn.execute("""
            UPDATE experiments SET status='crashed', result=?,
                completed_at=datetime('now')
            WHERE id=?
        """, (reason, experiment["id"]))
        conn.commit()
        conn.close()

        log_result(experiment["id"], "crash", {"rapr_test": 0, "p_value": 1,
                   "improvement_pct": 0}, experiment.get("hypothesis", ""))
        revert_strategy()
        reload_strategy()
        self.active_experiment = None
        log(f"Experiment #{experiment['id']} ABORTED: {reason}")

    def get_history(self, limit: int = 20) -> list:
        """Get recent experiment history."""
        conn = get_db()
        rows = conn.execute("""
            SELECT * FROM experiments ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Get experiment statistics."""
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
        kept = conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE status='completed'"
        ).fetchone()[0]
        reverted = conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE status='reverted'"
        ).fetchone()[0]
        crashed = conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE status='crashed'"
        ).fetchone()[0]
        conn.close()
        return {"total": total, "kept": kept, "reverted": reverted, "crashed": crashed}
