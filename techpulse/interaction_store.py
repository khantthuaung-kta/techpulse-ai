"""Local SQLite state for the interactive Telegram poller."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .articles import Article, url_key, valid_url
from .digest import shorten
from .ledger import fingerprint

CURSOR_LIFETIME = 6 * 24 * 3600
MAX_BOOKMARKS = 20


@dataclass(frozen=True, slots=True)
class Bookmark:
    title: str
    url: str


@contextmanager
def polling_lock(path: Path) -> Iterator[None]:
    """Use a separate SQLite write lock so the main database stays writable."""
    lock = path.with_name(path.name + ".poll-lock")
    lock.touch(mode=0o600, exist_ok=True)
    with closing(sqlite3.connect(lock, timeout=0)) as db:
        db.execute("BEGIN IMMEDIATE")
        yield


class InteractionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.touch(mode=0o600, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cursors (
                    bot TEXT PRIMARY KEY, next_id INTEGER NOT NULL, updated INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    reader TEXT PRIMARY KEY, topic TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY, reader TEXT NOT NULL, key TEXT NOT NULL,
                    title TEXT NOT NULL, url TEXT NOT NULL, UNIQUE(reader, key)
                );
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            yield db

    def offset(self, bot: str, now: int) -> int:
        with self.connect() as db:
            row = db.execute("SELECT next_id, updated FROM cursors WHERE bot = ?", (bot,)).fetchone()
        return row[0] if row and 0 <= now - row[1] < CURSOR_LIFETIME else 0

    def acknowledge(self, bot: str, next_id: int, now: int) -> None:
        with self.connect() as db:
            db.execute("""
                INSERT INTO cursors VALUES (?, ?, ?) ON CONFLICT(bot)
                DO UPDATE SET next_id=excluded.next_id, updated=excluded.updated
            """, (bot, next_id, now))

    def preference(self, reader: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT topic FROM preferences WHERE reader = ?", (reader,)).fetchone()
        return row[0] if row else "all"

    def set_preference(self, reader: str, topic: str) -> None:
        with self.connect() as db:
            if topic == "all":
                db.execute("DELETE FROM preferences WHERE reader = ?", (reader,))
            else:
                db.execute("""
                    INSERT INTO preferences VALUES (?, ?) ON CONFLICT(reader) DO UPDATE SET topic=excluded.topic
                """, (reader, topic))

    def bookmarks(self, reader: str) -> list[Bookmark]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT title, url FROM bookmarks WHERE reader = ? ORDER BY id DESC LIMIT ?",
                (reader, MAX_BOOKMARKS),
            ).fetchall()
        return [Bookmark(title, url) for title, url in rows if valid_url(url)]

    def save(self, reader: str, article: Article) -> None:
        if not valid_url(article.url):
            raise ValueError("Invalid bookmark URL.")
        key = fingerprint(repr(url_key(article.url)))
        with self.connect() as db:
            db.execute("""
                INSERT OR IGNORE INTO bookmarks (reader, key, title, url) VALUES (?, ?, ?, ?)
            """, (reader, key, shorten(article.title, 80), article.url))
            db.execute("""
                DELETE FROM bookmarks WHERE reader = ? AND id NOT IN (
                    SELECT id FROM bookmarks WHERE reader = ? ORDER BY id DESC LIMIT ?
                )
            """, (reader, reader, MAX_BOOKMARKS))

    def clear_bookmarks(self, reader: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM bookmarks WHERE reader = ?", (reader,))
