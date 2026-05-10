"""
东方财富交易适配器 — 通过 easytrader Windows 代理实现实盘交易

架构:
  Linux 后端 --HTTP--> Windows 代理 (eastmoney_agent.py) --easytrader--> 东方财富客户端

使用方式:
  # Windows 端
  pip install easytrader flask
  python scripts/eastmoney_agent.py

  # Linux 后端配置 .env:
  EM_TRADE_AGENT_URL=http://192.168.1.100:8520
  ORDER_EXECUTION_MODE=eastmoney
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.utils.exchange_adapter import ExchangeAdapter

logger = logging.getLogger(__name__)


def _to_raw_code(symbol: str) -> str:
    """系统标的代码 → 东方财富原始代码。600519.SH → 600519"""
    return symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")


class EastMoneyTradeAdapter(ExchangeAdapter):
    """东方财富 easytrader 交易适配器"""

    def __init__(self, agent_url: str = "http://127.0.0.1:8520", timeout: int = 10):
        self._agent_url = agent_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # 行情接口 — 仍走东方财富行情 API（不经过 easytrader）
    # ------------------------------------------------------------------

    async def get_ticker(self, symbol: str) -> Dict:
        from app.services.data_provider import fetch_real_klines
        from app.core.market_constants import BASE_PRICES

        klines = fetch_real_klines(symbol, "1d")
        if klines and len(klines) > 0:
            latest = klines[-1]
            return {
                "symbol": symbol,
                "last_price": latest["close"],
                "bid": latest["close"] * 0.999,
                "ask": latest["close"] * 1.001,
                "volume_24h": latest.get("volume", 0),
            }
        base = BASE_PRICES.get(symbol, 100.0)
        return {"symbol": symbol, "last_price": base}

    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        from app.services.data_provider import fetch_real_klines

        data = fetch_real_klines(symbol, interval)
        if data:
            return data[-limit:]
        return []

    # ------------------------------------------------------------------
    # 交易接口 — 通过 Windows 代理 HTTP API
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """向 Windows 代理发送请求"""
        url = f"{self._agent_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if method == "GET":
                    resp = await client.get(url)
                elif method == "POST":
                    resp = await client.post(url, json=kwargs)
                else:
                    raise ValueError(f"不支持的 HTTP 方法: {method}")
                resp.raise_for_status()
                result = resp.json()
        except httpx.ConnectError:
            raise RuntimeError(f"无法连接交易代理 {self._agent_url}，请确认 Windows 端 eastmoney_agent.py 已启动")
        except httpx.TimeoutException:
            raise RuntimeError(f"交易代理 {self._agent_url} 无响应（超时 {self._timeout}s）")
        except Exception as e:
            raise RuntimeError(f"交易代理请求失败: {e}")

        if not result.get("ok"):
            raise RuntimeError(result.get("error", "交易操作失败"))
        return result.get("data", result)

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> Dict:
        """下单（仅支持限价单）"""
        raw = _to_raw_code(symbol)
        side_cn = "buy" if side in ("buy", "BUY") else "sell"
        return await self._request(
            "POST", "/order",
            symbol=raw,
            side=side_cn,
            price=price or 0,
            amount=int(quantity),
        )

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """撤单"""
        return await self._request("POST", "/cancel", entrust_no=order_id)

    async def get_order(self, symbol: str, order_id: str) -> Dict:
        """查询委托（查找今日委托中匹配的）"""
        data = await self._request("GET", "/orders/today")
        if isinstance(data, list):
            for o in data:
                if str(o.get("entrust_no")) == str(order_id):
                    return o
        return {"status": "not_found"}

    async def get_position(self, symbol: str = "") -> Dict:
        """查询持仓"""
        data = await self._request("GET", "/position")
        raw = _to_raw_code(symbol) if symbol else ""
        if isinstance(data, list) and raw:
            for p in data:
                if str(p.get("stock_code", "")).startswith(raw):
                    return {
                        "symbol": symbol,
                        "quantity": p.get("current_amount", 0),
                        "available": p.get("enable_amount", 0),
                        "cost_price": p.get("cost_price", 0),
                        "market_value": p.get("market_value", 0),
                    }
            return {"symbol": symbol, "quantity": 0, "available": 0}
        return {"positions": data or []}

    async def get_account_info(self) -> Dict:
        """查询账户资金"""
        data = await self._request("GET", "/account")
        if isinstance(data, dict):
            return {
                "total_balance": data.get("balance", 0),
                "available_balance": data.get("available", 0),
                "frozen_balance": data.get("frozen", 0),
                "market_value": data.get("market_value", 0),
            }
        return {"total_balance": 0, "available_balance": 0}
