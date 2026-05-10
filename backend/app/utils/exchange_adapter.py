"""
交易所API适配器 - 可插拔设计，支持多交易所
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class ExchangeAdapter(ABC):
    """交易所适配器抽象基类"""

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Dict:
        """获取实时行情"""
        pass

    @abstractmethod
    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        """获取K线数据"""
        pass

    @abstractmethod
    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None) -> Dict:
        """创建订单"""
        pass

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """撤销订单"""
        pass

    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Dict:
        """查询订单"""
        pass

    @abstractmethod
    async def get_position(self, symbol: str) -> Dict:
        """查询持仓"""
        pass

    @abstractmethod
    async def get_account_info(self) -> Dict:
        """获取账户信息"""
        pass


class MockExchangeAdapter(ExchangeAdapter):
    """模拟交易所适配器 - 用于开发和测试"""

    def __init__(self):
        self._mock_prices: Dict[str, float] = {
            "BTC/USDT": 50000.0,
            "ETH/USDT": 3000.0,
            "AAPL": 180.0,
            "GOOGL": 140.0,
            "000001.SZ": 12.5,
            "600519.SH": 1680.0,
        }
        self._orders: Dict = {}

    async def get_ticker(self, symbol: str) -> Dict:
        import random
        price = self._mock_prices.get(symbol, 100.0)
        change = price * random.uniform(-0.02, 0.02)
        return {
            "symbol": symbol,
            "last_price": price + change,
            "bid": price + change - 0.01,
            "ask": price + change + 0.01,
            "volume_24h": random.uniform(10000, 1000000),
            "change_24h": random.uniform(-5, 5),
        }

    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        import random
        from datetime import datetime, timedelta, timezone
        base_price = self._mock_prices.get(symbol, 100.0)
        klines = []
        for i in range(limit):
            klines.append({
                "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=i * 5)).isoformat(),
                "open": base_price + random.uniform(-2, 2),
                "high": base_price + random.uniform(1, 5),
                "low": base_price + random.uniform(-5, -1),
                "close": base_price + random.uniform(-2, 2),
                "volume": random.uniform(100, 10000),
            })
        return klines

    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None) -> Dict:
        import uuid
        order_id = f"mock_{uuid.uuid4().hex[:12]}"
        self._orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "price": price,
            "status": "filled",
        }
        return {"order_id": order_id, "status": "filled", "filled_quantity": quantity, "avg_price": price or self._mock_prices.get(symbol, 100.0)}

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        return {"order_id": order_id, "status": "canceled"}

    async def get_order(self, symbol: str, order_id: str) -> Dict:
        return self._orders.get(order_id, {"status": "not_found"})

    async def get_position(self, symbol: str) -> Dict:
        return {"symbol": symbol, "quantity": 0, "available": 0, "frozen": 0}

    async def get_account_info(self) -> Dict:
        return {"total_balance": 100000.0, "available_balance": 100000.0, "frozen_balance": 0.0}


class ExchangeAdapterFactory:
    """交易所适配器工厂"""

    _adapters: Dict[str, type] = {
        "mock": MockExchangeAdapter,
        "sandbox": MockExchangeAdapter,
    }

    _adapter_instances: Dict[str, object] = {}

    @classmethod
    def register(cls, name: str, adapter_class: type):
        """注册新的交易所适配器"""
        cls._adapters[name.lower()] = adapter_class

    @classmethod
    def create(cls, exchange: str, **kwargs) -> ExchangeAdapter:
        """创建交易所适配器实例"""
        name = exchange.lower()

        # 单例缓存（避免重复初始化）
        cache_key = f"{name}:{kwargs}"
        if cache_key in cls._adapter_instances:
            return cls._adapter_instances[cache_key]

        if name == "eastmoney":
            from app.utils.eastmoney_trade_adapter import EastMoneyTradeAdapter
            from app.core.config import settings
            adapter = EastMoneyTradeAdapter(
                agent_url=kwargs.get("agent_url", settings.EM_TRADE_AGENT_URL),
                timeout=kwargs.get("timeout", 10),
            )
            cls._adapter_instances[cache_key] = adapter
            return adapter

        adapter_class = cls._adapters.get(name)
        if not adapter_class:
            raise ValueError(f"不支持的交易所: {exchange}，可选: {list(cls._adapters.keys())} + eastmoney")
        adapter = adapter_class(**kwargs)
        cls._adapter_instances[cache_key] = adapter
        return adapter
