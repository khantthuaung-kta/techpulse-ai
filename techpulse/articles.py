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
PUBLISHER_TEXT_BONUS = 1.0
PROTECTED_LEAD_STORIES = 3


class Category(StrEnum):
    AI = "AI & LLM"
    PROGRAMMING = "Programming"
    TOOLS = "Developer Tools"
    JAPAN = "Japan IT"
    OPEN_SOURCE = "Open Source"
    TRENDING = "Trending Tech"


class JapanTopic(StrEnum):
    ENGINEERING = "Industry / engineering"
    JOBS = "Jobs / hiring"
    POLICY = "Visa / regulation"


@dataclass(frozen=True, slots=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime
    category: Category
    popularity: int = 0
    description: str = ""
    reliability: float = 0.55
    japan_context: bool = False
    official_policy: bool = False


def japan_topic(article: Article) -> JapanTopic | None:
    if article.category != Category.JAPAN and not article.japan_context:
        return None
    if re.search(
        r"\b(?:visas?|immigration|regulations?|regulatory|legislation|work permits?)\b|在留資格|ビザ|入管|法改正|規制",
        article.title, re.I,
    ):
        return JapanTopic.POLICY
    if re.search(r"\b(?:jobs?|hiring|recruitment|vacanc\w+)\b|求人|採用|募集|転職", article.title, re.I):
        return JapanTopic.JOBS
    return JapanTopic.ENGINEERING


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


def title_key(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def publisher_text(article: Article) -> str:
    """Return usable feed evidence, excluding headline repeats and link-only text."""
    if japan_topic(article) in {JapanTopic.POLICY, JapanTopic.JOBS}:
        return ""
    description = article.description[:DESCRIPTION_LIMIT].strip()
    without_links = re.sub(r"https?://\S+", "", description, flags=re.IGNORECASE)
    normalized = title_key(without_links)
    headline = title_key(article.title)
    if headline and normalized.startswith(f"{headline} "):
        normalized = normalized[len(headline):].strip()
    if normalized in headline or normalized in {"read the full article", "read the full story"}:
        return ""
    return description if sum(char.isalnum() for char in normalized) >= 12 else ""


def select_articles(
    articles: Iterable[Article], now: datetime, window: timedelta, *, prefer_publisher_text: bool = False
) -> list[Article]:
    def rank(article: Article) -> tuple[float, datetime, str]:
        age = (now - article.published_at) / window
        topical = 1.0 if article.category != Category.TRENDING else 0.0
        popularity = min(math.log1p(article.popularity) / 8, 1.0)
        return 2 * (1 - age) + topical + popularity, article.published_at, article.url

    candidates = sorted(
        (article for article in articles if now - window <= article.published_at <= now),
        key=rank,
        reverse=True,
    )
    if prefer_publisher_text:
        # Keep the leading stories even when no publisher description is available.
        # Count distinct stories so duplicates do not consume protected positions.
        lead = distinct_articles(candidates, PROTECTED_LEAD_STORIES)

        def summary_rank(article: Article) -> tuple[float, datetime, str]:
            score, published, url = rank(article)
            return score + (PUBLISHER_TEXT_BONUS if publisher_text(article) else 0), published, url

        candidates = [*lead, *sorted(candidates, key=summary_rank, reverse=True)]
    return distinct_articles(candidates, 10)


def distinct_articles(articles: Iterable[Article], limit: int) -> list[Article]:
    selected = []
    urls = set()
    titles = set()
    for article in articles:
        url = url_key(article.url)
        title = title_key(article.title)
        if url in urls or title in titles:
            continue
        urls.add(url)
        if title:
            titles.add(title)
        selected.append(article)
        if len(selected) == limit:
            break
    return selected
