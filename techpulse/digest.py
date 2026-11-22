"""Render readable digest previews and Telegram messages."""

from datetime import UTC, datetime
from html import escape

from .articles import Article

TELEGRAM_LIMIT = 4096


def telegram_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def render_digest(
    articles: list[Article], now: datetime, *, telegram: bool = False
) -> list[str]:
    heading = f"TechPulse AI · {now.astimezone(UTC):%Y-%m-%d} UTC"
    continued = f"{heading} (continued)"
    if not articles:
        return [f"{heading}\n\nNo recent articles matched."]

    messages = []
    message = f"<b>{heading}</b>" if telegram else heading
    length = telegram_length(heading)
    for number, article in enumerate(articles, start=1):
        title = f"{number}. {shorten(article.title, 300)}"
        published = article.published_at.astimezone(UTC)
        metadata = f"{article.category} · {shorten(article.source, 100)} · {published:%Y-%m-%d %H:%M} UTC"
        if telegram:
            url = escape(article.url, quote=True)
            block = f'\n\n<b>{escape(title)}</b>\n{escape(metadata)}\n<a href="{url}">Original article</a>'
            block_length = telegram_length(f"\n\n{title}\n{metadata}\nOriginal article")
            if length + block_length > TELEGRAM_LIMIT:
                messages.append(message)
                message = f"<b>{continued}</b>"
                length = telegram_length(continued)
            length += block_length
        else:
            block = f"\n\n{title}\n{metadata}\n{article.url}"
        message += block
    messages.append(message)
    return messages
