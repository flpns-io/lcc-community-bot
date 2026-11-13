"""Persistent scheduled announcements and their background dispatcher."""

from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.database import Announcement, AnnouncementStore
from bot.embeds import INFO, info_embed, respond_error, success_embed

MIN_INTERVAL_SECONDS = 5 * 60
MAX_INTERVAL_SECONDS = 365 * 24 * 60 * 60
RETRY_DELAY = timedelta(minutes=5)
INTERVAL_PATTERN = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
logger = logging.getLogger(__name__)


def parse_schedule(value: str, now: datetime) -> tuple[datetime, int | None]:
    """Accept intervals like 30m or one-time UTC timestamps like once:2027-01-01T18:00:00Z."""
    text = value.strip()
    match = INTERVAL_PATTERN.fullmatch(text)
    if match:
        amount = int(match.group(1))
        unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]
        interval = amount * unit_seconds
        if not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
            raise ValueError("Recurring intervals must be between 5 minutes and 365 days.")
        return now + timedelta(seconds=interval), interval

    if text.lower().startswith("once:"):
        raw_timestamp = text[5:].strip()
        try:
            run_at = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Use `once:YYYY-MM-DDTHH:MM:SSZ` for a one-time UTC announcement.") from exc
        if run_at.tzinfo is None:
            raise ValueError("One-time timestamps must include a timezone; use a trailing `Z` for UTC.")
        run_at = run_at.astimezone(timezone.utc)
        if run_at < now + timedelta(seconds=30):
            raise ValueError("A one-time announcement must be scheduled at least 30 seconds in the future.")
        if run_at > now + timedelta(days=365):
            raise ValueError("One-time announcements can be scheduled up to 365 days ahead.")
        return run_at, None

    raise ValueError("Use a recurring interval such as `30m`, `2h`, `1d`, or a one-time UTC time such as `once:2027-01-01T18:00:00Z`.")


class AnnouncementWorker(commands.Cog):
    def __init__(self, bot: commands.Bot, store: AnnouncementStore) -> None:
        self.bot = bot
        self.store = store

    async def cog_load(self) -> None:
        self.dispatch_due_announcements.start()

    async def cog_unload(self) -> None:
        self.dispatch_due_announcements.cancel()

    @tasks.loop(seconds=15)
    async def dispatch_due_announcements(self) -> None:
        now = datetime.now(timezone.utc)
        try:
            due = await self.store.claim_due(now, now + timedelta(minutes=2))
        except Exception:
            logger.exception("Could not read due announcement schedules")
            return
        for item in due:
            try:
                await self._dispatch(item)
            except Exception:
                logger.exception("Unexpected error dispatching announcement schedule %s", item.id)

    @dispatch_due_announcements.before_loop
    async def before_dispatch(self) -> None:
        await self.bot.wait_until_ready()

    async def _dispatch(self, item: Announcement) -> None:
        channel = self.bot.get_channel(item.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(item.channel_id)
            except (discord.NotFound, discord.Forbidden):
                await self.store.remove(item.id)
                return
            except discord.HTTPException:
                await self.store.retry_later(item.id, datetime.now(timezone.utc) + RETRY_DELAY)
                return

        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.NewsChannel)):
            await self.store.remove(item.id)
            return

        embed = discord.Embed(description=item.message, color=INFO)
        embed.set_footer(text="Scheduled announcement")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await self.store.mark_sent(item, datetime.now(timezone.utc))
        except (discord.Forbidden, discord.NotFound):
            # This destination can no longer receive messages; remove the broken schedule.
            await self.store.remove(item.id)
        except discord.HTTPException:
            # discord.py handles Discord's standard rate-limit responses; defer other failures.
            await self.store.retry_later(item.id, datetime.now(timezone.utc) + RETRY_DELAY)


class Announcements(commands.Cog):
    def __init__(self, bot: commands.Bot, store: AnnouncementStore) -> None:
        self.bot = bot
        self.store = store
        self.announce = app_commands.Group(name="announce", description="Manage automated announcements")

        self.announce.add_command(
            app_commands.Command(
                name="create",
                description="Schedule a recurring or one-time announcement.",
                callback=self.create_announcement,
            )
        )
        self.announce.add_command(
            app_commands.Command(
                name="list",
                description="List this server's active scheduled announcements.",
                callback=self.list_announcements,
            )
        )
        self.announce.add_command(
            app_commands.Command(
                name="delete",
                description="Delete one of this server's scheduled announcements.",
                callback=self.delete_announcement,
            )
        )

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.announce)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.announce.name, type=self.announce.type)

    @app_commands.describe(
        channel="Text channel that will receive the announcement",
        interval_time="Recurring interval (30m, 2h, 1d) or one-time UTC time (once:2027-01-01T18:00:00Z)",
        message="Announcement text (up to 2,000 characters)",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(view_channel=True, send_messages=True, embed_links=True)
    async def create_announcement(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        interval_time: str,
        message: app_commands.Range[str, 1, 2000],
    ) -> None:
        if interaction.guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        if channel.guild.id != interaction.guild.id:
            return await respond_error(interaction, "Invalid channel", "Choose a text channel in this server.")
        me = interaction.guild.me
        permissions = channel.permissions_for(me) if me else discord.Permissions.none()
        if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            return await respond_error(interaction, "Channel permissions missing", "I need View Channel, Send Messages, and Embed Links in the selected channel.")
        try:
            next_run_at, interval_seconds = parse_schedule(interval_time, datetime.now(timezone.utc))
        except ValueError as exc:
            return await respond_error(interaction, "Invalid schedule", str(exc))

        schedule_id = await self.store.create(
            interaction.guild.id,
            channel.id,
            message,
            interval_seconds,
            next_run_at,
        )
        recurrence = "one-time" if interval_seconds is None else f"every {interval_time.strip()}"
        await interaction.response.send_message(
            embed=success_embed(
                "Announcement scheduled",
                f"Schedule ID: **{schedule_id}**\nChannel: {channel.mention}\nRuns: **{recurrence}**\nNext send: {discord.utils.format_dt(next_run_at, style='F')} (UTC)",
            ),
            ephemeral=True,
        )

    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_announcements(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        schedules = await self.store.list_for_guild(interaction.guild.id)
        if not schedules:
            return await interaction.response.send_message(
                embed=info_embed("Scheduled announcements", "There are no active schedules."),
                ephemeral=True,
            )
        lines: list[str] = []
        for item in schedules[:15]:
            channel_text = f"<#{item.channel_id}>"
            cadence = "one-time" if item.interval_seconds is None else f"every {self._format_interval(item.interval_seconds)}"
            message_preview = item.message.replace("\n", " ").strip()
            if len(message_preview) > 90:
                message_preview = message_preview[:87] + "..."
            lines.append(
                f"**ID {item.id}** · {channel_text} · {cadence}\n"
                f"Next: {discord.utils.format_dt(item.next_run_at, style='R')} · {discord.utils.escape_markdown(message_preview)}"
            )
        embed = info_embed("Scheduled announcements", "\n\n".join(lines))
        if len(schedules) > 15:
            embed.set_footer(text=f"Showing 15 of {len(schedules)} schedules. Use /announce delete with an ID.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.describe(announcement_id="ID shown by /announce list")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def delete_announcement(
        self,
        interaction: discord.Interaction,
        announcement_id: app_commands.Range[int, 1, 2_147_483_647],
    ) -> None:
        if interaction.guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        deleted = await self.store.delete(interaction.guild.id, announcement_id)
        if not deleted:
            return await respond_error(interaction, "Schedule not found", "No announcement with that ID exists in this server.")
        await interaction.response.send_message(
            embed=success_embed("Schedule deleted", f"Announcement schedule **{announcement_id}** was removed."),
            ephemeral=True,
        )

    @staticmethod
    def _format_interval(seconds: int) -> str:
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if seconds % size == 0:
                return f"{seconds // size}{unit}"
        return f"{seconds}s"


async def setup(bot: commands.Bot) -> None:
    store = bot.announcement_store  # type: ignore[attr-defined]
    await bot.add_cog(AnnouncementWorker(bot, store))
    await bot.add_cog(Announcements(bot, store))
