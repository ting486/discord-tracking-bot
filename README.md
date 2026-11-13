# 蜈蚣bot — Discord Trading Idea & Order Tracking Bot

**蜈蚣bot** is an asynchronous Python Discord bot (`discord.py` + `SQLAlchemy 2.0 Async`) built to track group trading ideas across **options** (`/open-options`) and **stocks** (`/open-stock`), enforce initiator (**蜈蚣头**) update permissions (`/update`), provide clutter-free private historical search (`/search`) with a one-click **"Make it public"** button, and post automated **End-of-Day (EOD)** summaries in **New York (`America/New_York`)** time.

---

## Features & Slash Commands

| Command | Visibility | Description & Fields |
| :--- | :--- | :--- |
| **`/open-options`** | **Public** | Open a new `options` trade idea.<br>• `蜈蚣头` (`@user`, required)<br>• `ticker` (`str`, required)<br>• `expiry` (`str`, required — strictly validated as `mm/dd/yyyy`)<br>• `strike` (`str`, required — e.g. `150C`, `145/155`)<br>• `strategy` (`str`, required — e.g. `Long Call`, `Bull Call Spread`)<br>• `thesis` (`str`, required)<br>• `price` (`str`, optional — e.g. `$4.20 debit`)<br>• `notes` (`str`, optional) |
| **`/open-stock`** | **Public** | Open a new `stock` trade idea.<br>• `蜈蚣头` (`@user`, required)<br>• `ticker` (`str`, required)<br>• `long_short` (`Choice`: `long` / `short`, required)<br>• `thesis` (`str`, required)<br>• `entry_price` (`str`, optional — e.g. `$138.50`)<br>• `notes` (`str`, optional) |
| **`/update`** | **Public** *(errors are ephemeral)* | Update an existing `open` & unexpired order (**restricted strictly to that trade's `蜈蚣头`**).<br>• `trade_id` (`int`, required — **live autocomplete** displays the caller's active `open` & unexpired trades as readable choices)<br>• `close_trade` (`bool`, optional — marks `status = 'closed'`)<br>• `additional_comments` (`str`, optional — appends a New York timestamped note) |
| **`/search`** | **Private (`ephemeral=True`) by default** | Search and sort historic orders by `蜈蚣头`, `ticker`, `expiry` (`mm/dd/yyyy`), `thesis`, `notes`, `status` (`open`, `closed`, `expired` — multi-select supported), and `created_date` (`mm/dd/yyyy`).<br>• Renders a crisp monospaced **Table** (`thesis` and `notes` truncated to ~22 chars so columns never wrap).<br>• **🔎 View Full Trade Details** dropdown below the table shows the full un-truncated `thesis`, complete timestamped `notes` history, and jump link to the original Discord chat message.<br>• **Status Multi-Select** dropdown (`open` / `closed` / `expired`).<br>• **📢 Make it public** button to publish the table or detail card to the group chat. |

### Automated New York Timezone & End-of-Day (EOD) Summary
- All dates (`created_date` as `mm/dd/yyyy`) and timestamps (`created_at`, `updated_at`, `closed_at`) are stored in **`America/New_York`** timezone.
- Any `open` options trade whose `expiry` (`mm/dd/yyyy`) is prior to today's New York date automatically transitions to `expired`.
- At **16:15 `America/New_York`** (Mon–Fri, configurable via `EOD_REPORT_TIME`), **蜈蚣bot** posts:
  1. **Trades Initiated Today**: Full table of all trades opened on the current New York day.
  2. **⚠️ Expiring Tomorrow / This Friday**: Lists any still-`open` options trades expiring within the next 1–2 trading days (or through Friday), tagging their **蜈蚣头**.

---

## Quick Start

### 1. Create a Virtual Environment & Install Dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Fill in `.env`:
- `DISCORD_BOT_TOKEN`: Your bot token from the [Discord Developer Portal](https://discord.com/developers/applications) (enable `applications.commands` + `bot` scopes with `Send Messages` and `Embed Links` permissions).
- `DISCORD_GUILD_ID` *(optional)*: Your Discord Server ID for instant slash-command registration.
- `EOD_CHANNEL_ID` *(optional)*: The Channel ID where the daily 16:15 NY time EOD report should be posted.
- `DATABASE_URL`:
  - **Local SQLite (default)**: `sqlite+aiosqlite:///data/trades.db`
  - **Cloud PostgreSQL (AWS RDS, Azure Database for PostgreSQL, Supabase, Neon)**: `postgresql+asyncpg://user:pass@host:5432/dbname`

### 3. Run the Bot

```bash
.venv/bin/python -m bot.main
```

### 4. Run Automated Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```
