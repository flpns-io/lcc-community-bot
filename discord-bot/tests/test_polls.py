from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.cogs.polls import PollView, Polls
from bot.database import AnnouncementStore, PollStore


class PollStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "polls.sqlite3"
        self.store = PollStore(self.database_path)
        await self.store.initialize()
        self.now = datetime.now(timezone.utc)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def create_poll(self, tag: str, expires_at: datetime):
        return await self.store.create(
            tag=tag,
            guild_id=123,
            channel_id=456,
            question="Which option?",
            options=("First", "Second"),
            expires_at=expires_at,
        )

    async def test_poll_and_votes_survive_reopening_database(self) -> None:
        poll = await self.create_poll("persistent-poll", self.now + timedelta(hours=1))
        await self.store.set_message_id(poll.tag, 789)
        await self.store.cast_vote(poll.tag, voter_id=11, choice=0, now=self.now)

        restarted_store = PollStore(self.database_path)
        await restarted_store.initialize()
        restored = await restarted_store.list_open(self.now)

        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].tag, poll.tag)
        self.assertEqual(restored[0].message_id, 789)
        self.assertEqual(restored[0].options, ("First", "Second"))
        self.assertEqual(await restarted_store.get_counts(poll.tag), (1, 0, 1))

    async def test_voter_can_change_choice_without_counting_twice(self) -> None:
        poll = await self.create_poll("changeable-poll", self.now + timedelta(hours=1))

        await self.store.cast_vote(poll.tag, voter_id=11, choice=0, now=self.now)
        changed = await self.store.cast_vote(poll.tag, voter_id=11, choice=1, now=self.now)
        counts = await self.store.get_counts(poll.tag)

        self.assertEqual(changed.counts, (0, 1))
        self.assertEqual(changed.total, 1)
        self.assertEqual(counts, (0, 1, 1))

    async def test_expired_poll_rejects_vote_and_can_be_cleaned_up(self) -> None:
        expires_at = self.now + timedelta(minutes=5)
        poll = await self.create_poll("expired-poll", expires_at)

        before_expiry = await self.store.cast_vote(
            poll.tag,
            voter_id=11,
            choice=0,
            now=self.now,
        )
        closed = await self.store.close_expired(expires_at + timedelta(seconds=1))
        after_expiry = await self.store.cast_vote(
            poll.tag,
            voter_id=12,
            choice=1,
            now=expires_at + timedelta(seconds=1),
        )

        self.assertFalse(before_expiry.closed)
        self.assertTrue(after_expiry.closed)
        self.assertEqual(after_expiry.total, 1)
        self.assertEqual([item.tag for item in closed], [poll.tag])
        self.assertEqual(await self.store.cleanup_closed(expires_at + timedelta(days=31)), 1)
        self.assertEqual(await self.store.get_counts(poll.tag), (0, 0, 0))

    async def test_invalid_choice_is_rejected(self) -> None:
        poll = await self.create_poll("invalid-choice-poll", self.now + timedelta(hours=1))

        with self.assertRaises(ValueError):
            await self.store.cast_vote(poll.tag, voter_id=11, choice=2, now=self.now)

        self.assertEqual(await self.store.get_counts(poll.tag), (0, 0, 0))

    async def test_announcement_store_uses_the_shared_connection_safely(self) -> None:
        announcement_store = AnnouncementStore(self.database_path)
        await announcement_store.initialize()

        announcement_id = await announcement_store.create(
            guild_id=123,
            channel_id=456,
            message="Reminder",
            interval_seconds=None,
            next_run_at=self.now + timedelta(minutes=5),
        )
        stored = await announcement_store.list_for_guild(123)

        self.assertEqual([item.id for item in stored], [announcement_id])

    async def test_restart_registers_a_persistent_view_for_each_open_poll(self) -> None:
        poll = await self.create_poll("restored-poll", self.now + timedelta(hours=1))
        await self.store.set_message_id(poll.tag, 789)

        class FakeBot:
            def __init__(self) -> None:
                self.views: list[tuple[PollView, int | None]] = []
                self.not_ready = asyncio.Event()

            def add_view(self, view: PollView, *, message_id: int | None = None) -> None:
                self.views.append((view, message_id))

            async def wait_until_ready(self) -> None:
                await self.not_ready.wait()

        bot = FakeBot()
        cog = Polls(bot, self.store)
        await cog.cog_load()
        try:
            self.assertEqual(len(bot.views), 1)
            view, message_id = bot.views[0]
            self.assertEqual(message_id, 789)
            self.assertIsNone(view.timeout)
            self.assertEqual(
                {item.custom_id for item in view.children},
                {f"poll:{poll.tag}:0", f"poll:{poll.tag}:1"},
            )
        finally:
            await cog.cog_unload()


if __name__ == "__main__":
    unittest.main()
