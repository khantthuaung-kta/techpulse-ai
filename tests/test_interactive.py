import json
import sqlite3
import time
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import requests

from techpulse import __main__ as cli
from techpulse.articles import Article, Category
from techpulse.interaction_store import CURSOR_LIFETIME, InteractionStore, polling_lock
from techpulse.interactive import (
    Access, InteractiveBot, NewsCache, parse_message, poll_updates, run_poller,
)
from techpulse.personalization import Preferences
from techpulse.telegram import TelegramError
from techpulse.telegram_polling import PollError, get_updates

TOKEN = "123:mock-secret-token"
OWNER = 1234
GROUP = -2345
BOT = "techpulse_test_bot"


def update(text, *, number=10, chat=OWNER, user=OWNER, kind="private", when=None):
    return {"update_id": number, "message": {
        "date": int(time.time()) if when is None else when,
        "chat": {"id": chat, "type": kind},
        "from": {"id": user, "is_bot": False},
        "text": text,
    }}


def reply(status, result):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(result).encode()
    return response


@pytest.fixture
def article():
    return Article("Python <3 & open tools", "https://example.org/?x=1&y=2", "Publisher",
                   datetime.now(UTC), Category.OPEN_SOURCE)


def test_access_requires_explicit_group_users(monkeypatch):
    monkeypatch.delenv("TELEGRAM_INTERACTIVE_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    assert Access.from_env(str(OWNER)) == Access(OWNER, frozenset({OWNER}))
    with pytest.raises(PollError, match="numeric"):
        Access.from_env("@channel")
    with pytest.raises(PollError, match="before polling a group"):
        Access.from_env(str(GROUP))
    monkeypatch.setenv("TELEGRAM_INTERACTIVE_CHAT_ID", str(GROUP))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", f"{OWNER}, 1235")
    assert Access.from_env("@channel") == Access(GROUP, frozenset({OWNER, 1235}))


def test_parser_authorizes_chat_and_sender_and_checks_address():
    access = Access(GROUP, frozenset({OWNER}))
    now = int(time.time())
    def parse(text, **kwargs):
        return parse_message(update(text, chat=GROUP, kind="supergroup", **kwargs), access, BOT, now)

    assert parse("/settings@TECHPULSE_TEST_BOT  AI") == (OWNER, "settings", "ai")
    assert parse("/latest@some_other_bot") is None
    assert parse("hello /latest") is None
    assert parse("/latest", user=999) is None
    assert parse("/latest", when=now - 3600) is None
    assert parse("/save 1\n/save 2") is None
    payload = update("/latest", chat=GROUP, kind="supergroup")
    payload["message"]["sender_chat"] = {"id": GROUP}
    assert parse_message(payload, access, BOT, now) is None
    assert parse_message(update("/latest"), access, BOT, now) is None
    assert parse_message({"update_id": 10, "channel_post": {}}, access, BOT, now) is None


def test_bookmark_settings_are_local_opt_in_and_scoped(tmp_path, article, monkeypatch):
    path = tmp_path / "state.sqlite3"
    store = InteractionStore(path)
    bot = InteractiveBot(TOKEN, Access(OWNER, frozenset({OWNER})), store, Mock())
    assert bot.reply("reader", "start", "", 100)[0] == bot.reply("reader", "help", "", 100)[0]
    assert "all" in bot.reply("reader", "settings", "", 100)[0][0]
    assert "opensource" in bot.reply("reader", "settings", "opensource", 100)[0][0]
    assert InteractionStore(path).preference("reader") == "opensource"
    assert store.preference("other") == "all"

    bot.news.get.return_value = [article]
    send = Mock()
    monkeypatch.setattr("techpulse.interactive.send_messages", send)
    bot.handle((OWNER, "latest", ""))
    message = send.call_args.args[0][0]
    assert "Python &lt;3 &amp; open tools" in message
    assert 'href="https://example.org/?x=1&amp;y=2"' in message
    reader, = bot.selections.keys()
    assert not store.bookmarks(reader)
    assert "saved" in bot.bookmark_reply(reader, "1", time.monotonic())
    assert "saved" in bot.bookmark_reply(reader, "1", time.monotonic())
    assert len(InteractionStore(path).bookmarks(reader)) == 1
    assert "https://example.org/?x=1&amp;y=2" in bot.bookmark_reply(reader, "", time.monotonic())
    assert not store.bookmarks("other")
    assert "no longer" in bot.bookmark_reply(reader, "1", time.monotonic() + 1000)
    assert "cleared" in bot.bookmark_reply(reader, "clear", time.monotonic())
    assert not store.bookmarks(reader)
    bot.reply("reader", "settings", "all", 100)
    assert store.preference("reader") == "all"


@pytest.mark.parametrize("command, topic", [
    ("latest", "ai"), ("ai", "ai"), ("japan", "japan"), ("opensource", "opensource"),
])
def test_all_news_commands_use_correct_topic(command, topic, tmp_path, article):
    store = InteractionStore(tmp_path / "state.sqlite3")
    store.set_preference("reader", "ai")
    news = Mock()
    news.get.return_value = [article]
    bot = InteractiveBot(TOKEN, Access(OWNER, frozenset({OWNER})), store, news)
    messages, selected = bot.reply("reader", command, "", 100)
    news.get.assert_called_once_with(topic)
    assert selected == [article] and "Original article" in messages[0]


def test_rate_limit_and_source_cache_prevent_repeated_requests(tmp_path, monkeypatch, article):
    clock = [10.0]
    monkeypatch.setattr("techpulse.interactive.time.monotonic", lambda: clock[0])
    fetch = Mock(side_effect=[([article], True), ([], False)])
    monkeypatch.setattr("techpulse.interactive.collect", fetch)
    send = Mock()
    monkeypatch.setattr("techpulse.interactive.send_messages", send)
    cache = NewsCache([], Preferences())
    bot = InteractiveBot(TOKEN, Access(OWNER, frozenset({OWNER})),
                         InteractionStore(tmp_path / "state.sqlite3"), cache)
    bot.handle((OWNER, "latest", ""))
    bot.handle((OWNER, "help", ""))
    assert send.call_count == 1
    clock[0] += 4
    bot.handle((OWNER, "opensource", ""))
    assert send.call_count == 2 and fetch.call_count == 1
    clock[0] += 301
    assert cache.get("opensource") == [article]
    assert cache.get("opensource") == [article]
    assert fetch.call_count == 2


def test_cli_poll_once_accepts_new_updates_and_preserves_cursor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "feeds.toml").write_text("feeds = []")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(OWNER))
    monkeypatch.setenv("TECHPULSE_INTERACTIVE_DB", str(tmp_path / "interactive.sqlite3"))
    monkeypatch.delenv("TELEGRAM_INTERACTIVE_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    post = Mock(side_effect=[
        reply(200, {"ok": True, "result": {"username": BOT}}),
        reply(200, {"ok": True, "result": [update("/help", number=10), update("/help", number=11, user=999)]}),
        reply(200, {"ok": True, "result": {"message_id": 42}}),
        reply(200, {"ok": True, "result": {"username": BOT}}),
        reply(200, {"ok": True, "result": [update("/help", number=10)]}),
    ])
    monkeypatch.setattr(requests, "post", post)
    args = ["--poll-telegram", "--poll-once"]
    assert cli.main(args) == cli.main(args) == 0
    assert [call.args[0].rsplit("/", 1)[-1] for call in post.call_args_list] == [
        "getMe", "getUpdates", "sendMessage", "getMe", "getUpdates",
    ]
    assert post.call_args_list[1].kwargs["json"]["offset"] == 0
    assert post.call_args_list[-1].kwargs["json"]["offset"] == 12
    assert post.call_args_list[1].kwargs["json"]["allowed_updates"] == ["message"]
    assert all(not call.kwargs["allow_redirects"] for call in post.call_args_list)
    assert not (tmp_path / ".techpulse-delivery.sqlite3").exists()


def test_poll_backoff_and_ambiguous_reply_does_not_replay(tmp_path, monkeypatch, caplog):
    store = InteractionStore(tmp_path / "state.sqlite3")
    bot = InteractiveBot(TOKEN, Access(OWNER, frozenset({OWNER})), store, Mock())
    batch = [update("/help", number=9)]
    get = Mock(side_effect=[PollError("Telegram rate limited polling.", retry=True, delay=2), batch, batch])
    sleep = Mock()
    send = Mock(side_effect=TelegramError("Timeout; delivery may have occurred"))
    monkeypatch.setattr("techpulse.interactive.get_updates", get)
    monkeypatch.setattr("techpulse.interactive.time.sleep", sleep)
    monkeypatch.setattr("techpulse.interactive.send_messages", send)
    poll_updates(bot, "bot", BOT, once=True)
    poll_updates(bot, "bot", BOT, once=True)
    assert get.call_args.args == (TOKEN, 10)
    send.assert_called_once()
    sleep.assert_called_once_with(2)
    assert TOKEN not in caplog.text


@pytest.mark.parametrize(("response", "message"), [
    (reply(409, {"ok": False, "description": TOKEN}), "conflict"),
    (reply(401, {"ok": False, "description": TOKEN}), "credentials"),
    (reply(429, {"ok": False, "parameters": {"retry_after": 999}}), "longer polling pause"),
    (reply(200, {"ok": True, "result": [{"update_id": None}]}), "invalid update batch"),
])
def test_poll_failures_hide_provider_payload(monkeypatch, response, message):
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(PollError, match=message) as error:
        get_updates(TOKEN, 0)
    assert TOKEN not in str(error.value)


def test_poll_lock_and_inactive_cursor(tmp_path, monkeypatch):
    path = tmp_path / "state.sqlite3"
    store = InteractionStore(path)
    store.acknowledge("bot", 20, 100)
    assert store.offset("bot", 110) == 20
    assert store.offset("bot", 100 + CURSOR_LIFETIME) == 0
    with polling_lock(path):
        with pytest.raises(sqlite3.Error):
            with polling_lock(path):
                pytest.fail("A second process entered the polling lock")
    monkeypatch.setenv("TELEGRAM_INTERACTIVE_CHAT_ID", "@not_a_numeric_chat")
    api = Mock()
    monkeypatch.setattr(requests, "post", api)
    assert run_poller(TOKEN, str(OWNER), [], Preferences(), once=True) == 1
    api.assert_not_called()
