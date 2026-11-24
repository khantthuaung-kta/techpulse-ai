from datetime import date
from unittest.mock import MagicMock, Mock

import pytest

from techpulse import dot_inbox

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
    ([b"a" * 17_000], 200), (["😀".encode() * 1751], 200), ([b"a\x01b"], 200),
])
def test_invalid_or_missing_inbox_falls_back(chunks, status, monkeypatch):
    response = MagicMock(status_code=status)
    response.__enter__.return_value = response
    response.iter_content.return_value = chunks
    monkeypatch.setattr(dot_inbox.requests, "get", Mock(return_value=response))
    monkeypatch.setenv("TECHPULSE_DIGEST_READ_TOKEN", "unit-test-placeholder")
    assert dot_inbox.read_dot_digest(DAY) is None
