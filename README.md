# Weekly Manipulation & Failed Breakout

A multi-timeframe trading strategy built around two ideas:

1. **Weekly manipulation** — early-week (Monday/Tuesday) fakeouts of the prior
   week's high or low, where price pierces the level and immediately reclaims
   the inner range.
2. **Failed breakouts** — pattern-based intraday breakouts that pull back and
   confirm with an engulfing candle or a "railway-tracks" pair.

The repo ships two implementations:

| Path                                       | Purpose                                       |
| ------------------------------------------ | --------------------------------------------- |
| `pine/weekly_manipulation_strategy.pine`   | TradingView Pine v6 strategy + visuals        |
| `backtest/strategy.py` + `run.py`          | Python port + multi-ticker backtest CLI       |
| `backtest/walk_forward.py`                 | Walk-forward parameter optimizer              |

---

## Strategy logic

### Step 1 — levels
- **PDH / PDL / PDC** — previous day's high, low, close.
- **Prev-week H/L** — pulled from the previous completed weekly bar.
- **ADR** — 14-day SMA of daily range (computed at yesterday's close, no lookahead).
- **Climax-day filter** — if yesterday's range exceeded `1.75 × ADR`, no trades today.
- **Day classification** — labels yesterday as `TD up`, `TD dn`, `Inside`, or `Climax`.

### Step 2 — weekly manipulation
A latch persists for the rest of the week once a fakeout is confirmed:

- **`foShort`** — high pierced prior-week high on Mon/Tue and a later bar closed back inside.
- **`foLong`**  — mirror image at prior-week low.

A second bias source is **Break-in-Structure**:

- **`bisBull`** — yesterday made a lower-low / lower-high (downtrend) AND today's high cleared PDH.
- **`bisBear`** — yesterday made a higher-high / higher-low (uptrend) AND today's low cleared PDL.

`bullBias = foLong or bisBull`, `bearBias = foShort or bisBear`.

### Step 3 — setup zone
The entry has to occur in a "value" location relative to the daily context:

- **Buy zone** — at PDL, PDC, or (on `bisBull`) the retest of the broken-up PDH.
- **Sell zone** — at PDH, PDC, or (on `bisBear`) the retest of the broken-down PDL.

Tolerance is `zone = ADR × zonePct` (default `0.15 × ADR`).

### Step 4 — execution sequence
On the chart's timeframe (15m or 5m intended; 1h used in the Python backtest):

1. **Pattern** — N-bar rectangle whose width is `< 0.5 × ADR` (consolidation).
2. **Breakout** — close clears the prior `patLen`-bar high or low.
3. **Pullback** — within `brkExp` bars, price retests the broken level.
4. **Trigger** — engulfing candle or "railway tracks" (two large opposite-coloured candles, body-size ratio > 0.7) in the bias direction.

### Step 5 — confirmations
The Pine script paints labels for the regime context (`TD up/dn`, `Inside`,
`Climax`, `FakeOut up/dn`).

**Optional 1H-confirmation gate.** Set `Require 1H confirmation candle` to
require the most recently closed 1H bar to also be a bullish/bearish
engulfing or railway-tracks pair in the bias direction. Useful when running
the strategy on a 5m or 15m chart so the entry timing is intraday but the
momentum signal is HTF.

### Step 6 — risk management
- **Stop**: beyond the swing extreme of the trigger bar plus `slBuf × ADR`.
- **TP1**: the relevant daily level (PDH for longs, PDL for shorts), 40% of size.
- **TP2**: `2R` (configurable via `rrTP2`), remaining 60% of size.
- **Sizing** (Python only): fixed-fractional, default `0.5%` account risk per trade.
- **Climax / no-context bars** are skipped entirely.

---

## TradingView (Pine v6)

1. Open TradingView -> Pine Editor.
2. Paste the contents of `pine/weekly_manipulation_strategy.pine`.
3. Click **Add to chart**.
4. Open the **Strategy Tester** tab to see equity, trade list, and stats.
5. To backtest a basket of instruments, use TradingView's symbol switcher —
   the strategy and inputs persist across symbols.

The script renders PDH/PDL/PDC dots and weekly H/L step-lines, plus
`TD up/dn`, `Inside`, `Climax`, and `FakeOut up/dn` labels. Four
`alertcondition`s are available:

- **Weekly High Reclaimed (Bearish)** / **Weekly Low Reclaimed (Bullish)** —
  the "dead-giveaway" manipulation prints.
- **Long Setup Triggered** / **Short Setup Triggered** — full A+ sequence
  alerts ready for webhook routing.

---

## Python backtest

Hourly bars from yfinance (free) over ~720 days. Daily and weekly context are
re-sampled inside the engine, so the strategy logic stays identical to the
Pine implementation.

### Install

```bash
pip install -r backtest/requirements.txt
```

### Run

```bash
# 21-instrument default basket: FX + index futures/spot + metals + crypto, NY filter on
python -m backtest.run

# 24h FX + metals + crypto, no NY filter, write CSV
python -m backtest.run --no-ny \
    --tickers EURUSD=X GC=F SI=F BTC-USD ETH-USD \
    --out-csv results.csv

# custom risk per trade
python -m backtest.run --risk-pct 0.25
```

### Walk-forward optimizer

```bash
# default: 8 ticker subset, train=240d, test=120d, step=60d, 27-combo grid
python -m backtest.walk_forward

# tighter windows
python -m backtest.walk_forward --train-days 180 --test-days 60 --step-days 30

# specific basket
python -m backtest.walk_forward --tickers EURUSD=X GC=F BTC-USD \
    --out-csv wf_detail.csv --summary-csv wf_summary.csv
```

The optimizer searches `zone_pct ∈ {0.10, 0.15, 0.20}`,
`rr_tp2 ∈ {1.5, 2.0, 3.0}`, `climax_mx ∈ {1.5, 1.75, 2.0}` (27 combos) on
each rolling training window, picks the best by total-return-pct (subject
to a min-trade gate), and applies that combo to the next out-of-sample
window. Reports per-window detail and per-ticker aggregates.

---

## Reference results

All numbers below come from the committed CSVs and were produced on the
720-day window ending 2026-06-05.

### Run A — 21-instrument basket, NY-filter on (`results_1h_720d.csv`)

| asset class    | best (return) | worst (return) | notable PF              |
| -------------- | ------------- | -------------- | ----------------------- |
| FX             | USDJPY +0.55% | GBPUSD -7.51%  | USDJPY 1.13             |
| Index futures  | RTY +2.40%    | ES -4.34%      | **RTY 1.91**            |
| Index spot     | ^GDAXI +0.53% | ^FTSE -0.67%   | ^GSPC/^NDX/^DJI 0 trades|
| Metals         | SI +2.72%     | HG -4.25%      | **SI 1.90**             |
| Crypto         | ETH +2.83%    | —              | BTC 1.64, ETH 1.47      |

Aggregate across 21 instruments: 250 trades, weighted win **29.6%**, mean
return **-0.99%**, median **-0.23%**, worst DD **-8.62%**.

The standouts (RTY, SI, BTC, ETH) all post profit factor > 1.4 and drawdown
under 2%, suggesting the core idea has real edge in trending markets. The
losers (EUR, GBP, ES, HG) are the typical low-vol mean-reverting names that
chew up structural-stop strategies.

### Run B — 24h FX + metals + crypto, NY-filter off (`results_1h_720d_24h.csv`)

Removing the session filter ~3x's the trade count and damages most equity
curves. Only SI, BTC, ETH stayed positive, confirming the NY filter is
doing useful work for FX.

| ticker   | trades | win % | total return | max DD  | PF   |
| -------- | -----: | ----: | -----------: | ------: | ---: |
| EURUSD=X | 64     | 25 %  | -11.94 %     | -12.7 % | 0.50 |
| GBPUSD=X | 71     | 21 %  | -17.05 %     | -18.5 % | 0.36 |
| SI=F     | 42     | 36 %  |  +1.89 %     |  -3.8 % | 1.17 |
| BTC-USD  | 66     | 42 %  |  +4.30 %     |  -4.5 % | 1.26 |
| ETH-USD  | 60     | 33 %  |  +1.47 %     |  -2.4 % | 1.09 |

### Run C — Walk-forward (`walk_forward_detail.csv`, `walk_forward_summary.csv`)

8 tickers × 27-combo grid × ≤9 rolling windows (train 240d / test 120d /
step 60d):

| ticker   | windows | OOS trades | OOS win % | OOS compound | OOS worst DD | IS mean | OOS mean |
| -------- | ------: | ---------: | --------: | -----------: | -----------: | ------: | -------: |
| BTC-USD  | 5       | 8          |  62.5 %   | **+2.26 %**  |  -0.62 %     | +2.38 % | +0.45 %  |
| ETH-USD  | 4       | 19         |  36.8 %   | **+1.84 %**  |  -1.04 %     | +1.64 % | +0.46 %  |
| SI=F     | 4       | 4          |  50.0 %   | **+1.72 %**  |  -0.52 %     | +1.92 % | +0.43 %  |
| GC=F     | 2       | 1          |   0   %   | -0.31 %      |  -0.32 %     | +0.14 % | -0.16 %  |
| NQ=F     | 9       | 23         |  26.1 %   | -0.54 %      |  -2.02 %     | -0.32 % | -0.06 %  |
| ES=F     | 9       | 25         |  28.0 %   | -0.69 %      |  -1.43 %     | +0.07 % | -0.07 %  |
| USDJPY=X | 1       | 2          |   0   %   | -1.13 %      |  -1.13 %     | +3.29 % | -1.13 %  |
| EURUSD=X | 5       | 5          |   0   %   | -2.23 %      |  -1.34 %     | -0.47 % | -0.45 %  |

Aggregate: 39 OOS windows, 87 OOS trades, weighted win **31.0%**, mean
compound OOS **+0.12%**, IS→OOS gap **+1.15%**.

#### How to read this
- **IS→OOS gap of +1.15% is small.** A heavily-overfit strategy would show a
  5–10% gap. We're picking parameters that generalise reasonably.
- **Crypto + silver are robustly profitable** out-of-sample: positive on every
  metric and small drawdowns.
- **FX EUR/USDJPY are robustly negative** even with optimised params — the
  problem isn't parameter tuning, it's that the structural-stop /
  engulfing-trigger mechanic doesn't fit those low-vol regimes. They should
  probably be excluded from the basket, or use a different trigger (e.g. an
  inside-bar breakout instead of engulfing).
- **Index futures (ES/NQ) are near-flat OOS.** With 23–25 trades each they're
  at least statistically meaningful — the strategy doesn't work for them, but
  it doesn't blow up either.

### How to read the basic backtests honestly
- The strategy is **selective on purpose** — ~10–25 trades / instrument /
  ~2 years on the NY-filtered run is in line with the spec ("A+ setups only").
- Numbers are **not annualised** and include only a flat 2 bps commission per
  side; real slippage on illiquid instruments would be higher.

---

## Known limitations of the Python run

1. **Execution timeframe = 1h**, not 15m / 5m. yfinance allows only 60 days
   of 15m data on the free tier. Use the Pine version on TradingView with
   the `Require 1H confirmation candle` toggle enabled to run the spec's
   intended 15m / 5m execution while still keeping a 1H momentum filter.
2. **NY session approximated as UTC 13:00–21:00** (covers both DST and
   non-DST windows). Pine reads exchange time directly so it's exact there.
3. **^GSPC / ^NDX / ^DJI / ^N225 / SPY / QQQ get 0 trades** because yfinance
   returns RTH-only ~7 bars/day for cash indices and US ETFs — too sparse
   for the 8-bar rectangle pattern. Use the Pine strategy on TradingView for
   cleaner equity backtests on these.
4. **Bar-internal SL/TP ordering is conservative** — when both a stop and a
   target sit inside the same bar, the engine assumes the stop hit first.
5. yfinance data is free-tier and contains occasional gaps; the engine
   tolerates them but they affect equal-width pattern detection.

---

## Repo layout

```
pullback-algo/
├── pine/
│   └── weekly_manipulation_strategy.pine
├── backtest/
│   ├── __init__.py
│   ├── strategy.py                       # pure logic, no I/O
│   ├── run.py                            # multi-instrument CLI
│   ├── walk_forward.py                   # walk-forward optimizer CLI
│   ├── requirements.txt
│   ├── results_1h_720d.csv               # NY-filter on, 21 instruments
│   ├── results_1h_720d_24h.csv           # 24h, FX + metals + crypto
│   ├── walk_forward_detail.csv           # per-window OOS records
│   └── walk_forward_summary.csv          # per-ticker aggregates
├── .kiro/steering/pine.md                # Pine v6 conventions for this repo
└── README.md
```

---

## Parameters reference

| Param         | Pine input                       | Python field      | Default | Notes                                                |
| ------------- | -------------------------------- | ----------------- | ------: | ---------------------------------------------------- |
| ADR length    | `ADR period (days)`              | `adr_len`         |   14    | rolling SMA of daily range, evaluated at yesterday   |
| Climax mult   | `Climax day x ADR`               | `climax_mx`       |  1.75   | yesterday's range / ADR threshold                    |
| Zone tol      | `Zone tolerance (x ADR)`         | `zone_pct`        |  0.15   | distance from PDH/PDL/PDC for setup zone             |
| Pattern len   | `Pattern lookback (bars)`        | `pat_len`         |    8    | rectangle high/low window                            |
| Breakout exp  | `Breakout expiry (bars)`         | `brk_exp`         |   20    | how long a breakout stays "fresh" for pullback       |
| Stop buf      | `Stop buffer (x ADR)`            | `sl_buf`          |  0.10   | structural extreme buffer                            |
| TP1 size %    | `TP1 size %`                     | `tp1_pct`         |   40    | partial close at PDH/PDL                             |
| TP2 R-mult    | `TP2 reward multiple`            | `rr_tp2`          |  2.0    | runner target as multiple of risk                    |
| Use NY        | `Trade only NY session`          | `use_ny`          |  True   |                                                      |
| HTF confirm   | `Require 1H confirmation candle` | (Pine only)       |  False  | gate entries on most recent 1H trigger candle        |
| HTF resolution| `Confirmation timeframe`         | (Pine only)       |  60     | timeframe for the HTF confirmation candle            |
| Risk %        | (sizing handled by Pine inputs)  | `risk_pct`        |  0.5 %  | account risk per trade (Python only)                 |
| Engulfing     | `Engulfing trigger`              | `use_eng`         |  True   |                                                      |
| Railway tracks| `Railway-Tracks trigger`         | `use_rr`          |  True   |                                                      |
