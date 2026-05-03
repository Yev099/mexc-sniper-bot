"""Configuration loader — reads .env and parses accounts."""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class FollowerAccount:
    name: str
    api_key: str
    api_secret: str
    ratio: float = 1.0


@dataclass
class Config:
    # Master account
    master_api_key: str = ""
    master_api_secret: str = ""

    # Followers
    followers: List[FollowerAccount] = field(default_factory=list)

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Trading settings
    poll_interval: float = 0.5
    default_leverage: int = 20
    copy_limit_orders: bool = True
    copy_tp_sl: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls(
            master_api_key=os.getenv("MEXC_API_KEY", ""),
            master_api_secret=os.getenv("MEXC_API_SECRET", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            poll_interval=float(os.getenv("POLL_INTERVAL", "0.5")),
            default_leverage=int(os.getenv("DEFAULT_LEVERAGE", "20")),
            copy_limit_orders=os.getenv("COPY_LIMIT_ORDERS", "true").lower() == "true",
            copy_tp_sl=os.getenv("COPY_TP_SL", "true").lower() == "true",
        )

        raw = os.getenv("FOLLOWERS", "")
        if raw:
            for entry in raw.split(","):
                parts = entry.strip().split(":")
                if len(parts) >= 3:
                    cfg.followers.append(FollowerAccount(
                        name=parts[0],
                        api_key=parts[1],
                        api_secret=parts[2],
                        ratio=float(parts[3]) if len(parts) > 3 else 1.0,
                    ))

        return cfg
