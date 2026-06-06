# Improving sample size and win rate

This is a research note, not a TODO list. It identifies the concrete failure
modes in the current strategy and proposes evidence-based fixes ranked by
expected impact ÷ implementation cost. Sources are cited inline; verbatim
quotes are kept under 30 words per source. Content was rephrased for
compliance with licensing restrictions.

---

## 1. Failure-mode analysis (from the committed CSVs)

Inspecting `backtest/results_1h_720d.csv` plus a per-trade exit-reason
breakdown produced the following findings.

### 1.1 The single biggest leak: directional skew in trending markets

| ticker | total trades | longs | shorts | wins (long) | wins (short) |
| ------ | -----------: | ----: | -----: | ----------: | -----------: |
| ES=F   | 27           |  1    | **26** |  0          |  7           |
| NQ=F   | 19           |  3    |  16    |  1          |  6           |
| YM=F   | 22           |  2    |  20    |  0          |  5           |

ES, NQ and YM were in a sustained uptrend across the test window. The
strategy's bias detection (`foShort`/`bisBear`) frequently fires anyway
because PDH gets pierced almost daily in a strong bull leg, satisfying
the "high pierces, then closes back below" condition often enough to
generate a stream of counter-trend shorts that get stopped out. **There
is no higher-timeframe trend gate.**

### 1.2 The second biggest leak: TP1 → TP2 conversion

| ticker   | TP1 hits | TP2 hits | SL after TP1 (estimated) |
| -------- | -------: | -------: | -----------------------: |
| EURUSD=X | 9        | 2        | ~7                       |
| GBPUSD=X | 6        | 3        | ~3                       |
| ES=F     | 12       | 7        | ~5                       |

After the 40% partial at TP1, the remaining 60% sits on the original stop.
On EUR/GBP/ES that runner gets stopped out roughly **half** of the
time it reaches TP1 — so the partial gets booked but the bigger reward
gets given back. **There is no breakeven move on the runner.**

### 1.3 Stops are too tight on low-vol pairs

EUR/GBP/USDCAD have ADRs of ~30–60 pips. The current stop is the
structural extreme of the trigger bar plus `0.10 × ADR`. On a 50-pip ADR
that is a 5-pip buffer, which is well inside the typical noise band. The
SL hit rate is 80–88% on these instruments, far above the population mean.

### 1.4 Sample size is constrained by trigger rarity

The entry sequence is *bias × value-zone × pullback × engulfing/RR*. Each
of those filters is independently restrictive. With four cascaded gates,
many genuine A+ setups complete the first three steps and then never
print an engulfing or railway-tracks pair, so the trade is missed. **The
spec mentions pin bars but we never coded them.**

---

## 2. Evidence-based proposals

Each item lists: expected sample-size impact, expected win-rate impact,
implementation cost, and rationale with a citation.

### Tier 1 — HIGH impact, LOW cost (ship first)

#### 2.1 Daily HTF trend filter (200 EMA or 50/200 cross)
- **Sample size**: −20 to −35% (cuts trades opposing the daily trend)
- **Win rate**: +5 to +10 percentage points
- **Cost**: ~5 lines of Pine + Python each
- **Rationale**: A 200-period MA on the daily timeframe is a widely
  documented institutional level acting as dynamic support/resistance
  ([priyanksf8 on TradingView](https://www.tradingview.com/script/ZhGVesOi-WMA-200-Trend-Filter-with-EMA-50-Cross-Dynamic-Zones/),
  [SMC/ICT scripts](https://www.tradingview.com/script/pMKsPFWQ/)).
  Multiple sources confirm trading only in the daily-trend direction
  removes a meaningful share of losers. This is a direct fix for the
  ES/NQ/YM skew documented in §1.1.

#### 2.2 Breakeven stop on the runner after TP1
- **Sample size**: 0
- **Win rate**: 0 (it changes runners-stopped-out into runners-flat,
  which doesn't hit either column), but **avg_R per trade rises**
- **Cost**: a single conditional in the exit loop
- **Rationale**: The general advice to move stops to BE on entry can
  reduce profitability in pure trend-following because the market needs
  room ([atas.net summarising Quantified Strategies research](https://atas.net/blog/break-even-in-trading/)).
  *Our case differs*: 40% has already been booked at TP1, so the remaining
  60% is house money. Moving its stop to entry after TP1 is a defensive
  fix for the §1.2 bleed, not the aggressive BE-at-entry pattern the
  research warns against.
- **Variant to test**: BE + small offset (e.g., +0.1R) so noise doesn't
  unwind the position.

#### 2.3 ATR-multiple stop, instrument-aware
- **Sample size**: 0
- **Win rate**: +2 to +5 pp (fewer noise-driven SL hits)
- **Cost**: ~10 lines
- **Rationale**: Using a single ATR multiplier across a heterogeneous
  basket is documented as a fundamental risk-management error
  ([quantstrategy.io](https://quantstrategy.io/blog/optimizing-atr-multipliers-backtesting-strategies-for/)).
  Replace the structural+`0.10×ADR` stop with `max(structural,
  entry ± k × ATR(14))` where `k` is per-instrument-class
  (FX-major: 1.5, indices: 2.0, metals: 1.8, crypto: 1.5–2.0).

### Tier 2 — HIGH impact, MEDIUM cost

#### 2.4 Trend-strength gate (ADX or Choppiness Index)
- **Sample size**: −30 to −45%
- **Win rate**: +5 to +12 pp
- **Cost**: ~15 lines (ADX is in `ta.*` for Pine; Python needs to compute it)
- **Rationale**: ADX measures trend strength on 0–100; > 25 = strong trend
  ([luxalgo Rob Booker ADX Breakout](https://www.luxalgo.com/blog/rob-booker-adx-breakout-entry-signals/),
  [therobusttrader](https://therobusttrader.com/adx-indicator-settings-best/)).
  An ADX exit-filter on a basic NQ breakout was reported to lift the
  return-to-DD ratio from 3.4 to 6.1
  ([algotr substack](https://open.substack.com/pub/algotr/p/your-entries-arent-the-problem-your)).
  Choppiness Index below 38.2 indicates a strong trend, above 61.8 a
  range ([netpicks](https://www.netpicks.com/choppiness-index-by-bill-dreiss/)).
  Pick one — they measure roughly the same thing — and gate entries on it.
- **Concrete recipe**: require `ADX(14) > 22` on the chart timeframe at
  entry bar. Loosen to `> 18` if combined with the daily-trend filter
  in §2.1 (the two together are mildly redundant).

#### 2.5 Add pin-bar and inside-bar-break triggers
- **Sample size**: +40 to +70%
- **Win rate**: ±2 pp (similar quality to engulfing)
- **Cost**: ~20 lines
- **Rationale**: The original spec lists pin bars as a high-probability
  confirmation. Inside-bar breakouts are a documented continuation
  pattern, particularly effective right after consolidation rectangles
  ([oxfordstrat ADX](https://oxfordstrat.com/trading-strategies/adx/) lists
  IB+breakout as a "filter threshold" candidate). Implementing both
  multiplies the trigger-OR set from 2 patterns to 4, with the same
  bias and zone gates upstream — quality stays high while sample widens.
- **Implementation**:
  - **Pin bar bullish**: `low < min(low[1], low[2]) - 0.5*ADR` AND
    `close > (high+low)/2` AND wick is ≥ 60% of bar range.
  - **Inside-bar break**: bar with `high < high[1] AND low > low[1]`
    followed by a close beyond the parent bar's extreme.

### Tier 3 — MEDIUM impact, MEDIUM cost

#### 2.6 Day-level liquidity sweep trigger (the spec only sweeps the week)
- **Sample size**: +50 to +90% (this is the single biggest sample-size lever)
- **Win rate**: +3 to +8 pp (sweep-and-reclaim is one of the higher-quality triggers)
- **Cost**: medium — daily-scale latch like the existing weekly fakeout state machine
- **Rationale**: The current strategy has a Mon/Tue fakeout state
  machine that fires only at the **prior-week** H/L. The same
  pattern at the **prior-day** H/L is the canonical ICT/SMC liquidity
  sweep, documented as one of the highest-probability setups when
  combined with change-of-character + FVG
  ([damnpropfirms](https://damnpropfirms.com/glossary/liquidity-sweep/),
  [smartmoneyict](https://smartmoneyict.com/liquidity-sweep-vs-liquidity-run/)).
  Add a second latch: pierce PDH/PDL → close back inside → bias flag for
  the rest of the day. This roughly doubles the bullBias/bearBias trigger
  rate without lowering quality (the same mechanic that works at the
  weekly scale works at the daily scale).
- **Implementation**: copy the existing `pendUp / pendDn / foShort /
  foLong` block, anchor it to PDH/PDL, reset on `newDay` instead of
  `newWeek`. Combine with the existing weekly latch via OR.

#### 2.7 Session expansion (FX-specific)
- **Sample size**: +30 to +60% on FX
- **Win rate**: -2 to +2 pp (London open is the same quality as NY)
- **Cost**: low
- **Rationale**: The strategy currently only trades the NY session
  (UTC 13–21). FX has two more high-quality liquidity windows: the
  **London open (UTC 07–10)** and the **Asian-EU overlap on JPY pairs
  (UTC 23–02)**. Restricting to NY-only is a defensible default for
  USD pairs but excludes much of the available signal on
  EUR/GBP/AUD/JPY crosses
  ([fxempire on news-driven FX](https://www.fxempire.com/education/article/news-driven-fx-trading-how-to-trade-events-like-the-fomc-cpi-and-nfp-1549791)).
- **Implementation**: turn the single `useNY` toggle into a multi-window
  session config: `["0700-1000", "1300-2100"]`.

### Tier 4 — Lower priority

#### 2.8 Anchored VWAP from start of week
- **Sample size**: +10 to +20%
- **Win rate**: +2 to +4 pp
- **Cost**: medium for instruments with reliable volume; doesn't work
  on spot FX (yfinance returns volume of 0 for `=X` symbols)
- **Rationale**: AVWAP is a recognised swing-context support/resistance
  level when anchored to a real event like the weekly open
  ([financialtechwiz](https://www.financialtechwiz.com/post/vwap-tradingview),
  [abovethegreenline](https://abovethegreenline.com/anchored-vwap-guide/)).
  Add it as another option in the `buy_zone`/`sell_zone` set so that the
  weekly AVWAP retest is also a valid value location.

#### 2.9 Economic-event filter (FOMC, CPI, NFP)
- **Sample size**: −5 to −10%
- **Win rate**: +1 to +3 pp
- **Cost**: HIGH — requires an external economic calendar feed
  (Investing.com or ForexFactory CSV; not in yfinance)
- **Rationale**: Standard advice is to flatten 30 minutes before
  high-impact releases and re-evaluate 5–15 minutes after
  ([supertrader.me](https://www.supertrader.me/blog/economic-calendar-trading-guide/)).
  The engulfing/RR pattern recogniser misfires on news gaps.
  Defer to a later iteration; can be added as a manual
  "skip-this-day" CSV input.

#### 2.10 Re-entry after stop-out within the same bias window
- **Sample size**: +20 to +40%
- **Win rate**: −5 to 0 pp (re-entries are typically lower quality)
- **Cost**: low
- **Notes**: usually a wash, but potentially useful on crypto where
  bias windows are long. Test in walk-forward only.

#### 2.11 Volume / order-flow filter (CVD, footprint)
- **Sample size**: 0
- **Win rate**: +5 to +10 pp on instruments with real volume
- **Cost**: HIGH — needs tick or footprint data
- **Notes**: out of scope for yfinance-based sandbox; powerful on a
  proper data feed.

---

## 3. Suggested rollout order

| Phase | Items                                          | Why this order                               |
| ----- | ---------------------------------------------- | -------------------------------------------- |
| 1     | 2.1 daily trend filter, 2.2 BE on runner, 2.3 ATR-stop | These three address the three quantified failure modes from §1 directly. They cost ~30 lines combined. |
| 2     | 2.4 ADX gate, 2.5 pin/inside-bar triggers, 2.6 daily sweep | Phase 1 will trim sample size; phase 2 grows it back with comparable-quality signals and adds a second quality gate.  |
| 3     | 2.7 session expansion, 2.8 AVWAP, 2.9 calendar | Asset-class-specific tuning; some need extra data. |

A reasonable success criterion for Phase 1 alone, replayed on
the existing 720d basket:

- Aggregate win rate moves from **29.6% → 38–42%**
- Mean instrument return moves from **−0.99% → +1 to +3%**
- Worst DD shrinks from **−8.6% → −5 to −6%**
- Sample size drops modestly: **250 → ~170 trades**

Phase 2 is then expected to bring the sample size back to ~280–320 with
similar or better win rate.

These targets are deliberate, not promises — the actual numbers depend
on which instruments stay in the basket. EUR/GBP and HG (copper)
should probably be dropped regardless: the walk-forward analysis already
showed they fail in-sample-and-out, which is a strategy-fit problem
rather than a parameter-tuning problem.

---

## 4. What to avoid

A few patterns that look attractive but the literature flags as net
negative:

- **Aggressive breakeven-at-entry stops.** ([atas.net](https://atas.net/blog/break-even-in-trading/))
  cite Quantified Strategies research showing this *cuts* long-term
  profitability for trend-following. The version proposed in §2.2 is
  different: it acts only after a partial profit is already booked.
- **One ATR multiplier across all instruments.** Already covered in §2.3 — see the [quantstrategy.io](https://quantstrategy.io/blog/optimizing-atr-multipliers-backtesting-strategies-for/) note that calls this a "fundamental error".
- **Trading the actual news release.** The win-rate uplift comes from
  *avoiding* news, not chasing it.
- **AI-driven secondary classifier on this dataset.** The dataset is
  small (≤ 250 trades); an ML filter would just memorise. Defer until the
  strategy itself produces a meaningful number of trades.

---

## 5. References

All citations are inline above. The recurring sources are:

- TradingView script descriptions (community-maintained)
- Quantified Strategies (via `atas.net`, `quantifiedstrategies.substack.com`)
- `quantstrategy.io` blog series on ATR-stop tuning
- LuxAlgo, Mind Math Money, The Robust Trader on ADX/200-EMA filters
- ICT/SMC primary sources (`smartmoneyict.com`, `damnpropfirms.com`)
- `supertrader.me` on economic-event filters
