"""
数据真实性校验模块

职责:
  1. 定义规范的数据来源标识
  2. 数据入库前进行真实性校验
  3. 严格模式下禁止静默降级到 mock
  4. 数据质量有问题时记录日志并标记
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class DataSource(str, Enum):
    """数据来源规范标识"""
    EASTMONEY = "eastmoney"
    SINA = "sina"
    AKSHARE = "akshare"
    BAOSTOCK = "baostock"
    EASTMONEY_SKILLS = "eastmoney_skills"
    MOCK = "mock"
    UNKNOWN = "unknown"


class DiscrepancySeverity(str, Enum):
    """跨源数据差异严重程度"""
    MINOR = "minor"        # < moderate threshold
    MODERATE = "moderate"  # moderate ~ critical
    CRITICAL = "critical"  # >= critical threshold


# 字段类型分类，用于按类型选择差异阈值
PRICE_FIELDS = frozenset({"last_price", "high", "low", "open", "prev_close", "close"})
VOLUME_FIELDS = frozenset({"volume", "amount"})
RATIO_FIELDS = frozenset({"change_pct", "turnover_rate"})

# 真实数据源集合
REAL_SOURCES = frozenset({DataSource.EASTMONEY, DataSource.SINA, DataSource.AKSHARE, DataSource.BAOSTOCK, DataSource.EASTMONEY_SKILLS})


class DataAuthenticityError(Exception):
    """数据真实性校验失败（严格模式）"""
    pass


class MockFallbackBlockedError(DataAuthenticityError):
    """严格模式下拦截了 mock 降级"""
    pass


# ---------------------------------------------------------------------------
# 校验函数
# ---------------------------------------------------------------------------


def validate_price_plausibility(price: float, symbol: str) -> bool:
    """
    检查价格是否在 A 股的合理范围内。

    判定标准:
      - 价格 > 0.01（低于最小变动价位）
      - 价格 < 10000（超过 A 股合理上限）
    """
    if not isinstance(price, (int, float)) or price <= 0:
        logger.warning("%s 价格非正数: %s", symbol, price)
        return False
    if price < 0.01:
        logger.warning("%s 价格过低: %s", symbol, price)
        return False
    if price > 10000:
        logger.warning("%s 价格过高: %s", symbol, price)
        return False
    return True


def validate_kline_ohlc(open_p: float, high: float, low: float, close: float,
                         symbol: str) -> bool:
    """
    校验 OHLC 逻辑一致性。

    High >= max(open, close) 且 Low <= min(open, close)。
    """
    if not (low <= min(open_p, close) and high >= max(open_p, close)):
        logger.warning(
            "%s OHLC 不一致: O=%s H=%s L=%s C=%s",
            symbol, open_p, high, low, close,
        )
        return False
    return True


def validate_quote_completeness(quote: Dict[str, Any]) -> List[str]:
    """
    检查实时行情数据是否缺少关键字段。

    返回警告信息列表（空列表 = 数据完整）。
    """
    warnings = []
    critical = ["last_price"]
    for field in critical:
        if quote.get(field) is None:
            warnings.append(f"缺少关键字段: {field}")

    if quote.get("last_price") is not None:
        symbol = quote.get("symbol", "?")
        if not validate_price_plausibility(float(quote["last_price"]), str(symbol)):
            warnings.append(f"价格不合理: {quote['last_price']}")

    return warnings


def should_allow_mock_fallback(context: str = "") -> bool:
    """
    判断当前配置是否允许降级到 mock 数据。

    严格模式下绝不降级，直接抛出 MockFallbackBlockedError。
    非严格模式下仅在 MARKET_DATA_PROVIDER == "mock" 时允许。
    """
    if settings.DATA_AUTHENTICITY_STRICT:
        raise MockFallbackBlockedError(
            f"严格模式下禁止 mock 降级 (上下文: {context})。"
            f"当前数据源: {settings.MARKET_DATA_PROVIDER}"
        )

    if settings.MARKET_DATA_PROVIDER not in ("mock", ""):
        logger.warning(
            "%s 数据获取失败 — 当前配置为真实数据源 (%s)，不降级到 mock",
            context, settings.MARKET_DATA_PROVIDER,
        )
        return False

    return True


def verify_data_source(source: str, operation: str = "消费数据") -> bool:
    """
    校验数据来源是否可靠。

    如果使用 mock/unknown 数据做真实决策，记录警告日志。
    返回 True 表示数据来自真实来源。
    """
    if source in REAL_SOURCES:
        return True

    logger.warning(
        "在「%s」中使用了来源为 '%s' 的数据，分析结果可能不反映真实市场状况",
        operation, source,
    )
    return False


def validate_cross_source_consistency(
    field: str,
    value_a: float,
    value_b: float,
    source_a: str,
    source_b: str,
) -> Optional[DiscrepancySeverity]:
    """校验两个数据源对同一字段的值是否一致。返回 None 表示在阈值内通过，否则返回差异等级。"""
    if value_a is None or value_b is None:
        return None

    # 计算百分比差异
    denom = max(abs(float(value_a)), abs(float(value_b)), 0.001)
    delta_pct = abs(float(value_a) - float(value_b)) / denom

    # 按字段类型选择阈值
    if field in PRICE_FIELDS:
        threshold = settings.QUOTE_PRICE_DISCREPANCY_THRESHOLD
    elif field in VOLUME_FIELDS:
        threshold = settings.QUOTE_VOLUME_DISCREPANCY_THRESHOLD
    else:
        threshold = settings.QUOTE_PRICE_DISCREPANCY_THRESHOLD  # 默认用价格阈值

    if delta_pct < threshold:
        return None  # 在阈值内，不视为差异

    if delta_pct >= settings.DISCREPANCY_CRITICAL_THRESHOLD:
        return DiscrepancySeverity.CRITICAL
    elif delta_pct >= settings.DISCREPANCY_MODERATE_THRESHOLD:
        return DiscrepancySeverity.MODERATE
    else:
        return DiscrepancySeverity.MINOR
