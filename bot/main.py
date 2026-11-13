"""Main entrypoint for 蜈蚣bot (Discord Trading Idea & Order Tracking Bot)."""
from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

from bot.cogs.trades import TradesCog
from bot.config import BotConfig, load_config
from bot.database import TradeDatabase


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("wugong_bot")


class WuGongBot(commands.Bot):
    """蜈蚣bot Discord client with async SQL database lifecycle and command tree sync."""

    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )
        self.config = config
        self.db = TradeDatabase(config.database_url)

    async def setup_hook(self) -> None:
        """Initialize database tables, load cogs, and sync slash commands without duplicates."""
        logger.info("Initializing 蜈蚣bot database (%s)...", self.config.database_url.split("@")[-1])
        await self.db.init_db()
        await self.db.expire_past_options()

        await self.add_cog(TradesCog(self, self.db, self.config))

        if self.config.guild_id:
            guild_obj = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            # Clear global commands before syncing so Discord doesn't show BOTH
            # global + guild copies (which causes duplicate /search, /update in the UI).
            self.tree.clear_commands(guild=None)
            try:
                synced_guild = await self.tree.sync(guild=guild_obj)
                await self.tree.sync(guild=None)  # clears global duplicates on Discord's servers
                logger.info(
                    "Synced %d guild slash commands to guild ID %d (and cleared global duplicates).",
                    len(synced_guild),
                    self.config.guild_id,
                )
            except discord.Forbidden:
                logger.warning(
                    "403 Forbidden (Missing Access) when syncing to DISCORD_GUILD_ID=%d. "
                    "Make sure the bot is invited to that server with 'applications.commands' scope!",
                    self.config.guild_id,
                )
        else:
            synced = await self.tree.sync()
            logger.info("Synced %d global slash commands.", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            invite_url = (
                f"https://discord.com/oauth2/authorize?client_id={self.user.id}"
                f"&permissions=277025409024&scope=bot%20applications.commands"
            )
            guild_names = [f"{g.name} (ID: {g.id})" for g in self.guilds]
            logger.info("蜈蚣bot is online as %s (ID: %d)", self.user, self.user.id)
            logger.info("Connected servers (%d): %s", len(self.guilds), ", ".join(guild_names) if guild_names else "None yet")
            if self.config.guild_id and all(g.id != self.config.guild_id for g in self.guilds):
                logger.warning(
                    "DISCORD_GUILD_ID (%d) in .env does not match any server the bot is currently in! "
                    "Invite URL (with applications.commands scope): %s",
                    self.config.guild_id,
                    invite_url,
                )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Automatically sync slash commands immediately when the bot is invited to the target server."""
        logger.info("Joined server %s (ID: %d). Syncing slash commands...", guild.name, guild.id)
        if self.config.guild_id and self.config.guild_id == guild.id:
            try:
                self.tree.copy_global_to(guild=guild)
                self.tree.clear_commands(guild=None)
                synced = await self.tree.sync(guild=guild)
                await self.tree.sync(guild=None)
                logger.info("Instant-synced %d slash commands to %s (ID: %d).", len(synced), guild.name, guild.id)
            except discord.HTTPException as exc:
                logger.warning("Failed to guild-sync on join for %s: %s", guild.name, exc)

    async def close(self) -> None:
        await self.db.close()
        await super().close()


def main() -> None:
    config = load_config()
    if not config.discord_token:
        logger.error("DISCORD_BOT_TOKEN is not set. Please copy .env.example to .env and configure your token.")
        sys.exit(1)

    bot = WuGongBot(config)
    try:
        bot.run(config.discord_token, log_handler=None)
    except KeyboardInterrupt:
        asyncio.run(bot.close())


if __name__ == "__main__":
    main()
