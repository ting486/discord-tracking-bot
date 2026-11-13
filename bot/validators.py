"""New York timezone helpers and strict mm/dd/yyyy validation for 蜈蚣bot."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    from backports.zoneinfo import ZoneInfo  # type: ignore


NY_TZ = ZoneInfo("America/New_York")

# Strict 2-digit month, 2-digit day, 4-digit year: mm/dd/yyyy
MMDDYYYY_REGEX = re.compile(r"^(0[1-9]|1[0-2])/(0[1-9]|[12][0-9]|3[01])/(\d{4})$")

VALID_ASSET_TYPES = ("options", "stock")
VALID_STATUSES = ("open", "closed", "expired")
VALID_DIRECTIONS = ("long", "short")
VALID_SORT_FIELDS = (
    "created_at",
    "蜈蚣头",
    "initiator",
    "ticker",
    "expiry",
    "status",
    "thesis",
    "notes",
)


class ValidationError(ValueError):
    """Raised when user-supplied trade fields fail validation."""


def now_ny() -> datetime:
    """Return the current timezone-aware datetime in America/New_York."""
    return datetime.now(NY_TZ)


def format_ny_datetime(dt: Optional[datetime] = None) -> str:
    """
    Format a datetime in New York timezone consistently for created_at, updated_at, and closed_at.
    Format: 'YYYY-MM-DD HH:MM:SS America/New_York' (e.g. '2026-11-12 14:30:05 EST').
    """
    target = dt.astimezone(NY_TZ) if dt is not None else now_ny()
    tz_abbr = target.strftime("%Z") or "ET"
    return f"{target.strftime('%Y-%m-%d %H:%M:%S')} {tz_abbr}"


def format_ny_short_timestamp(dt: Optional[datetime] = None) -> str:
    """Format a compact NY timestamp for note annotations, e.g. '11/12/2026 14:30 ET'."""
    target = dt.astimezone(NY_TZ) if dt is not None else now_ny()
    tz_abbr = target.strftime("%Z") or "ET"
    return f"{target.strftime('%m/%d/%Y %H:%M')} {tz_abbr}"


def format_ny_date(dt: Optional[datetime] = None) -> str:
    """Return the New York date formatted as 'mm/dd/yyyy'."""
    target = dt.astimezone(NY_TZ) if dt is not None else now_ny()
    return target.strftime("%m/%d/%Y")


def today_ny_date() -> date:
    """Return today's calendar date in America/New_York."""
    return now_ny().date()


def today_ny_iso() -> str:
    """Return today's calendar date in America/New_York as 'YYYY-MM-DD'."""
    return today_ny_date().isoformat()


def validate_mmddyyyy(raw_date: str, field_name: str = "expiry") -> Tuple[str, str]:
    """
    Validate that `raw_date` strictly matches `mm/dd/yyyy` and is a real calendar date.

    Returns:
        Tuple of (normalized_mmddyyyy, iso_yyyy_mm_dd)
        e.g. ("12/18/2026", "2026-12-18")

    Raises:
        ValidationError: if the format or calendar date is invalid.
    """
    if not raw_date or not isinstance(raw_date, str):
        raise ValidationError(f"`{field_name}` cannot be empty. Expected format: `mm/dd/yyyy` (e.g. `12/18/2026`).")

    cleaned = raw_date.strip()
    if not MMDDYYYY_REGEX.match(cleaned):
        raise ValidationError(
            f"Invalid `{field_name}` format `{raw_date}`. Must strictly follow `mm/dd/yyyy` "
            f"(2-digit month, 2-digit day, 4-digit year, e.g. `12/18/2026`)."
        )

    try:
        parsed = datetime.strptime(cleaned, "%m/%d/%Y").date()
    except ValueError as exc:
        raise ValidationError(
            f"Invalid calendar date for `{field_name}`: `{raw_date}` does not exist. "
            f"Please provide a valid `mm/dd/yyyy` date."
        ) from exc

    return parsed.strftime("%m/%d/%Y"), parsed.isoformat()


def validate_ticker(raw_ticker: str) -> str:
    """Normalize and validate ticker symbol (uppercase, 1-12 chars)."""
    if not raw_ticker or not raw_ticker.strip():
        raise ValidationError("`ticker` cannot be empty.")
    cleaned = raw_ticker.strip().upper()
    if len(cleaned) > 15 or not re.match(r"^[A-Z0-9./\-^]+$", cleaned):
        raise ValidationError(f"Invalid `ticker` `{raw_ticker}`. Please enter a valid symbol (e.g. `NVDA`, `SPY`, `BRK.B`).")
    return cleaned


def validate_asset_type(asset_type: str) -> str:
    """Ensure asset_type is strictly lowercase 'options' or 'stock'."""
    cleaned = (asset_type or "").strip().lower()
    if cleaned not in VALID_ASSET_TYPES:
        raise ValidationError(f"Invalid `asset_type` `{asset_type}`. Allowed: {', '.join(VALID_ASSET_TYPES)}.")
    return cleaned


def validate_status(status: str) -> str:
    """Ensure status is strictly lowercase 'open', 'closed', or 'expired'."""
    cleaned = (status or "").strip().lower()
    if cleaned not in VALID_STATUSES:
        raise ValidationError(f"Invalid `status` `{status}`. Allowed: {', '.join(VALID_STATUSES)}.")
    return cleaned


def parse_status_filter(raw_status: Optional[str | Iterable[str]]) -> Optional[List[str]]:
    """
    Parse a multi-select or comma-separated status string into a validated list of lowercase statuses.
    Examples:
        "open,closed" -> ["open", "closed"]
        ["OPEN", "expired"] -> ["open", "expired"]
        None or "" or "all" -> None (no filter)
    """
    if raw_status is None:
        return None

    if isinstance(raw_status, str):
        stripped = raw_status.strip().lower()
        if not stripped or stripped == "all":
            return None
        items = [part.strip().lower() for part in re.split(r"[,;\s]+", stripped) if part.strip()]
    else:
        items = [str(part).strip().lower() for part in raw_status if str(part).strip()]

    if not items or "all" in items:
        return None

    validated: List[str] = []
    for item in items:
        v = validate_status(item)
        if v not in validated:
            validated.append(v)
    return validated


def validate_stock_direction(direction: str) -> str:
    """Ensure stock direction is 'long' or 'short' (lowercase)."""
    cleaned = (direction or "").strip().lower()
    if cleaned not in VALID_DIRECTIONS:
        raise ValidationError(f"Invalid stock direction `{direction}`. Must be `long` or `short`.")
    return cleaned


def upcoming_expiry_window_iso(reference_date: Optional[date] = None) -> Tuple[str, str]:
    """
    Compute the ISO date window [tomorrow_iso, end_window_iso] for the EOD
    '⚠️ Expiring Tomorrow / This Friday' reminder section.

    - Always covers at least the next 2 calendar days (or through this coming Friday
      if today is Monday–Thursday, or through Monday if today is Thursday/Friday).
    """
    base = reference_date or today_ny_date()
    start_date = base + timedelta(days=1)

    # Weekday: Monday=0 .. Friday=4, Saturday=5, Sunday=6
    weekday = base.weekday()
    if weekday < 4:
        # Monday..Thursday: include through this Friday
        days_until_friday = 4 - weekday
        end_date = base + timedelta(days=max(2, days_until_friday))
    elif weekday == 4:
        # Friday: look ahead through Monday (next 3 calendar days / 1-2 trading days)
        end_date = base + timedelta(days=3)
    else:
        # Weekend: look ahead 3 days
        end_date = base + timedelta(days=3)

    return start_date.isoformat(), end_date.isoformat()

