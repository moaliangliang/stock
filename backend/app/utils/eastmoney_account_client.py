"""
东方财富实盘账号客户端 — 直接调用 tradeapp.eastmoney.com 内部 API

替代 easytrader/Windows 方案，纯 Linux 可运行。

使用方法:
  1. 浏览器登录 https://tradeapp.eastmoney.com/
  2. F12 → Application → Cookies → 复制 userid, ctToken, utToken, fundaccount, secuid
  3. 填入 .env 对应字段
  4. 调用 EastMoneyAccountClient.from_settings() 创建实例
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
        # 已经是完整代码或空值
        return code
    suffix = _MARKET_SUFFIX.get(code[0], "")
    if not suffix:
        logger.warning("无法识别代码 %s 的市场，跳过", code)
    return code + suffix


class EastMoneyAccountClient:
    """东方财富实盘账号 HTTP 客户端"""

    def __init__(
        self,
        base_url: str = "https://tradeapp.eastmoney.com",
        userid: str = "",
        ct_token: str = "",
        ut_token: str = "",
        fund_account: str = "",
        secuid: str = "",
        timeout: int = 15,
    ):
        self._base_url = base_url.rstrip("/")
        self._userid = userid
        self._ct_token = ct_token
        self._ut_token = ut_token
        self._fund_account = fund_account
        self._secuid = secuid
        self._timeout = timeout

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
        )

    @property
    def is_configured(self) -> bool:
        """是否已配置必填的认证字段"""
        return bool(self._userid and self._ut_token and self._fund_account and self._secuid)

    def _build_cookie(self) -> str:
        """构造 Cookie 字符串"""
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
        }
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self, method: str, path: str, json_body: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """统一 HTTP 请求"""
        url = f"{self._base_url}{path}"
        headers = self._build_headers(
            {"Content-Type": "application/json"} if json_body else None
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                elif method == "POST":
                    resp = await client.post(url, headers=headers, json=json_body)
                else:
                    raise ValueError(f"不支持的 HTTP 方法: {method}")
                resp.raise_for_status()
                return resp.json()
        except httpx.ConnectError:
            raise RuntimeError(f"无法连接东方财富交易服务器 {self._base_url}")
        except httpx.TimeoutException:
            raise RuntimeError(f"东方财富交易服务器无响应（超时 {self._timeout}s）")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401 or e.response.status_code == 403:
                raise RuntimeError("东方财富 token 已过期，请重新登录 tradeapp.eastmoney.com 获取新 token")
            raise RuntimeError(f"东方财富 API 返回错误: HTTP {e.response.status_code}")

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------

    async def verify(self) -> bool:
        """验证认证信息是否有效"""
        try:
            await self.get_account()
            return True
        except RuntimeError:
            return False

    async def get_account(self) -> Dict[str, Any]:
        """获取账户资金信息"""
        result = await self._request("GET", "/api/v1/trading/account")
        return self._parse_account(result)

    async def get_positions(self) -> List[Dict[str, Any]]:
        """获取持仓列表"""
        result = await self._request("GET", "/api/v1/trading/hold")

        # 适配多种可能的响应格式
        if isinstance(result, list):
            items = result
        elif isinstance(result, dict):
            items = result.get("data") or result.get("Data") or result.get("holdList") or []
            if isinstance(items, dict):
                items = items.get("list") or items.get("items") or []
        else:
            items = []

        return [self._parse_position(item) for item in items if item]

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def _parse_account(self, raw: Dict) -> Dict[str, Any]:
        """解析账户资金响应"""
        data = raw.get("data") or raw.get("Data") or raw
        return {
            "total_balance": float(data.get("totalAssets") or data.get("total_balance") or data.get("balance") or 0),
            "available_balance": float(data.get("enableBalance") or data.get("available") or data.get("usable") or 0),
            "frozen_balance": float(data.get("frozenBalance") or data.get("frozen") or 0),
            "market_value": float(data.get("marketValue") or data.get("market_value") or data.get("holdValue") or 0),
        }

    def _parse_position(self, raw: Dict) -> Dict[str, Any]:
        """解析单条持仓数据"""
        code = str(raw.get("stockCode") or raw.get("stock_code") or raw.get("code") or "")
        symbol = em_code_to_symbol(code)

        quantity = int(float(raw.get("currentAmount") or raw.get("current_amount") or raw.get("holdVol") or raw.get("hold_vol") or 0))
        available = int(float(raw.get("enableAmount") or raw.get("enable_amount") or raw.get("usableVol") or raw.get("usable_vol") or quantity))
        cost_price = float(raw.get("costPrice") or raw.get("cost_price") or raw.get("holdPrice") or raw.get("hold_price") or 0)
        current_price = float(raw.get("lastPrice") or raw.get("last_price") or raw.get("newPrice") or raw.get("new_price") or cost_price)
        market_value = float(raw.get("marketValue") or raw.get("market_value") or raw.get("holdValue") or raw.get("hold_value") or 0)
        day_pnl = float(raw.get("todayProfit") or raw.get("today_profit") or raw.get("dayEarn") or raw.get("day_earn") or 0)
        total_pnl = float(raw.get("holdProfit") or raw.get("hold_profit") or raw.get("holdEarn") or raw.get("hold_earn") or 0)

        return {
            "symbol": symbol,
            "quantity": quantity,
            "available_quantity": available,
            "cost_price": cost_price,
            "current_price": current_price,
            "market_value": market_value,
            "day_pnl": day_pnl,
            "total_pnl": total_pnl,
        }
