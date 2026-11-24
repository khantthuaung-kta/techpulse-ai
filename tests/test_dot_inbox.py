from datetime import date
from html import unescape
from unittest.mock import MagicMock, Mock

import pytest

from techpulse import dot_inbox
from techpulse.digest import TELEGRAM_LIMIT, telegram_length

DAY = date(2026, 11, 24)


@pytest.mark.parametrize("preview,folder", [(False, "digests"), (True, "previews")])
def test_uses_only_the_requested_folder_and_yangon_date(preview, folder, monkeypatch):
    response = MagicMock(status_code=200)
    response.__enter__.return_value = response
    response.iter_content.return_value = ["မြန်မာ့သတင်း".encode("utf-8")]
    get = Mock(return_value=response)
    monkeypatch.setattr(dot_inbox.requests, "get", get)
    monkeypatch.setenv("TECHPULSE_DIGEST_READ_TOKEN", "unit-test-placeholder")
    assert dot_inbox.read_dot_digest(DAY, preview=preview) == "မြန်မာ့သတင်း"
    assert get.call_args.args[0].endswith(f"/{folder}/2026-11-24.md")
    assert get.call_args.kwargs["params"] == {"ref": "main"}
    assert get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("chunks,status", [
    ([b"sample"], 404), ([b"sample"], 302), ([b"\xff"], 200),
    ([b"a" * 61_441], 200), (["😀".encode() * 12_501], 200), ([b"a\x01b"], 200),
])
def test_invalid_or_missing_inbox_falls_back(chunks, status, monkeypatch):
    response = MagicMock(status_code=status)
    response.__enter__.return_value = response
    response.iter_content.return_value = chunks
    monkeypatch.setattr(dot_inbox.requests, "get", Mock(return_value=response))
    monkeypatch.setenv("TECHPULSE_DIGEST_READ_TOKEN", "unit-test-placeholder")
    assert dot_inbox.read_dot_digest(DAY) is None


def test_reader_accepts_a_large_burmese_edition(monkeypatch):
    text = "က" * 20_480  # Exactly 60 KiB, but comfortably below the UTF-16 cap.
    raw = text.encode("utf-8")
    response = MagicMock(status_code=200)
    response.__enter__.return_value = response
    response.iter_content.return_value = [raw[i:i + 4096] for i in range(0, len(raw), 4096)]
    monkeypatch.setattr(dot_inbox.requests, "get", Mock(return_value=response))
    monkeypatch.setenv("TECHPULSE_DIGEST_READ_TOKEN", "unit-test-placeholder")
    assert dot_inbox.read_dot_digest(DAY) == text


def test_split_keeps_story_blocks_and_preserves_literal_text():
    stories = [
        f"{i}. မြန်မာ့သတင်း <b> & အကြံပြုချက်\n" + "😀" * 530
        + f"\nhttps://example.org/news?id={i}&edition=today"
        for i in range(1, 16)
    ]
    source = "\n\n".join(stories)
    parts = dot_inbox.split_dot_digest(source)
    assert len(parts) > 1
    bodies = []
    for index, part in enumerate(parts, 1):
        assert "<b>" not in part
        visible = unescape(part)
        assert telegram_length(visible) <= TELEGRAM_LIMIT
        heading, body = visible.split("\n\n", 1)
        assert heading == f"TechPulse N2 · အပိုင်း {index}/{len(parts)}"
        bodies.append(body)
    assert "".join(bodies) == source
    assert all(sum(story in body for body in bodies) == 1 for story in stories)


def test_split_overlong_unbroken_story_never_splits_astral_character():
    source = "က" + "😀" * 5000
    parts = dot_inbox.split_dot_digest(source)
    assert len(parts) == 3
    assert "".join(unescape(p).split("\n\n", 1)[1] for p in parts) == source
    assert all(telegram_length(unescape(part)) <= TELEGRAM_LIMIT for part in parts)
