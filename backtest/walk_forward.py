"""
Walk-forward parameter optimizer for the Weekly Manipulation strategy.

For every (train, test) window:
  1. Grid-search the param space on the training segment.
  2. Pick the combo that maximises `metric` on training (subject to a
     minimum trade count to avoid lucky-1-trade outliers).
  3. Apply that combo to the test segment and record OOS stats.
  4. Slide forward by `step_days` and repeat.

Aggregate OOS performance across windows tells you whether the strategy
generalises out-of-sample, not just in-sample.

CLI:
    python -m backtest.walk_forward
    python -m backtest.walk_forward --tickers EURUSD=X GC=F BTC-USD
    python -m backtest.walk_forward --train-days 270 --test-days 60 --step-days 60
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from dataclasses import asdict
from datetime import timedelta
from itertools import product

import numpy as np
import pandas as pd

from backtest.strategy import Params, backtest, summarise

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)


# ---------------------------------------------------------------------------
# Default search grid - small on purpose to keep WF compute reasonable.
# ---------------------------------------------------------------------------
DEFAULT_GRID = {
    "zone_pct":  [0.10, 0.15, 0.20],
    "rr_tp2":    [1.5, 2.0, 3.0],
    "climax_mx": [1.5, 1.75, 2.0],
}

DEFAULT_TICKERS = [
    "EURUSD=X", "USDJPY=X",
    "ES=F", "NQ=F",
    "GC=F", "SI=F",
    "BTC-USD", "ETH-USD",
]


# ---------------------------------------------------------------------------
# Grid search on a single window
# ---------------------------------------------------------------------------
def grid_search(
    df: pd.DataFrame,
    base: Params,
    grid: dict,
    metric: str = "total_return_pct",
    min_trades: int = 5,
) -> tuple[dict | None, list[dict]]:
    keys = list(grid.keys())
    rows: list[dict] = []
    best: tuple[float, dict] | None = None

    for combo in product(*[grid[k] for k in keys]):
        kw = {**asdict(base), **dict(zip(keys, combo))}
        p = Params(**kw)
        try:
            trades, eq = backtest(df, p)
        except Exception as exc:                     # noqa: BLE001
            print(f"  ! {combo}: {exc}", file=sys.stderr)
            continue
        s = summarise(trades, eq, p)
        s.update(dict(zip(keys, combo)))
        rows.append(s)
        if s["trades"] < min_trades:
            continue
        score = s[metric]
        if score is None or (isinstance(score, float) and np.isnan(score)):
            continue
        if best is None or score > best[0]:
            best = (score, s)

    return (None if best is None else best[1]), rows


# ---------------------------------------------------------------------------
# Walk-forward driver for a single ticker
# ---------------------------------------------------------------------------
def walk_forward_ticker(
    df: pd.DataFrame,
    base: Params,
    grid: dict,
    train_days: int = 360,
    test_days: int = 90,
    step_days: int = 90,
    metric: str = "total_return_pct",
    min_trades: int = 5,
) -> list[dict]:
    if df.empty:
        return []
    keys = list(grid.keys())
    start = df.index[0]
    end   = df.index[-1]

    cur = start
    out: list[dict] = []
    while cur + timedelta(days=train_days + test_days) <= end:
        train_end = cur + timedelta(days=train_days)
        test_end  = train_end + timedelta(days=test_days)
        df_train  = df.loc[cur:train_end]
        df_test   = df.loc[train_end:test_end]
        if len(df_train) < 200 or len(df_test) < 50:
            cur += timedelta(days=step_days)
            continue

        best, _ = grid_search(df_train, base, grid, metric, min_trades)
        if best is None:
            cur += timedelta(days=step_days)
            continue

        chosen = {k: best[k] for k in keys}
        kw = {**asdict(base), **chosen}
        p_test = Params(**kw)
        trades_oos, eq_oos = backtest(df_test, p_test)
        s_oos = summarise(trades_oos, eq_oos, p_test)
        s_oos.update({
            "train_start": cur.date().isoformat(),
            "train_end":   train_end.date().isoformat(),
            "test_end":    test_end.date().isoformat(),
            "is_return_pct": best["total_return_pct"],
            "is_trades":     best["trades"],
            "is_win_rate":   best["win_rate"],
        })
        s_oos.update(chosen)
        out.append(s_oos)
        cur += timedelta(days=step_days)

    return out


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------
def _compound(returns_pct: list[float]) -> float:
    """Compound a list of period-returns (in pct) into a total pct return."""
    if not returns_pct:
        return 0.0
    eq = 1.0
    for r in returns_pct:
        eq *= (1.0 + r / 100.0)
    return (eq - 1.0) * 100.0


def aggregate(per_ticker: dict[str, list[dict]]) -> pd.DataFrame:
    rows = []
    for tkr, windows in per_ticker.items():
        if not windows:
            continue
        wdf = pd.DataFrame(windows)
        oos_returns = wdf["total_return_pct"].tolist()
        compound_oos = _compound(oos_returns)
        rows.append({
            "ticker":            tkr,
            "windows":           len(windows),
            "oos_trades":        int(wdf["trades"].sum()),
            "oos_win_rate":      float(
                (wdf["win_rate"] * wdf["trades"]).sum()
                / max(wdf["trades"].sum(), 1)),
            "oos_compound_pct":  compound_oos,
            "oos_mean_pct":      float(wdf["total_return_pct"].mean()),
            "oos_median_pct":    float(wdf["total_return_pct"].median()),
            "oos_worst_dd_pct":  float(wdf["max_dd_pct"].min()),
            "is_mean_pct":       float(wdf["is_return_pct"].mean()),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    from backtest.run import fetch  # local import to avoid yfinance at import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers",     nargs="+", default=DEFAULT_TICKERS)
    ap.add_argument("--interval",    default="1h")
    ap.add_argument("--period",      default="720d")
    ap.add_argument("--train-days",  type=int, default=360)
    ap.add_argument("--test-days",   type=int, default=90)
    ap.add_argument("--step-days",   type=int, default=90)
    ap.add_argument("--risk-pct",    type=float, default=0.5)
    ap.add_argument("--no-ny",       action="store_true")
    ap.add_argument("--metric",      default="total_return_pct",
                    choices=["total_return_pct", "profit_factor", "avg_R"])
    ap.add_argument("--min-trades",  type=int, default=5)
    ap.add_argument("--out-csv",     default=None,
                    help="optional CSV path for the per-window detail table")
    ap.add_argument("--summary-csv", default=None,
                    help="optional CSV path for the per-ticker aggregate")
    args = ap.parse_args()

    base = Params(
        risk_pct=args.risk_pct / 100.0,
        use_ny=not args.no_ny,
    )

    print(f"\nWalk-forward optimization "
          f"(grid={sum(1 for _ in product(*DEFAULT_GRID.values()))} combos, "
          f"train={args.train_days}d, test={args.test_days}d, "
          f"step={args.step_days}d, metric={args.metric})")
    print("-" * 86)

    all_rows: list[dict] = []
    per_ticker: dict[str, list[dict]] = {}
    t0 = time.time()
    for tkr in args.tickers:
        print(f"  {tkr:<10s}", end="", flush=True)
        df = fetch(tkr, args.interval, args.period)
        if df is None or len(df) < 500:
            print(f"  skipped (n={0 if df is None else len(df)})")
            continue
        ts = time.time()
        windows = walk_forward_ticker(
            df, base, DEFAULT_GRID,
            train_days=args.train_days, test_days=args.test_days,
            step_days=args.step_days,
            metric=args.metric, min_trades=args.min_trades,
        )
        elapsed = time.time() - ts
        if not windows:
            print(f"  no usable windows ({elapsed:.1f}s)")
            continue
        for w in windows:
            w["ticker"] = tkr
            all_rows.append(w)
        per_ticker[tkr] = windows

        wdf = pd.DataFrame(windows)
        compound = _compound(wdf["total_return_pct"].tolist())
        print(f"  {len(windows)} windows | "
              f"OOS compound {compound:+.2f}% | "
              f"OOS mean {wdf['total_return_pct'].mean():+.2f}% | "
              f"trades {int(wdf['trades'].sum())} | "
              f"{elapsed:.1f}s")

    if not all_rows:
        print("\nno results")
        return 1

    detail = pd.DataFrame(all_rows)
    detail = detail[[
        "ticker", "train_start", "train_end", "test_end",
        "zone_pct", "rr_tp2", "climax_mx",
        "is_trades", "is_win_rate", "is_return_pct",
        "trades", "win_rate", "total_return_pct", "max_dd_pct",
        "profit_factor",
    ]]
    pd.options.display.float_format = "{:.3f}".format

    print(f"\n  total elapsed: {time.time() - t0:.1f}s")

    summary = aggregate(per_ticker)
    print("\nPer-ticker OOS summary")
    print("=" * 86)
    print(summary.to_string(index=False))

    print("\nAggregate OOS")
    print("-" * 86)
    print(f"  instruments       : {len(summary)}")
    print(f"  OOS windows total : {int(summary['windows'].sum())}")
    print(f"  OOS trades total  : {int(summary['oos_trades'].sum())}")
    print(f"  weighted win rate : "
          f"{(summary['oos_win_rate'] * summary['oos_trades']).sum() / max(summary['oos_trades'].sum(), 1):.1%}")
    print(f"  mean compound OOS : {summary['oos_compound_pct'].mean():+.2f}%")
    print(f"  median compound   : {summary['oos_compound_pct'].median():+.2f}%")
    print(f"  IS->OOS gap (mean): "
          f"{(summary['is_mean_pct'] - summary['oos_mean_pct']).mean():+.2f}%")

    if args.out_csv:
        detail.to_csv(args.out_csv, index=False)
        print(f"\n  wrote per-window detail -> {args.out_csv}")
    if args.summary_csv:
        summary.to_csv(args.summary_csv, index=False)
        print(f"  wrote per-ticker summary -> {args.summary_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
