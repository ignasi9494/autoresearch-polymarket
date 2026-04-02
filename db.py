"""
AutoResearch Polymarket - Database Setup
SQLite schema for polls, trades, experiments, and portfolio tracking.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "research.db")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # --- Market registry (discovered 5-min markets) ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            condition_id TEXT UNIQUE NOT NULL,
            question TEXT,
            token_id_yes TEXT,
            token_id_no TEXT,
            end_date TEXT,
            discovered_at TEXT DEFAULT (datetime('now')),
            active INTEGER DEFAULT 1
        )
    """)

    # --- Polls: every 30-sec observation ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            condition_id TEXT,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            yes_mid REAL,
            no_mid REAL,
            spread_yes REAL,
            spread_no REAL,
            total_ask REAL,
            total_bid REAL,
            gap REAL,
            depth_yes_usd REAL,
            depth_no_usd REAL,
            binance_price REAL,
            volatility_1h REAL,
            experiment_id INTEGER,
            phase TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        )
    """)

    # --- Paper trades ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER,
            phase TEXT,
            coin TEXT NOT NULL,
            size_usd REAL NOT NULL,
            fill_yes REAL,
            fill_no REAL,
            total_cost REAL,
            fees REAL,
            slippage REAL,
            net_pnl REAL,
            filled INTEGER DEFAULT 1,
            reason TEXT,
            window_end TEXT,
            resolved INTEGER DEFAULT 0,
            resolved_at TEXT,
            open_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # --- 5-min windows ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            condition_id TEXT,
            start_time TEXT,
            end_time TEXT,
            polls_count INTEGER DEFAULT 0,
            trades_count INTEGER DEFAULT 0,
            opportunities_seen INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            resolved INTEGER DEFAULT 0
        )
    """)

    # --- Experiments ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis TEXT NOT NULL,
            strategy_code TEXT,
            strategy_hash TEXT,
            status TEXT DEFAULT 'proposed',
            baseline_trades INTEGER DEFAULT 0,
            baseline_rapr REAL,
            baseline_pnl REAL,
            test_trades INTEGER DEFAULT 0,
            test_rapr REAL,
            test_pnl REAL,
            p_value REAL,
            improvement_pct REAL,
            result TEXT,
            llm_reasoning TEXT,
            mutation_source TEXT DEFAULT 'random',
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # --- Strategy versions (code snapshots) ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS strategy_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER,
            code TEXT NOT NULL,
            code_hash TEXT,
            description TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # --- Portfolio state ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            balance_usd REAL DEFAULT 1000.0,
            total_pnl REAL DEFAULT 0.0,
            total_trades INTEGER DEFAULT 0,
            total_fees REAL DEFAULT 0.0,
            winning_trades INTEGER DEFAULT 0,
            losing_trades INTEGER DEFAULT 0,
            timestamp TEXT DEFAULT (datetime('now'))
        )
    """)

    # Migrate: add sell columns to real_trades (safe if already exist)
    for col, coltype in [("sell_attempted", "INTEGER DEFAULT 0"),
                          ("sell_success", "INTEGER DEFAULT 0"),
                          ("sell_price", "REAL"),
                          ("sell_pnl", "REAL"),
                          ("sell_error", "TEXT")]:
        try:
            c.execute(f"ALTER TABLE real_trades ADD COLUMN {col} {coltype}")
        except Exception:
            pass  # Column already exists

    # Indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_polls_coin_ts ON polls(coin, timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_exp ON trades(experiment_id, phase)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_resolved ON trades(resolved)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_windows_coin ON windows(coin, end_time)")

    # Seed portfolio
    c.execute("SELECT COUNT(*) FROM portfolio")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO portfolio (balance_usd) VALUES (1000.0)")

    conn.commit()
    conn.close()
    print(f"[DB] Initialized: {DB_PATH}")


if __name__ == "__main__":
    init_db()
