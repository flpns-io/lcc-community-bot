"""Shared, consistent embed and interaction-response helpers."""

from __future__ import annotations

import discord

SUCCESS = discord.Color.green()
ERROR = discord.Color.red()
INFO = discord.Color.blurple()
WARNING = discord.Color.orange()


def success_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=SUCCESS)


def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=ERROR)


def info_embed(title: str, description: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=INFO)


async def respond_error(
    interaction: discord.Interaction,
    title: str,
    description: str,
) -> None:
    """Send an error whether or not the interaction has already been deferred."""
    embed = error_embed(title, description)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)
