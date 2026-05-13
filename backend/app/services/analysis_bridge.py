"""
Bridge: Analysis script buy recommendations -> Auto-trade execution.
Import and call from analysis scripts to submit buy candidates for sandbox execution.
"""
import logging
from typing import Dict, Any, List

from app.core.config import settings
from app.core.database import SyncSessionLocal

logger = logging.getLogger(__name__)


def submit_buy_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Submit buy recommendations from analysis scripts to the auto-trade engine.

    Args:
        recommendations: list of dicts, each with:
            - symbol (required): '000001.SZ'
            - name (optional): stock name
            - price (required): current price
            - score (optional): 0-100
            - reasons (optional): list of reason strings

    Returns:
        List of auto-trade result dicts.
    """
    if not settings.AUTO_TRADE_ENABLED:
        logger.info("[ANALYSIS-BRIDGE] Auto-trade disabled, skipping")
        return [{"symbol": r["symbol"], "executed": False, "reason": "disabled"}
                for r in recommendations]

    if not recommendations:
        return []

    signals = []
    for rec in recommendations:
        score = rec.get("score", 60)
        level = "STRONG_BUY" if score >= 75 else "BUY" if score >= 60 else "WATCH"

        reason_list = rec.get("reasons", [])
        signal_dicts = [{"type": r, "confidence": min(score + 5, 95)} for r in reason_list]
        if not signal_dicts:
            signal_dicts = [{"type": "分析推荐", "confidence": score}]

        signals.append({
            "symbol": rec["symbol"],
            "name": rec.get("name", rec["symbol"]),
            "price": rec.get("price", 0),
            "level": level,
            "score": score,
            "signals": signal_dicts,
        })

    db = SyncSessionLocal()
    try:
        from app.services.auto_trade import execute_signal_batch_sync
        results = execute_signal_batch_sync(db, user_id=1, stock_signals=signals)
        db.commit()

        for r in results:
            at = r.get("auto_trade", {})
            if at.get("executed"):
                logger.info(f"[ANALYSIS-BRIDGE] {r['symbol']}: EXECUTED — {at.get('reason', '')}")
            else:
                logger.info(f"[ANALYSIS-BRIDGE] {r['symbol']}: SKIP ({at.get('reason', 'unknown')})")

        return results
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
