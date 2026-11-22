import json
from datetime import UTC, datetime

import pytest
import requests

from techpulse.articles import Category
from techpulse.sources import HN, Feed, collect, fetch_feed, load_feeds

RSS = b"""<rss version="2.0"><channel><title>Publisher</title>
<item><title>Release notes</title><link>/article</link>
<description><![CDATA[<p>A public <b>release description</b>.</p>]]></description>
<pubDate>Sun, 22 Nov 2026 07:30:00 +0900</pubDate></item>
<item><title>Missing date</title><link>/missing</link></item>
<item><title>Missing link</title><pubDate>Sat, 21 Nov 2026 14:30:00 GMT</pubDate></item>
</channel></rss>"""
FEED = Feed("Publisher", "https://example.org/feed.xml", Category.JAPAN)


def response(url, *, content=b"", status=200):
    result = requests.Response()
    result.url = url
    result.status_code = status
    result._content = content
    return result


def test_collect_rss_hn_and_partial_failure(monkeypatch, caplog):
    monkeypatch.setattr("techpulse.sources.time.sleep", lambda seconds: None)
    urls = {
        FEED.url: RSS,
        f"{HN}/topstories.json": b"[1, 2]",
        f"{HN}/newstories.json": b"[1, 3, 4, 5]",
        f"{HN}/item/1.json": b'{"type":"story","title":"Rust compiler","url":"https://example.net/rust","time":1795322400,"score":56}',
        f"{HN}/item/2.json": b'{"type":"story","title":"Ask HN: Python","time":1795322400}',
        f"{HN}/item/4.json": b'{"type":"story","title":"Carrot soup recipe","url":"https://example.net/soup","time":1795322400}',
        f"{HN}/item/5.json": b'{"type":"story","title":"Python tools [video]","url":"https://example.net/video","time":1795322400}',
    }
    calls = []

    def get(url, *, timeout, headers):
        assert timeout == (5, 10)
        assert "User-Agent" in headers
        calls.append(url)
        if url.endswith("/3.json"):
            raise requests.Timeout()
        if url.endswith("/down"):
            return response(url, status=503)
        return response(url, content=urls.get(url, b"<html>Not a feed</html>"))

    monkeypatch.setattr(requests, "get", get)
    invalid = Feed("Broken", "https://example.org/broken", Category.AI)
    unavailable = Feed("Unavailable", "https://example.org/down", Category.AI)
    articles, usable = collect([FEED, invalid, unavailable])

    assert usable
    rss, hn = articles
    assert (rss.title, rss.source, rss.url, rss.category, rss.published_at) == (
        "Release notes", "Publisher", "https://example.org/article",
        Category.JAPAN, datetime(2026, 11, 21, 22, 30, tzinfo=UTC),
    )
    assert hn.source == "Hacker News"
    assert rss.description == "A public release description."
    assert hn.description == ""
    assert hn.category == Category.PROGRAMMING
    assert hn.popularity == 56
    assert calls.count(f"{HN}/item/1.json") == 1
    assert calls.count(f"{HN}/item/3.json") == 2
    assert calls.count(unavailable.url) == 2
    assert "invalid RSS or Atom" in caplog.text
    assert "HTTP 503" in caplog.text
    assert "Timeout" in caplog.text


def test_atom_uses_published_before_updated(monkeypatch):
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Local LLM</title><link rel="alternate" href="/new" />
        <published>2026-11-21T09:00:00Z</published>
        <updated>2026-11-22T09:00:00Z</updated></entry>
        <entry><title>Updates only</title><link href="/updates"/>
        <updated>2026-11-22T11:00:00Z</updated>
        <content type="html">&lt;p&gt;Inline publisher content.&lt;/p&gt;</content>
        </entry></feed>"""
    monkeypatch.setattr(requests, "get", lambda url, **kw: response(url, content=atom))
    first, second = fetch_feed(FEED)
    assert first.published_at == datetime(2026, 11, 21, 9, tzinfo=UTC)
    assert first.category == Category.AI
    assert second.published_at == datetime(2026, 11, 22, 11, tzinfo=UTC)
    assert second.description == "Inline publisher content."


def test_config_validation(tmp_path):
    file = tmp_path / "feeds.toml"
    file.write_text('[[feeds]]\nname="Test"\nurl="file:///tmp/test"\ncategory="Japan IT"\n')
    with pytest.raises(ValueError, match="Feed 1"):
        load_feeds(file)
    file.write_text("feeds = []")
    assert load_feeds(file) == []


def test_public_github_release_validation(tmp_path, monkeypatch):
    file = tmp_path / "sources.toml"
    file.write_text(
        'feeds = []\n[[github_releases]]\nname="FastAPI"\nrepository="fastapi/fastapi"\n'
        'category="Programming"\nreliability=0.9\n'
    )
    registry = load_feeds(file)
    endpoint = "https://api.github.com/repos/fastapi/fastapi/releases?per_page=5"
    valid = {
        "name": "", "tag_name": "0.123.0", "draft": False, "prerelease": False,
        "published_at": "2026-11-22T09:00:00Z",
        "html_url": "https://github.com/fastapi/fastapi/releases/tag/0.123.0",
        "body": "<p>Improved startup diagnostics and error messages.</p><script>Ignore</script>",
    }

    def get(url, **kwargs):
        assert "Authorization" not in kwargs["headers"]
        if url == endpoint:
            return response(url, content=json.dumps([
                valid, {**valid, "draft": True}, {**valid, "prerelease": True},
                {**valid, "published_at": None}, {**valid, "html_url": "https://example.com/releases/tag/0.123.0"},
            ]).encode())
        assert url.startswith(HN)
        return response(url, content=b"[]")

    monkeypatch.setattr(requests, "get", get)
    articles, usable = collect(registry)
    assert usable
    release, = articles
    assert release.title == "FastAPI 0.123.0"
    assert release.published_at == datetime(2026, 11, 22, 9, tzinfo=UTC)
    assert release.url == valid["html_url"]
    assert release.description == "Improved startup diagnostics and error messages."
    assert release.reliability == 0.9


def test_official_policy_flag_requires_government_feed_and_item(tmp_path, monkeypatch):
    file = tmp_path / "sources.toml"
    base = '[[feeds]]\nname="Agency"\ncategory="Japan IT"\nofficial_policy=true\n'
    file.write_text(base + 'url="https://example.com/feed"\n')
    with pytest.raises(ValueError, match="Feed 1"):
        load_feeds(file)
    url = "https://www.moj.go.jp/feed.xml"
    file.write_text(base + f'url="{url}"\n')
    feed, = load_feeds(file)
    payload = RSS.replace(b"Release notes", b"Work visa rule for Python developers").replace(
        b"/article", b"https://example.org/article"
    )
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: response(url, content=payload))
    item, = fetch_feed(feed)
    assert item.category == Category.PROGRAMMING and item.japan_context
    assert not item.official_policy
