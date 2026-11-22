"""Collect dated articles from Hacker News and trusted RSS/Atom feeds."""

import logging
import re
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import feedparser
import requests

from .articles import DESCRIPTION_LIMIT, Article, Category, classify, valid_url

LOG = logging.getLogger(__name__)
HN = "https://hacker-news.firebaseio.com/v0"
TIMEOUT = (5, 10)
USER_AGENT = "TechPulseAI/0.1 (technology digest reader)"
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")


class SourceError(Exception):
    """A source did not return usable data."""


class DescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        elif tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


def plain_description(value: str) -> str:
    parser = DescriptionParser()
    try:
        parser.feed(value[:DESCRIPTION_LIMIT * 4])
        parser.close()
    except (ValueError, AssertionError):
        return ""
    return " ".join("".join(parser.parts).split())[:DESCRIPTION_LIMIT]


@dataclass(frozen=True, slots=True)
class Feed:
    name: str
    url: str
    category: Category
    reliability: float = 0.75
    official_policy: bool = False


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    name: str
    repository: str
    category: Category
    reliability: float = 0.95


def load_feeds(path: Path) -> list[Feed | ReleaseSource]:
    with path.open("rb") as file:
        config = tomllib.load(file)
    if not config.keys() & {"feeds", "github_releases"}:
        raise ValueError("Expected feeds or github_releases in the source registry.")
    sources: list[Feed | ReleaseSource] = []
    for section, label in (("feeds", "Feed"), ("github_releases", "Release source")):
        entries = config.get(section, [])
        if not isinstance(entries, list):
            raise ValueError(f"Expected {section} to be an array.")
        for index, entry in enumerate(entries, start=1):
            try:
                name = entry["name"].strip()
                category = Category(entry["category"])
                reliability = entry.get("reliability", 0.75 if section == "feeds" else 0.95)
                if not name or type(reliability) not in {int, float} or not 0 <= reliability <= 1:
                    raise ValueError
                if section == "feeds":
                    url = entry["url"]
                    official = entry.get("official_policy", False)
                    if not valid_url(url) or type(official) is not bool:
                        raise ValueError
                    if official and (
                        category != Category.JAPAN or not is_japan_government_url(url)
                    ):
                        raise ValueError
                    sources.append(Feed(name, url, category, reliability, official))
                else:
                    repository = entry["repository"]
                    if not REPOSITORY.fullmatch(repository):
                        raise ValueError
                    sources.append(ReleaseSource(name, repository, category, reliability))
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ValueError(f"{label} {index} has an invalid name, location, category or reliability.") from None
    return sources


def get(url: str) -> requests.Response:
    response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response


def fetch_feed(feed: Feed) -> list[Article]:
    response = get(feed.url)
    parsed = feedparser.parse(response.content, response_headers={
        "content-location": response.url,
        "content-type": response.headers.get("Content-Type", "application/xml"),
    })
    if not parsed.version or (parsed.bozo and not parsed.entries):
        raise SourceError("invalid RSS or Atom document")
    if parsed.bozo:
        LOG.warning("Feed %s is malformed. Processing recovered entries.", feed.name)

    articles = []
    for entry in parsed.entries:
        title = " ".join(unescape(entry.get("title", "")).split())
        url = urljoin(response.url, entry.get("link", ""))
        # Atom permits entries with only an updated timestamp.
        date = entry.get("published_parsed") or (
            entry.get("updated_parsed") if parsed.version.startswith("atom") else None
        )
        if not title or not entry.get("link") or not valid_url(url) or not date:
            continue
        try:
            published = datetime(*date[:6], tzinfo=UTC)
        except ValueError:
            continue
        description = plain_description(entry.get("summary", ""))
        if not description:
            for content in entry.get("content", ()):
                if content.get("type") in {"text/plain", "text/html", "application/xhtml+xml"}:
                    description = plain_description(content.get("value", ""))
                    if description:
                        break
        articles.append(Article(
            title, url, feed.name, published, classify(title, feed.category),
            description=description, reliability=feed.reliability,
            japan_context=feed.category == Category.JAPAN,
            official_policy=(
                feed.official_policy and is_japan_government_url(response.url) and is_japan_government_url(url)
            ),
        ))
    return articles


def is_japan_government_url(url: str) -> bool:
    return urlsplit(url).scheme == "https" and (urlsplit(url).hostname or "").endswith(".go.jp")


def fetch_releases(source: ReleaseSource) -> list[Article]:
    payload = get(f"https://api.github.com/repos/{source.repository}/releases?per_page=5").json()
    if not isinstance(payload, list):
        raise SourceError("invalid GitHub release list")
    articles = []
    expected_path = f"/{source.repository}/releases/tag/".casefold()
    for release in payload[:5]:
        if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        url, tag = release.get("html_url"), release.get("tag_name")
        if not valid_url(url) or not isinstance(tag, str) or not tag.strip():
            continue
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "github.com" or not parts.path.casefold().startswith(expected_path):
            continue
        try:
            published = datetime.fromisoformat(release["published_at"])
            if published.tzinfo is None:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        name, body = release.get("name"), release.get("body")
        release_name = " ".join(unescape(name if isinstance(name, str) and name.strip() else tag).split())
        if tag not in release_name:
            release_name = f"{release_name} ({tag})"
        articles.append(Article(
            f"{source.name} {release_name}", url, source.name, published.astimezone(UTC), source.category,
            description=plain_description(body) if isinstance(body, str) else "",
            reliability=source.reliability,
        ))
    return articles


def fetch_hn_list(kind: str) -> list[int]:
    ids = get(f"{HN}/{kind}.json").json()
    if not isinstance(ids, list) or any(type(item) is not int or item <= 0 for item in ids):
        raise SourceError("invalid Hacker News story list")
    return ids[:40]


def fetch_hn_item(item_id: int) -> Article | None:
    item = get(f"{HN}/item/{item_id}.json").json()
    if item is None:
        return None
    if not isinstance(item, dict):
        raise SourceError("invalid Hacker News item")
    if item.get("type") != "story" or item.get("deleted") or item.get("dead"):
        return None
    url, raw_title, timestamp = item.get("url"), item.get("title"), item.get("time")
    if not valid_url(url) or not isinstance(raw_title, str) or type(timestamp) is not int:
        return None
    title = " ".join(unescape(raw_title).split())
    if re.search(r"\[(?:video|audio|podcast)\]$", title, re.IGNORECASE):
        return None
    category = classify(title)
    if not category:
        return None
    try:
        published = datetime.fromtimestamp(timestamp, UTC)
    except (ValueError, OverflowError, OSError):
        return None
    score = item.get("score")
    return Article(title, url, "Hacker News", published, category,
                   max(score, 0) if type(score) is int else 0)


def collect(feeds: list[Feed | ReleaseSource]) -> tuple[list[Article], bool]:
    """Return articles and whether any source returned usable data."""
    articles = []
    usable = False
    with ThreadPoolExecutor(max_workers=12) as pool:
        feed_jobs = [
            (feed.name, pool.submit(fetch_feed, feed) if isinstance(feed, Feed)
             else pool.submit(fetch_releases, feed))
            for feed in feeds
        ]
        list_jobs = [(kind, pool.submit(fetch_hn_list, kind)) for kind in ("topstories", "newstories")]
        ids = set()
        for name, job in list_jobs:
            result = read_source(name, job.result)
            if result is not None:
                ids.update(result)
                usable |= not result
        item_jobs = [(item_id, pool.submit(fetch_hn_item, item_id)) for item_id in sorted(ids)]
        for name, job in feed_jobs:
            result = read_source(name, job.result)
            if result is not None:
                usable = True
                LOG.info("Source %s returned %d dated articles.", name, len(result))
                articles.extend(result)
        hn_count = 0
        for item_id, job in item_jobs:
            try:
                article = job.result()
            except (requests.RequestException, ValueError, SourceError) as error:
                log_source_error(f"Hacker News item {item_id}", error)
                continue
            usable = True
            if article:
                articles.append(article)
                hn_count += 1
        LOG.info("Hacker News returned %d dated technology stories from %d candidate IDs.", hn_count, len(ids))
    return articles, usable


def log_source_error(name: str, error: Exception) -> None:
    if isinstance(error, requests.HTTPError) and error.response is not None:
        reason = f"HTTP {error.response.status_code}"
    elif isinstance(error, SourceError):
        reason = str(error)
    else:
        reason = type(error).__name__
    LOG.warning("%s skipped (%s).", name, reason)


def read_source[T](name: str, read: Callable[[], T]) -> T | None:
    try:
        return read()
    except (requests.RequestException, ValueError, SourceError) as error:
        log_source_error(name, error)
        return None
