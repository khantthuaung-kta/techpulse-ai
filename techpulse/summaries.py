"""Provider contract, evidence validation and optional local summary cache."""

import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .articles import DESCRIPTION_LIMIT, Article, url_key

LOG = logging.getLogger(__name__)
PROMPT_VERSION = "burmese-v1"
TEXT_LIMITS = {"headline": 180, "summary": 500, "relevance": 220, "evidence": 350}
NUMBERS = re.compile(r"\d+(?:[.,]\d+)*")
LINK = re.compile(r"https?\s*:|www\.|\[[^\]]*\]\(|[<>]", re.I)
MYANMAR = re.compile(r"[\u1000-\u102a]")


class SummaryError(Exception):
    """A provider request or its result was unusable."""


class ProviderUnavailable(SummaryError):
    """Stop requests for this digest, such as after quota exhaustion."""


@dataclass(frozen=True, slots=True)
class Summary:
    headline: str
    summary: str
    relevance: str
    evidence: str


class SummaryProvider(Protocol):
    cache_namespace: str

    def summarize(self, headline: str, description: str) -> object:
        """Return a structured response for validation."""
        ...


def normalize_numbers(text: str) -> set[str]:
    return {
        "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in number)
        for number in NUMBERS.findall(text)
    }


def validate_summary(raw: object, title: str, description: str) -> Summary:
    if not isinstance(raw, dict) or raw.keys() != TEXT_LIMITS.keys():
        raise SummaryError("invalid summary fields")
    fields = {}
    for name, limit in TEXT_LIMITS.items():
        value = raw[name]
        if not isinstance(value, str):
            raise SummaryError("invalid summary text")
        value = " ".join(value.split())
        if not value or len(value) > limit or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
            raise SummaryError("invalid summary length or characters")
        if name != "evidence" and (LINK.search(value) or not MYANMAR.search(value)):
            raise SummaryError("invalid Burmese text or unexpected link")
        fields[name] = value
    if fields["evidence"] not in description or len(fields["evidence"]) < 12:
        raise SummaryError("summary evidence was absent from the description")
    output = " ".join(fields[name] for name in ("headline", "summary", "relevance"))
    if not normalize_numbers(output) <= normalize_numbers(f"{title} {description}"):
        raise SummaryError("unsupported numbers in summary")
    return Summary(**fields)


def cache_key(article: Article, headline: str, description: str, provider: SummaryProvider) -> str:
    data = json.dumps([PROMPT_VERSION, provider.cache_namespace, url_key(article.url), headline, description])
    return hashlib.sha256(data.encode()).hexdigest()


def summarize_articles(
    articles: list[Article], provider: SummaryProvider, cache_path: Path | None = None
) -> dict[str, Summary]:
    summaries = {}
    cache = None
    try:
        if cache_path is not None:
            cache = sqlite3.connect(cache_path, timeout=2)
            cache.execute("CREATE TABLE IF NOT EXISTS summaries (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    except (sqlite3.Error, ValueError):
        LOG.warning("Summary cache unavailable. Continuing without a cache.")
        if cache is not None:
            cache.close()
        cache = None

    unavailable = False
    try:
        for article in articles[:10]:
            headline = article.title[:300]
            description = article.description[:DESCRIPTION_LIMIT]
            if len(description) < 12 or description.casefold() in article.title.casefold():
                continue
            key = cache_key(article, headline, description, provider)
            if cache is not None:
                try:
                    row = cache.execute("SELECT value FROM summaries WHERE key = ?", (key,)).fetchone()
                    if row:
                        summaries[article.url] = validate_summary(json.loads(row[0]), headline, description)
                        continue
                except (sqlite3.Error, ValueError, SummaryError):
                    LOG.warning("Could not read a valid cached summary.")
            if unavailable:
                continue
            try:
                raw = provider.summarize(headline, description)
                summary = validate_summary(raw, headline, description)
            except ProviderUnavailable as error:
                # ProviderUnavailable messages must contain only safe diagnostics.
                LOG.warning("AI provider unavailable (%s). Using original headlines for remaining items.", error)
                unavailable = True
                continue
            except SummaryError:
                LOG.warning("AI summary unavailable for an item. Using its original headline.")
                continue
            summaries[article.url] = summary
            if cache is not None:
                try:
                    cache.execute("INSERT OR REPLACE INTO summaries VALUES (?, ?)", (key, json.dumps(asdict(summary))))
                    cache.commit()
                except sqlite3.Error:
                    LOG.warning("Could not save a summary to the cache.")
    finally:
        if cache is not None:
            cache.close()
    LOG.info("AI summaries available for %d/%d selected articles.", len(summaries), len(articles))
    return summaries
