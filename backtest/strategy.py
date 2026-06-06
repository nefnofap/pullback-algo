"""
Weekly Manipulation & Failed Breakout - event-driven backtest engine.

Mirrors `pine/weekly_manipulation_strategy.pine`:
  - Levels    : PDH / PDL / PDC, prev-week H/L, ADR, climax-day filter
  - Context   : Mon/Tue fakeout state machine, Break-in-Structure
  - Setups    : Sell-High at PDH zone, Buy-Low at PDL zone
  - Execution : Rectangle break -> pullback -> engulfing / railway-tracks trigger
  - Risk      : SL beyond structural extreme (-/+) ADR buffer, TP1 at PDH/PDL,
                TP2 at R-multiple
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
@dataclass
class Params:
    adr_len:   int   = 14
    climax_mx: float = 1.75
    zone_pct:  float = 0.15
    pat_len:   int   = 8
    use_eng:   bool  = True
    use_rr:    bool  = True
    brk_exp:   int   = 20
    use_ny:    bool  = True
    sl_buf:    float = 0.10
    tp1_pct:   float = 40.0
    rr_tp2:    float = 2.0
    risk_pct:  float = 0.005     # 0.5% account risk per trade
    commission_bps: float = 2.0  # 2 bps per side
    initial_capital: float = 10_000.0


# ---------------------------------------------------------------------------
# Context preparation
# ---------------------------------------------------------------------------
def _iso_week_key(idx: pd.DatetimeIndex) -> np.ndarray:
    iso = idx.isocalendar()
    return (iso.year.astype(np.int64) * 100 + iso.week.astype(np.int64)).to_numpy()


def prepare_context(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    """Add daily and weekly HTF columns to an intraday OHLCV frame."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df.index must be DatetimeIndex")
    df = df.sort_index().copy()

    # ------- daily ---------------------------------------------------------
    daily = df.resample("1D").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    ).dropna()
    daily["range"] = daily["High"] - daily["Low"]
    daily["adr"]   = daily["range"].rolling(params.adr_len).mean()

    ctx = pd.DataFrame(index=daily.index)
    ctx["pdh"]        = daily["High"].shift(1)
    ctx["pdl"]        = daily["Low"].shift(1)
    ctx["pdc"]        = daily["Close"].shift(1)
    ctx["pdh2"]       = daily["High"].shift(2)
    ctx["pdl2"]       = daily["Low"].shift(2)
    ctx["adr_yday"]   = daily["adr"].shift(1)
    ctx["prev_range"] = daily["range"].shift(1)
    ctx.index = ctx.index.normalize()

    df["_date"] = df.index.normalize()
    df = df.merge(ctx, left_on="_date", right_index=True, how="left")
    df.drop(columns=["_date"], inplace=True)

    # ------- weekly --------------------------------------------------------
    # Group by ISO year-week, take H/L of completed weeks.
    tmp = df[["High", "Low"]].copy()
    tmp["_yw"] = _iso_week_key(tmp.index)
    weekly = tmp.groupby("_yw").agg({"High": "max", "Low": "min"}).sort_index()
    weekly["wh"] = weekly["High"].shift(1)
    weekly["wl"] = weekly["Low"].shift(1)

    df["_yw"] = _iso_week_key(df.index)
    df = df.merge(weekly[["wh", "wl"]], left_on="_yw", right_index=True, how="left")
    df.drop(columns=["_yw"], inplace=True)

    # ------- pattern range (rectangle high/low over last patLen bars) ------
    df["prior_hi"] = df["High"].shift(1).rolling(params.pat_len).max()
    df["prior_lo"] = df["Low"].shift(1).rolling(params.pat_len).min()
    return df


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    side: str              # "long" or "short"
    entry_time: pd.Timestamp
    entry_price: float
    size: float            # in instrument units
    sl: float
    tp1: float
    tp2: float
    exit_time: Optional[pd.Timestamp] = None
    exit_legs: list = field(default_factory=list)   # [(time, price, qty_frac, reason)]
    pnl: float = 0.0
    r_multiple: float = 0.0


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------
def backtest(df_in: pd.DataFrame, params: Params) -> tuple[list[Trade], pd.Series]:
    """Run the strategy on a single instrument.

    Returns (trades, equity_curve).
    """
    df = prepare_context(df_in, params)

    O = df["Open"].to_numpy(np.float64)
    H = df["High"].to_numpy(np.float64)
    L = df["Low"].to_numpy(np.float64)
    C = df["Close"].to_numpy(np.float64)

    pdh   = df["pdh"].to_numpy(np.float64)
    pdl   = df["pdl"].to_numpy(np.float64)
    pdc   = df["pdc"].to_numpy(np.float64)
    pdh2  = df["pdh2"].to_numpy(np.float64)
    pdl2  = df["pdl2"].to_numpy(np.float64)
    adr   = df["adr_yday"].to_numpy(np.float64)
    prng  = df["prev_range"].to_numpy(np.float64)
    wh    = df["wh"].to_numpy(np.float64)
    wl    = df["wl"].to_numpy(np.float64)
    p_hi  = df["prior_hi"].to_numpy(np.float64)
    p_lo  = df["prior_lo"].to_numpy(np.float64)

    times = df.index
    week_key = _iso_week_key(times)
    dow      = times.dayofweek.to_numpy()
    hour     = times.hour.to_numpy()

    n = len(df)

    # NY session, approximated in UTC: ~13:00-21:00 covers 9:30-16:00 ET
    # across DST transitions. yfinance returns UTC for FX/crypto/futures and
    # exchange-local for equities; we convert at the run layer.
    in_ny = (hour >= 13) & (hour < 21) if params.use_ny else np.ones(n, dtype=bool)
    early_w = (dow == 0) | (dow == 1)

    # ---- triggers (vectorised) -------------------------------------------
    Cp, Op = np.r_[np.nan, C[:-1]], np.r_[np.nan, O[:-1]]
    bullEng = (C > O) & (Cp < Op) & (C >= Op) & (O <= Cp)
    bearEng = (C < O) & (Cp > Op) & (C <= Op) & (O >= Cp)
    bn = np.abs(C - O)
    bp = np.abs(Cp - Op)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(np.maximum(bn, bp) > 0,
                         np.minimum(bn, bp) / np.maximum(bn, bp), 0.0)
    sim = (bp > 0) & (bn > 0) & (ratio > 0.7)
    bullRR = (C > O) & (Cp < Op) & sim
    bearRR = (C < O) & (Cp > Op) & sim
    trigBull = (params.use_eng & bullEng) | (params.use_rr & bullRR)
    trigBear = (params.use_eng & bearEng) | (params.use_rr & bearRR)

    # ---- state -----------------------------------------------------------
    pendUp = pendDn = foShort = foLong = False
    last_lvl_up = last_lvl_dn = np.nan
    last_bar_up = last_bar_dn = -10**9

    pos: Optional[Trade] = None
    pos_qty_remaining = 0.0
    pos_partial_taken = False
    trades: list[Trade] = []

    equity = params.initial_capital
    eq_curve = np.full(n, np.nan)

    cm = params.commission_bps / 10_000.0

    def _close_leg(p: Trade, t, price, qty, reason):
        nonlocal equity
        sign = 1.0 if p.side == "long" else -1.0
        gross = (price - p.entry_price) * sign * qty
        cost  = (abs(price) + abs(p.entry_price)) * qty * cm
        net   = gross - cost
        equity += net
        p.pnl += net
        p.exit_legs.append((t, price, qty, reason))

    for i in range(1, n):
        # reset weekly latches on new week
        if week_key[i] != week_key[i - 1]:
            pendUp = pendDn = foShort = foLong = False

        eq_curve[i] = equity

        # need full daily context
        if (np.isnan(pdh[i]) or np.isnan(pdh2[i]) or np.isnan(adr[i])
                or adr[i] <= 0 or np.isnan(p_hi[i])):
            continue

        zone = adr[i] * params.zone_pct

        # --- weekly fakeout state machine ---------------------------------
        if early_w[i] and not np.isnan(wh[i]) and H[i] > wh[i]:
            pendUp = True
        if early_w[i] and not np.isnan(wl[i]) and L[i] < wl[i]:
            pendDn = True
        if pendUp and C[i] < wh[i]:
            foShort = True
            pendUp = False
        if pendDn and C[i] > wl[i]:
            foLong = True
            pendDn = False

        # --- BIS ----------------------------------------------------------
        htf_down = pdl[i] < pdl2[i] and pdh[i] < pdh2[i]
        htf_up   = pdh[i] > pdh2[i] and pdl[i] > pdl2[i]
        bis_bull = htf_down and H[i] > pdh[i]
        bis_bear = htf_up   and L[i] < pdl[i]

        # --- setup zones --------------------------------------------------
        # Bias: failed-break-reclaim OR break-in-structure
        bull_bias = foLong  or bis_bull
        bear_bias = foShort or bis_bear

        # Buy-low location: at PDL, PDC, or (on BIS) retest of broken PDH
        buy_zone = (
            ((pdl[i] - zone) <= L[i] <= (pdl[i] + zone))
            or (not np.isnan(pdc[i])
                and (pdc[i] - zone) <= L[i] <= (pdc[i] + zone))
            or (bis_bull and (pdh[i] - zone) <= L[i] <= (pdh[i] + zone))
        )
        # Sell-high location: at PDH, PDC, or (on BIS) retest of broken PDL
        sell_zone = (
            ((pdh[i] - zone) <= H[i] <= (pdh[i] + zone))
            or (not np.isnan(pdc[i])
                and (pdc[i] - zone) <= H[i] <= (pdc[i] + zone))
            or (bis_bear and (pdl[i] - zone) <= H[i] <= (pdl[i] + zone))
        )

        long_setup  = bull_bias and buy_zone
        short_setup = bear_bias and sell_zone

        climax_y = adr[i] > 0 and prng[i] > params.climax_mx * adr[i]
        trading_allowed = adr[i] > 0 and not climax_y

        # --- pattern + breakout ------------------------------------------
        rect_rng = p_hi[i] - p_lo[i]
        is_rect  = rect_rng > 0 and rect_rng < adr[i] * 0.5
        if is_rect and C[i] > p_hi[i]:
            last_lvl_up = p_hi[i]
            last_bar_up = i
        if is_rect and C[i] < p_lo[i]:
            last_lvl_dn = p_lo[i]
            last_bar_dn = i

        pb_up = (
            (i - last_bar_up) <= params.brk_exp
            and not np.isnan(last_lvl_up)
            and L[i] <= last_lvl_up + zone
            and C[i] > last_lvl_up
        )
        pb_dn = (
            (i - last_bar_dn) <= params.brk_exp
            and not np.isnan(last_lvl_dn)
            and H[i] >= last_lvl_dn - zone
            and C[i] < last_lvl_dn
        )

        long_cond  = trading_allowed and in_ny[i] and long_setup  and pb_up and trigBull[i]
        short_cond = trading_allowed and in_ny[i] and short_setup and pb_dn and trigBear[i]

        # ---- exit management on next bar ---------------------------------
        if pos is not None and i > pos_entry_idx:
            if pos.side == "long":
                # Conservative: if SL and TP both inside the bar, assume SL first.
                sl_hit  = L[i] <= pos.sl
                tp1_hit = (not pos_partial_taken) and H[i] >= pos.tp1
                tp2_hit = pos_partial_taken and H[i] >= pos.tp2
                if sl_hit:
                    _close_leg(pos, times[i], pos.sl, pos_qty_remaining, "SL")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None
                    pos_qty_remaining = 0.0
                    pos_partial_taken = False
                elif tp1_hit:
                    qty = pos_qty_remaining * (params.tp1_pct / 100.0)
                    _close_leg(pos, times[i], pos.tp1, qty, "TP1")
                    pos_qty_remaining -= qty
                    pos_partial_taken = True
                    if H[i] >= pos.tp2 and pos_qty_remaining > 0:
                        _close_leg(pos, times[i], pos.tp2,
                                   pos_qty_remaining, "TP2")
                        pos.exit_time = times[i]
                        trades.append(pos)
                        pos = None
                        pos_qty_remaining = 0.0
                        pos_partial_taken = False
                elif tp2_hit:
                    _close_leg(pos, times[i], pos.tp2, pos_qty_remaining, "TP2")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None
                    pos_qty_remaining = 0.0
                    pos_partial_taken = False
            else:  # short
                sl_hit  = H[i] >= pos.sl
                tp1_hit = (not pos_partial_taken) and L[i] <= pos.tp1
                tp2_hit = pos_partial_taken and L[i] <= pos.tp2
                if sl_hit:
                    _close_leg(pos, times[i], pos.sl, pos_qty_remaining, "SL")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None
                    pos_qty_remaining = 0.0
                    pos_partial_taken = False
                elif tp1_hit:
                    qty = pos_qty_remaining * (params.tp1_pct / 100.0)
                    _close_leg(pos, times[i], pos.tp1, qty, "TP1")
                    pos_qty_remaining -= qty
                    pos_partial_taken = True
                    if L[i] <= pos.tp2 and pos_qty_remaining > 0:
                        _close_leg(pos, times[i], pos.tp2,
                                   pos_qty_remaining, "TP2")
                        pos.exit_time = times[i]
                        trades.append(pos)
                        pos = None
                        pos_qty_remaining = 0.0
                        pos_partial_taken = False
                elif tp2_hit:
                    _close_leg(pos, times[i], pos.tp2, pos_qty_remaining, "TP2")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None
                    pos_qty_remaining = 0.0
                    pos_partial_taken = False

        # ---- entries ----------------------------------------------------
        if pos is None:
            if long_cond:
                sl  = min(L[i], L[i - 1]) - adr[i] * params.sl_buf
                risk = C[i] - sl
                if risk > 0 and pdh[i] > C[i]:
                    tp1 = pdh[i]
                    tp2 = C[i] + params.rr_tp2 * risk
                    size = (equity * params.risk_pct) / risk
                    pos = Trade(
                        side="long", entry_time=times[i], entry_price=C[i],
                        size=size, sl=sl, tp1=tp1, tp2=tp2,
                    )
                    pos_qty_remaining = size
                    pos_partial_taken = False
                    pos_entry_idx = i
                    pos.r_multiple = (tp1 - C[i]) / risk
            elif short_cond:
                sl  = max(H[i], H[i - 1]) + adr[i] * params.sl_buf
                risk = sl - C[i]
                if risk > 0 and pdl[i] < C[i]:
                    tp1 = pdl[i]
                    tp2 = C[i] - params.rr_tp2 * risk
                    size = (equity * params.risk_pct) / risk
                    pos = Trade(
                        side="short", entry_time=times[i], entry_price=C[i],
                        size=size, sl=sl, tp1=tp1, tp2=tp2,
                    )
                    pos_qty_remaining = size
                    pos_partial_taken = False
                    pos_entry_idx = i
                    pos.r_multiple = (C[i] - tp1) / risk

    # close any dangling position at the last bar's close
    if pos is not None and pos_qty_remaining > 0:
        _close_leg(pos, times[-1], C[-1], pos_qty_remaining, "EOD")
        pos.exit_time = times[-1]
        trades.append(pos)

    eq_curve[-1] = equity
    eq_series = pd.Series(eq_curve, index=df.index, name="equity")
    eq_series = eq_series.ffill().fillna(params.initial_capital)
    return trades, eq_series


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def summarise(trades: list[Trade], equity: pd.Series, params: Params) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "win_rate": np.nan, "avg_R": np.nan,
            "total_return_pct": 0.0, "max_dd_pct": 0.0, "profit_factor": np.nan,
            "final_equity": params.initial_capital,
        }

    pnl = np.array([t.pnl for t in trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() < 0 else np.nan

    eq = equity.dropna()
    if len(eq) == 0:
        eq = pd.Series([params.initial_capital])
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0

    risk_dollars = params.initial_capital * params.risk_pct
    avg_R = float(np.mean(pnl) / risk_dollars) if risk_dollars > 0 else np.nan

    return {
        "trades":          n,
        "win_rate":        float((pnl > 0).mean()),
        "avg_R":           avg_R,
        "total_return_pct": float((eq.iloc[-1] / params.initial_capital - 1) * 100),
        "max_dd_pct":      float(max_dd * 100),
        "profit_factor":   float(pf) if not np.isnan(pf) else float("nan"),
        "final_equity":    float(eq.iloc[-1]),
    }
