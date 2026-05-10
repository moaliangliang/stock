"""
新浪财经 HTTP API 客户端 — 实时行情 + K线数据
"""
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SINA_QUOTE_URL = "https://hq.sinajs.cn/list={codes}"
SINA_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)

# 周期 → 新浪 scale 参数映射
SCALE_MAP = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1d": 240,
}

REQUEST_INTERVAL = 0.3
_last_request_time = 0.0


def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _symbol_to_sina(symbol: str) -> Optional[str]:
    """将 .SH/.SZ 代码转为新浪格式 sh600028 / sz000001"""
    if symbol.endswith(".SH"):
        return "sh" + symbol[:-3]
    if symbol.endswith(".SZ"):
        return "sz" + symbol[:-3]
    return None


def _sina_to_symbol(code: str) -> Optional[str]:
    """新浪 sh600028 → 600028.SH"""
    m = re.match(r"(sh|sz)(\d{6})", code, re.IGNORECASE)
    if not m:
        return None
    suffix = "SH" if m.group(1).lower() == "sh" else "SZ"
    return f"{m.group(2)}.{suffix}"


# ---------------------------------------------------------------------------
# 实时行情
# ---------------------------------------------------------------------------

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def fetch_realtime_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """批量获取实时行情。
    Returns:
        {symbol: {last_price, high, low, open, volume, amount, change_pct, ...}}
    """
    mapping: Dict[str, str] = {}
    for sym in symbols:
        code = _symbol_to_sina(sym)
        if code:
            mapping[code] = sym

    if not mapping:
        return {}

    _rate_limit()

    url = SINA_QUOTE_URL.format(codes=",".join(mapping.keys()))
    try:
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "gbk"
        text = resp.text
    except Exception as e:
        logger.warning("新浪实时行情请求失败: %s", e)
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        m = re.match(r'var hq_str_(\w+)="(.*)"', line.strip())
        if not m:
            continue
        code, values = m.group(1), m.group(2)
        sym = _sina_to_symbol(code)
        if not sym:
            continue
        parts = values.split(",")
        if len(parts) < 10:
            continue
        try:
            result[sym] = {
                "name": parts[0],
                "open": _float(parts[1]),
                "prev_close": _float(parts[2]),
                "last_price": _float(parts[3]),
                "high": _float(parts[4]),
                "low": _float(parts[5]),
                "bid": _float(parts[6]),
                "ask": _float(parts[7]),
                "volume": _float(parts[8]),
                "amount": _float(parts[9]),
                "change_pct": round(
                    (_float(parts[3]) - _float(parts[2])) / _float(parts[2]) * 100, 2
                ) if _float(parts[2]) else 0,
            }
        except (ValueError, IndexError):
            continue

    return result


# ---------------------------------------------------------------------------
# K 线数据
# ---------------------------------------------------------------------------


def fetch_kline(
    symbol: str,
    interval: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    fqt: int = 1,
) -> Optional[List[Dict[str, Any]]]:
    """获取日 K 线数据（新浪仅稳定支持日线）。

    Args:
        symbol: 系统标的代码 "600028.SH"
        interval: K线周期，仅 "1d" 有完整支持
        start_date: yyyyMMdd
        end_date: yyyyMMdd
        fqt: 新浪忽略（默认前复权）

    Returns:
        [{timestamp, open, high, low, close, volume, amount}, ...]
    """
    code = _symbol_to_sina(symbol)
    if not code:
        return None

    scale = SCALE_MAP.get(interval, 240)

    _rate_limit()

    try:
        resp = requests.get(
            SINA_KLINE_URL,
            params={
                "symbol": code,
                "scale": scale,
                "ma": "no",
                "datalen": 2000,
            },
            headers=SINA_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("新浪K线请求失败 %s: %s", symbol, e)
        return None

    if not data or not isinstance(data, list):
        return None

    # 按日期过滤
    if start_date:
        start_dt = _parse_date(start_date)
        data = [d for d in data if _parse_date(d.get("day", "")) >= start_dt]
    if end_date:
        end_dt = _parse_date(end_date)
        data = [d for d in data if _parse_date(d.get("day", "")) <= end_dt]

    result = []
    for item in data:
        day = item.get("day", "")
        try:
            ts = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        result.append({
            "timestamp": ts,
            "open": _float(item.get("open")),
            "close": _float(item.get("close")),
            "high": _float(item.get("high")),
            "low": _float(item.get("low")),
            "volume": _float(item.get("volume")),
            "amount": _float(item.get("amount", 0)),
        })

    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(s: str) -> datetime:
    s = s.strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.min
