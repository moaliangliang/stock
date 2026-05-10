"""
共享常量 — 标的基准价和各周期数据生成天数
"""
# 各周期对应的模拟数据生成天数（短周期生成较少数据）
INTERVAL_DAYS = {
    "1m": 1,
    "5m": 3,
    "15m": 7,
    "30m": 14,
    "60m": 30,
    "1d": 90,
}

INTERVAL_MINUTES = {
    "1d": 1440,
    "60m": 60,
    "30m": 30,
    "15m": 15,
    "5m": 5,
    "1m": 1,
}

# K 线 mock 数据配置
MOCK_CONFIG = {
    "BTC/USDT": {"base_price": 50000.0, "days": 90, "interval_minutes": 60},
    "ETH/USDT": {"base_price": 3000.0, "days": 90, "interval_minutes": 60},
    "AAPL": {"base_price": 180.0, "days": 90, "interval_minutes": 60},
    "600519.SH": {"base_price": 1680.0, "days": 90, "interval_minutes": 60},
    "000001.SZ": {"base_price": 12.5, "days": 90, "interval_minutes": 60},
    "002475.SZ": {"base_price": 36.5, "days": 90, "interval_minutes": 60},
    "002202.SZ": {"base_price": 10.2, "days": 90, "interval_minutes": 60},
    "601633.SH": {"base_price": 28.0, "days": 90, "interval_minutes": 60},
    "600028.SH": {"base_price": 6.8, "days": 90, "interval_minutes": 60},
    "000338.SZ": {"base_price": 13.2, "days": 90, "interval_minutes": 60},
    "002384.SZ": {"base_price": 26.0, "days": 90, "interval_minutes": 60},
    "688256.SH": {"base_price": 500.0, "days": 90, "interval_minutes": 60},
    "688041.SH": {"base_price": 70.0, "days": 90, "interval_minutes": 60},
    "688981.SH": {"base_price": 45.0, "days": 90, "interval_minutes": 60},
    "688008.SH": {"base_price": 80.0, "days": 90, "interval_minutes": 60},
    "688012.SH": {"base_price": 100.0, "days": 90, "interval_minutes": 60},
    "688521.SH": {"base_price": 30.0, "days": 90, "interval_minutes": 60},
    "688498.SH": {"base_price": 180.0, "days": 90, "interval_minutes": 60},
    "688525.SH": {"base_price": 80.0, "days": 90, "interval_minutes": 60},
    "688072.SH": {"base_price": 300.0, "days": 90, "interval_minutes": 60},
    "688347.SH": {"base_price": 28.0, "days": 90, "interval_minutes": 60},
}

# 内置标的基准价（用于实时行情模拟降级）
BASE_PRICES = {k: v["base_price"] for k, v in MOCK_CONFIG.items()}
BASE_PRICES.update({
    "GOOGL": 140.0,
    "TSLA": 250.0,
    "300750.SZ": 200.0,
})
