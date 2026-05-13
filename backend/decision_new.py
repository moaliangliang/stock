"""
Investment decision service — core decision management and CRUD.

Scoring is delegated to:
  app.services.indicators   — pure technical indicator functions
  app.services.scoring      — multi-factor scoring engine
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import DecisionRecommendation, DecisionStatus, InvestmentDecision
from app.models.market_data import KLine, Ticker
from app.models.position import Position
from app.models.risk import RiskRecord
from app.models.user import User
from app.services.indicators import (
    _rolling_mean,
    _rolling_std,
    _calc_adx,
    _calc_kdj,
    _calc_money_flow,
    _detect_regime,
    _detect_regime_transition,
    _detect_volume_divergence,
    _detect_macd_divergence,
    _detect_rsi_divergence,
    _calc_market_context_adjustment,
)
from app.services.scoring import (
    DEFAULT_WEIGHTS,
    _compute_dynamic_weights,
    _normalize_score,
    _calc_technical_score,
    _calc_sentiment_score,
    _compute_risk_score,
    _calc_risk_score_sync,
    _calc_risk_score,
    _calc_momentum_score,
    _afetch_fundamental,
    _calc_fundamental_score,
    _score_to_recommendation,
    _apply_correlation_discount,
)
from app.services.market import get_kline_data, get_ticker, save_kline_data
from app.services.notification import create_notification
from app.services.decision_config import get as _cfg

logger = logging.getLogger(__name__)




