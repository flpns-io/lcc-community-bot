from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.cogs.announcements import (
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    parse_schedule,
)
from bot.database import AnnouncementStore


class ParseScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

    def test_parses_recurring_intervals(self) -> None:
        cases = (
            ("5m", MIN_INTERVAL_SECONDS),
            ("30m", 30 * 60),
            ("2h", 2 * 60 * 60),
            ("1d", 24 * 60 * 60),
            ("365d", MAX_INTERVAL_SECONDS),
            (" 30M ", 30 * 60),
        )

        for value, seconds in cases:
            with self.subTest(value=value):
                next_run_at, interval_seconds = parse_schedule(value, self.now)
                self.assertEqual(next_run_at, self.now + timedelta(seconds=seconds))
                self.assertEqual(interval_seconds, seconds)

    def test_parses_one_time_utc_timestamp(self) -> None:
        run_at, interval_seconds = parse_schedule(
            "once:2026-10-24T18:30:00Z",
            self.now,
        )

        self.assertEqual(run_at, datetime(2026, 10, 24, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(run_at.tzinfo, timezone.utc)
        self.assertIsNone(interval_seconds)

    def test_accepts_exact_one_time_timestamp_boundaries(self) -> None:
        earliest = self.now + timedelta(seconds=30)
        latest = self.now + timedelta(days=365)

        self.assertEqual(parse_schedule(f"once:{earliest.isoformat()}", self.now)[0], earliest)
        self.assertEqual(parse_schedule(f"once:{latest.isoformat()}", self.now)[0], latest)

    def test_rejects_invalid_and_out_of_range_schedules(self) -> None:
        invalid_values = (
            "",
            "daily",
            "30x",
            "0m",
            "4m",
            "366d",
            "once:not-a-timestamp",
            "once:2026-10-24T18:30:00",
            "once:2026-10-23T12:00:29Z",
            "once:2027-10-23T12:00:01Z",
        )

        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_schedule(value, self.now)


class AnnouncementStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "announcements.sqlite3"
        self.store = AnnouncementStore(self.database_path)
        await self.store.initialize()
        self.now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def create_schedule(
        self,
        *,
        guild_id: int = 123,
        channel_id: int = 456,
        message: str = "A scheduled reminder",
        interval_seconds: int | None = 3600,
        next_run_at: datetime | None = None,
    ) -> int:
        return await self.store.create(
            guild_id=guild_id,
            channel_id=channel_id,
            message=message,
            interval_seconds=interval_seconds,
            next_run_at=next_run_at or self.now,
        )

    async def test_schedule_persists_after_store_restart(self) -> None:
        schedule_id = await self.create_schedule(
            interval_seconds=None,
            next_run_at=self.now + timedelta(minutes=5),
        )

        restarted_store = AnnouncementStore(self.database_path)
        await restarted_store.initialize()
        schedules = await restarted_store.list_for_guild(123)

        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0].id, schedule_id)
        self.assertEqual(schedules[0].message, "A scheduled reminder")
        self.assertIsNone(schedules[0].interval_seconds)
        self.assertEqual(schedules[0].next_run_at, self.now + timedelta(minutes=5))

    async def test_delete_is_scoped_to_the_schedule_guild(self) -> None:
        schedule_id = await self.create_schedule()

        self.assertFalse(await self.store.delete(999, schedule_id))
        self.assertTrue(await self.store.delete(123, schedule_id))
        self.assertEqual(await self.store.list_for_guild(123), [])

    async def test_claim_due_sets_a_lease_and_excludes_future_schedules(self) -> None:
        due_id = await self.create_schedule(next_run_at=self.now - timedelta(seconds=1))
        await self.create_schedule(next_run_at=self.now + timedelta(hours=1))
        lease_until = self.now + timedelta(minutes=2)

        claimed = await self.store.claim_due(self.now, lease_until)
        claimed_again = await self.store.claim_due(self.now, lease_until)
        claimed_after_lease = await self.store.claim_due(
            lease_until + timedelta(seconds=1),
            lease_until + timedelta(minutes=2),
        )

        self.assertEqual([item.id for item in claimed], [due_id])
        self.assertEqual(claimed_again, [])
        self.assertEqual([item.id for item in claimed_after_lease], [due_id])

    async def test_competing_store_instances_do_not_claim_the_same_row(self) -> None:
        schedule_id = await self.create_schedule()
        second_store = AnnouncementStore(self.database_path)
        lease_until = self.now + timedelta(minutes=2)

        claims = await asyncio.gather(
            self.store.claim_due(self.now, lease_until),
            second_store.claim_due(self.now, lease_until),
        )

        self.assertEqual(
            sorted(item.id for claim in claims for item in claim),
            [schedule_id],
        )

    async def test_retry_moves_schedule_and_makes_it_claimable_again(self) -> None:
        schedule_id = await self.create_schedule()
        await self.store.claim_due(self.now, self.now + timedelta(minutes=2))
        retry_at = self.now + timedelta(minutes=5)

        await self.store.retry_later(schedule_id, retry_at)
        before_retry = await self.store.claim_due(
            retry_at - timedelta(seconds=1),
            retry_at + timedelta(minutes=2),
        )
        at_retry = await self.store.claim_due(
            retry_at,
            retry_at + timedelta(minutes=2),
        )

        self.assertEqual(before_retry, [])
        self.assertEqual([item.id for item in at_retry], [schedule_id])
        self.assertEqual(at_retry[0].next_run_at, retry_at)

    async def test_mark_sent_advances_recurring_schedule_and_releases_lease(self) -> None:
        schedule_id = await self.create_schedule(interval_seconds=3600)
        claimed = await self.store.claim_due(self.now, self.now + timedelta(minutes=2))
        sent_at = self.now + timedelta(seconds=20)

        await self.store.mark_sent(claimed[0], sent_at)
        schedules = await self.store.list_for_guild(123)
        next_claim = await self.store.claim_due(
            sent_at + timedelta(seconds=3599),
            sent_at + timedelta(hours=2),
        )

        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0].id, schedule_id)
        self.assertEqual(schedules[0].next_run_at, sent_at + timedelta(hours=1))
        self.assertEqual(next_claim, [])

    async def test_mark_sent_removes_one_time_schedule(self) -> None:
        await self.create_schedule(interval_seconds=None)
        claimed = await self.store.claim_due(self.now, self.now + timedelta(minutes=2))

        await self.store.mark_sent(claimed[0], self.now + timedelta(seconds=1))

        self.assertEqual(await self.store.list_for_guild(123), [])


if __name__ == "__main__":
    unittest.main()
