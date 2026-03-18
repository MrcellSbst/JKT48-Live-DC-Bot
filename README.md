# JKT48 Live Bot

A Discord bot that monitors **JKT48** member live streams on [IDN Live](https://www.idn.app) and [SHOWROOM](https://www.showroom-live.com), delivering real-time go-live and end-of-live notifications directly to a Discord channel.

---

## Features

| Feature | Description |
|---|---|
| **IDN Live detection** | Polls the IDN Live GraphQL API every 2 minutes and notifies when a JKT48 member goes live |
| **SHOWROOM detection** | Polls the SHOWROOM API for JKT48 room live sessions |
| **Live end detection** | Detects when a live session ends and posts a summary embed with session duration |
| **Persistent state** | SQLite database tracks live session history to prevent duplicate notifications across restarts |
| **Slash and prefix commands** | `/streams` slash command and `!streams` prefix command to check current live sessions on demand |

---

## Getting Started

### Prerequisites

- Python **3.10+**
- A Discord bot token ([create one here](https://discord.com/developers/applications))
- The target Discord channel ID where notifications will be posted

### 1. Clone the repository

```bash
git clone https://github.com/your-username/JKT48Bot.git
cd JKT48Bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
# Required
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_ID=123456789012345678

# Optional — custom SQLite database path
LIVE_STATUS_DB=data/live_status.db
```

### 4. Run the bot

```bash
python jkt48_live_bot.py
```

---

## Discord Bot Setup

In the [Discord Developer Portal](https://discord.com/developers/applications), ensure your bot has the following configuration:

**Privileged Gateway Intents:**
- Message Content Intent

**Bot Permissions (when inviting):**
- Send Messages
- Embed Links
- Read Message History

Use this URL template to invite the bot (replace `CLIENT_ID`):
```
https://discord.com/api/oauth2/authorize?client_id=CLIENT_ID&permissions=51200&scope=bot%20applications.commands
```

---

## Bot Commands

| Command | Type | Description |
|---|---|---|
| `!streams` | Prefix | Shows a live summary of current IDN & SHOWROOM sessions |
| `/streams` | Slash | Same as above, as a Discord slash command |
| `!help` | Prefix | Lists available bot commands |

---

## Project Structure

```
JKT48Bot/
├── jkt48_live_bot.py   # Main bot entry point
├── requirements.txt    # Python dependencies
├── .env                # Environment variables (not committed)
└── data/
    └── live_status.db  # SQLite database (auto-created at runtime)
```

---

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | Yes | — | Discord bot token |
| `DISCORD_CHANNEL_ID` | Yes | — | Target channel for notifications |
| `LIVE_STATUS_DB` | No | `data/live_status.db` | Path to SQLite database file |

---

## Dependencies

| Package | Purpose |
|---|---|
| [`discord.py`](https://discordpy.readthedocs.io) | Discord bot framework |
| [`aiohttp`](https://docs.aiohttp.org) | Async HTTP client for IDN & SHOWROOM APIs |
| [`python-dotenv`](https://pypi.org/project/python-dotenv/) | `.env` file loading |

---

## License

MIT License — free to fork and adapt for other idol groups or live platforms.
