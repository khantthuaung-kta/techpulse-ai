"""Telegram Bot API delivery."""

import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

import requests
from dotenv import load_dotenv

TIMEOUT = (5, 10)
MAX_RETRY_AFTER = 30
CONNECTION_MESSAGE = "✅ TechPulse AI is connected successfully!"
LOG = logging.getLogger(__name__)


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


def send_digest(
    messages: list[str], token: str, chat_id: str,
    *, on_confirmed: Callable[[int, int], None] | None = None,
) -> None:
    send_messages(messages, token, chat_id, parse_mode="HTML", on_confirmed=on_confirmed)


def send_messages(
    messages: list[str], token: str, chat_id: str, *, parse_mode: str | None = None,
    on_confirmed: Callable[[int, int], None] | None = None,
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
            message_id = post_message(token, payload)
            if on_confirmed is not None and message_id is None:
                raise TelegramError("Telegram did not return a usable message ID; delivery may have occurred")
        except TelegramError as error:
            raise TelegramError(
                f"Telegram delivery failed after {delivered}/{len(messages)} confirmed messages ({error})."
            ) from None
        if on_confirmed is not None and message_id is not None:
            on_confirmed(delivered, message_id)


def post_message(token: str, payload: dict[str, object]) -> int | None:
    for attempt in range(2):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage", json=payload,
                timeout=TIMEOUT, allow_redirects=False,
            )
        except requests.RequestException as error:
            # The request may have reached Telegram. Its exception can contain the bot token.
            raise TelegramError(f"{type(error).__name__}; delivery may have occurred") from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code == 429:
            parameters = data.get("parameters") if isinstance(data, dict) and data.get("ok") is False else None
            delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
            if not attempt and type(delay) is int and 1 <= delay <= MAX_RETRY_AFTER:
                LOG.warning("Telegram rate limited the message. Retrying once after %d seconds.", delay)
                time.sleep(delay)
                continue
            raise TelegramError("HTTP 429")
        if response.status_code != 200:
            reason = "; delivery may have occurred" if response.status_code >= 500 else ""
            raise TelegramError(f"HTTP {response.status_code}{reason}")
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise TelegramError("unexpected API response (HTTP 200); delivery may have occurred")
        result = data.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return message_id if type(message_id) is int and message_id > 0 else None
    raise TelegramError("HTTP 429")
