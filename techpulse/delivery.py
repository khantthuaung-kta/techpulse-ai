"""Coordinate a daily claim before sending any Telegram message."""

import json
import logging
import os
from datetime import date, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from .articles import Article
from .ledger import (
    ClaimConflict, DeliveryRecord, GitHubLedger, Ledger, LedgerError, SQLiteLedger,
    article_fingerprints, fingerprint,
)
from .telegram import TelegramError, send_digest

LOG = logging.getLogger(__name__)
YANGON = ZoneInfo("Asia/Yangon")


def delivery_date(now: datetime) -> date:
    created = os.getenv("TECHPULSE_RUN_CREATED_AT")
    if created:
        try:
            original = datetime.fromisoformat(created)
            if original.tzinfo is None:
                raise ValueError
            return original.astimezone(YANGON).date()
        except ValueError:
            raise LedgerError("Invalid original GitHub run timestamp.") from None
    if os.getenv("GITHUB_ACTIONS") == "true":
        raise LedgerError("The original GitHub run timestamp is required for delivery.")
    return now.astimezone(YANGON).date()


def open_ledger() -> Ledger:
    if os.getenv("GITHUB_ACTIONS") == "true":
        return GitHubLedger(os.getenv("GITHUB_REPOSITORY", ""),
                            os.getenv("TECHPULSE_GITHUB_TOKEN", ""), os.getenv("GITHUB_SHA", ""))
    return SQLiteLedger(Path(os.getenv("TECHPULSE_DELIVERY_DB") or ".techpulse-delivery.sqlite3"))


def already_claimed(ledger: Ledger, day: date) -> bool:
    previous = ledger.read(day)
    if previous is None:
        return False
    record, _ = previous
    if record.status == "sent":
        LOG.info("Digest date %s is already delivered. Skipping duplicate delivery.", day)
        return True
    raise LedgerError(
        f"Digest date {day} is already claimed ({len(record.confirmed)}/{record.parts} confirmed parts). "
        "Check Telegram and reconcile the ledger before retrying."
    )


def deliver(
    ledger: Ledger, day: date, destination: str, articles: list[Article],
    messages: list[str], token: str, chat_id: str,
) -> bool:
    by_message = []
    for message in messages:
        keys: set[str] = set()
        for article in articles:
            anchor = f'href="{escape(article.url, quote=True)}">Original article</a>'
            if anchor in message:
                keys.update(article_fingerprints(article))
        by_message.append(sorted(keys))
    record = DeliveryRecord(destination, fingerprint(json.dumps(messages, ensure_ascii=False)), len(messages))
    try:
        revision = ledger.write(day, record, None)
    except ClaimConflict:
        if already_claimed(ledger, day):
            return False
        raise
    LOG.info("Claimed digest date %s with %d articles and %d parts.", day, len(articles), len(messages))

    def confirm(index: int, message_id: int) -> None:
        nonlocal revision
        if str(message_id) in record.confirmed:
            raise LedgerError("Telegram returned a duplicate receipt ID. Verify the claimed digest in the chat.")
        record.confirmed[str(message_id)] = by_message[index]
        if len(record.confirmed) == record.parts:
            record.status = "sent"
        try:
            revision = ledger.write(day, record, revision)
        except LedgerError:
            raise LedgerError(
                "Telegram acknowledged a message, but the ledger receipt could not be confirmed. "
                "The claim remains. Check the chat before attempting recovery."
            ) from None
        LOG.info("Delivery progress date=%s confirmed_parts=%d total_parts=%d.",
                 day, len(record.confirmed), record.parts)

    try:
        send_digest(messages, token, chat_id, on_confirmed=confirm)
    except TelegramError:
        # Never release the claim automatically, including on an ambiguous timeout.
        record.status = "failed"
        try:
            ledger.write(day, record, revision)
        except LedgerError:
            LOG.error("Could not persist failure status. The original delivery claim remains in place.")
        raise
    return True
