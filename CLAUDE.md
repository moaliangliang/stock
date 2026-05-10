# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

量化交易平台 (Quantitative Trading Platform) — a full-stack web app with a FastAPI backend, Vue 3 frontend, Celery task workers, PostgreSQL or SQLite database, and Redis for caching/message brokering. Provides market data display, strategy backtesting, live trading, risk management, and price alerts for Chinese A-share stocks.

## Development commands

### Backend (Python 3.10+, FastAPI)

```bash
cd backend
source venv/bin/activate

# Start dev server (SQLite by default when DEBUG=true)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run tests
pytest
```

### Frontend (Node.js 18+, Vue 3 + Vite)

```bash
cd frontend

# Start dev server (proxies /api to localhost:8000)
npm run dev

# Type-check and build
npm run build
```

### Docker (full stack: PostgreSQL, Redis, backend, Celery worker, Celery beat, Nginx)

```bash
docker-compose up -d
```

### Database migrations (Alembic)

```bash
cd backend
source venv/bin/activate

# Generate new migration from model changes
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

## Architecture

### Backend (`backend/`)

```
app/
  main.py              FastAPI app entry point, lifespan, middleware, routers
  core/
    config.py           Pydantic Settings from env vars (DB, Redis, Celery, CORS)
    database.py         SQLAlchemy async engine + session (uses aiosqlite in DEBUG, asyncpg in production)
    security.py         JWT creation/verification, bcrypt password hashing
    deps.py             FastAPI dependency injection (get_db, get_current_user)
    celery_app.py       Celery instance + beat schedule (market data, strategies, alerts, stock push, cleanup)
    redis.py            Redis client helper
    market_constants.py Market hours, holiday calendars for A-shares
  models/               SQLAlchemy ORM models: User, Strategy, KLine, Ticker, SymbolInfo, Order, Trade, Position, RiskRule, RiskRecord, PriceAlert, SystemLog, TradeLog, StrategyRunLog
  schemas/              Pydantic request/response schemas
  api/v1/endpoints/     REST endpoints (auth, market, strategy, backtest, trade, risk, data, notification, alert)
  api/v1/ws.py          WebSocket endpoint for real-time ticker streaming (/api/v1/ws/tickers)
  services/             Business logic layer (auth, market, strategy, backtest, trade, risk, alert, notification, data provider, WebSocket manager)
  tasks/                Celery async tasks (market data update, alert checking, scheduled strategies, stock push notifications, data cleanup)
  utils/                Logger setup, rate limiter middleware, exchange adapter
```

Key design patterns:
- **Dual database mode**: When `DEBUG=true`, uses SQLite (file `quant_trade.db` in backend root). When `DEBUG=false`, uses PostgreSQL via asyncpg. This lets you develop without Docker.
- **Data provider abstraction**: `MARKET_DATA_PROVIDER` configures whether market data comes from `mock` (simulated), `akshare` (A-share scraping), or `ccxt` (crypto exchanges). See `app/services/data_provider.py`.
- **Order execution modes**: `ORDER_EXECUTION_MODE` can be `sandbox` (simulated) or connected to a real exchange via the exchange adapter.
- **Rate limiting**: Only activates when `DEBUG=false`. RateLimiterMiddleware at 60 req/min.

### Frontend (`frontend/src/`)

```
views/          Login, Register, Dashboard, Market, Strategy, Backtest, Trade, Risk
components/     ChangePasswordDialog, NotificationBell, StrategyLogViewer, StrategyScheduleForm
api/            Axios API client modules (mirrors backend endpoint groups)
composables/    useECharts, useTheme, useWebSocket
store/          Pinia stores: useAuthStore (JWT token + user), useAppStore (sidebar, theme)
router/         Vue Router with auth guard
```

Stack: Vue 3 (Composition API), Element Plus UI, ECharts, dayjs, Pinia. Auto-import for Element Plus components via `unplugin-vue-components`.

### Celery task system

4 Celery queues: `market`, `strategy`, `backtest`, `maintenance`. Beat schedule handles periodic tasks: market data refresh (every 5s), strategy scheduling (every 60s), price alert checks (every 10s), stock push notifications (weekdays 9:00-15:55), daily data cleanup.
