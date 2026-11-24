"""Read a date-pinned plain-text digest without granting the runner write access."""

import logging
import os
from datetime import date
from html import escape

import requests

from .digest import TELEGRAM_LIMIT, telegram_length

LOG = logging.getLogger(__name__)
_BASE = "https://api.github.com/repos/khantthuaung-kta/techpulse-digest-inbox/contents"
_MAX_BYTES = 60 * 1024
_MAX_UTF16_UNITS = 25_000
_PART_BODY_LIMIT = TELEGRAM_LIMIT - 96  # Reserve room for a numbered header.


def read_dot_digest(day: date, *, preview: bool = False) -> str | None:
    """Return a suitable digest, or let the usual news collector run."""
    token = os.getenv("TECHPULSE_DIGEST_READ_TOKEN", "").strip()
    if not token:
        return None
    folder = "previews" if preview else "digests"
    try:
        with requests.get(
            f"{_BASE}/{folder}/{day.isoformat()}.md",
            params={"ref": "main"},
            headers={
                "Accept": "application/vnd.github.raw+json",
                "Authorization": f"Bearer {token}",
            },
            timeout=(5, 10),
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code != 200:
                LOG.info("Dot inbox unavailable for %s (HTTP %d).", day, response.status_code)
                return None
            body = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                body.extend(chunk)
                if len(body) > _MAX_BYTES:
                    LOG.warning("Dot digest exceeds the input size limit.")
                    return None
        result = body.decode("utf-8")
    except (requests.RequestException, UnicodeError):
        LOG.warning("Dot inbox response failed validation or could not be read.")
        return None
    if (
        not result.strip()
        or telegram_length(result) > _MAX_UTF16_UNITS
        or any(ord(character) < 32 and character not in "\n\r\t" for character in result)
    ):
        LOG.warning("Dot digest is empty, too long, or contains unsupported control characters.")
        return None
    return result


def _part_cut(text: str) -> int:
    units = 0
    for end, character in enumerate(text):
        units += 2 if ord(character) > 0xFFFF else 1
        if units > _PART_BODY_LIMIT:
            break
    else:
        return len(text)

    # Keep whole story blocks when possible; otherwise prefer paragraphs/lines,
    # then whitespace. An oversized word cannot leave the splitter stuck.
    for separators in (("\n\n", "\r\n\r\n"), ("\n",), (" ", "\t")):
        candidates = []
        for separator in separators:
            position = text.rfind(separator, 0, end)
            if position >= 0:
                candidates.append(position + len(separator))
        if candidates:
            cut = max(candidates)
            if text[:cut].strip():
                return cut
    return end


def split_dot_digest(text: str) -> list[str]:
    """Build numbered, HTML-safe parts from one prevalidated plain-text digest."""
    parts: list[str] = []
    while text:
        cut = _part_cut(text)
        parts.append(text[:cut])
        text = text[cut:]
    if len(parts) <= 1:
        return [escape(part) for part in parts]
    total = len(parts)
    return [
        escape(f"TechPulse N2 · အပိုင်း {index}/{total}\n\n{part}")
        for index, part in enumerate(parts, 1)
    ]
