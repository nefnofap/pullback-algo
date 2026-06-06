"""
24/7 signal service.

Polls yfinance for the configured basket on a schedule (default: 5 min after
each hour close), runs the strategy over the recent window, fires webhooks
for any fresh signal that hasn't been fired before.

Usage:
    python -m signals.service
    python -m signals.service --basket curated --interval 1h --poll-sec 300
    python -m signals.service --tickers ES=F GC=F BTC-USD --once
    DISCORD_WEBHOOK_URL=https://... python -m signals.service

Env vars:
    DISCORD_WEBHOOK_URL    Discord channel webhook URL
    TELEGRAM_BOT_TOKEN     Telegram bot token (BotFather)
    TELEGRAM_CHAT_ID       Telegram destination chat/channel id
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backtest.run import (
    fetch, make_baseline_params, make_v2_loose_params, make_v2_params,
    CURATED_TICKERS, DEFAULT_TICKERS, WIDE_TICKERS,
)

from .check import detect_signals_on_last_closed
from .notify import from_env
from .state import State

LOGGER = logging.getLogger("signals")


# ---------------------------------------------------------------------------
def _basket_from_name(name: str) -> list[str]:
    if name == "curated":
        return CURATED_TICKERS
    if name == "default":
        return DEFAULT_TICKERS
    if name == "wide":
        return WIDE_TICKERS
    raise ValueError(f"unknown basket: {name}")


def _params_from_name(name: str, use_ny: bool, daily_mode: bool):
    if name == "baseline":
        return make_baseline_params(use_ny=use_ny, daily_mode=daily_mode)
    if name == "v2":
        return make_v2_params(use_ny=use_ny, daily_mode=daily_mode)
    return make_v2_loose_params(use_ny=use_ny, daily_mode=daily_mode)


# ---------------------------------------------------------------------------
def tick(tickers: list[str], interval: str, period: str, params,
         state: State, notifier, closed_bar_age_bars: int = 1) -> int:
    """One polling cycle. Returns number of fresh signals fired."""
    fired = 0
    for tkr in tickers:
        try:
            df = fetch(tkr, interval, period)
        except Exception as exc:
            LOGGER.warning("%s: fetch error: %s", tkr, exc)
            continue
        if df is None or len(df) < 250:
            LOGGER.debug("%s: insufficient bars (%s)", tkr,
                         0 if df is None else len(df))
            continue

        try:
            for sig in detect_signals_on_last_closed(
                    tkr, df, params, closed_bar_age_bars=closed_bar_age_bars):
                if state.already_fired(tkr, sig["side"], sig["bar_time"]):
                    LOGGER.debug("%s: dedup %s @ %s", tkr, sig["side"], sig["bar_time"])
                    continue
                state.record(
                    tkr, sig["side"], sig["bar_time"], sig["fired_at"],
                    sig["entry_price"], sig["sl"], sig["tp1"], sig["tp2"],
                )
                if notifier.send(sig):
                    fired += 1
                    LOGGER.info("%s: %s signal fired @ %s",
                                tkr, sig["side"], sig["bar_time"])
        except Exception as exc:
            LOGGER.exception("%s: signal-detect error: %s", tkr, exc)
    return fired


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--basket", choices=["curated", "default", "wide"],
                    default="curated", help="ticker basket name")
    ap.add_argument("--tickers", nargs="+", default=None,
                    help="override basket with explicit ticker list")
    ap.add_argument("--preset", choices=["baseline", "v2", "v2_loose"],
                    default="v2_loose", help="strategy preset")
    ap.add_argument("--interval", default="1h",
                    help="bar interval (1h recommended; 15m caps at 60d on yfinance)")
    ap.add_argument("--period", default="60d",
                    help="lookback window for state-machine warmup (60d covers 8 weeks of 1h)")
    ap.add_argument("--poll-sec", type=int, default=300,
                    help="seconds between polling cycles (default 300 = 5 min)")
    ap.add_argument("--no-ny", action="store_true",
                    help="disable NY-session filter (24h FX/crypto runs)")
    ap.add_argument("--state-db", default="signals/state.sqlite",
                    help="path to dedupe state DB")
    ap.add_argument("--log-path", default="signals/log.jsonl",
                    help="path to JSON-lines signal log")
    ap.add_argument("--bar-age", type=int, default=1,
                    help="how many bars old the trigger bar must be "
                         "(1 = use second-to-last bar; safe default for live data lag)")
    ap.add_argument("--once", action="store_true",
                    help="run a single polling cycle and exit (useful for cron)")
    ap.add_argument("--prune-days", type=int, default=30,
                    help="discard dedupe records older than this many days at startup")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    tickers = args.tickers if args.tickers is not None else _basket_from_name(args.basket)
    params = _params_from_name(args.preset, use_ny=not args.no_ny, daily_mode=False)

    state = State(args.state_db)
    if args.prune_days > 0:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.prune_days)).isoformat()
        n = state.prune_older_than(cutoff)
        if n:
            LOGGER.info("pruned %d dedupe records older than %dd", n, args.prune_days)

    notifier = from_env(log_path=args.log_path)

    LOGGER.info(
        "starting service: %d tickers, preset=%s, interval=%s, poll=%ds, bar_age=%d",
        len(tickers), args.preset, args.interval, args.poll_sec, args.bar_age,
    )
    LOGGER.info("basket: %s", " ".join(tickers))

    # Graceful shutdown
    stop = {"flag": False}
    def _shutdown(signum, frame):
        LOGGER.info("received signal %d, stopping", signum)
        stop["flag"] = True
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    cycle = 0
    while not stop["flag"]:
        cycle += 1
        cycle_start = time.time()
        try:
            fired = tick(
                tickers, args.interval, args.period, params,
                state, notifier, closed_bar_age_bars=args.bar_age,
            )
            elapsed = time.time() - cycle_start
            LOGGER.info("cycle #%d done in %.1fs - %d signals fired",
                        cycle, elapsed, fired)
        except Exception as exc:
            LOGGER.exception("tick failed: %s", exc)

        if args.once:
            return 0

        # Sleep but check shutdown flag every second
        sleep_s = max(0, args.poll_sec - (time.time() - cycle_start))
        end = time.time() + sleep_s
        while time.time() < end and not stop["flag"]:
            time.sleep(1.0)

    LOGGER.info("service stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
