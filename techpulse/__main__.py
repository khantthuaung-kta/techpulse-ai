"""One-shot daily digest entry point."""

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from .ai_config import configured_summaries, load_summary_settings
from .delivery import YANGON, already_claimed, deliver, delivery_date, open_ledger
from .digest import render_digest
from .interactive import run_poller
from .ledger import LedgerError, article_fingerprints, destination_key, sent_history
from .personalization import explain_ranking, load_preferences, rank_digest
from .sources import collect, load_feeds
from .telegram import TelegramError, load_credentials, send_connection_test

LOG = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a daily technology news digest.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print the digest to the terminal.")
    mode.add_argument("--send", action="store_true", help="Deliver the digest to Telegram.")
    mode.add_argument("--test-telegram", action="store_true", help="Send a Telegram connection test message.")
    mode.add_argument("--poll-telegram", action="store_true", help="Run the interactive Telegram bot locally.")
    parser.add_argument("--feeds", type=Path, default=Path("feeds.toml"), help="Path to the news source registry.")
    parser.add_argument("--preferences", type=Path, help="Path to ranking preferences. Defaults to preferences.toml if present.")
    parser.add_argument("--explain", action="store_true", help="Show ranking factors with --dry-run.")
    parser.add_argument("--learn-tip", action="store_true", help="Include a reading exercise linked to one selected source.")
    parser.add_argument("--poll-once", action="store_true", help="Process one update batch with --poll-telegram.")
    args = parser.parse_args(argv)
    if args.explain and not args.dry_run:
        parser.error("--explain requires --dry-run.")
    if args.poll_once and not args.poll_telegram:
        parser.error("--poll-once requires --poll-telegram.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.send or args.test_telegram or args.poll_telegram:
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

    if args.send:
        try:
            started = datetime.now(UTC)
            day = delivery_date(started)
            today = started.astimezone(YANGON).date()
            if day > today:
                raise LedgerError("The digest date is in the future.")
            if day < today:
                LOG.info("Original run date %s has passed. Skipping stale delivery.", day)
                return 0
            ledger = open_ledger()
            if already_claimed(ledger, day):
                return 0
            destination = destination_key(token, chat_id)
            seen = sent_history(ledger, day, destination)
        except LedgerError as error:
            LOG.error("%s", error)
            return 1

    try:
        feeds = load_feeds(args.feeds)
    except (OSError, ValueError) as error:
        LOG.error("Cannot load feed configuration (%s).", error)
        return 1
    try:
        path = args.preferences or (Path("preferences.toml") if Path("preferences.toml").exists() else None)
        preferences = load_preferences(path)
    except (OSError, ValueError) as error:
        LOG.error("Cannot load ranking preferences (%s).", error)
        return 1
    if args.poll_telegram:
        return run_poller(token, chat_id, feeds, preferences, once=args.poll_once)

    articles, usable = collect(feeds)
    if not usable:
        LOG.error("All news sources failed.")
        return 1

    if args.dry_run:
        load_dotenv(Path(".env"))
    fetched = len(articles)
    if args.send:
        articles = [article for article in articles if not article_fingerprints(article) & seen]
    skipped = fetched - len(articles)
    settings = load_summary_settings()
    now = datetime.now(UTC)
    ranked = rank_digest(
        articles, now, preferences, prefer_publisher_text=settings.provider is not None
    )
    selected = [story.article for story in ranked]
    LOG.info("Digest counts fetched=%d previously_delivered=%d filtered=%d selected=%d.",
             fetched, skipped, len(articles) - len(selected), len(selected))
    if args.send and not selected:
        LOG.info("No eligible unsent stories. No Telegram message or daily claim created.")
        return 0
    summaries = configured_summaries(selected, settings)
    LOG.info("Digest summary count translated=%d selected=%d.", len(summaries or {}), len(selected))
    if args.dry_run:
        print(render_digest(selected, now, summaries=summaries, learn_tip=args.learn_tip)[0])
        if args.explain and ranked:
            print("\nRanking factors")
            print("\n".join(explain_ranking(ranked)))
        return 0

    try:
        if datetime.now(UTC).astimezone(YANGON).date() != day:
            LOG.info("Digest collection crossed the Yangon date boundary. Skipping stale delivery.")
            return 0
        messages = render_digest(selected, now, telegram=True, summaries=summaries, learn_tip=args.learn_tip)
        if not deliver(ledger, day, destination, selected, messages, token, chat_id):
            return 0
    except (LedgerError, TelegramError) as error:
        LOG.error("%s", error)
        return 1
    LOG.info("Delivered digest with %d articles.", len(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
