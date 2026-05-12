"""
行情中心模型 - K线数据、实时行情、标的信息
"""
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, Text, BigInteger, Index, UniqueConstraint

from app.core.database import Base


class SymbolInfo(Base):
    """标的物信息表"""
    __tablename__ = "symbol_info"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), unique=True, index=True, nullable=False, comment="标的代码")
    name = Column(String(100), nullable=False, comment="标的名称")
    exchange = Column(String(20), nullable=False, comment="交易所")
    asset_type = Column(String(20), default="stock", comment="资产类型: stock/crypto/future/forex")
    price_precision = Column(Integer, default=2, comment="价格精度")
    qty_precision = Column(Integer, default=0, comment="数量精度")
    min_qty = Column(Float, default=0, comment="最小交易数量")
    max_qty = Column(Float, default=0, comment="最大交易数量")
    tick_size = Column(Float, default=0.01, comment="最小变动价位")
    status = Column(String(10), default="active", comment="状态: active/inactive")
    fundamental_cache = Column(Text, nullable=True, comment="基本面数据缓存JSON")
    fundamental_cached_at = Column(DateTime(timezone=True), nullable=True, comment="基本面缓存时间")
    is_watched = Column(Boolean, default=False, comment="自选标的，Celery定时决策仅处理此项为True的股票")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class KLine(Base):
    """K线数据表"""
    __tablename__ = "kline_data"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "timestamp", name="uq_kline"),
        Index("idx_kline_symbol_interval_ts", "symbol", "interval", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True, comment="标的代码")
    interval = Column(String(10), nullable=False, comment="时间周期: 1m/5m/15m/30m/60m/1d")
    timestamp = Column(DateTime(timezone=True), nullable=False, comment="K线时间")
    open = Column(Float, nullable=False, comment="开盘价")
    high = Column(Float, nullable=False, comment="最高价")
    low = Column(Float, nullable=False, comment="最低价")
    close = Column(Float, nullable=False, comment="收盘价")
    volume = Column(Float, default=0, comment="成交量")
    amount = Column(Float, default=0, comment="成交额")
    data_source = Column(String(20), nullable=False, default="unknown", index=True,
                         comment="数据来源: eastmoney/sina/akshare/mock/unknown")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Ticker(Base):
    """实时行情快照表"""
    __tablename__ = "ticker_data"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), unique=True, index=True, nullable=False, comment="标的代码")
    last_price = Column(Float, nullable=False, comment="最新价")
    bid = Column(Float, nullable=True, comment="买一价")
    ask = Column(Float, nullable=True, comment="卖一价")
    bid_volume = Column(Float, nullable=True, comment="买一量")
    ask_volume = Column(Float, nullable=True, comment="卖一量")
    high_24h = Column(Float, nullable=True, comment="24小时最高")
    low_24h = Column(Float, nullable=True, comment="24小时最低")
    volume_24h = Column(Float, nullable=True, comment="24小时成交量")
    change_24h = Column(Float, nullable=True, comment="24小时涨跌幅(%)")
    prev_close = Column(Float, nullable=True, comment="昨日收盘价")
    turnover_24h = Column(Float, nullable=True, comment="24小时成交额")
    data_source = Column(String(20), nullable=False, default="unknown", index=True,
                         comment="数据来源: eastmoney/sina/mock/unknown")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
