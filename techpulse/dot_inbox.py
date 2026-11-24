"""Read a date-pinned plain-text digest without granting the runner write access."""

import logging
import os
from datetime import date

import requests

LOG = logging.getLogger(__name__)
_BASE = "https://api.github.com/repos/khantthuaung-kta/techpulse-digest-inbox/contents"
_MAX_BYTES = 16_384
_MAX_UTF16_UNITS = 3_500


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
        result = body.decode("utf-8").strip()
    except (requests.RequestException, UnicodeError):
        LOG.warning("Dot inbox response failed validation or could not be read.")
        return None
    if (
        not result
        or len(result.encode("utf-16-le")) // 2 > _MAX_UTF16_UNITS
        or any(ord(character) < 32 and character not in "\n\r\t" for character in result)
    ):
        LOG.warning("Dot digest is empty, too long, or contains unsupported control characters.")
        return None
    return result
