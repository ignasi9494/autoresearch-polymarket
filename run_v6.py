"""
AutoResearch v6 - Agentic Gemini 3.1 Pro + 20 params + strict evaluator
Switch USE_LLM in strategy.py to toggle between LLM and random.
"""
import os, time, traceback, math, json
from datetime import datetime
os.chdir(r'C:\Proyectos_ignasi\autoresearch_polymarket')

log_f = open('data/session_v6.log', 'w', encoding='utf-8')
def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    try: print(line, flush=True)
    except: pass
    try: log_f.write(line + '\n'); log_f.flush()
    except: pass


def strict_evaluate(baseline_trades, test_trades, hours_b, hours_t):
    """Strict evaluator: min 10 trades, p<0.10, test_pnl>0."""
    b_pnls = [t.get('net_pnl', 0) for t in baseline_trades if t.get('filled', True)]
    t_pnls = [t.get('net_pnl', 0) for t in test_trades if t.get('filled', True)]
    result = {'baseline_trades': len(b_pnls), 'test_trades': len(t_pnls),
              'baseline_pnl': sum(b_pnls), 'test_pnl': sum(t_pnls)}

    if len(b_pnls) < 10 or len(t_pnls) < 10:
        result.update({'result': 'insufficient_data', 'keep': False,
                       'p_value': 1.0, 'improvement_pct': 0, 'rapr_baseline': 0, 'rapr_test': 0})
        return result

    n1, n2 = len(b_pnls), len(t_pnls)
    m1, m2 = sum(b_pnls)/n1, sum(t_pnls)/n2
    v1 = sum((x-m1)**2 for x in b_pnls)/(n1-1)
    v2 = sum((x-m2)**2 for x in t_pnls)/(n2-1)
    se = math.sqrt(v1/n1 + v2/n2) if (v1/n1 + v2/n2) > 0 else 0.001
    t_stat = (m2 - m1) / se
    ax = abs(t_stat)
    t = 1.0 / (1.0 + 0.2316419 * ax)
    p_half = 0.3989422804 * math.exp(-ax*ax/2) * (t*(0.31938+t*(-0.35656+t*(1.78148+t*(-1.82126+t*1.33027)))))
    p_value = min(1.0, max(0.0, p_half * 2 if t_stat >= 0 else (1-p_half)*2))

    def rapr(pnls, hrs):
        if not pnls or hrs <= 0: return 0
        total = sum(pnls); mean = total/len(pnls)
        var = sum((p-mean)**2 for p in pnls)/max(len(pnls)-1,1)
        std = math.sqrt(var) if var > 0 else 0.001
        return (total/hrs) * min(abs(mean)/std, 3.0) * (len([p for p in pnls if p!=0])/len(pnls))

    rb, rt = rapr(b_pnls, hours_b), rapr(t_pnls, hours_t)
    imp = (rt - rb) / max(abs(rb), 0.001) * 100
    result.update({'p_value': p_value, 'improvement_pct': imp, 'rapr_baseline': rb, 'rapr_test': rt})

    if imp > 5 and p_value < 0.10 and sum(t_pnls) > 0:
        result.update({'result': 'improved', 'keep': True})
    else:
        result.update({'result': 'no_improvement', 'keep': False})
    return result


try:
    log('=== SESSION v6: AGENTIC GEMINI 3.1 PRO + 20 PARAMS ===')
    log('LLM: multi-turn chat, decides own steps (1-5), full context each turn')

    from db import init_db, get_db
    import market_fetcher
    from paper_trader import RealisticPaperTrader
    from experiment_manager import (ExperimentManager, reload_strategy,
                                     revert_strategy, init_results_tsv, STRATEGY_PATH)
    from orchestrator import run_phase, export_dashboard_data
    from llm_advisor import agentic_propose, random_propose, apply_mutation, PARAM_SPACE
    import orchestrator, strategy

    PHASE_MINS = 60
    orchestrator.PHASE_DURATION_MINS = PHASE_MINS
    orchestrator.POLL_INTERVAL_SECS = 30

    log(f'Phase: {PHASE_MINS}min | Exp: {PHASE_MINS*2/60:.1f}h | Params: {len(PARAM_SPACE)}')
    init_db(); init_results_tsv()
    trader = RealisticPaperTrader()
    manager = ExperimentManager()
    log(f'Balance: ${trader.balance:.2f} | PnL: ${trader.total_pnl:+.2f}')
    log(f'USE_LLM: {strategy.USE_LLM}')

    market_fetcher._market_cache = {}; market_fetcher._cache_ts = 0
    markets = market_fetcher.discover_markets()
    log(f'Markets: {list(markets.keys())}')
    conn = get_db()
    for coin, mkt in markets.items():
        conn.execute('INSERT OR REPLACE INTO markets (coin, condition_id, question, token_id_yes, token_id_no, end_date) VALUES (?,?,?,?,?,?)',
            (coin, mkt['condition_id'], mkt['question'], mkt['token_up'], mkt['token_down'], mkt['end_date']))
    conn.commit(); conn.close()

    run_phase('observe', 5, trader); export_dashboard_data()
    log('Warmup done')

    for exp_num in range(1, 101):
        log('')
        log(f'########## EXP #{exp_num}/100 (2h) ##########')
        try:
            market_fetcher._market_cache = {}; market_fetcher._cache_ts = 0

            # BASELINE
            log(f'[BASELINE] {PHASE_MINS} min...')
            baseline = run_phase('baseline', PHASE_MINS, trader, experiment_id=exp_num)
            arbs_b = sum(1 for t in baseline if t.get('arb_filled'))
            pnl_b = sum(t.get('net_pnl',0) for t in baseline)
            log(f'[BASELINE] {len(baseline)}tr ARB:{arbs_b} PnL:${pnl_b:+.4f}')

            # MUTATE: LLM or Random based on USE_LLM switch
            import importlib; importlib.reload(strategy)
            use_llm = getattr(strategy, 'USE_LLM', True)

            if use_llm:
                try:
                    log('[LLM] Starting agentic proposal (Gemini 3.1 Pro)...')
                    proposal = agentic_propose(STRATEGY_PATH)
                    source = 'LLM'
                    steps = proposal.get('steps_used', '?')
                    conf = proposal.get('confidence', '?')
                    log(f'[LLM] Done in {steps} steps. Confidence: {conf}')
                except Exception as e:
                    log(f'[LLM] Failed: {e}. Falling back to random.')
                    proposal = random_propose()
                    source = 'RND'
            else:
                proposal = random_propose()
                source = 'RND'

            old_val, new_val, hypothesis = apply_mutation(
                STRATEGY_PATH, proposal['param'], proposal['value'])
            hypothesis = f'[{source}] {hypothesis}'
            if proposal.get('reasoning'):
                hypothesis += f' | {proposal["reasoning"][:120]}'
            log(f'[MUTATION] {hypothesis[:150]}')

            exp = manager.create_experiment(hypothesis)
            manager.start_experiment(exp)
            if not manager.transition_to_test(exp):
                log('[CRASH] Reverting')
                time.sleep(60); continue

            # TEST
            log(f'[TEST] {PHASE_MINS} min...')
            test = run_phase('test', PHASE_MINS, trader, experiment_id=exp_num)
            arbs_t = sum(1 for t in test if t.get('arb_filled'))
            pnl_t = sum(t.get('net_pnl',0) for t in test)
            log(f'[TEST] {len(test)}tr ARB:{arbs_t} PnL:${pnl_t:+.4f}')

            # EVALUATE (strict)
            result = strict_evaluate(baseline, test, PHASE_MINS/60, PHASE_MINS/60)
            keep = result.get('keep', False)

            conn = get_db()
            conn.execute("""
                UPDATE experiments SET
                    baseline_trades=?, baseline_rapr=?, baseline_pnl=?,
                    test_trades=?, test_rapr=?, test_pnl=?,
                    p_value=?, improvement_pct=?, result=?,
                    status=?, completed_at=datetime('now'),
                    mutation_source=?
                WHERE id=?
            """, (result['baseline_trades'], result.get('rapr_baseline',0), result['baseline_pnl'],
                  result['test_trades'], result.get('rapr_test',0), result['test_pnl'],
                  result.get('p_value'), result.get('improvement_pct'), result['result'],
                  'completed' if keep else 'reverted', source.lower(), exp['id']))
            conn.commit(); conn.close()

            manager.finalize(exp, keep)
            port = trader.get_portfolio_summary()
            log(f'>>> {"KEPT" if keep else "DISC"} | Bal:${port["balance"]:.2f} PnL:${port["total_pnl"]:+.2f} WR:{port["win_rate"]:.0f}%')
            log(f'    src={source} p={result.get("p_value",1):.4f} imp={result.get("improvement_pct",0):+.1f}% trades={result["baseline_trades"]}/{result["test_trades"]}')

            # UPLOAD TO VERCEL AFTER EVERY EXPERIMENT
            try:
                export_dashboard_data()
                from upload_data import upload_and_push
                upload_and_push()
                log('[VERCEL] Uploaded')
            except Exception as ue:
                log(f'[VERCEL] {ue}')

            log('[COOLDOWN] 5 min'); time.sleep(300)
        except Exception as e:
            log(f'[ERROR] {e}'); traceback.print_exc()
            revert_strategy(); reload_strategy(); time.sleep(60)

    log('SESSION v6 COMPLETE')
except Exception as e:
    log(f'FATAL: {e}'); traceback.print_exc()
finally:
    try: log_f.close()
    except: pass
