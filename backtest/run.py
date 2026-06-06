"""
Multi-instrument runner for the Weekly Manipulation & Failed Breakout backtest.

Uses yfinance for OHLC data. Works on 1H bars (yfinance allows ~730 days of
hourly data, which is the longest practical window for this strategy on the
free tier).

Usage:
    python -m backtest.run                       # default basket, 1h, ~24 months
    python -m backtest.run --tickers EURUSD=X SPY BTC-USD
    python -m backtest.run --interval 1h --period 730d
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

from backtest.strategy import Params, backtest, summarise

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)


DEFAULT_TICKERS = [
    # FX majors
    "EURUSD=X", "GBPUSD=X", "AUDUSD=X", "USDJPY=X", "USDCAD=X",
    # Index futures (continuous, ~24h)
    "ES=F", "NQ=F", "YM=F", "RTY=F",
    # Index spot
    "^GSPC", "^NDX", "^DJI", "^FTSE", "^GDAXI", "^N225",
    # Metals (continuous futures)
    "GC=F", "SI=F", "HG=F", "PL=F",
    # Crypto (24/7 stress test)
    "BTC-USD", "ETH-USD",
]


def _flatten_yf(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance sometimes returns multi-index columns; flatten to single."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = ["Open", "High", "Low", "Close"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns from yfinance: {missing}")
    out = df[needed].copy()
    if "Volume" in df.columns:
        out["Volume"] = df["Volume"]
    out = out.dropna()
    if out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    return out


def fetch(ticker: str, interval: str, period: str) -> pd.DataFrame | None:
    import yfinance as yf
    try:
        df = yf.download(
            ticker, interval=interval, period=period,
            auto_adjust=False, progress=False, threads=False,
        )
    except Exception as exc:
        print(f"  ! fetch failed for {ticker}: {exc}", file=sys.stderr)
        return None
    if df is None or df.empty:
        print(f"  ! no data for {ticker}", file=sys.stderr)
        return None
    return _flatten_yf(df)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    ap.add_argument("--interval", default="1h",
                    help="yfinance interval: 1h (730d max), 15m (60d max), etc.")
    ap.add_argument("--period",   default="720d",
                    help="yfinance period: 720d, 60d, 7d, max, ...")
    ap.add_argument("--risk-pct", type=float, default=0.5,
                    help="risk per trade as percent of equity")
    ap.add_argument("--no-ny", action="store_true",
                    help="disable NY-session filter (for FX/crypto 24h tests)")
    ap.add_argument("--out-csv", default=None,
                    help="optional CSV path for the summary table")
    args = ap.parse_args()

    params = Params(
        risk_pct=args.risk_pct / 100.0,
        use_ny=not args.no_ny,
    )

    rows = []
    print(f"\nWeekly Manipulation & Failed Breakout - backtest "
          f"({args.interval} bars, {args.period}, "
          f"NY-filter={'off' if args.no_ny else 'on'})")
    print("-" * 86)

    for tkr in args.tickers:
        print(f"  fetching {tkr} ...", end="", flush=True)
        df = fetch(tkr, args.interval, args.period)
        if df is None or len(df) < 200:
            print(f" skipped (insufficient data, n={0 if df is None else len(df)})")
            continue
        try:
            trades, equity = backtest(df, params)
        except Exception as exc:
            print(f" ERROR: {exc}")
            continue

        stats = summarise(trades, equity, params)
        stats["ticker"]   = tkr
        stats["bars"]     = len(df)
        stats["start"]    = df.index[0].date().isoformat()
        stats["end"]      = df.index[-1].date().isoformat()
        rows.append(stats)
        print(f" {len(df):>5} bars | trades={stats['trades']:>3} "
              f"win={stats['win_rate']:.0%} ret={stats['total_return_pct']:+.2f}% "
              f"DD={stats['max_dd_pct']:+.2f}%")

    if not rows:
        print("\nno results")
        return 1

    df_sum = pd.DataFrame(rows)
    df_sum = df_sum[[
        "ticker", "bars", "start", "end",
        "trades", "win_rate", "avg_R",
        "total_return_pct", "max_dd_pct", "profit_factor",
        "final_equity",
    ]]

    # pretty print
    pd.options.display.float_format = "{:.3f}".format
    print("\nResults")
    print("=" * 86)
    print(df_sum.to_string(index=False))

    # aggregate
    agg_trades = int(df_sum["trades"].sum())
    weighted_win = (
        (df_sum["win_rate"] * df_sum["trades"]).sum() / max(agg_trades, 1)
    )
    print("\nAggregate")
    print("-" * 86)
    print(f"  instruments        : {len(df_sum)}")
    print(f"  total trades       : {agg_trades}")
    print(f"  weighted win rate  : {weighted_win:.1%}")
    print(f"  mean total return  : {df_sum['total_return_pct'].mean():+.2f}%")
    print(f"  median total return: {df_sum['total_return_pct'].median():+.2f}%")
    print(f"  worst max DD       : {df_sum['max_dd_pct'].min():+.2f}%")

    if args.out_csv:
        df_sum.to_csv(args.out_csv, index=False)
        print(f"\nwrote {args.out_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
