"""Career interests and transparent, diversity-aware daily ranking."""

import math
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from .articles import (
    PUBLISHER_TEXT_BONUS, PROTECTED_LEAD_STORIES, Article, Category, JapanTopic,
    japan_topic, publisher_text, title_key, url_key,
)

INTERESTS = {
    "ai": (0.8, r"\b(?:ai|llms?|gpt|machine learning|inference|language models?)\b|生成AI"),
    "python": (1.0, r"\b(?:python|cpython|pypi|pytest|django)\b"),
    "fastapi": (1.2, r"\b(?:fastapi|starlette|pydantic|asgi)\b"),
    "react_typescript": (1.0, r"\b(?:(?-i:React)|reactjs|typescript|next\.js|jsx)\b"),
    "odoo": (1.2, r"\b(?:odoo|odoo community association)\b"),
    "developer_tools": (0.7, r"\b(?:developer tools?|git|github|docker|debugg\w*|toolchain|ci/cd|vs code|visual studio code)\b"),
    "open_source": (0.65, r"\b(?:open[ -]source|foss|gnu|linux kernel)\b|オープンソース"),
    "japan_it": (1.0, r"\b(?:japan|japanese|tokyo|osaka)\b|日本|東京"),
}
PATTERNS = {name: re.compile(pattern, re.I) for name, (_, pattern) in INTERESTS.items()}
STOP_WORDS = {"a", "an", "and", "for", "from", "in", "of", "the", "to", "with"}


@dataclass(frozen=True, slots=True)
class Preferences:
    weights: dict[str, float] = field(default_factory=lambda: {
        name: weight for name, (weight, _) in INTERESTS.items()
    })
    max_per_category: int = 4
    max_per_source: int = 4
    fallback_hours: int = 24
    min_recent: int = 5


def load_preferences(path: Path | None) -> Preferences:
    if path is None:
        return Preferences()
    with path.open("rb") as file:
        config = tomllib.load(file)
    if config.keys() - {"interests", "selection"}:
        raise ValueError("Expected interests and selection sections.")
    interests = config.get("interests", {})
    selection = config.get("selection", {})
    if not isinstance(interests, dict) or not isinstance(selection, dict):
        raise ValueError("Interests and selection must be TOML tables.")
    defaults = Preferences()
    weights = defaults.weights.copy()
    for name, weight in interests.items():
        if name not in INTERESTS or type(weight) not in {float, int} or not 0 <= weight <= 2:
            raise ValueError("Interest names must be supported and weights must be between 0 and 2.")
        weights[name] = weight
    bounds = {"max_per_category": (1, 10), "max_per_source": (1, 10),
              "fallback_hours": (24, 72), "min_recent": (1, 10)}
    for name, value in selection.items():
        if name not in bounds or type(value) is not int or not bounds[name][0] <= value <= bounds[name][1]:
            raise ValueError("Selection contains a setting outside its supported range.")
    return Preferences(weights=weights, **selection)


@dataclass(frozen=True, slots=True)
class RankedStory:
    article: Article
    interests: tuple[str, ...]
    freshness: float
    category: float
    career: float
    reliability: float
    engagement: float
    publisher: float
    novelty: float = 0
    older_fallback: bool = False

    @property
    def score(self) -> float:
        return sum((self.freshness, self.category, self.career, self.reliability,
                    self.engagement, self.publisher, self.novelty))


def score_story(article: Article, now: datetime, preferences: Preferences,
                prefer_publisher_text: bool) -> RankedStory:
    text = publisher_text(article)
    matches = tuple(name for name, pattern in PATTERNS.items() if preferences.weights[name] and (
        pattern.search(f"{article.title} {article.source} {text}")
        or (name == "japan_it" and japan_topic(article) is not None)
        or (name == "ai" and article.category == Category.AI)
    ))
    age_hours = (now - article.published_at).total_seconds() / 3600
    return RankedStory(
        article, matches, max(0, 2 * (1 - age_hours / 24)),
        0.8 if article.category != Category.TRENDING else 0.0,
        min(2.0, sum(preferences.weights[name] for name in matches)),
        0.8 * article.reliability,
        min(math.log1p(article.popularity) / 8, 1.0),
        PUBLISHER_TEXT_BONUS if prefer_publisher_text and text else 0.0,
    )


def overlap(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def rank_digest(articles: list[Article], now: datetime, preferences: Preferences,
                *, prefer_publisher_text: bool = False) -> list[RankedStory]:
    recent_cutoff = now - timedelta(hours=24)
    oldest = now - timedelta(hours=preferences.fallback_hours)
    candidates = []
    for article in articles:
        if not oldest <= article.published_at <= now:
            continue
        if japan_topic(article) == JapanTopic.POLICY and not article.official_policy:
            continue
        title = title_key(article.title)
        tokens = frozenset(title.split()) - STOP_WORDS
        numbers = tuple(re.findall(r"\d+(?:\.\d+)*", article.title))
        candidates.append((score_story(article, now, preferences, prefer_publisher_text),
                           url_key(article.url), title, tokens, numbers))

    selected: list[RankedStory] = []
    urls: set[tuple[str, str, str]] = set()
    titles: set[str] = set()
    signatures: list[tuple[frozenset[str], tuple[str, ...]]] = []
    sources: Counter[str] = Counter()
    categories: Counter[Category] = Counter()

    for recent in (True, False):
        if not recent and len(selected) >= preferences.min_recent:
            break
        pool = [candidate for candidate in candidates
                if (candidate[0].article.published_at >= recent_cutoff) == recent]
        while len(selected) < 10:
            choices = []
            for story, url, title, tokens, numbers in pool:
                article = story.article
                if url in urls or title in titles:
                    continue
                if (
                    sources[article.source.casefold()] >= preferences.max_per_source
                    or categories[article.category] >= preferences.max_per_category
                ):
                    continue
                similarity = max((overlap(tokens, other) for other, _ in signatures), default=0)
                if len(tokens) >= 5 and any(
                    other_numbers == numbers and len(other) >= 5 and overlap(tokens, other) >= 0.83
                    for other, other_numbers in signatures
                ):
                    continue
                novelty = 0.25 * (1 - similarity)
                ranked = replace(
                    story,
                    publisher=story.publisher if len(selected) >= PROTECTED_LEAD_STORIES else 0,
                    novelty=novelty,
                    older_fallback=not recent,
                )
                choices.append((ranked, url, title, tokens, numbers))
            if not choices:
                break
            story, url, title, tokens, numbers = max(
                choices, key=lambda row: (row[0].score, row[0].article.published_at, row[0].article.url)
            )
            selected.append(story)
            urls.add(url)
            if title:
                titles.add(title)
            signatures.append((tokens, numbers))
            sources[story.article.source.casefold()] += 1
            categories[story.article.category] += 1
    return selected


def explain_ranking(stories: list[RankedStory]) -> list[str]:
    return [
        f"{number}. {story.score:.2f} | freshness {story.freshness:.2f} | category {story.category:.2f}"
        f" | interests {story.career:.2f} ({', '.join(story.interests) or 'none'})"
        f" | reliability {story.reliability:.2f} | engagement {story.engagement:.2f}"
        f" | publisher text {story.publisher:.2f} | novelty {story.novelty:.2f}"
        f"{' | extended lookback' if story.older_fallback else ''}"
        for number, story in enumerate(stories, 1)
    ]
