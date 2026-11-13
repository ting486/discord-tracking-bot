"""Configuration loader for 蜈蚣bot."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = BASE_DIR / "data" / "trades.db"


@dataclass(frozen=True)
class BotConfig:
    """Immutable runtime configuration for 蜈蚣bot."""

    discord_token: str
    database_url: str
    guild_id: Optional[int]
    eod_channel_id: Optional[int]
    eod_report_hour: int
    eod_report_minute: int


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    if not value or not value.strip():
        return None
    return int(value.strip())


def _parse_eod_time(value: str) -> tuple[int, int]:
    raw = (value or "16:15").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid EOD_REPORT_TIME '{value}'. Expected HH:MM (24-hour format).")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid EOD_REPORT_TIME '{value}'. Hour must be 0..23 and minute 0..59.")
    return hour, minute


def load_config() -> BotConfig:
    """Load configuration from environment variables with sensible defaults."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{DEFAULT_SQLITE_PATH}"

    guild_id = _parse_optional_int(os.getenv("DISCORD_GUILD_ID"))
    eod_channel_id = _parse_optional_int(os.getenv("EOD_CHANNEL_ID"))
    hour, minute = _parse_eod_time(os.getenv("EOD_REPORT_TIME", "16:15"))

    return BotConfig(
        discord_token=token,
        database_url=db_url,
        guild_id=guild_id,
        eod_channel_id=eod_channel_id,
        eod_report_hour=hour,
        eod_report_minute=minute,
    )

