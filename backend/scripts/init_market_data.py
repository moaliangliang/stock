"""
Data initialization script — fetch real ticker data and populate kline data.
Uses real APIs when available, falls back to realistic mock data anchored to real prices.

Usage:
    cd backend && source venv/bin/activate && python scripts/init_market_data.py
"""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SyncSessionLocal
from app.core.config import settings
from app.models.market_data import SymbolInfo, KLine, Ticker
from app.services.data_provider import (
    refresh_all_tickers,
    fetch_real_klines,
)
from app.services.market import save_kline_data_sync, mock_market_data
from sqlalchemy import select


def init_tickers(db):
    """Fetch real ticker data for all active symbols."""
    print("=== Fetching ticker data ===")
    refresh_all_tickers(db)
    db.commit()
    print("Ticker data updated.")


def get_ticker_price(db, symbol):
    """Get current ticker last_price for a symbol."""
    ticker = db.execute(
        select(Ticker.last_price).where(Ticker.symbol == symbol)
    ).scalar_one_or_none()
    return float(ticker) if ticker else None


def init_klines(db):
    """Fetch real daily kline for A-shares; mock for non-A-shares.
    For A-shares where real API fails, generate mock data anchored to real ticker price."""
    print("\n=== Fetching K-line data ===")
    symbols = db.execute(
        select(SymbolInfo).where(SymbolInfo.status == "active")
    ).scalars().all()

    a_shares = [s for s in symbols if s.symbol.endswith((".SH", ".SZ"))]
    others = [s for s in symbols if not s.symbol.endswith((".SH", ".SZ"))]

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

    for sym in a_shares:
        # Skip if already has data
        existing = db.execute(
            select(KLine.timestamp).where(
                KLine.symbol == sym.symbol, KLine.interval == "1d"
            ).limit(1)
        ).scalar_one_or_none()
        if existing:
            print(f"  {sym.symbol}: already has kline data, skipping.")
            continue

        print(f"  {sym.symbol}: trying real API ...", end=" ")
        data = fetch_real_klines(sym.symbol, "1d", start_date, end_date)
        if data:
            inserted = save_kline_data_sync(db, sym.symbol, "1d", data)
            db.commit()
            print(f"OK — {inserted} new rows ({len(data)} total)")
        else:
            # Fallback: mock data anchored to real ticker price
            base = get_ticker_price(db, sym.symbol)
            if base is None:
                from app.core.market_constants import BASE_PRICES
                base = BASE_PRICES.get(sym.symbol, 100.0)
            print(f"API unavailable, generating mock (base={base}) ...", end=" ")
            data = mock_market_data(
                base_price=base, days=365, interval_minutes=1440, volatility=0.025
            )
            inserted = save_kline_data_sync(db, sym.symbol, "1d", data)
            db.commit()
            print(f"{inserted} rows")

    for sym in others:
        existing = db.execute(
            select(KLine.timestamp).where(
                KLine.symbol == sym.symbol, KLine.interval == "1d"
            ).limit(1)
        ).scalar_one_or_none()
        if existing:
            print(f"  {sym.symbol}: already has data, skipping.")
            continue
        print(f"  {sym.symbol}: generating mock kline ...", end=" ")
        data = mock_market_data(days=365, interval_minutes=1440, volatility=0.02)
        inserted = save_kline_data_sync(db, sym.symbol, "1d", data)
        db.commit()
        print(f"{inserted} rows")


def print_summary(db):
    """Print summary of populated data."""
    print("\n=== Summary ===")
    tickers = db.execute(select(Ticker)).scalars().all()
    print(f"Ticker records: {len(tickers)}")
    for t in tickers:
        print(f"  {t.symbol}: price={t.last_price}, change={t.change_24h}%, updated={t.updated_at}")

    from collections import Counter
    klines = db.execute(
        select(KLine.symbol, KLine.interval)
    ).all()
    ksum = Counter()
    for k in klines:
        ksum[(k.symbol, k.interval)] += 1
    print("K-line records:")
    for (sym, iv), cnt in sorted(ksum.items()):
        print(f"  {sym} ({iv}): {cnt} bars")


def main():
    print(f"Provider: {settings.MARKET_DATA_PROVIDER}")
    print(f"Database: {settings.DATABASE_URL}")
    db = SyncSessionLocal()
    try:
        init_tickers(db)
        init_klines(db)
        print_summary(db)
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
