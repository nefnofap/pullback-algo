"""
Notification backends: console, file, Discord webhook, Telegram bot.

A "Notifier" exposes one method, `send(signal)`. `signal` is a dict with the
keys produced by `signals.check.signal_dict()`.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, signal: dict) -> bool: ...


def _format_human(s: dict) -> str:
    arrow = "BUY" if s["side"] == "long" else "SELL"
    risk = abs(s["entry_price"] - s["sl"])
    rr1 = abs(s["tp1"] - s["entry_price"]) / risk if risk else 0
    rr2 = abs(s["tp2"] - s["entry_price"]) / risk if risk else 0
    return (
        f"[{s['fired_at']}] {arrow} {s['ticker']} @ {s['entry_price']:.5f}\n"
        f"  bar_time : {s['bar_time']}\n"
        f"  SL       : {s['sl']:.5f}   ({risk:.5f})\n"
        f"  TP1      : {s['tp1']:.5f}   ({rr1:.2f}R)\n"
        f"  TP2      : {s['tp2']:.5f}   ({rr2:.2f}R)\n"
        f"  trigger  : {s.get('reason', 'cascade')}"
    )


# ---------------------------------------------------------------------------
@dataclass
class ConsoleNotifier:
    """Writes formatted signals to stdout/stderr."""
    use_stderr: bool = False

    def send(self, signal: dict) -> bool:
        out = sys.stderr if self.use_stderr else sys.stdout
        print(_format_human(signal), file=out, flush=True)
        return True


# ---------------------------------------------------------------------------
@dataclass
class FileNotifier:
    """Append-only JSON-Lines log."""
    path: Path

    def __post_init__(self):
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, signal: dict) -> bool:
        with self.path.open("a") as f:
            f.write(json.dumps(signal, default=str) + "\n")
        return True


# ---------------------------------------------------------------------------
@dataclass
class DiscordNotifier:
    """POST a Discord webhook. Set webhook_url to your channel's webhook URL.

    Discord webhooks are free and require no OAuth - just create one in
    Server Settings -> Integrations -> Webhooks.
    """
    webhook_url: str
    timeout_s: float = 10.0

    def send(self, signal: dict) -> bool:
        import urllib.request
        import urllib.error
        arrow = "🟢 BUY" if signal["side"] == "long" else "🔴 SELL"
        risk = abs(signal["entry_price"] - signal["sl"])
        rr1 = abs(signal["tp1"] - signal["entry_price"]) / risk if risk else 0
        rr2 = abs(signal["tp2"] - signal["entry_price"]) / risk if risk else 0
        body = {
            "username": "pullback-algo",
            "embeds": [{
                "title": f"{arrow} {signal['ticker']}",
                "color": 0x00B894 if signal["side"] == "long" else 0xD63031,
                "fields": [
                    {"name": "Entry", "value": f"{signal['entry_price']:.5f}", "inline": True},
                    {"name": "SL",    "value": f"{signal['sl']:.5f}",         "inline": True},
                    {"name": "Risk",  "value": f"{risk:.5f}",                 "inline": True},
                    {"name": "TP1",   "value": f"{signal['tp1']:.5f} ({rr1:.2f}R)", "inline": True},
                    {"name": "TP2",   "value": f"{signal['tp2']:.5f} ({rr2:.2f}R)", "inline": True},
                    {"name": "Bar",   "value": str(signal["bar_time"]),       "inline": False},
                ],
                "footer": {"text": f"trigger: {signal.get('reason', 'cascade')}"},
                "timestamp": signal["fired_at"],
            }],
        }
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                if resp.status >= 300:
                    logger.warning("discord webhook %s: %s", resp.status, resp.reason)
                    return False
                return True
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("discord webhook failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
@dataclass
class TelegramNotifier:
    """Sends to a Telegram channel/chat via bot API. Requires a bot token
    (from @BotFather) and a chat_id."""
    bot_token: str
    chat_id: str
    timeout_s: float = 10.0

    def send(self, signal: dict) -> bool:
        import urllib.request
        import urllib.error
        text = _format_human(signal)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": text}).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return resp.status < 300
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("telegram api failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
@dataclass
class CompositeNotifier:
    """Fan-out to multiple notifiers."""
    backends: Iterable[Notifier]

    def send(self, signal: dict) -> bool:
        ok = True
        for n in self.backends:
            if not n.send(signal):
                ok = False
        return ok


# ---------------------------------------------------------------------------
def from_env(log_path: str = "signals/log.jsonl") -> Notifier:
    """Auto-build a notifier from env vars.

    Always emits to console + file. Adds Discord/Telegram if the relevant
    env vars are set.

    Env vars:
      DISCORD_WEBHOOK_URL : full Discord webhook URL
      TELEGRAM_BOT_TOKEN  : bot token from @BotFather
      TELEGRAM_CHAT_ID    : numeric chat or channel id
    """
    backends: list[Notifier] = [ConsoleNotifier(), FileNotifier(Path(log_path))]
    if (url := os.getenv("DISCORD_WEBHOOK_URL")):
        backends.append(DiscordNotifier(url))
        logger.info("discord notifier enabled")
    if (tok := os.getenv("TELEGRAM_BOT_TOKEN")) and (cid := os.getenv("TELEGRAM_CHAT_ID")):
        backends.append(TelegramNotifier(tok, cid))
        logger.info("telegram notifier enabled")
    return CompositeNotifier(backends)
