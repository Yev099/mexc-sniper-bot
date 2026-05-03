"""Copy Trading Service — monitors master and replicates trades to followers."""

import asyncio
import logging
from typing import Dict, List, Optional, Set

from config import Config, FollowerAccount
from mexc_api import MEXCFuturesClient

log = logging.getLogger("copy-trader")


class CopyTradingService:
    """Core copy trading engine.

    Polls master account positions and pending orders every N seconds.
    When changes are detected, replicates them to all follower accounts.
    """

    def __init__(self, config: Config):
        self.config = config
        self.master = MEXCFuturesClient(
            config.master_api_key, config.master_api_secret, name="master"
        )
        self.followers: List[MEXCFuturesClient] = [
            MEXCFuturesClient(f.api_key, f.api_secret, name=f.name)
            for f in config.followers
        ]
        self.follower_configs: Dict[str, FollowerAccount] = {
            f.name: f for f in config.followers
        }

        # State tracking
        self._master_positions: Dict[str, Dict] = {}  # symbol_side -> position
        self._copied_orders: Set[str] = set()
        self._running = False
        self._notify_callback = None

    def set_notify_callback(self, callback):
        """Set callback for Telegram notifications: callback(message: str)."""
        self._notify_callback = callback

    async def _notify(self, message: str):
        """Send notification via callback (Telegram bot)."""
        if self._notify_callback:
            try:
                await self._notify_callback(message)
            except Exception:
                pass

    # ─── Main Loop ──────────────────────────────────────────────────

    async def start(self):
        """Start the copy trading monitor loop."""
        self._running = True
        log.info("Copy trading started. Polling every %.1fs", self.config.poll_interval)
        await self._notify("✅ Copy trading started")

        while self._running:
            try:
                await self._poll_cycle()
            except Exception as e:
                log.error("Poll cycle error: %s", e)
                await self._notify(f"⚠️ Error: {e}")
            await asyncio.sleep(self.config.poll_interval)

    async def stop(self):
        """Stop the copy trading monitor."""
        self._running = False
        await self._notify("🛑 Copy trading stopped")
        await self.master.close()
        for f in self.followers:
            await f.close()

    async def _poll_cycle(self):
        """Single poll iteration: check master, copy changes."""
        # Fetch master state
        async def _empty():
            return []

        positions, pending_orders = await asyncio.gather(
            self.master.get_open_positions(),
            self.master.get_pending_orders() if self.config.copy_limit_orders else _empty(),
        )

        # Detect new/closed positions
        current_keys = set()
        for pos in positions:
            symbol = pos.get("symbol", "")
            side = pos.get("positionType", 0)  # 1=long, 2=short
            key = f"{symbol}_{side}"
            current_keys.add(key)

            if key not in self._master_positions:
                # New position detected
                await self._on_new_position(pos)
            else:
                # Check for TP/SL changes
                old = self._master_positions[key]
                if self.config.copy_tp_sl:
                    await self._check_tp_sl_change(pos, old)

            self._master_positions[key] = pos

        # Detect closed positions
        closed_keys = set(self._master_positions.keys()) - current_keys
        for key in closed_keys:
            await self._on_position_closed(self._master_positions[key])
            del self._master_positions[key]

        # Copy limit orders
        if self.config.copy_limit_orders:
            await self._copy_pending_orders(pending_orders)

    # ─── Position Events ────────────────────────────────────────────

    async def _on_new_position(self, pos: Dict):
        """Master opened a new position — copy to all followers."""
        symbol = pos.get("symbol", "")
        side = pos.get("positionType", 0)  # 1=long, 2=short
        vol = int(pos.get("holdVol", 0))
        leverage = int(pos.get("leverage", self.config.default_leverage))
        entry = float(pos.get("openAvgPrice", 0))
        tp = pos.get("takeProfitPrice")
        sl = pos.get("stopLossPrice")

        side_label = "LONG" if side == 1 else "SHORT"
        log.info("[NEW] Master opened %s %s x%d vol=%d", symbol, side_label, leverage, vol)
        await self._notify(f"📈 Master: {side_label} {symbol} x{leverage} vol={vol} @ {entry}")

        # Open side: 1=open long, 3=open short
        order_side = 1 if side == 1 else 3

        tasks = []
        for client in self.followers:
            fc = self.follower_configs[client.name]
            adjusted_vol = max(1, round(vol * fc.ratio))
            tasks.append(self._open_follower_position(
                client, symbol, order_side, adjusted_vol, leverage, tp, sl
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for client, result in zip(self.followers, results):
            if isinstance(result, Exception):
                log.error("[COPY] %s failed: %s", client.name, result)

    async def _open_follower_position(
        self,
        client: MEXCFuturesClient,
        symbol: str,
        side: int,
        vol: int,
        leverage: int,
        tp: Optional[float],
        sl: Optional[float],
    ):
        """Open position on follower account + set TP/SL."""
        # Set leverage first
        await client.set_leverage(symbol, leverage)

        # Place market order
        result = await client.place_market_order(symbol, side, vol, leverage)
        if not result.get("success"):
            log.warning("[COPY] %s order failed: %s", client.name, result.get("message"))
            return

        log.info("[COPY] %s opened %s side=%d vol=%d", client.name, symbol, side, vol)
        await self._notify(f"✅ {client.name}: opened {symbol} vol={vol}")

        # Set TP/SL after short delay (MEXC needs time to register position)
        if self.config.copy_tp_sl and (tp or sl):
            await asyncio.sleep(1.5)
            await self._set_follower_tp_sl(client, symbol, side, tp, sl)

    async def _set_follower_tp_sl(
        self,
        client: MEXCFuturesClient,
        symbol: str,
        side: int,
        tp: Optional[float],
        sl: Optional[float],
    ):
        """Set TP/SL on follower position via separate API call."""
        # side in order context: 1=open long, 3=open short → position type: 1=long, 2=short
        pos_type = 1 if side in (1, 2) else 2
        position = await client.get_position_by_symbol(symbol, pos_type)
        if not position:
            log.warning("[TP/SL] %s: position not found for %s", client.name, symbol)
            return

        position_id = position.get("positionId")
        if not position_id:
            return

        tp_val = float(tp) if tp else None
        sl_val = float(sl) if sl else None

        result = await client.set_tp_sl(position_id, tp_val, sl_val)
        if result.get("success"):
            log.info("[TP/SL] %s %s: TP=%s SL=%s", client.name, symbol, tp_val, sl_val)
        else:
            log.warning("[TP/SL] %s %s failed: %s", client.name, symbol, result.get("message"))

    async def _on_position_closed(self, pos: Dict):
        """Master closed a position — close on all followers."""
        symbol = pos.get("symbol", "")
        side = pos.get("positionType", 0)
        side_label = "LONG" if side == 1 else "SHORT"

        log.info("[CLOSE] Master closed %s %s", symbol, side_label)
        await self._notify(f"📉 Master closed {side_label} {symbol}")

        # Close side: 4=close long, 2=close short
        close_side = 4 if side == 1 else 2

        tasks = []
        for client in self.followers:
            tasks.append(self._close_follower_position(client, symbol, close_side, side))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_follower_position(
        self, client: MEXCFuturesClient, symbol: str, close_side: int, pos_type: int
    ):
        """Close a specific position on follower."""
        position = await client.get_position_by_symbol(symbol, pos_type)
        if not position:
            return

        vol = int(position.get("holdVol", 0))
        if vol <= 0:
            return

        result = await client.place_market_order(symbol, close_side, vol)
        if result.get("success"):
            log.info("[CLOSE] %s closed %s vol=%d", client.name, symbol, vol)
            await self._notify(f"✅ {client.name}: closed {symbol}")
        else:
            log.warning("[CLOSE] %s failed: %s", client.name, result.get("message"))

    # ─── TP/SL Sync ────────────────────────────────────────────────

    async def _check_tp_sl_change(self, new_pos: Dict, old_pos: Dict):
        """If master changed TP/SL, update on followers."""
        new_tp = new_pos.get("takeProfitPrice")
        new_sl = new_pos.get("stopLossPrice")
        old_tp = old_pos.get("takeProfitPrice")
        old_sl = old_pos.get("stopLossPrice")

        if new_tp == old_tp and new_sl == old_sl:
            return

        symbol = new_pos.get("symbol", "")
        side = new_pos.get("positionType", 0)
        order_side = 1 if side == 1 else 3

        log.info("[TP/SL] Master updated %s: TP=%s→%s SL=%s→%s", symbol, old_tp, new_tp, old_sl, new_sl)

        tasks = [
            self._set_follower_tp_sl(client, symbol, order_side, new_tp, new_sl)
            for client in self.followers
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ─── Limit Order Copying ───────────────────────────────────────

    async def _copy_pending_orders(self, orders: List[Dict]):
        """Copy new limit orders from master to followers."""
        for order in orders:
            order_id = str(order.get("orderId", ""))
            if not order_id or order_id in self._copied_orders:
                continue

            symbol = order.get("symbol", "")
            side = int(order.get("side", 0))
            vol = int(order.get("vol", 0))
            price = float(order.get("price", 0))
            leverage = int(order.get("leverage", self.config.default_leverage))

            side_label = {1: "open long", 2: "close short", 3: "open short", 4: "close long"}.get(side, "?")
            log.info("[LIMIT] Master placed %s %s vol=%d @ %s", symbol, side_label, vol, price)
            await self._notify(f"📋 Limit: {side_label} {symbol} vol={vol} @ {price}")

            tasks = []
            for client in self.followers:
                fc = self.follower_configs[client.name]
                adjusted_vol = max(1, round(vol * fc.ratio))
                tasks.append(
                    client.place_limit_order(symbol, side, adjusted_vol, price, leverage)
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for client, result in zip(self.followers, results):
                if isinstance(result, Exception):
                    log.error("[LIMIT] %s failed: %s", client.name, result)
                elif isinstance(result, dict) and result.get("success"):
                    log.info("[LIMIT] %s copied order %s", client.name, order_id)

            self._copied_orders.add(order_id)
