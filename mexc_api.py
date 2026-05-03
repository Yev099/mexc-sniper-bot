"""MEXC Futures API client with HMAC-SHA256 signing."""

import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

BASE_URL = "https://contract.mexc.com"


class MEXCFuturesClient:
    """Async client for MEXC Futures API (contract.mexc.com)."""

    def __init__(self, api_key: str, api_secret: str, name: str = "account"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.name = name
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    def _sign(self, timestamp: int, params_str: str = "") -> str:
        """HMAC-SHA256: sign(api_key + timestamp + params_string)."""
        message = f"{self.api_key}{timestamp}{params_str}"
        return hmac.HMAC(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, params_str: str = "") -> Dict[str, str]:
        """Build authenticated headers."""
        ts = int(time.time() * 1000)
        return {
            "Content-Type": "application/json",
            "ApiKey": self.api_key,
            "Request-Time": str(ts),
            "Signature": self._sign(ts, params_str),
        }

    async def _request(self, method: str, path: str, params: Optional[Dict] = None) -> Dict:
        """Make signed API request."""
        params = params or {}
        url = f"{BASE_URL}{path}"
        session = await self._get_session()

        try:
            if method == "GET":
                qs = urlencode(params) if params else ""
                headers = self._headers(qs)
                full_url = f"{url}?{qs}" if qs else url
                async with session.get(full_url, headers=headers) as resp:
                    return await resp.json(content_type=None)
            else:
                body = json.dumps(params) if params else ""
                headers = self._headers(body)
                async with session.post(url, headers=headers, data=body) as resp:
                    return await resp.json(content_type=None)
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ─── Position Methods ───────────────────────────────────────────

    async def get_open_positions(self) -> List[Dict]:
        """Get all open positions."""
        data = await self._request("GET", "/api/v1/private/position/open_positions")
        if data.get("success") and data.get("data"):
            positions = data["data"]
            if isinstance(positions, list):
                return positions
        return []

    async def get_position_by_symbol(self, symbol: str, side: int) -> Optional[Dict]:
        """Get specific position. side: 1=LONG, 2=SHORT."""
        positions = await self.get_open_positions()
        for pos in positions:
            if pos.get("symbol") == symbol and pos.get("positionType") == side:
                return pos
        return None

    # ─── Order Methods ─────────────────────────────���────────────────

    async def place_market_order(
        self,
        symbol: str,
        side: int,
        vol: int,
        leverage: int = 20,
        open_type: int = 1,
    ) -> Dict:
        """Place market order.
        side: 1=open long, 2=close short, 3=open short, 4=close long
        open_type: 1=isolated, 2=cross
        """
        params = {
            "symbol": symbol,
            "price": 0,
            "vol": vol,
            "side": side,
            "type": 5,  # market order
            "openType": open_type,
            "leverage": leverage,
        }
        return await self._request("POST", "/api/v1/private/order/submit", params)

    async def place_limit_order(
        self,
        symbol: str,
        side: int,
        vol: int,
        price: float,
        leverage: int = 20,
        open_type: int = 1,
    ) -> Dict:
        """Place limit order.
        side: 1=open long, 2=close short, 3=open short, 4=close long
        """
        params = {
            "symbol": symbol,
            "price": price,
            "vol": vol,
            "side": side,
            "type": 1,  # limit order
            "openType": open_type,
            "leverage": leverage,
        }
        return await self._request("POST", "/api/v1/private/order/submit", params)

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """Cancel a pending order."""
        params = {"symbol": symbol, "orderId": order_id}
        return await self._request("POST", "/api/v1/private/order/cancel", params)

    async def get_pending_orders(self) -> List[Dict]:
        """Get all open/pending orders."""
        data = await self._request("GET", "/api/v1/private/order/list/open_orders", {
            "page_num": 1, "page_size": 50,
        })
        if data.get("success") and data.get("data"):
            raw = data["data"]
            if isinstance(raw, list):
                return raw
            if isinstance(raw, dict) and isinstance(raw.get("data"), list):
                return raw["data"]
        return []

    # ─── TP/SL Methods ──────────────────────────────���───────────────

    async def set_tp_sl(
        self,
        position_id: int,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Dict:
        """Set take profit and/or stop loss on a position."""
        params: Dict[str, Any] = {"positionId": position_id}
        if take_profit is not None:
            params["takeProfitPrice"] = take_profit
        if stop_loss is not None:
            params["stopLossPrice"] = stop_loss
        return await self._request("POST", "/api/v1/private/position/change_tp_sl", params)

    # ─── Leverage ────────────────────────��──────────────────────────

    async def set_leverage(self, symbol: str, leverage: int, open_type: int = 1) -> Dict:
        """Set leverage for a symbol. open_type: 1=isolated, 2=cross."""
        params = {
            "symbol": symbol,
            "leverage": leverage,
            "openType": open_type,
        }
        return await self._request("POST", "/api/v1/private/position/change_leverage", params)

    # ─── Account Info ───────────────────────���───────────────────────

    async def get_account_assets(self) -> Dict:
        """Get futures account balance/assets."""
        data = await self._request("GET", "/api/v1/private/account/assets")
        if data.get("success"):
            return data.get("data", {})
        return {}

    # ─── Cleanup ────────────────────────────���───────────────────────

    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
