"""DCA 定投回测 API"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.common import Response
from app.schemas.dca import DcaBacktestRequest, DcaBacktestResponse
from app.services.dca_backtest import run_dca_backtest, DEFAULT_INDICES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dca", tags=["DCA定投回测"])


@router.get("/indices", response_model=Response)
def get_dca_indices(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """获取支持的定投标的列表。"""
    indices = [{"symbol": s, "name": n} for s, n in DEFAULT_INDICES.items()]
    return {"code": 200, "message": "success", "data": indices}


@router.post("/backtest", response_model=Response)
def run_dca_backtest_endpoint(
    req: DcaBacktestRequest,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """执行 DCA 定投回测。

    支持两种模式:
    - fixed (定额): 每期投入固定金额
    - smart (智能): 根据12月均线估值动态调整投入金额
      - 价格低于均线 → 加大投入 (最多 max_multiplier 倍)
      - 价格高于均线 → 减少投入 (最少 min_multiplier 倍)
      - 乘数公式: 1 + (SMA-price)/SMA * aggressiveness

    默认标的:
    - 513100.SH 纳斯达克100
    - 513500.SH 标普500
    """
    result = run_dca_backtest(
        symbols=req.symbols,
        start_date=req.start_date,
        end_date=req.end_date,
        amount=req.amount,
        mode=req.mode,
        smart_aggressiveness=req.smart_aggressiveness,
        smart_min_multiplier=req.smart_min_multiplier,
        smart_max_multiplier=req.smart_max_multiplier,
    )

    if "error" in result:
        return {"code": 400, "message": result["error"], "data": result.get("details")}

    return {"code": 200, "message": "success", "data": result}
