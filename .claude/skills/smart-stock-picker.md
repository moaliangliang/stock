# smart-stock-picker: 妙想智能选股 Skill

## 触发方式

用户输入包含选股/筛选/扫股/排行/发现/推荐等选股意图时自动激活。直接说股票名（如"中国石化"）不走此skill，那是个股分析。

## 可用功能

### 1. 条件选股 — `/选股 <条件>`

支持组合条件，逗号分隔：

| 条件 | 说明 | 示例 |
|------|------|------|
| `超卖` | RSI<30 或 J<20 | `/选股 超卖` |
| `超买` | RSI>70 或 J>80 | `/选股 超买` |
| `破净` | PB<1 | `/选股 破净` |
| `低估值` | PE<20 | `/选股 低估值` |
| `高成长` | 营收增速>20% | `/选股 高成长` |
| `资金流入` | CMF>0.1 | `/选股 资金流入` |
| `底背离` | MACD/量价底背离 | `/选股 底背离` |
| `多头排列` | 均线多头 | `/选股 多头排列` |
| `趋势` | 回测趋势策略有效(MA>10%) | `/选股 趋势` |
| `震荡` | 回测反转策略有效(KDJ>10%) | `/选股 震荡` |
| `金叉` | MA5刚上穿MA20 | `/选股 金叉` |
| `死叉` | MA5刚下穿MA20 | `/选股 死叉` |
| `买入信号` | 综合评分>65 推荐买入 | `/选股 买入信号` |

### 2. 相似股发现 — `/相似股 <标的>`

找与指定股票股性相似（趋势/震荡风格一致）的标的。

### 3. 回测排行 — `/排行 <策略>`

对所有活跃标的跑指定策略并按收益排名。

### 4. 批量扫描 — `/扫股`

扫描所有活跃标的，逐只输出五因子综合评分排名。

---

## 执行流程

### 条件选股

```bash
cd /root/workspace/stock/backend && source venv/bin/activate && python scripts/smart_screener.py --filter "<条件1>,<条件2>" --top 10
```

脚本未创建则直接调用后端函数，步骤：
1. 读取 SymbolInfo 获取所有活跃标的
2. 对每只股票拉取最新日线数据（已有则跳过）
3. 用 `app.services.decision` 中的各因子函数计算指标
4. 按条件筛选，输出排名表
5. 可选：对筛选结果批量运行回测对比

### 相似股

```bash
cd /root/workspace/stock/backend && source venv/bin/activate && python scripts/smart_screener.py --similar "<symbol>" --top 5
```

1. 对目标标的计算完整五因子+五策略回测
2. 对其他活跃标的做同样计算
3. 用回测向量（五个策略收益率）做余弦相似度匹配
4. 输出最相似的5只

### 回测排行

```bash
cd /root/workspace/stock/backend && source venv/bin/activate && python scripts/smart_screener.py --rank "MA交叉" --top 10
```

对所有活跃标的跑指定策略回测，按年化收益率降序排列。

### 批量扫描

```bash
cd /root/workspace/stock/backend && source venv/bin/activate && python scripts/smart_screener.py --scan --top 20
```

---

## 输出格式

```
## 选股结果: <条件>

| 排名 | 代码 | 名称 | 当前价 | RSI | PE | 评分 | 最佳策略 |
|------|------|------|--------|-----|-----|------|----------|
| 1 | xxx | xxx | xx | xx | xx | xx | xxx |

### 推荐操作
| 股票 | 策略 | 入场 | 理由 |
|------|------|------|------|
```

---

## 技术约束

- 数据源：新浪财经（实时）+ 东方财富Skills（基本面，月300次）
- 回测引擎：`app.services.backtest.run_backtest()`
- 因子引擎：`app.services.decision` 中各 `_calc_*_score` 函数
- 首次对某标的选股会自动拉取K线数据，耗3-5秒/只
- 如果数据库已有数据则秒出结果
- 活跃标的列表来自 `symbol_info WHERE status='active'`

## 已分析标的（结果可复用）

截至目前已拉取日线数据的标的：600028.SH, 002475.SZ, 601633.SH, 600036.SH, 600789.SH, 600519.SH, 002475.SZ, 002202.SZ, 000001.SZ, 300750.SZ 及各ETF。新增标的首次分析需要拉数据。
