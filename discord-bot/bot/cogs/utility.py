"""General-purpose slash commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds import INFO, info_embed, respond_error


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Show the bot's Discord API latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        embed = discord.Embed(title="Pong", description=f"API latency: **{latency_ms} ms**", color=INFO)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="userinfo", description="Show information about a server member.")
    @app_commands.describe(member="Member to inspect (defaults to you)")
    async def userinfo(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        member = member or interaction.user
        if not isinstance(member, discord.Member):
            return await respond_error(interaction, "Member unavailable", "I could not load that server member.")

        roles = [role.mention for role in reversed(member.roles) if role != interaction.guild.default_role]
        role_text = ", ".join(roles[:15]) if roles else "No roles"
        if len(roles) > 15:
            role_text += f", and {len(roles) - 15} more"
        embed = info_embed(f"User information: {member.display_name}")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Username", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="F") if member.joined_at else "Unknown", inline=True)
        embed.add_field(name="Discord account created", value=discord.utils.format_dt(member.created_at, style="F"), inline=True)
        embed.add_field(name="Roles", value=role_text, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Show statistics about this server.")
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await respond_error(interaction, "Server only", "This command can only be used in a server.")
        owner = guild.owner.mention if guild.owner else f"<@{guild.owner_id}>"
        embed = info_embed(f"{guild.name} — server information")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=owner, inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count:,}" if guild.member_count is not None else "Unknown", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="D"), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
