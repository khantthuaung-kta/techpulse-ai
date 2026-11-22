"""Telegram Bot API delivery."""

import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

TIMEOUT = (5, 10)
CONNECTION_MESSAGE = "✅ TechPulse AI is connected successfully!"


class TelegramError(Exception):
    """Telegram delivery failed."""


def load_credentials() -> tuple[str, str]:
    load_dotenv(Path(".env"))
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not chat_id:
        raise TelegramError("Set a valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.")
    return token, chat_id


def send_connection_test(token: str, chat_id: str) -> None:
    send_messages([CONNECTION_MESSAGE], token, chat_id)


def send_digest(messages: list[str], token: str, chat_id: str) -> None:
    send_messages(messages, token, chat_id, parse_mode="HTML")


def send_messages(
    messages: list[str], token: str, chat_id: str, *, parse_mode: str | None = None
) -> None:
    for delivered, message in enumerate(messages):
        payload = {
            "chat_id": chat_id,
            "text": message,
            "link_preview_options": {"is_disabled": True},
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                timeout=TIMEOUT,
                allow_redirects=False,
            )
            response.raise_for_status()
            result = response.json()
            if response.status_code != 200 or not isinstance(result, dict) or result.get("ok") is not True:
                reason = f"unexpected API response (HTTP {response.status_code})"
            else:
                continue
        except (requests.RequestException, ValueError) as error:
            # Request exceptions may include the bot token in the request URL.
            if isinstance(error, requests.HTTPError) and error.response is not None:
                reason = f"HTTP {error.response.status_code}"
            else:
                reason = type(error).__name__
        raise TelegramError(
            f"Telegram delivery failed after {delivered}/{len(messages)} confirmed messages ({reason})."
        )
