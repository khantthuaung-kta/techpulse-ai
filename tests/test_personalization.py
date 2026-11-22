from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from techpulse.articles import Article, Category, JapanTopic, japan_topic, publisher_text
from techpulse.digest import render_digest
from techpulse.personalization import Preferences, explain_ranking, load_preferences, rank_digest

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


def news(number, title, *, source="Hacker News", category=Category.PROGRAMMING, age=2):
    return Article(title, f"https://example.org/{number}", source, NOW - timedelta(hours=age), category)


def test_configurable_interests_and_score_factors(tmp_path):
    file = tmp_path / "preferences.toml"
    file.write_text("[interests]\npython=0\nodoo=2\n[selection]\nfallback_hours=36\n")
    settings = load_preferences(file)
    python = news(1, "Python compiler release", age=1)
    odoo = replace(news(2, "Odoo accounting changes", source="Publisher"), reliability=0.95)
    results = rank_digest([python, odoo], NOW, settings)
    assert [result.article for result in results] == [odoo, python]
    assert results[0].interests == ("odoo",)
    assert "interests 2.00 (odoo)" in explain_ranking(results)[0]
    assert "reliability 0.76" in explain_ranking(results)[0]
    file.write_text("[interests]\nodoo=3")
    with pytest.raises(ValueError):
        load_preferences(file)


def test_mixed_sources_categories_and_cross_source_dedup():
    topics = ["Parser", "Decoder", "Serialization", "Compiler", "Pattern matcher", "Interpreter"]
    items = [news(i, f"{topic} update") for i, topic in enumerate(topics)]
    items.extend([
        news(6, "A working guide to Tokio async runtimes", source="Rust Blog", category=Category.OPEN_SOURCE),
        news(7, "Japan software engineering adoption survey", source="Japan Publisher", category=Category.JAPAN),
        news(8, "Kubernetes observability release", source="Engineering Blog", category=Category.TOOLS),
        news(9, "New graphics hardware review", source="Tech Publisher", category=Category.TRENDING),
        news(10, "An LLM inference deployment guide", source="AI Blog", category=Category.AI),
        news(11, "GPU model deployment notes", source="AI Blog", category=Category.AI),
    ])
    items.append(replace(items[6], url="http://www.example.org/6/?utm_campaign=rss", source="Community"))
    items.append(replace(items[6], url="https://other.example/topics/tokio", title="Working guide to Tokio async runtimes"))
    items.append(replace(items[7], url="https://other.example/japan", title="JAPAN software engineering adoption survey!"))
    stories = [entry.article for entry in rank_digest(items, NOW, Preferences())]
    assert len(stories) == 10
    assert Counter(article.source for article in stories)["Hacker News"] == 4
    assert len({article.category for article in stories}) >= 5
    assert sum("tokio" in article.title.lower() for article in stories) == 1
    assert sum("adoption survey" in article.title.lower() for article in stories) == 1


def test_fallback_is_opt_in_and_only_applies_with_few_recent_items():
    fresh = news(1, "Rust async iterator updates")
    older = news(2, "Python runtime configuration notes", age=30)
    stale = news(3, "Compiler optimization review", age=45)
    prefs = Preferences(fallback_hours=36, min_recent=2)
    assert [entry.article for entry in rank_digest([fresh, older, stale], NOW, Preferences())] == [fresh]
    assert [entry.article for entry in rank_digest([fresh, older, stale], NOW, prefs)] == [fresh, older]
    other = news(4, "Open source toolchain benchmarking")
    assert older not in [entry.article for entry in rank_digest([fresh, other, older], NOW, prefs)]


def test_sparse_sources_do_not_break_diversity_caps():
    items = [news(i, f"Python project {i}", age=i) for i in range(10)]
    extra = news(20, "Python debugging", source="Publisher")
    selected = rank_digest([*items, extra], NOW, Preferences(max_per_source=2, max_per_category=3))
    assert len(selected) == 3
    assert Counter(entry.article.source for entry in selected)["Hacker News"] == 2
    assert extra in [entry.article for entry in selected]


def test_title_overlap_does_not_merge_distinct_release_versions():
    first = news(1, "FastAPI 1.2.3 release improves Python routing and response validation")
    second = news(2, "FastAPI 1.2.4 release improves Python routing and response validation")
    assert {entry.article.url for entry in rank_digest([first, second], NOW, Preferences())} == {
        first.url, second.url,
    }


def test_policy_requires_official_source_and_jobs_are_labeled():
    job = news(1, "Japan hiring trends for Python developers", category=Category.JAPAN)
    visa = replace(news(2, "Japan work visa rules update", category=Category.JAPAN), description="A source excerpt.")
    official = replace(visa, url="https://www.moj.go.jp/update", source="Agency", official_policy=True)
    assert japan_topic(job) == JapanTopic.JOBS
    assert publisher_text(official) == ""
    assert [entry.article for entry in rank_digest([visa, official, job], NOW, Preferences())] == [job, official]
    preview = render_digest([job, official], NOW)[0]
    assert "Jobs / hiring" in preview and "Visa / regulation" in preview


def test_optional_tip_uses_selected_publisher_source():
    item = replace(news(1, "FastAPI release"), description="The project added tracing hooks for route handlers.")
    html = render_digest([item], NOW, telegram=True, learn_tip=True)[0]
    assert "Learn something today" in html and f'href="{item.url}"' in html
    assert "Learn something today" not in render_digest([item], NOW)[0]
