#!/usr/bin/env python3
"""
数据种子脚本 - 初始化测试数据
用于首次部署后填充基础数据

用法: python scripts/seed_data.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core.database import async_session_factory, init_db
from app.core.security import get_password_hash
from app.models.user import User, UserRole
from app.models.market_data import SymbolInfo
from app.models.risk import RiskRule, RiskRuleType, RiskAction
from sqlalchemy import select


async def seed():
    """填充基础数据"""
    await init_db()
    async with async_session_factory() as session:
        # 1. 检查是否已有管理员
        result = await session.execute(select(User).where(User.username == "admin"))
        if not result.scalar_one_or_none():
            admin = User(
                username="admin",
                email="admin@quanttrade.com",
                hashed_password=get_password_hash("admin123"),
                nickname="系统管理员",
                role=UserRole.ADMIN,
                is_active=True,
                is_superuser=True,
            )
            session.add(admin)
            print("✅ 创建管理员用户: admin / admin123")

            # 创建测试用户
            trader = User(
                username="trader",
                email="trader@quanttrade.com",
                hashed_password=get_password_hash("trader123"),
                nickname="交易员",
                role=UserRole.TRADER,
                is_active=True,
            )
            session.add(trader)
            print("✅ 创建交易员用户: trader / trader123")
        else:
            print("ℹ️  管理员用户已存在，跳过")

        # 2. 检查是否已有标的
        result = await session.execute(select(SymbolInfo).limit(1))
        if not result.scalar_one_or_none():
            symbols = [
                SymbolInfo(symbol="BTC/USDT", name="Bitcoin/USDT", exchange="binance", asset_type="crypto", price_precision=2, qty_precision=4, min_qty=0.0001, tick_size=0.01),
                SymbolInfo(symbol="ETH/USDT", name="Ethereum/USDT", exchange="binance", asset_type="crypto", price_precision=2, qty_precision=4, min_qty=0.001, tick_size=0.01),
                SymbolInfo(symbol="AAPL", name="Apple Inc.", exchange="NASDAQ", asset_type="stock", price_precision=2, qty_precision=0, min_qty=1, tick_size=0.01),
                SymbolInfo(symbol="GOOGL", name="Alphabet Inc.", exchange="NASDAQ", asset_type="stock", price_precision=2, qty_precision=0, min_qty=1, tick_size=0.01),
                SymbolInfo(symbol="TSLA", name="Tesla Inc.", exchange="NASDAQ", asset_type="stock", price_precision=2, qty_precision=0, min_qty=1, tick_size=0.01),
                SymbolInfo(symbol="000001.SZ", name="平安银行", exchange="SZSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="600519.SH", name="贵州茅台", exchange="SSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="300750.SZ", name="宁德时代", exchange="SZSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="002475.SZ", name="立讯精密", exchange="SZSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="002202.SZ", name="金风科技", exchange="SZSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="601633.SH", name="长城汽车", exchange="SSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="600028.SH", name="中国石化", exchange="SSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="000338.SZ", name="潍柴动力", exchange="SZSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
                SymbolInfo(symbol="002384.SZ", name="东山精密", exchange="SZSE", asset_type="stock", price_precision=2, qty_precision=0, min_qty=100, tick_size=0.01),
            ]
            session.add_all(symbols)
            print(f"✅ 创建 {len(symbols)} 个交易标的")
        else:
            print("ℹ️  交易标的数据已存在，跳过")

        # 3. 检查是否已有风控规则
        result = await session.execute(select(RiskRule).limit(1))
        if not result.scalar_one_or_none():
            rules = [
                RiskRule(name="全局-单日最大亏损", rule_type=RiskRuleType.MAX_DAILY_LOSS, action=RiskAction.BLOCK, is_active=True, params='{"ratio": 5}', description="单日亏损超过5%时阻止交易"),
                RiskRule(name="全局-最大仓位比例", rule_type=RiskRuleType.MAX_POSITION_RATIO, action=RiskAction.BLOCK, is_active=True, params='{"ratio": 30}', description="单标的仓位不超过总资产30%"),
                RiskRule(name="全局-止损规则", rule_type=RiskRuleType.STOP_LOSS, action=RiskAction.WARN, is_active=True, params='{"ratio": 2}', description="单笔亏损超过2%时警告"),
            ]
            session.add_all(rules)
            print(f"✅ 创建 {len(rules)} 条风控规则")
        else:
            print("ℹ️  风控规则已存在，跳过")

        await session.commit()

    print("\n🎉 数据初始化完成！")
    print("   管理员账号: admin / admin123")
    print("   交易员账号: trader / trader123")


if __name__ == "__main__":
    asyncio.run(seed())
