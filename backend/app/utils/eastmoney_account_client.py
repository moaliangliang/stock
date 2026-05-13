"""
东方财富实盘账号客户端 — 直接调用 jywgmix.18.cn 网页交易平台 API

替代 easytrader/Windows 方案，纯 Linux 可运行。

真实 API（从 /Js/Search/Position.js 逆向）:
  POST /Com/queryAssetAndPositionV1  {moneyType: "RMB"}
  返回: {Status:"0", Data:[{Zzc, Kyzj, Zjye, positions:[...], bonds:[...]}]}

使用方法:
  1. 浏览器登录 https://jywgmix.18.cn/
  2. F12 → Console → 粘贴 scripts/em_capture.js 运行
  3. 输出中提取 userid / ctToken / utToken / fundaccount / secuid
  4. 填入 .env 对应字段
  5. 调用 EastMoneyAccountClient.from_settings() 创建实例
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# 市场代码映射: 代码首位 → 后缀
_MARKET_SUFFIX = {
    "6": ".SH",   # 上海主板、科创板 (688xxx)
    "0": ".SZ",   # 深圳主板
    "3": ".SZ",   # 深圳创业板
    "4": ".BJ",   # 北京
    "8": ".BJ",   # 北京
    "9": ".SH",   # 上海B股
    "2": ".SZ",   # 深圳B股
    "5": ".SH",   # 上海基金/ETF
    "1": ".SZ",   # 深圳基金/ETF
}


def em_code_to_symbol(code: str) -> str:
    """东方财富代码 → 系统代码。如 600519 → 600519.SH"""
    if not code or "." in code:
        return code
    suffix = _MARKET_SUFFIX.get(code[0], "")
    if not suffix:
        logger.warning("无法识别代码 %s 的市场，跳过", code)
    return code + suffix


def _to_raw_code(symbol: str) -> str:
    """系统代码 → 东方财富原始代码。600519.SH → 600519"""
    return symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")


def _market_code(symbol: str) -> str:
    """系统代码 → 东方财富市场代码。1=上海 2=深圳 0=北京"""
    if ".SH" in symbol or symbol.startswith(("5", "6", "9")):
        return "1"
    if ".BJ" in symbol or symbol.startswith(("4", "8")):
        return "0"
    return "2"


def _parse_order_item(raw: Dict) -> Dict[str, Any]:
    """解析委托记录"""
    code = str(raw.get("Zqdm") or "")
    return {
        "entrust_no": str(raw.get("Wtbh") or raw.get("wtxh") or ""),
        "symbol": em_code_to_symbol(code),
        "name": str(raw.get("Zqmc") or ""),
        "side": "buy" if str(raw.get("Mmlb") or "") in ("1", "B") else "sell",
        "price": float(raw.get("Wtjg") or 0),
        "quantity": int(float(raw.get("Wtsl") or 0)),
        "filled_quantity": int(float(raw.get("Cjsl") or 0)),
        "canceled_quantity": int(float(raw.get("Cdsl") or 0)),
        "status": str(raw.get("Wtzt") or ""),
        "status_text": str(raw.get("WtztName") or ""),
        "time": str(raw.get("Wtsj") or ""),
        "trade_type": str(raw.get("WtType") or ""),
    }


def _parse_trade_item(raw: Dict) -> Dict[str, Any]:
    """解析成交记录"""
    code = str(raw.get("Zqdm") or "")
    return {
        "entrust_no": str(raw.get("Wtbh") or raw.get("wtxh") or ""),
        "trade_id": str(raw.get("Cjbh") or ""),
        "symbol": em_code_to_symbol(code),
        "name": str(raw.get("Zqmc") or ""),
        "side": "buy" if str(raw.get("Mmlb") or "") in ("1", "B") else "sell",
        "price": float(raw.get("Cjjg") or 0),
        "quantity": int(float(raw.get("Cjsl") or 0)),
        "amount": float(raw.get("Cjje") or 0),
        "time": str(raw.get("Cjsj") or ""),
    }


class EastMoneyAccountClient:
    """东方财富实盘账号 HTTP 客户端"""

    REQUIRED_FIELDS = ("userid", "ut_token", "fund_account", "secuid")

    def __init__(
        self,
        base_url: str = "https://jywgmix.18.cn",
        userid: str = "",
        ct_token: str = "",
        ut_token: str = "",
        fund_account: str = "",
        secuid: str = "",
        raw_cookie: str = "",
        timeout: int = 15,
    ):
        self._base_url = base_url.rstrip("/")
        self._userid = userid
        self._ct_token = ct_token
        self._ut_token = ut_token
        self._fund_account = fund_account
        self._secuid = secuid
        self._raw_cookie = raw_cookie
        self._timeout = timeout
        self._cached_result: Optional[Dict] = None

    @classmethod
    def from_settings(cls) -> "EastMoneyAccountClient":
        """从全局配置创建实例"""
        return cls(
            base_url=settings.EM_ACCOUNT_BASE_URL,
            userid=settings.EM_ACCOUNT_USERID,
            ct_token=settings.EM_ACCOUNT_CT_TOKEN,
            ut_token=settings.EM_ACCOUNT_UT_TOKEN,
            fund_account=settings.EM_ACCOUNT_FUND_ACCOUNT,
            secuid=settings.EM_ACCOUNT_SECUID,
            raw_cookie=settings.EM_ACCOUNT_COOKIE,
        )

    @property
    def is_configured(self) -> bool:
        """是否已配置认证信息"""
        if self._raw_cookie:
            return True
        return bool(self._userid and self._ut_token and self._fund_account and self._secuid)

    @classmethod
    def missing_fields(cls) -> List[str]:
        """从全局配置检查缺失的认证字段，返回缺失字段名列表"""
        if settings.EM_ACCOUNT_COOKIE:
            return []
        field_names = {
            "userid": settings.EM_ACCOUNT_USERID,
            "ct_token": settings.EM_ACCOUNT_CT_TOKEN,
            "ut_token": settings.EM_ACCOUNT_UT_TOKEN,
            "fund_account": settings.EM_ACCOUNT_FUND_ACCOUNT,
            "secuid": settings.EM_ACCOUNT_SECUID,
        }
        return [name for name, val in field_names.items() if not val]

    def _build_cookie(self) -> str:
        """构造 Cookie 字符串"""
        if self._raw_cookie:
            return self._raw_cookie
        parts = []
        if self._userid:
            parts.append(f"userid={self._userid}")
        if self._ct_token:
            parts.append(f"ctToken={self._ct_token}")
        if self._ut_token:
            parts.append(f"utToken={self._ut_token}")
        if self._fund_account:
            parts.append(f"fundaccount={self._fund_account}")
        if self._secuid:
            parts.append(f"secuid={self._secuid}")
        return "; ".join(parts)

    def _build_headers(self, extra: Optional[Dict] = None) -> Dict[str, str]:
        """构造请求头，模拟浏览器"""
        headers = {
            "Cookie": self._build_cookie(),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"{self._base_url}/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self, method: str, path: str, data: Optional[Dict] = None, use_json: bool = True
    ) -> Dict[str, Any]:
        """统一 HTTP 请求"""
        url = f"{self._base_url}{path}"
        headers = self._build_headers(
            {"Content-Type": "application/json"} if use_json and data else None
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                elif method == "POST":
                    if use_json and data:
                        resp = await client.post(url, headers=headers, json=data)
                    elif data:
                        headers["Content-Type"] = "application/x-www-form-urlencoded"
                        resp = await client.post(url, headers=headers, data=data)
                    else:
                        resp = await client.post(url, headers=headers)
                else:
                    raise ValueError(f"不支持的 HTTP 方法: {method}")
                resp.raise_for_status()
                return resp.json()
        except httpx.ConnectError:
            raise RuntimeError(f"无法连接东方财富交易服务器 {self._base_url}")
        except httpx.TimeoutException:
            raise RuntimeError(f"东方财富交易服务器无响应（超时 {self._timeout}s）")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise RuntimeError("东方财富 token 已过期，请重新登录 jywgmix.18.cn 获取新 token")
            raise RuntimeError(f"东方财富 API 返回错误: HTTP {e.response.status_code}")

    # ------------------------------------------------------------------
    # 核心: 查询资产和持仓（真实 API）
    # ------------------------------------------------------------------

    async def query_asset_and_position(self) -> Dict[str, Any]:
        """调用 /Com/queryAssetAndPositionV1，返回原始 Data[0]"""
        result = await self._request("POST", "/Com/queryAssetAndPositionV1", {"moneyType": "RMB"})
        if isinstance(result, dict):
            status = result.get("Status", "")
        else:
            raise RuntimeError(f"东方财富 API 返回非JSON: {type(result).__name__}: {str(result)[:200]}")
        if str(status) != "0":
            raise RuntimeError(f"东方财富 API 业务错误: {result.get('Message', '未知错误')} (Status={status})")
        data_list = result.get("Data", [])
        if not data_list:
            raise RuntimeError("东方财富 API 返回空数据")
        self._cached_result = data_list[0]
        return self._cached_result

    async def verify(self) -> bool:
        """验证认证信息是否有效"""
        try:
            await self.query_asset_and_position()
            return True
        except RuntimeError:
            return False

    async def get_account(self) -> Dict[str, Any]:
        """获取账户资金信息"""
        data = self._cached_result or await self.query_asset_and_position()
        return self._parse_account(data)

    async def get_positions(self) -> List[Dict[str, Any]]:
        """获取持仓列表"""
        data = self._cached_result or await self.query_asset_and_position()
        positions = data.get("positions", [])
        bonds = data.get("bonds", [])
        items = positions + bonds
        return [self._parse_position(item) for item in items if item]

    # ------------------------------------------------------------------
    # 交易: 下单 / 查询今日委托 / 查询今日成交 / 撤单
    # ------------------------------------------------------------------

    async def submit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        amount: int,
        trade_type: str = "0a",
        stock_name: str = "",
    ) -> Dict[str, Any]:
        """提交买卖委托到 /Trade/SubmitTradeV2

        Args:
            symbol: 系统代码如 600519.SH
            side: buy / sell
            price: 委托价格
            amount: 委托数量（股）
            trade_type: 委托方式, 0a=限价 0b=市价
            stock_name: 证券名称（可选）

        Returns:
            {order_id, entrust_no, status}
        """
        code = _to_raw_code(symbol)
        market = _market_code(symbol)

        data = {
            "stockCode": code,
            "price": str(price),
            "amount": str(amount),
            "tradeType": trade_type,
            "zqmc": stock_name or "",
            "market": market,
        }

        result = await self._request("POST", "/Trade/SubmitTradeV2", data)

        if isinstance(result, dict):
            status = result.get("Status", "")
        else:
            raise RuntimeError(
                f"东方财富下单返回非JSON: {type(result).__name__}: {str(result)[:200]}"
            )

        errcode = result.get("Errcode") if isinstance(result, dict) else None
        if str(status) != "0":
            if errcode == -8:
                raise RuntimeError("东方财富下单失败: Cookie 已过期，请重新登录 jywgmix.18.cn")
            raise RuntimeError(
                f"东方财富下单失败: {result.get('Message', '未知错误')} (Status={status})"
            )

        data_list = result.get("Data", [])
        if not data_list:
            raise RuntimeError("东方财富下单返回空数据")

        order_info = data_list[0]
        entrust_no = str(order_info.get("Wtbh") or order_info.get("wtxh", ""))
        return {
            "order_id": entrust_no,
            "entrust_no": entrust_no,
            "status": "pending",
        }

    async def query_today_orders(self) -> List[Dict[str, Any]]:
        """查询今日委托 — POST /Search/queryTodayOrderWEB"""
        result = await self._request("POST", "/Search/queryTodayOrderWEB")
        if isinstance(result, dict):
            if str(result.get("Status", "")) != "0":
                raise RuntimeError(
                    f"查询今日委托失败: {result.get('Message', '未知错误')}"
                )
            items = result.get("Data", [])
        elif isinstance(result, list):
            items = result
        else:
            return []
        return [_parse_order_item(o) for o in (items or [])]

    async def query_today_trades(self) -> List[Dict[str, Any]]:
        """查询今日成交 — POST /Search/queryTodayMatchWEB"""
        result = await self._request("POST", "/Search/queryTodayMatchWEB")
        if isinstance(result, dict):
            if str(result.get("Status", "")) != "0":
                raise RuntimeError(
                    f"查询今日成交失败: {result.get('Message', '未知错误')}"
                )
            items = result.get("Data", [])
        elif isinstance(result, list):
            items = result
        else:
            return []
        return [_parse_trade_item(t) for t in (items or [])]

    async def cancel_order(self, entrust_no: str) -> bool:
        """撤单 — POST /Trade/CancelOrder"""
        result = await self._request(
            "POST", "/Trade/CancelOrder",
            {"entrustNo": entrust_no}
        )
        if isinstance(result, dict) and str(result.get("Status", "")) == "0":
            return True
        raise RuntimeError(
            f"撤单失败: {result.get('Message', '未知错误') if isinstance(result, dict) else str(result)[:200]}"
        )

    # ------------------------------------------------------------------
    # 响应解析（字段名来自 /Js/Search/Position.js）
    # ------------------------------------------------------------------

    def _parse_account(self, data: Dict) -> Dict[str, Any]:
        return {
            "total_balance": float(data.get("Zzc") or 0),
            "available_balance": float(data.get("Kyzj") or 0),
            "frozen_balance": float(data.get("Djzj") or 0),
            "market_value": float(data.get("totalSecMkval") or 0),
            "fund_balance": float(data.get("Zjye") or 0),
            "withdrawable": float(data.get("Kqzj") or 0),
            "total_pnl": float(data.get("Ljyk") or 0),
            "day_pnl": float(data.get("Dryk") or 0),
        }

    def _parse_position(self, raw: Dict) -> Dict[str, Any]:
        code = str(raw.get("Zqdm") or "")
        symbol = em_code_to_symbol(code)

        quantity = int(float(raw.get("Zqsl") or 0))
        available = int(float(raw.get("Kysl") or quantity))
        cost_price = float(raw.get("Cbjg") or 0)
        current_price = float(raw.get("Zxjg") or cost_price)
        market_value = float(raw.get("Zxsz") or 0)
        total_pnl = float(raw.get("Ljyk") or 0)
        day_pnl = float(raw.get("Dryk") or 0)

        return {
            "symbol": symbol,
            "name": str(raw.get("Zqmc") or ""),
            "gddm": str(raw.get("Gddm") or ""),
            "quantity": quantity,
            "available_quantity": available,
            "cost_price": cost_price,
            "current_price": current_price,
            "market_value": market_value,
            "day_pnl": day_pnl,
            "total_pnl": total_pnl,
        }
