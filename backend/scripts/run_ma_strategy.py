#!/usr/bin/env python3
"""MA5/20 策略定时执行器 — 由 cron 触发"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from datetime import datetime, timezone
from app.core.database import SyncSessionLocal
from app.models.strategy import Strategy, StrategyRunLog, StrategyStatus
from app.models.market_data import KLine
from app.services.strategy import ma_cross_strategy, _kline_to_dataframe
from sqlalchemy import select

db = SyncSessionLocal()
try:
    strategy = db.query(Strategy).filter(
        Strategy.id == 10, Strategy.status == StrategyStatus.ACTIVE
    ).first()
    if not strategy:
        print("策略未找到或未激活")
        sys.exit(0)

    rows = db.execute(
        select(KLine).where(KLine.symbol == '002384.SZ', KLine.interval == '1d')
        .order_by(KLine.timestamp.desc()).limit(200)
    ).scalars().all()

    klines = [{'timestamp': k.timestamp, 'open': k.open, 'high': k.high,
               'low': k.low, 'close': k.close, 'volume': k.volume}
              for k in reversed(rows)]

    df = _kline_to_dataframe(klines)
    raw_signals = ma_cross_strategy(df, fast_period=5, slow_period=20)

    signals = []
    for s in raw_signals:
        ts = s.get('timestamp')
        if hasattr(ts, 'timestamp'): ts = int(ts.timestamp())
        elif hasattr(ts, 'value'): ts = int(ts.value / 1e9)
        else: ts = int(ts)
        signals.append({'timestamp': ts, 'action': s['action'],
                        'price': float(s['price']), 'reason': s['reason']})

    log = StrategyRunLog(
        strategy_id=10, run_time=datetime.now(timezone.utc),
        status='success', signals=signals,
        message=f"定时执行: {len(signals)}个信号", duration_ms=0,
    )
    db.add(log)
    db.commit()

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    last = signals[-1] if signals else None
    if last:
        ts = datetime.fromtimestamp(last['timestamp']).strftime('%m-%d')
        print(f"[{now}] MA5/20 | {len(signals)}信号 | 最新: {ts} {last['action']} @{last['price']:.2f}")
    else:
        print(f"[{now}] MA5/20 | 无信号")
except Exception as e:
    print(f"策略执行失败: {e}")
finally:
    db.close()
