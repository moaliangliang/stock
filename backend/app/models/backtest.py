"""
回测结果模型
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, String

from app.core.database import Base


class BacktestResult(Base):
    """回测结果表 — 存储批量回测的汇总指标"""
    __tablename__ = "backtest_results"
    __table_args__ = (
        Index("idx_br_symbol", "symbol"),
        Index("idx_br_strategy", "strategy_type"),
        Index("idx_br_created", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, comment="标的代码")
    strategy_type = Column(String(20), nullable=False, comment="策略类型")
    strategy_params = Column(String(200), nullable=True, comment="策略参数摘要")

    # 收益指标
    total_return = Column(Float, default=0.0, nullable=False, comment="总收益率")
    annual_return = Column(Float, default=0.0, nullable=False, comment="年化收益率")
    max_drawdown = Column(Float, default=0.0, nullable=False, comment="最大回撤")
    sharpe_ratio = Column(Float, default=0.0, nullable=False, comment="夏普比率")
    final_equity = Column(Float, default=0.0, nullable=False, comment="最终权益")

    # 交易统计
    win_rate = Column(Float, default=0.0, nullable=False, comment="胜率")
    total_trades = Column(Integer, default=0, nullable=False, comment="总交易次数")
    profit_trades = Column(Integer, default=0, nullable=False, comment="盈利交易次数")
    loss_trades = Column(Integer, default=0, nullable=False, comment="亏损交易次数")
    profit_factor = Column(Float, default=0.0, nullable=False, comment="盈亏比")

    # 元数据
    kline_count = Column(Integer, default=0, nullable=False, comment="回测使用的K线数量")
    data_start = Column(String(20), nullable=True, comment="数据起始日期")
    data_end = Column(String(20), nullable=True, comment="数据结束日期")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")
