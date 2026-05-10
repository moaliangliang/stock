"""
Fetch missing K-line data with retries and backoff.
Targets A-share symbols that don't have 1d kline data yet.
"""
import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone, timedelta
from app.core.database import SyncSessionLocal
from app.models.market_data import SymbolInfo, KLine
from app.services.data_provider import fetch_klines_from_sina, fetch_klines_from_eastmoney
from app.services.market import save_kline_data_sync
from sqlalchemy import select, func, or_


def get_missing_symbols(db):
    """Find A-share symbols with no 1d kline data."""
    all_active = db.execute(
        select(SymbolInfo.symbol).where(
            SymbolInfo.status == "active",
            or_(SymbolInfo.symbol.like('%.SH'), SymbolInfo.symbol.like('%.SZ'))
        )
    ).scalars().all()

    has_data = db.execute(
        select(KLine.symbol).where(KLine.interval == "1d").distinct()
    ).scalars().all()

    missing = [s for s in all_active if s not in has_data]
    return missing


def fetch_with_retry(symbol, max_retries=3):
    """Try sina first, then eastmoney, with backoff."""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

    for attempt in range(max_retries):
        # Try Sina first
        print(f"    Sina attempt {attempt+1}...", end=" ")
        data = fetch_klines_from_sina(symbol, "1d", start, end)
        if data:
            print(f"OK ({len(data)} bars)")
            return data

        # Try Eastmoney
        print(f"Eastmoney...", end=" ")
        time.sleep(2)  # backoff
        data = fetch_klines_from_eastmoney(symbol, "1d", start, end)
        if data:
            print(f"OK ({len(data)} bars)")
            return data

        print(f"failed, waiting {2**(attempt+1)}s...")
        time.sleep(2 ** (attempt + 1))

    return None


def main():
    db = SyncSessionLocal()
    try:
        missing = get_missing_symbols(db)
        print(f"Missing K-line for: {missing}")

        for sym in missing:
            print(f"\nFetching {sym}...")
            data = fetch_with_retry(sym)
            if data:
                inserted = save_kline_data_sync(db, sym, "1d", data)
                db.commit()
                print(f"  Saved {inserted} new bars out of {len(data)} total")
            else:
                print(f"  FAILED: no data after retries")

        # Show final summary
        print("\n=== Final K-line counts ===")
        rows = db.execute(
            select(KLine.symbol, KLine.interval, func.count())
            .where(KLine.interval == "1d")
            .group_by(KLine.symbol, KLine.interval)
        ).all()
        for r in rows:
            print(f"  {r[0]}: {r[2]} bars")

    finally:
        db.close()


if __name__ == "__main__":
    main()
