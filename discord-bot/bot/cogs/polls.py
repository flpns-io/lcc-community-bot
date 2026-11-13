"""Interactive polls backed by persistent SQLite storage."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.database import Poll, PollStore
from bot.embeds import INFO, info_embed, respond_error

POLL_DURATION = timedelta(hours=24)
POLL_RETENTION = timedelta(days=30)
logger = logging.getLogger(__name__)


class PollButton(discord.ui.Button):
    def __init__(self, poll_tag: str, index: int, label: str) -> None:
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary if index == 0 else discord.ButtonStyle.secondary,
            custom_id=f"poll:{poll_tag}:{index}",
        )
        self.choice = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, PollView):
            return
        try:
            result = await view.store.cast_vote(view.poll_tag, interaction.user.id, self.choice)
        except Exception:
            logger.exception("Could not record a vote for poll %s", view.poll_tag)
            return await interaction.response.send_message(
                "Your vote could not be saved. Please try again.",
                ephemeral=True,
            )

        if result is None:
            view.disable_all_items()
            return await interaction.response.send_message(
                "This poll is no longer available.",
                ephemeral=True,
            )

        view.update_counts(result.counts)
        if result.closed:
            view.closed = True
            view.disable_all_items()
        await interaction.response.edit_message(embed=view.make_embed(), view=view)
        if result.closed:
            await interaction.followup.send("This poll has ended, so your vote was not counted.", ephemeral=True)


class PollView(discord.ui.View):
    """A persistent poll view whose results update immediately."""

    def __init__(
        self,
        poll: Poll,
        store: PollStore,
        counts: tuple[int, int] = (0, 0),
        *,
        closed: bool = False,
    ) -> None:
        # A persistent Discord view must have no timeout and stable custom IDs.
        super().__init__(timeout=None)
        self.poll_tag = poll.tag
        self.question = poll.question
        self.options = poll.options
        self.store = store
        self.counts = counts
        self.closed = closed

        for index, option in enumerate(self.options):
            button = PollButton(self.poll_tag, index, option)
            button.disabled = closed
            self.add_item(button)

    def update_counts(self, counts: tuple[int, int]) -> None:
        self.counts = counts

    def make_embed(self) -> discord.Embed:
        total = sum(self.counts)
        lines = [
            (
                f"**{self.options[index]}** — {self.counts[index]} vote(s) "
                f"({(self.counts[index] / total * 100):.0f}%)"
            )
            if total
            else f"**{self.options[index]}** — 0 votes"
            for index in range(2)
        ]
        embed = info_embed(self.question, "\n".join(lines))
        footer = f"{total} unique voter(s)"
        if not self.closed:
            footer += " · Vote again to change your choice"
        else:
            footer += " · Poll closed"
        embed.set_footer(text=footer)
        return embed


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot, store: PollStore) -> None:
        self.bot = bot
        self.store = store
        self._refresh_open_polls = True

    async def cog_load(self) -> None:
        polls = await self.store.list_open(datetime.now(timezone.utc))
        for poll in polls:
            counts = await self.store.get_counts(poll.tag)
            view = PollView(poll, self.store, counts[:2])
            self.bot.add_view(view, message_id=poll.message_id)
        self.maintain_polls.start()

    async def cog_unload(self) -> None:
        self.maintain_polls.cancel()

    @tasks.loop(minutes=1)
    async def maintain_polls(self) -> None:
        now = datetime.now(timezone.utc)
        try:
            expired_polls = await self.store.close_expired(now)
            for poll in expired_polls:
                counts = await self.store.get_counts(poll.tag)
                view = PollView(poll, self.store, counts[:2], closed=True)
                if poll.message_id is not None:
                    # Keep a handler registered if Discord temporarily rejects the edit.
                    self.bot.add_view(view, message_id=poll.message_id)
                    await self._refresh_message(poll, view, remove_view_on_success=True)

            if self._refresh_open_polls:
                for poll in await self.store.list_open(now):
                    counts = await self.store.get_counts(poll.tag)
                    await self._refresh_message(
                        poll,
                        PollView(poll, self.store, counts[:2]),
                    )
                self._refresh_open_polls = False

            await self.store.cleanup_closed(now - POLL_RETENTION)
        except Exception:
            logger.exception("Could not maintain persistent polls")

    @maintain_polls.before_loop
    async def before_maintaining_polls(self) -> None:
        await self.bot.wait_until_ready()

    async def _refresh_message(
        self,
        poll: Poll,
        view: PollView,
        *,
        remove_view_on_success: bool = False,
    ) -> bool:
        if poll.message_id is None:
            return False
        try:
            channel = self.bot.get_channel(poll.channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(poll.channel_id)
            if not hasattr(channel, "fetch_message"):
                return False
            message = await channel.fetch_message(poll.message_id)
            await message.edit(embed=view.make_embed(), view=view)
            if remove_view_on_success:
                self.bot.remove_view(view, message_id=poll.message_id)
            return True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.warning(
                "Could not refresh poll message %s in channel %s",
                poll.message_id,
                poll.channel_id,
                exc_info=True,
            )
            return False

    @app_commands.command(name="poll", description="Create an interactive two-option poll.")
    @app_commands.describe(question="Question for the poll", option1="First choice", option2="Second choice")
    async def poll(
        self,
        interaction: discord.Interaction,
        question: app_commands.Range[str, 1, 200],
        option1: app_commands.Range[str, 1, 80],
        option2: app_commands.Range[str, 1, 80],
    ) -> None:
        if interaction.guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        if option1.casefold() == option2.casefold():
            return await respond_error(interaction, "Choices must differ", "Provide two different poll options.")

        now = datetime.now(timezone.utc)
        poll = await self.store.create(
            uuid.uuid4().hex,
            interaction.guild.id,
            interaction.channel_id,
            question,
            (option1, option2),
            now + POLL_DURATION,
        )
        view = PollView(poll, self.store)
        try:
            await interaction.response.send_message(embed=view.make_embed(), view=view)
        except Exception:
            await self.store.delete(poll.tag)
            raise

        try:
            message = await interaction.original_response()
            await self.store.set_message_id(poll.tag, message.id)
            self.bot.add_view(view, message_id=message.id)
        except Exception:
            # Keep the poll and a generic persistent handler if Discord accepted the message
            # but returning its message ID or persisting it failed.
            logger.exception("Could not save the message ID for poll %s", poll.tag)
            self.bot.add_view(view)


async def setup(bot: commands.Bot) -> None:
    store = bot.poll_store  # type: ignore[attr-defined]
    await bot.add_cog(Polls(bot, store))
