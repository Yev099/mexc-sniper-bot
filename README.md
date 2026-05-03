# MEXC Sniper Bot — Copy Trading

Open-source bot for copying futures trades on MEXC exchange. One master account trades — all follower accounts automatically replicate positions, limit orders, TP/SL.

## Features

- **Real-time copy trading** — polls master every 500ms
- **Volume ratio** — each follower can have different position size multiplier
- **TP/SL sync** — take profit and stop loss copied via separate API call (reliable)
- **Limit orders** — pending orders replicated to all followers
- **Telegram bot** — notifications + control (start/stop/status/positions)
- **Auto-close** — when master closes, followers close too
- **Leverage sync** — followers match master's leverage per symbol

## Quick Start

```bash
# Clone
git clone https://github.com/Yev099/mexc-sniper-bot.git
cd mexc-sniper-bot

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Verify connection
python check_connection.py

# Run
python main.py
```

> **New here?** Read [QUICK_START.md](QUICK_START.md) — full guide in Russian/English.

## Configuration (.env)

| Variable | Description |
|----------|------------|
| `MEXC_API_KEY` | Master account API key |
| `MEXC_API_SECRET` | Master account API secret |
| `FOLLOWERS` | API key followers: `name:key:secret:ratio` (comma-separated) |
| `FOLLOWERS_COOKIE` | Cookie followers: `name\|u_id\|ratio\|proxy\|ua` (pipe-separated, comma between accounts) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |
| `POLL_INTERVAL` | Polling interval in seconds (default: 0.5) |
| `DEFAULT_LEVERAGE` | Default leverage if not detected (default: 20) |
| `COPY_LIMIT_ORDERS` | Copy pending/limit orders (default: true) |
| `COPY_TP_SL` | Copy take profit / stop loss (default: true) |

## Two Auth Methods

**1. API Keys** — standard way. Get from MEXC → Account → API Management.

**2. Cookie (u_id)** — no API keys needed! Get from browser:
1. Open mexc.com → Futures → press F12 (DevTools)
2. Network tab → filter `futures.mexc.com`
3. Click any request → Headers → copy `Authorization` value
4. That's your u_id — paste into `FOLLOWERS_COOKIE`

## How to Get API Keys

1. Go to [MEXC](https://www.mexc.com) → Account → API Management
2. Create new API key with **Futures Trading** permission
3. Whitelist your server IP (recommended)
4. Copy the key and secret to `.env`

## Telegram Bot Setup

1. Message [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copy the token to `TELEGRAM_BOT_TOKEN`
3. Message your bot, then get your chat ID via `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Set `TELEGRAM_CHAT_ID`

### Bot Commands

| Command | Description |
|---------|------------|
| `/start` | Show control panel |
| `/status` | Current bot status |
| `/positions` | Master's open positions |
| `/stop` | Pause copy trading |
| `/resume` | Resume copy trading |
| `/help` | Help message |

## Architecture

```
main.py              → Entry point, wires everything together
config.py            → Loads .env, parses follower accounts
mexc_api.py          → MEXC API client (API keys + Cookie/u_id auth)
copy_service.py      → Core copy trading logic (polling, replication)
telegram_bot.py      → Telegram bot (notifications + control)
check_connection.py  → Validates keys/cookies before first run
```

## How Copy Trading Works

1. Bot polls master's positions and orders every 500ms
2. When a new position appears → opens same position on all followers (adjusted by ratio)
3. After position opens → sets TP/SL via separate API call (1.5s delay for MEXC to register)
4. When master sets/changes TP/SL → syncs to followers
5. When master places limit order → copies to followers with adjusted volume
6. When master closes position → closes on all followers

## Contributing

Pull requests welcome! If you improve the bot, share it with the community.

### Ideas for contribution:
- Web UI dashboard
- Multiple master accounts support
- Risk management (max drawdown, position limits)
- WebSocket instead of polling
- Trailing stop loss
- Partial close support

## Disclaimer

This software is provided as-is for educational purposes. Trading futures involves substantial risk of loss. Use at your own risk. The authors are not responsible for any financial losses.

## License

MIT
