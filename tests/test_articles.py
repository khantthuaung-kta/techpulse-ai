from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from techpulse.articles import Article, Category, classify, publisher_text, select_articles

NOW = datetime(2026, 11, 22, 10, tzinfo=UTC)
WINDOW = timedelta(hours=24)


@pytest.mark.parametrize(("title", "category"), [
    ("Local LLM inference", Category.AI),
    ("New Rust compiler", Category.PROGRAMMING),
    ("GitHub rolls out a debugger", Category.TOOLS),
    ("Semiconductor investment in Japan", Category.JAPAN),
    ("Open-source media player", Category.OPEN_SOURCE),
    ("A new NVIDIA chip", Category.TRENDING),
    ("A recipe for carrot soup", None),
])
def test_classify(title, category):
    assert classify(title) == category


def test_selection_ranks_and_deduplicates_within_window():
    best = Article("Python 3.12!", "https://example.org/post", "Publisher", NOW, Category.PROGRAMMING)
    candidates = [
        replace(best, title="Same URL", url="http://www.example.org/post/?utm_source=test#intro",
                published_at=NOW - timedelta(hours=2)),
        replace(best, title="Ｐｙｔｈｏｎ 3.12", url="https://example.com/different",
                published_at=NOW - timedelta(hours=1)),
        replace(best, title="Older", url="https://example.org/old", published_at=NOW-WINDOW-timedelta(seconds=1)),
        replace(best, title="Future", url="https://example.org/future", published_at=NOW+timedelta(seconds=1)),
        replace(best, title="New browser", url="https://example.org/browser", category=Category.TRENDING),
        best,
    ]
    assert select_articles(candidates, NOW, WINDOW) == [best, candidates[4]]


def test_selection_limit_and_popularity():
    articles = [
        Article(f"Rust project {i}", f"https://example.org/{i}", "Hacker News",
                NOW, Category.PROGRAMMING, i)
        for i in range(15)
    ]
    assert select_articles(articles, NOW, WINDOW) == articles[:4:-1]


def test_publisher_text_preference_preserves_leads_and_promotes_recent_evidence():
    headlines = [
        Article(f"Browser update {i}", f"https://example.org/{i}", "Hacker News",
                NOW - timedelta(minutes=i), Category.TRENDING, 80 - i)
        for i in range(10)
    ]
    publisher = replace(
        headlines[-1], source="Publisher", url="https://www.example.org/9?utm_source=rss",
        published_at=NOW - timedelta(hours=2), popularity=0,
        description="The browser update fixes issues with restoring tabs.",
    )
    expired = replace(publisher, url="https://example.org/expired", title="Browser news yesterday",
                      published_at=NOW - WINDOW - timedelta(seconds=1))
    candidates = [*headlines, publisher, expired]
    assert select_articles(candidates, NOW, WINDOW) == headlines

    selected = select_articles(candidates, NOW, WINDOW, prefer_publisher_text=True)
    assert selected[:3] == headlines[:3]
    assert selected[3] == publisher
    assert headlines[-1] not in selected and expired not in selected
    assert len(selected) == 10


@pytest.mark.parametrize("description", [
    "A Python release!",
    "A Python release. Read the full article",
    "https://example.org/an/article/about/python",
    "Read more",
])
def test_publisher_text_ignores_inadequate_evidence(description):
    article = Article("A Python release", "https://example.org/python", "Publisher", NOW,
                      Category.PROGRAMMING, description=description)
    assert publisher_text(article) == ""
