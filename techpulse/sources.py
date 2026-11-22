"""Collect dated articles from Hacker News and trusted RSS/Atom feeds."""

import logging
import re
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests

from .articles import Article, Category, classify, valid_url

LOG = logging.getLogger(__name__)
HN = "https://hacker-news.firebaseio.com/v0"
TIMEOUT = (5, 10)
USER_AGENT = "TechPulseAI/0.1 (technology digest reader)"


class SourceError(Exception):
    """A source did not return usable data."""


@dataclass(frozen=True, slots=True)
class Feed:
    name: str
    url: str
    category: Category


def load_feeds(path: Path) -> list[Feed]:
    with path.open("rb") as file:
        entries = tomllib.load(file).get("feeds")
    if not isinstance(entries, list):
        raise ValueError("Expected a feeds array in the TOML configuration.")
    feeds = []
    for index, entry in enumerate(entries, start=1):
        try:
            name, url = entry["name"].strip(), entry["url"]
            category = Category(entry["category"])
            if not name or not valid_url(url):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError(f"Feed {index} requires a name, HTTP(S) URL and valid category.") from None
        feeds.append(Feed(name, url, category))
    return feeds


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
        articles.append(Article(title, url, feed.name, published, classify(title, feed.category)))
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


def collect(feeds: list[Feed]) -> tuple[list[Article], bool]:
    """Return articles and whether any source returned usable data."""
    articles = []
    usable = False
    with ThreadPoolExecutor(max_workers=12) as pool:
        feed_jobs = [(feed.name, pool.submit(fetch_feed, feed)) for feed in feeds]
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
                articles.extend(result)
        for item_id, job in item_jobs:
            try:
                article = job.result()
            except (requests.RequestException, ValueError, SourceError) as error:
                log_source_error(f"Hacker News item {item_id}", error)
                continue
            usable = True
            if article:
                articles.append(article)
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
