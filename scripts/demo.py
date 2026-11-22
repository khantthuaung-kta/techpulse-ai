"""Replay a two-story publisher snapshot without loading .env or using the network."""

import argparse
import re
from dataclasses import replace
from datetime import UTC, datetime
from html import unescape
from time import perf_counter

from techpulse.articles import Article, Category, title_key, url_key
from techpulse.digest import TELEGRAM_LIMIT, render_digest, telegram_length
from techpulse.personalization import Preferences, rank_digest
from techpulse.summaries import Summary, validate_summary

SNAPSHOT = datetime(2026, 3, 3, 12, tzinfo=UTC)
PUBLISHED = datetime(2026, 3, 3, tzinfo=UTC)
RELEASE = Article(
    "Python 3.12.13, 3.11.15 and 3.10.20 are now available!",
    "https://blog.python.org/2026/03/python-31213-31115-31020/",
    "Python Insider", PUBLISHED, Category.PROGRAMMING,
    description="The Python team published source-only security releases for Python 3.12, 3.11 and 3.10.",
    reliability=0.95,
)
BLOG_MOVE = Article(
    "The Python Insider Blog Has Moved!",
    "https://blog.python.org/2026/03/the-python-insider-blog-has-moved/",
    "Python Insider", PUBLISHED, Category.PROGRAMMING,
    description="Python Insider moved to blog.python.org. Older posts were migrated and existing URLs redirect.",
    reliability=0.95,
)
FIXTURE = [RELEASE, BLOG_MOVE, replace(RELEASE, url=f"{RELEASE.url}?utm_source=fixture", reliability=0.65)]
SAMPLE = {
    "headline": "Python 3.12၊ 3.11 နှင့် 3.10 အတွက် လုံခြုံရေးအပ်ဒိတ်များ ထွက်ရှိ",
    "summary": "Python အဖွဲ့က ဗားရှင်း 3.12၊ 3.11 နှင့် 3.10 အတွက် လုံခြုံရေးအပ်ဒိတ်များ ထုတ်ပြန်ခဲ့သည်။ ဤအပ်ဒိတ်များကို ဆော့ဖ်ဝဲမူရင်းကုဒ်အဖြစ်သာ ထုတ်ပြန်ထားသည်။",
    "relevance": "Python ပရောဂျက်အတွက် အသုံးပြုနေသော ဗားရှင်းကို စစ်ဆေးရာတွင် အသုံးဝင်သည်။",
    "evidence": RELEASE.description,
}


def prepare() -> tuple[list[Article], dict[str, Summary]]:
    ranked = rank_digest(FIXTURE, SNAPSHOT, Preferences(), prefer_publisher_text=True)
    summaries = {RELEASE.url: validate_summary(SAMPLE, RELEASE.title, RELEASE.description)}
    return [story.article for story in ranked], summaries


def html_preview(messages: list[str]) -> str:
    # The production renderer has already escaped all source text and URLs.
    cards = "".join(f'<article>{message}</article>' for message in messages)
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>TechPulse AI · Offline digest preview</title>
<style>
body {{ background: #eef2f7; color: #14243b; font: 17px system-ui, sans-serif; margin: 0; }}
main {{ box-sizing: border-box; max-width: 830px; margin: 0 auto; padding: 42px; }}
h1 {{ font-size: 29px; margin: 0; }}
p {{ margin: 8px 0 20px; color: #364967; }}
article {{ background: white; border: 1px solid #ccd8e7; border-radius: 12px; padding: 24px;
white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7;
font-family: system-ui, "Noto Sans Myanmar", sans-serif; }}
a {{ color: #225793; }}
</style>
<main>
<h1>TechPulse AI</h1>
<p>Offline preview of the Telegram digest · historical fixtures</p>
{cards}
</main>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Verify fixture output without printing the digest.")
    mode.add_argument("--html", action="store_true", help="Render the Telegram payload as an offline HTML preview.")
    mode.add_argument("--benchmark", type=int, metavar="N", help="Measure N local ranking and rendering iterations.")
    args = parser.parse_args()
    if args.benchmark is not None and not 1 <= args.benchmark <= 10_000:
        parser.error("--benchmark must be between 1 and 10000")

    articles, summaries = prepare()
    messages = render_digest(articles, SNAPSHOT, telegram=True, summaries=summaries)
    visible = [unescape(re.sub(r"<[^>]*>", "", message)) for message in messages]
    if not (
        len(articles) == 2
        and len({url_key(article.url) for article in articles}) == 2
        and len({title_key(article.title) for article in articles}) == 2
        and sum(message.count("Headline/description-based") for message in visible) == 1
        and sum(message.count("Burmese summary unavailable") for message in visible) == 1
        and all(article.url in "".join(messages) for article in articles)
        and all(telegram_length(message) <= TELEGRAM_LIMIT for message in visible)
    ):
        raise RuntimeError("The offline demo did not meet its ranking and rendering checks.")

    if args.check:
        print("Offline demo passed: 3 inputs, 2 distinct stories, 1 validated Burmese sample, 1 fallback.")
    elif args.html:
        print(html_preview(messages))
    elif args.benchmark:
        start = perf_counter()
        for _ in range(args.benchmark):
            ranked, samples = prepare()
            render_digest(ranked, SNAPSHOT, telegram=True, summaries=samples)
        print(f"Mean offline ranking and render time: {(perf_counter() - start) * 1000 / args.benchmark:.3f} ms "
              f"over {args.benchmark} iterations.")
    else:
        print("Offline demo · Captured publisher dates · No network calls\n")
        print(render_digest(articles, SNAPSHOT, summaries=summaries)[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
