"""Durable daily claims and delivery receipts."""

import base64
import binascii
import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import requests

from .articles import Article, title_key, url_key

STATE_BRANCH = "techpulse-delivery-state"
HISTORY_DAYS = 7
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_PATTERN = re.compile(r"[a-fA-F0-9]{40}")


class LedgerError(Exception):
    """The delivery ledger could not be read or updated safely."""


class ClaimConflict(LedgerError):
    """The expected ledger revision changed."""


@dataclass(slots=True)
class DeliveryRecord:
    destination: str
    batch: str
    parts: int
    status: str = "claimed"
    confirmed: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def decode(cls, content: bytes | str) -> "DeliveryRecord":
        try:
            record = cls(**json.loads(content))
            if (
                record.status not in {"claimed", "failed", "sent"}
                or type(record.parts) is not int or record.parts < 1
                or not isinstance(record.destination, str) or not isinstance(record.batch, str)
                or not isinstance(record.confirmed, dict)
                or len(record.confirmed) > record.parts
                or (record.status == "sent" and len(record.confirmed) != record.parts)
                or any(
                    not key.isdecimal() or not isinstance(values, list)
                    or any(not isinstance(value, str) for value in values)
                    for key, values in record.confirmed.items()
                )
            ):
                raise ValueError
            return record
        except (TypeError, ValueError, UnicodeError):
            raise LedgerError("Invalid delivery ledger record.") from None

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


class Ledger(Protocol):
    def read(self, day: date) -> tuple[DeliveryRecord, str] | None: ...
    def write(self, day: date, record: DeliveryRecord, revision: str | None) -> str: ...


class SQLiteLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            with self.connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS digests (day TEXT PRIMARY KEY, record TEXT, revision INTEGER)")
        except (sqlite3.Error, ValueError):
            raise LedgerError("Cannot initialize the local delivery ledger.") from None

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            yield db

    def read(self, day: date) -> tuple[DeliveryRecord, str] | None:
        try:
            with self.connect() as db:
                row = db.execute("SELECT record, revision FROM digests WHERE day = ?", (day.isoformat(),)).fetchone()
        except sqlite3.Error:
            raise LedgerError("Cannot read the local delivery ledger.") from None
        return (DeliveryRecord.decode(row[0]), str(row[1])) if row else None

    def write(self, day: date, record: DeliveryRecord, revision: str | None) -> str:
        try:
            with self.connect() as db:
                if revision is None:
                    cursor = db.execute("INSERT OR IGNORE INTO digests VALUES (?, ?, 1)",
                                        (day.isoformat(), record.encode()))
                    next_revision = 1
                else:
                    next_revision = int(revision) + 1
                    cursor = db.execute("UPDATE digests SET record = ?, revision = ? WHERE day = ? AND revision = ?",
                                        (record.encode(), next_revision, day.isoformat(), int(revision)))
                if cursor.rowcount != 1:
                    raise ClaimConflict("Delivery ledger revision changed.")
            return str(next_revision)
        except sqlite3.Error:
            raise LedgerError("Cannot write the local delivery ledger.") from None


class GitHubLedger:
    def __init__(self, repository: str, token: str, commit: str) -> None:
        if not REPO_PATTERN.fullmatch(repository) or not token or not SHA_PATTERN.fullmatch(commit):
            raise LedgerError("GitHub delivery ledger configuration is incomplete.")
        self.base = f"https://api.github.com/repos/{repository}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.commit = commit
        self.ensure_branch()

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            return requests.request(method, self.base + path, headers=self.headers,
                                    timeout=(5, 10), allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            raise LedgerError(f"GitHub delivery ledger request failed ({type(error).__name__}).") from None

    def ensure_branch(self) -> None:
        path = f"/git/ref/heads/{STATE_BRANCH}"
        response = self.request("GET", path)
        if response.status_code == 200:
            return
        if response.status_code != 404:
            raise LedgerError(f"GitHub ledger branch lookup failed (HTTP {response.status_code}).")
        response = self.request("POST", "/git/refs", json={
            "ref": f"refs/heads/{STATE_BRANCH}", "sha": self.commit,
        })
        if response.status_code == 201:
            return
        if response.status_code in {409, 422} and self.request("GET", path).status_code == 200:
            return
        raise LedgerError(f"GitHub ledger branch creation failed (HTTP {response.status_code}).")

    def read(self, day: date) -> tuple[DeliveryRecord, str] | None:
        response = self.request("GET", f"/contents/delivery/{day.isoformat()}.json",
                                params={"ref": STATE_BRANCH})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise LedgerError(f"GitHub ledger read failed (HTTP {response.status_code}).")
        try:
            data = response.json()
            revision = data["sha"]
            if not SHA_PATTERN.fullmatch(revision) or data["encoding"] != "base64":
                raise ValueError
            content = base64.b64decode("".join(data["content"].split()), validate=True)
        except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
            raise LedgerError("GitHub returned an invalid delivery ledger file.") from None
        return DeliveryRecord.decode(content), revision

    def write(self, day: date, record: DeliveryRecord, revision: str | None) -> str:
        payload = {
            "message": f"Record TechPulse delivery state for {day.isoformat()}",
            "branch": STATE_BRANCH,
            "content": base64.b64encode(record.encode().encode()).decode(),
        }
        if revision is not None:
            payload["sha"] = revision
        response = self.request("PUT", f"/contents/delivery/{day.isoformat()}.json", json=payload)
        if response.status_code in {409, 422}:
            raise ClaimConflict(f"GitHub ledger write was rejected (HTTP {response.status_code}).")
        if response.status_code != (201 if revision is None else 200):
            raise LedgerError(f"GitHub ledger write failed (HTTP {response.status_code}).")
        try:
            new_revision = response.json()["content"]["sha"]
            if not SHA_PATTERN.fullmatch(new_revision):
                raise ValueError
            return new_revision
        except (ValueError, KeyError, TypeError):
            raise LedgerError("GitHub returned an invalid delivery ledger revision.") from None


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def destination_key(token: str, chat_id: str) -> str:
    return hmac.new(token.encode(), chat_id.casefold().encode(), hashlib.sha256).hexdigest()


def article_fingerprints(article: Article) -> set[str]:
    result = {fingerprint("url:" + json.dumps(url_key(article.url), ensure_ascii=False))}
    title = title_key(article.title)
    if title:
        result.add(fingerprint("title:" + title))
    return result


def sent_history(ledger: Ledger, day: date, destination: str) -> set[str]:
    seen: set[str] = set()
    for offset in range(1, HISTORY_DAYS + 1):
        saved = ledger.read(day - timedelta(days=offset))
        if saved and saved[0].destination == destination:
            for fingerprints in saved[0].confirmed.values():
                seen.update(fingerprints)
    return seen
