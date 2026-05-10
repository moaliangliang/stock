"""
东方财富 HTTP API 客户端 — 实时行情（批量）+ K线数据
"""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

EM_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EM_CLIST_URL = "http://push2.eastmoney.com/api/qt/clist/get"
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

# clist/get 全部 A 股板块筛选（沪深京）
ALL_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# clist/get 行情字段
# f2=最新价 f3=涨跌幅 f5=成交量 f6=成交额 f8=换手率
# f9=动态市盈率 f12=代码 f14=名称 f15=最高 f16=最低 f17=今开 f18=昨收
# f20=总市值 f21=流通市值 f23=市净率 f115=静态市盈率
CLIST_FIELDS = "f2,f3,f5,f6,f8,f9,f12,f14,f15,f16,f17,f18,f20,f21,f23,f115"

# K线周期映射
KLT_MAP = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60,
    "1d": 101, "1w": 102, "1mon": 103,
}

REQUEST_INTERVAL = 0.5
_last_request_time = 0.0

# 批量行情缓存（5s 内复用）
_cached_quotes: Optional[Dict[str, Dict[str, Any]]] = None
_cache_time: float = 0.0
CACHE_TTL = 5.0


def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _symbol_to_secid(symbol: str) -> Optional[str]:
    """将系统标的代码转换为东方财富 secid 格式。"""
    if symbol.endswith(".SH"):
        return "1." + symbol[:-3]
    if symbol.endswith(".SZ"):
        return "0." + symbol[:-3]
    return None


def _secid_to_symbol(secid: str) -> str:
    """东方财富 secid 转回系统标的代码。"""
    market, code = secid.split(".")
    suffix = "SH" if market == "1" else "SZ"
    return f"{code}.{suffix}"


def _format_symbol_em(symbol: str) -> Optional[str]:
    """将系统标的代码转换为东方财富代码（纯数字，无后缀）。"""
    if symbol.endswith(".SH"):
        return symbol[:-3]
    if symbol.endswith(".SZ"):
        return symbol[:-3]
    return None


# ---------------------------------------------------------------------------
# 批量实时行情（clist/get，一次拉取全市场，内存缓存 5s）
# ---------------------------------------------------------------------------


def _fetch_all_batch() -> Optional[Dict[str, Dict[str, Any]]]:
    """从 clist/get 拖取全部 A 股行情，返回 {code: {fields}} 字典。"""
    global _cached_quotes, _cache_time

    now = time.time()
    if _cached_quotes is not None and (now - _cache_time) < CACHE_TTL:
        return _cached_quotes

    _rate_limit()
    headers = {
        "Referer": "http://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    all_items: Dict[str, Dict[str, Any]] = {}
    page = 1
    page_size = 500
    max_retries = 2

    while True:
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": ALL_A_SHARE_FS,
            "fields": CLIST_FIELDS,
        }

        # 每页之间间隔 1.5s，避开东方财富反爬
        time.sleep(1.5)

        data = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.get(EM_CLIST_URL, params=params, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < max_retries:
                    logger.debug("批量行情 p%s 重试 %s/%s: %s", page, attempt + 1, max_retries, e)
                    time.sleep(2)
                else:
                    logger.warning("东方财富批量行情请求失败 (page %s): %s", page, e)

        if data is None:
            break

        diff = data.get("data", {}).get("diff") if data.get("data") else None
        if not diff:
            break

        for item in diff:
            code = item.get("f12", "")
            if not code:
                continue
            all_items[code] = {
                "last_price": item.get("f2"),
                "high": item.get("f15"),
                "low": item.get("f16"),
                "open": item.get("f17"),
                "prev_close": item.get("f18"),
                "volume": item.get("f5"),
                "amount": item.get("f6"),
                "change_pct": item.get("f3"),
                "turnover_rate": item.get("f8"),
                "total_market_value": item.get("f20"),
                "circulating_market_value": item.get("f21"),
                "pe_ratio": item.get("f9"),
                "pe_static": item.get("f115"),
                "pb_ratio": item.get("f23"),
            }

        total = data.get("data", {}).get("total", 0)
        if page * page_size >= total:
            break
        page += 1

    if all_items:
        _cached_quotes = all_items
        _cache_time = now
        logger.info("东方财富批量行情: %s 只股票", len(all_items))

    return all_items or None


def fetch_realtime_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """批量获取实时行情（从全市场缓存中筛选）。
    Args:
        symbols: 标的代码列表（如 ["600519.SH", "000001.SZ"]）
    Returns:
        {symbol: {last_price, high, low, open, volume, amount, change_pct, ...}}
    """
    all_quotes = _fetch_all_batch()
    if not all_quotes:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        code = _format_symbol_em(sym)
        if not code:
            continue
        q = all_quotes.get(code)
        if q and q.get("last_price") is not None:
            result[sym] = q

    return result


# ---------------------------------------------------------------------------
# K 线数据（kline/get，单股查询）
# ---------------------------------------------------------------------------


def fetch_kline(
    symbol: str,
    interval: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    fqt: int = 1,
) -> Optional[List[Dict[str, Any]]]:
    """获取 K 线数据。
    Args:
        symbol: 标的代码（如 "600519.SH"）
        interval: K线周期 (1m/5m/15m/30m/60m/1d/1w/1mon)
        start_date: 开始日期 yyyyMMdd
        end_date: 结束日期 yyyyMMdd
        fqt: 复权类型 0=不复权 1=前复权 2=后复权
    Returns:
        [{timestamp, open, high, low, close, volume, amount}, ...] 或 None
    """
    secid = _symbol_to_secid(symbol)
    if not secid:
        return None

    klt = KLT_MAP.get(interval)
    if klt is None:
        logger.warning("不支持的K线周期: %s", interval)
        return None

    if start_date is None:
        start_date = "20200101"
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    _rate_limit()

    try:
        resp = requests.get(
            EM_KLINE_URL,
            params={
                "secid": secid,
                "klt": klt,
                "fqt": fqt,
                "beg": start_date,
                "end": end_date,
                "lmt": 500,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "fltt": "2",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("东方财富K线请求失败 %s: %s", symbol, e)
        return None

    if not data.get("data") or not data["data"].get("klines"):
        return None

    result = []
    for line in data["data"]["klines"]:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        try:
            ts = datetime.strptime(
                parts[0], "%Y-%m-%d %H:%M" if ":" in parts[0] else "%Y-%m-%d"
            )
        except ValueError:
            ts = datetime.strptime(parts[0], "%Y%m%d")
        result.append({
            "timestamp": ts.replace(tzinfo=timezone.utc),
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
        })

    return result
