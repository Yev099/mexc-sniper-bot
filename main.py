"""MEXC Copy Trading Bot — Entry Point.

Usage:
    1. Copy .env.example to .env
    2. Fill in your API keys and follower accounts
    3. Run: python main.py
"""

import asyncio
import logging
import signal
import sys

from config import Config
from copy_service import CopyTradingService
from telegram_bot import TradingBot

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("main")


async def main():
    # Load configuration
    config = Config.from_env()

    # Validate
    if not config.master_api_key or not config.master_api_secret:
        log.error("MEXC_API_KEY and MEXC_API_SECRET are required. Check your .env file.")
        sys.exit(1)

    if not config.followers:
        log.error("No follower accounts configured. Check FOLLOWERS in .env file.")
        sys.exit(1)

    log.info("Master account configured")
    log.info("Followers: %s", ", ".join(f.name for f in config.followers))

    # Initialize services
    copy_service = CopyTradingService(config)

    # Initialize Telegram bot
    bot = TradingBot(config.telegram_bot_token, config.telegram_chat_id, copy_service)
    copy_service.set_notify_callback(bot.send_message)

    # Start bot
    await bot.start()

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def shutdown_handler():
        log.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    # Start copy trading
    copy_task = asyncio.create_task(copy_service.start())

    # Wait for shutdown
    await stop_event.wait()

    # Cleanup
    await copy_service.stop()
    await bot.stop()
    copy_task.cancel()

    log.info("Shutdown complete")


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════╗
║   MEXC Copy Trading Bot                      ║
║   github.com/Yev099/mexc-sniper-bot          ║
╠══════════════════════════════════════════════╣
║   Ctrl+C to stop                             ║
╚══════════════════════════════════════════════╝
    """)
    asyncio.run(main())
