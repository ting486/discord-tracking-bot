"""Slash commands and scheduled EOD summary task for 蜈蚣bot:
- /open-options
- /open-stock
- /update (restricted to 蜈蚣头, with live autocomplete for caller's open & unexpired trades)
- /search (ephemeral by default, table output, '🔎 View Full Trade Details', status multi-select, 'Make it public' button)
- Automated End-of-Day (EOD) summary + ⚠️ Expiring Tomorrow / This Friday reminder in America/New_York time.
"""
from __future__ import annotations

import logging
from datetime import time as dt_time
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import BotConfig
from bot.database import (
    InvalidTradeStateError,
    PermissionDeniedError,
    TradeDatabase,
    TradeNotFoundError,
    TradeRecord,
)
from bot.formatters import (
    build_new_trade_embed,
    build_updated_trade_embed,
    format_eod_summary_message,
    render_trades_table_discord_file,
)
from bot.validators import (
     NY_TZ,
    ValidationError,
    format_ny_date,
    now_ny,
    parse_status_filter,
)
from bot.views import SearchPaginationView

logger = logging.getLogger("wugong_bot.trades")


def _build_filters_summary(
    *,
    initiator: Optional[discord.User | discord.Member],
    ticker: Optional[str],
    expiry: Optional[str],
    thesis: Optional[str],
    notes: Optional[str],
    statuses: Optional[List[str]],
    created_date: Optional[str],
) -> str:
    parts: List[str] = []
    if initiator is not None:
        parts.append(f"蜈蚣头=@{initiator.display_name}")
    if ticker:
        parts.append(f"ticker={ticker.strip().upper()}")
    if expiry:
        parts.append(f"expiry={expiry.strip()}")
    if thesis:
        parts.append(f"thesis~'{thesis.strip()}'")
    if notes:
        parts.append(f"notes~'{notes.strip()}'")
    if statuses:
        parts.append(f"status={','.join(statuses)}")
    if created_date:
        parts.append(f"created={created_date.strip()}")
    return "`" + "`, `".join(parts) + "`" if parts else "`all`"


class TradesCog(commands.Cog, name="Trades"):
    """Core trading idea & order tracking slash commands for 蜈蚣bot."""

    def __init__(self, bot: commands.Bot, db: TradeDatabase, config: BotConfig) -> None:
        self.bot = bot
        self.db = db
        self.config = config

        # Configure the daily EOD task time in America/New_York
        eod_time = dt_time(
            hour=config.eod_report_hour,
            minute=config.eod_report_minute,
            tzinfo=NY_TZ,
        )
        self.daily_eod_summary_task.change_interval(time=eod_time)
        self.daily_eod_summary_task.start()

    def cog_unload(self) -> None:
        self.daily_eod_summary_task.cancel()

    # =========================================================================
    # 1. /open-options
    # =========================================================================
    @app_commands.command(
        name="open-options",
        description="发起新期权订单 | Open a new options trade idea (蜈蚣bot)",
    )
    @app_commands.rename(initiator="蜈蚣头")
    @app_commands.describe(
        initiator="蜈蚣头 (Select the trade initiator in the group)",
        ticker="Stock/ETF ticker symbol (e.g. NVDA, SPY, TSLA)",
        expiry="Option expiration date in strict mm/dd/yyyy format (e.g. 12/18/2026)",
        strike="Option strike(s) (text field, e.g. 150C, 145/155, 580P)",
        strategy="Option strategy (text field, e.g. Long Call, Bull Call Spread, Iron Condor)",
        thesis="Trade thesis & reasoning",
        price="Optional option premium / debit / credit (e.g. $4.20 debit)",
        notes="Optional notes (e.g. target, stop-loss, sizing)",
    )
    async def open_options(
        self,
        interaction: discord.Interaction,
        initiator: discord.User,
        ticker: str,
        expiry: str,
        strike: str,
        strategy: str,
        thesis: str,
        price: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        guild_id = interaction.guild_id or 0
        channel_id = interaction.channel_id or 0

        await interaction.response.defer(ephemeral=False)

        try:
            trade = await self.db.create_options_trade(
                guild_id=guild_id,
                channel_id=channel_id,
                initiator_id=initiator.id,
                initiator_name=initiator.display_name,
                created_by_id=interaction.user.id,
                ticker=ticker,
                expiry=expiry,
                strike=strike,
                strategy=strategy,
                thesis=thesis,
                price=price,
                notes=notes,
            )
        except ValidationError as exc:
            await interaction.followup.send(f"❌ **Validation Error:** {exc}", ephemeral=True)
            return

        embed = build_new_trade_embed(trade)
        sent_msg = await interaction.followup.send(
            content=f"📣 **蜈蚣bot** 记录了新的期权订单 `#{trade.id}` — 蜈蚣头: <@{initiator.id}>",
            embed=embed,
            wait=True,
        )

        try:
            await self.db.set_message_url(trade.id, sent_msg.jump_url)
        except discord.HTTPException:
            pass

    # =========================================================================
    # 2. /open-stock
    # =========================================================================
    @app_commands.command(
        name="open-stock",
        description="发起新正股订单 | Open a new stock trade idea (long/short)",
    )
    @app_commands.rename(initiator="蜈蚣头")
    @app_commands.describe(
        initiator="蜈蚣头 (Select the trade initiator in the group)",
        ticker="Stock/ETF ticker symbol (e.g. AAPL, NVDA, QQQ)",
        long_short="Select direction: long or short",
        thesis="Trade thesis & reasoning",
        entry_price="Optional entry price (e.g. $138.50)",
        notes="Optional notes (e.g. target price, stop-loss)",
    )
    @app_commands.choices(
        long_short=[
            app_commands.Choice(name="long 📈", value="long"),
            app_commands.Choice(name="short 📉", value="short"),
        ]
    )
    async def open_stock(
        self,
        interaction: discord.Interaction,
        initiator: discord.User,
        ticker: str,
        long_short: app_commands.Choice[str],
        thesis: str,
        entry_price: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        guild_id = interaction.guild_id or 0
        channel_id = interaction.channel_id or 0

        await interaction.response.defer(ephemeral=False)

        try:
            trade = await self.db.create_stock_trade(
                guild_id=guild_id,
                channel_id=channel_id,
                initiator_id=initiator.id,
                initiator_name=initiator.display_name,
                created_by_id=interaction.user.id,
                ticker=ticker,
                long_short=long_short.value,
                thesis=thesis,
                entry_price=entry_price,
                notes=notes,
            )
        except ValidationError as exc:
            await interaction.followup.send(f"❌ **Validation Error:** {exc}", ephemeral=True)
            return

        embed = build_new_trade_embed(trade)
        sent_msg = await interaction.followup.send(
            content=f"📣 **蜈蚣bot** 记录了新的正股订单 `#{trade.id}` — 蜈蚣头: <@{initiator.id}>",
            embed=embed,
            wait=True,
        )

        try:
            await self.db.set_message_url(trade.id, sent_msg.jump_url)
        except discord.HTTPException:
            pass

    # =========================================================================
    # 3. /update (Only 蜈蚣头 can update their own open & unexpired trades)
    # =========================================================================
    @app_commands.command(
        name="update",
        description="更新未到期订单 | Update an existing open & unexpired trade (Only 蜈蚣头)",
    )
    @app_commands.describe(
        trade_id="Select one of your active open & unexpired trades (#ID)",
        close_trade="Set to True if closing this trade",
        additional_comments="Additional comments/updates to append to notes",
    )
    async def update_trade_cmd(
        self,
        interaction: discord.Interaction,
        trade_id: int,
        close_trade: Optional[bool] = False,
        additional_comments: Optional[str] = None,
    ) -> None:
        guild_id = interaction.guild_id

        try:
            updated = await self.db.update_trade(
                trade_id=trade_id,
                actor_user_id=interaction.user.id,
                guild_id=guild_id,
                additional_comments=additional_comments,
                close_trade=bool(close_trade),
            )
        except PermissionDeniedError as exc:
            await interaction.response.send_message(f"🚫 **Permission Denied:** {exc}", ephemeral=True)
            return
        except (TradeNotFoundError, InvalidTradeStateError, ValidationError) as exc:
            await interaction.response.send_message(f"❌ **Cannot Update Order:** {exc}", ephemeral=True)
            return

        embed = build_updated_trade_embed(updated)
        action_text = "已平仓 (closed)" if updated.status == "closed" else "已更新备注 (updated)"
        await interaction.response.send_message(
            content=f"🔄 **蜈蚣头** <@{updated.initiator_id}> {action_text} 订单 `#{updated.id}` (`{updated.ticker}`)",
            embed=embed,
            ephemeral=False,
        )

    @update_trade_cmd.autocomplete("trade_id")
    async def update_trade_id_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[int]]:
        """
        Autocomplete for `/update` `trade_id`: queries only the caller's active `open`
        & unexpired trades and displays them as readable choices.
        """
        trades = await self.db.get_open_unexpired_trades_for_user(
            initiator_id=interaction.user.id,
            guild_id=interaction.guild_id,
            search_text=current,
            limit=25,
        )
        return [
            app_commands.Choice(name=t.autocomplete_label(), value=t.id)
            for t in trades
        ]

    # =========================================================================
    # 4. /search (Ephemeral by default + "Make it public" button + Detail Select)
    # =========================================================================
    @app_commands.command(
        name="search",
        description="查询历史订单 (默认仅自己可见，可点击 Make it public 公开到群聊)",
    )
    @app_commands.rename(initiator="蜈蚣头")
    @app_commands.describe(
        initiator="Filter by 蜈蚣头 (@user)",
        ticker="Filter by ticker symbol (e.g. NVDA)",
        expiry="Filter by option expiry date in mm/dd/yyyy format (e.g. 12/18/2026)",
        thesis="Search keyword in thesis",
        notes="Search keyword in notes",
        status="Filter by status (e.g. 'open', 'closed', 'expired', or comma-separated 'open,closed')",
        created_date="Filter by creation date in New York time (mm/dd/yyyy)",
        sort_by="Field to sort results by (default: created_at)",
        sort_order="Sort direction (DESC or ASC)",
    )
    @app_commands.choices(
        sort_by=[
            app_commands.Choice(name="Creation Date (created_at)", value="created_at"),
            app_commands.Choice(name="蜈蚣头 (Initiator)", value="蜈蚣头"),
            app_commands.Choice(name="Ticker", value="ticker"),
            app_commands.Choice(name="Expiry (mm/dd/yyyy)", value="expiry"),
            app_commands.Choice(name="Status (open/closed/expired)", value="status"),
            app_commands.Choice(name="Thesis", value="thesis"),
            app_commands.Choice(name="Notes", value="notes"),
        ],
        sort_order=[
            app_commands.Choice(name="Descending (Newest / Z→A)", value="DESC"),
            app_commands.Choice(name="Ascending (Oldest / A→Z)", value="ASC"),
        ],
    )
    async def search_cmd(
        self,
        interaction: discord.Interaction,
        initiator: Optional[discord.User] = None,
        ticker: Optional[str] = None,
        expiry: Optional[str] = None,
        thesis: Optional[str] = None,
        notes: Optional[str] = None,
        status: Optional[str] = None,
        created_date: Optional[str] = None,
        sort_by: Optional[app_commands.Choice[str]] = None,
        sort_order: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        guild_id = interaction.guild_id
        chosen_sort_by = sort_by.value if sort_by else "created_at"
        chosen_sort_order = sort_order.value if sort_order else "DESC"

        await interaction.response.defer(ephemeral=True)

        try:
            initial_statuses = parse_status_filter(status)
            trades = await self.db.search_trades(
                guild_id=guild_id,
                initiator_id=initiator.id if initiator else None,
                ticker=ticker,
                expiry=expiry,
                thesis_keyword=thesis,
                notes_keyword=notes,
                statuses=initial_statuses,
                created_date=created_date,
                sort_by=chosen_sort_by,
                sort_order=chosen_sort_order,
            )
        except ValidationError as exc:
            await interaction.followup.send(f"❌ **Search Filter Error:** {exc}", ephemeral=True)
            return

        filters_summary = _build_filters_summary(
            initiator=initiator,
            ticker=ticker,
            expiry=expiry,
            thesis=thesis,
            notes=notes,
            statuses=initial_statuses,
            created_date=created_date,
        )

        async def _requery_with_statuses(new_statuses: Optional[List[str]]) -> tuple[List[TradeRecord], str]:
            refreshed = await self.db.search_trades(
                guild_id=guild_id,
                initiator_id=initiator.id if initiator else None,
                ticker=ticker,
                expiry=expiry,
                thesis_keyword=thesis,
                notes_keyword=notes,
                statuses=new_statuses,
                created_date=created_date,
                sort_by=chosen_sort_by,
                sort_order=chosen_sort_order,
            )
            new_summary = _build_filters_summary(
                initiator=initiator,
                ticker=ticker,
                expiry=expiry,
                thesis=thesis,
                notes=notes,
                statuses=new_statuses,
                created_date=created_date,
            )
            return refreshed, new_summary

        view = SearchPaginationView(
            trades=trades,
            filters_summary=filters_summary,
            sort_by=chosen_sort_by,
            sort_order=chosen_sort_order,
            active_statuses=initial_statuses,
            requery_callback=_requery_with_statuses,
        )

        await interaction.followup.send(
            content=view.render_message_content(is_public=False),
            file=view.render_table_file(),
            view=view,
            ephemeral=True,
        )

    @search_cmd.autocomplete("ticker")
    async def search_ticker_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        tickers = await self.db.get_distinct_tickers(
            guild_id=interaction.guild_id,
            prefix=current,
            limit=25,
        )
        return [app_commands.Choice(name=t, value=t) for t in tickers]

    # =========================================================================
    # 5. Scheduled End-of-Day (EOD) Summary Task (New York Market Days)
    # =========================================================================
    @tasks.loop(time=dt_time(hour=16, minute=15, tzinfo=NY_TZ))
    async def daily_eod_summary_task(self) -> None:
        """
        Runs every day at 16:15 America/New_York (or configured EOD_REPORT_TIME):
        1. Always runs EOD option expiration (`include_today=True`) so any open options
           expiring today transition to `expired` at EOD (even if EOD_CHANNEL_ID is unset).
        2. On US market weekdays (Mon–Fri), posts all trades initiated that NY day plus
           the '⚠️ Expiring Tomorrow / This Friday' reminder section.
        """
        current_ny = now_ny()

        # 1. Always expire today's and past-due options at EOD
        expired_count = await self.db.expire_past_options(now_dt=current_ny, include_today=True)
        if expired_count > 0:
            logger.info("EOD auto-expiration marked %d option trade(s) as expired.", expired_count)

        # 2. Monday=0 .. Friday=4; skip Saturday (5) & Sunday (6) for channel summary post
        if current_ny.weekday() >= 5:
            return

        if not self.config.eod_channel_id:
            logger.debug("EOD_CHANNEL_ID is not configured; skipping automated EOD channel message.")
            return

        channel = self.bot.get_channel(self.config.eod_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.config.eod_channel_id)
            except discord.HTTPException as exc:
                logger.warning("Failed to fetch EOD_CHANNEL_ID %s: %s", self.config.eod_channel_id, exc)
                return

        if not hasattr(channel, "send"):
            return

        guild_id = getattr(getattr(channel, "guild", None), "id", None)
        ny_date_str = format_ny_date(current_ny)

        initiated_today = await self.db.get_daily_initiated_trades(
            guild_id=guild_id,
            ny_date_mmddyyyy=ny_date_str,
            now_dt=current_ny,
        )
        expiring_soon = await self.db.get_expiring_soon_options(
            guild_id=guild_id,
            now_dt=current_ny,
        )

        msg = format_eod_summary_message(
            ny_date_str=ny_date_str,
            initiated_today=initiated_today,
            expiring_soon=expiring_soon,
        )
        if initiated_today:
            eod_file = render_trades_table_discord_file(
                initiated_today,
                filename="eod_trades_table.png",
                title=f"蜈蚣bot • 今日日报 | EOD Trades Initiated ({ny_date_str})",
                subtitle=f"Total initiated today: {len(initiated_today)}",
            )
            await channel.send(content=msg, file=eod_file)  # type: ignore[union-attr]
        else:
            await channel.send(content=msg)  # type: ignore[union-attr]

    @daily_eod_summary_task.before_loop
    async def before_eod_task(self) -> None:
        await self.bot.wait_until_ready()

