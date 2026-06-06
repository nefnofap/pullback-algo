# Weekly Manipulation & Failed Breakout

A multi-timeframe trading strategy built around two ideas:

1. **Weekly manipulation** — early-week (Monday/Tuesday) fakeouts of the prior
   week's high or low, where price pierces the level and immediately reclaims
   the inner range.
2. **Failed breakouts** — pattern-based intraday breakouts that pull back and
   confirm with an engulfing candle, "railway-tracks" pair, pin bar, or
   inside-bar break.

This repo ships the strategy in two flavours plus full multi-instrument
backtest tooling:

| Path                                       | Purpose                                       |
| ------------------------------------------ | --------------------------------------------- |
| `pine/weekly_manipulation_strategy.pine`   | TradingView Pine v6 strategy + visuals        |
| `backtest/strategy.py` + `run.py`          | Python port + multi-ticker backtest CLI       |
| `backtest/walk_forward.py`                 | Walk-forward parameter optimizer              |
| **`signals/service.py`**                   | **24/7 multi-instrument live signal service** |
| [`research/improvements.md`](research/improvements.md) | Deep-research note: failure-mode analysis + ranked improvements |

---

## v2: what changed

The first iteration scored 30% win rate with mean returns near zero and
chronic counter-trend skew on equity indices. The
[research note](research/improvements.md) identified three concrete leaks
(directional skew on trending markets, TP1→TP2 conversion bleed, stops too
tight on low-vol pairs) and an under-spec'd trigger set. **v2 fixes all of
them.** Every feature is toggleable; defaults are on.

### Tier 1 — biggest leaks

| Feature              | Pine input                        | Python field         | Default |
| -------------------- | --------------------------------- | -------------------- | :-----: |
| Daily 200-EMA trend filter | `Daily trend filter (200 EMA)` | `use_htf_trend`     | True    |
| Runner SL → BE+offset after TP1 fills | `Move runner SL to BE+offset after TP1` | `runner_be` | True    |
| ATR-multiple stop combined with structural extreme | `Combine with ATR-multiple stop` | `use_atr_stop` | True    |

### Tier 2 — quality + sample size together

| Feature              | Pine input                        | Python field         | Default |
| -------------------- | --------------------------------- | -------------------- | :-----: |
| ADX > 22 trend-strength gate | `ADX trend-strength gate`     | `use_adx_filter`    | True    |
| Pin-bar trigger      | `Pin-bar trigger`                 | `use_pin_bar`       | True    |
| Inside-bar-break trigger | `Inside-bar-break trigger`    | `use_inside_break`  | True    |
| Day-level sweep & reclaim latch (mirror of weekly) | `Day-level sweep latch (PDH/PDL)` | `use_day_sweep` | True |

### Tier 3 — instrument-class specific

| Feature              | Pine input                        | Python field         | Default |
| -------------------- | --------------------------------- | -------------------- | :-----: |
| Extended session (London + NY) | (use the `nySess` input) | `extended_session`  | False   |
| Daily-bar swing variant for long-history runs | (Pine: change chart TF to D) | `daily_mode`        | False   |

### Tier 4 — sample-size broadeners (v2_loose preset)

Targeted at the two filter stages that consume 97% of signals (rectangle
+ pullback at -83% drop, trigger candle at -82% drop). Default off so v2
numbers stay stable; opt in via `--loose` or `--three-way`. See
[research/improvements.md §6-12](research/improvements.md) for the funnel
diagnostic that justifies these.

| Feature              | Pine input                          | Python field         | Default |
| -------------------- | ----------------------------------- | -------------------- | :-----: |
| Loose pattern (drop rectangle req.) | `Loose pattern (drop rectangle req.)` | `loose_pattern`   | False   |
| Prior-week mid + H/L value zones | `Add prior-week mid + H/L as value zones` | `use_pwm_zone` | False   |
| 2-bar momentum trigger | `2-bar momentum trigger`          | `use_momentum_trig`  | False   |
| NR4 expansion trigger | `NR4 expansion trigger`            | `use_nr_expansion`   | False   |
| Pin-bar wick threshold (0.6 → 0.5 in loose) | `Pin-bar wick ratio` | `pin_wick_ratio` | 0.6 (0.5 in loose) |

---

## Apples-to-apples backtest: v1 vs v2 vs v2_loose

### Curated 15-instrument basket (recommended)

The 15 positive-performers basket from the wide-basket study:
ES, NQ, YM index futures + ^GSPC, ^NDX, ^DJI cash indices + GC, SI, HG metals
+ CL, NG, RB energies + GBPUSD + BTC, SOL crypto. Use `--curated`.

```
                      trades   win    return    DD     positive
baseline (v1)           211   28.9%  -1.48%   -8.62%   3/15
v2                      119   54.6%  +0.31%   -2.15%   8/15
v2_loose                266   59.8%  +1.05%   -3.30%   12/15
```

**v2_loose on the curated basket: 59.8% win rate, +1.05% mean return, only
-3.30% worst DD, 12 of 15 instruments positive.** That's the cleanest result
in the whole study.

### Walk-forward OOS validation (curated basket, v2_loose preset)

```
  15 instruments, 240d train / 120d test / 60d step, 27-combo grid
  122 OOS windows, 234 OOS trades
  weighted win rate (OOS)   : 56.4%
  mean compound (OOS)       : +0.26%
  IS -> OOS gap (mean)      : +0.61%   <- small = generalises
```

The +0.61% IS→OOS gap is the key number — heavily-overfit strategies show
5-10pp drops; we lose 0.6pp. Best OOS performers: BTC (69% win, +5.74% compound),
GC (72% win, +2.22%), NG (63% win, +1.52%), HG (67% win, +1.07%),
NQ (69% win), ES (59% win). The one OOS underperformer is GBPUSD (29% win,
-2.64%) — even with v2_loose the FX inclusion drags. Consider replacing it.

### Default 21-instrument basket (broader but lower quality)

Same 720-day 1h window, all 21 instruments including the chronic losers:

```
                      trades   win    return    DD     positive
baseline (v1)           250   29.6%  -0.99%   -8.62%   6/21
v2                      156   47.4%  -0.20%   -3.96%   7/21
v2_loose                359   51.3%  -0.56%   -6.22%   9/21
```### v2_loose vs v2 — the sample-size answer

- **Trades: +130%** (156 → 359)
- **Win rate: +3.9 pp** (47.4% → 51.3%) — the new triggers are *higher* quality
- Positive instruments: 7 → **9 of 21**
- Mean return: -0.20% → -0.56% (slightly worse)
- Worst DD: -3.96% → -6.22% (the loose pattern admits more setups, some marginal)

### Wider basket: v2_loose on 32 instruments

Adding JPY crosses, NZDUSD, EURGBP, energies (CL, NG, RB) and altcoins:

```
v2_loose / 32 instruments   587 trades   48.7% win   -0.68% mean
```

**~3.8x the v2 trade count** with a 1.3pp win-rate dip. Standouts on the
wide basket:

| ticker  | trades | win   | return | note                              |
| ------- | -----: | ----: | -----: | --------------------------------- |
| ^DJI    | 14     | 93%   | +2.15% | Cash index, was 0 trades on v2    |
| RB=F    | 13     | 77%   | +2.48% | Gasoline futures (new)            |
| ES=F    | 22     | 73%   | +3.07% | v2 had 9; loose finds 13 more     |
| YM=F    | 17     | 65%   | +4.10% | Best return on the basket         |
| GC=F    | 22     | 59%   | +1.24% | More gold setups detected         |

### Standout v1→v2 improvements (unchanged)

| ticker   | trades (b/v2) | win % (b/v2)  | return (b/v2)        | PF (b/v2)   |
| -------- | ------------- | ------------- | -------------------- | ----------- |
| GBPUSD=X | 20 → 9        | 15% → **67%** | -7.51% → **+1.60%**  | 0.18 → 1.91 |
| NQ=F     | 19 → 9        | 37% → **78%** | -0.23% → **+2.31%**  | 0.95 → 3.16 |
| ES=F     | 27 → 9        | 26% → **56%** | -4.34% → -0.37%      | 0.55 → 0.82 |
| GC=F     | 14 → 12       | 29% → **58%** | -1.65% → **+1.03%**  | 0.64 → 1.39 |
| HG=F     | 9 → 5         | 0% → **40%**  | -4.25% → -0.42%      | 0.00 → 0.74 |
| YM=F     | 22 → 10       | 23% → **40%** | -3.17% → -0.29%      | 0.52 → 0.89 |
| EURUSD=X | 16 → 7        | 13% → 14%     | -5.27% → -2.79%      | 0.20 → 0.08 |
| ETH-USD  | 23 → 13       | 35% → **69%** | +2.83% → +2.27%      | 1.47 → 2.08 |

### What got worse

A handful of already-trending instruments lost ground because the new
filters are over-restrictive when the v1 setup was already trend-aligned:

| ticker   | return (b/v2)       | note                                 |
| -------- | ------------------- | ------------------------------------ |
| AUDUSD=X | -1.67% → -3.75%     | V1 was barely losing; v2 worse       |
| BTC-USD  | +1.99% → -0.11%     | Was already PF 1.64 baseline         |
| SI=F     | +2.72% → -0.84%     | Trend filter cuts good silver longs  |
| USDJPY=X | +0.55% → -1.78%     | Daily 200 EMA filters trend-aligned trades |
| RTY=F    | +2.40% → -0.11%     | Was a baseline gem; v2 over-filters  |

### Bottom line on v2

- **Win rate +18 percentage points** (29.6% → 47.4%)
- **Worst drawdown halved** (-8.62% → -3.96%)
- **Mean return roughly 5x better** (-0.99% → -0.20%)
- **Sample size cut by ~38%** (250 → 156 trades) — that's the trend filter doing its job
- **12 of 21 instruments improved**; the losses are bounded (always small)

The honest message: v2 is a clear win for instruments where v1 was
chronically losing, and a marginal step back for a few that were already
working. Per-instrument basket curation (drop the chronic losers, leave
trend-aligned winners alone) is the natural next step — see
[research/improvements.md §3](research/improvements.md) for the rollout
plan.

---

## 20-year daily-bar swing variant

The Python engine has a `daily_mode` that runs the same logic on daily
bars instead of 1h, with a few thresholds auto-relaxed (`zone_pct` 0.15 →
0.30, `pat_len` 8 → 4, `brk_exp` 20 → 10, rectangle threshold scaled by
`pat_len`).

```
python -m backtest.run --daily --years 20 --vs-baseline --no-ny
```

Run on an 18-instrument long-history basket (FX majors + US equity ETFs
+ cash indices + metals + BTC):

```
[baseline] 18 instruments / 20 years
  total trades : 43       weighted win rate : 25.6%
  mean return  : -0.30%   worst max DD      : -3.00%

[v2]       18 instruments / 20 years
  total trades : 47       weighted win rate : 80.9%
  mean return  : -0.02%   worst max DD      : -0.90%
  improved tickers : 10/18
```

### Reading these numbers honestly

- **The win rate is real** (80.9% on v2 vs 25.6% on baseline) but the
  **sample is small** — 47 trades over 20 years across 18 instruments is
  ~0.13 trades per instrument per year. Several FX pairs and ^N225 fired
  zero times.
- This is expected: the strategy was designed for **intraday execution**
  off daily levels. Porting it to daily bars where each "previous bar" is
  yesterday and "previous week H/L" is the wH/wL we already had makes the
  same setup much rarer.
- The result is a useful **principles still hold over 20 years** check —
  and they do — but it is **not** a meaningful equity-curve test. The
  720-day 1h apples-to-apples comparison above is the rigorous result.

If you want a true 20-year equity-curve test of this strategy, you need
1h history beyond yfinance's free 730-day cap. Sources to look at:
[histdata.com](https://www.histdata.com/) (free 1m FX back to 2000, can
be resampled to 1h), [Dukascopy historical data](https://www.dukascopy.com/swiss/english/marketwatch/historical/),
or paid feeds like Polygon/Databento.

---

## TradingView (Pine v6)

1. Pine Editor → paste `pine/weekly_manipulation_strategy.pine`.
2. Add to chart. Renders PDH/PDL/PDC, prev-week H/L, daily 200-EMA, plus
   `TD up/dn`, `Inside`, `Climax`, and `FakeOut up/dn` labels.
3. Strategy Tester for backtests. Use the symbol switcher to test across
   a basket — inputs persist.

Inputs are grouped: *Context & Levels*, *Execution*, *Trend & Strength
Filters*, *Session*, *Risk*, *Visuals*. Every v2 feature has its own
toggle so you can ablate them and compare.

Four `alertcondition`s ship out of the box:

- *Weekly High Reclaimed (Bearish)* / *Weekly Low Reclaimed (Bullish)* —
  the "dead-giveaway" weekly manipulation prints
- *Long Setup Triggered* / *Short Setup Triggered* — full A+ sequence
  webhook-ready alerts

---

## Python backtest

### Install

```bash
pip install -r backtest/requirements.txt
```

### Run

```bash
# v2 default basket, 720d 1h
python -m backtest.run

# v1 baseline (Tier 1+2 features off)
python -m backtest.run --baseline

# v2_loose (Tier 4 sample-size broadeners on)
python -m backtest.run --loose

# Three-way comparison: baseline + v2 + v2_loose with delta tables
python -m backtest.run --three-way

# Curated 15-instrument basket (v2_loose's positive performers)
python -m backtest.run --curated --three-way

# Wide 32-instrument basket (FX crosses, energies, altcoins)
python -m backtest.run --loose --big

# 20-year daily swing variant, vs-baseline
python -m backtest.run --daily --years 20 --vs-baseline --no-ny

# custom basket
python -m backtest.run --tickers EURUSD=X GC=F BTC-USD

# custom risk per trade and CSV output
python -m backtest.run --three-way --risk-pct 0.25 --out-csv results.csv
```

### Walk-forward optimizer

```bash
python -m backtest.walk_forward
python -m backtest.walk_forward --preset v2_loose --train-days 240 --test-days 120 --step-days 60
```

Searches `zone_pct ∈ {0.10, 0.15, 0.20}`, `rr_tp2 ∈ {1.5, 2.0, 3.0}`,
`climax_mx ∈ {1.5, 1.75, 2.0}` (27 combos) on rolling training windows,
applies the best to each OOS window. Reports per-window detail and
per-ticker aggregates. Pass `--preset {baseline,v2,v2_loose}` to choose
the base param set the grid is searched on top of.

---

## 24/7 live signal service

A standalone Python service that polls yfinance on a schedule, runs the
strategy across the configured basket, and fires webhooks for any fresh
signal. No TradingView or browser required — runs anywhere Python runs.

### Quickstart

```bash
# Console-only smoke test (3 tickers, single cycle)
python -m signals.service --once --tickers ES=F GC=F BTC-USD

# Full curated basket, polling every 5 minutes, console + JSON-log output
python -m signals.service

# With Discord webhook (free; create one in Server Settings -> Integrations)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \
    python -m signals.service

# Telegram bot
TELEGRAM_BOT_TOKEN=123:abc \
TELEGRAM_CHAT_ID=-100123456789 \
    python -m signals.service
```

The service exits cleanly on Ctrl-C / SIGTERM. SQLite-backed dedupe means
the same setup never fires twice; restart-safe.

### What you get

For each fresh signal:

```
[2026-06-06T19:45:03+00:00] BUY ES=F @ 7559.50000
  bar_time : 2026-06-04T13:00:00
  SL       : 7530.41429   (29.08571)
  TP1      : 7627.00000   (2.32R)
  TP2      : 7617.67143   (2.00R)
  trigger  : cascade
```

JSON-Lines log at `signals/log.jsonl` records every signal for replay /
analysis. Discord embeds get colour-coded green/red. Telegram gets the
plain-text format.

### Configuration

| Flag             | Default            | Purpose                                                        |
| ---------------- | ------------------ | -------------------------------------------------------------- |
| `--basket`       | `curated`          | `curated`, `default`, or `wide`                                |
| `--tickers`      | (use basket)       | Override basket with explicit symbol list                      |
| `--preset`       | `v2_loose`         | `baseline`, `v2`, or `v2_loose`                                |
| `--interval`     | `1h`               | Bar timeframe (yfinance: 1h capped at 730d, 15m at 60d)        |
| `--period`       | `60d`              | Lookback for state-machine warm-up (60d covers 8 weeks of 1h)  |
| `--poll-sec`     | `300`              | Seconds between polling cycles                                 |
| `--bar-age`      | `1`                | Trigger bar must be N bars old (1 = use second-to-last)        |
| `--no-ny`        | off                | Disable NY-session filter (for 24h FX/crypto runs)             |
| `--once`         | off                | Run a single cycle and exit (great for cron)                   |
| `--state-db`     | `signals/state.sqlite` | Dedupe DB path                                             |
| `--log-path`     | `signals/log.jsonl` | JSON-Lines log path                                            |

Environment variables for notifications:

| Var                    | Effect                                          |
| ---------------------- | ----------------------------------------------- |
| `DISCORD_WEBHOOK_URL`  | Enables Discord embeds (no Discord auth needed) |
| `TELEGRAM_BOT_TOKEN`   | Bot token from @BotFather                       |
| `TELEGRAM_CHAT_ID`     | Numeric chat or channel id                      |

Console + JSON-Lines log are always on. Discord/Telegram are added when the
relevant env vars are set.

### Deployment

The service is a long-running Python process. Three reasonable approaches:

```bash
# Local dev with auto-restart
python -m signals.service

# Background via screen/tmux
screen -S signals
python -m signals.service
# Ctrl-A D to detach

# As a systemd service (Linux)
# Create /etc/systemd/system/pullback-signals.service:
[Unit]
Description=Pullback algo signal service
After=network.target

[Service]
WorkingDirectory=/opt/pullback-algo
Environment="DISCORD_WEBHOOK_URL=https://..."
ExecStart=/usr/bin/python3 -m signals.service
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target

# Then:
systemctl daemon-reload && systemctl enable --now pullback-signals
```

### Honest caveats

1. **yfinance is delayed**, often 15-30 minutes for free-tier intraday data.
   This is fine for a 1h strategy where you act on the *previous* bar's
   signal anyway, but means you won't have tick-precise execution. For
   that, swap `signals/check.py`'s `fetch()` call for Polygon/Databento.
2. **The default `--bar-age 1` skips the live bar.** That bar isn't closed
   yet so signals computed on it would repaint. The service trusts the
   second-to-last bar (which IS closed). Set `--bar-age 0` only if you
   know your data source is exact-close-aligned.
3. **The service is stateless across signal latches** — it re-runs the
   backtest engine over the recent 60d window every cycle, which
   reconstructs the weekly fakeout latch from history. This is slower
   than incremental state but immune to consistency bugs.
4. **No order routing.** This is a *signal* service, not an *execution*
   service. Pair it with a broker API (Alpaca, IBKR, Tradovate) if you
   want auto-execution. The webhook payload has every field a broker
   adapter would need.

### Files

```
signals/
├── service.py     # main polling loop, CLI
├── check.py       # signal detection (runs the strategy, finds fresh trades)
├── notify.py      # console / file / Discord / Telegram notifiers
├── state.py       # SQLite-backed dedupe store
├── state.sqlite   # (created at runtime)
└── log.jsonl      # (created at runtime)
```

---

## Repo layout

```
pullback-algo/
├── pine/
│   └── weekly_manipulation_strategy.pine    # v2: all Tier 1+2 features
├── backtest/
│   ├── __init__.py
│   ├── strategy.py                          # core engine + Params
│   ├── run.py                               # CLI runner with --vs-baseline / --daily
│   ├── walk_forward.py                      # walk-forward optimizer
│   ├── requirements.txt
│   ├── results_1h_720d.csv                  # v1 reference (NY-filter on, 21 inst)
│   ├── results_1h_720d_24h.csv              # v1 reference (24h, FX+metals+crypto)
│   ├── results_v2_apples.csv                # v1 vs v2 same-basket comparison (per-row stats)
│   ├── results_v2_apples_delta.csv          # v1 vs v2 side-by-side delta table
│   ├── results_v2_20yr_daily.csv            # 20-year daily-mode v1 vs v2
│   ├── results_v2_20yr_daily_delta.csv      # 20-year delta table
│   ├── walk_forward_detail.csv              # per-window OOS records
│   └── walk_forward_summary.csv             # per-ticker aggregates
├── research/
│   └── improvements.md                      # failure-mode analysis + ranked proposals
├── .kiro/steering/pine.md                   # Pine v6 conventions
└── README.md
```

---

## Parameters reference (v2)

| Param            | Pine input                              | Python field          | Default  | Notes                                                  |
| ---------------- | --------------------------------------- | --------------------- | -------: | ------------------------------------------------------ |
| ADR length       | `ADR period (days)`                     | `adr_len`             | 14       | rolling SMA of daily range                             |
| Climax mult      | `Climax day x ADR`                      | `climax_mx`           | 1.75     | yesterday's range / ADR threshold                      |
| Zone tol         | `Zone tolerance (x ADR)`                | `zone_pct`            | 0.15     | distance from PDH/PDL/PDC for setup zone               |
| Pattern len      | `Pattern lookback (bars)`               | `pat_len`             | 8        | rectangle high/low window                              |
| Breakout exp     | `Breakout expiry (bars)`                | `brk_exp`             | 20       | breakout staleness for pullback                        |
| **Trend EMA**    | `Daily trend filter (200 EMA)`          | `use_htf_trend`       | True     | only longs above EMA, shorts below                     |
| **Trend len**    | `Trend EMA period`                      | `htf_trend_period`    | 200      | EMA period (D for intraday, daily-bar for daily_mode)  |
| **ATR stop**     | `Combine with ATR-multiple stop`        | `use_atr_stop`        | True     | takes the wider of structural and ATR stop             |
| **ATR k**        | `ATR stop multiplier (k)`               | `atr_stop_k`          | 1.5      | entry +/- k * ATR(14)                                  |
| **Runner BE**    | `Move runner SL to BE+offset after TP1` | `runner_be`           | True     | shifts the 60% runner's stop to entry+offset after TP1 |
| **BE offset**    | `Runner BE offset (x R)`                | `runner_be_offset_r`  | 0.10     | +/- 0.1R from entry                                    |
| **ADX gate**     | `ADX trend-strength gate`               | `use_adx_filter`      | True     | block entries when ADX < threshold                     |
| **ADX threshold**| `ADX threshold`                         | `adx_threshold`       | 22.0     | 22 default; 18 in daily_mode                           |
| **Pin trigger**  | `Pin-bar trigger`                       | `use_pin_bar`         | True     | wick ≥ 60% of bar range + close past midpoint          |
| **Inside trig**  | `Inside-bar-break trigger`              | `use_inside_break`    | True     | inside bar followed by close beyond parent extreme     |
| **Day sweep**    | `Day-level sweep latch (PDH/PDL)`       | `use_day_sweep`       | True     | mirror of weekly fakeout, anchored to previous day     |
| Stop buf         | `Stop buffer (x ADR)`                   | `sl_buf`              | 0.10     | structural extreme buffer                              |
| TP1 size         | `TP1 size %`                            | `tp1_pct`             | 40       | partial close at PDH/PDL                               |
| TP2 R-mult       | `TP2 reward multiple`                   | `rr_tp2`              | 2.0      | runner target as multiple of risk                      |
| Use NY           | `Trade only NY session`                 | `use_ny`              | True     |                                                        |
| Risk %           | (sizing handled by Pine inputs)         | `risk_pct`            | 0.5%     | account risk per trade (Python only)                   |
| Daily mode       | (use D chart TF in Pine)                | `daily_mode`          | False    | swing-variant for daily-bar long-history runs          |
