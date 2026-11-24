from datetime import UTC, datetime
from html import unescape
import re
import subprocess
import sys
from unittest.mock import MagicMock, Mock

import pytest
import requests

from techpulse import __main__ as cli
from techpulse.articles import Article, Category
from techpulse.digest import TELEGRAM_LIMIT, render_digest, telegram_length
from techpulse.telegram import TelegramError, send_digest


@pytest.fixture
def article():
    return Article("Python <3 & tools", "https://example.org/?x=1&y=2", "News & Views",
                   datetime.now(UTC), Category.PROGRAMMING)


@pytest.fixture
def configured(tmp_path, monkeypatch, article):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "feeds.toml").write_text("feeds = []")
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123:test-token\nTELEGRAM_CHAT_ID=-555\n")
    monkeypatch.setattr(cli, "collect", lambda feeds: ([article], True))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("TECHPULSE_RUN_CREATED_AT", raising=False)
    monkeypatch.setenv("TECHPULSE_DELIVERY_DB", str(tmp_path / "delivery.sqlite3"))
    # dotenv mutates the process environment. Track both keys for fixture cleanup.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")


def test_dry_run(configured, capsys, monkeypatch, article):
    def unexpected_send(*args, **kwargs):
        pytest.fail("Dry run attempted delivery")

    monkeypatch.setattr(requests, "post", unexpected_send)
    assert cli.main(["--dry-run"]) == 0
    text = capsys.readouterr().out
    assert article.title in text
    assert article.url in text
    assert "Programming · News & Views" in text


def test_dry_run_explain_shows_selected_score(configured, capsys, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "off")
    assert cli.main(["--dry-run", "--explain"]) == 0
    text = capsys.readouterr().out
    assert "Ranking factors" in text and "interests 1.00 (python)" in text


def test_send_reads_dotenv_and_posts_escaped_html(configured, monkeypatch):
    def post(url, *, json, timeout, allow_redirects):
        assert url == "https://api.telegram.org/bot123:test-token/sendMessage"
        assert json["chat_id"] == "-555"
        assert json["parse_mode"] == "HTML"
        assert "Python &lt;3 &amp; tools" in json["text"]
        assert 'href="https://example.org/?x=1&amp;y=2"' in json["text"]
        assert json["link_preview_options"]["is_disabled"]
        assert timeout and not allow_redirects
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true,"result":{"message_id":12}}'
        return response

    delivered = Mock(side_effect=post)
    monkeypatch.setattr(requests, "post", delivered)
    assert cli.main(["--send"]) == 0
    assert cli.main(["--send"]) == 0
    delivered.assert_called_once()


def test_empty_send_leaves_no_daily_claim(configured, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(cli, "collect", lambda feeds: ([], True))
    post = Mock()
    monkeypatch.setattr(requests, "post", post)
    assert cli.main(["--send"]) == 0
    assert "No eligible unsent stories" in caplog.text
    post.assert_not_called()


def test_src_module_exposes_telegram_command():
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "--help"],
        check=True, capture_output=True, text=True,
    )
    assert "--test-telegram" in result.stdout


def test_telegram_connection_message(configured, monkeypatch, caplog):
    def post(url, *, json, timeout, allow_redirects):
        assert url == "https://api.telegram.org/bot123:test-token/sendMessage"
        assert json["chat_id"] == "-555"
        assert json["text"] == "✅ TechPulse AI is connected successfully!"
        assert "parse_mode" not in json
        assert timeout == (5, 10)
        assert not allow_redirects
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true}'
        return response

    monkeypatch.setattr(requests, "post", post)
    assert cli.main(["--test-telegram", "--feeds", "absent.toml"]) == 0
    assert "test-token" not in caplog.text


@pytest.mark.parametrize("status, reason", [(None, "Timeout"), (401, "HTTP 401"), (200, "HTTP 200")])
def test_telegram_connection_failure(configured, monkeypatch, caplog, status, reason):
    def post(url, **kwargs):
        if status is None:
            raise requests.Timeout(url)
        response = requests.Response()
        response.status_code = status
        response.url = url
        response._content = b'{"ok":false,"description":"123:test-token"}'
        return response

    monkeypatch.setattr(requests, "post", post)
    assert cli.main(["--test-telegram"]) == 1
    assert reason in caplog.text
    assert "test-token" not in caplog.text


def test_all_sources_fail(configured, monkeypatch, caplog):
    monkeypatch.setattr(cli, "collect", lambda feeds: ([], False))
    assert cli.main(["--dry-run"]) == 1
    assert "All news sources failed" in caplog.text


def test_successful_collection_without_matches(configured, monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect", lambda feeds: ([], True))
    assert cli.main(["--dry-run"]) == 0
    assert "No recent articles matched." in capsys.readouterr().out


def test_digest_splits_between_complete_entries(article):
    articles = [
        Article("😀" * 400, f"https://example.org/{i}", "Publisher",
                article.published_at, article.category)
        for i in range(10)
    ]
    messages = render_digest(articles, article.published_at, telegram=True)
    assert len(messages) > 1
    for message in messages:
        visible = unescape(re.sub(r"<[^>]*>", "", message))
        assert telegram_length(visible) <= TELEGRAM_LIMIT
    for item in articles:
        assert sum(f'href="{item.url}"' in message for message in messages) == 1


def test_telegram_failure_redacts_token(monkeypatch):
    def post(url, **kwargs):
        raise requests.Timeout(url)

    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TelegramError, match="0/1 confirmed messages.*Timeout") as error:
        send_digest(["Digest"], "123:test-token", "-555")
    assert "test-token" not in str(error.value)


def test_telegram_reports_partial_delivery(monkeypatch):
    responses = []
    for status in (200, 429):
        response = requests.Response()
        response.status_code = status
        response._content = b'{"ok":true}' if status == 200 else b'{"ok":false}'
        responses.append(response)
    responses = iter(responses)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: next(responses))
    with pytest.raises(TelegramError, match="1/3 confirmed messages.*HTTP 429"):
        send_digest(["One", "Two", "Three"], "123:test-token", "-555")


def test_dot_digest_uses_existing_claim_and_treats_text_as_plain(configured, monkeypatch):
    message = 'မြန်မာ့သတင်း <b> & source https://example.org/?a=1&b=2'
    get = Mock(return_value=message)
    monkeypatch.setattr(cli, "read_dot_digest", get)
    monkeypatch.setattr(cli, "collect", Mock(side_effect=AssertionError("unexpected fallback")))
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"ok":true,"result":{"message_id":134}}'
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    assert cli.main(["--send"]) == 0
    assert cli.main(["--send"]) == 0
    get.assert_called_once()
    post.assert_called_once()
    payload = post.call_args.kwargs["json"]
    assert "<b>" not in payload["text"]
    assert unescape(payload["text"]) == message


def test_dot_preview_is_private_and_never_posts(configured, monkeypatch, caplog, capsys):
    preview = Mock(return_value="PRIVATE DRAFT")
    post = Mock(side_effect=AssertionError("unexpected send"))
    monkeypatch.setattr(cli, "read_dot_digest", preview)
    monkeypatch.setattr(requests, "post", post)
    caplog.set_level("INFO")
    assert cli.main(["--check-dot-preview=2026-11-24"]) == 0
    assert "PRIVATE DRAFT" not in caplog.text + capsys.readouterr().out
    assert "validated" in caplog.text
    assert preview.call_args.kwargs == {"preview": True}
    post.assert_not_called()


@pytest.mark.parametrize("fail_second", [False, True])
def test_long_dot_edition_uses_one_multipart_claim(configured, monkeypatch, fail_second):
    source = "\n\n".join(
        f"{i}. မြန်မာ့သတင်း & အလုပ်အကိုင်\n"
        + "အနှစ်ချုပ်: လုပ်ငန်းအသစ်တွေမှာ နည်းပညာကျွမ်းကျင်မှု ပိုလိုလာတယ်။ " * 7
        + "\nဘာကြောင့်သိသင့်လဲ: လျှောက်မယ့်အလုပ်အတွက် လိုအပ်ချက်ကို ကြိုသိနိုင်တယ်။ " * 7
        + "\nဘာလုပ်ကြည့်ရမလဲ: ကြေညာချက်ထဲက လိုအပ်တဲ့ skill ကို စာရင်းလုပ်ပါ။ " * 5
        + f"\nhttps://example.org/news?id={i}&edition=today"
        for i in range(1, 16)
    )
    response = MagicMock(status_code=200)
    response.__enter__.return_value = response
    raw = source.encode("utf-8")
    response.iter_content.return_value = [raw[i:i + 4096] for i in range(0, len(raw), 4096)]
    get = Mock(return_value=response)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setenv("TECHPULSE_DIGEST_READ_TOKEN", "unit-test-placeholder")
    monkeypatch.setattr(cli, "collect", Mock(side_effect=AssertionError("unexpected fallback")))
    posts = []

    def post(url, **kwargs):
        response = requests.Response()
        posts.append(kwargs["json"])
        if len(posts) == 2 and fail_second:
            response.status_code = 400
            response._content = b'{"ok":false}'
        else:
            response.status_code = 200
            response._content = f'{{"ok":true,"result":{{"message_id":{200 + len(posts)}}}}}'.encode()
        return response

    monkeypatch.setattr(requests, "post", post)
    code = 1 if fail_second else 0
    assert cli.main(["--send"]) == code
    claim = cli.open_ledger().read(cli.delivery_date(datetime.now(UTC)))
    assert claim is not None
    record, _ = claim
    expected_parts = len(cli.split_dot_digest(source))
    assert expected_parts >= 4 and record.parts == expected_parts
    assert cli.main(["--send"]) == code
    assert get.call_count == 1
    if fail_second:
        assert len(posts) == 2 and record.status == "failed" and list(record.confirmed) == ["201"]
    else:
        assert len(posts) == expected_parts and record.status == "sent"
        assert "".join(unescape(posted["text"]).split("\n\n", 1)[1] for posted in posts) == source
    assert all(telegram_length(unescape(posted["text"])) <= TELEGRAM_LIMIT for posted in posts)
