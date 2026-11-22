import subprocess

import pytest

from scripts.check_repository import main


@pytest.mark.parametrize(("filename", "content", "expected"), [
    (".env.example", "GROQ_API_KEY=", 0),
    (".env", "placeholder", 1),
    ("delivery.sqlite3-wal", "placeholder", 1),
    ("notes.md", "gsk_" + "x" * 40, 1),
])
def test_repository_check_uses_git_index_without_printing_content(
    filename, content, expected, tmp_path, monkeypatch, capsys
):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    file = tmp_path / filename
    file.write_text(content)
    subprocess.run(["git", "add", "--", filename], check=True)
    file.write_text("working tree replaced")
    assert main() == expected
    assert content not in capsys.readouterr().out
