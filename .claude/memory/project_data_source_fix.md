---
name: 数据源修复进度
description: 东方财富API不可用，正在切换到新浪财经数据源
type: project
---

## 背景
东方财富 API（push2.eastmoney.com）拒绝海外IP连接（TLS握手成功但HTTP请求被断开）。用户选择方案1：修复数据源。

## 已完成
1. **创建 `backend/app/utils/sina_client.py`** ✅ — 新浪财经客户端，实现了：
   - `fetch_realtime_quotes(symbols)` — 批量实时行情，接口 `hq.sinajs.cn`
   - `fetch_kline(symbol, interval)` — K线日线数据（2000条回溯到2018年）
   - 测试通过：600028.SH=5.39（与用户说的真实价格一致）

2. **更新 `backend/app/services/data_provider.py`** ✅ — 添加了：
   - `_refresh_tickers_from_sina()` 函数
   - `fetch_klines_from_sina()` 函数
   - `refresh_all_tickers()` / `refresh_ticker()` 支持 `sina` provider
   - `fetch_real_klines()` 添加新浪作为数据源

3. **更新 `backend/app/core/config.py`** ✅ — `MARKET_DATA_PROVIDER` 默认值改为 `"sina"`

## 待完成
4. **更新 `backend/app/core/market_constants.py`** — 将 BASE_PRICES 从旧的 mock 基准价更新为从新浪获取的真实股价
5. **清除旧 mock 数据 + 拉取真实数据** — 编写脚本清除 `kline_data` 表中的 mock 数据，从新浪API拉取所有活跃标的的真实K线写入
6. **重启后端服务** — 让配置和代码变更生效
7. **验证** — 确认前端显示的行情、K线都是真实数据
