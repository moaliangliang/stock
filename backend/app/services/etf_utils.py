"""
ETF 工具模块 — 检测、指数映射、跟踪误差计算
"""
import logging
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ETF → 跟踪指数 baostock 代码映射
# A-share ETF 代码规律：510xxx/588xxx.SH, 159xxx/512xxx/513xxx/515xxx/516xxx/517xxx/560xxx/561xxx/562xxx/563xxx.SZ 等
ETF_INDEX_MAP: Dict[str, Tuple[str, str]] = {
    # 宽基ETF
    "510050.SH": ("sh.000016", "上证50"),
    "510300.SH": ("sh.000300", "沪深300"),
    "510310.SH": ("sh.000300", "沪深300"),
    "510330.SH": ("sh.000300", "沪深300"),
    "510500.SH": ("sh.000905", "中证500"),
    "510880.SH": ("sh.000922", "中证红利"),
    "512100.SH": ("sh.000922", "中证1000"),
    "588000.SH": ("sh.000688", "科创50"),
    "588200.SH": ("sh.000688", "科创芯片"),
    "159915.SZ": ("sz.399006", "创业板指"),
    "159949.SZ": ("sz.399673", "创业板50"),
    "159922.SZ": ("sz.399001", "深证成指"),
    "159845.SZ": ("sz.399905", "中证1000"),
    # 行业ETF
    "512880.SH": ("sh.399967", "证券ETF"),
    "512800.SH": ("sh.399986", "银行ETF"),
    "512690.SH": ("sh.399997", "酒ETF"),
    "512170.SH": ("sh.399989", "医疗ETF"),
    "512010.SH": ("sh.399991", "医药ETF"),
    "512980.SH": ("sh.399986", "传媒ETF"),
    "515050.SH": ("sh.931079", "5GETF"),
    "515790.SH": ("sh.931151", "光伏ETF"),
    "516160.SH": ("sh.931456", "新能源ETF"),
    "516110.SH": ("sh.931521", "汽车ETF"),
    "159928.SZ": ("sz.399987", "消费ETF"),
    "159995.SZ": ("sh.932087", "芯片ETF"),
}


def is_etf(symbol: str) -> bool:
    """检测是否为 A 股 ETF（通过代码规则判断）。"""
    if not symbol.endswith((".SH", ".SZ")):
        return False
    code = symbol[:-3]
    if len(code) != 6:
        return False
    # ETF 代码范围
    if code.startswith(("51", "56", "58", "59")):
        return True
    return False


def get_etf_index(symbol: str) -> Optional[Tuple[str, str]]:
    """获取 ETF 对应的跟踪指数 baostock 代码和名称。"""
    return ETF_INDEX_MAP.get(symbol)


def calc_tracking_error(
    etf_returns: np.ndarray,
    index_returns: np.ndarray,
    annualize: bool = True,
) -> float:
    """
    计算 ETF 跟踪误差。

    跟踪误差 = std(ETF日收益 - 指数日收益) × sqrt(252)（年化）

    Args:
        etf_returns: ETF 日收益率数组
        index_returns: 指数日收益率数组
        annualize: 是否年化

    Returns:
        年化跟踪误差（百分比），如 0.5 表示 0.5%
    """
    if len(etf_returns) < 5 or len(index_returns) < 5:
        return 0.0
    n = min(len(etf_returns), len(index_returns))
    diff = etf_returns[-n:] - index_returns[-n:]
    te = float(np.std(diff) * 100)  # 转为百分比
    if annualize:
        te = te * np.sqrt(252)
    return round(te, 2)


def fetch_etf_tracking_error(symbol: str, kline_data: dict) -> Dict:
    """
    计算 ETF 跟踪误差并返回评分详情。

    Args:
        symbol: ETF 代码
        kline_data: _klines_to_arrays 返回的日线数据 dict（包含 close）

    Returns:
        {score, weight, label, details} 格式，与 _calc_fundamental_score 一致
    """
    mapping = get_etf_index(symbol)
    if not mapping:
        return _etf_no_tracking_result(symbol, "未找到对应指数映射")

    idx_bs_code, idx_name = mapping

    # 获取指数 K 线
    try:
        import baostock as bs
        from datetime import date, timedelta

        lg = bs.login()
        if lg.error_code != "0":
            return _etf_no_tracking_result(symbol, "baostock登录失败")

        end_date = date.today().strftime("%Y-%m-%d")
        start_date = (date.today() - timedelta(days=180)).strftime("%Y-%m-%d")

        rs = bs.query_history_k_data_plus(
            idx_bs_code,
            "date,close",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",
        )
        if rs.error_code != "0":
            bs.logout()
            return _etf_no_tracking_result(symbol, f"指数{idx_name}数据获取失败")

        idx_closes = []
        idx_dates = []
        while rs.next():
            row = rs.get_row_data()
            try:
                idx_dates.append(row[0])
                idx_closes.append(float(row[1]))
            except (ValueError, IndexError):
                continue

        bs.logout()

        if len(idx_closes) < 20:
            return _etf_no_tracking_result(symbol, f"指数{idx_name}数据不足")

    except ImportError:
        return _etf_no_tracking_result(symbol, "baostock未安装")
    except Exception as e:
        logger.warning("ETF跟踪误差计算失败 %s: %s", symbol, e)
        return _etf_no_tracking_result(symbol, str(e))

    # 用 ETF K 线日收益与指数日收益计算跟踪误差
    etf_close = kline_data.get("close")
    if etf_close is None or len(etf_close) < 20:
        return _etf_no_tracking_result(symbol, "ETF K线数据不足")

    etf_ret = np.diff(etf_close) / etf_close[:-1]
    idx_ret = np.diff(np.array(idx_closes, dtype=float)) / np.array(idx_closes[:-1], dtype=float)

    te = calc_tracking_error(etf_ret, idx_ret)

    signals = []
    adjustments = []

    # 评分：跟踪误差越低越好
    # <0.5% 优秀, 0.5-1% 良好, 1-2% 一般, 2-3% 较差, >3% 差
    if te <= 0:
        signals.append("跟踪误差数据不足")
    elif te < 0.5:
        adjustments.append(10)
        signals.append(f"跟踪误差={te:.2f}%(优秀,紧密跟踪{idx_name})")
    elif te < 1.0:
        adjustments.append(5)
        signals.append(f"跟踪误差={te:.2f}%(良好,较紧密跟踪{idx_name})")
    elif te < 2.0:
        signals.append(f"跟踪误差={te:.2f}%(一般,跟踪{idx_name})")
    elif te < 3.0:
        adjustments.append(-5)
        signals.append(f"跟踪误差={te:.2f}%(偏大,跟踪{idx_name}不够紧密)")
    else:
        adjustments.append(-10)
        signals.append(f"跟踪误差={te:.2f}%(严重偏离{idx_name})")

    # 复用 _normalize_score
    from app.services.scoring import _normalize_score
    score = _normalize_score(adjustments)

    return {
        "score": score,
        "weight": 0.10,
        "label": f"跟踪误差({idx_name})",
        "details": {
            "tracking_error_pct": te,
            "index_name": idx_name,
            "index_code": idx_bs_code,
            "signals": signals,
        },
    }


def _etf_no_tracking_result(symbol: str, reason: str) -> Dict:
    """ETF 无法计算跟踪误差时的兜底结果。"""
    return {
        "score": 50.0,
        "weight": 0.10,
        "label": "跟踪误差",
        "details": {
            "tracking_error_pct": None,
            "signals": [f"跟踪误差不可用: {reason}"],
        },
    }
