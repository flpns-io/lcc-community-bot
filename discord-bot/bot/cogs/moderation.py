"""Server moderation slash commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds import respond_error, success_embed


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="kick", description="Remove a member from this server.")
    @app_commands.describe(member="Member to remove", reason="Reason recorded in the audit log")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 400] = "No reason provided",
    ) -> None:
        if interaction.guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        if member.id == interaction.user.id:
            return await respond_error(interaction, "Invalid target", "You cannot kick yourself.")
        if member.id == self.bot.user.id:
            return await respond_error(interaction, "Invalid target", "I cannot kick myself.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await member.kick(reason=f"{reason} (by {interaction.user} / {interaction.user.id})")
            await interaction.followup.send(
                embed=success_embed("Member kicked", f"{member.mention} was kicked.\n**Reason:** {reason}"),
                ephemeral=True,
            )
        except discord.Forbidden:
            await respond_error(interaction, "Action blocked", "Discord denied this action. Check role hierarchy and permissions.")
        except discord.HTTPException:
            await respond_error(interaction, "Request failed", "Discord could not complete the kick. Please try again.")

    @app_commands.command(name="ban", description="Ban a member from this server.")
    @app_commands.describe(member="Member to ban", reason="Reason recorded in the audit log")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 400] = "No reason provided",
    ) -> None:
        if interaction.guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        if member.id == interaction.user.id:
            return await respond_error(interaction, "Invalid target", "You cannot ban yourself.")
        if member.id == self.bot.user.id:
            return await respond_error(interaction, "Invalid target", "I cannot ban myself.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await interaction.guild.ban(
                member,
                reason=f"{reason} (by {interaction.user} / {interaction.user.id})",
            )
            await interaction.followup.send(
                embed=success_embed("Member banned", f"{member.mention} was banned.\n**Reason:** {reason}"),
                ephemeral=True,
            )
        except discord.Forbidden:
            await respond_error(interaction, "Action blocked", "Discord denied this action. Check role hierarchy and permissions.")
        except discord.HTTPException:
            await respond_error(interaction, "Request failed", "Discord could not complete the ban. Please try again.")

    @app_commands.command(name="clear", description="Delete recent messages in this channel.")
    @app_commands.describe(amount="Number of recent messages to delete (1–100)")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 100],
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await respond_error(interaction, "Unsupported channel", "Use this command in a text channel or thread.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            deleted = await channel.purge(limit=amount, reason=f"Cleared by {interaction.user} ({interaction.user.id})")
            await interaction.followup.send(
                embed=success_embed("Messages cleared", f"Deleted **{len(deleted)}** message(s)."),
                ephemeral=True,
            )
        except discord.Forbidden:
            await respond_error(interaction, "Permission missing", "I need Manage Messages and Read Message History in this channel.")
        except discord.HTTPException:
            await respond_error(interaction, "Request failed", "Discord could not delete those messages. Try a smaller amount.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
