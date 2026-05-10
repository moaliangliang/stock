-- ==========================================
-- 量化交易平台 - 数据库初始化脚本
-- ==========================================

-- 创建扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- 插入测试标的
INSERT INTO symbol_info (symbol, name, exchange, asset_type, price_precision, qty_precision, min_qty, tick_size, status)
VALUES
    ('BTC/USDT', 'Bitcoin/USDT', 'binance', 'crypto', 2, 4, 0.0001, 0.01, 'active'),
    ('ETH/USDT', 'Ethereum/USDT', 'binance', 'crypto', 2, 4, 0.001, 0.01, 'active'),
    ('AAPL', 'Apple Inc.', 'NASDAQ', 'stock', 2, 0, 1, 0.01, 'active'),
    ('GOOGL', 'Alphabet Inc.', 'NASDAQ', 'stock', 2, 0, 1, 0.01, 'active'),
    ('TSLA', 'Tesla Inc.', 'NASDAQ', 'stock', 2, 0, 1, 0.01, 'active'),
    ('000001.SZ', '平安银行', 'SZSE', 'stock', 2, 0, 100, 0.01, 'active'),
    ('600519.SH', '贵州茅台', 'SSE', 'stock', 2, 0, 100, 0.01, 'active'),
    ('300750.SZ', '宁德时代', 'SZSE', 'stock', 2, 0, 100, 0.01, 'active');

-- 插入默认风控规则
INSERT INTO risk_rules (user_id, name, rule_type, action, is_active, params, description)
VALUES
    (NULL, '全局-单日最大亏损', 'max_daily_loss', 'block', true, '{"ratio": 5}', '单日亏损超过5%时阻止交易'),
    (NULL, '全局-最大仓位比例', 'max_position_ratio', 'block', true, '{"ratio": 30}', '单标的仓位不超过总资产30%'),
    (NULL, '全局-止损规则', 'stop_loss', 'warn', true, '{"ratio": 2}', '单笔亏损超过2%时警告');

-- 插入测试行情数据（使用 mock 数据）
-- 这些会在后端启动时通过 API 自动生成

-- 插入默认管理员用户（密码: admin123）
-- 密码哈希对应 bcrypt('admin123')
-- 注意：此操作通过后端 API 完成更安全，此处仅为参考
-- INSERT INTO users (username, email, hashed_password, role, is_active, is_superuser)
-- VALUES ('admin', 'admin@quanttrade.com', '$2b$12$...', 'admin', true, true);
