"""Discord message, Embed, and compact box-drawing Table formatters for 蜈蚣bot."""
from __future__ import annotations

import io
import os
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

import discord
from PIL import Image, ImageDraw, ImageFont

from bot.database import TradeRecord


STATUS_BADGE: Dict[str, str] = {
    "open": "🟢 open",
    "closed": "🔴 closed",
    "expired": "⚪ expired",
}

EMBED_COLOR_BY_STATUS: Dict[str, int] = {
    "open": 0x2ECC71,     # Emerald Green
    "closed": 0xE74C3C,   # Alizarin Red
    "expired": 0x95A5A6,  # Concrete Grey
}


def _char_display_width(ch: str) -> int:
    """Return terminal column width of a character (2 for East Asian Wide/Fullwidth, 1 otherwise)."""
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width(text: str) -> int:
    """Return total monospaced display width of a string (accounting for CJK characters)."""
    return sum(_char_display_width(c) for c in text)


def clean_single_line(text: Optional[str]) -> str:
    """Collapse newlines/tabs into single spaces for crisp single-row table rendering."""
    if not text:
        return "-"
    cleaned = " ".join(text.replace("\r", " ").replace("\n", " / ").split())
    return cleaned if cleaned else "-"


def truncate_to_width(text: Optional[str], max_width: int = 22) -> str:
    """
    Truncate `text` so its visual monospaced width never exceeds `max_width`,
    appending '...' when truncated (~22 characters for thesis and notes in `/search`).
    """
    single = clean_single_line(text)
    if display_width(single) <= max_width:
        return single

    ellipsis = "..."
    target_width = max(1, max_width - len(ellipsis))
    out: List[str] = []
    current_w = 0
    for ch in single:
        w = _char_display_width(ch)
        if current_w + w > target_width:
            break
        out.append(ch)
        current_w += w
    return "".join(out).rstrip() + ellipsis


def pad_to_width(text: str, width: int) -> str:
    """Right-pad `text` with spaces to match exact visual column `width`."""
    pad_len = max(0, width - display_width(text))
    return text + (" " * pad_len)


_FONT_CANDIDATES = (
    # macOS CJK + Latin fonts
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux CJK / DejaVu fonts (for cloud deployment)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # Windows CJK fonts
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


_FONT_CACHE: Dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cached = _FONT_CACHE.get(size)
    if cached is not None:
        return cached
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size=size)
                _FONT_CACHE[size] = font
                return font
            except OSError:
                continue
    default_font = ImageFont.load_default()
    _FONT_CACHE[size] = default_font
    return default_font


def _measure_text_px(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])
    except Exception:
        return len(text) * 10


def _truncate_text_px(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_px: int,
) -> str:
    if _measure_text_px(draw, text, font) <= max_px:
        return text
    ellipsis = "..."
    out: List[str] = []
    for ch in text:
        candidate = "".join(out) + ch + ellipsis
        if _measure_text_px(draw, candidate, font) > max_px:
            break
        out.append(ch)
    return "".join(out).rstrip() + ellipsis


def render_trades_table_image(
    trades: Sequence[TradeRecord],
    *,
    title: str = "蜈蚣bot • Trade Search Results",
    subtitle: str = "",
) -> bytes:
    """
    Render `trades` into a high-DPI dark-mode PNG table image where every row is strictly
    on a single line with zero wrapping, following exact columns:
    ID | ticker | (if option then <expiry> <strike> <strategy>, if stock then <long/short> <entry price>) | thesis | notes | status | 蜈蚣头
    """
    font_title = _load_font(22)
    font_sub = _load_font(15)
    font_header = _load_font(17)
    font_cell = _load_font(16)

    headers = [
        "ID",
        "ticker",
        "expiry / strike / strategy | dir / price",
        "thesis",
        "notes",
        "status",
        "蜈蚣头",
    ]

    # Max pixel width per column so the table stays sleek and balanced (~1480px wide)
    max_col_px = [85, 110, 340, 340, 310, 110, 160]
    min_col_px = [60, 80, 230, 180, 180, 90, 110]

    raw_rows: List[List[str]] = []
    for t in trades:
        if t.asset_type == "options":
            parts = [p for p in (t.expiry, t.strike, t.direction_or_strategy) if p]
            contract_col = clean_single_line(" ".join(parts))
        else:
            parts = [p for p in (t.direction_or_strategy, t.price) if p]
            contract_col = clean_single_line(" ".join(parts))

        raw_rows.append(
            [
                f"#{t.id}",
                t.ticker,
                contract_col,
                clean_single_line(t.thesis),
                clean_single_line(t.notes),
                t.status,
                f"@{t.initiator_name}",
            ]
        )

    # Scratch canvas for text pixel measurement
    scratch = Image.new("RGB", (10, 10))
    s_draw = ImageDraw.Draw(scratch)

    col_px: List[int] = []
    truncated_rows: List[List[str]] = [[] for _ in raw_rows]

    for col_idx, header_text in enumerate(headers):
        h_w = _measure_text_px(s_draw, header_text, font_header)
        best_w = max(min_col_px[col_idx], h_w)
        for row_idx, row in enumerate(raw_rows):
            cell_str = _truncate_text_px(s_draw, row[col_idx], font_cell, max_col_px[col_idx])
            truncated_rows[row_idx].append(cell_str)
            c_w = _measure_text_px(s_draw, cell_str, font_cell)
            best_w = max(best_w, c_w)
        col_px.append(min(max_col_px[col_idx], best_w) + 28)  # 14px horizontal padding each side

    outer_pad_x = 24
    outer_pad_y = 20
    banner_h = 68 if subtitle else 46
    header_h = 44
    row_h = 42
    num_rows = max(1, len(truncated_rows))

    table_w = sum(col_px)
    img_w = table_w + outer_pad_x * 2
    img_h = outer_pad_y * 2 + banner_h + header_h + (num_rows * row_h)

    # Colors (Discord Dark Theme palette)
    bg_canvas = (30, 31, 34)       # #1E1F22
    bg_header = (43, 45, 49)       # #2B2D31
    bg_row_even = (49, 51, 56)     # #313338
    bg_row_odd = (43, 45, 49)      # #2B2D31
    border_col = (63, 65, 71)      # #3F4147
    text_white = (242, 243, 245)   # #F2F3F5
    text_muted = (181, 186, 193)   # #B5BAC1
    text_accent = (88, 101, 242)   # Discord Blurple
    ticker_gold = (240, 178, 50)   # Crisp Amber/Gold for tickers

    status_pill_colors: Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = {
        "open": ((35, 90, 58), (87, 242, 135)),       # Dark green pill / bright green text
        "closed": ((95, 40, 45), (237, 66, 69)),       # Dark red pill / bright red text
        "expired": ((65, 68, 75), (181, 186, 193)),    # Grey pill / muted text
    }

    img = Image.new("RGB", (img_w, img_h), color=bg_canvas)
    draw = ImageDraw.Draw(img)

    # Title & subtitle banner
    draw.text((outer_pad_x, outer_pad_y), title, font=font_title, fill=text_white)
    if subtitle:
        draw.text((outer_pad_x, outer_pad_y + 32), subtitle, font=font_sub, fill=text_muted)

    table_top = outer_pad_y + banner_h

    # Header background
    draw.rectangle(
        [outer_pad_x, table_top, outer_pad_x + table_w, table_top + header_h],
        fill=bg_header,
        outline=border_col,
        width=1,
    )

    # Draw column headers
    cur_x = outer_pad_x
    for col_idx, header_text in enumerate(headers):
        draw.text(
            (cur_x + 14, table_top + 11),
            header_text,
            font=font_header,
            fill=text_muted,
        )
        cur_x += col_px[col_idx]
        if col_idx < len(headers) - 1:
            draw.line(
                [(cur_x, table_top), (cur_x, table_top + header_h)],
                fill=border_col,
                width=1,
            )

    # Draw rows
    if not truncated_rows:
        row_y = table_top + header_h
        draw.rectangle(
            [outer_pad_x, row_y, outer_pad_x + table_w, row_y + row_h],
            fill=bg_row_even,
            outline=border_col,
            width=1,
        )
        draw.text(
            (outer_pad_x + 18, row_y + 11),
            "No matching trades found.",
            font=font_cell,
            fill=text_muted,
        )
    else:
        for r_idx, row in enumerate(truncated_rows):
            row_y = table_top + header_h + (r_idx * row_h)
            row_bg = bg_row_even if r_idx % 2 == 0 else bg_row_odd
            draw.rectangle(
                [outer_pad_x, row_y, outer_pad_x + table_w, row_y + row_h],
                fill=row_bg,
                outline=border_col,
                width=1,
            )

            cur_x = outer_pad_x
            for col_idx, cell_text in enumerate(row):
                cell_w = col_px[col_idx]

                if col_idx == 0:
                    # ID column
                    draw.text((cur_x + 14, row_y + 11), cell_text, font=font_cell, fill=text_accent)
                elif col_idx == 1:
                    # Ticker column
                    draw.text((cur_x + 14, row_y + 11), cell_text, font=font_header, fill=ticker_gold)
                elif col_idx == 5:
                    # Status pill badge
                    pill_bg, pill_fg = status_pill_colors.get(
                        cell_text.lower(),
                        ((65, 68, 75), text_white),
                    )
                    tw = _measure_text_px(draw, cell_text, font_cell)
                    pill_w = tw + 20
                    pill_x1 = cur_x + 12
                    pill_y1 = row_y + 8
                    draw.rounded_rectangle(
                        [pill_x1, pill_y1, pill_x1 + pill_w, pill_y1 + 26],
                        radius=6,
                        fill=pill_bg,
                    )
                    draw.text((pill_x1 + 10, row_y + 10), cell_text, font=font_cell, fill=pill_fg)
                else:
                    draw.text((cur_x + 14, row_y + 11), cell_text, font=font_cell, fill=text_white)

                cur_x += cell_w
                if col_idx < len(headers) - 1:
                    draw.line(
                        [(cur_x, row_y), (cur_x, row_y + row_h)],
                        fill=border_col,
                        width=1,
                    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_trades_table_discord_file(
    trades: Sequence[TradeRecord],
    *,
    filename: str = "trades_table.png",
    title: str = "蜈蚣bot • Trade Search Results",
    subtitle: str = "",
) -> discord.File:
    """Create a fresh `discord.File` containing the rendered PNG table image."""
    png_bytes = render_trades_table_image(trades, title=title, subtitle=subtitle)
    return discord.File(io.BytesIO(png_bytes), filename=filename)


def format_trades_table(
    trades: Sequence[TradeRecord],
    *,
    thesis_max_width: int = 16,
    notes_max_width: int = 16,
) -> str:
    """
    Render a list of `TradeRecord` items into a single-grid table with exact columns:
    ID | ticker | (if option then <expiry> <strike> <strategy>, if stock then <long/short> <entry price>) | thesis | notes | status | 蜈蚣头
    """
    if not trades:
        return "```text\nNo matching trades found.\n```"

    headers = [
        "ID",
        "ticker",
        "contract / position",
        "thesis",
        "notes",
        "status",
        "蜈蚣头",
    ]

    grid_rows: List[List[str]] = []

    for t in trades:
        if t.asset_type == "options":
            parts = [p for p in (t.expiry, t.strike, t.direction_or_strategy) if p]
            contract_col = truncate_to_width(" ".join(parts), 26)
        else:
            parts = [p for p in (t.direction_or_strategy, t.price) if p]
            contract_col = truncate_to_width(" ".join(parts), 26)

        grid_rows.append(
            [
                f"#{t.id}",
                t.ticker,
                contract_col,
                truncate_to_width(t.thesis, thesis_max_width),
                truncate_to_width(t.notes, notes_max_width),
                t.status,
                truncate_to_width(f"@{t.initiator_name}", 10),
            ]
        )

    col_widths: List[int] = []
    for col_idx, header in enumerate(headers):
        max_w = display_width(header)
        for row in grid_rows:
            max_w = max(max_w, display_width(row[col_idx]))
        col_widths.append(max_w)

    top_border = "┌─" + "─┬─".join("─" * w for w in col_widths) + "─┐"
    header_line = "│ " + " │ ".join(pad_to_width(h, col_widths[i]) for i, h in enumerate(headers)) + " │"
    mid_border = "├─" + "─┼─".join("─" * w for w in col_widths) + "─┤"
    body_lines = [
        "│ " + " │ ".join(pad_to_width(row[i], col_widths[i]) for i in range(len(headers))) + " │"
        for row in grid_rows
    ]
    bot_border = "└─" + "─┴─".join("─" * w for w in col_widths) + "─┘"

    combined = "\n".join(
        [
            top_border,
            header_line,
            mid_border,
            *body_lines,
            bot_border,
        ]
    )
    return f"```text\n{combined}\n```"


def build_new_trade_embed(trade: TradeRecord) -> discord.Embed:
    """Build the public Discord Embed card when `/open-options` or `/open-stock` is executed."""
    is_options = trade.asset_type == "options"
    title_prefix = "📣 新期权开仓 | New Options Trade" if is_options else "📣 新正股开仓 | New Stock Trade"
    color = EMBED_COLOR_BY_STATUS.get(trade.status, 0x2ECC71)

    embed = discord.Embed(
        title=f"{title_prefix} #{trade.id}",
        description=f"**蜈蚣头:** <@{trade.initiator_id}> (`@{trade.initiator_name}`)",
        color=color,
    )

    embed.add_field(name="Ticker", value=f"`{trade.ticker}`", inline=True)
    embed.add_field(name="Asset Type", value=f"`{trade.asset_type}`", inline=True)
    embed.add_field(
        name="Status",
        value=f"`{STATUS_BADGE.get(trade.status, trade.status)}`",
        inline=True,
    )

    if is_options:
        embed.add_field(name="Strike", value=f"`{trade.strike or '-'}`", inline=True)
        embed.add_field(name="Strategy", value=f"`{trade.direction_or_strategy}`", inline=True)
        embed.add_field(name="Expiry (mm/dd/yyyy)", value=f"`{trade.expiry}`", inline=True)
        embed.add_field(name="Price", value=f"`{trade.price or 'N/A'}`", inline=True)
    else:
        direction_icon = "📈 long" if trade.direction_or_strategy == "long" else "📉 short"
        embed.add_field(name="Direction", value=f"`{direction_icon}`", inline=True)
        embed.add_field(name="Entry Price", value=f"`{trade.price or 'N/A'}`", inline=True)

    embed.add_field(name="Thesis", value=trade.thesis[:1024], inline=False)
    if trade.notes:
        embed.add_field(name="Notes", value=trade.notes[:1024], inline=False)

    embed.set_footer(
        text=f"蜈蚣bot • Trade #{trade.id} • Created {trade.created_at}"
    )
    return embed


def build_updated_trade_embed(trade: TradeRecord) -> discord.Embed:
    """Build the public Discord Embed card when `/update` is executed by the 蜈蚣头."""
    status_label = STATUS_BADGE.get(trade.status, trade.status)
    color = EMBED_COLOR_BY_STATUS.get(trade.status, 0x3498DB)

    embed = discord.Embed(
        title=f"🔄 订单更新 | Trade #{trade.id} Updated ({status_label})",
        description=f"**蜈蚣头:** <@{trade.initiator_id}> (`@{trade.initiator_name}`)",
        color=color,
    )

    embed.add_field(name="Ticker", value=f"`{trade.ticker}`", inline=True)
    embed.add_field(name="Asset Type", value=f"`{trade.asset_type}`", inline=True)
    embed.add_field(name="Status", value=f"`{status_label}`", inline=True)

    if trade.asset_type == "options":
        embed.add_field(name="Strike", value=f"`{trade.strike or '-'}`", inline=True)
        embed.add_field(name="Strategy", value=f"`{trade.direction_or_strategy}`", inline=True)
        embed.add_field(name="Expiry", value=f"`{trade.expiry}`", inline=True)
        if trade.price:
            embed.add_field(name="Price", value=f"`{trade.price}`", inline=True)
    else:
        embed.add_field(name="Direction", value=f"`{trade.direction_or_strategy}`", inline=True)
        if trade.price:
            embed.add_field(name="Entry Price", value=f"`{trade.price}`", inline=True)

    embed.add_field(name="Thesis", value=trade.thesis[:1024], inline=False)
    embed.add_field(
        name="Notes",
        value=(trade.notes or "-")[:1024],
        inline=False,
    )

    if trade.message_url:
        embed.add_field(
            name="🔗 Original Order Post",
            value=f"[Jump to Original Message]({trade.message_url})",
            inline=False,
        )

    footer = f"蜈蚣bot • Updated {trade.updated_at}"
    if trade.closed_at:
        footer += f" • Closed {trade.closed_at}"
    embed.set_footer(text=footer)
    return embed


def build_trade_detail_embed(trade: TradeRecord) -> discord.Embed:
    """
    Build the full un-truncated detail Embed when a user selects a trade from the
    '🔎 View Full Trade Details' dropdown below the `/search` table.
    """
    status_label = STATUS_BADGE.get(trade.status, trade.status)
    color = EMBED_COLOR_BY_STATUS.get(trade.status, 0x3498DB)

    embed = discord.Embed(
        title=f"🔎 订单详情 | Full Trade Details #{trade.id} ({trade.ticker})",
        description=(
            f"**蜈蚣头:** <@{trade.initiator_id}> (`@{trade.initiator_name}`)\n"
            f"**Created By:** <@{trade.created_by_id}>"
        ),
        color=color,
    )

    embed.add_field(name="Asset Type", value=f"`{trade.asset_type}`", inline=True)
    embed.add_field(name="Status", value=f"`{status_label}`", inline=True)
    embed.add_field(name="Created Date (NY)", value=f"`{trade.created_date}`", inline=True)

    if trade.asset_type == "options":
        embed.add_field(name="Strike", value=f"`{trade.strike or '-'}`", inline=True)
        embed.add_field(name="Strategy", value=f"`{trade.direction_or_strategy}`", inline=True)
        embed.add_field(name="Expiry", value=f"`{trade.expiry}`", inline=True)
        embed.add_field(name="Price", value=f"`{trade.price or 'N/A'}`", inline=True)
    else:
        embed.add_field(name="Direction", value=f"`{trade.direction_or_strategy}`", inline=True)
        embed.add_field(name="Entry Price", value=f"`{trade.price or 'N/A'}`", inline=True)

    # Full un-truncated thesis and notes (up to Discord field limit of 1024 chars)
    embed.add_field(name="Thesis", value=trade.thesis[:1024], inline=False)
    embed.add_field(
        name="Notes",
        value=(trade.notes or "*(No notes recorded)*")[:1024],
        inline=False,
    )

    timestamps_text = (
        f"• **Created (NY):** `{trade.created_at}`\n"
        f"• **Updated (NY):** `{trade.updated_at}`"
    )
    if trade.closed_at:
        timestamps_text += f"\n• **Closed (NY):** `{trade.closed_at}`"
    embed.add_field(name="🕒 Timestamps", value=timestamps_text, inline=False)

    if trade.message_url:
        embed.add_field(
            name="🔗 Original Chat Message",
            value=f"[Click to Jump to Original Discord Message]({trade.message_url})",
            inline=False,
        )

    embed.set_footer(text=f"蜈蚣bot • Trade #{trade.id}")
    return embed


def format_search_message(
    trades: Sequence[TradeRecord],
    *,
    page: int,
    per_page: int,
    filters_summary: str,
    sort_by: str,
    sort_order: str,
    is_public: bool = False,
    include_text_table: bool = False,
) -> str:
    """
    Format a paginated `/search` message header (and optional ASCII table fallback).
    By default (`include_text_table=False`), the table is attached as a crisp PNG image
    so every row stays on one single line without wrapping!
    """
    total = len(trades)
    total_pages = max(1, (total + per_page - 1) // per_page)
    safe_page = max(0, min(page, total_pages - 1))
    start_idx = safe_page * per_page
    end_idx = min(total, start_idx + per_page)
    page_slice = trades[start_idx:end_idx]

    visibility_badge = "📢 **公开历史订单查询 | Public Trade Search Results**" if is_public else "🔒 **历史订单查询 | Private Trade Search Results**"
    range_str = f"{start_idx + 1}–{end_idx} of {total}" if total > 0 else "0 results"

    header = (
        f"{visibility_badge}\n"
        f"**Filters:** {filters_summary} | **Sort:** `{sort_by} {sort_order.upper()}` | "
        f"**Page:** `{safe_page + 1}/{total_pages}` ({range_str})"
    )

    if not include_text_table:
        return header[:1998]

    table_block = "\n" + format_trades_table(page_slice, thesis_max_width=16, notes_max_width=16)
    full_msg = header + table_block
    return full_msg[:1998]


def format_eod_summary_message(
    *,
    ny_date_str: str,
    initiated_today: Sequence[TradeRecord],
    expiring_soon: Sequence[TradeRecord],
) -> str:
    """
    Format the End-of-Day (EOD) daily summary message in New York time:
      1. All trades initiated today (`created_date == ny_date_str`) in a table.
      2. '⚠️ Expiring Tomorrow / This Friday' section reminding 蜈蚣头 of open options expiring within 1-2 trading days.
    """
    open_count = sum(1 for t in initiated_today if t.status == "open")
    closed_count = sum(1 for t in initiated_today if t.status == "closed")
    expired_count = sum(1 for t in initiated_today if t.status == "expired")

    lines: List[str] = [
        f"🌙 **蜈蚣bot 今日日报 | End-of-Day Trade Summary ({ny_date_str} NY Time)**",
        (
            f"今日共发起 **{len(initiated_today)}** 笔交易 "
            f"(`open: {open_count}` | `closed: {closed_count}` | `expired: {expired_count}`):"
        ),
    ]

    if initiated_today:
        lines.append(format_trades_table(initiated_today[:10], thesis_max_width=22, notes_max_width=22))
        if len(initiated_today) > 10:
            lines.append(f"*...and {len(initiated_today) - 10} more trades initiated today (use `/search created_date:{ny_date_str}` to view all).*")
    else:
        lines.append("```text\n今日暂无新发起的交易 (No new trades initiated today).\n```")

    # Section 2: ⚠️ Expiring Tomorrow / This Friday reminder
    lines.append("\n⚠️ **Expiring Tomorrow / This Friday (即将到期期权提醒):**")
    if expiring_soon:
        for t in expiring_soon[:15]:
            strike_info = f" `{t.strike}`" if t.strike else ""
            lines.append(
                f"• **`#{t.id}`** `{t.ticker}`{strike_info} ({t.direction_or_strategy}) — "
                f"**Exp `{t.expiry}`** — 蜈蚣头: <@{t.initiator_id}> *(请及时平仓/展期或使用 `/update` 更新状态!)*"
            )
        if len(expiring_soon) > 15:
            lines.append(f"*...and {len(expiring_soon) - 15} more expiring options.*")
    else:
        lines.append("• 未来 1–2 个交易日 / 本周五前暂无未平仓 (`open`) 的到期期权。")

    full_text = "\n".join(lines)
    return full_text[:1998]
