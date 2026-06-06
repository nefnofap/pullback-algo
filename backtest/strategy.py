"""
Weekly Manipulation & Failed Breakout - event-driven backtest engine.

Mirrors `pine/weekly_manipulation_strategy.pine`. Implements every step of
the original spec PLUS the Tier 1+2+3 improvements documented in
`research/improvements.md`:

  * Daily 200 EMA HTF trend filter
  * Runner stop moves to BE (+/- offset R) after TP1 fills
  * ATR-multiple stop combined with the structural extreme
  * ADX trend-strength gate
  * Pin-bar and inside-bar-break triggers in addition to engulfing/RR
  * Day-level sweep+reclaim latch alongside the weekly fakeout
  * Optional London-open session window in addition to NY
  * Daily-bar swing-variant mode (`daily_mode=True`) for long-history runs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ===========================================================================
# Parameters
# ===========================================================================
@dataclass
class Params:
    # --- existing -----------------------------------------------------------
    adr_len:            int   = 14
    climax_mx:          float = 1.75
    zone_pct:           float = 0.15
    pat_len:            int   = 8
    use_eng:            bool  = True
    use_rr:             bool  = True
    brk_exp:            int   = 20
    use_ny:             bool  = True
    sl_buf:             float = 0.10
    tp1_pct:            float = 40.0
    rr_tp2:             float = 2.0
    risk_pct:           float = 0.005
    commission_bps:     float = 2.0
    initial_capital:    float = 10_000.0

    # --- Tier 1: HTF trend filter, runner BE, ATR-stop -----------------------
    use_htf_trend:      bool  = True
    htf_trend_period:   int   = 200    # EMA period in HTF bars (D for intraday, daily-bar for daily_mode)
    runner_be:          bool  = True
    runner_be_offset_r: float = 0.10   # +/- 0.1R from entry once TP1 fills
    use_atr_stop:       bool  = True
    atr_period:         int   = 14
    atr_stop_k:         float = 1.5    # entry +/- k * ATR

    # --- Tier 2: ADX gate, more triggers, day-level sweep --------------------
    use_adx_filter:     bool  = True
    adx_period:         int   = 14
    adx_threshold:      float = 22.0
    use_pin_bar:        bool  = True
    pin_wick_ratio:     float = 0.6
    use_inside_break:   bool  = True
    use_day_sweep:      bool  = True

    # --- Tier 3: extended session, daily-bar swing mode ----------------------
    extended_session:   bool  = False  # adds London-open window 07-13 UTC
    daily_mode:         bool  = False  # bars are 1d; switches resampling rules

    # --- consolidation threshold for the rectangle pattern -------------------
    # In intraday mode patLen bars cover < 1 day, so range < 0.5 * ADR is tight.
    # In daily_mode patLen bars cover patLen days, so range scales with patLen.
    # Set to None to auto-pick (0.5 intraday, pat_len*0.3 daily).
    rect_max_mult:      Optional[float] = None


# ===========================================================================
# Helpers
# ===========================================================================
def _iso_week_key(idx: pd.DatetimeIndex) -> np.ndarray:
    iso = idx.isocalendar()
    return (iso.year.astype(np.int64) * 100 + iso.week.astype(np.int64)).to_numpy()


def _atr(h: pd.Series, l: pd.Series, c: pd.Series, period: int) -> pd.Series:
    """Wilder-smoothed ATR."""
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _adx(h: pd.Series, l: pd.Series, c: pd.Series, period: int) -> pd.Series:
    """Wilder-smoothed ADX."""
    up = h.diff()
    dn = -l.diff()
    plus_dm  = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di  = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


# ===========================================================================
# Context preparation
# ===========================================================================
def prepare_context(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    """Add HTF context columns (PDH/PDL/PDC, prev-week H/L, ADR, EMA, ADX, ATR,
    rectangle high/low) to an OHLC frame.

    Works for two modes:
      * intraday (default): df has 1h bars; daily and weekly context are
        resampled.
      * daily_mode: df has 1d bars; "previous day" is just shift(1) and weekly
        is aggregated by ISO week.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df.index must be DatetimeIndex")
    df = df.sort_index().copy()

    # ---------- previous-day series + ADR -------------------------------------
    if params.daily_mode:
        df["pdh"]        = df["High"].shift(1)
        df["pdl"]        = df["Low"].shift(1)
        df["pdc"]        = df["Close"].shift(1)
        df["pdh2"]       = df["High"].shift(2)
        df["pdl2"]       = df["Low"].shift(2)
        rng              = df["High"] - df["Low"]
        df["adr_yday"]   = rng.rolling(params.adr_len).mean().shift(1)
        df["prev_range"] = rng.shift(1)
    else:
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

    # ---------- weekly H/L (previous completed week) --------------------------
    tmp = df[["High", "Low"]].copy()
    tmp["_yw"] = _iso_week_key(tmp.index)
    weekly = tmp.groupby("_yw").agg({"High": "max", "Low": "min"}).sort_index()
    weekly["wh"] = weekly["High"].shift(1)
    weekly["wl"] = weekly["Low"].shift(1)
    df["_yw"] = _iso_week_key(df.index)
    df = df.merge(weekly[["wh", "wl"]], left_on="_yw", right_index=True, how="left")
    df.drop(columns=["_yw"], inplace=True)

    # ---------- pattern range (rolling N-bar high/low on chart bars) ----------
    df["prior_hi"] = df["High"].shift(1).rolling(params.pat_len).max()
    df["prior_lo"] = df["Low"].shift(1).rolling(params.pat_len).min()

    # ---------- HTF 200-EMA trend filter --------------------------------------
    if params.use_htf_trend:
        if params.daily_mode:
            df["htf_ema"] = df["Close"].ewm(
                span=params.htf_trend_period, adjust=False
            ).mean().shift(1)
        else:
            d_close = df.resample("1D")["Close"].last().dropna()
            ema = d_close.ewm(span=params.htf_trend_period, adjust=False).mean().shift(1)
            ema_df = pd.DataFrame({"htf_ema": ema})
            ema_df.index = ema_df.index.normalize()
            df["_date"] = df.index.normalize()
            df = df.merge(ema_df, left_on="_date", right_index=True, how="left")
            df.drop(columns=["_date"], inplace=True)
    else:
        df["htf_ema"] = np.nan

    # ---------- ATR for ATR-multiple stops ------------------------------------
    if params.use_atr_stop:
        df["atr"] = _atr(df["High"], df["Low"], df["Close"], params.atr_period)
    else:
        df["atr"] = np.nan

    # ---------- ADX for trend-strength gate -----------------------------------
    if params.use_adx_filter:
        df["adx"] = _adx(df["High"], df["Low"], df["Close"], params.adx_period)
    else:
        df["adx"] = np.nan

    return df


# ===========================================================================
# Trade record
# ===========================================================================
@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    size: float
    sl: float
    original_sl: float
    tp1: float
    tp2: float
    exit_time: Optional[pd.Timestamp] = None
    exit_legs: list = field(default_factory=list)
    pnl: float = 0.0
    r_multiple: float = 0.0


# ===========================================================================
# Backtest core
# ===========================================================================
def backtest(df_in: pd.DataFrame, params: Params) -> tuple[list[Trade], pd.Series]:
    df = prepare_context(df_in, params)

    O = df["Open" ].to_numpy(np.float64)
    H = df["High" ].to_numpy(np.float64)
    L = df["Low"  ].to_numpy(np.float64)
    C = df["Close"].to_numpy(np.float64)

    pdh   = df["pdh"]        .to_numpy(np.float64)
    pdl   = df["pdl"]        .to_numpy(np.float64)
    pdc   = df["pdc"]        .to_numpy(np.float64)
    pdh2  = df["pdh2"]       .to_numpy(np.float64)
    pdl2  = df["pdl2"]       .to_numpy(np.float64)
    adr   = df["adr_yday"]   .to_numpy(np.float64)
    prng  = df["prev_range"] .to_numpy(np.float64)
    wh    = df["wh"]         .to_numpy(np.float64)
    wl    = df["wl"]         .to_numpy(np.float64)
    p_hi  = df["prior_hi"]   .to_numpy(np.float64)
    p_lo  = df["prior_lo"]   .to_numpy(np.float64)
    ema   = df["htf_ema"]    .to_numpy(np.float64)
    atr   = df["atr"]        .to_numpy(np.float64)
    adx   = df["adx"]        .to_numpy(np.float64)

    times    = df.index
    week_key = _iso_week_key(times)
    day_key  = times.normalize().asi8
    dow      = times.dayofweek.to_numpy()
    hour     = times.hour.to_numpy()
    n        = len(df)

    # --------- session filter -------------------------------------------------
    if params.daily_mode or not params.use_ny:
        in_sess = np.ones(n, dtype=bool)
    elif params.extended_session:
        # London open ~07:00 UTC through NY close ~21:00 UTC
        in_sess = (hour >= 7) & (hour < 21)
    else:
        in_sess = (hour >= 13) & (hour < 21)
    early_w = (dow == 0) | (dow == 1)

    # --------- vectorised trigger candles -------------------------------------
    Cp, Op = np.r_[np.nan, C[:-1]], np.r_[np.nan, O[:-1]]
    Hp, Lp = np.r_[np.nan, H[:-1]], np.r_[np.nan, L[:-1]]
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

    # Pin bars (long lower wick at a swing low / long upper wick at a swing high)
    rng_arr = H - L
    with np.errstate(divide="ignore", invalid="ignore"):
        lower_wick = np.minimum(O, C) - L
        upper_wick = H - np.maximum(O, C)
        lw_pct = np.where(rng_arr > 0, lower_wick / rng_arr, 0.0)
        uw_pct = np.where(rng_arr > 0, upper_wick / rng_arr, 0.0)
    pinBull = (lw_pct >= params.pin_wick_ratio) & (C > (H + L) / 2.0) & (L < np.r_[np.nan, np.minimum(L[:-1], np.r_[np.nan, L[:-2]] if n > 2 else np.nan)])
    pinBear = (uw_pct >= params.pin_wick_ratio) & (C < (H + L) / 2.0) & (H > np.r_[np.nan, np.maximum(H[:-1], np.r_[np.nan, H[:-2]] if n > 2 else np.nan)])
    # Replace NaNs from boolean ops
    pinBull = np.where(np.isnan(pinBull.astype(float)), False, pinBull).astype(bool)
    pinBear = np.where(np.isnan(pinBear.astype(float)), False, pinBear).astype(bool)

    # Inside-bar break: previous bar is inside the bar before it; current bar closes beyond
    # the inside bar's range in the direction of close.
    Hpp, Lpp = np.r_[np.nan, np.nan, H[:-2]], np.r_[np.nan, np.nan, L[:-2]]
    inside_prev = (Hp < Hpp) & (Lp > Lpp)
    insideBull = inside_prev & (C > Hp) & (C > O)
    insideBear = inside_prev & (C < Lp) & (C < O)

    trigBull = ((params.use_eng        & bullEng)
              | (params.use_rr         & bullRR)
              | (params.use_pin_bar    & pinBull)
              | (params.use_inside_break & insideBull))
    trigBear = ((params.use_eng        & bearEng)
              | (params.use_rr         & bearRR)
              | (params.use_pin_bar    & pinBear)
              | (params.use_inside_break & insideBear))

    # --------- state ----------------------------------------------------------
    pendUp = pendDn = foShort = foLong = False
    pendUpD = pendDnD = foShortD = foLongD = False
    last_lvl_up = last_lvl_dn = np.nan
    last_bar_up = last_bar_dn = -10**9

    pos: Optional[Trade] = None
    pos_qty_remaining = 0.0
    pos_partial_taken = False
    pos_entry_idx = -1
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

    for i in range(2, n):
        # weekly latch resets
        if week_key[i] != week_key[i - 1]:
            pendUp = pendDn = foShort = foLong = False
        # daily latch resets
        if params.use_day_sweep and day_key[i] != day_key[i - 1]:
            pendUpD = pendDnD = foShortD = foLongD = False

        eq_curve[i] = equity

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
            pendUp  = False
        if pendDn and C[i] > wl[i]:
            foLong  = True
            pendDn  = False

        # --- day-level sweep state machine (Tier-2 #2.6) ------------------
        if params.use_day_sweep:
            if H[i] > pdh[i]:
                pendUpD = True
            if L[i] < pdl[i]:
                pendDnD = True
            if pendUpD and C[i] < pdh[i]:
                foShortD = True
                pendUpD  = False
            if pendDnD and C[i] > pdl[i]:
                foLongD  = True
                pendDnD  = False
        any_short = foShort or (params.use_day_sweep and foShortD)
        any_long  = foLong  or (params.use_day_sweep and foLongD)

        # --- BIS ----------------------------------------------------------
        htf_down = pdl[i] < pdl2[i] and pdh[i] < pdh2[i]
        htf_up   = pdh[i] > pdh2[i] and pdl[i] > pdl2[i]
        bis_bull = htf_down and H[i] > pdh[i]
        bis_bear = htf_up   and L[i] < pdl[i]

        # --- setup zones --------------------------------------------------
        bull_bias = any_long  or bis_bull
        bear_bias = any_short or bis_bear
        buy_zone = (
            ((pdl[i] - zone) <= L[i] <= (pdl[i] + zone))
            or (not np.isnan(pdc[i]) and (pdc[i] - zone) <= L[i] <= (pdc[i] + zone))
            or (bis_bull and (pdh[i] - zone) <= L[i] <= (pdh[i] + zone))
        )
        sell_zone = (
            ((pdh[i] - zone) <= H[i] <= (pdh[i] + zone))
            or (not np.isnan(pdc[i]) and (pdc[i] - zone) <= H[i] <= (pdc[i] + zone))
            or (bis_bear and (pdl[i] - zone) <= H[i] <= (pdl[i] + zone))
        )
        long_setup  = bull_bias and buy_zone
        short_setup = bear_bias and sell_zone

        climax_y = adr[i] > 0 and prng[i] > params.climax_mx * adr[i]
        trading_allowed = adr[i] > 0 and not climax_y

        # --- pattern + breakout ------------------------------------------
        rect_rng = p_hi[i] - p_lo[i]
        if params.rect_max_mult is not None:
            rect_max = adr[i] * params.rect_max_mult
        elif params.daily_mode:
            rect_max = adr[i] * params.pat_len * 0.3
        else:
            rect_max = adr[i] * 0.5
        is_rect  = rect_rng > 0 and rect_rng < rect_max
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
            and C[i] >  last_lvl_up
        )
        pb_dn = (
            (i - last_bar_dn) <= params.brk_exp
            and not np.isnan(last_lvl_dn)
            and H[i] >= last_lvl_dn - zone
            and C[i] <  last_lvl_dn
        )

        # --- HTF trend filter (Tier-1 #2.1) -------------------------------
        if params.use_htf_trend and not np.isnan(ema[i]):
            htf_long_ok  = C[i] > ema[i]
            htf_short_ok = C[i] < ema[i]
        else:
            htf_long_ok = htf_short_ok = True

        # --- ADX gate (Tier-2 #2.4) ---------------------------------------
        if params.use_adx_filter and not np.isnan(adx[i]):
            adx_ok = adx[i] >= params.adx_threshold
        else:
            adx_ok = True

        long_cond  = (trading_allowed and in_sess[i] and long_setup
                      and pb_up and trigBull[i] and htf_long_ok and adx_ok)
        short_cond = (trading_allowed and in_sess[i] and short_setup
                      and pb_dn and trigBear[i] and htf_short_ok and adx_ok)

        # ----------------- exit management on next bar --------------------
        if pos is not None and i > pos_entry_idx:
            if pos.side == "long":
                sl_hit  = L[i] <= pos.sl
                tp1_hit = (not pos_partial_taken) and H[i] >= pos.tp1
                tp2_hit = pos_partial_taken and H[i] >= pos.tp2
                if sl_hit:
                    _close_leg(pos, times[i], pos.sl, pos_qty_remaining, "SL")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None; pos_qty_remaining = 0.0; pos_partial_taken = False
                elif tp1_hit:
                    qty = pos_qty_remaining * (params.tp1_pct / 100.0)
                    _close_leg(pos, times[i], pos.tp1, qty, "TP1")
                    pos_qty_remaining -= qty
                    pos_partial_taken = True
                    # Tier-1 #2.2: BE move on the runner
                    if params.runner_be:
                        risk = abs(pos.entry_price - pos.original_sl)
                        if risk > 0:
                            pos.sl = pos.entry_price + params.runner_be_offset_r * risk
                    if H[i] >= pos.tp2 and pos_qty_remaining > 0:
                        _close_leg(pos, times[i], pos.tp2, pos_qty_remaining, "TP2")
                        pos.exit_time = times[i]
                        trades.append(pos)
                        pos = None; pos_qty_remaining = 0.0; pos_partial_taken = False
                elif tp2_hit:
                    _close_leg(pos, times[i], pos.tp2, pos_qty_remaining, "TP2")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None; pos_qty_remaining = 0.0; pos_partial_taken = False
            else:
                sl_hit  = H[i] >= pos.sl
                tp1_hit = (not pos_partial_taken) and L[i] <= pos.tp1
                tp2_hit = pos_partial_taken and L[i] <= pos.tp2
                if sl_hit:
                    _close_leg(pos, times[i], pos.sl, pos_qty_remaining, "SL")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None; pos_qty_remaining = 0.0; pos_partial_taken = False
                elif tp1_hit:
                    qty = pos_qty_remaining * (params.tp1_pct / 100.0)
                    _close_leg(pos, times[i], pos.tp1, qty, "TP1")
                    pos_qty_remaining -= qty
                    pos_partial_taken = True
                    if params.runner_be:
                        risk = abs(pos.entry_price - pos.original_sl)
                        if risk > 0:
                            pos.sl = pos.entry_price - params.runner_be_offset_r * risk
                    if L[i] <= pos.tp2 and pos_qty_remaining > 0:
                        _close_leg(pos, times[i], pos.tp2, pos_qty_remaining, "TP2")
                        pos.exit_time = times[i]
                        trades.append(pos)
                        pos = None; pos_qty_remaining = 0.0; pos_partial_taken = False
                elif tp2_hit:
                    _close_leg(pos, times[i], pos.tp2, pos_qty_remaining, "TP2")
                    pos.exit_time = times[i]
                    trades.append(pos)
                    pos = None; pos_qty_remaining = 0.0; pos_partial_taken = False

        # ---------------------------- entries ----------------------------
        if pos is None:
            if long_cond:
                sl_struct = min(L[i], L[i - 1]) - adr[i] * params.sl_buf
                if params.use_atr_stop and not np.isnan(atr[i]):
                    sl_atr = C[i] - params.atr_stop_k * atr[i]
                    sl = min(sl_struct, sl_atr)
                else:
                    sl = sl_struct
                risk = C[i] - sl
                if risk > 0 and pdh[i] > C[i]:
                    tp1 = pdh[i]
                    tp2 = C[i] + params.rr_tp2 * risk
                    size = (equity * params.risk_pct) / risk
                    pos = Trade(
                        side="long", entry_time=times[i], entry_price=C[i],
                        size=size, sl=sl, original_sl=sl, tp1=tp1, tp2=tp2,
                    )
                    pos.r_multiple = (tp1 - C[i]) / risk
                    pos_qty_remaining = size
                    pos_partial_taken = False
                    pos_entry_idx = i
            elif short_cond:
                sl_struct = max(H[i], H[i - 1]) + adr[i] * params.sl_buf
                if params.use_atr_stop and not np.isnan(atr[i]):
                    sl_atr = C[i] + params.atr_stop_k * atr[i]
                    sl = max(sl_struct, sl_atr)
                else:
                    sl = sl_struct
                risk = sl - C[i]
                if risk > 0 and pdl[i] < C[i]:
                    tp1 = pdl[i]
                    tp2 = C[i] - params.rr_tp2 * risk
                    size = (equity * params.risk_pct) / risk
                    pos = Trade(
                        side="short", entry_time=times[i], entry_price=C[i],
                        size=size, sl=sl, original_sl=sl, tp1=tp1, tp2=tp2,
                    )
                    pos.r_multiple = (C[i] - tp1) / risk
                    pos_qty_remaining = size
                    pos_partial_taken = False
                    pos_entry_idx = i

    # close any dangling position at last bar
    if pos is not None and pos_qty_remaining > 0:
        _close_leg(pos, times[-1], C[-1], pos_qty_remaining, "EOD")
        pos.exit_time = times[-1]
        trades.append(pos)

    eq_curve[-1] = equity
    eq_series = pd.Series(eq_curve, index=df.index, name="equity")
    eq_series = eq_series.ffill().fillna(params.initial_capital)
    return trades, eq_series


# ===========================================================================
# Stats
# ===========================================================================
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
        "trades":           n,
        "win_rate":         float((pnl > 0).mean()),
        "avg_R":            avg_R,
        "total_return_pct": float((eq.iloc[-1] / params.initial_capital - 1) * 100),
        "max_dd_pct":       float(max_dd * 100),
        "profit_factor":    float(pf) if not np.isnan(pf) else float("nan"),
        "final_equity":     float(eq.iloc[-1]),
    }
