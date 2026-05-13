"""
Market data service - K-line query/cache, ticker, and symbol management.
"""
import json
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# A-share daily price move limits
_DAILY_LIMIT: Dict[str, float] = {
    "sh": 0.10,   # Shanghai main board
    "sz": 0.10,   # Shenzhen main board
}
# ChiNext (300xxx) and STAR (688xxx) have ±20% limits
_EXTENDED_LIMIT_PREFIXES = ("300", "301", "688")

# Max single-day price change we'll accept before flagging (25% — above any A-share limit)
_MAX_PRICE_CHANGE = 0.25

def _max_daily_change(symbol: str) -> float:
    """Return max allowed single-day price change for the given symbol."""
    code = symbol.split(".")[0] if "." in symbol else symbol
    if code.startswith(_EXTENDED_LIMIT_PREFIXES):
        return 0.20
    return 0.10


def _validate_kline_bar(item: Dict[str, Any], symbol: str) -> Optional[str]:
    """Validate a single kline bar. Returns an error message string or None."""
    o = float(item.get("open", 0))
    h = float(item.get("high", 0))
    l = float(item.get("low", 0))
    c = float(item.get("close", 0))

    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return f"非正价格 O={o} H={h} L={l} C={c}"
    if l > h:
        return f"Low({l}) > High({h})"
    if o < l or o > h:
        return f"Open({o}) 不在 [L={l}, H={h}]"
    if c < l or c > h:
        return f"Close({c}) 不在 [L={l}, H={h}]"
    return None


def _validate_kline_continuity(
    data_list: List[Dict[str, Any]], symbol: str
) -> List[str]:
    """Check for unrealistic price jumps between consecutive bars. Returns warnings."""
    warnings = []
    if len(data_list) < 2:
        return warnings

    max_pct = _MAX_PRICE_CHANGE
    for i in range(1, len(data_list)):
        prev_c = float(data_list[i - 1].get("close", 0))
        curr_o = float(data_list[i].get("open", 0))
        curr_c = float(data_list[i].get("close", 0))
        if prev_c <= 0 or curr_o <= 0:
            continue

        open_chg = abs(curr_o - prev_c) / prev_c
        close_chg = abs(curr_c - prev_c) / prev_c
        if open_chg > max_pct:
            ts = data_list[i].get("timestamp", "?")
            warnings.append(
                f"{symbol} {ts}: 开盘价 {curr_o:.2f} 较前收 {prev_c:.2f} 变动 {open_chg*100:.0f}% > {max_pct*100:.0f}%"
            )
        elif close_chg > max_pct:
            ts = data_list[i].get("timestamp", "?")
            warnings.append(
                f"{symbol} {ts}: 收盘价 {curr_c:.2f} 较前收 {prev_c:.2f} 变动 {close_chg*100:.0f}% > {max_pct*100:.0f}%"
            )
    return warnings

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import cache_kline, get_cached_kline
from app.models.market_data import KLine, SymbolInfo, Ticker


async def get_kline_data(
    db: AsyncSession,
    symbol: str,
    interval: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 500,
) -> List[KLine]:
    """
    Query kline (candlestick) data with optional time range filtering.

    Results are ordered by timestamp ascending.

    Args:
        db: Database session.
        symbol: Trading pair / symbol code.
        interval: Kline interval (e.g. 1m, 5m, 15m, 30m, 60m, 1d).
        start_time: Earliest timestamp to include (inclusive).
        end_time: Latest timestamp to include (inclusive).
        limit: Maximum number of records to return.

    Returns:
        A list of KLine objects.
    """
    # Try cached data first (gracefully handle Redis unavailable)
    try:
        cached = await get_cached_kline(symbol, interval)
        if cached and isinstance(cached, list) and len(cached) > 0:
            return _deserialize_kline_cache(cached, symbol, interval, start_time, end_time, limit)
    except Exception:
        pass  # Redis不可用时直接查数据库

    # Get the most recent N records, sorted ascending for chronological analysis
    subq = select(KLine).where(
        and_(KLine.symbol == symbol, KLine.interval == interval)
    )

    if start_time is not None:
        subq = subq.where(KLine.timestamp >= start_time)
    if end_time is not None:
        subq = subq.where(KLine.timestamp <= end_time)

    subq = subq.order_by(KLine.timestamp.desc()).limit(limit).subquery()

    query = select(KLine).where(
        KLine.id.in_(select(subq.c.id))
    ).order_by(KLine.timestamp.asc())

    result = await db.execute(query)
    rows = list(result.scalars().all())

    return rows


async def save_kline_data(
    db: AsyncSession, symbol: str, interval: str, data_list: List[Dict[str, Any]]
) -> int:
    """
    Batch save kline data. Existing records (matching symbol + interval + timestamp)
    are skipped.

    Args:
        db: Database session.
        symbol: Trading pair / symbol code.
        interval: Kline interval.
        data_list: List of dicts with keys:
            timestamp (datetime or int), open, high, low, close, volume, amount.

    Returns:
        Number of records inserted.
    """
    # 一次查询所有已存在的时间戳
    result = await db.execute(
        select(KLine.timestamp).where(
            KLine.symbol == symbol,
            KLine.interval == interval,
        )
    )
    existing_timestamps = {row[0] for row in result.all()}

    # Normalize existing timestamps: SQLite stores naive datetimes, but fetched
    # klines may have tzinfo. Strip tzinfo for correct dedup comparison.
    existing_naive = {t.replace(tzinfo=None) if t and t.tzinfo else t for t in existing_timestamps}

    saved = 0
    batch = []
    error_count = 0
    for item in data_list:
        # Validate bar integrity
        err = _validate_kline_bar(item, symbol)
        if err:
            error_count += 1
            if error_count <= 3:  # Log first few only
                logger.warning("K线数据异常 %s: %s", symbol, err)
            continue

        ts = item.get("timestamp")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc)

        ts_naive = ts.replace(tzinfo=None) if ts and ts.tzinfo else ts

        if ts_naive in existing_naive:
            continue

        batch.append(KLine(
            symbol=symbol,
            interval=interval,
            timestamp=ts,
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item.get("volume", 0)),
            amount=float(item.get("amount", 0)),
            data_source=item.get("data_source", "unknown"),
        ))
        saved += 1

        if saved % 50 == 0:
            db.add_all(batch)
            await db.flush()
            batch = []

    if batch:
        db.add_all(batch)
        await db.flush()

    # Continuity check: warn about unrealistic price jumps (non-blocking)
    valid_items = [it for it in data_list if _validate_kline_bar(it, symbol) is None]
    continuity_warnings = _validate_kline_continuity(valid_items, symbol)
    for w in continuity_warnings[:5]:
        logger.warning("K线价格跳变: %s", w)

    if error_count > 0:
        logger.warning("%s: %d/%d 条K线校验不通过，已跳过", symbol, error_count, len(data_list))

    return saved


def save_kline_data_sync(db, symbol: str, interval: str, data_list: List[Dict[str, Any]]) -> int:
    """Synchronous version of save_kline_data for Celery tasks."""
    from datetime import datetime as dt
    result = db.execute(
        select(KLine.timestamp).where(
            KLine.symbol == symbol,
            KLine.interval == interval,
        )
    )
    existing_timestamps = {row[0] for row in result.all()}

    saved = 0
    batch = []
    # Normalize existing timestamps: SQLite stores naive datetimes, but fetched
    # klines may have tzinfo. Strip tzinfo for correct dedup comparison.
    existing_naive = {t.replace(tzinfo=None) if t and t.tzinfo else t for t in existing_timestamps}
    for item in data_list:
        ts = item.get("timestamp")
        if isinstance(ts, (int, float)):
            ts = dt.fromtimestamp(ts, tz=timezone.utc)

        ts_naive = ts.replace(tzinfo=None) if ts and ts.tzinfo else ts
        if ts_naive in existing_naive:
            continue

        batch.append(KLine(
            symbol=symbol,
            interval=interval,
            timestamp=ts,
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item.get("volume", 0)),
            amount=float(item.get("amount", 0)),
            data_source=item.get("data_source", "unknown"),
        ))
        saved += 1

        if saved % 50 == 0:
            db.add_all(batch)
            db.flush()
            batch = []

    if batch:
        db.add_all(batch)
        db.flush()

    return saved


async def get_ticker(db: AsyncSession, symbol: str) -> Optional[Ticker]:
    """
    Get the latest ticker for a symbol.

    Args:
        db: Database session.
        symbol: Trading pair / symbol code.

    Returns:
        A Ticker object if found, None otherwise.
    """
    result = await db.execute(
        select(Ticker).where(Ticker.symbol == symbol)
    )
    return result.scalar_one_or_none()


async def get_all_tickers(db: AsyncSession, offset: int = 0, limit: int | None = None) -> List[Ticker]:
    """
    Get all latest tickers with optional pagination.

    Args:
        db: Database session.
        offset: Number of rows to skip (default 0).
        limit: Maximum rows to return (None = all).

    Returns:
        A list of Ticker objects.
    """
    query = select(Ticker).order_by(Ticker.symbol)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_symbols(
    db: AsyncSession, asset_type: Optional[str] = None,
    offset: int = 0, limit: int | None = None,
) -> List[SymbolInfo]:
    """
    Get the list of available trading symbols, optionally filtered by asset type.

    Args:
        db: Database session.
        asset_type: Optional filter (e.g. "stock", "crypto", "future", "forex").
        offset: Number of rows to skip (default 0).
        limit: Maximum rows to return (None = all).

    Returns:
        A list of SymbolInfo objects.
    """
    query = select(SymbolInfo).where(SymbolInfo.status == "active")
    if asset_type:
        query = query.where(SymbolInfo.asset_type == asset_type)
    query = query.order_by(SymbolInfo.symbol)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


async def ensure_mock_tickers(db: AsyncSession):
    """如果没有行情数据，仅在 mock 模式下为所有活跃标的生成模拟行情"""
    result = await db.execute(select(func.count(Ticker.id)))
    if result.scalar() > 0:
        return

    from app.core.config import settings
    if settings.MARKET_DATA_PROVIDER not in ("mock", ""):
        import logging
        logging.getLogger(__name__).warning(
            "无行情数据且 MARKET_DATA_PROVIDER=%s 非 mock 模式，跳过模拟行情生成",
            settings.MARKET_DATA_PROVIDER,
        )
        return

    # 内置各标的基准价
    _BASE_PRICES = {
        "BTC/USDT": 50000.0, "ETH/USDT": 3000.0,
        "AAPL": 180.0, "GOOGL": 140.0, "TSLA": 250.0,
        "600519.SH": 1680.0, "000001.SZ": 12.5, "300750.SZ": 200.0,
        "002475.SZ": 36.5, "002202.SZ": 10.2, "601633.SH": 28.0, "600028.SH": 6.8,
    }

    symbols = await get_symbols(db)
    tickers = []
    for sym in symbols:
        base_price = _BASE_PRICES.get(sym.symbol, 100.0)
        change = round(random.uniform(-5, 5), 2)
        high_24h = round(base_price * random.uniform(1.01, 1.08), 2)
        low_24h = round(base_price * random.uniform(0.92, 0.99), 2)
        tickers.append(Ticker(
            symbol=sym.symbol,
            last_price=base_price,
            bid=round(base_price * 0.999, 2),
            ask=round(base_price * 1.001, 2),
            bid_volume=random.uniform(100, 10000),
            ask_volume=random.uniform(100, 10000),
            high_24h=high_24h,
            low_24h=low_24h,
            volume_24h=random.uniform(100000, 10000000),
            change_24h=change,
            turnover_24h=round(random.uniform(1000000, 100000000), 2),
            data_source="mock",
        ))
    db.add_all(tickers)
    await db.flush()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _deserialize_kline_cache(
    cached: list,
    symbol: str,
    interval: str,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    limit: int,
) -> List[KLine]:
    """Rebuild KLine objects from cached dicts with optional filtering."""
    rows: List[KLine] = []
    for item in cached:
        ts = datetime.fromtimestamp(item["t"], tz=timezone.utc)
        if start_time and ts < start_time:
            continue
        if end_time and ts > end_time:
            continue
        # Build a lightweight KLine-like object for the cache hit
        k = KLine(
            symbol=symbol,
            interval=interval,
            timestamp=ts,
            open=item["o"],
            high=item["h"],
            low=item["l"],
            close=item["c"],
            volume=item.get("v", 0),
        )
        rows.append(k)
        if len(rows) >= limit:
            break
    return rows


# ---------------------------------------------------------------------------
# Mock data helpers (for testing / development)
# ---------------------------------------------------------------------------

def mock_market_data(
    base_price: float = 50000.0,
    days: int = 365,
    interval_minutes: int = 60,
    volatility: float = 0.02,
    start_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Generate mock kline data for testing purposes.

    The mock data simulates a random-walk price series with realistic OHLC values.

    Args:
        base_price: Starting price.
        days: Number of days of data to generate.
        interval_minutes: Candle interval in minutes.
        volatility: Daily volatility factor.
        start_date: Start date (defaults to *days* ago from now).

    Returns:
        A list of dicts with keys: timestamp, open, high, low, close, volume.
    """
    now = datetime.now(timezone.utc)
    interval_delta = timedelta(minutes=interval_minutes)
    # 计算 K 线数量，覆盖从 days 天前到现在（含当前区间）
    total_bars = int(math.ceil(days * 24 * 60 / interval_minutes)) + 1
    # 从结束时间倒推开始时间，确保最后一条 K 线覆盖到当前时间
    if start_date is None:
        start_date = now - timedelta(minutes=(total_bars - 1) * interval_minutes)
    data: List[Dict[str, Any]] = []

    price = base_price
    current_ts = start_date

    for _ in range(total_bars):
        # Random walk next close
        change_pct = random.gauss(0, volatility * math.sqrt(interval_minutes / (24 * 60)))
        next_price = price * (1 + change_pct)

        # Build OHLC from open/close
        high_price = max(price, next_price) * (1 + abs(random.gauss(0, volatility * 0.3)))
        low_price = min(price, next_price) * (1 - abs(random.gauss(0, volatility * 0.3)))

        volume = random.uniform(100, 10000) * (1 + abs(change_pct) * 10)
        amount = volume * (price + next_price) / 2

        data.append({
            "timestamp": int(current_ts.timestamp()),
            "open": round(price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(next_price, 2),
            "volume": round(volume, 4),
            "amount": round(amount, 2),
            "data_source": "mock",
        })

        price = next_price
        current_ts += interval_delta

    return data
