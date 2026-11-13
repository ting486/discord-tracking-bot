"""Interactive Discord UI Views for `/search`:
- Ephemeral pagination (`◀ Prev` / `Next ▶`)
- "🔎 View Full Trade Details" dropdown (un-truncated thesis, notes history, jump link)
- Interactive Status Multi-Select dropdown (`open`, `closed`, `expired`)
- "Make it public" button to publish the ephemeral search result or trade detail to the channel.
"""
from __future__ import annotations

from typing import Callable, Coroutine, Any, List, Optional, Sequence

import discord

from bot.database import TradeRecord
from bot.formatters import (
    build_trade_detail_embed,
    format_search_message,
    render_trades_table_discord_file,
    truncate_to_width,
)


class ShareDetailPublicView(discord.ui.View):
    """View attached to an ephemeral full trade detail card allowing the user to post it publicly."""

    def __init__(self, trade: TradeRecord, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.trade = trade

    @discord.ui.button(
        label="Make it public",
        style=discord.ButtonStyle.success,
        emoji="📢",
    )
    async def make_detail_public(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        # 1. Acknowledge the button click immediately (within 3s) so Discord never times out!
        button.disabled = True
        button.label = "Published to Channel"
        await interaction.response.edit_message(view=self)

        # 2. Post the public message via followup (works with applications.commands scope alone)
        embed = build_trade_detail_embed(self.trade)
        embed.set_footer(
            text=f"蜈蚣bot • Trade #{self.trade.id} • Shared publicly by @{interaction.user.display_name}"
        )
        await interaction.followup.send(embed=embed, ephemeral=False)


class TradeDetailSelect(discord.ui.Select):
    """'🔎 View Full Trade Details' dropdown right below the `/search` table."""

    def __init__(self, page_trades: Sequence[TradeRecord]) -> None:
        options: List[discord.SelectOption] = []
        for t in page_trades[:25]:
            desc_parts = [t.asset_type, f"@{t.initiator_name}"]
            if t.expiry:
                desc_parts.append(f"Exp {t.expiry}")
            desc_parts.append(truncate_to_width(t.thesis, 40))
            options.append(
                discord.SelectOption(
                    label=f"#{t.id} | {t.ticker} ({t.status})",
                    value=str(t.id),
                    description=" • ".join(desc_parts)[:100],
                    emoji="🔎",
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No trades on this page",
                    value="none",
                    description="Run a broader search to inspect trade details",
                )
            )

        super().__init__(
            placeholder="🔎 View Full Trade Details (Select #ID)...",
            min_values=1,
            max_values=1,
            options=options,
            disabled=(len(page_trades) == 0),
            row=0,
        )
        self._trades_by_id = {str(t.id): t for t in page_trades}

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_id = self.values[0]
        trade = self._trades_by_id.get(selected_id)
        if trade is None:
            await interaction.response.send_message("Trade not found on this page.", ephemeral=True)
            return

        embed = build_trade_detail_embed(trade)
        view = ShareDetailPublicView(trade)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class StatusMultiSelect(discord.ui.Select):
    """Interactive multi-select status filter (`open`, `closed`, `expired`) on `/search`."""

    def __init__(self, active_statuses: Optional[Sequence[str]] = None) -> None:
        active_set = {s.lower() for s in (active_statuses or [])}
        options = [
            discord.SelectOption(
                label="open",
                value="open",
                description="Currently active, unexpired trades",
                emoji="🟢",
                default=("open" in active_set),
            ),
            discord.SelectOption(
                label="closed",
                value="closed",
                description="Trades closed by 蜈蚣头",
                emoji="🔴",
                default=("closed" in active_set),
            ),
            discord.SelectOption(
                label="expired",
                value="expired",
                description="Options past their mm/dd/yyyy expiry date",
                emoji="⚪",
                default=("expired" in active_set),
            ),
        ]
        super().__init__(
            placeholder="Filter by Status (multi-select: open / closed / expired)...",
            min_values=0,
            max_values=3,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        parent_view: SearchPaginationView = self.view  # type: ignore[assignment]
        await parent_view.on_status_filter_changed(interaction, list(self.values))


class SearchPaginationView(discord.ui.View):
    """
    Ephemeral interactive view for `/search` results featuring:
    - Row 0: "🔎 View Full Trade Details" dropdown
    - Row 1: Status multi-select dropdown (`open` / `closed` / `expired`)
    - Row 2: `◀ Prev`, `Page X/Y`, `Next ▶`, and `📢 Make it public` button.
    """

    def __init__(
        self,
        *,
        trades: Sequence[TradeRecord],
        filters_summary: str,
        sort_by: str,
        sort_order: str,
        active_statuses: Optional[List[str]] = None,
        per_page: int = 8,
        requery_callback: Optional[
            Callable[[Optional[List[str]]], Coroutine[Any, Any, tuple[List[TradeRecord], str]]]
        ] = None,
        timeout: float = 600.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.trades: List[TradeRecord] = list(trades)
        self.filters_summary = filters_summary
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.active_statuses: Optional[List[str]] = active_statuses
        self.per_page = per_page
        self.page = 0
        self.requery_callback = requery_callback
        self.is_published = False

        self._rebuild_components()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.trades) + self.per_page - 1) // self.per_page)

    def current_page_slice(self) -> Sequence[TradeRecord]:
        start = self.page * self.per_page
        end = min(len(self.trades), start + self.per_page)
        return self.trades[start:end]

    def render_message_content(self, is_public: bool = False) -> str:
        return format_search_message(
            self.trades,
            page=self.page,
            per_page=self.per_page,
            filters_summary=self.filters_summary,
            sort_by=self.sort_by,
            sort_order=self.sort_order,
            is_public=is_public,
            include_text_table=False,
        )

    def render_table_file(self) -> discord.File:
        """Render the current page of trades into a crisp single-line-per-row PNG table image."""
        clean_filters = self.filters_summary.replace("`", "")
        subtitle = (
            f"Filters: {clean_filters}  •  Sort: {self.sort_by} {self.sort_order.upper()}  •  "
            f"Page {self.page + 1}/{self.total_pages} ({len(self.trades)} total)"
        )
        return render_trades_table_discord_file(
            self.current_page_slice(),
            filename="trades_table.png",
            title="蜈蚣bot • 历史订单查询 | Trade Search Results",
            subtitle=subtitle,
        )

    def _rebuild_components(self) -> None:
        self.clear_items()

        # Row 0: "🔎 View Full Trade Details" dropdown
        self.add_item(TradeDetailSelect(self.current_page_slice()))

        # Row 1: Multi-select Status filter dropdown
        if self.requery_callback is not None:
            self.add_item(StatusMultiSelect(self.active_statuses))

        # Row 2: Pagination & "Make it public" buttons
        prev_btn = discord.ui.Button(
            label="◀ Prev",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page <= 0),
            row=2,
        )
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        page_indicator = discord.ui.Button(
            label=f"Page {self.page + 1}/{self.total_pages}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=2,
        )
        self.add_item(page_indicator)

        next_btn = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page >= self.total_pages - 1),
            row=2,
        )
        next_btn.callback = self._on_next
        self.add_item(next_btn)

        make_public_btn = discord.ui.Button(
            label="Published to Channel" if self.is_published else "Make it public",
            style=discord.ButtonStyle.success,
            emoji="📢",
            disabled=self.is_published,
            row=2,
        )
        make_public_btn.callback = self._on_make_public
        self.add_item(make_public_btn)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if self.page > 0:
            self.page -= 1
        self._rebuild_components()
        await interaction.edit_original_response(
            content=self.render_message_content(is_public=False),
            attachments=[self.render_table_file()],
            view=self,
        )

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if self.page < self.total_pages - 1:
            self.page += 1
        self._rebuild_components()
        await interaction.edit_original_response(
            content=self.render_message_content(is_public=False),
            attachments=[self.render_table_file()],
            view=self,
        )

    async def on_status_filter_changed(
        self,
        interaction: discord.Interaction,
        selected_statuses: List[str],
    ) -> None:
        await interaction.response.defer()
        self.active_statuses = selected_statuses if selected_statuses else None
        if self.requery_callback is not None:
            new_trades, new_summary = await self.requery_callback(self.active_statuses)
            self.trades = new_trades
            self.filters_summary = new_summary
            self.page = 0
            self.is_published = False

        self._rebuild_components()
        await interaction.edit_original_response(
            content=self.render_message_content(is_public=False),
            attachments=[self.render_table_file()],
            view=self,
        )

    async def _on_make_public(self, interaction: discord.Interaction) -> None:
        """
        Publish the current search table image publicly to the channel:
        1. Acknowledge the button click immediately via `edit_message` so Discord's 3-second
           interaction timer never expires while rendering/uploading the PNG.
        2. Send the public table message via `interaction.followup.send(..., ephemeral=False)`.
        """
        self.is_published = True
        self._rebuild_components()
        await interaction.response.edit_message(
            content=self.render_message_content(is_public=False),
            view=self,
        )

        public_content = self.render_message_content(is_public=True)
        shared_by_line = f"\n*Shared publicly by <@{interaction.user.id}>*"
        if len(public_content) + len(shared_by_line) <= 2000:
            public_content += shared_by_line

        await interaction.followup.send(
            content=public_content,
            file=self.render_table_file(),
            ephemeral=False,
        )

