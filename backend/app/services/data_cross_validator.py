"""
多渠道数据交叉校验引擎

从东方财富和新浪财经同时获取行情数据，对比重叠字段，
将差异记录到 SystemLog 表，用于数据准确性监控。
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.log import LogCategory, LogLevel, SystemLog
from app.services.data_authenticity import (
    DiscrepancySeverity,
    PRICE_FIELDS,
    VOLUME_FIELDS,
    validate_cross_source_consistency,
)

logger = logging.getLogger(__name__)

# 行情对比的重叠字段（两个源都有的字段）
_QUOTE_OVERLAP_FIELDS = ["last_price", "high", "low", "open", "prev_close", "volume", "amount", "change_pct"]

# K线对比字段
_KLINE_FIELDS = ["open", "close", "high", "low", "volume", "amount"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FieldDiscrepancy:
    """单字段差异"""
    field: str
    value_a: float
    value_b: float
    source_a: str
    source_b: str
    delta_pct: float
    severity: DiscrepancySeverity


@dataclass
class BatchSummary:
    """批量校验汇总"""
    total_checked: int = 0
    symbols_with_discrepancies: int = 0
    total_discrepancies: int = 0
    minor: int = 0
    moderate: int = 0
    critical: int = 0
    symbols_missing_secondary: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_delta_pct(value_a: float, value_b: float) -> float:
    """计算两个值的绝对百分比差异，处理零值和 None。"""
    if value_a is None or value_b is None:
        return 0.0
    a, b = float(value_a), float(value_b)
    if a == 0 and b == 0:
        return 0.0
    denom = max(abs(a), abs(b), 0.001)
    return abs(a - b) / denom


def classify_discrepancy(field: str, delta_pct: float) -> DiscrepancySeverity:
    """按字段类型和阈值对差异分级。"""
    if field in PRICE_FIELDS:
        threshold = settings.QUOTE_PRICE_DISCREPANCY_THRESHOLD
    elif field in VOLUME_FIELDS:
        threshold = settings.QUOTE_VOLUME_DISCREPANCY_THRESHOLD
    else:
        threshold = settings.QUOTE_PRICE_DISCREPANCY_THRESHOLD

    if delta_pct < threshold:
        return DiscrepancySeverity.MINOR

    if delta_pct >= settings.DISCREPANCY_CRITICAL_THRESHOLD:
        return DiscrepancySeverity.CRITICAL
    elif delta_pct >= settings.DISCREPANCY_MODERATE_THRESHOLD:
        return DiscrepancySeverity.MODERATE
    return DiscrepancySeverity.MINOR


def write_system_log_sync(
    level: LogLevel,
    title: str,
    content: Optional[str] = None,
    category: LogCategory = LogCategory.MARKET,
    source: str = "cross_validator",
) -> None:
    """用独立 Session 写入 SystemLog，不影响调用方事务。"""
    sdb = SyncSessionLocal()
    try:
        entry = SystemLog(
            category=category,
            level=level,
            title=title,
            content=content,
            source=source,
        )
        sdb.add(entry)
        sdb.commit()
    except Exception:
        sdb.rollback()
        logger.exception("写入 SystemLog 失败")
    finally:
        sdb.close()


# ---------------------------------------------------------------------------
# Comparison functions
# ---------------------------------------------------------------------------


def compare_quote_fields(
    symbol: str,
    quote_a: Dict[str, Any],
    quote_b: Dict[str, Any],
    source_a: str,
    source_b: str,
) -> List[FieldDiscrepancy]:
    """对比两个行情源的重叠字段，返回差异列表。"""
    diffs: List[FieldDiscrepancy] = []

    for field in _QUOTE_OVERLAP_FIELDS:
        va = quote_a.get(field)
        vb = quote_b.get(field)
        if va is None or vb is None:
            continue

        delta = compute_delta_pct(va, vb)
        severity = validate_cross_source_consistency(field, va, vb, source_a, source_b)
        if severity is None:
            continue

        diffs.append(FieldDiscrepancy(
            field=field,
            value_a=float(va),
            value_b=float(vb),
            source_a=source_a,
            source_b=source_b,
            delta_pct=delta,
            severity=severity,
        ))

    return diffs


def compare_kline_candles(
    symbol: str,
    klines_a: List[Dict[str, Any]],
    klines_b: List[Dict[str, Any]],
    source_a: str,
    source_b: str,
) -> List[FieldDiscrepancy]:
    """对比两份 K线数据中匹配时间戳的 K线，返回差异列表。"""
    # 按日期索引（日线取日期部分，分钟级取完整时间戳）
    def _key(k: Dict) -> str:
        ts = k.get("timestamp")
        if ts is None:
            return ""
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            dt = ts
        return dt.strftime("%Y-%m-%d")

    idx_b: Dict[str, Dict] = {}
    for k in klines_b:
        key = _key(k)
        if key:
            idx_b[key] = k

    diffs: List[FieldDiscrepancy] = []
    for ka in klines_a:
        key = _key(ka)
        kb = idx_b.get(key)
        if kb is None:
            continue

        for field in _KLINE_FIELDS:
            va = ka.get(field)
            vb = kb.get(field)
            if va is None or vb is None:
                continue

            delta = compute_delta_pct(va, vb)
            severity = validate_cross_source_consistency(field, va, vb, source_a, source_b)
            if severity is None:
                continue

            diffs.append(FieldDiscrepancy(
                field=f"{field}@{key}",
                value_a=float(va),
                value_b=float(vb),
                source_a=source_a,
                source_b=source_b,
                delta_pct=delta,
                severity=severity,
            ))

    return diffs


# ---------------------------------------------------------------------------
# CrossValidator orchestrator
# ---------------------------------------------------------------------------


class CrossValidator:
    """跨源数据校验编排器，在一次批量刷新中维护汇总状态。"""

    def __init__(self):
        self.summary = BatchSummary()

    def validate_ticker_quotes(
        self,
        primary_quotes: Dict[str, Dict[str, Any]],
        secondary_quotes: Dict[str, Dict[str, Any]],
        primary_source: str,
        secondary_source: str,
    ) -> BatchSummary:
        """对比所有标的两源行情数据。"""
        all_symbols = set(primary_quotes.keys()) | set(secondary_quotes.keys())

        for symbol in sorted(all_symbols):
            qa = primary_quotes.get(symbol)
            qb = secondary_quotes.get(symbol)

            if qa is None or qb is None:
                if qa is None:
                    self.summary.symbols_missing_secondary.append(symbol)
                continue

            self.summary.total_checked += 1
            diffs = compare_quote_fields(symbol, qa, qb, primary_source, secondary_source)
            if diffs:
                self.summary.symbols_with_discrepancies += 1
                self.summary.total_discrepancies += len(diffs)
                for d in diffs:
                    if d.severity == DiscrepancySeverity.CRITICAL:
                        self.summary.critical += 1
                    elif d.severity == DiscrepancySeverity.MODERATE:
                        self.summary.moderate += 1
                    else:
                        self.summary.minor += 1

                self._log_symbol_discrepancies(symbol, diffs, primary_source, secondary_source)

        self.emit_summary_report(primary_source, secondary_source)
        return self.summary

    def validate_klines(
        self,
        symbol: str,
        primary_klines: List[Dict[str, Any]],
        secondary_klines: List[Dict[str, Any]],
        primary_source: str,
        secondary_source: str,
    ) -> List[FieldDiscrepancy]:
        """对比单标的 K线数据。"""
        if not primary_klines or not secondary_klines:
            return []

        diffs = compare_kline_candles(symbol, primary_klines, secondary_klines, primary_source, secondary_source)
        if diffs:
            self._log_symbol_discrepancies(symbol, diffs, primary_source, secondary_source, data_type="K线")
        return diffs

    def emit_summary_report(self, primary_source: str, secondary_source: str) -> None:
        """输出本次批量校验汇总到 SystemLog。"""
        s = self.summary
        if s.total_checked == 0:
            return

        level = LogLevel.WARNING if s.total_discrepancies > 0 else LogLevel.INFO
        content = json.dumps({
            "primary": primary_source,
            "secondary": secondary_source,
            "checked": s.total_checked,
            "with_discrepancies": s.symbols_with_discrepancies,
            "total_discrepancies": s.total_discrepancies,
            "minor": s.minor,
            "moderate": s.moderate,
            "critical": s.critical,
            "missing_secondary": s.symbols_missing_secondary[:20],  # 截断
        }, ensure_ascii=False)

        write_system_log_sync(
            level=level,
            title=f"交叉校验完成: {primary_source} vs {secondary_source}",
            content=content,
        )
        logger.info(
            "交叉校验汇总: %d个标的, %d个有差异 (minor=%d moderate=%d critical=%d), %d个标的缺少第二源",
            s.total_checked, s.symbols_with_discrepancies,
            s.minor, s.moderate, s.critical,
            len(s.symbols_missing_secondary),
        )

    def _log_symbol_discrepancies(
        self,
        symbol: str,
        diffs: List[FieldDiscrepancy],
        source_a: str,
        source_b: str,
        data_type: str = "行情",
    ) -> None:
        """为单个标的的差异写一条 SystemLog。"""
        lines = []
        for d in diffs:
            lines.append(
                f"  [{d.severity.value.upper()}] {d.field}: "
                f"{source_a}={d.value_a} {source_b}={d.value_b} "
                f"(Δ{d.delta_pct:.2%})"
            )

        content = json.dumps({
            "symbol": symbol,
            "type": data_type,
            "source_a": source_a,
            "source_b": source_b,
            "discrepancies": [
                {
                    "field": d.field,
                    "value_a": d.value_a,
                    "value_b": d.value_b,
                    "delta_pct": round(d.delta_pct, 6),
                    "severity": d.severity.value,
                }
                for d in diffs
            ],
        }, ensure_ascii=False)

        worst = max(d.severity for d in diffs)
        level = LogLevel.ERROR if worst == DiscrepancySeverity.CRITICAL else LogLevel.WARNING

        write_system_log_sync(
            level=level,
            title=f"{data_type}交叉校验差异: {symbol}",
            content=content,
        )


# ---------------------------------------------------------------------------
# Standalone cross-validation entry points
# ---------------------------------------------------------------------------


def _fetch_secondary_quotes(symbols: List[str], primary_source: str) -> Tuple[Dict[str, Any], str]:
    """从第二数据源拉取实时行情。返回 (quotes, source_name)。"""
    secondary = "eastmoney" if primary_source == "sina" else "sina"
    try:
        if secondary == "eastmoney":
            from app.utils.eastmoney_client import fetch_realtime_quotes
            quotes = fetch_realtime_quotes(symbols)
        else:
            from app.utils.sina_client import fetch_realtime_quotes
            quotes = fetch_realtime_quotes(symbols)
        return quotes or {}, secondary
    except Exception:
        logger.warning("第二数据源 %s 获取失败", secondary)
        return {}, secondary


def cross_validate_all_tickers(
    primary_quotes: Dict[str, Dict[str, Any]],
    primary_source: str,
) -> BatchSummary:
    """对已获取的主源行情数据，拉取第二源并执行交叉校验。"""
    if not primary_quotes:
        return BatchSummary()

    symbols = list(primary_quotes.keys())
    secondary_quotes, secondary_source = _fetch_secondary_quotes(symbols, primary_source)

    validator = CrossValidator()
    return validator.validate_ticker_quotes(
        primary_quotes, secondary_quotes, primary_source, secondary_source,
    )


def cross_validate_klines_for_symbol(
    symbol: str,
    interval: str,
    primary_klines: List[Dict[str, Any]],
    primary_source: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[FieldDiscrepancy]:
    """对已获取的主源 K线，拉取第二源并执行交叉校验。"""
    if not primary_klines:
        return []

    secondary_source = "eastmoney" if primary_source == "sina" else "sina"
    try:
        if secondary_source == "eastmoney":
            from app.utils.eastmoney_client import fetch_kline
            secondary_klines = fetch_kline(symbol, interval, start_date, end_date, fqt=1)
        else:
            from app.utils.sina_client import fetch_kline
            secondary_klines = fetch_kline(symbol, interval, start_date, end_date)
    except Exception:
        logger.warning("K线第二源 %s 获取失败 %s", secondary_source, symbol)
        return []

    if not secondary_klines:
        return []

    validator = CrossValidator()
    return validator.validate_klines(symbol, primary_klines, secondary_klines, primary_source, secondary_source)
