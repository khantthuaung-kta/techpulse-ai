"""Select an explicitly confirmed free-tier AI provider."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import gemini, groq
from .articles import Article
from .summaries import Summary, SummaryProvider, summarize_articles

LOG = logging.getLogger(__name__)
PROVIDERS: dict[str, tuple[Callable[[str, str], SummaryProvider], str, str]] = {
    "gemini": (gemini.GeminiProvider, gemini.MODEL, "GEMINI"),
    "groq": (groq.GroqProvider, groq.MODEL, "GROQ"),
}


@dataclass(frozen=True, slots=True)
class SummarySettings:
    requested: bool
    provider: SummaryProvider | None = None
    cache_path: Path | None = None


def load_summary_settings() -> SummarySettings:
    name = os.getenv("AI_PROVIDER", "").strip().lower()
    if name in {"", "off"}:
        return SummarySettings(requested=False)
    config = PROVIDERS.get(name)
    if config is None:
        LOG.warning("Unsupported AI provider. Using original headlines.")
        return SummarySettings(requested=True)
    adapter, supported_model, prefix = config
    model = os.getenv("AI_MODEL", "").strip() or supported_model
    api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
    free_confirmed = os.getenv(f"{prefix}_FREE_TIER_CONFIRMED", "").strip().lower() == "true"
    if model != supported_model or not api_key or not free_confirmed:
        LOG.warning("AI configuration incomplete or unsupported. Using original headlines.")
        return SummarySettings(requested=True)
    cache = os.getenv("AI_SUMMARY_CACHE", "").strip()
    return SummarySettings(True, adapter(api_key, model), Path(cache) if cache else None)


def configured_summaries(
    articles: list[Article], settings: SummarySettings | None = None
) -> dict[str, Summary] | None:
    """Return None when disabled, or a mapping of validated summaries."""
    settings = settings if settings is not None else load_summary_settings()
    if not settings.requested:
        return None
    if settings.provider is None:
        return {}
    return summarize_articles(articles, settings.provider, settings.cache_path)
