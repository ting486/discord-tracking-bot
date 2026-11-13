"""Async database tests for 蜈蚣bot covering:
- /open-options (with strike, optional price, strict mm/dd/yyyy expiry validation, lowercase 'options' & 'open')
- /open-stock (with long/short, optional entry_price, lowercase 'stock' & 'open')
- /update (only 蜈蚣头 can update their own orders, rejects expired/closed orders, updates updated_at in NY format)
- Autocomplete helper for caller's open & unexpired trades
- Automatic expiration based on New York date
- /search filtering (蜈蚣头, ticker, expiry, thesis, notes, multi-select status, created_date) and sorting
- End-of-Day (EOD) daily trades query + expiring-soon reminder query
"""
from __future__ import annotations

import unittest
from datetime import datetime

from bot.database import (
    InvalidTradeStateError,
    PermissionDeniedError,
    TradeDatabase,
    TradeNotFoundError,
)
from bot.validators import NY_TZ, ValidationError


class TestTradeDatabase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.db = TradeDatabase("sqlite+aiosqlite:///:memory:")
        await self.db.init_db()
        # Reference NY time: Thursday, Nov 12, 2026 at 10:30:00 EST
        self.base_ny = datetime(2026, 11, 12, 10, 30, 0, tzinfo=NY_TZ)

    async def asyncTearDown(self) -> None:
        await self.db.close()

    async def test_open_options_and_open_stock_fields_and_lowercase_enums(self) -> None:
        opt = await self.db.create_options_trade(
            guild_id=1001,
            channel_id=2001,
            initiator_id=111,
            initiator_name="Alex",
            created_by_id=222,
            ticker="nvda",
            expiry="12/18/2026",
            strike="145/155C",
            strategy="Bull Call Spread",
            thesis="Blackwell ramp and earnings momentum",
            price="$4.20 debit",
            notes="Scale out half at +50%",
            now_dt=self.base_ny,
        )
        self.assertEqual(opt.asset_type, "options")
        self.assertEqual(opt.status, "open")
        self.assertEqual(opt.ticker, "NVDA")
        self.assertEqual(opt.strike, "145/155C")
        self.assertEqual(opt.price, "$4.20 debit")
        self.assertEqual(opt.expiry, "12/18/2026")
        self.assertEqual(opt.created_by_id, 222)
        self.assertEqual(opt.created_date, "11/12/2026")
        self.assertEqual(opt.created_at, "2026-11-12 10:30:00 EST")
        self.assertEqual(opt.updated_at, opt.created_at)

        stk = await self.db.create_stock_trade(
            guild_id=1001,
            channel_id=2001,
            initiator_id=333,
            initiator_name="Sarah",
            created_by_id=333,
            ticker="tsla",
            long_short="SHORT",
            thesis="Overextended post-delivery rally",
            entry_price="$348.50",
            notes="Stop above $360",
            now_dt=self.base_ny,
        )
        self.assertEqual(stk.asset_type, "stock")
        self.assertEqual(stk.status, "open")
        self.assertEqual(stk.direction_or_strategy, "short")
        self.assertEqual(stk.price, "$348.50")
        self.assertIsNone(stk.expiry)
        self.assertIsNone(stk.strike)

    async def test_open_options_rejects_invalid_expiry_format(self) -> None:
        with self.assertRaises(ValidationError):
            await self.db.create_options_trade(
                guild_id=1001,
                channel_id=2001,
                initiator_id=111,
                initiator_name="Alex",
                created_by_id=111,
                ticker="SPY",
                expiry="12/18/26",  # Must be mm/dd/yyyy
                strike="600C",
                strategy="Long Call",
                thesis="Year-end rally",
                now_dt=self.base_ny,
            )

    async def test_update_restricted_to_wugongtou_and_unexpired_orders(self) -> None:
        trade = await self.db.create_options_trade(
            guild_id=1001,
            channel_id=2001,
            initiator_id=111,
            initiator_name="Alex",
            created_by_id=222,
            ticker="NVDA",
            expiry="12/18/2026",
            strike="150C",
            strategy="Long Call",
            thesis="Earnings run-up",
            notes="Initial note",
            now_dt=self.base_ny,
        )

        # Non-蜈蚣头 user (222) cannot update trade initiated by 111
        with self.assertRaises(PermissionDeniedError):
            await self.db.update_trade(
                trade_id=trade.id,
                actor_user_id=222,
                guild_id=1001,
                additional_comments="Trying to hijack update",
                now_dt=self.base_ny,
            )

        # 蜈蚣头 (111) updates comments and closes trade
        later_ny = datetime(2026, 11, 12, 14, 45, 0, tzinfo=NY_TZ)
        updated = await self.db.update_trade(
            trade_id=trade.id,
            actor_user_id=111,
            guild_id=1001,
            additional_comments="Closed at $6.80 before bell",
            close_trade=True,
            now_dt=later_ny,
        )
        self.assertEqual(updated.status, "closed")
        self.assertEqual(updated.updated_at, "2026-11-12 14:45:00 EST")
        self.assertEqual(updated.closed_at, "2026-11-12 14:45:00 EST")
        self.assertIn("Initial note", updated.notes or "")
        self.assertIn("[11/12/2026 14:45 EST] Closed at $6.80 before bell", updated.notes or "")

        # Once closed, even 蜈蚣头 cannot update it again
        with self.assertRaises(InvalidTradeStateError):
            await self.db.update_trade(
                trade_id=trade.id,
                actor_user_id=111,
                guild_id=1001,
                additional_comments="Another update",
                now_dt=later_ny,
            )

    async def test_auto_expiration_and_autocomplete_open_unexpired(self) -> None:
        # Trade 1: expires 11/13/2026 (tomorrow relative to base_ny 11/12/2026)
        t1 = await self.db.create_options_trade(
            guild_id=1001,
            channel_id=2001,
            initiator_id=111,
            initiator_name="Alex",
            created_by_id=111,
            ticker="AMD",
            expiry="11/13/2026",
            strike="160C",
            strategy="Long Call",
            thesis="Chip sympathy play",
            now_dt=self.base_ny,
        )
        # Trade 2: expires 12/18/2026
        t2 = await self.db.create_options_trade(
            guild_id=1001,
            channel_id=2001,
            initiator_id=111,
            initiator_name="Alex",
            created_by_id=111,
            ticker="NVDA",
            expiry="12/18/2026",
            strike="150C",
            strategy="Long Call",
            thesis="AI leader",
            now_dt=self.base_ny,
        )

        # On 11/12/2026, both are open and show up in autocomplete
        open_list = await self.db.get_open_unexpired_trades_for_user(
            initiator_id=111,
            guild_id=1001,
            now_dt=self.base_ny,
        )
        self.assertEqual([t.id for t in open_list], [t2.id, t1.id])
        self.assertIn("NVDA", open_list[0].autocomplete_label())

        # On 11/13/2026 at 11:00 AM EST (during market hours), Trade 1 (exp 11/13/2026) is STILL open
        nov13_morning_ny = datetime(2026, 11, 13, 11, 0, 0, tzinfo=NY_TZ)
        open_morning = await self.db.get_open_unexpired_trades_for_user(
            initiator_id=111,
            guild_id=1001,
            now_dt=nov13_morning_ny,
        )
        self.assertEqual([t.id for t in open_morning], [t2.id, t1.id])

        # On 11/13/2026 at 16:15 EST (EOD / after 4:00 PM market close), Trade 1 automatically expires!
        nov13_eod_ny = datetime(2026, 11, 13, 16, 15, 0, tzinfo=NY_TZ)
        open_eod = await self.db.get_open_unexpired_trades_for_user(
            initiator_id=111,
            guild_id=1001,
            now_dt=nov13_eod_ny,
        )
        self.assertEqual([t.id for t in open_eod], [t2.id])

        # Attempting to /update Trade 1 at EOD on 11/13/2026 fails because it is now expired
        with self.assertRaises(InvalidTradeStateError):
            await self.db.update_trade(
                trade_id=t1.id,
                actor_user_id=111,
                guild_id=1001,
                additional_comments="Late update after market close",
                now_dt=nov13_eod_ny,
            )

    async def test_search_filters_and_sorting_and_eod_queries(self) -> None:
        t1 = await self.db.create_options_trade(
            guild_id=1001,
            channel_id=2001,
            initiator_id=111,
            initiator_name="Alex",
            created_by_id=111,
            ticker="NVDA",
            expiry="11/13/2026",  # Expires Friday (tomorrow from Thu Nov 12)
            strike="145C",
            strategy="Long Call",
            thesis="Blackwell demand",
            notes="Watch $150 level",
            now_dt=self.base_ny,
        )
        t2 = await self.db.create_stock_trade(
            guild_id=1001,
            channel_id=2001,
            initiator_id=222,
            initiator_name="Bob",
            created_by_id=222,
            ticker="AAPL",
            long_short="long",
            thesis="Services growth",
            entry_price="$225",
            notes="Core position",
            now_dt=self.base_ny,
        )
        await self.db.update_trade(
            trade_id=t2.id,
            actor_user_id=222,
            guild_id=1001,
            close_trade=True,
            additional_comments="Took profit at $232",
            now_dt=self.base_ny,
        )

        # Multi-select status filter: ["open", "closed"]
        both = await self.db.search_trades(
            guild_id=1001,
            statuses=["open", "closed"],
            sort_by="ticker",
            sort_order="ASC",
            now_dt=self.base_ny,
        )
        self.assertEqual([r.ticker for r in both], ["AAPL", "NVDA"])

        # Filter by thesis keyword
        by_thesis = await self.db.search_trades(
            guild_id=1001,
            thesis_keyword="blackwell",
            now_dt=self.base_ny,
        )
        self.assertEqual(len(by_thesis), 1)
        self.assertEqual(by_thesis[0].id, t1.id)

        # Filter by notes keyword
        by_notes = await self.db.search_trades(
            guild_id=1001,
            notes_keyword="profit",
            now_dt=self.base_ny,
        )
        self.assertEqual(len(by_notes), 1)
        self.assertEqual(by_notes[0].id, t2.id)

        # EOD daily initiated trades (11/12/2026)
        eod_today = await self.db.get_daily_initiated_trades(
            guild_id=1001,
            now_dt=self.base_ny,
        )
        self.assertEqual(len(eod_today), 2)

        # EOD expiring soon options (t1 expires 11/13/2026, which is tomorrow / Friday)
        expiring_soon = await self.db.get_expiring_soon_options(
            guild_id=1001,
            now_dt=self.base_ny,
        )
        self.assertEqual([r.id for r in expiring_soon], [t1.id])


if __name__ == "__main__":
    unittest.main()

