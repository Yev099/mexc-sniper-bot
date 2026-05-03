"""Copy Trading Service — monitors master and replicates trades to followers.

Supports both API key and Cookie (u_id) authenticated followers.
Master always uses API keys. Followers can use either method.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Set, Union

from config import Config, FollowerAccount
from mexc_api import MEXCCookieClient, MEXCFuturesClient

log = logging.getLogger("copy-trader")

# Type alias for either client type
Client = Union[MEXCFuturesClient, MEXCCookieClient]


def _create_client(follower: FollowerAccount) -> Client:
    """Create the right client based on follower's auth type."""
    if follower.auth_type == "api_key":
        return MEXCFuturesClient(follower.api_key, follower.api_secret, name=follower.name)
    else:
        return MEXCCookieClient(
            u_id=follower.u_id, name=follower.name,
            proxy=follower.proxy, user_agent=follower.user_agent,
        )


class CopyTradingService:
    """Core engine: polls master, replicates to followers (API or cookie auth)."""

    def __init__(self, config: Config):
        self.config = config
        self.master = MEXCFuturesClient(
            config.master_api_key, config.master_api_secret, name="master"
        )
        self.followers: List[Client] = [_create_client(f) for f in config.followers]
        self.follower_ratios: Dict[str, float] = {f.name: f.ratio for f in config.followers}

        self._master_positions: Dict[str, Dict] = {}
        self._copied_orders: Set[str] = set()
        self._running = False
        self._notify_callback = None

    def set_notify_callback(self, callback):
        self._notify_callback = callback

    async def _notify(self, message: str):
        if self._notify_callback:
            try:
                await self._notify_callback(message)
            except Exception:
                pass

    # ─── Main Loop ──────────────────────────────────────────────────

    async def start(self):
        self._running = True
        follower_info = ", ".join(
            f"{c.name}({'cookie' if isinstance(c, MEXCCookieClient) else 'api'})"
            for c in self.followers
        )
        log.info("Copy trading started | %d followers: %s | Poll: %.1fs",
                 len(self.followers), follower_info, self.config.poll_interval)
        await self._notify("✅ Copy trading started")

        while self._running:
            try:
                await self._poll_cycle()
            except Exception as e:
                log.error("Poll error: %s", e)
                await self._notify(f"⚠️ Error: {e}")
            await asyncio.sleep(self.config.poll_interval)

    async def stop(self):
        self._running = False
        await self._notify("🛑 Copy trading stopped")
        await self.master.close()
        for f in self.followers:
            await f.close()

    async def _poll_cycle(self):
        """Single poll: fetch master state, detect changes, replicate."""
        async def _empty():
            return []

        t0 = time.time()

        positions, pending_orders = await asyncio.gather(
            self.master.get_open_positions(),
            self.master.get_pending_orders() if self.config.copy_limit_orders else _empty(),
        )

        # Detect new/changed/closed positions
        current_keys = set()
        for pos in positions:
            symbol = pos.get("symbol", "")
            side = pos.get("positionType", 0)
            key = f"{symbol}_{side}"
            current_keys.add(key)

            if key not in self._master_positions:
                await self._on_new_position(pos)
            elif self.config.copy_tp_sl:
                await self._check_tp_sl_change(pos, self._master_positions[key])

            self._master_positions[key] = pos

        # Detect closed positions
        closed_keys = set(self._master_positions.keys()) - current_keys
        for key in closed_keys:
            await self._on_position_closed(self._master_positions[key])
            del self._master_positions[key]

        # Copy limit orders
        if self.config.copy_limit_orders:
            await self._copy_pending_orders(pending_orders)

        elapsed = (time.time() - t0) * 1000
        if elapsed > 2000:
            log.warning("Poll cycle slow: %.0fms", elapsed)

    # ─── Open Position ──────────────────────────────────────────────

    async def _on_new_position(self, pos: Dict):
        """Master opened — copy to all followers in parallel."""
        symbol = pos.get("symbol", "")
        side = pos.get("positionType", 0)
        vol = int(pos.get("holdVol", 0))
        leverage = int(pos.get("leverage", self.config.default_leverage))
        entry = float(pos.get("openAvgPrice", 0))
        tp = pos.get("takeProfitPrice")
        sl = pos.get("stopLossPrice")

        side_label = "LONG" if side == 1 else "SHORT"
        order_side = 1 if side == 1 else 3

        log.info("[OPEN] Master: %s %s x%d vol=%d @ %s", symbol, side_label, leverage, vol, entry)
        await self._notify(f"📈 Master: {side_label} {symbol} x{leverage} vol={vol} @ {entry}")

        tasks = []
        for client in self.followers:
            ratio = self.follower_ratios.get(client.name, 1.0)
            adj_vol = max(1, round(vol * ratio))
            tasks.append(self._open_follower(client, symbol, order_side, adj_vol, leverage, tp, sl))

        t0 = time.time()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = (time.time() - t0) * 1000

        for client, result in zip(self.followers, results):
            if isinstance(result, Exception):
                log.error("[OPEN] %s failed: %s", client.name, result)

        log.info("[OPEN] %d followers fired in %.0fms", len(tasks), elapsed)

    async def _open_follower(self, client: Client, symbol: str,
                              side: int, vol: int, leverage: int,
                              tp: Optional[float], sl: Optional[float]):
        """Open position on one follower + set TP/SL."""
        # Skip if follower already has this position (avoids duplicates on restart)
        pos_type = 1 if side in (1, 2) else 2
        existing = await client.get_position_by_symbol(symbol, pos_type)
        if existing and int(existing.get("holdVol", 0)) > 0:
            log.info("[SKIP] %s already has %s position", client.name, symbol)
            return

        # Set leverage first
        await client.set_leverage(symbol, leverage)

        # Place market order
        result = await client.place_market_order(symbol, side, vol, leverage)
        if not result.get("success"):
            log.warning("[OPEN] %s: %s", client.name, result.get("message"))
            return

        log.info("[OPEN] %s: %s side=%d vol=%d", client.name, symbol, side, vol)
        await self._notify(f"✅ {client.name}: {symbol} vol={vol}")

        # Set TP/SL via separate call (MEXC ignores inline TP/SL for market orders)
        if self.config.copy_tp_sl and (tp or sl):
            await asyncio.sleep(1.5)
            await self._set_tp_sl(client, symbol, side, tp, sl)

    # ─── Close Position ─────────────────────────────────────────────

    async def _on_position_closed(self, pos: Dict):
        """Master closed — close on all followers in parallel."""
        symbol = pos.get("symbol", "")
        side = pos.get("positionType", 0)
        side_label = "LONG" if side == 1 else "SHORT"
        close_side = 4 if side == 1 else 2

        log.info("[CLOSE] Master: %s %s", symbol, side_label)
        await self._notify(f"📉 Closed {side_label} {symbol}")

        tasks = [self._close_follower(c, symbol, close_side, side) for c in self.followers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for client, r in zip(self.followers, results):
            if isinstance(r, Exception):
                log.error("[CLOSE] %s: %s", client.name, r)

    async def _close_follower(self, client: Client, symbol: str,
                               close_side: int, pos_type: int):
        """Close position on one follower."""
        position = await client.get_position_by_symbol(symbol, pos_type)
        if not position:
            return

        vol = int(position.get("holdVol", 0))
        if vol <= 0:
            return

        result = await client.place_market_order(symbol, close_side, vol)
        if result.get("success"):
            log.info("[CLOSE] %s: %s vol=%d", client.name, symbol, vol)
            await self._notify(f"✅ {client.name}: closed {symbol}")
        else:
            log.warning("[CLOSE] %s: %s", client.name, result.get("message"))

    # ─── TP/SL ──────────────────────────────────────────────────────

    async def _set_tp_sl(self, client: Client, symbol: str,
                          side: int, tp: Optional[float], sl: Optional[float]):
        """Set TP/SL via separate API call (reliable method)."""
        pos_type = 1 if side in (1, 2) else 2
        position = await client.get_position_by_symbol(symbol, pos_type)
        if not position:
            log.warning("[TP/SL] %s: no position for %s", client.name, symbol)
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
            log.warning("[TP/SL] %s %s: %s", client.name, symbol, result.get("message"))

    async def _check_tp_sl_change(self, new: Dict, old: Dict):
        """Master changed TP/SL → sync to followers."""
        new_tp = new.get("takeProfitPrice")
        new_sl = new.get("stopLossPrice")
        if new_tp == old.get("takeProfitPrice") and new_sl == old.get("stopLossPrice"):
            return

        symbol = new.get("symbol", "")
        side = new.get("positionType", 0)
        order_side = 1 if side == 1 else 3

        log.info("[TP/SL] Master: %s TP=%s SL=%s", symbol, new_tp, new_sl)
        tasks = [self._set_tp_sl(c, symbol, order_side, new_tp, new_sl) for c in self.followers]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ─── Limit Orders ───────────────────────────────────────────────

    async def _copy_pending_orders(self, orders: List[Dict]):
        """Copy new limit orders from master to all followers."""
        for order in orders:
            oid = str(order.get("orderId", ""))
            if not oid or oid in self._copied_orders:
                continue

            symbol = order.get("symbol", "")
            side = int(order.get("side", 0))
            vol = int(order.get("vol", 0))
            price = float(order.get("price", 0))
            leverage = int(order.get("leverage", self.config.default_leverage))

            labels = {1: "open long", 2: "close short", 3: "open short", 4: "close long"}
            log.info("[LIMIT] Master: %s %s vol=%d @ %s", symbol, labels.get(side, "?"), vol, price)
            await self._notify(f"📋 Limit: {labels.get(side)} {symbol} vol={vol} @ {price}")

            tasks = []
            for client in self.followers:
                ratio = self.follower_ratios.get(client.name, 1.0)
                adj_vol = max(1, round(vol * ratio))
                tasks.append(client.place_limit_order(symbol, side, adj_vol, price, leverage))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for client, r in zip(self.followers, results):
                if isinstance(r, Exception):
                    log.error("[LIMIT] %s: %s", client.name, r)
                elif isinstance(r, dict) and r.get("success"):
                    log.info("[LIMIT] %s: copied %s", client.name, oid)

            self._copied_orders.add(oid)
