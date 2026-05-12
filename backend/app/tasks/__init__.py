from app.tasks.market import update_market_data, cleanup_old_data
from app.tasks.strategy import run_scheduled_strategies, run_single_strategy
from app.tasks.backtest import run_async_backtest
from app.tasks.alert import check_price_alerts
from app.tasks.stock_push import run_stock_push
from app.tasks.signal_scanner import run_signal_scanner
from app.tasks.ma_monitor import run_ma_monitor
from app.tasks.auto_close import check_stop_orders, close_expired_positions
