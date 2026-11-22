"""One-shot daily digest entry point."""

import argparse
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .ai_config import configured_summaries, load_summary_settings
from .articles import select_articles
from .digest import render_digest
from .sources import collect, load_feeds
from .telegram import TelegramError, load_credentials, send_connection_test, send_digest

LOG = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a daily technology news digest.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print the digest to the terminal.")
    mode.add_argument("--send", action="store_true", help="Deliver the digest to Telegram.")
    mode.add_argument("--test-telegram", action="store_true", help="Send a Telegram connection test message.")
    parser.add_argument("--feeds", type=Path, default=Path("feeds.toml"), help="Path to the RSS feed configuration.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.send or args.test_telegram:
        # urllib3 debug output includes the Telegram request path and its token.
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
        try:
            token, chat_id = load_credentials()
        except TelegramError as error:
            parser.error(str(error))

    if args.test_telegram:
        try:
            send_connection_test(token, chat_id)
        except TelegramError as error:
            LOG.error("%s", error)
            return 1
        LOG.info("Telegram test message delivered.")
        return 0

    try:
        feeds = load_feeds(args.feeds)
    except (OSError, ValueError) as error:
        LOG.error("Cannot load feed configuration (%s).", error)
        return 1

    articles, usable = collect(feeds)
    if not usable:
        LOG.error("All news sources failed.")
        return 1

    if args.dry_run:
        load_dotenv(Path(".env"))
    settings = load_summary_settings()
    now = datetime.now(UTC)
    selected = select_articles(
        articles, now, timedelta(hours=24), prefer_publisher_text=settings.provider is not None
    )
    summaries = configured_summaries(selected, settings)
    if args.dry_run:
        print(render_digest(selected, now, summaries=summaries)[0])
        return 0

    try:
        send_digest(render_digest(selected, now, telegram=True, summaries=summaries), token, chat_id)
    except TelegramError as error:
        LOG.error("%s", error)
        return 1
    LOG.info("Delivered digest with %d articles.", len(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
