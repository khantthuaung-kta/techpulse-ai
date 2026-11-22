"""Reject tracked credential files and recognizable provider keys."""

import re
import subprocess
from pathlib import PurePosixPath

KEY_PATTERN = (
    r"gsk_[A-Za-z0-9]{35,}|AIza[A-Za-z0-9_-]{32,}|AQ\.[A-Za-z0-9_-]{35,}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|[0-9]{8,}:[A-Za-z0-9_-]{30,}"
)
SQLITE_FILE = re.compile(r"\.(?:db|sqlite3?)(?:$|[.-])", re.IGNORECASE)


def main() -> int:
    names = subprocess.check_output(["git", "ls-files", "-z"]).decode(errors="replace").split("\0")
    for name in names:
        base = PurePosixPath(name).name
        if (
            base == ".env"
            or (base.startswith(".env.") and base != ".env.example")
            or SQLITE_FILE.search(base)
        ):
            print("Repository check failed: a credentials or SQLite state file is tracked.")
            return 1
    result = subprocess.run(
        ["git", "grep", "--cached", "-I", "-l", "-E", KEY_PATTERN, "--", "."],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        print("Repository check failed: a tracked file matches a provider credential pattern.")
        return 1
    if result.returncode != 1:
        print("Repository check failed: could not scan the Git index.")
        return 1
    print("Tracked file and credential-pattern checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
