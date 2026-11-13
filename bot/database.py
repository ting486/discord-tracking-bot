"""Async SQL repository for 蜈蚣bot supporting SQLite (aiosqlite) and PostgreSQL (asyncpg) via SQLAlchemy 2.0."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    asc,
    desc,
    func,
    select,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from bot.validators import (
    NY_TZ,
    ValidationError,
    format_ny_date,
    format_ny_datetime,
    format_ny_short_timestamp,
    now_ny,
    parse_status_filter,
    today_ny_iso,
    upcoming_expiry_window_iso,
    validate_asset_type,
    validate_mmddyyyy,
    validate_status,
    validate_stock_direction,
    validate_ticker,
)


class Base(DeclarativeBase):
    """Declarative base for SQLAlchemy 2.0 ORM models."""


class PermissionDeniedError(PermissionError):
    """Raised when a user who is not the 蜈蚣头 tries to update an order."""


class InvalidTradeStateError(ValueError):
    """Raised when attempting to update an already closed or expired trade."""


class TradeNotFoundError(LookupError):
    """Raised when a trade_id does not exist."""


class TradeORM(Base):
    """SQLAlchemy ORM model for the `trades` table (compatible with SQLite and PostgreSQL)."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 'options' or 'stock' (strictly lowercase)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # 蜈蚣头 (trade initiator)
    initiator_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    initiator_name: Mapped[str] = mapped_column(String(128), nullable=False)

    # User who invoked the /open-options or /open-stock command
    created_by_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    ticker: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    strike: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    direction_or_strategy: Mapped[str] = mapped_column(String(255), nullable=False)

    # Validated 'mm/dd/yyyy' for options (NULL for stock)
    expiry: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    # 'YYYY-MM-DD' derived from expiry for accurate chronological sorting & auto-expiration
    expiry_iso: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)

    thesis: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 'open', 'closed', or 'expired' (strictly lowercase)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)

    # All dates/datetimes stored in New York timezone ('America/New_York')
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    created_date: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    closed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("asset_type IN ('options', 'stock')", name="ck_trades_asset_type"),
        CheckConstraint("status IN ('open', 'closed', 'expired')", name="ck_trades_status"),
        Index("ix_trades_guild_status", "guild_id", "status"),
        Index("ix_trades_guild_created_date", "guild_id", "created_date"),
    )


@dataclass
class TradeRecord:
    """Clean, detached domain object representing a row in `trades`."""

    id: int
    guild_id: int
    channel_id: int
    message_url: Optional[str]
    asset_type: str
    initiator_id: int
    initiator_name: str
    created_by_id: int
    ticker: str
    strike: Optional[str]
    price: Optional[str]
    direction_or_strategy: str
    expiry: Optional[str]
    expiry_iso: Optional[str]
    thesis: str
    notes: Optional[str]
    status: str
    created_at: str
    created_date: str
    updated_at: str
    closed_at: Optional[str]

    @classmethod
    def from_orm(cls, obj: TradeORM) -> "TradeRecord":
        return cls(
            id=obj.id,
            guild_id=obj.guild_id,
            channel_id=obj.channel_id,
            message_url=obj.message_url,
            asset_type=obj.asset_type,
            initiator_id=obj.initiator_id,
            initiator_name=obj.initiator_name,
            created_by_id=obj.created_by_id,
            ticker=obj.ticker,
            strike=obj.strike,
            price=obj.price,
            direction_or_strategy=obj.direction_or_strategy,
            expiry=obj.expiry,
            expiry_iso=obj.expiry_iso,
            thesis=obj.thesis,
            notes=obj.notes,
            status=obj.status,
            created_at=obj.created_at,
            created_date=obj.created_date,
            updated_at=obj.updated_at,
            closed_at=obj.closed_at,
        )

    def autocomplete_label(self) -> str:
        """
        Format a human-readable choice label (<=100 chars for Discord autocomplete)
        for `/update` trade_id lookup.
        Example: '#101 | NVDA | 145C | Bull Call Spread (Exp 12/18/2026)'
        """
        if self.asset_type == "options":
            strike_part = f" {self.strike}" if self.strike else ""
            exp_part = f"Exp {self.expiry}" if self.expiry else ""
            raw = f"#{self.id} | {self.ticker}{strike_part} | {self.direction_or_strategy} ({exp_part})"
        else:
            price_part = f" @ {self.price}" if self.price else ""
            raw = f"#{self.id} | {self.ticker} | {self.direction_or_strategy} stock{price_part} ({self.created_date})"
        return raw[:97] + "..." if len(raw) > 100 else raw


SORT_COLUMN_MAP = {
    "created_at": TradeORM.id,
    "蜈蚣头": func.lower(TradeORM.initiator_name),
    "initiator": func.lower(TradeORM.initiator_name),
    "ticker": TradeORM.ticker,
    "expiry": TradeORM.expiry_iso,
    "status": TradeORM.status,
    "thesis": func.lower(TradeORM.thesis),
    "notes": func.lower(func.coalesce(TradeORM.notes, "")),
}


class TradeDatabase:
    """Async repository for managing trades across SQLite (aiosqlite) or Cloud PostgreSQL (asyncpg)."""

    def __init__(self, database_url: str, echo: bool = False) -> None:
        self.database_url = database_url
        self._ensure_sqlite_parent_dir(database_url)
        self.engine: AsyncEngine = create_async_engine(database_url, echo=echo, future=True)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @staticmethod
    def _ensure_sqlite_parent_dir(database_url: str) -> None:
        """If using a file-backed SQLite URL, automatically create its parent directory."""
        try:
            url = make_url(database_url)
            if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
                db_path = Path(url.database)
                if db_path.parent and str(db_path.parent) != ".":
                    db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    async def init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose of the underlying SQLAlchemy AsyncEngine connection pool."""
        await self.engine.dispose()

    async def expire_past_options(
        self,
        guild_id: Optional[int] = None,
        now_dt: Optional[datetime] = None,
        include_today: Optional[bool] = None,
    ) -> int:
        """
        Automatically mark past-due 'open' options trades as 'expired', updating `updated_at`
        in New York format:
        - Before 16:00 America/New_York (market hours): expires options with `expiry_iso < today_iso`
          so same-day expiring options remain 'open' and updatable until the closing bell.
        - At or after 16:00 America/New_York (EOD / Market Close), or when `include_today=True`:
          expires options with `expiry_iso <= today_iso` so today's expiring options transition
          to 'expired' at EOD.

        Returns the number of trades transitioned to 'expired'.
        """
        target_ny = now_dt.astimezone(NY_TZ) if now_dt is not None else now_ny()
        current_iso = target_ny.date().isoformat()
        ny_timestamp = format_ny_datetime(target_ny)

        if include_today is None:
            # Market close cutoff: 16:00 (4:00 PM) America/New_York
            include_today = target_ny.hour >= 16

        expiry_condition = (
            TradeORM.expiry_iso <= current_iso
            if include_today
            else TradeORM.expiry_iso < current_iso
        )

        stmt = (
            update(TradeORM)
            .where(TradeORM.asset_type == "options")
            .where(TradeORM.status == "open")
            .where(TradeORM.expiry_iso.is_not(None))
            .where(expiry_condition)
        )
        if guild_id is not None:
            stmt = stmt.where(TradeORM.guild_id == guild_id)

        stmt = stmt.values(status="expired", updated_at=ny_timestamp)

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    async def create_options_trade(
        self,
        *,
        guild_id: int,
        channel_id: int,
        initiator_id: int,
        initiator_name: str,
        created_by_id: int,
        ticker: str,
        expiry: str,
        strike: str,
        strategy: str,
        thesis: str,
        price: Optional[str] = None,
        notes: Optional[str] = None,
        message_url: Optional[str] = None,
        now_dt: Optional[datetime] = None,
    ) -> TradeRecord:
        """
        Validate inputs (including strict mm/dd/yyyy expiry) and create a new 'options' trade.
        """
        norm_ticker = validate_ticker(ticker)
        norm_expiry, expiry_iso = validate_mmddyyyy(expiry, field_name="expiry")

        if not strike or not strike.strip():
            raise ValidationError("`strike` cannot be empty for `/open-options`.")
        if not strategy or not strategy.strip():
            raise ValidationError("`strategy` cannot be empty for `/open-options`.")
        if not thesis or not thesis.strip():
            raise ValidationError("`thesis` cannot be empty.")

        ny_dt_str = format_ny_datetime(now_dt)
        ny_date_str = format_ny_date(now_dt)
        target_ny = now_dt.astimezone(NY_TZ) if now_dt is not None else now_ny()

        # Determine initial status based on NY date & 16:00 market close cutoff
        today_iso = target_ny.date().isoformat()
        if expiry_iso < today_iso or (expiry_iso == today_iso and target_ny.hour >= 16):
            initial_status = "expired"
        else:
            initial_status = "open"

        clean_notes = notes.strip() if notes and notes.strip() else None
        clean_price = price.strip() if price and price.strip() else None

        trade = TradeORM(
            guild_id=guild_id,
            channel_id=channel_id,
            message_url=message_url,
            asset_type=validate_asset_type("options"),
            initiator_id=initiator_id,
            initiator_name=initiator_name.strip(),
            created_by_id=created_by_id,
            ticker=norm_ticker,
            strike=strike.strip(),
            price=clean_price,
            direction_or_strategy=strategy.strip(),
            expiry=norm_expiry,
            expiry_iso=expiry_iso,
            thesis=thesis.strip(),
            notes=clean_notes,
            status=validate_status(initial_status),
            created_at=ny_dt_str,
            created_date=ny_date_str,
            updated_at=ny_dt_str,
            closed_at=None,
        )

        async with self.session_factory() as session:
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            return TradeRecord.from_orm(trade)

    async def create_stock_trade(
        self,
        *,
        guild_id: int,
        channel_id: int,
        initiator_id: int,
        initiator_name: str,
        created_by_id: int,
        ticker: str,
        long_short: str,
        thesis: str,
        entry_price: Optional[str] = None,
        notes: Optional[str] = None,
        message_url: Optional[str] = None,
        now_dt: Optional[datetime] = None,
    ) -> TradeRecord:
        """
        Validate inputs and create a new 'stock' trade with direction 'long' or 'short'.
        """
        norm_ticker = validate_ticker(ticker)
        norm_direction = validate_stock_direction(long_short)

        if not thesis or not thesis.strip():
            raise ValidationError("`thesis` cannot be empty.")

        ny_dt_str = format_ny_datetime(now_dt)
        ny_date_str = format_ny_date(now_dt)

        clean_notes = notes.strip() if notes and notes.strip() else None
        clean_price = entry_price.strip() if entry_price and entry_price.strip() else None

        trade = TradeORM(
            guild_id=guild_id,
            channel_id=channel_id,
            message_url=message_url,
            asset_type=validate_asset_type("stock"),
            initiator_id=initiator_id,
            initiator_name=initiator_name.strip(),
            created_by_id=created_by_id,
            ticker=norm_ticker,
            strike=None,
            price=clean_price,
            direction_or_strategy=norm_direction,
            expiry=None,
            expiry_iso=None,
            thesis=thesis.strip(),
            notes=clean_notes,
            status=validate_status("open"),
            created_at=ny_dt_str,
            created_date=ny_date_str,
            updated_at=ny_dt_str,
            closed_at=None,
        )

        async with self.session_factory() as session:
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            return TradeRecord.from_orm(trade)

    async def set_message_url(self, trade_id: int, message_url: str) -> None:
        """Save the Discord message jump URL after posting the public trade embed."""
        async with self.session_factory() as session:
            stmt = update(TradeORM).where(TradeORM.id == trade_id).values(message_url=message_url)
            await session.execute(stmt)
            await session.commit()

    async def get_trade(
        self,
        trade_id: int,
        guild_id: Optional[int] = None,
        now_dt: Optional[datetime] = None,
    ) -> Optional[TradeRecord]:
        """Fetch a single trade by ID after running automatic expiration."""
        await self.expire_past_options(guild_id=guild_id, now_dt=now_dt)

        async with self.session_factory() as session:
            stmt = select(TradeORM).where(TradeORM.id == trade_id)
            if guild_id is not None:
                stmt = stmt.where(TradeORM.guild_id == guild_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return TradeRecord.from_orm(row) if row else None

    async def update_trade(
        self,
        *,
        trade_id: int,
        actor_user_id: int,
        guild_id: Optional[int] = None,
        additional_comments: Optional[str] = None,
        close_trade: bool = False,
        now_dt: Optional[datetime] = None,
    ) -> TradeRecord:
        """
        Update an existing open, unexpired trade.
        Rules:
          1. Automatically expires past-due options first.
          2. Only the trade's 蜈蚣头 (initiator_id == actor_user_id) can update the trade.
          3. Only 'open' (unexpired, unclosed) trades can be updated.
          4. At least one of `additional_comments` or `close_trade=True` must be provided.
          5. Updates `updated_at` (and `closed_at` if closing) in New York timezone format.
        """
        await self.expire_past_options(guild_id=guild_id, now_dt=now_dt)

        has_comment = bool(additional_comments and additional_comments.strip())
        if not has_comment and not close_trade:
            raise ValidationError(
                "Please provide `additional_comments` and/or set `close_trade=True` to update the order."
            )

        async with self.session_factory() as session:
            stmt = select(TradeORM).where(TradeORM.id == trade_id)
            if guild_id is not None:
                stmt = stmt.where(TradeORM.guild_id == guild_id)
            result = await session.execute(stmt)
            trade = result.scalar_one_or_none()

            if trade is None:
                raise TradeNotFoundError(f"Order `#{trade_id}` was not found.")

            if trade.initiator_id != actor_user_id:
                raise PermissionDeniedError(
                    f"Only the **蜈蚣头** (<@{trade.initiator_id}> / `{trade.initiator_name}`) "
                    f"can update order `#{trade_id}`."
                )

            if trade.status == "expired":
                raise InvalidTradeStateError(
                    f"Order `#{trade_id}` (`{trade.ticker}` exp `{trade.expiry}`) has already **expired** "
                    f"and cannot be updated."
                )

            if trade.status == "closed":
                raise InvalidTradeStateError(
                    f"Order `#{trade_id}` (`{trade.ticker}`) is already **closed** and cannot be updated."
                )

            ny_dt_str = format_ny_datetime(now_dt)
            short_ts = format_ny_short_timestamp(now_dt)

            if has_comment:
                comment_line = f"[{short_ts}] {additional_comments.strip()}"
                if trade.notes and trade.notes.strip():
                    trade.notes = f"{trade.notes.strip()}\n{comment_line}"
                else:
                    trade.notes = comment_line

            if close_trade:
                trade.status = validate_status("closed")
                trade.closed_at = ny_dt_str

            trade.updated_at = ny_dt_str

            await session.commit()
            await session.refresh(trade)
            return TradeRecord.from_orm(trade)

    async def get_open_unexpired_trades_for_user(
        self,
        *,
        initiator_id: int,
        guild_id: Optional[int] = None,
        search_text: Optional[str] = None,
        limit: int = 25,
        now_dt: Optional[datetime] = None,
    ) -> List[TradeRecord]:
        """
        Return the caller's active `open` & unexpired trades for `/update` `trade_id` autocomplete.
        """
        await self.expire_past_options(guild_id=guild_id, now_dt=now_dt)

        async with self.session_factory() as session:
            stmt = (
                select(TradeORM)
                .where(TradeORM.initiator_id == initiator_id)
                .where(TradeORM.status == "open")
                .order_by(desc(TradeORM.id))
            )
            if guild_id is not None:
                stmt = stmt.where(TradeORM.guild_id == guild_id)

            result = await session.execute(stmt)
            rows = [TradeRecord.from_orm(r) for r in result.scalars().all()]

        if search_text and search_text.strip():
            q = search_text.strip().lower().lstrip("#")
            rows = [
                r
                for r in rows
                if q in str(r.id)
                or q in r.ticker.lower()
                or q in r.direction_or_strategy.lower()
                or (r.strike and q in r.strike.lower())
                or (r.expiry and q in r.expiry.lower())
            ]

        return rows[:limit]

    async def search_trades(
        self,
        *,
        guild_id: Optional[int] = None,
        initiator_id: Optional[int] = None,
        initiator_name: Optional[str] = None,
        ticker: Optional[str] = None,
        expiry: Optional[str] = None,
        thesis_keyword: Optional[str] = None,
        notes_keyword: Optional[str] = None,
        statuses: Optional[str | Iterable[str]] = None,
        created_date: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "DESC",
        now_dt: Optional[datetime] = None,
    ) -> List[TradeRecord]:
        """
        Search historic orders with multi-field filtering and customizable sorting.
        - Validates `expiry` and `created_date` as `mm/dd/yyyy` if provided.
        - Supports multi-select `statuses` (e.g. "open,closed" or ["open", "expired"]).
        - Sorts by any of: created_at, 蜈蚣头, ticker, expiry, status, thesis, notes.
        """
        await self.expire_past_options(guild_id=guild_id, now_dt=now_dt)

        stmt = select(TradeORM)

        if guild_id is not None:
            stmt = stmt.where(TradeORM.guild_id == guild_id)

        if initiator_id is not None:
            stmt = stmt.where(TradeORM.initiator_id == initiator_id)
        elif initiator_name and initiator_name.strip():
            q_init = f"%{initiator_name.strip().lower()}%"
            stmt = stmt.where(func.lower(TradeORM.initiator_name).like(q_init))

        if ticker and ticker.strip():
            norm_ticker = validate_ticker(ticker)
            stmt = stmt.where(TradeORM.ticker == norm_ticker)

        if expiry and expiry.strip():
            norm_expiry, _ = validate_mmddyyyy(expiry, field_name="expiry")
            stmt = stmt.where(TradeORM.expiry == norm_expiry)

        if created_date and created_date.strip():
            norm_created_date, _ = validate_mmddyyyy(created_date, field_name="created_date")
            stmt = stmt.where(TradeORM.created_date == norm_created_date)

        if thesis_keyword and thesis_keyword.strip():
            q_thesis = f"%{thesis_keyword.strip().lower()}%"
            stmt = stmt.where(func.lower(TradeORM.thesis).like(q_thesis))

        if notes_keyword and notes_keyword.strip():
            q_notes = f"%{notes_keyword.strip().lower()}%"
            stmt = stmt.where(func.lower(func.coalesce(TradeORM.notes, "")).like(q_notes))

        parsed_statuses = parse_status_filter(statuses)
        if parsed_statuses:
            stmt = stmt.where(TradeORM.status.in_(parsed_statuses))

        # Sorting
        sort_key = (sort_by or "created_at").strip()
        if sort_key not in SORT_COLUMN_MAP:
            sort_key = "created_at"
        sort_col = SORT_COLUMN_MAP[sort_key]

        is_asc = (sort_order or "DESC").strip().upper() == "ASC"
        direction_fn = asc if is_asc else desc

        # When sorting by expiry, place NULLs (stocks) at the end
        if sort_key == "expiry":
            nulls_order = asc(TradeORM.expiry_iso.is_(None))
            stmt = stmt.order_by(nulls_order, direction_fn(sort_col), desc(TradeORM.id))
        else:
            stmt = stmt.order_by(direction_fn(sort_col), desc(TradeORM.id))

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            rows: Sequence[TradeORM] = result.scalars().all()
            return [TradeRecord.from_orm(r) for r in rows]

    async def get_daily_initiated_trades(
        self,
        *,
        guild_id: Optional[int] = None,
        ny_date_mmddyyyy: Optional[str] = None,
        now_dt: Optional[datetime] = None,
    ) -> List[TradeRecord]:
        """
        Return all trades initiated on the given New York day (`mm/dd/yyyy`, defaults to today in NY).
        """
        await self.expire_past_options(guild_id=guild_id, now_dt=now_dt)
        target_date = (
            validate_mmddyyyy(ny_date_mmddyyyy, field_name="created_date")[0]
            if ny_date_mmddyyyy
            else format_ny_date(now_dt)
        )

        stmt = select(TradeORM).where(TradeORM.created_date == target_date)
        if guild_id is not None:
            stmt = stmt.where(TradeORM.guild_id == guild_id)
        stmt = stmt.order_by(asc(TradeORM.id))

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            return [TradeRecord.from_orm(r) for r in result.scalars().all()]

    async def get_expiring_soon_options(
        self,
        *,
        guild_id: Optional[int] = None,
        now_dt: Optional[datetime] = None,
    ) -> List[TradeRecord]:
        """
        Return all still-'open' options trades expiring within the upcoming 1–2 trading days
        / through this Friday (based on New York date) for the EOD pre-expiry reminder.
        """
        await self.expire_past_options(guild_id=guild_id, now_dt=now_dt)

        ref_date = now_dt.date() if now_dt is not None else None
        start_iso, end_iso = upcoming_expiry_window_iso(ref_date)

        stmt = (
            select(TradeORM)
            .where(TradeORM.asset_type == "options")
            .where(TradeORM.status == "open")
            .where(TradeORM.expiry_iso.is_not(None))
            .where(TradeORM.expiry_iso >= start_iso)
            .where(TradeORM.expiry_iso <= end_iso)
        )
        if guild_id is not None:
            stmt = stmt.where(TradeORM.guild_id == guild_id)
        stmt = stmt.order_by(asc(TradeORM.expiry_iso), asc(TradeORM.id))

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            return [TradeRecord.from_orm(r) for r in result.scalars().all()]

    async def get_distinct_tickers(
        self,
        guild_id: Optional[int] = None,
        prefix: str = "",
        limit: int = 25,
    ) -> List[str]:
        """Return distinct tickers logged in this server for `/search` autocomplete."""
        stmt = select(TradeORM.ticker).distinct()
        if guild_id is not None:
            stmt = stmt.where(TradeORM.guild_id == guild_id)
        if prefix and prefix.strip():
            stmt = stmt.where(TradeORM.ticker.like(f"{prefix.strip().upper()}%"))
        stmt = stmt.order_by(asc(TradeORM.ticker)).limit(limit)

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

