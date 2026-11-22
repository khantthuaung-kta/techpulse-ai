"""Select an explicitly confirmed free-tier AI provider."""

import logging
import os
from pathlib import Path

from . import gemini, groq
from .articles import Article
from .summaries import Summary, summarize_articles

LOG = logging.getLogger(__name__)
PROVIDERS = {
    "gemini": (gemini.GeminiProvider, gemini.MODEL, "GEMINI"),
    "groq": (groq.GroqProvider, groq.MODEL, "GROQ"),
}


def configured_summaries(articles: list[Article]) -> dict[str, Summary] | None:
    """Return None when disabled, or a mapping of validated summaries."""
    name = os.getenv("AI_PROVIDER", "").strip().lower()
    if name in {"", "off"}:
        return None
    config = PROVIDERS.get(name)
    if config is None:
        LOG.warning("Unsupported AI provider. Using original headlines.")
        return {}
    adapter, supported_model, prefix = config
    model = os.getenv("AI_MODEL", "").strip() or supported_model
    api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
    free_confirmed = os.getenv(f"{prefix}_FREE_TIER_CONFIRMED", "").strip().lower() == "true"
    if model != supported_model or not api_key or not free_confirmed:
        LOG.warning("AI configuration incomplete or unsupported. Using original headlines.")
        return {}
    cache = os.getenv("AI_SUMMARY_CACHE", "").strip()
    return summarize_articles(articles, adapter(api_key, model), Path(cache) if cache else None)
