import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

from techpulse.articles import Article, Category
from techpulse.delivery import already_claimed, deliver, delivery_date
from techpulse.digest import render_digest
from techpulse.ledger import (
    STATE_BRANCH, ClaimConflict, DeliveryRecord, GitHubLedger, LedgerError, SQLiteLedger,
    article_fingerprints, destination_key, sent_history,
)
from techpulse.telegram import TelegramError, send_digest

DAY = date(2026, 11, 22)
BOT = "123:mock-token"
CHAT = "-555"


def reply(status, data):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    return response


def test_atomic_local_claim_across_independent_connections(tmp_path):
    path = tmp_path / "state.sqlite3"
    SQLiteLedger(path)

    def claim(_):
        try:
            SQLiteLedger(path).write(DAY, DeliveryRecord("destination", "batch", 1), None)
            return True
        except ClaimConflict:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(4))) == 1


def test_partial_delivery_is_retained_and_history_uses_only_confirmed_articles(tmp_path, monkeypatch):
    path = tmp_path / "state.sqlite3"
    ledger = SQLiteLedger(path)
    destination = destination_key(BOT, CHAT)
    now = datetime(2026, 11, 22, 5, tzinfo=UTC)
    articles = [
        Article(title, f"https://example.org/{index}?x=1&y=2", "Publisher", now, Category.PROGRAMMING)
        for index, title in enumerate(("Python first", "Python second"))
    ]
    messages = [render_digest([article], now, telegram=True)[0] for article in articles]
    post = Mock(side_effect=[reply(200, {"ok": True, "result": {"message_id": 11}}), requests.Timeout(BOT)])
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TelegramError, match="1/2 confirmed messages"):
        deliver(ledger, DAY, destination, articles, messages, BOT, CHAT)
    restarted = SQLiteLedger(path)
    record, _ = restarted.read(DAY)
    assert record.status == "failed"
    assert record.confirmed == {"11": sorted(article_fingerprints(articles[0]))}
    assert BOT not in record.encode() and CHAT not in record.encode()
    assert sent_history(restarted, DAY + timedelta(days=1), destination) == article_fingerprints(articles[0])
    with pytest.raises(LedgerError, match="already claimed"):
        already_claimed(restarted, DAY)
    assert post.call_count == 2


def test_github_create_conflict_reads_existing_record(monkeypatch):
    current = DeliveryRecord("destination", "batch", 1, "sent", {"42": ["article"]})
    response = reply(200, {"encoding": "base64", "sha": "b" * 40,
                           "content": base64.b64encode(current.encode().encode()).decode()})
    request = Mock(side_effect=[reply(200, {}), reply(422, {}), response])
    monkeypatch.setattr(requests, "request", request)
    ledger = GitHubLedger("owner/repo", "mock-github-token", "a" * 40)
    assert not deliver(ledger, DAY, "other", [], ["Digest"], BOT, CHAT)
    assert [call.args[0] for call in request.call_args_list] == ["GET", "PUT", "GET"]
    payload = request.call_args_list[1].kwargs["json"]
    assert payload["branch"] == STATE_BRANCH and "sha" not in payload
    assert all(call.kwargs["allow_redirects"] is False for call in request.call_args_list)


def test_github_unavailable_after_claim_conflict_fails_closed(monkeypatch):
    request = Mock(side_effect=[reply(200, {}), reply(422, {}), reply(404, {})])
    monkeypatch.setattr(requests, "request", request)
    post = Mock()
    monkeypatch.setattr(requests, "post", post)
    ledger = GitHubLedger("owner/repo", "mock-github-token", "a" * 40)
    with pytest.raises(ClaimConflict):
        deliver(ledger, DAY, "destination", [], ["Digest"], BOT, CHAT)
    post.assert_not_called()


def test_github_branch_bootstrap_handles_creation_race(monkeypatch):
    request = Mock(side_effect=[reply(404, {}), reply(422, {}), reply(200, {})])
    monkeypatch.setattr(requests, "request", request)
    GitHubLedger("owner/repo", "mock-github-token", "a" * 40)
    assert request.call_args_list[1].kwargs["json"] == {"ref": f"refs/heads/{STATE_BRANCH}", "sha": "a" * 40}


def test_github_rerun_uses_original_yangon_date(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("TECHPULSE_RUN_CREATED_AT", "2026-11-21T18:00:00Z")
    assert delivery_date(datetime(2026, 11, 25, 12, tzinfo=UTC)) == DAY
    monkeypatch.delenv("TECHPULSE_RUN_CREATED_AT")
    with pytest.raises(LedgerError, match="original GitHub run timestamp"):
        delivery_date(datetime(2026, 11, 25, 12, tzinfo=UTC))


def test_telegram_retries_explicit_short_rate_limit_once(monkeypatch):
    post = Mock(side_effect=[
        reply(429, {"ok": False, "parameters": {"retry_after": 2}}),
        reply(200, {"ok": True, "result": {"message_id": 42}}),
    ])
    monkeypatch.setattr(requests, "post", post)
    sleep = Mock()
    monkeypatch.setattr("techpulse.telegram.time.sleep", sleep)
    confirm = Mock()
    send_digest(["Digest"], BOT, CHAT, on_confirmed=confirm)
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["json"] == post.call_args_list[1].kwargs["json"]
    sleep.assert_called_once_with(2)
    confirm.assert_called_once_with(0, 42)


@pytest.mark.parametrize("response", [
    reply(429, {"ok": False, "parameters": {"retry_after": 120}}),
    reply(500, {"ok": False}),
])
def test_telegram_does_not_repeat_long_rate_limit_or_ambiguous_error(monkeypatch, response):
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TelegramError, match=f"HTTP {response.status_code}"):
        send_digest(["Digest"], BOT, CHAT)
    post.assert_called_once()


def test_github_successful_receipt_survives_a_fresh_runner(monkeypatch):
    saved = {}
    def request(method, url, **options):
        if "/git/ref/heads/" in url:
            return reply(200, {})
        if method == "GET":
            return reply(200, saved[url]) if url in saved else reply(404, {})
        assert method == "PUT"
        payload = options["json"]
        previous = saved.get(url)
        assert payload.get("sha") == (previous["sha"] if previous else None)
        sha = "c" * 40 if previous else "b" * 40
        saved[url] = {"content": payload["content"], "encoding": "base64", "sha": sha}
        return reply(200 if previous else 201, {"content": {"sha": sha}})

    monkeypatch.setattr(requests, "request", request)
    post = Mock(return_value=reply(200, {"ok": True, "result": {"message_id": 42}}))
    monkeypatch.setattr(requests, "post", post)
    args = ("owner/repo", "mock-github-token", "a" * 40)
    now = datetime(2026, 11, 22, 5, tzinfo=UTC)
    article = Article("Python release", "https://example.org/python", "Publisher", now, Category.PROGRAMMING)
    messages = render_digest([article], now, telegram=True)
    destination = destination_key(BOT, CHAT)
    assert deliver(GitHubLedger(*args), DAY, destination, [article], messages, BOT, CHAT)
    restarted = GitHubLedger(*args)
    assert already_claimed(restarted, DAY)
    assert sent_history(restarted, DAY + timedelta(days=1), destination) == article_fingerprints(article)
    post.assert_called_once()


def test_receipt_write_failure_stops_before_next_message(tmp_path, monkeypatch):
    ledger = SQLiteLedger(tmp_path / "state.sqlite3")
    write = ledger.write
    def fail_after_claim(day, record, revision):
        if revision is not None:
            raise LedgerError("unavailable")
        return write(day, record, revision)

    monkeypatch.setattr(ledger, "write", fail_after_claim)
    post = Mock(return_value=reply(200, {"ok": True, "result": {"message_id": 42}}))
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(LedgerError, match="Telegram acknowledged a message"):
        deliver(ledger, DAY, "destination", [], ["One", "Two"], BOT, CHAT)
    post.assert_called_once()
    with pytest.raises(LedgerError, match="already claimed"):
        already_claimed(ledger, DAY)


def test_corrupt_record_never_treated_as_an_unused_day():
    with pytest.raises(LedgerError, match="Invalid delivery ledger record"):
        DeliveryRecord.decode('{"destination":"opaque","batch":"hash","parts":2,"status":"sent","confirmed":{}}')
