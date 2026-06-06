# Weekly Manipulation & Failed Breakout

A multi-timeframe trading strategy built around two ideas:

1. **Weekly manipulation** — early-week (Monday/Tuesday) fakeouts of the prior
   week's high or low, where price pierces the level and immediately reclaims
   the inner range.
2. **Failed breakouts** — pattern-based intraday breakouts that pull back and
   confirm with an engulfing candle or a "railway-tracks" pair.

The repo ships two implementations:

| Path                                       | Purpose                                  |
| ------------------------------------------ | ---------------------------------------- |
| `pine/weekly_manipulation_strategy.pine`   | TradingView Pine v6 strategy + visuals   |
| `backtest/`                                | Python backtest engine + multi-ticker runner |

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
`Climax`, `FakeOut up/dn`) so you can eyeball whether the auto-detected events
line up with the manual reading.

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
3. Click **Add to chart**. The strategy renders PDH/PDL/PDC dots and weekly H/L
   step-lines, plus `TD`, `Inside`, `Climax`, and `FakeOut` labels.
4. Open the **Strategy Tester** tab to see equity, trade list, and stats.
5. To backtest a basket of instruments, use TradingView's *symbol switcher* —
   the strategy and inputs persist across symbols.

The script exposes four `alertcondition`s:

- *Weekly High Reclaimed (Bearish)* and *Weekly Low Reclaimed (Bullish)* —
  the "dead-giveaway" manipulation prints.
- *Long Setup Triggered* and *Short Setup Triggered* — full A+ sequence
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
# default basket: FX + ETFs + futures + crypto, NY-session filter on
python -m backtest.run

# 24h FX + crypto, no NY filter, write CSV
python -m backtest.run --no-ny \
    --tickers EURUSD=X GBPUSD=X BTC-USD ETH-USD \
    --out-csv results.csv

# custom risk per trade
python -m backtest.run --risk-pct 0.25
```

### Output

A per-ticker summary table plus aggregates:

```
ticker     bars  trades  win_rate  avg_R  total_return_pct  max_dd_pct  profit_factor
EURUSD=X  17001     16     0.125  -0.659           -5.269      -5.801          0.199
USDJPY=X  16904     15     0.400   0.074           +0.552      -1.853          1.132
BTC-USD   17242     13     0.538   0.306           +1.991      -1.548          1.641
ETH-USD   17239     23     0.348   0.246           +2.833      -1.041          1.470
...
```

---

## Observed multi-instrument results

Two reference runs from the current code are committed under `backtest/`.

### Run A — `results_1h_720d.csv` (NY-filter on, 10 instruments)

| ticker   | trades | win % | total return | max DD | PF   |
| -------- | -----: | ----: | -----------: | -----: | ---: |
| EURUSD=X | 16     | 12 %  | -5.27 %      | -5.8 % | 0.20 |
| GBPUSD=X | 20     | 15 %  | -7.51 %      | -8.6 % | 0.18 |
| AUDUSD=X | 14     | 29 %  | -1.67 %      | -3.0 % | 0.67 |
| USDJPY=X | 15     | 40 %  | +0.55 %      | -1.9 % | 1.13 |
| ES=F     | 27     | 26 %  | -4.34 %      | -5.0 % | 0.55 |
| NQ=F     | 19     | 37 %  | -0.23 %      | -3.9 % | 0.95 |
| BTC-USD  | 13     | 54 %  | +1.99 %      | -1.5 % | 1.64 |
| ETH-USD  | 23     | 35 %  | +2.83 %      | -1.0 % | 1.47 |
| SPY/QQQ  | 0      | —     | 0 %          | 0 %    | —    |

Aggregate: 147 trades, weighted win 30 %, mean return -1.37 %, worst DD -8.6 %.

### Run B — `results_1h_720d_24h.csv` (NY-filter off, FX + crypto)

Removing the session filter ~3x's the trade count but hurts FX equity (the
session filter is doing useful work). Crypto stays positive.

| ticker   | trades | win % | total return | max DD  | PF   |
| -------- | -----: | ----: | -----------: | ------: | ---: |
| EURUSD=X | 64     | 25 %  | -11.9 %      | -12.7 % | 0.50 |
| GBPUSD=X | 71     | 21 %  | -17.0 %      | -18.5 % | 0.36 |
| BTC-USD  | 66     | 42 %  | +4.30 %      |  -4.5 % | 1.26 |
| ETH-USD  | 60     | 33 %  | +1.47 %      |  -2.4 % | 1.09 |

### How to read these numbers honestly

- The strategy is **selective on purpose**. ~10–25 trades / instrument /
  ~2 years on the NY-filtered run is in line with the spec ("A+ setups only").
- Crypto profile (positive expectancy, PF > 1) and USDJPY (~1R-neutral,
  slight positive equity) suggest the core idea has a real edge in
  trending / volatile regimes.
- EUR/GBP losses suggest the structural-stop placement and the engulfing
  trigger threshold are not yet tuned for low-volatility ranges. This is
  exactly the kind of thing to iterate on per-instrument in the
  TradingView optimizer.
- Numbers are **not** annualized and do **not** include slippage beyond a
  flat 2 bps commission per side.

### Known limitations of the Python run

1. **Execution timeframe = 1h**, not 15m / 5m. yfinance only allows 60 days
   of 15m data on the free tier. Use the Pine version on TradingView to
   test the spec's intended 15m / 5m execution.
2. **NY session approximated as UTC 13:00–21:00** to cover both DST and
   non-DST windows. Pine reads exchange time directly.
3. **SPY / QQQ got zero trades** because yfinance returns RTH-only bars
   (~7 / day), which is too sparse for the rectangle-pattern logic. Use
   the Pine strategy on TradingView for cleaner equity backtests.
4. **Bar-internal SL/TP ordering is conservative**: when both a stop and a
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
│   ├── strategy.py                    # pure logic, no I/O
│   ├── run.py                         # multi-instrument CLI
│   ├── requirements.txt
│   ├── results_1h_720d.csv            # reference run (NY-filter on)
│   └── results_1h_720d_24h.csv        # reference run (24h)
├── .kiro/steering/pine.md             # Pine v6 conventions for this repo
└── README.md
```

---

## Parameters reference

| Param      | Pine input                  | Python field      | Default | Notes                                                |
| ---------- | --------------------------- | ----------------- | ------: | ---------------------------------------------------- |
| ADR length | `ADR period (days)`         | `adr_len`         |   14    | rolling SMA of daily range, evaluated at yesterday   |
| Climax mult| `Climax day x ADR`          | `climax_mx`       |  1.75   | yesterday's range / ADR threshold                    |
| Zone tol   | `Zone tolerance (x ADR)`    | `zone_pct`        |  0.15   | distance from PDH/PDL/PDC for setup zone             |
| Pattern    | `Pattern lookback (bars)`   | `pat_len`         |    8    | rectangle high/low window                            |
| Breakout exp| `Breakout expiry (bars)`   | `brk_exp`         |   20    | how long a breakout stays "fresh" for pullback       |
| Stop buf   | `Stop buffer (x ADR)`       | `sl_buf`          |  0.10   | structural extreme buffer                            |
| TP1 size % | `TP1 size %`                | `tp1_pct`         |   40    | partial close at PDH/PDL                             |
| TP2 R-mult | `TP2 reward multiple`       | `rr_tp2`          |  2.0    | runner target as multiple of risk                    |
| Use NY     | `Trade only NY session`     | `use_ny`          |  True   |                                                      |
| Risk       | (sizing handled by Pine)    | `risk_pct`        |  0.5 %  | account risk per trade (Python only)                 |
| Engulfing  | `Engulfing trigger`         | `use_eng`         |  True   |                                                      |
| RR pair    | `Railway-Tracks trigger`    | `use_rr`          |  True   |                                                      |
