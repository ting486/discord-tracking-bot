"""Unit tests for New York timezone formatting and strict mm/dd/yyyy validation."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from bot.validators import (
    NY_TZ,
    ValidationError,
    format_ny_date,
    format_ny_datetime,
    parse_status_filter,
    upcoming_expiry_window_iso,
    validate_asset_type,
    validate_mmddyyyy,
    validate_status,
    validate_stock_direction,
    validate_ticker,
)


class TestValidators(unittest.TestCase):
    def test_validate_mmddyyyy_valid(self) -> None:
        norm, iso = validate_mmddyyyy("12/18/2026")
        self.assertEqual(norm, "12/18/2026")
        self.assertEqual(iso, "2026-12-18")

        norm2, iso2 = validate_mmddyyyy("01/05/2027")
        self.assertEqual(norm2, "01/05/2027")
        self.assertEqual(iso2, "2027-01-05")

    def test_validate_mmddyyyy_rejects_invalid_formats_and_dates(self) -> None:
        invalid_inputs = [
            "",
            "12/18/26",        # 2-digit year must be rejected
            "2026-12-18",      # ISO format must be rejected
            "1/18/2026",       # 1-digit month must be rejected
            "12/5/2026",       # 1-digit day must be rejected
            "13/01/2026",      # month 13
            "02/30/2026",      # non-existent calendar date
            "02/29/2026",      # 2026 is not a leap year
            "04/31/2026",      # April has 30 days
        ]
        for bad in invalid_inputs:
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    validate_mmddyyyy(bad)

    def test_ny_timezone_conversion(self) -> None:
        # 2026-11-13 02:30:00 UTC is still 2026-11-12 21:30:00 EST (New York day = 11/12/2026)
        utc_dt = datetime(2026, 11, 13, 2, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(format_ny_date(utc_dt), "11/12/2026")
        self.assertEqual(format_ny_datetime(utc_dt), "2026-11-12 21:30:00 EST")

        # Summer EDT check: 2026-07-15 18:00:00 UTC -> 2026-07-15 14:00:00 EDT
        summer_utc = datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(format_ny_datetime(summer_utc), "2026-07-15 14:00:00 EDT")

    def test_enums_and_status_multi_select(self) -> None:
        self.assertEqual(validate_asset_type("OPTIONS"), "options")
        self.assertEqual(validate_asset_type("stock"), "stock")
        with self.assertRaises(ValidationError):
            validate_asset_type("crypto")

        self.assertEqual(validate_status("OPEN"), "open")
        self.assertEqual(validate_status("closed"), "closed")
        self.assertEqual(validate_status("expired"), "expired")
        with self.assertRaises(ValidationError):
            validate_status("pending")

        self.assertEqual(validate_stock_direction("LONG"), "long")
        self.assertEqual(validate_stock_direction("short"), "short")
        with self.assertRaises(ValidationError):
            validate_stock_direction("hold")

        self.assertEqual(parse_status_filter("open,closed"), ["open", "closed"])
        self.assertEqual(parse_status_filter(["OPEN", "expired", "open"]), ["open", "expired"])
        self.assertIsNone(parse_status_filter(""))
        self.assertIsNone(parse_status_filter("all"))

    def test_validate_ticker(self) -> None:
        self.assertEqual(validate_ticker(" nvda "), "NVDA")
        self.assertEqual(validate_ticker("brk.b"), "BRK.B")
        with self.assertRaises(ValidationError):
            validate_ticker("")

    def test_upcoming_expiry_window_iso(self) -> None:
        # Tuesday Nov 10, 2026 -> window should be Wed Nov 11 through Fri Nov 13
        tuesday = date(2026, 11, 10)
        start_iso, end_iso = upcoming_expiry_window_iso(tuesday)
        self.assertEqual(start_iso, "2026-11-11")
        self.assertEqual(end_iso, "2026-11-13")


if __name__ == "__main__":
    unittest.main()

