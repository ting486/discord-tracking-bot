"""Unit tests for ASCII/Unicode table formatting, ~22-char truncation, detail embeds, and EOD summary."""
from __future__ import annotations

import unittest

from bot.database import TradeRecord
from bot.formatters import (
    build_trade_detail_embed,
    display_width,
    format_eod_summary_message,
    format_search_message,
    format_trades_table,
    render_trades_table_image,
    truncate_to_width,
)


def _sample_options_trade() -> TradeRecord:
    return TradeRecord(
        id=101,
        guild_id=1001,
        channel_id=2001,
        message_url="https://discord.com/channels/1001/2001/3001",
        asset_type="options",
        initiator_id=111,
        initiator_name="Alex",
        created_by_id=222,
        ticker="NVDA",
        strike="145/155C",
        price="$4.20 debit",
        direction_or_strategy="Bull Call Spread",
        expiry="12/18/2026",
        expiry_iso="2026-12-18",
        thesis="Blackwell ramp + pre-earnings institutional call buying",
        notes="Scale out half at +50%\n[11/12/2026 14:30 EST] Holding strong",
        status="open",
        created_at="2026-11-12 10:15:00 EST",
        created_date="11/12/2026",
        updated_at="2026-11-12 14:30:00 EST",
        closed_at=None,
    )


class TestFormatters(unittest.TestCase):
    def test_truncate_to_22_chars(self) -> None:
        long_text = "Blackwell ramp + pre-earnings institutional call buying"
        truncated = truncate_to_width(long_text, 22)
        self.assertTrue(truncated.endswith("..."))
        self.assertLessEqual(display_width(truncated), 22)

    def test_format_trades_table_alignment_and_truncation(self) -> None:
        trade = _sample_options_trade()
        table = format_trades_table([trade], thesis_max_width=22, notes_max_width=22)
        self.assertTrue(table.startswith("```text\n"))
        self.assertIn("#101", table)
        self.assertIn("NVDA", table)
        self.assertIn("145/155C", table)
        self.assertIn("12/18/2026", table)
        self.assertIn("Blackwell ramp + pr...", table)

        # Verify all box-drawing grid lines have identical visual display width
        inner_lines = table.strip("`\n").splitlines()[1:]  # skip 'text'
        widths = {display_width(line) for line in inner_lines}
        self.assertEqual(len(widths), 1)
        # Verify column order: ID | ticker | <expiry> <strike> <strategy> | thesis | notes | status | 蜈蚣头
        self.assertIn("12/18/2026 145/155C Bul...", table)

    def test_full_trade_detail_embed_untruncated(self) -> None:
        trade = _sample_options_trade()
        embed = build_trade_detail_embed(trade)
        field_values = "\n".join(f.value for f in embed.fields)
        # Full thesis & notes must NOT be truncated to 22 chars in the detail card
        self.assertIn("Blackwell ramp + pre-earnings institutional call buying", field_values)
        self.assertIn("[11/12/2026 14:30 EST] Holding strong", field_values)
        self.assertIn("https://discord.com/channels/1001/2001/3001", field_values)

    def test_search_and_eod_messages_fit_discord_limit(self) -> None:
        trades = [_sample_options_trade() for _ in range(8)]
        search_msg = format_search_message(
            trades,
            page=0,
            per_page=8,
            filters_summary="`ticker=NVDA`",
            sort_by="created_at",
            sort_order="DESC",
            is_public=False,
        )
        self.assertLessEqual(len(search_msg), 2000)

        eod_msg = format_eod_summary_message(
            ny_date_str="11/12/2026",
            initiated_today=trades[:3],
            expiring_soon=trades[:2],
        )
        self.assertLessEqual(len(eod_msg), 2000)
        self.assertIn("Expiring Tomorrow / This Friday", eod_msg)
        self.assertIn("<@111>", eod_msg)

    def test_render_trades_table_image_png(self) -> None:
        trade = _sample_options_trade()
        png_bytes = render_trades_table_image(
            [trade],
            title="蜈蚣bot • Trade Search Results",
            subtitle="Filters: ticker=NVDA",
        )
        # Verify valid PNG signature (magic bytes \x89PNG\r\n\x1a\n)
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png_bytes), 1000)


if __name__ == "__main__":
    unittest.main()

