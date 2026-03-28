"""
AutoResearch Polymarket - Scorer & Statistical Evaluator
RAPR metric + Welch's t-test for experiment comparison.
"""

import math
from datetime import datetime


def calculate_rapr(trades: list, hours: float) -> float:
    """
    Risk-Adjusted Profit Rate.
    Single metric that captures: profitability, consistency, and execution quality.

    RAPR = net_pnl_per_hour * consistency * fill_rate

    - net_pnl_per_hour: total net P&L divided by hours elapsed
    - consistency: min(|mean_pnl| / std_pnl, 3.0) - Sharpe-like, capped
    - fill_rate: fraction of trades that actually filled
    """
    if not trades or hours <= 0:
        return 0.0

    filled_trades = [t for t in trades if t.get("filled", True)]
    all_pnls = [t.get("net_pnl", 0) for t in filled_trades]

    if not all_pnls:
        return 0.0

    # Net PnL per hour
    total_pnl = sum(all_pnls)
    net_pnl_per_hour = total_pnl / hours

    # Consistency (Sharpe-like ratio, capped at 3.0)
    mean_pnl = total_pnl / len(all_pnls)
    if len(all_pnls) > 1:
        variance = sum((p - mean_pnl) ** 2 for p in all_pnls) / (len(all_pnls) - 1)
        std_pnl = math.sqrt(variance) if variance > 0 else 0.001
    else:
        std_pnl = 0.001
    consistency = min(abs(mean_pnl) / std_pnl, 3.0) if std_pnl > 0 else 1.0

    # Fill rate
    fill_rate = len(filled_trades) / len(trades) if trades else 0

    rapr = net_pnl_per_hour * consistency * fill_rate
    return rapr


def welch_ttest(sample1: list, sample2: list) -> tuple:
    """
    Welch's t-test for two samples with potentially unequal variances.
    Returns (t_statistic, p_value).
    No scipy dependency - pure Python implementation.
    """
    n1, n2 = len(sample1), len(sample2)
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0

    mean1 = sum(sample1) / n1
    mean2 = sum(sample2) / n2

    var1 = sum((x - mean1) ** 2 for x in sample1) / (n1 - 1)
    var2 = sum((x - mean2) ** 2 for x in sample2) / (n2 - 1)

    se = math.sqrt(var1 / n1 + var2 / n2)
    if se == 0:
        return 0.0, 1.0

    t_stat = (mean2 - mean1) / se  # Positive = sample2 is better

    # Welch-Satterthwaite degrees of freedom
    num = (var1 / n1 + var2 / n2) ** 2
    den = ((var1 / n1) ** 2 / (n1 - 1) + (var2 / n2) ** 2 / (n2 - 1))
    df = num / den if den > 0 else 1

    # Approximate p-value using normal distribution (good for df > 10)
    # For smaller df, this is conservative (p will be slightly too large)
    p_value = _normal_sf(abs(t_stat)) * 2  # Two-tailed
    return t_stat, p_value


def _normal_sf(x: float) -> float:
    """Survival function of standard normal (1 - CDF). Approximation."""
    # Abramowitz & Stegun approximation
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989422804014327  # 1/sqrt(2*pi)
    p = d * math.exp(-x * x / 2) * (
        t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
        t * (-1.821255978 + t * 1.330274429))))
    )
    return p if x >= 0 else 1 - p


def compare_experiments(baseline_trades: list, test_trades: list,
                        baseline_hours: float, test_hours: float) -> dict:
    """
    Compare baseline vs test experiment using Welch's t-test + RAPR.

    Returns:
        {
            "result": "improved" | "no_improvement" | "confirm_needed" | "insufficient_data",
            "keep": bool | None,
            "rapr_baseline": float,
            "rapr_test": float,
            "improvement_pct": float,
            "p_value": float,
            "t_stat": float,
            "baseline_summary": dict,
            "test_summary": dict,
        }
    """
    b_filled = [t for t in baseline_trades if t.get("filled", True)]
    t_filled = [t for t in test_trades if t.get("filled", True)]

    b_pnls = [t.get("net_pnl", 0) for t in b_filled]
    t_pnls = [t.get("net_pnl", 0) for t in t_filled]

    rapr_baseline = calculate_rapr(baseline_trades, baseline_hours)
    rapr_test = calculate_rapr(test_trades, test_hours)

    result = {
        "rapr_baseline": rapr_baseline,
        "rapr_test": rapr_test,
        "baseline_trades": len(b_filled),
        "test_trades": len(t_filled),
        "baseline_pnl": sum(b_pnls),
        "test_pnl": sum(t_pnls),
        "baseline_summary": _summarize(b_pnls),
        "test_summary": _summarize(t_pnls),
    }

    # Minimum data threshold
    min_trades = 5
    if len(b_pnls) < min_trades or len(t_pnls) < min_trades:
        # Not enough trades - use simple PnL comparison as fallback
        if len(t_pnls) == 0 and len(b_pnls) == 0:
            result.update({"result": "insufficient_data", "keep": False,
                           "p_value": 1.0, "t_stat": 0, "improvement_pct": 0})
            return result

        # With few trades, just compare totals
        improvement = 0
        if rapr_baseline != 0:
            improvement = (rapr_test - rapr_baseline) / abs(rapr_baseline) * 100
        elif rapr_test > 0:
            improvement = 100

        keep = improvement > 10  # Need >10% with low data
        result.update({"result": "improved" if keep else "no_improvement",
                       "keep": keep, "p_value": 0.5, "t_stat": 0,
                       "improvement_pct": improvement})
        return result

    # Welch's t-test
    t_stat, p_value = welch_ttest(b_pnls, t_pnls)

    # RAPR improvement
    denom = max(abs(rapr_baseline), 0.001)
    improvement_pct = (rapr_test - rapr_baseline) / denom * 100

    result["p_value"] = p_value
    result["t_stat"] = t_stat
    result["improvement_pct"] = improvement_pct

    # Decision logic
    if improvement_pct > 5 and p_value < 0.10:
        if improvement_pct > 30 and p_value < 0.01:
            # Suspiciously good - needs confirmation
            result["result"] = "confirm_needed"
            result["keep"] = None
        else:
            result["result"] = "improved"
            result["keep"] = True
    elif improvement_pct > 0 and p_value < 0.05:
        # Marginal but statistically significant
        result["result"] = "improved"
        result["keep"] = True
    else:
        result["result"] = "no_improvement"
        result["keep"] = False

    return result


def _summarize(pnls: list) -> dict:
    """Quick summary stats for a list of PnLs."""
    if not pnls:
        return {"count": 0, "total": 0, "mean": 0, "std": 0, "min": 0, "max": 0}
    n = len(pnls)
    total = sum(pnls)
    mean = total / n
    variance = sum((p - mean) ** 2 for p in pnls) / max(n - 1, 1)
    return {
        "count": n,
        "total": round(total, 6),
        "mean": round(mean, 6),
        "std": round(math.sqrt(variance), 6),
        "min": round(min(pnls), 6),
        "max": round(max(pnls), 6),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n * 100, 1) if n > 0 else 0,
    }


def format_comparison(result: dict) -> str:
    """Human-readable comparison report."""
    lines = []
    lines.append(f"=== Experiment Evaluation ===")
    lines.append(f"Result: {result['result'].upper()}")
    lines.append(f"Keep: {result.get('keep', '?')}")
    lines.append("")
    lines.append(f"RAPR  baseline={result['rapr_baseline']:.6f}  test={result['rapr_test']:.6f}")
    lines.append(f"Improvement: {result.get('improvement_pct', 0):+.1f}%")
    lines.append(f"p-value: {result.get('p_value', 1):.4f}")
    lines.append(f"t-stat: {result.get('t_stat', 0):+.3f}")
    lines.append("")

    for label, key in [("Baseline", "baseline_summary"), ("Test", "test_summary")]:
        s = result.get(key, {})
        lines.append(f"{label}: {s.get('count', 0)} trades, "
                      f"PnL=${s.get('total', 0):.4f}, "
                      f"mean=${s.get('mean', 0):.4f}, "
                      f"std=${s.get('std', 0):.4f}, "
                      f"WR={s.get('win_rate', 0):.0f}%")

    return "\n".join(lines)
