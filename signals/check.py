"""
Signal detection: run the strategy on a bar window and detect signals
that opened on the most recently CLOSED bar.

The trick: we run the full deterministic backtest over the recent window
(e.g., last 600 bars). All weekly/daily latches reconstruct from history,
so we don't need to persist them. Then we check if any trade has its
entry_time on the last closed bar -> that's a fresh signal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import pandas as pd

from backtest.strategy import Params, backtest


def signal_dict(ticker: str, trade, fired_at: datetime, reason: str = "cascade") -> dict:
    return {
        "ticker":      ticker,
        "side":        trade.side,
        "bar_time":    trade.entry_time.isoformat() if hasattr(trade.entry_time, "isoformat") else str(trade.entry_time),
        "fired_at":    fired_at.isoformat(),
        "entry_price": float(trade.entry_price),
        "sl":          float(trade.sl),
        "tp1":         float(trade.tp1),
        "tp2":         float(trade.tp2),
        "size":        float(trade.size),
        "r_multiple":  float(trade.r_multiple),
        "reason":      reason,
    }


def detect_signals_on_last_closed(
    ticker: str,
    df: pd.DataFrame,
    params: Params,
    closed_bar_age_bars: int = 1,
) -> Iterator[dict]:
    """Yield signal dicts for any trade whose entry_time matches the
    last-fully-closed bar (i.e., df.index[-1 - closed_bar_age_bars]).

    `closed_bar_age_bars` defaults to 1: the very last bar in df might be
    incomplete (live), so we treat df.index[-2] as the most recently
    closed bar. Set to 0 if you trust the last bar is closed.
    """
    if df is None or len(df) < 200:
        return
    target_bar_idx = -1 - closed_bar_age_bars
    if abs(target_bar_idx) > len(df):
        return
    target_bar_time = df.index[target_bar_idx]

    trades, _ = backtest(df, params)
    fired_at = datetime.now(timezone.utc)

    for t in trades:
        if t.entry_time == target_bar_time:
            yield signal_dict(ticker, t, fired_at)
