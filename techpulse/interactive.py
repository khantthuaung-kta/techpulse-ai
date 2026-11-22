"""Owner-only Telegram commands and local polling."""

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from .articles import Article, Category
from .digest import render_digest
from .interaction_store import InteractionStore, polling_lock
from .ledger import destination_key, fingerprint
from .personalization import Preferences, rank_digest
from .sources import Feed, ReleaseSource, collect
from .telegram import TelegramError, send_messages
from .telegram_polling import PollError, get_updates, get_username

LOG = logging.getLogger(__name__)
COMMAND = re.compile(r"/([a-zA-Z]+)(?:@([A-Za-z0-9_]{1,32}))?(?:\s+(.*))?", re.ASCII)
COMMAND_INTERVAL = 3
NEWS_TTL = 300
NEWS_STALE_LIMIT = 1200
NEWS_FAILURE_DELAY = 60
MAX_UPDATE_AGE = 600
MAX_POLL_FAILURES = 5
RECENT_SELECTION_TTL = 900
TOPICS = {"ai": Category.AI, "japan": Category.JAPAN, "opensource": Category.OPEN_SOURCE}
HELP = """TechPulse AI commands

/latest · Show latest stories using your topic preference
/ai · AI and LLM stories
/japan · Japan IT stories
/opensource · Open source stories
/settings · Show your topic preference
/settings all | ai | japan | opensource · Set your topic preference
/save 1 · Bookmark story 1 from your latest results
/save · List your bookmarks
/save clear · Remove your bookmarks
/help · Show these commands"""


@dataclass(frozen=True, slots=True)
class Access:
    chat_id: int
    user_ids: frozenset[int]

    @classmethod
    def from_env(cls, daily_chat: str) -> "Access":
        raw_chat = os.getenv("TELEGRAM_INTERACTIVE_CHAT_ID", "").strip() or daily_chat
        if not re.fullmatch(r"-?[1-9][0-9]{0,15}", raw_chat):
            raise PollError("Set a numeric TELEGRAM_INTERACTIVE_CHAT_ID for a private chat or group.")
        chat_id = int(raw_chat)
        if abs(chat_id) >= 2**52:
            raise PollError("Interactive chat ID is out of range.")
        raw_users = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        if raw_users:
            values = [value.strip() for value in raw_users.split(",")]
            if len(values) > 20 or any(not re.fullmatch(r"[1-9][0-9]{0,15}", value) for value in values):
                raise PollError("TELEGRAM_ALLOWED_USER_IDS must contain up to 20 positive numeric IDs.")
            users = frozenset(map(int, values))
            if any(user >= 2**52 for user in users):
                raise PollError("An allowed Telegram user ID is out of range.")
        elif chat_id > 0:
            users = frozenset({chat_id})
        else:
            raise PollError("Set TELEGRAM_ALLOWED_USER_IDS before polling a group.")
        return cls(chat_id, users)


def parse_message(update: dict, access: Access, bot_username: str, now: int) -> tuple[int, str, str] | None:
    message = update.get("message")
    if not isinstance(message, dict) or "sender_chat" in message:
        return None
    chat, sender = message.get("chat"), message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None
    chat_id, user_id = chat.get("id"), sender.get("id")
    if (
        type(chat_id) is not int or chat_id != access.chat_id
        or chat.get("type") not in {"private", "group", "supergroup"}
        or type(user_id) is not int or user_id not in access.user_ids
        or sender.get("is_bot") is not False
        or (chat.get("type") == "private" and user_id != chat_id)
    ):
        return None
    text, when = message.get("text"), message.get("date")
    if (
        not isinstance(text, str) or len(text) > 120 or type(when) is not int
        or not -30 <= now - when <= MAX_UPDATE_AGE
    ):
        return None
    command = COMMAND.fullmatch(text.strip())
    if not command:
        return None
    name, mention, argument = command.groups()
    if mention and mention.casefold() != bot_username:
        return None
    return user_id, name.casefold(), (argument or "").strip().casefold()


class NewsCache:
    """Reuse public source results and cap refreshes during a source outage."""

    def __init__(self, sources: list[Feed | ReleaseSource], preferences: Preferences) -> None:
        self.sources = sources
        self.preferences = preferences
        self.articles: list[Article] | None = None
        self.loaded_at = 0.0
        self.next_refresh = 0.0

    def get(self, topic: str) -> list[Article] | None:
        clock = time.monotonic()
        if clock >= self.next_refresh:
            articles, usable = collect(self.sources)
            clock = time.monotonic()
            if usable:
                self.articles = articles
                self.loaded_at = clock
                self.next_refresh = clock + NEWS_TTL
            else:
                self.next_refresh = clock + NEWS_FAILURE_DELAY
        if self.articles is None or clock - self.loaded_at > NEWS_STALE_LIMIT:
            return None
        candidates = [article for article in self.articles if topic == "all" or article.category == TOPICS[topic]]
        return [story.article for story in rank_digest(candidates, datetime.now(UTC), self.preferences)]


class InteractiveBot:
    def __init__(self, token: str, access: Access, store: InteractionStore, news: NewsCache) -> None:
        self.token = token
        self.access = access
        self.store = store
        self.news = news
        self.last_command: dict[str, float] = {}
        self.selections: dict[str, tuple[float, list[Article]]] = {}

    def handle(self, command: tuple[int, str, str]) -> None:
        user_id, name, argument = command
        reader = destination_key(self.token, f"{self.access.chat_id}:{user_id}")
        clock = time.monotonic()
        if clock - self.last_command.get(reader, float("-inf")) < COMMAND_INTERVAL:
            return
        self.last_command[reader] = clock
        messages, selection = self.reply(reader, name, argument, clock)
        try:
            send_messages(messages, self.token, str(self.access.chat_id), parse_mode="HTML")
        except TelegramError as error:
            LOG.warning("Interactive Telegram reply failed (%s). The update will not be replayed.", error)
            return
        if selection is not None:
            self.selections[reader] = time.monotonic(), selection

    def reply(self, reader: str, name: str, argument: str, clock: float) -> tuple[list[str], list[Article] | None]:
        if name in {"start", "help"}:
            return [HELP], None
        if name in {"latest", *TOPICS}:
            if argument:
                return [f"Use /{name} without arguments. See /help."], None
            topic = self.store.preference(reader) if name == "latest" else name
            if topic not in {"all", *TOPICS}:
                topic = "all"
            articles = self.news.get(topic)
            if articles is None:
                return ["News sources are temporarily unavailable. Try again later."], []
            return render_digest(articles, datetime.now(UTC), telegram=True), articles
        if name == "settings":
            if argument in {"all", *TOPICS}:
                self.store.set_preference(reader, argument)
            elif argument:
                return ["Use /settings all, ai, japan, or opensource."], None
            topic = escape(self.store.preference(reader))
            return [f"Latest topic: {topic}. Choose /settings all, ai, japan, or opensource."], None
        if name == "save":
            return [self.bookmark_reply(reader, argument, clock)], None
        return ["Unknown command. Use /help to see the available commands."], None

    def bookmark_reply(self, reader: str, argument: str, clock: float) -> str:
        if not argument:
            bookmarks = self.store.bookmarks(reader)
            if not bookmarks:
                return "No bookmarks yet. Run /latest, then use /save 1."
            links = [f'<a href="{escape(item.url, quote=True)}">{escape(item.title)}</a>' for item in bookmarks]
            return "<b>Bookmarks</b>\n" + "\n".join(links)
        if argument == "clear":
            self.store.clear_bookmarks(reader)
            return "Bookmarks cleared."
        if not re.fullmatch(r"[1-9]|10", argument):
            return "Use /save followed by a story number from your latest results."
        selected_at, articles = self.selections.get(reader, (0.0, []))
        index = int(argument) - 1
        if clock - selected_at > RECENT_SELECTION_TTL or index >= len(articles):
            return "That story is no longer in your recent results. Run /latest first."
        article = articles[index]
        self.store.save(reader, article)
        return "Bookmark saved. Use /save to see your bookmarks."


def run_poller(
    token: str, daily_chat: str, feeds: list[Feed | ReleaseSource], preferences: Preferences, *, once: bool,
) -> int:
    try:
        access = Access.from_env(daily_chat)
        path = Path(os.getenv("TECHPULSE_INTERACTIVE_DB") or ".techpulse-interactive.sqlite3").resolve()
        with polling_lock(path):
            store = InteractionStore(path)
            username = get_username(token)
            # The bot ID in the token prefix survives a token rotation.
            bot_key = fingerprint(token.split(":", 1)[0])
            bot = InteractiveBot(token, access, store, NewsCache(feeds, preferences))
            LOG.info("Local Telegram polling started.")
            poll_updates(bot, bot_key, username, once)
    except (sqlite3.Error, OSError, ValueError):
        LOG.error("Could not use interactive SQLite state. Check the path and ensure only one local poller is running.")
        return 1
    except PollError as error:
        LOG.error("%s", error)
        return 1
    except KeyboardInterrupt:
        LOG.info("Local Telegram polling stopped.")
    return 0


def poll_updates(bot: InteractiveBot, key: str, username: str, once: bool) -> None:
    failures = 0
    while True:
        offset = bot.store.offset(key, int(time.time()))
        try:
            updates = get_updates(bot.token, offset)
        except PollError as error:
            failures += 1
            if not error.retry or failures >= MAX_POLL_FAILURES:
                raise
            delay = error.delay or 2 ** (failures - 1)
            LOG.warning("%s Retrying polling in %d seconds.", error, delay)
            time.sleep(delay)
            continue
        failures = 0
        for update in updates:
            update_id = update["update_id"]
            if update_id < offset:
                continue
            offset = update_id + 1
            now = int(time.time())
            # Save before side effects to avoid replaying a reply after a crash.
            bot.store.acknowledge(key, offset, now)
            command = parse_message(update, bot.access, username, now)
            if command is not None:
                bot.handle(command)
        if once:
            return
