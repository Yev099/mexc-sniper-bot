"""MEXC Futures API client — supports both API keys and Cookie (u_id) authentication."""

import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

BASE_URL = "https://contract.mexc.com"
WEB_URL = "https://futures.mexc.com"


# ════════════════════════════════════════════════════════════════
# API KEY CLIENT (HMAC-SHA256)
# ════════════════════════════════════════════════════════════════

class MEXCFuturesClient:
    """Async client for MEXC Futures using API keys (HMAC-SHA256)."""

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
        ts = int(time.time() * 1000)
        return {
            "Content-Type": "application/json",
            "ApiKey": self.api_key,
            "Request-Time": str(ts),
            "Signature": self._sign(ts, params_str),
        }

    async def _request(self, method: str, path: str, params: Optional[Dict] = None) -> Dict:
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

    # ─── Positions ──────────────────────────────────────────────────

    async def get_open_positions(self) -> List[Dict]:
        data = await self._request("GET", "/api/v1/private/position/open_positions")
        if data.get("success") and data.get("data"):
            positions = data["data"]
            if isinstance(positions, list):
                return positions
        return []

    async def get_position_by_symbol(self, symbol: str, side: int) -> Optional[Dict]:
        """side: 1=LONG, 2=SHORT."""
        positions = await self.get_open_positions()
        for pos in positions:
            if pos.get("symbol") == symbol and pos.get("positionType") == side:
                return pos
        return None

    # ─── Orders ─────────────────────────────────────────────────────

    async def place_market_order(self, symbol: str, side: int, vol: int,
                                  leverage: int = 20, open_type: int = 1) -> Dict:
        """side: 1=open long, 2=close short, 3=open short, 4=close long"""
        return await self._request("POST", "/api/v1/private/order/submit", {
            "symbol": symbol, "price": 0, "vol": vol,
            "side": side, "type": 5, "openType": open_type, "leverage": leverage,
        })

    async def place_limit_order(self, symbol: str, side: int, vol: int,
                                 price: float, leverage: int = 20, open_type: int = 1) -> Dict:
        return await self._request("POST", "/api/v1/private/order/submit", {
            "symbol": symbol, "price": price, "vol": vol,
            "side": side, "type": 1, "openType": open_type, "leverage": leverage,
        })

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        return await self._request("POST", "/api/v1/private/order/cancel", {
            "symbol": symbol, "orderId": order_id,
        })

    async def get_pending_orders(self) -> List[Dict]:
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

    # ─── TP/SL ──────────────────────────────────────────────────────

    async def set_tp_sl(self, position_id: int,
                         take_profit: Optional[float] = None,
                         stop_loss: Optional[float] = None) -> Dict:
        params: Dict[str, Any] = {"positionId": position_id}
        if take_profit is not None:
            params["takeProfitPrice"] = take_profit
        if stop_loss is not None:
            params["stopLossPrice"] = stop_loss
        return await self._request("POST", "/api/v1/private/position/change_tp_sl", params)

    # ─── Leverage ───────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int, open_type: int = 1) -> Dict:
        return await self._request("POST", "/api/v1/private/position/change_leverage", {
            "symbol": symbol, "leverage": leverage, "openType": open_type,
        })

    # ─── Account ────────────────────────────────────────────────────

    async def get_account_assets(self) -> Dict:
        data = await self._request("GET", "/api/v1/private/account/assets")
        if data.get("success"):
            return data.get("data", {})
        return {}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ════════════════════════════════════════════════════════════════
# COOKIE (u_id) CLIENT — no API keys needed
# ════════════════════════════════════════════════════════════════

def _web_sign(u_id: str, body_json: str, timestamp: int) -> tuple:
    """MEXC web signing algorithm (reverse-engineered):
    d = MD5(u_id + timestamp)[7:]
    sign = MD5(timestamp + bodyJSON + d)
    """
    ts_str = str(timestamp)
    d = hashlib.md5((u_id + ts_str).encode()).hexdigest()[7:]
    sign = hashlib.md5((ts_str + body_json + d).encode()).hexdigest()
    return sign, ts_str


class MEXCCookieClient:
    """Async client for MEXC Futures using cookie/u_id authentication.

    This is an alternative to API keys. You get u_id from your browser's
    Authorization header when logged into MEXC futures.

    How to get u_id:
    1. Open mexc.com → Futures → open DevTools (F12)
    2. Go to Network tab → filter by "futures.mexc.com"
    3. Find any request → look at Headers → "Authorization" value
    4. That's your u_id
    """

    def __init__(self, u_id: str, name: str = "account",
                 proxy: Optional[str] = None, user_agent: Optional[str] = None):
        self.u_id = u_id
        self.name = name
        self.proxy = proxy
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    def _parse_proxy(self) -> Optional[str]:
        """Parse proxy string to aiohttp-compatible URL."""
        if not self.proxy:
            return None
        raw = self.proxy.strip()
        if "@" in raw:
            if not raw.startswith(("http://", "socks5://", "https://")):
                raw = "http://" + raw
            return raw
        parts = raw.replace("http://", "").replace("https://", "").split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            return f"http://{user}:{pwd}@{ip}:{port}"
        elif len(parts) == 2:
            return f"http://{parts[0]}:{parts[1]}"
        return None

    async def _request(self, method: str, path: str, payload: Optional[Dict] = None) -> Dict:
        url = f"{WEB_URL}{path}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
            "Authorization": self.u_id,
            "Origin": "https://www.mexc.com",
            "Referer": "https://www.mexc.com/",
        }

        body_str = json.dumps(payload) if payload else ""

        if method.upper() == "POST" and body_str:
            ts = int(time.time() * 1000)
            sign, nonce = _web_sign(self.u_id, body_str, ts)
            headers["x-mxc-sign"] = sign
            headers["x-mxc-nonce"] = nonce

        proxy_url = self._parse_proxy()
        session = await self._get_session()

        try:
            if method.upper() == "GET":
                qs = urlencode(payload) if payload else ""
                full_url = f"{url}?{qs}" if qs else url
                async with session.get(full_url, headers=headers, proxy=proxy_url) as resp:
                    return await resp.json(content_type=None)
            else:
                async with session.post(url, headers=headers, data=body_str, proxy=proxy_url) as resp:
                    return await resp.json(content_type=None)
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ─── Positions ──────────────────────────────────────────────────

    async def get_open_positions(self) -> List[Dict]:
        data = await self._request("GET", "/api/v1/private/position/open_positions")
        if data.get("success") and data.get("data"):
            positions = data["data"]
            if isinstance(positions, list):
                return positions
        return []

    async def get_position_by_symbol(self, symbol: str, side: int) -> Optional[Dict]:
        positions = await self.get_open_positions()
        for pos in positions:
            if pos.get("symbol") == symbol and pos.get("positionType") == side:
                return pos
        return None

    # ─── Orders ─────────────────────────────────────────────────────

    async def place_market_order(self, symbol: str, side: int, vol: int,
                                  leverage: int = 20, open_type: int = 1) -> Dict:
        return await self._request("POST", "/api/v1/private/order/submit", {
            "symbol": symbol, "price": 0, "vol": vol,
            "side": side, "type": 5, "openType": open_type, "leverage": leverage,
        })

    async def place_limit_order(self, symbol: str, side: int, vol: int,
                                 price: float, leverage: int = 20, open_type: int = 1) -> Dict:
        return await self._request("POST", "/api/v1/private/order/submit", {
            "symbol": symbol, "price": price, "vol": vol,
            "side": side, "type": 1, "openType": open_type, "leverage": leverage,
        })

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        return await self._request("POST", "/api/v1/private/order/cancel", {
            "symbol": symbol, "orderId": order_id,
        })

    async def get_pending_orders(self) -> List[Dict]:
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

    # ─── TP/SL ──────────────────────────────────────────────────────

    async def set_tp_sl(self, position_id: int,
                         take_profit: Optional[float] = None,
                         stop_loss: Optional[float] = None) -> Dict:
        params: Dict[str, Any] = {"positionId": position_id}
        if take_profit is not None:
            params["takeProfitPrice"] = take_profit
        if stop_loss is not None:
            params["stopLossPrice"] = stop_loss
        return await self._request("POST", "/api/v1/private/position/change_tp_sl", params)

    # ─── Leverage ───────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int, open_type: int = 1) -> Dict:
        return await self._request("POST", "/api/v1/private/position/change_leverage", {
            "symbol": symbol, "leverage": leverage, "openType": open_type,
        })

    # ─── Account ────────────────────────────────────────────────────

    async def get_account_assets(self) -> Dict:
        data = await self._request("GET", "/api/v1/private/account/assets")
        if data.get("success"):
            return data.get("data", {})
        return {}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
