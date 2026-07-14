from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramNotifier:
    bot_token: str
    chat_id: str

    @classmethod
    def from_env(
        cls, environment: Mapping[str, str] | None = None
    ) -> TelegramNotifier | None:
        values = os.environ if environment is None else environment
        bot_token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = values.get("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            return None
        return cls(bot_token=bot_token, chat_id=chat_id)

    def send(self, message: str) -> bool:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=urllib.parse.urlencode(
                {"chat_id": self.chat_id, "text": message}
            ).encode(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            print("warning: Telegram notification failed", file=sys.stderr)
            return False
