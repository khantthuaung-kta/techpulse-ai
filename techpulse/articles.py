"""Article model, categorization and digest selection."""

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit

DESCRIPTION_LIMIT = 3000


class Category(StrEnum):
    AI = "AI & LLM"
    PROGRAMMING = "Programming"
    TOOLS = "Developer Tools"
    JAPAN = "Japan IT"
    OPEN_SOURCE = "Open Source"
    TRENDING = "Trending Tech"


@dataclass(frozen=True, slots=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime
    category: Category
    popularity: int = 0
    description: str = ""


TOPICS = {
    Category.JAPAN: r"\b(?:japan|japanese|tokyo|osaka)\b|日本|東京",
    Category.AI: r"\b(?:ai|llms?|gpt(?:-\w+)?|chatgpt|machine learning|neural network|inference)\b|生成AI",
    Category.OPEN_SOURCE: r"\b(?:open[ -]source|foss|floss|gnu|linux kernel)\b",
    Category.TOOLS: r"\b(?:git|github|gitlab|docker|kubernetes|debugger|ide|ci/cd|devops|toolchain)\b",
    Category.PROGRAMMING: r"\b(?:programming|python|typescript|javascript|rust|compiler|sql|webassembly|software engineering)\b",
}
TECH = re.compile(r"\b(?:tech(?:nology)?|software|hardware|internet|computer|chip|semiconductor|cybersecurity|encryption|browser|robotics|cloud|apple|microsoft|google|android|ios|nvidia)\b", re.I)


def classify(title: str, default: Category | None = None) -> Category | None:
    for category, pattern in TOPICS.items():
        if re.search(pattern, title, re.I):
            return category
    if default is not None:
        return default
    if TECH.search(title):
        return Category.TRENDING
    return None


def valid_url(value: object) -> bool:
    if not isinstance(value, str) or not value or any(char.isspace() or ord(char) < 32 for char in value):
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and parts.username is None
            and (parts.port is None or parts.port > 0)
        )
    except ValueError:
        return False


def url_key(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    default_port = 80 if parts.scheme == "http" else 443
    if parts.port == default_port:
        host = host.rsplit(":", 1)[0]
    query = urlencode(sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ))
    return host, parts.path.rstrip("/"), query


def select_articles(
    articles: Iterable[Article], now: datetime, window: timedelta
) -> list[Article]:
    def rank(article: Article) -> tuple[float, datetime, str]:
        age = (now - article.published_at) / window
        topical = 1.0 if article.category != Category.TRENDING else 0.0
        popularity = min(math.log1p(article.popularity) / 8, 1.0)
        return 2 * (1 - age) + topical + popularity, article.published_at, article.url

    candidates = (article for article in articles if now - window <= article.published_at <= now)
    selected = []
    urls = set()
    titles = set()
    for article in sorted(candidates, key=rank, reverse=True):
        url = url_key(article.url)
        title = " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", article.title).casefold()))
        if url in urls or title in titles:
            continue
        urls.add(url)
        if title:
            titles.add(title)
        selected.append(article)
        if len(selected) == 10:
            break
    return selected
