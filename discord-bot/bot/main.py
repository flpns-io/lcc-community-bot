"""Bot startup, slash-command synchronization, and global error handling."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import load_settings
from bot.database import AnnouncementStore, PollStore
from bot.embeds import respond_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("community_bot")

EXTENSIONS = (
    "bot.cogs.moderation",
    "bot.cogs.utility",
    "bot.cogs.announcements",
    "bot.cogs.polls",
)


class CommunityBot(commands.Bot):
    def __init__(self, settings) -> None:
        intents = discord.Intents.default()
        intents.members = True  # Needed for member join dates and complete role/member data.
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.announcement_store = AnnouncementStore(settings.database_path)
        self.poll_store = PollStore(settings.database_path)

    async def setup_hook(self) -> None:
        await self.announcement_store.initialize()
        await self.poll_store.initialize()
        for extension in EXTENSIONS:
            await self.load_extension(extension)

        if self.application_id and self.application_id != self.settings.client_id:
            raise RuntimeError(
                "CLIENT_ID does not match the application ID associated with DISCORD_TOKEN."
            )

        if self.settings.test_guild_id:
            guild = discord.Object(id=self.settings.test_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s slash commands to test guild %s", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global slash commands", len(synced))


bot = CommunityBot(load_settings())


@bot.event
async def on_ready() -> None:
    logger.info("Connected as %s (ID %s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have the server permissions required to use this command."
        title = "Permission required"
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = f"I need these permissions to do that: {', '.join(error.missing_permissions)}."
        title = "Bot permission missing"
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"Please wait {error.retry_after:.1f} seconds before trying again."
        title = "Slow down"
    else:
        original = getattr(error, "original", error)
        logger.error(
            "Unhandled slash-command error for %s",
            interaction.command.qualified_name if interaction.command else "unknown command",
            exc_info=(type(original), original, original.__traceback__),
        )
        message = "Something went wrong while running that command. Please try again later."
        title = "Command failed"
    await respond_error(interaction, title, message)


def main() -> None:
    settings = bot.settings
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
