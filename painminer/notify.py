"""Minimal Telegram push (§7.6 abort path).

Just enough to send a message via the Bot API. The full bot — commands and
inline buttons — is Phase 7; this exists so the judge can raise the alarm when
Machine B is unreachable, which is the failure mode that silently kills the
system (§11). IPv4-forced for the same reason as fetch.py.
"""

from __future__ import annotations

import httpx

from painminer.config import Config


def send_telegram(config: Config, text: str) -> bool:
    """Send a message to the configured chat. Returns True on success.

    Never raises — a notifier that crashes the crash-reporter is useless.
    """
    url = f"https://api.telegram.org/bot{config.telegram_token}/sendMessage"
    transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=1)
    try:
        with httpx.Client(transport=transport, timeout=15) as client:
            resp = client.post(
                url,
                json={"chat_id": config.telegram_chat_id, "text": text},
            )
            return resp.status_code == 200
    except Exception:
        return False
