"""Telegram Bot for Copy Trading notifications and control."""

import asyncio
import logging
from typing import Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

log = logging.getLogger("tg-bot")


class TradingBot:
    """Telegram bot for monitoring and controlling the copy trading service."""

    def __init__(self, token: str, chat_id: str, copy_service=None):
        self.token = token
        self.chat_id = chat_id
        self.copy_service = copy_service
        self.app: Optional[Application] = None

    async def start(self):
        """Initialize and start the Telegram bot."""
        if not self.token:
            log.warning("No Telegram token — bot disabled")
            return

        self.app = Application.builder().token(self.token).build()

        # Register commands
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("positions", self._cmd_positions))
        self.app.add_handler(CommandHandler("stop", self._cmd_stop))
        self.app.add_handler(CommandHandler("resume", self._cmd_resume))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CallbackQueryHandler(self._callback_handler))

        # Set bot commands menu
        await self.app.bot.set_my_commands([
            BotCommand("start", "Start bot"),
            BotCommand("status", "Show copy trading status"),
            BotCommand("positions", "Show open positions"),
            BotCommand("stop", "Pause copy trading"),
            BotCommand("resume", "Resume copy trading"),
            BotCommand("help", "Show help"),
        ])

        # Start polling in background
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram bot started")

    async def stop(self):
        """Stop the Telegram bot."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    async def send_message(self, text: str):
        """Send message to the configured chat."""
        if not self.app or not self.chat_id:
            return
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            log.warning("Failed to send TG message: %s", e)

    # ─── Command Handlers ───────────────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        keyboard = [
            [
                InlineKeyboardButton("📊 Status", callback_data="status"),
                InlineKeyboardButton("📈 Positions", callback_data="positions"),
            ],
            [
                InlineKeyboardButton("⏸ Stop", callback_data="stop"),
                InlineKeyboardButton("▶️ Resume", callback_data="resume"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "🤖 <b>MEXC Copy Trading Bot</b>\n\n"
            "Use the buttons below or commands to control the bot.",
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current copy trading status."""
        if not self.copy_service:
            await update.message.reply_text("⚠️ Copy service not connected")
            return

        running = "✅ Running" if self.copy_service._running else "🛑 Stopped"
        master = self.copy_service.master.name
        followers = ", ".join(f.name for f in self.copy_service.followers) or "none"
        positions = len(self.copy_service._master_positions)
        orders_copied = len(self.copy_service._copied_orders)

        text = (
            f"📊 <b>Copy Trading Status</b>\n\n"
            f"State: {running}\n"
            f"Master: <code>{master}</code>\n"
            f"Followers: <code>{followers}</code>\n"
            f"Open positions: {positions}\n"
            f"Orders copied: {orders_copied}\n"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show master's open positions."""
        if not self.copy_service:
            await update.message.reply_text("⚠️ Copy service not connected")
            return

        positions = self.copy_service._master_positions
        if not positions:
            await update.message.reply_text("📭 No open positions")
            return

        lines = ["📈 <b>Master Positions</b>\n"]
        for key, pos in positions.items():
            symbol = pos.get("symbol", "?")
            side = "LONG" if pos.get("positionType") == 1 else "SHORT"
            vol = pos.get("holdVol", 0)
            entry = pos.get("openAvgPrice", 0)
            pnl = pos.get("unrealizedPnl", 0)
            emoji = "🟢" if float(pnl) >= 0 else "🔴"
            lines.append(f"{emoji} {symbol} {side} vol={vol} @ {entry} PnL: {pnl}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Pause copy trading."""
        if self.copy_service:
            self.copy_service._running = False
            await update.message.reply_text("⏸ Copy trading paused")
        else:
            await update.message.reply_text("⚠️ Copy service not connected")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Resume copy trading."""
        if self.copy_service and not self.copy_service._running:
            self.copy_service._running = True
            asyncio.create_task(self.copy_service.start())
            await update.message.reply_text("▶️ Copy trading resumed")
        else:
            await update.message.reply_text("Already running or not connected")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help."""
        text = (
            "🤖 <b>MEXC Copy Trading Bot</b>\n\n"
            "<b>Commands:</b>\n"
            "/status — Show current status\n"
            "/positions — Show open positions\n"
            "/stop — Pause copy trading\n"
            "/resume — Resume copy trading\n"
            "/help — This message\n\n"
            "<b>How it works:</b>\n"
            "The bot monitors the master account every 0.5s. "
            "When a new position opens, it copies to all followers "
            "with the configured volume ratio. TP/SL and limit orders "
            "are also copied automatically."
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── Callback Handler ───────────────────────────────────────────

    async def _callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button presses."""
        query = update.callback_query
        await query.answer()

        # Simulate command execution
        fake_update = update
        fake_update.message = query.message

        handlers = {
            "status": self._cmd_status,
            "positions": self._cmd_positions,
            "stop": self._cmd_stop,
            "resume": self._cmd_resume,
        }

        handler = handlers.get(query.data)
        if handler:
            await handler(fake_update, context)
