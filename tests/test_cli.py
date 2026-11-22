from datetime import UTC, datetime
from html import unescape
import re
import subprocess
import sys

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
        response._content = b'{"ok":true}'
        return response

    monkeypatch.setattr(requests, "post", post)
    assert cli.main(["--send"]) == 0


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
