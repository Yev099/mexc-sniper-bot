"""Connection checker — validates API keys and shows account info.

Run this before starting the bot to make sure everything is configured correctly.
Usage: python check_connection.py
"""

import asyncio
import sys

from config import Config
from mexc_api import MEXCFuturesClient


async def check():
    config = Config.from_env()

    print("=" * 50)
    print("  MEXC Connection Checker")
    print("=" * 50)

    # Check master
    if not config.master_api_key:
        print("\n[ERROR] MEXC_API_KEY is empty. Fill in your .env file.")
        sys.exit(1)

    print(f"\n[Master] Key: {config.master_api_key[:8]}...{config.master_api_key[-4:]}")

    master = MEXCFuturesClient(config.master_api_key, config.master_api_secret, "master")
    assets = await master.get_account_assets()

    if assets:
        usdt = None
        if isinstance(assets, list):
            for a in assets:
                if a.get("currency") == "USDT":
                    usdt = a
                    break
        elif isinstance(assets, dict):
            usdt = assets

        if usdt:
            balance = usdt.get("availableBalance", usdt.get("equity", "?"))
            print(f"[Master] Balance: {balance} USDT")
            print("[Master] Connection OK")
        else:
            print("[Master] Connected but no USDT balance found")
    else:
        print("[Master] ERROR: Could not fetch account. Check API key permissions.")
        await master.close()
        sys.exit(1)

    # Check positions
    positions = await master.get_open_positions()
    print(f"[Master] Open positions: {len(positions)}")
    for pos in positions:
        symbol = pos.get("symbol", "?")
        side = "LONG" if pos.get("positionType") == 1 else "SHORT"
        vol = pos.get("holdVol", 0)
        print(f"  - {symbol} {side} vol={vol}")

    await master.close()

    # Check followers
    if not config.followers:
        print("\n[WARNING] No followers configured. Bot will have nothing to copy to.")
    else:
        print(f"\n[Followers] Count: {len(config.followers)}")
        for f in config.followers:
            print(f"  - {f.name} (ratio={f.ratio}, key={f.api_key[:8]}...)")
            client = MEXCFuturesClient(f.api_key, f.api_secret, f.name)
            a = await client.get_account_assets()
            if a:
                print(f"    Connection OK")
            else:
                print(f"    ERROR: Cannot connect. Check key/secret.")
            await client.close()

    # Telegram
    print(f"\n[Telegram] Token: {'configured' if config.telegram_bot_token else 'NOT SET (optional)'}")
    print(f"[Telegram] Chat ID: {config.telegram_chat_id or 'NOT SET (optional)'}")

    print("\n" + "=" * 50)
    print("  All checks passed! Run: python main.py")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(check())
