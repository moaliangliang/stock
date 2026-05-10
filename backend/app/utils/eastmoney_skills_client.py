"""
东方财富妙想Skills API 客户端 — 仅基本面数据（PE/PB/ROE/增长）

API 每月限额 300 次，每次查询获取一只股票的完整基本面数据。
实时行情走 sina，不消耗 Skills 配额。
计数器持久化到 DB，跨进程共享，每月1日自动重置。
"""
import logging
from typing import Any, Dict, Optional

import requests

from app.core.config import settings
from app.utils.token_usage import check_quota, increment as _incr_usage, get_stats as _get_usage_stats

logger = logging.getLogger(__name__)

# 指标编码映射（东方财富妙想Skills 内部编码，稳定不变）
INDICATOR_CODES = {
    "pe_ttm": "328773",         # 市盈率PE(TTM)
    "pb": "328664",             # 市净率PB
    "roe": "100000000003466",   # 净资产收益率ROE
    "revenue_growth": "100000000004683",  # 营业收入同比增长率
    "profit_growth": "100000000006667",   # 净利润同比增长率
}

MONTHLY_LIMIT = 300


def _check_rate_limit() -> bool:
    """检查月度配额，超出返回 False。计数器持久化到 DB。"""
    ok = check_quota("skills_api", MONTHLY_LIMIT)
    if not ok:
        stats = _get_usage_stats("skills_api")
        logger.warning(
            "东方财富Skills月度配额已用尽 (%s/%s)",
            stats.get("call_count", MONTHLY_LIMIT),
            MONTHLY_LIMIT,
        )
        return False
    return True


def fetch_fundamental(symbol: str) -> Optional[Dict[str, Any]]:
    """通过东方财富妙想Skills API 获取单只股票的基本面数据。

    一次查询返回 PE(TTM)、PB、ROE、营收增长率、净利润增长率。
    成功返回 dict，失败返回 None（调用方应降级到下一数据源）。
    """
    if not settings.EASTMONEY_SKILLS_API_KEY:
        return None
    if not _check_rate_limit():
        return None

    # 去掉后缀用于查询文本
    code = symbol.replace(".SH", "").replace(".SZ", "")
    query = f"{code} 市盈率PE 市净率PB ROE 营收增长率 净利润增长率"

    headers = {
        "Content-Type": "application/json",
        "apikey": settings.EASTMONEY_SKILLS_API_KEY,
    }

    try:
        resp = requests.post(
            settings.EASTMONEY_SKILLS_BASE_URL,
            headers=headers,
            json={"toolQuery": query},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("东方财富Skills请求失败 %s: %s", symbol, e)
        return None

    # HTTP 请求成功即消耗一次配额（无论 API 业务层是否返回有效数据）
    _incr_usage("skills_api", MONTHLY_LIMIT)

    if not data.get("success"):
        logger.warning("东方财富Skills返回失败 %s: %s", symbol, data.get("message", "unknown"))
        return None

    # 解析响应：取 A 股条目（筛选 .SH/.SZ）
    items = (
        data.get("data", {})
        .get("data", {})
        .get("searchDataResultDTO", {})
        .get("dataTableDTOList", [])
    )

    pe = 0.0
    pb = 0.0
    roe = 0.0
    revenue_growth = 0.0
    profit_growth = 0.0

    for item in items:
        item_code = item.get("code", "")
        if ".SH" not in item_code and ".SZ" not in item_code:
            continue  # 跳过港股等非A股条目

        raw = item.get("rawTable", {})
        if not raw:
            continue

        # PE/PB table
        if INDICATOR_CODES["pe_ttm"] in raw:
            pe = _first_float(raw[INDICATOR_CODES["pe_ttm"]])
        if INDICATOR_CODES["pb"] in raw:
            pb = _first_float(raw[INDICATOR_CODES["pb"]])

        # ROE / growth table
        if INDICATOR_CODES["roe"] in raw:
            roe = _first_float(raw[INDICATOR_CODES["roe"]])
        if INDICATOR_CODES["revenue_growth"] in raw:
            revenue_growth = _first_float(raw[INDICATOR_CODES["revenue_growth"]])
        if INDICATOR_CODES["profit_growth"] in raw:
            profit_growth = _first_float(raw[INDICATOR_CODES["profit_growth"]])

    # 至少有一个非零指标才算有效（PE 可能因 NLP 解析不稳定而缺失）
    if pe <= 0 and pb <= 0 and roe <= 0:
        logger.warning("东方财富Skills未获取到任何有效基本面数据 %s", symbol)
        return None

    if pe <= 0:
        logger.info("东方财富Skills基本面 %s: PE缺失, 已获取 PB=%.2f ROE=%.2f, 剩余指标可用", symbol, pb, roe)
    else:
        logger.info("东方财富Skills基本面 %s: PE=%.2f PB=%.2f ROE=%.2f", symbol, pe, pb, roe)

    return {
        "pe": round(pe, 2),
        "pb": round(pb, 2),
        "roe": round(roe, 2),
        "revenue_growth": round(revenue_growth, 2),
        "profit_growth": round(profit_growth, 2),
        "source": "eastmoney_skills",
    }


def _first_float(values: list) -> float:
    """取列表第一个有效数值。"""
    if not values:
        return 0.0
    try:
        return float(values[0])
    except (ValueError, TypeError):
        return 0.0
