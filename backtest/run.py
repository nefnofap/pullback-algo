"""
Multi-instrument runner for the Weekly Manipulation & Failed Breakout backtest.

Two modes:
  * intraday (default): fetch 1h bars, ~720d max from yfinance.
  * --daily            : fetch 1d bars, supports very long history (use --years).

Examples:
    python -m backtest.run                     # 1h intraday default basket
    python -m backtest.run --tickers EURUSD=X SPY BTC-USD
    python -m backtest.run --daily --years 20  # 20-year daily-bar swing variant
    python -m backtest.run --vs-baseline       # compare new strategy vs baseline
"""
from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from backtest.strategy import Params, backtest, summarise

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)


# ---------------------------------------------------------------------------
# Default baskets
# ---------------------------------------------------------------------------
DEFAULT_TICKERS = [
    # FX majors
    "EURUSD=X", "GBPUSD=X", "AUDUSD=X", "USDJPY=X", "USDCAD=X",
    # Index futures (continuous, ~24h)
    "ES=F", "NQ=F", "YM=F", "RTY=F",
    # Index spot
    "^GSPC", "^NDX", "^DJI", "^FTSE", "^GDAXI", "^N225",
    # Metals (continuous futures)
    "GC=F", "SI=F", "HG=F", "PL=F",
    # Crypto
    "BTC-USD", "ETH-USD",
]

# Wider basket — adds JPY crosses, NZDUSD, energies, more crypto.
# Use with --tickers BIG to opt in.
WIDE_TICKERS = DEFAULT_TICKERS + [
    "NZDUSD=X", "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "EURGBP=X",
    "CL=F", "NG=F", "RB=F",
    "SOL-USD", "XRP-USD", "DOGE-USD",
]

# Curated basket: 15 instruments hand-picked from v2_loose --big results.
# Selected for either high win rate (>= 55%) or strong positive contribution.
# Excludes chronic losers (AUDUSD, USDJPY, EURUSD, JPY crosses, DOGE/XRP).
# Heavy on US equity by design (the data justified it).
CURATED_TICKERS = [
    # US index futures (3) - core trend channel, deep liquidity
    "ES=F", "NQ=F", "YM=F",
    # US cash indices (3) - independent signal sources from futures channel
    "^GSPC", "^NDX", "^DJI",
    # Metals (3) - low correlation with equities; all >= 50% win on v2_loose
    "GC=F", "SI=F", "HG=F",
    # Energies (3) - all >= 50% win on v2_loose; CL was 60%
    "CL=F", "NG=F", "RB=F",
    # Softs (1) - cocoa was 84.6% win / PF 2.74 standalone; replaced GBPUSD
    "CC=F",
    # Crypto (2) - 24/7, decent sample contribution
    "BTC-USD", "SOL-USD",
]

# Curated basket with deep history for the 20-year daily run.
# Picked instruments where yfinance reliably returns 5000+ daily bars.
LONG_HISTORY_TICKERS = [
    # FX majors (yfinance from ~Dec 2003)
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X",
    # US equity ETFs (deep history)
    "SPY", "QQQ", "DIA", "IWM",
    # Cash indices (very deep history)
    "^GSPC", "^NDX", "^DJI", "^FTSE", "^GDAXI", "^N225",
    # Metals
    "GC=F", "SI=F",
    # Crypto (limited history; included anyway for completeness)
    "BTC-USD",
]


def _flatten_yf(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = ["Open", "High", "Low", "Close"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"missing yfinance columns: {missing}")
    out = df[needed].copy()
    if "Volume" in df.columns:
        out["Volume"] = df["Volume"]
    out = out.dropna()
    if out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    return out


def fetch(ticker: str, interval: str, period: str | None = None,
          start: str | None = None, end: str | None = None) -> pd.DataFrame | None:
    import yfinance as yf
    try:
        if start is not None:
            df = yf.download(ticker, interval=interval, start=start, end=end,
                             auto_adjust=False, progress=False, threads=False)
        else:
            df = yf.download(ticker, interval=interval, period=period,
                             auto_adjust=False, progress=False, threads=False)
    except Exception as exc:
        print(f"  ! fetch failed for {ticker}: {exc}", file=sys.stderr)
        return None
    if df is None or df.empty:
        print(f"  ! no data for {ticker}", file=sys.stderr)
        return None
    return _flatten_yf(df)


# ---------------------------------------------------------------------------
# Param presets
# ---------------------------------------------------------------------------
def make_baseline_params(use_ny: bool, daily_mode: bool) -> Params:
    """Original strategy as documented in v1: no HTF trend, no BE move,
    no ATR stop, no ADX, no pin/inside, no day-sweep."""
    p = Params(
        use_ny=use_ny, daily_mode=daily_mode,
        use_htf_trend=False, runner_be=False, use_atr_stop=False,
        use_adx_filter=False, use_pin_bar=False, use_inside_break=False,
        use_day_sweep=False, extended_session=False,
    )
    if daily_mode:
        # On daily bars, value zones rarely sit exactly on a prior level and
        # consolidations span 1-2 weeks rather than hours; relax accordingly.
        p = replace(p, zone_pct=0.30, pat_len=4, brk_exp=10)
    return p


def make_v2_params(use_ny: bool, daily_mode: bool) -> Params:
    """V2 strategy: all Tier 1+2 improvements on, Tier 3 (extended session)
    off by default since it's instrument-class specific."""
    p = Params(
        use_ny=use_ny, daily_mode=daily_mode,
        use_htf_trend=True, runner_be=True, use_atr_stop=True,
        use_adx_filter=True, use_pin_bar=True, use_inside_break=True,
        use_day_sweep=True, extended_session=False,
    )
    if daily_mode:
        p = replace(p, zone_pct=0.30, pat_len=4, brk_exp=10,
                    adx_threshold=18.0)  # daily ADX is lower-volatility
    return p


def make_v2_loose_params(use_ny: bool, daily_mode: bool) -> Params:
    """V2 + Tier 4 sample-size broadeners: loose pattern, prior-week-mid zones,
    momentum + NR-expansion triggers, lower pin-wick threshold."""
    p = make_v2_params(use_ny, daily_mode)
    return replace(p,
                   loose_pattern=True,
                   use_pwm_zone=True,
                   use_momentum_trig=True,
                   use_nr_expansion=True,
                   pin_wick_ratio=0.5)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_table(rows: list[dict], cols: list[str]):
    if not rows:
        print("  (no rows)")
        return
    df = pd.DataFrame(rows)[cols]
    pd.options.display.float_format = "{:.3f}".format
    print(df.to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=None)
    ap.add_argument("--interval", default=None,
                    help="yfinance interval (default: 1h or 1d if --daily)")
    ap.add_argument("--period", default=None,
                    help="yfinance period (default: 720d intraday / overridden by --years)")
    ap.add_argument("--years", type=int, default=None,
                    help="lookback in years (daily mode); overrides --period")
    ap.add_argument("--daily", action="store_true",
                    help="run the daily-bar swing variant of the strategy")
    ap.add_argument("--risk-pct", type=float, default=0.5,
                    help="risk per trade as percent of equity")
    ap.add_argument("--no-ny", action="store_true")
    ap.add_argument("--baseline", action="store_true",
                    help="run the v1 baseline strategy (Tier 1+2 off)")
    ap.add_argument("--vs-baseline", action="store_true",
                    help="run baseline AND v2 in one go and print a delta table")
    ap.add_argument("--loose", action="store_true",
                    help="use v2_loose preset (Tier 4 sample-size broadeners on)")
    ap.add_argument("--three-way", action="store_true",
                    help="run baseline, v2, AND v2_loose for full comparison")
    ap.add_argument("--big", action="store_true",
                    help="use the wider 32-ticker basket (FX crosses + energies + altcoins)")
    ap.add_argument("--curated", action="store_true",
                    help="use the curated 15-ticker basket (positive performers only)")
    ap.add_argument("--out-csv", default=None)
    args = ap.parse_args()

    # ---- defaults ---------------------------------------------------------
    if args.tickers is None:
        if args.daily:
            args.tickers = LONG_HISTORY_TICKERS
        elif args.curated:
            args.tickers = CURATED_TICKERS
        elif args.big:
            args.tickers = WIDE_TICKERS
        else:
            args.tickers = DEFAULT_TICKERS
    if args.interval is None:
        args.interval = "1d" if args.daily else "1h"
    if args.period is None:
        if args.years is not None:
            args.period = f"{args.years * 365 + 30}d"
        else:
            args.period = "720d"

    use_ny     = not args.no_ny
    daily_mode = args.daily

    def _make(preset: str) -> Params:
        if preset == "baseline":
            p = make_baseline_params(use_ny, daily_mode)
        elif preset == "v2_loose":
            p = make_v2_loose_params(use_ny, daily_mode)
        else:
            p = make_v2_params(use_ny, daily_mode)
        return replace(p, risk_pct=args.risk_pct / 100.0)

    presets = []
    if args.three_way:
        presets = [("baseline", _make("baseline")),
                   ("v2",       _make("v2")),
                   ("v2_loose", _make("v2_loose"))]
    elif args.vs_baseline:
        presets = [("baseline", _make("baseline")), ("v2", _make("v2"))]
    elif args.baseline:
        presets = [("baseline", _make("baseline"))]
    elif args.loose:
        presets = [("v2_loose", _make("v2_loose"))]
    else:
        presets = [("v2", _make("v2"))]

    # ---- header -----------------------------------------------------------
    label = "daily-swing" if daily_mode else f"intraday-{args.interval}"
    presets_str = " + ".join(name for name, _ in presets)
    print(f"\nWeekly Manipulation & Failed Breakout - {label} - {presets_str} "
          f"(period={args.period}, NY={'off' if args.no_ny else 'on'})")
    print("-" * 96)

    rows_all: list[dict] = []
    delta_rows: list[dict] = []
    for tkr in args.tickers:
        print(f"  {tkr:<12s}", end="", flush=True)
        df = fetch(tkr, args.interval, args.period)
        if df is None or len(df) < 200:
            print(f"   skipped (n={0 if df is None else len(df)})")
            continue
        print(f" n={len(df):>5d}", end="")

        per_preset: dict[str, dict] = {}
        for name, p in presets:
            try:
                trades, eq = backtest(df, p)
            except Exception as exc:
                print(f"   {name} ERROR: {exc}")
                continue
            stats = summarise(trades, eq, p)
            stats["ticker"] = tkr
            stats["preset"] = name
            stats["bars"]   = len(df)
            stats["start"]  = df.index[0].date().isoformat()
            stats["end"]    = df.index[-1].date().isoformat()
            rows_all.append(stats)
            per_preset[name] = stats
            print(f"   {name}: t={stats['trades']:>3d} w={stats['win_rate']:>5.0%} "
                  f"r={stats['total_return_pct']:>+6.2f}% dd={stats['max_dd_pct']:>+6.2f}%",
                  end="")

        if "baseline" in per_preset and "v2" in per_preset:
            b, v = per_preset["baseline"], per_preset["v2"]
            delta_rows.append({
                "ticker":     tkr,
                "tr_base":    b["trades"],     "tr_v2":  v["trades"],
                "wr_base":    b["win_rate"],   "wr_v2":  v["win_rate"],
                "ret_base":   b["total_return_pct"], "ret_v2": v["total_return_pct"],
                "dd_base":    b["max_dd_pct"], "dd_v2":  v["max_dd_pct"],
                "pf_base":    b["profit_factor"], "pf_v2":  v["profit_factor"],
                "ret_delta":  v["total_return_pct"] - b["total_return_pct"],
            })
        print()

    if not rows_all:
        print("\nno results")
        return 1

    pd.options.display.float_format = "{:.3f}".format

    # Per-preset summary
    for name, _ in presets:
        rows = [r for r in rows_all if r["preset"] == name]
        if not rows:
            continue
        df_sum = pd.DataFrame(rows)
        agg_trades = int(df_sum["trades"].sum())
        win_rates = df_sum["win_rate"].dropna()
        weighted_win = (
            (df_sum["win_rate"].fillna(0) * df_sum["trades"]).sum()
            / max(agg_trades, 1))
        print(f"\n[{name}] aggregate over {len(df_sum)} instruments:")
        print(f"  total trades       : {agg_trades}")
        print(f"  weighted win rate  : {weighted_win:.1%}")
        print(f"  mean total return  : {df_sum['total_return_pct'].mean():+.2f}%")
        print(f"  median total return: {df_sum['total_return_pct'].median():+.2f}%")
        print(f"  worst max DD       : {df_sum['max_dd_pct'].min():+.2f}%")
        print(f"  positive instruments: "
              f"{int((df_sum['total_return_pct'] > 0).sum())}/{len(df_sum)}")

    # Side-by-side delta
    if delta_rows:
        print("\nbaseline -> v2 deltas")
        print("=" * 96)
        df_delta = pd.DataFrame(delta_rows)
        df_delta = df_delta.sort_values("ret_delta", ascending=False)
        cols = ["ticker", "tr_base", "tr_v2", "wr_base", "wr_v2",
                "ret_base", "ret_v2", "ret_delta", "dd_base", "dd_v2",
                "pf_base", "pf_v2"]
        print(df_delta[cols].to_string(index=False))
        # Aggregate delta
        print(f"\n  mean return delta  : {df_delta['ret_delta'].mean():+.2f}%")
        print(f"  median return delta: {df_delta['ret_delta'].median():+.2f}%")
        print(f"  improved tickers   : "
              f"{int((df_delta['ret_delta'] > 0).sum())}/{len(df_delta)}")

    if args.out_csv:
        # write everything for later analysis
        pd.DataFrame(rows_all).to_csv(args.out_csv, index=False)
        print(f"\nwrote {args.out_csv}")
        if delta_rows:
            delta_csv = args.out_csv.replace(".csv", "_delta.csv")
            pd.DataFrame(delta_rows).to_csv(delta_csv, index=False)
            print(f"wrote {delta_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
