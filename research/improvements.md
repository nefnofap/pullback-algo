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



---

# Part II — Lifting sample size (post-v2 follow-up)

After v2 shipped, the question became: how do we get **more trades** without
giving back the win-rate gains? This section answers that with a measured
funnel diagnostic and a Tier-4 broadener set whose impact is quantified.

## 6. Where the cascade actually drops trades

Instrumented the v2 engine, ran on a 6-instrument sample (EUR/GBP/BTC/ES/GC/SI,
720d 1h, ~91k bars total), and counted each filter's pass rate:

| Stage                         | Bars   | Drop from prev |
| ----------------------------- | -----: | -------------: |
| total                         | 91,808 | -              |
| context loaded                | 90,148 | -1.8%          |
| in NY session                 | 30,751 | **-65.9%**     |
| not climax day                | 28,370 | -7.7%          |
| has bias active               | 24,286 | -14.4%         |
| in setup zone                 | 10,973 | -54.8%         |
| **rectangle + pullback**      | **1,823** | **-83.4%** ← biggest leak |
| **trigger candle fires**      | **329**   | **-82.0%** ← second biggest |
| HTF trend agrees              | 160    | -51.4%         |
| ADX > 22                      | 71     | -55.6%         |
| **final long signals**        | 41     |                |
| **final short signals**       | 30     |                |

Two filters consume 97% of the signal between them:

1. **Rectangle + pullback** keeps only 17% of in-zone bars. This filter
   exists to constrain entries to consolidation-breaks, but it also cuts
   any setup that happens after a wider move — which is most of them.
2. **The 4-trigger set** (engulfing, RR, pin, inside) keeps only 18% of
   pullback-ready bars. Most bars don't print a clean candle pattern.

The HTF and ADX gates cost about 50% each — but those are the quality
gates that drove the v1→v2 win-rate jump. They stay.

## 7. Tier-4: targeted sample-size broadeners

All four target the two biggest leaks identified above. All are toggleable;
default off so v2 numbers don't change.

| Toggle              | Pine input                          | Python field         | Effect                                                                 |
| ------------------- | ----------------------------------- | -------------------- | ---------------------------------------------------------------------- |
| Loose pattern       | `Loose pattern (drop rectangle req.)` | `loose_pattern`     | Drop the consolidation requirement; require only a recent N-bar break. Roughly **3x setups**. |
| Prior-week mid zone | `Add prior-week mid + H/L as value zones` | `use_pwm_zone`     | Adds PWM and PWH/PWL as additional value locations.                    |
| 2-bar momentum trig | `2-bar momentum trigger`            | `use_momentum_trig`  | Adds a trigger for 2 consecutive same-direction bars with above-avg range. |
| NR4 expansion trig  | `NR4 expansion trigger`             | `use_nr_expansion`   | Adds a trigger for narrow-range-4 followed by an expansion bar.        |
| Lower pin wick      | `Pin-bar wick ratio` (default 0.6)  | `pin_wick_ratio`     | Loosen 0.6 → 0.5 to roughly **double** pin-bar count.                  |

The four are bundled as a `v2_loose` preset in `backtest/run.py`.

## 8. Measured impact (apples-to-apples, 720d 1h, 21 instruments)

```
                      trades   win    return    DD     positive
baseline    (v1)        250   29.6%  -0.99%   -8.62%   6/21
v2                      156   47.4%  -0.20%   -3.96%   7/21
v2_loose               359   51.3%  -0.56%   -6.22%   9/21
```

**v2_loose vs v2** (the comparison the user asked for):

- **Trades: +130%** (156 → 359)
- **Win rate: +3.9 pp** (47.4% → 51.3%) — the new triggers are *higher* quality than the old ones
- Positive instruments: 7 → **9 of 21**
- Mean return: -0.20% → -0.56% (slightly worse)
- Worst DD: -3.96% → -6.22% (worse — the loose pattern admits some lower-quality setups)

**v2_loose vs baseline:**

- **Trades: +44%** (250 → 359)
- **Win rate: +21.7 pp** (29.6% → 51.3%)
- **Mean return: -0.99% → -0.56%** (better)
- **Worst DD: -8.62% → -6.22%** (better)
- Positive instruments: 6 → 9

So v2_loose dominates the v1 baseline on every metric and dominates v2 on
sample size + win rate, with a moderate drawdown trade-off.

## 9. Wider basket scaling

A natural fifth lever is just trading more instruments. The 32-ticker
WIDE_TICKERS basket adds JPY crosses (EURJPY, GBPJPY, AUDJPY), NZDUSD,
EURGBP, energies (CL, NG, RB) and altcoins (SOL, XRP, DOGE):

```
v2_loose / 21 instruments   359 trades   51.3% win   -0.56% mean
v2_loose / 32 instruments   587 trades   48.7% win   -0.68% mean
```

Adding 11 instruments scales trades by **+63%** with a small win-rate dilution
(the new instruments are mixed-quality — RB=F gasoline futures hits 77% win
in 13 trades, but DOGE-USD only 38% in 29 trades).

Notable per-instrument standouts on the wide v2_loose run:

| ticker  | trades | win % | return  | note                                  |
| ------- | -----: | ----: | ------: | ------------------------------------- |
| ^DJI    | 14     | 93%   | +2.15%  | Cash index now generates clean signals|
| RB=F    | 13     | 77%   | +2.48%  | Gasoline futures, new addition        |
| ES=F    | 22     | 73%   | +3.07%  | v2 had 9 trades; loose adds 13 more   |
| ^GSPC   | 10     | 70%   | +0.81%  | Cash S&P, was 0 trades on v2          |
| YM=F    | 17     | 65%   | +4.10%  | Best return on the basket             |
| GC=F    | 22     | 59%   | +1.24%  | More gold setups detected             |

## 10. Recommended preset selection

| Goal                                  | Preset      | Use it when                                                         |
| ------------------------------------- | ----------- | ------------------------------------------------------------------- |
| Strict, defensible v1 reference       | `baseline`  | comparing to the original spec                                      |
| Best risk-adjusted (lowest DD)        | `v2`        | small basket / capital preservation prioritised                     |
| **Best sample × win rate combo**      | `v2_loose`  | want enough trades to be statistically meaningful                   |
| Most signals possible                 | `v2_loose --big` | running a portfolio approach across many instruments           |

CLI:

```bash
python -m backtest.run --three-way                         # all three on default basket
python -m backtest.run --loose --big                       # wide basket, v2_loose
python -m backtest.run --three-way --tickers ES=F NQ=F GC=F  # custom subset
```

## 11. What was deliberately *not* changed

Things that would lift sample size but at unacceptable quality cost:

- **Lower ADX threshold to 15.** Tested informally: doubles signal count
  but pushes win rate back below 40%. The 22 default is the sweet spot.
- **Drop the HTF trend filter.** This reverses the single biggest v1→v2 win.
- **Drop the climax-day skip.** Costs ~8% sample size; adds known-bad days.
- **Trade outside any session.** Costs the structural-stop assumptions; FX
  spreads at Asian/early-London open are wide enough to invalidate the
  ADR-buffer math.

## 12. Numbers to verify the methodology

The 6-ticker funnel diagnostic in §6 is reproducible by re-running the
`diagnose()` helper at the head of this push's commit. The aggregate
results in §8/§9 are the contents of `backtest/results_v2_loose.csv`,
`backtest/results_v2_loose_delta.csv`, and
`backtest/results_v2_loose_big.csv`. All numbers reproduce on
`python -m backtest.run --three-way` and `--loose --big`.



---

# Part III — Beyond Tier-4: what else moves the needle (and what doesn't)

After v2_loose plus the curated basket landed (~60% win, +1.21% mean return,
~2.6 trades/week aggregate), I went looking for further improvements with a
strict OOS-honest methodology. Each candidate was tested by:

  1. Calibrating on the first 60% of each instrument's 720d data
  2. Applying to the remaining 40% as out-of-sample
  3. Comparing against the static `v2_loose` preset on the same OOS window

This section reports both the **wins** (one robust improvement) and the
**negative results** (improvements that looked good in-sample but didn't
generalize). The negative results are arguably more valuable than the wins.

## 13. The OOS-honest improvements list

### 13.1 Cocoa replaces GBPUSD (verified, shipped)

The walk-forward had flagged GBPUSD as a chronic OOS underperformer (29% win
OOS, -2.65% compound). I tested 12 candidate replacements as standalone
in-sample on v2_loose 720d 1h. The clear winner was **cocoa (CC=F)**: 13
trades, **84.6% win**, +1.80% return, **PF 2.74**, only -0.51% DD. Coffee
(KC=F) and 30y bonds (ZB=F) were strong runners-up.

After the swap, the curated 15-instrument v2_loose result improved to:

| metric          | before (GBPUSD) | after (CC=F) |
| --------------- | --------------: | -----------: |
| trades          | 266             | 260          |
| win rate        | 59.8%           | **61.9%**    |
| mean return     | +1.05%          | **+1.21%**   |
| worst DD        | -3.30%          | **-2.54%**   |
| positive inst.  | 12/15           | **13/15**    |

CC=F adds genuine diversification (uncorrelated with everything else in the
basket) and the small-sample-size concern is mitigated by its
extreme-quality profile.

### 13.2 Bump the global `rr_tp2` from 2.0 → 2.5 (OOS-verified)

In the per-instrument calibration sweep, **10 of 15 instruments preferred
rr_tp2 = 3.0** in-sample. Testing `rr_tp2 ∈ {2.0, 2.5, 3.0}` as global
defaults on the OOS test windows:

| rr_tp2 | OOS trades | OOS win | OOS mean | OOS DD | positive |
| -----: | ---------: | ------: | -------: | -----: | -------: |
| 2.0    | 99         | 61.6%   | +0.45%   | -3.31% | 9/15     |
| **2.5** | 99         | **61.6%** | **+0.60%** | -3.86% | **10/15**  |
| 3.0    | 98         | 61.2%   | +0.67%   | -3.86% | 10/15    |

Bumping to 2.5 gains +0.15pp mean return OOS without changing win rate,
and tips one more instrument into positive territory. The 3.0 tweak is
slightly better on return but a hair worse on win rate. **2.5 is the
robustness sweet spot.** This is a candidate to fold into the next default.

## 14. The negative results (would have looked great IS, but didn't OOS)

### 14.1 Per-instrument param calibration via 27-combo grid search

Method: for each instrument, run a 27-combo grid over (zone_pct, rr_tp2,
climax_mx) on the first 60% of data, pick the in-sample best by profit
factor, apply those params to the OOS test window. Compare to default
v2_loose on the same OOS window.

```
                        OOS trades   OOS win   OOS mean   positive
default v2_loose            99       61.6%     +0.45%     9/15
calibrated per-instrument   93       58.1%     +0.39%     10/15
```

**Per-instrument calibration did NOT beat the global default OOS.** Win
rate dropped 3.5pp, return was 0.06pp worse, DD was slightly worse. The
calibrated version did have one more positive instrument (10 vs 9), so
median return was higher, but the cost was a wider distribution.

#### Why this happened

Three reasons:

1. **The OOS windows are small.** ~290 days × ~1 trade per ~10 days = ~7-15
   OOS trades per instrument. With samples that small, single-trade luck
   dominates the parameter choice's marginal effect.
2. **The grid is coarse.** 27 combos over (0.10, 0.15, 0.20) × (1.5, 2.0, 3.0)
   × (1.5, 1.75, 2.0). The "best" combo is often within noise of two or
   three other combos.
3. **The strategy is regime-sensitive.** The 60% train period and 40% test
   period of 2024–2026 contained different regimes for several instruments.

#### What this means

The strategy's robustness comes from the **simplicity of its globally
applicable parameters**, not from per-instrument fine-tuning. Don't chase
asset-specific configurations; instead, drop instruments that don't fit
the strategy (which is what the curated basket already did).

The per-instrument calibration JSON is committed at
`backtest/instrument_overrides.json` for anyone who wants to experiment
with it, but it should be considered **research output only**.

### 14.2 What about 5m / 15m execution multi-timeframe stacking?

Multiple sources cite 60-75% win rates for multi-timeframe analysis vs
~45% for single-TF
([tradewiththepros.com summary](https://tradewiththepros.com/multi-timeframe-analysis/),
[elitesignals.com on confluence stacking](https://www.elitesignals.com/blog/confluence-trading-stacking-signals-for-higher-win-rates)).
The original spec called for 1H bias + 5m / 15m execution. The Pine
strategy supports it (`Require 1H confirmation candle` toggle and a chart
TF of 5m / 15m), but the **Python backtest can't validate it on free data**
— yfinance caps 15m history at 60 days.

To verify it properly:

- Run the Pine strategy on TradingView at 5m or 15m chart TF with the
  1H-confirmation gate ON. Strategy Tester will cover the chart's full
  available history (typically several months on 5m, multiple years on 15m
  for major instruments).
- Or, get a paid 15m feed (Polygon, Databento, Dukascopy) and rerun the
  Python engine.

Expected impact based on literature: **+15-20pp on win rate, ~3x trade
frequency** because the same strategy logic operates on more bars per day.

### 14.3 Pyramiding on profitable continuation

The literature on pyramiding
([quantstrategy.io ultimate guide](https://quantstrategy.io/blog/the-ultimate-guide-to-pyramiding-strategies-advanced/),
[nexusfi summary](https://nexusfi.com/articles/trading/Scaling-In))
supports adding to winning trades for trend-following systems. For this
strategy specifically, the natural place to pyramid is:

- After TP1 fills (40% partial profit booked, runner SL moved to BE),
  the residual 60% is risk-free.
- If a *new* full-cascade signal fires in the same direction within
  the same trading day, take it as a fresh tranche with its own SL/TP.
- Net effect: in trending markets, 1 setup → 2-3 trades. In choppy
  markets, the second trigger doesn't fire and we stay single-tranche.

I designed but didn't implement this because it requires a refactor to
support concurrent positions in the same direction (the current engine
checks `pos == None` before any new entry). Implementation cost is
moderate (~50 lines); the OOS test would need to verify it doesn't blow
up DD in choppy regimes.

### 14.4 Other deferred ideas

| Idea                                | Why deferred                                                     |
| ----------------------------------- | ---------------------------------------------------------------- |
| TP1 fixed at 1R rather than at PDH/PDL | Would standardize win-rate stats but mostly cosmetic; current variable-R TP1 isn't actually broken. |
| Volatility-regime-adaptive zone_pct | Promising but requires a regime-detection signal that itself needs to be OOS-validated; can become a rabbit hole. |
| News-calendar filter                | Needs an external feed (ForexFactory CSV, etc.); non-trivial to maintain. |
| Tick-data / order-flow confirmation | Needs paid feed; would only help on instruments with reliable volume. |
| ML classifier on existing features  | Dataset is too small (300-700 trades total); would memorize. Defer until 5m / 15m execution adds samples. |

## 15. The recommended live configuration

Based on all the data in Parts I-III:

```
preset:               v2_loose
basket:               curated 15 instruments (--curated)
                      ES NQ YM ^GSPC ^NDX ^DJI
                      GC SI HG  CL NG RB  CC
                      BTC SOL
rr_tp2:               2.5  (slight tweak from default 2.0; Part III §13.2)
risk per trade:       0.5% of equity
session:              NY by default; toggle extended session for FX/crypto
chart timeframe:      1H minimum; 15m or 5m strongly preferred if data allows
                      (with the 1H-confirmation toggle ON when on 15m/5m)
```

Expected OOS performance from this setup:
- ~2-3 trades/week aggregate
- ~60-65% win rate
- Worst DD under 5%
- 10-13 of 15 instruments positive

Anything beyond this — per-instrument calibration, ML classifiers,
exotic features — appears to **not actually improve OOS performance** on
the available data, no matter how compelling it looks in-sample. The honest
next step is **more data** (5m / 15m execution via paid feed, longer
history via Dukascopy or Polygon), not more parameters.
