"""Async SQLite storage for announcement schedules and interactive polls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class Announcement:
    id: int
    guild_id: int
    channel_id: int
    message: str
    interval_seconds: int | None
    next_run_at: datetime


@dataclass(frozen=True)
class Poll:
    tag: str
    guild_id: int
    channel_id: int
    message_id: int | None
    question: str
    options: tuple[str, str]
    created_at: datetime
    expires_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class PollVoteResult:
    counts: tuple[int, int]
    total: int
    closed: bool


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_utc_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AnnouncementStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.path, timeout=10) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA busy_timeout = 10000")
            yield connection

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS announcements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    interval_seconds INTEGER,
                    next_run_at TEXT NOT NULL,
                    lease_until TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_announcements_due
                ON announcements(next_run_at, lease_until)
                """
            )
            await db.commit()

    async def create(
        self,
        guild_id: int,
        channel_id: int,
        message: str,
        interval_seconds: int | None,
        next_run_at: datetime,
    ) -> int:
        now = to_utc_iso(datetime.now(timezone.utc))
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO announcements
                    (guild_id, channel_id, message, interval_seconds, next_run_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    message,
                    interval_seconds,
                    to_utc_iso(next_run_at),
                    now,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def list_for_guild(self, guild_id: int) -> list[Announcement]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT id, guild_id, channel_id, message, interval_seconds, next_run_at
                FROM announcements
                WHERE guild_id = ?
                ORDER BY next_run_at
                """,
                (guild_id,),
            )
            rows = await cursor.fetchall()
        return [
            Announcement(
                id=row["id"],
                guild_id=row["guild_id"],
                channel_id=row["channel_id"],
                message=row["message"],
                interval_seconds=row["interval_seconds"],
                next_run_at=from_utc_iso(row["next_run_at"]),
            )
            for row in rows
        ]

    async def delete(self, guild_id: int, announcement_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM announcements WHERE guild_id = ? AND id = ?",
                (guild_id, announcement_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def claim_due(self, now: datetime, lease_until: datetime) -> list[Announcement]:
        """Claim due rows atomically so two bot processes do not send them together."""
        now_iso = to_utc_iso(now)
        lease_iso = to_utc_iso(lease_until)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT id, guild_id, channel_id, message, interval_seconds, next_run_at
                FROM announcements
                WHERE next_run_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY next_run_at
                """,
                (now_iso, now_iso),
            )
            rows = await cursor.fetchall()
            if rows:
                await db.executemany(
                    "UPDATE announcements SET lease_until = ? WHERE id = ?",
                    [(lease_iso, row["id"]) for row in rows],
                )
            await db.commit()
        return [
            Announcement(
                id=row["id"],
                guild_id=row["guild_id"],
                channel_id=row["channel_id"],
                message=row["message"],
                interval_seconds=row["interval_seconds"],
                next_run_at=from_utc_iso(row["next_run_at"]),
            )
            for row in rows
        ]

    async def mark_sent(self, announcement: Announcement, sent_at: datetime) -> None:
        async with self._connect() as db:
            if announcement.interval_seconds is None:
                await db.execute("DELETE FROM announcements WHERE id = ?", (announcement.id,))
            else:
                next_run = sent_at.timestamp() + announcement.interval_seconds
                next_run_at = datetime.fromtimestamp(next_run, tz=timezone.utc)
                await db.execute(
                    """
                    UPDATE announcements
                    SET next_run_at = ?, lease_until = NULL
                    WHERE id = ?
                    """,
                    (to_utc_iso(next_run_at), announcement.id),
                )
            await db.commit()

    async def retry_later(self, announcement_id: int, retry_at: datetime) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE announcements
                SET next_run_at = ?, lease_until = NULL
                WHERE id = ?
                """,
                (to_utc_iso(retry_at), announcement_id),
            )
            await db.commit()

    async def remove(self, announcement_id: int) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
            await db.commit()


class PollStore:
    """Durable poll definitions and one current choice per voter."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.path, timeout=10) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA busy_timeout = 10000")
            await connection.execute("PRAGMA foreign_keys = ON")
            yield connection

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS polls (
                    tag TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    question TEXT NOT NULL,
                    option1 TEXT NOT NULL,
                    option2 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    closed_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_polls_open
                ON polls(closed_at, expires_at)
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS poll_votes (
                    poll_tag TEXT NOT NULL REFERENCES polls(tag) ON DELETE CASCADE,
                    voter_id INTEGER NOT NULL,
                    choice INTEGER NOT NULL CHECK (choice IN (0, 1)),
                    PRIMARY KEY (poll_tag, voter_id)
                )
                """
            )
            await db.commit()

    async def create(
        self,
        tag: str,
        guild_id: int,
        channel_id: int,
        question: str,
        options: tuple[str, str],
        expires_at: datetime,
    ) -> Poll:
        now = datetime.now(timezone.utc)
        poll = Poll(
            tag=tag,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=None,
            question=question,
            options=options,
            created_at=now,
            expires_at=expires_at,
            closed_at=None,
        )
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO polls (
                    tag, guild_id, channel_id, question, option1, option2,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    poll.tag,
                    poll.guild_id,
                    poll.channel_id,
                    poll.question,
                    poll.options[0],
                    poll.options[1],
                    to_utc_iso(poll.created_at),
                    to_utc_iso(poll.expires_at),
                ),
            )
            await db.commit()
        return poll

    async def set_message_id(self, tag: str, message_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE polls SET message_id = ? WHERE tag = ?",
                (message_id, tag),
            )
            await db.commit()

    async def list_open(self, now: datetime) -> list[Poll]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT tag, guild_id, channel_id, message_id, question, option1,
                       option2, created_at, expires_at, closed_at
                FROM polls
                WHERE closed_at IS NULL AND expires_at > ?
                ORDER BY created_at
                """,
                (to_utc_iso(now),),
            )
            rows = await cursor.fetchall()
        return [self._to_poll(row) for row in rows]

    async def get_counts(self, tag: str) -> tuple[int, int, int]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN choice = 0 THEN 1 ELSE 0 END), 0) AS first_count,
                    COALESCE(SUM(CASE WHEN choice = 1 THEN 1 ELSE 0 END), 0) AS second_count,
                    COUNT(*) AS total
                FROM poll_votes
                WHERE poll_tag = ?
                """,
                (tag,),
            )
            row = await cursor.fetchone()
        return (int(row["first_count"]), int(row["second_count"]), int(row["total"]))

    async def cast_vote(
        self,
        tag: str,
        voter_id: int,
        choice: int,
        now: datetime | None = None,
    ) -> PollVoteResult | None:
        if choice not in (0, 1):
            raise ValueError("Poll choice must be 0 or 1.")

        current_time = now or datetime.now(timezone.utc)
        current_time_iso = to_utc_iso(current_time)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT expires_at, closed_at FROM polls WHERE tag = ?",
                (tag,),
            )
            poll_row = await cursor.fetchone()
            if poll_row is None:
                await db.commit()
                return None

            expired = from_utc_iso(poll_row["expires_at"]) <= current_time
            closed = poll_row["closed_at"] is not None or expired
            if expired and poll_row["closed_at"] is None:
                await db.execute(
                    "UPDATE polls SET closed_at = ? WHERE tag = ?",
                    (current_time_iso, tag),
                )

            if not closed:
                await db.execute(
                    """
                    INSERT INTO poll_votes (poll_tag, voter_id, choice)
                    VALUES (?, ?, ?)
                    ON CONFLICT (poll_tag, voter_id)
                    DO UPDATE SET choice = excluded.choice
                    """,
                    (tag, voter_id, choice),
                )

            cursor = await db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN choice = 0 THEN 1 ELSE 0 END), 0) AS first_count,
                    COALESCE(SUM(CASE WHEN choice = 1 THEN 1 ELSE 0 END), 0) AS second_count,
                    COUNT(*) AS total
                FROM poll_votes
                WHERE poll_tag = ?
                """,
                (tag,),
            )
            counts_row = await cursor.fetchone()
            await db.commit()

        return PollVoteResult(
            counts=(int(counts_row["first_count"]), int(counts_row["second_count"])),
            total=int(counts_row["total"]),
            closed=closed,
        )

    async def close_expired(self, now: datetime) -> list[Poll]:
        now_iso = to_utc_iso(now)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT tag, guild_id, channel_id, message_id, question, option1,
                       option2, created_at, expires_at, closed_at
                FROM polls
                WHERE closed_at IS NULL AND expires_at <= ?
                ORDER BY expires_at
                """,
                (now_iso,),
            )
            rows = await cursor.fetchall()
            if rows:
                await db.executemany(
                    "UPDATE polls SET closed_at = ? WHERE tag = ?",
                    [(now_iso, row["tag"]) for row in rows],
                )
            await db.commit()
        return [self._to_poll(row, closed_at=now_iso) for row in rows]

    async def cleanup_closed(self, before: datetime) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM polls WHERE closed_at IS NOT NULL AND closed_at < ?",
                (to_utc_iso(before),),
            )
            await db.commit()
            return cursor.rowcount

    async def delete(self, tag: str) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM polls WHERE tag = ?", (tag,))
            await db.commit()

    @staticmethod
    def _to_poll(row: aiosqlite.Row, closed_at: str | None = None) -> Poll:
        return Poll(
            tag=row["tag"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            question=row["question"],
            options=(row["option1"], row["option2"]),
            created_at=from_utc_iso(row["created_at"]),
            expires_at=from_utc_iso(row["expires_at"]),
            closed_at=(
                from_utc_iso(closed_at if closed_at is not None else row["closed_at"])
                if closed_at is not None or row["closed_at"] is not None
                else None
            ),
        )
