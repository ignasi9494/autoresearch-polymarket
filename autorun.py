"""
AutoResearch Polymarket - Autonomous Session Runner
====================================================
Arranca automaticamente a las 15:30 CEST, corre hasta las 00:00 CEST.
Lanza dashboard, ejecuta experimentos, logea todo, genera resumen.

Uso:
  python autorun.py              # Espera hasta 15:30 y arranca
  python autorun.py --now        # Arranca inmediatamente
  python autorun.py --test       # Verifica todo y sale
  python autorun.py --until 23   # Arranca ahora, para a las 23:00
"""

import sys
import os
import io
import time
import subprocess
import signal
import argparse
from datetime import datetime, timedelta

# Fix Windows encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_DIR, "data")

# ─── Session config ──────────────────────────────────────────────────────

START_HOUR = 15       # 15:30 CEST = 9:30 AM ET
START_MINUTE = 30
STOP_HOUR = 0         # 00:00 CEST = 6:00 PM ET (midnight)
PHASE_MINS = 60       # 60 min per phase -> ~4 experiments in 8.5h (statistically robust)
COOLDOWN_MINS = 5     # 5 min cooldown between experiments


class TeeLogger:
    """Write to both console and log file."""
    def __init__(self, log_path):
        self.terminal = sys.stdout
        self.log = open(log_path, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        self.log.close()


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def wait_until_start():
    """Wait until START_HOUR:START_MINUTE."""
    now = datetime.now()
    target = now.replace(hour=START_HOUR, minute=START_MINUTE, second=0, microsecond=0)

    # If we're past today's start time, it means we should start now
    if now >= target:
        log(f"Ya son las {now.strftime('%H:%M')} - arrancando inmediatamente")
        return

    wait_secs = (target - now).total_seconds()
    hours = int(wait_secs // 3600)
    mins = int((wait_secs % 3600) // 60)

    log(f"Esperando hasta las {START_HOUR}:{START_MINUTE:02d}...")
    log(f"Faltan {hours}h {mins}min ({wait_secs:.0f} segundos)")
    log(f"Dashboard estara en http://localhost:8080 cuando arranque")
    log("")

    # Wait with periodic status updates
    while True:
        now = datetime.now()
        if now >= target:
            break
        remaining = (target - now).total_seconds()
        if remaining > 300:
            # Update every 5 min
            log(f"  ... {remaining/60:.0f} min restantes")
            time.sleep(min(300, remaining))
        else:
            time.sleep(min(30, remaining))

    log(f">> HORA DE ARRANCAR: {datetime.now().strftime('%H:%M:%S')}")


def should_stop(stop_hour):
    """Check if we should stop (past stop_hour)."""
    now = datetime.now()
    if stop_hour == 0:
        # Midnight: stop if hour >= 23:55 or hour == 0
        return now.hour == 0 or (now.hour == 23 and now.minute >= 55)
    return now.hour >= stop_hour


def start_dashboard():
    """Start dashboard server as background process."""
    log("Arrancando dashboard server en background...")
    server_script = os.path.join(PROJECT_DIR, "server.py")
    proc = subprocess.Popen(
        [sys.executable, server_script],
        cwd=PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
    )
    time.sleep(2)
    if proc.poll() is None:
        log(f"Dashboard OK: http://localhost:8080 (PID: {proc.pid})")
    else:
        log("WARN: Dashboard puede no haber arrancado")
    return proc


def clean_start():
    """Fresh DB for new session."""
    log("Limpiando DB para sesion nueva...")
    db_path = os.path.join(PROJECT_DIR, "data", "research.db")
    for ext in ["", "-wal", "-shm"]:
        p = db_path + ext
        if os.path.exists(p):
            os.remove(p)

    # Re-init
    from db import init_db
    init_db()

    # Restore default strategy
    import shutil
    src = os.path.join(PROJECT_DIR, "strategy_default.py")
    dst = os.path.join(PROJECT_DIR, "strategy.py")
    shutil.copy2(src, dst)
    log("DB limpia, strategy.py restaurado a default")


def run_session(stop_hour, phase_mins, cooldown_mins):
    """Run the full autonomous experiment session."""
    import market_fetcher
    from paper_trader import RealisticPaperTrader
    from experiment_manager import (
        ExperimentManager, reload_strategy, revert_strategy,
        save_strategy_version, init_results_tsv, STRATEGY_PATH
    )
    from scorer import calculate_rapr, format_comparison
    from orchestrator import run_phase, export_dashboard_data, _propose_mutation
    import orchestrator

    # Override phase duration
    orchestrator.PHASE_DURATION_MINS = phase_mins
    orchestrator.COOLDOWN_MINS = cooldown_mins

    init_results_tsv()

    # Discover markets
    log("\n[INIT] Buscando mercados...")
    market_fetcher._market_cache = {}
    market_fetcher._cache_ts = 0
    markets = market_fetcher.discover_markets()
    if not markets:
        log("[FATAL] No se encontraron mercados! Reintentando en 5 min...")
        time.sleep(300)
        markets = market_fetcher.discover_markets()
        if not markets:
            log("[FATAL] Sigue sin mercados. Abortando.")
            return

    log(f"Mercados: {list(markets.keys())}")

    # Save markets to DB
    from db import get_db
    conn = get_db()
    for coin, mkt in markets.items():
        conn.execute("""
            INSERT OR REPLACE INTO markets (coin, condition_id, question,
                token_id_yes, token_id_no, end_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (coin, mkt["condition_id"], mkt["question"],
              mkt["token_yes"], mkt["token_no"], mkt["end_date"]))
    conn.commit()
    conn.close()

    trader = RealisticPaperTrader()
    manager = ExperimentManager()

    log(f"Balance inicial: ${trader.balance:.2f}")
    log(f"Fases de {phase_mins} min | Cooldown {cooldown_mins} min")
    log(f"Parando a las {stop_hour:02d}:00")

    # Quick observe
    log(f"\n[OBSERVE] Verificando datos (2 min)...")
    run_phase("observe", 2, trader)
    export_dashboard_data()

    # Main loop
    session_start = datetime.now()
    experiment_num = 0

    log("\n" + "=" * 60)
    log("  SESION AUTONOMA INICIADA")
    log(f"  Hora: {session_start.strftime('%H:%M:%S')}")
    log(f"  Hasta: {stop_hour:02d}:00")
    log(f"  Fases: {phase_mins} min | ~{int(8.5*60 / (phase_mins*2 + cooldown_mins))} experimentos estimados")
    log("=" * 60)

    while not should_stop(stop_hour):
        experiment_num += 1
        exp_start = datetime.now()

        log(f"\n{'#' * 60}")
        log(f"  EXPERIMENTO #{experiment_num}")
        log(f"  {exp_start.strftime('%H:%M:%S')} | "
            f"Sesion: {(exp_start - session_start).seconds // 60} min")
        log(f"{'#' * 60}")

        try:
            # Refresh markets (they rotate every 5 min)
            market_fetcher._market_cache = {}
            market_fetcher._cache_ts = 0

            # Step 1: BASELINE
            log(f"\n[1/5] BASELINE ({phase_mins} min)...")
            baseline_trades = run_phase(
                "baseline", phase_mins, trader,
                experiment_id=experiment_num
            )

            if should_stop(stop_hour):
                log("Hora de parar - no empezamos test")
                break

            # Step 2: MUTATE
            hypothesis = _propose_mutation(experiment_num)
            exp = manager.create_experiment(hypothesis)
            manager.start_experiment(exp)

            # Step 3: TEST
            log(f"\n[3/5] TEST ({phase_mins} min)...")
            success = manager.transition_to_test(exp)
            if not success:
                log("Strategy crasheo - saltando al siguiente")
                time.sleep(60)
                continue

            test_trades = run_phase(
                "test", phase_mins, trader,
                experiment_id=experiment_num
            )

            # Step 4: EVALUATE
            log(f"\n[4/5] Evaluando experimento #{experiment_num}...")
            hours = phase_mins / 60
            result = manager.evaluate_experiment(
                exp, baseline_trades, test_trades, hours, hours
            )

            # Step 5: DECIDE
            keep = result.get("keep", False)

            if result["result"] == "confirm_needed" and not should_stop(stop_hour):
                log(f"\n[CONFIRM] Resultado muy bueno - confirmando...")
                confirm_trades = run_phase(
                    "confirm", phase_mins, trader,
                    experiment_id=experiment_num
                )
                confirm_result = manager.evaluate_experiment(
                    exp, baseline_trades, confirm_trades, hours, hours
                )
                keep = confirm_result.get("keep", False)

            manager.finalize(exp, keep)

            status = "KEPT" if keep else "DISCARDED"
            log(f"\n  >>> Experimento #{experiment_num}: {status}")
            log(f"  >>> RAPR: {result['rapr_baseline']:.6f} -> {result['rapr_test']:.6f} "
                f"({result.get('improvement_pct', 0):+.1f}%)")
            log(f"  >>> p-value: {result.get('p_value', 1):.4f}")

            # Export dashboard
            export_dashboard_data()

            # Portfolio update
            port = trader.get_portfolio_summary()
            log(f"  >>> Balance: ${port['balance']:.2f} | "
                f"PnL: ${port['total_pnl']:+.4f} | "
                f"Trades: {port['total_trades']} | "
                f"WR: {port['win_rate']:.0f}%")

            # Cooldown
            if not should_stop(stop_hour):
                log(f"\n[COOLDOWN] {cooldown_mins} min...")
                time.sleep(cooldown_mins * 60)

        except KeyboardInterrupt:
            log("\n[STOP] Interrumpido por usuario (Ctrl+C)")
            export_dashboard_data()
            break
        except Exception as e:
            log(f"\n[ERROR] Experimento #{experiment_num}: {e}")
            import traceback
            traceback.print_exc()
            revert_strategy()
            reload_strategy()
            time.sleep(60)

    # ─── Session Summary ───────────────────────────────────────────
    session_end = datetime.now()
    session_mins = (session_end - session_start).total_seconds() / 60
    stats = manager.get_stats()
    port = trader.get_portfolio_summary()

    log("\n" + "=" * 60)
    log("  RESUMEN DE SESION")
    log("=" * 60)
    log(f"  Duracion: {session_mins:.0f} min ({session_mins/60:.1f} horas)")
    log(f"  Experimentos: {stats['total']}")
    log(f"    Kept:     {stats['kept']}")
    log(f"    Discard:  {stats['reverted']}")
    log(f"    Crashed:  {stats['crashed']}")
    log(f"  Balance:    ${port['balance']:.2f}")
    log(f"  PnL total:  ${port['total_pnl']:+.4f}")
    log(f"  Trades:     {port['total_trades']}")
    log(f"  Win Rate:   {port['win_rate']:.1f}%")
    log(f"  Fees total: ${port['total_fees']:.4f}")

    # Best experiment
    from db import get_db
    conn = get_db()
    best = conn.execute("""
        SELECT id, hypothesis, test_rapr, improvement_pct
        FROM experiments WHERE status='completed'
        ORDER BY test_rapr DESC LIMIT 1
    """).fetchone()
    if best:
        log(f"\n  Mejor experimento: #{best['id']}")
        log(f"    Hipotesis: {best['hypothesis']}")
        log(f"    RAPR: {best['test_rapr']:.6f} ({best['improvement_pct']:+.1f}%)")

    # Results TSV
    results_path = os.path.join(PROJECT_DIR, "results.tsv")
    if os.path.exists(results_path):
        log(f"\n  results.tsv:")
        with open(results_path, "r") as f:
            for line in f:
                log(f"    {line.rstrip()}")

    log("\n" + "=" * 60)
    log(f"  Sesion finalizada: {session_end.strftime('%H:%M:%S')}")
    log("=" * 60)

    # Final dashboard export
    export_dashboard_data()


def run_test():
    """Quick verification that everything works."""
    log("=== TEST MODE ===")

    # DB
    from db import init_db
    init_db()
    log("[OK] DB inicializada")

    # APIs
    import market_fetcher
    market_fetcher._market_cache = {}
    market_fetcher._cache_ts = 0
    markets = market_fetcher.discover_markets()
    log(f"[OK] Mercados: {list(markets.keys())} ({len(markets)}/5)")

    # Strategy
    import strategy
    assert hasattr(strategy, "decide"), "strategy.decide no encontrado"
    log("[OK] strategy.py valido")

    # Paper trader
    from paper_trader import RealisticPaperTrader
    t = RealisticPaperTrader()
    log(f"[OK] Paper trader (balance: ${t.balance:.2f})")

    # Scorer
    from scorer import calculate_rapr, welch_ttest
    rapr = calculate_rapr([{"net_pnl": 0.05, "filled": True}], 0.5)
    log(f"[OK] Scorer (RAPR test: {rapr:.4f})")

    # Mutation
    from orchestrator import _propose_mutation
    h = _propose_mutation(999)
    log(f"[OK] Mutacion: {h}")

    # Revert
    import shutil
    shutil.copy2(
        os.path.join(PROJECT_DIR, "strategy_default.py"),
        os.path.join(PROJECT_DIR, "strategy.py")
    )
    log("[OK] Strategy revertido")

    log("\n=== TODOS LOS TESTS OK ===")
    log(f"Sistema listo para arrancar a las {START_HOUR}:{START_MINUTE:02d}")


def main():
    parser = argparse.ArgumentParser(description="AutoResearch - Session Runner")
    parser.add_argument("--now", action="store_true", help="Start immediately")
    parser.add_argument("--test", action="store_true", help="Run tests only")
    parser.add_argument("--until", type=int, default=STOP_HOUR,
                        help=f"Stop hour (default: {STOP_HOUR})")
    parser.add_argument("--phase", type=int, default=PHASE_MINS,
                        help=f"Phase duration in minutes (default: {PHASE_MINS})")
    parser.add_argument("--noclean", action="store_true",
                        help="Don't clean DB on start")
    args = parser.parse_args()

    # Setup logging
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"session_{datetime.now().strftime('%Y%m%d_%H%M')}.log")
    logger = TeeLogger(log_path)
    sys.stdout = logger

    log("=" * 60)
    log("  AUTORESEARCH POLYMARKET - Session Runner")
    log("=" * 60)
    log(f"  Log: {log_path}")
    log(f"  Phase: {args.phase} min | Stop: {args.until:02d}:00")
    log("")

    if args.test:
        run_test()
        return

    # Wait for start time
    if not args.now:
        wait_until_start()

    # Start dashboard
    dashboard_proc = start_dashboard()

    try:
        # Clean DB
        if not args.noclean:
            clean_start()

        # Run session
        run_session(
            stop_hour=args.until,
            phase_mins=args.phase,
            cooldown_mins=COOLDOWN_MINS,
        )
    except KeyboardInterrupt:
        log("\n[STOP] Ctrl+C recibido")
    except Exception as e:
        import traceback
        log(f"\n[FATAL ERROR] {type(e).__name__}: {e}")
        log(traceback.format_exc())
    finally:
        # Kill dashboard
        if dashboard_proc and dashboard_proc.poll() is None:
            log("Parando dashboard server...")
            dashboard_proc.terminate()
            dashboard_proc.wait(timeout=5)

        log("Sesion terminada.")
        logger.close()


if __name__ == "__main__":
    main()
