# 投资决策引擎 — 系统详细设计文档

> 覆盖 `app/services/decision.py` 全部数学公式，从底层算子到最终综合评分的完整推导链路。

---

## 目录

1. [核心数学算子](#1-核心数学算子)
2. [动态权重计算](#2-动态权重计算)
3. [多因子综合评分](#3-多因子综合评分)
4. [评分归一化（tanh）](#4-评分归一化tanh)
5. [推荐等级阈值](#5-推荐等级阈值)
6. [自适应阈值（信号分歧调节）](#6-自适应阈值信号分歧调节)
7. [市场状态分类（ADX + 布林带）](#7-市场状态分类adx--布林带)
8. [状态转换检测](#8-状态转换检测)
9. [技术面因子](#9-技术面因子)
10. [情绪面因子](#10-情绪面因子)
11. [风险面因子](#11-风险面因子)
12. [动量面因子](#12-动量面因子)
13. [基本面因子](#13-基本面因子)
14. [ETF 跟踪误差](#14-etf-跟踪误差)
15. [市场环境调整](#15-市场环境调整)
16. [目标价与止损](#16-目标价与止损)
17. [多周期确认](#17-多周期确认)
18. [信号相关性折扣](#18-信号相关性折扣)
19. [配置系数速查](#19-配置系数速查)

---

## 1. 核心数学算子

### 1.1 简单移动平均（SMA）

$$\text{SMA}_t(N) = \frac{1}{N} \sum_{i=0}^{N-1} p_{t-i}$$

通过累积和优化实现：

$$\text{SMA}_{N-1..} = \frac{\text{cumsum}[N:] - \text{cumsum}[: -N]}{N}$$

### 1.2 滚动标准差

$$\sigma_t(N) = \sqrt{ \frac{1}{N} \sum_{i=0}^{N-1} (p_{t-i} - \bar{p})^2 }$$

使用 `ddof=0`（总体标准差）。

### 1.3 指数加权移动平均（EWMA）

$$\text{EWMA}_t = \alpha \cdot p_t + (1-\alpha) \cdot \text{EWMA}_{t-1}, \quad \alpha = \frac{1}{\text{span}}$$

### 1.4 标准指数移动平均（EMA）

$$\text{EMA}_t(P) = \alpha \cdot p_t + (1-\alpha) \cdot \text{EMA}_{t-1}(P), \quad \alpha = \frac{2}{P+1}$$

### 1.5 Wilder 平滑（用于 RSI / ADX）

$$\text{Wilder}_t(P) = \alpha \cdot p_t + (1-\alpha) \cdot \text{Wilder}_{t-1}(P), \quad \alpha = \frac{1}{P}$$

> **与标准 EMA 的关键差异**：Wilder 使用 $\alpha = 1/N$ 而非 $2/(N+1)$，产生更慢、更稳定的平滑效果，适合震荡指标的基准线计算。

初始化方式：首个非零/非 NaN 位置直接取原始值，后续按 EMA 递推。ADX 的首个有效值使用 SMA 初始化：

$$\text{Wilder}_{start+P-1} = \frac{1}{P}\sum_{i=start}^{start+P-1} p_i$$

之后对于 $t \geq start+P$：

$$\text{Wilder}_t = \frac{p_t + (P-1) \cdot \text{Wilder}_{t-1}}{P}$$

---

## 2. 动态权重计算

动态权重根据当前市场微观结构（趋势强度、波动率、方向）连续调整五个因子的权重，替代旧版硬分类的四种预设状态。

### 2.1 Sigmoid 平滑函数

$$\sigma(x; k) = \frac{1}{1 + e^{-k \cdot x}}$$

将任意实数映射到 $(0, 1)$ 的平滑区间。溢出保护：$x < 0$ 返回 0，$x > 0$ 返回 1。

### 2.2 趋势强度

$$\text{trend\_strength} = \sigma(\text{ADX} - C_{\text{adx}};\; k_{\text{adx}})$$

| 参数 | 默认值 | 含义 |
|-----------|---------|---------|
| $C_{\text{adx}}$ | 25.0 | ADX sigmoid 中心值（ADX > 25 视为有趋势） |
| $k_{\text{adx}}$ | 0.25 | 陡峭度（越小越平滑） |

$\text{trend\_strength} \in (0, 1)$：0 = 震荡，1 = 强趋势。

### 2.3 波动率强度

$$\text{BB\_width} = \frac{2 \cdot \sigma_{20}}{\text{MA}_{20}}$$

$$\text{vol\_strength} = \sigma(\text{BB\_width} - C_{\text{bb}};\; k_{\text{bb}})$$

| 参数 | 默认值 | 含义 |
|-----------|---------|---------|
| $C_{\text{bb}}$ | 0.08 | 布林带宽中心值（8%） |
| $k_{\text{bb}}$ | 60.0 | 陡峭度 |

$\text{vol\_strength} \in (0, 1)$：0 = 平静，1 = 高波动。

### 2.4 趋势方向偏差

$$\text{trend\_bias} = \text{clamp}\left( \left(\frac{P_{\text{close}}}{\text{MA}_{20}} - 1\right) \times S_{\text{trend}},\; -1,\; 1 \right)$$

| 参数 | 默认值 | 含义 |
|-----------|---------|---------|
| $S_{\text{trend}}$ | 10.0 | 趋势偏差放大系数 |

$-1$ = 下跌趋势，$+1$ = 上涨趋势。

### 2.5 权重调整流程

**初始基础权重**：

$$W_{\text{tech}}^{(0)} = 0.35,\; W_{\text{sent}}^{(0)} = 0.20,\; W_{\text{risk}}^{(0)} = 0.25,\; W_{\text{mom}}^{(0)} = 0.10,\; W_{\text{fund}}^{(0)} = 0.10$$

**趋势强度调整**：

$$W_{\text{mom}}^{(1)} = W_{\text{mom}}^{(0)} + 0.18 \times \text{trend\_strength}$$
$$W_{\text{fund}}^{(1)} = W_{\text{fund}}^{(0)} - 0.05 \times \text{trend\_strength}$$

**波动率调整**：

$$W_{\text{risk}}^{(2)} = W_{\text{risk}}^{(1)} + 0.10 \times \text{vol\_strength}$$
$$W_{\text{sent}}^{(2)} = W_{\text{sent}}^{(1)} + 0.10 \times \text{vol\_strength}$$
$$W_{\text{mom}}^{(2)} = W_{\text{mom}}^{(1)} - 0.08 \times \text{vol\_strength}$$

**下跌趋势偏移**（$\text{trend\_bias} < -0.02$）：

$$W_{\text{mom}} \mathrel{-}= |\text{trend\_bias}| \times 0.12$$
$$W_{\text{sent}} \mathrel{+}= |\text{trend\_bias}| \times 0.12$$

**上涨趋势偏移**（$\text{trend\_bias} > 0.02$）：

$$W_{\text{mom}} \mathrel{+}= \text{trend\_bias} \times 0.08$$
$$W_{\text{tech}} \mathrel{+}= \text{trend\_bias} \times 0.08 \times 0.50$$

### 2.6 钳位边界

| 因子 | 下限 | 上限 |
|--------|-----|-----|
| 技术面 | 0.15 | 0.50 |
| 情绪面 | 0.10 | 0.40 |
| 风险面 | 0.10 | 0.40 |
| 动量面 | 0.02 | 0.35 |
| 基本面 | 0.05 | 0.20 |

### 2.7 归一化

$$W_i^{\text{final}} = \frac{ \text{clamp}(W_i, \min_i, \max_i) }{ \sum_j \text{clamp}(W_j, \min_j, \max_j) }$$

保证 $\sum W_i^{\text{final}} = 1.0$。

---

## 3. 多因子综合评分

### 3.1 日线 + 周线技术面加权

$$S_{\text{tech}}^{\text{composite}} = 0.70 \times S_{\text{tech}}^{\text{daily}} + 0.30 \times S_{\text{tech}}^{\text{weekly}}$$

### 3.2 加权线性组合

$$\text{raw\_weighted} = \sum_{i \in \{\text{tech},\,\text{sent},\,\text{risk},\,\text{mom},\,\text{fund}\}} W_i \times S_i$$

### 3.3 信号分歧惩罚

当五个因子的评分方向不一致时，惩罚综合评分：

$$\text{score\_std} = \sqrt{ \frac{1}{5} \sum_{i=1}^{5} (S_i - \bar{S})^2 }$$

$$\text{disagreement\_penalty} = \min(0.30 \times \text{score\_std},\; 15.0)$$

$$\text{composite} = \text{clamp}(\text{raw\_weighted} - \text{disagreement\_penalty},\; 0,\; 100)$$

### 3.4 一致性因子（置信度校准）

$$\text{agreement\_factor} = \max\left(0.40,\; 1.0 - \frac{\text{score\_std}}{80.0}\right)$$

$$\text{confidence} = \text{clamp}(\text{composite} \times \text{agreement\_factor},\; 0,\; 100)$$

置信度保底机制：当 $\text{confidence} < 10$ 且 $\text{composite} > 20$ 时，置信度保底为 10。

### 3.5 状态转换乘数

$$\text{composite} \leftarrow \text{composite} \times \text{transition\_multiplier}$$

| 转换方向 | 乘数 |
|------------|------------|
| 进入趋势 | $\times 1.06$ |
| 退出趋势 | $\times 0.92$ |
| 稳态 | $\times 1.00$ |

### 3.6 市场环境基线调整

$$\text{composite} \leftarrow \text{composite} + \text{market\_adjustment}$$

market_adjustment 通常在 $\pm 5$ 分以内（详见第 15 章）。

---

## 4. 评分归一化（tanh）

$$\text{score} = \text{clamp}\left( 50 + 50 \times \tanh\left( \frac{\sum a_i}{70} \right),\; 0,\; 100 \right)$$

其中 $a_i$ 为各子信号的调整量（正 = 看多加分，负 = 看空减分）。

| 参数 | 默认值 | 含义 |
|-----------|---------|---------|
| baseline | 50 | 中性基线 |
| divisor | 70 | tanh 除数（越小越敏感，越大越线性） |

除数 70 相比旧值 50 扩大了约 40% 的线性区域，使 80–100 高置信区间的信号仍然可区分。

> **风险因子特殊处理**：风险评分基线为 80（高于其他因子的 50），体现默认给予更高风险容忍度的设计意图。

$$S_{\text{risk}} = \text{clamp}\left( 80 + 50 \times \tanh\left( \frac{\sum a_i^{\text{risk}}}{70} \right),\; 0,\; 100 \right)$$

---

## 5. 推荐等级阈值

| 等级 | 阈值（agreement=1.0） | 含义 |
|-------|---------------------------|-------|
| STRONG_BUY | $\geq 85$ | 强烈买入 |
| BUY | $\geq 65$ | 买入 |
| HOLD | $\geq 35$ | 持有 |
| SELL | $\geq 15$ | 卖出 |
| STRONG_SELL | $< 15$ | 强烈卖出 |

---

## 6. 自适应阈值（信号分歧调节）

当因子之间信号不一致（低 agreement_factor）时，HOLD 区扩宽、STRONG 门槛抬高，反映真实的不确定性而非强行给出确定性标签。

定义分歧度：

$$d = 1 - \text{agreement\_factor} \in [0,\; 0.6]$$

$$\text{hold\_expansion} = d \times 12$$
$$\text{strong\_barrier} = d \times 10$$

**自适应规则**：

| 条件 | 结果 |
|-----------|--------|
| $\text{composite} \geq 85 + \text{strong\_barrier}$ | STRONG_BUY |
| $\text{composite} \geq 65 + \text{hold\_expansion} \times 0.6$ | BUY |
| $\text{composite} \geq 35 - \text{hold\_expansion} \times 0.6$ | HOLD |
| $\text{composite} \geq 15 - \text{strong\_barrier}$ | SELL |
| 其余 | STRONG_SELL |

**最大分歧时**（$d = 0.6$）：HOLD 区扩宽 $\pm 7.2$ 分，STRONG 门槛抬高 6 分。

---

## 7. 市场状态分类（ADX + 布林带）

### 7.1 ADX 计算

**真实波幅（True Range）**：

$$\text{TR}_t = \max(H_t - L_t,\; |H_t - C_{t-1}|,\; |L_t - C_{t-1}|)$$

**方向运动（Directional Movement）**：

$$\text{+DM}_t = \begin{cases} H_t - H_{t-1} & \text{若 } H_t - H_{t-1} > L_{t-1} - L_t \text{ 且 } H_t - H_{t-1} > 0 \\ 0 & \text{否则} \end{cases}$$

$$\text{-DM}_t = \begin{cases} L_{t-1} - L_t & \text{若 } L_{t-1} - L_t > H_t - H_{t-1} \text{ 且 } L_{t-1} - L_t > 0 \\ 0 & \text{否则} \end{cases}$$

**方向指标（经 Wilder 平滑，周期=14）**：

$$\text{+DI} = 100 \times \frac{\text{Wilder}(\text{+DM}, 14)}{\text{Wilder}(\text{TR}, 14)}$$

$$\text{-DI} = 100 \times \frac{\text{Wilder}(\text{-DM}, 14)}{\text{Wilder}(\text{TR}, 14)}$$

**DX 与 ADX**：

$$\text{DX} = 100 \times \frac{|\text{+DI} - \text{-DI}|}{\text{+DI} + \text{-DI}}$$

$$\text{ADX} = \text{Wilder}(\text{DX}, 14)$$

### 7.2 分类规则

| 条件 | 状态 |
|-----------|--------|
| $\text{ADX} \geq 25$ 且 $\text{Close} > \text{MA}_{20}$ | `trending_up`（上涨趋势） |
| $\text{ADX} \geq 25$ 且 $\text{Close} \leq \text{MA}_{20}$ | `trending_down`（下跌趋势） |
| $\text{ADX} < 25$ 且 $\text{BB\_width} > 0.10$ | `volatile`（高波动） |
| $\text{ADX} < 25$ 且 $\text{BB\_width} \leq 0.10$ | `ranging`（震荡整理） |

---

## 8. 状态转换检测

检测 ADX 穿越 25 阈值的时刻——这是趋势跟踪最关键的拐点，也是最具盈利潜力的信号窗口。

**当前 ADX**：使用全部日线数据计算。

**先前 ADX**：排除最近 $L = 5$ 根 K 线，用剩余数据计算。

**转换判断逻辑**：

| 条件 | 转换类型 | 方向 | 乘数 |
|-----------|------------|-----------|------------|
| $\text{ADX}_{\text{prev}} < 25 \leq \text{ADX}_{\text{curr}}$ | `entering_trend`（进入趋势） | up/down（由 Close vs MA20 决定） | $\times 1.06$ |
| $\text{ADX}_{\text{prev}} \geq 25 > \text{ADX}_{\text{curr}}$ | `exiting_trend`（退出趋势） | up/down | $\times 0.92$ |
| 其他 | `steady`（稳态） | — | $\times 1.00$ |

---

## 9. 技术面因子

### 9.1 均线排列

| 条件 | 调整量 | 信号说明 |
|-----------|------------|--------|
| $\text{MA}_5 > \text{MA}_{20}$ | $+10$ | 短期均线在长期均线上方（多头排列） |
| $\text{MA}_5 < \text{MA}_{20}$ | $-10$ | 短期均线在长期均线下方（空头排列） |

### 9.2 MACD

$$\text{EMA}_{12} = \text{EMA}(\text{Close}, 12)$$
$$\text{EMA}_{26} = \text{EMA}(\text{Close}, 26)$$
$$\text{MACD\_line} = \text{EMA}_{12} - \text{EMA}_{26}$$
$$\text{Signal} = \text{EMA}(\text{MACD\_line}, 9)$$
$$\text{Histogram} = \text{MACD\_line} - \text{Signal}$$

| 条件 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $\text{Hist} > 0$ 且 $\text{Hist}_t > \text{Hist}_{t-1}$ | $+8$ | MACD 柱为正且扩大（动能增强） |
| $\text{Hist} > 0$ 且不扩大 | $+4$ | MACD 柱为正（多头动能） |
| $\text{Hist} < 0$ 且 $\text{Hist}_t < \text{Hist}_{t-1}$ | $-8$ | MACD 柱为负且扩大（动能减弱） |
| $\text{Hist} < 0$ 且不收缩 | $-4$ | MACD 柱为负（空头动能） |

#### MACD 背离

在最近 25 根 K 线内，通过 $\pm 2$ 邻域定义查找价格和 MACD 柱状图的局部峰谷。

**顶背离**：最近两个价格峰值——价格创新高但 MACD 柱高度下降：
$$P_{\text{high}}[t_2] > P_{\text{high}}[t_1] \;\text{且}\; \text{Hist}[t_2] < \text{Hist}[t_1] \quad\Rightarrow\quad -14$$

**底背离**：最近两个价格谷值——价格创新低但 MACD 柱回升：
$$P_{\text{low}}[t_2] < P_{\text{low}}[t_1] \;\text{且}\; \text{Hist}[t_2] > \text{Hist}[t_1] \quad\Rightarrow\quad +14$$

### 9.3 RSI（Wilder 方法，14 周期）

$$\Delta_t = \text{Close}_t - \text{Close}_{t-1}$$

$$\text{gain}_t = \max(\Delta_t, 0),\quad \text{loss}_t = \max(-\Delta_t, 0)$$

$$\text{avg\_gain} = \text{Wilder}_{14}(\text{gain}),\quad \text{avg\_loss} = \text{Wilder}_{14}(\text{loss})$$

$$\text{RS} = \frac{\text{avg\_gain}}{\text{avg\_loss}},\quad \text{RSI} = 100 - \frac{100}{1 + \text{RS}}$$

（当 $\text{avg\_loss} = 0$ 时，$\text{RSI} = 100$）

| RSI 区间 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $< 25$（深度超卖） | $+12$ | 高概率反弹 |
| $[25, 35)$（超卖） | $+8$ | 可能反弹 |
| $[35, 65]$（中性） | $+2$ | 中性区间 |
| $(65, 75]$（超买） | $-8$ | 可能回调 |
| $> 75$（深度超买） | $-12$ | 高概率回调 |

附加偏多/偏空调校：$\text{RSI} > 50$ 加 $+2$（偏多），$\text{RSI} \leq 50$ 加 $-2$（偏空）。

### 9.4 布林带（20 周期，2σ）

$$\text{BB}_{\text{mid}} = \text{MA}_{20}$$

$$\text{BB}_{\text{upper}} = \text{MA}_{20} + 2 \times \sigma_{20} \quad\quad \text{BB}_{\text{lower}} = \text{MA}_{20} - 2 \times \sigma_{20}$$

**布林带位置（百分比）**：

$$\text{BB\_position} = \frac{\text{Close} - \text{BB}_{\text{lower}}}{\text{BB}_{\text{upper}} - \text{BB}_{\text{lower}}} \times 100$$

| 条件 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $\text{Close} \leq \text{BB}_{\text{lower}} \times 1.02$ | $+8$ | 价格接近布林带下轨（超卖） |
| $\text{Close} \geq \text{BB}_{\text{upper}} \times 0.98$ | $-8$ | 价格接近布林带上轨（超买） |

### 9.5 KDJ（9-3-3）

**RSV（未成熟随机值）**：

$$\text{RSV}_t = \frac{\text{Close}_t - \min_{i=0}^{8} L_{t-i}}{\max_{i=0}^{8} H_{t-i} - \min_{i=0}^{8} L_{t-i}} \times 100$$

当 $H_{\max} = L_{\min}$ 时，$\text{RSV} = 50$。

**K / D / J 递推**：

$$K_t = \frac{2}{3} K_{t-1} + \frac{1}{3} \text{RSV}_t$$
$$D_t = \frac{2}{3} D_{t-1} + \frac{1}{3} K_t$$
$$J_t = 3K_t - 2D_t$$

初始种子：$K_{N-1} = D_{N-1} = \text{RSV}_{N-1}$。

| 条件 | 调整量 | 信号说明 |
|-----------|------------|------------|
| 金叉（$K_{t-1} \leq D_{t-1}$ 且 $K_t > D_t$）且 $K < 20$ | $+8$ | KDJ 超卖区金叉 |
| 死叉（$K_{t-1} \geq D_{t-1}$ 且 $K_t < D_t$）且 $K > 80$ | $-8$ | KDJ 超买区死叉 |
| 普通金叉（非极端区） | $+4$ | KDJ 金叉 |
| 普通死叉（非极端区） | $-4$ | KDJ 死叉 |

超买判定：$J > 100$。超卖判定：$J < 0$。

### 9.6 成交量背离

比较两个时间段：$[n-10, n-6)$ vs $[n-5, n-1]$（最近 5 根 vs 前 5 根）。

**顶背离**——价格更高但成交量萎缩（派发信号）：
$$\max(C[-5:]) > \max(C[-10:-5]) \times 1.01 \;\text{且}\; \bar{V}_{\text{recent}} < \bar{V}_{\text{prev}} \times 0.85 \quad\Rightarrow\quad -12$$

**底背离**——价格更低但成交量放大（吸筹信号）：
$$\min(C[-5:]) < \min(C[-10:-5]) \times 0.99 \;\text{且}\; \bar{V}_{\text{recent}} > \bar{V}_{\text{prev}} \times 1.15 \quad\Rightarrow\quad +12$$

### 9.7 成交量趋势

$$\text{vol\_ratio} = \frac{\bar{V}_{5}}{\bar{V}_{20}}$$

| vol_ratio | MA 趋势 | 调整量 | 信号说明 |
|-----------|----------|------------|------------|
| $> 1.3$ | 多头 | $+6$ | 放量上涨（资金流入确认） |
| $> 1.3$ | 其他 | $-4$ | 放量但趋势偏弱（谨慎） |
| $< 0.7$ | 任意 | $-3$ | 缩量（市场参与度低） |

### 9.8 Chaikin 资金流指标（CMF）

**资金流乘数**：

$$\text{MFM}_t = \frac{(C_t - L_t) - (H_t - C_t)}{H_t - L_t}$$

（当 $H_t = L_t$ 时 $\text{MFM}_t = 0$）

**资金流量**：

$$\text{MFV}_t = \text{MFM}_t \times V_t$$

$$\text{CMF}_t(20) = \frac{\sum_{i=0}^{19} \text{MFV}_{t-i}}{\sum_{i=0}^{19} V_{t-i}}$$

| CMF 值 | 分类 | 调整量 | 信号说明 |
|-----------|---------------|------------|------------|
| $> 0.15$ | 显著流入 | $+10$ | CMF 资金流指标显示显著流入 |
| $[0.05, 0.15]$ | 温和流入 | $+4$ | CMF 资金流指标显示温和流入 |
| $[-0.05, 0.05]$ | 中性 | $0$ | — |
| $[-0.15, -0.05)$ | 温和流出 | $-4$ | CMF 资金流指标显示温和流出 |
| $< -0.15$ | 显著流出 | $-10$ | CMF 资金流指标显示显著流出 |

---

## 10. 情绪面因子

基于实时 Ticker 行情数据计算。

### 10.1 24 小时涨跌幅

| $|\Delta_{24h}|\%$ | 调整量 | 信号说明 |
|---------------------|------------|------------|
| $> 5\%$ | $+15$ | 强势 |
| $> 2\%$ | $+8$ | 偏强 |
| $> 0\%$ | $+3$ | 微涨 |
| $> -2\%$ | $-3$ | 微跌 |
| $> -5\%$ | $-8$ | 偏弱 |
| $\leq -5\%$ | $-15$ | 弱势 |

### 10.2 订单簿不平衡

（当 bid + ask 成交量 > 0 时计算）

$$\text{imbalance} = \frac{\text{bid\_vol} - \text{ask\_vol}}{\text{bid\_vol} + \text{ask\_vol}} \in [-1, 1]$$

| 不平衡度 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $> 0.2$ | $+6$ | 买方挂单多于卖方（买盘偏强） |
| $< -0.2$ | $-6$ | 卖方挂单多于买方（卖盘偏强） |

---

## 11. 风险面因子

### 11.1 仓位集中度

$$\text{pos\_ratio} = \frac{\text{该股持仓市值}}{\text{总持仓市值}} \times 100\%$$

| 条件 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $\text{pos\_ratio} > \text{max\_ratio}$ | $-20$ | 持仓占比超过上限（风险偏高） |
| $\text{pos\_ratio} > 0.7 \times \text{max\_ratio}$ | $-8$ | 持仓占比接近上限 |

### 11.2 浮动盈亏

| 盈亏比区间 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $< -5\%$ | $-12$ | 浮动亏损较大（回撤较大） |
| $[-5\%, -2\%)$ | $-5$ | 轻微浮亏 |
| $[-2\%, 5\%]$ | $0$ | 盈亏适中 |
| $> 5\%$ | $+5$ | 浮动盈利（持仓盈利） |

### 11.3 风控事件

$$\text{risk\_adjustment} = -N_{\text{events}} \times 10$$

$N_{\text{events}}$ = 该标的最近 24 小时内触发的风控事件数量。

### 11.4 当日亏损

$$\text{loss\_ratio} = \frac{|\sum \text{day\_pnl}|}{\sum \text{market\_value}} \times 100\%$$

| 条件 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $\text{loss\_ratio} > \text{max\_daily\_loss}$ | $-25$ | 今日亏损超过上限（高风险） |
| $\text{loss\_ratio} > 0.5 \times \text{max\_daily\_loss}$ | $-10$ | 今日亏损接近上限 |

### 11.5 波动率（ATR）

$$\text{ATR}_{10} = \frac{1}{10} \sum_{i=0}^{9} \text{TR}_{t-i}$$

$$\text{ATR\_pct} = \frac{\text{ATR}_{10}}{P_{\text{current}}} \times 100\%$$

| ATR% | 调整量 | 信号说明 |
|------|------------|------------|
| $> 5\%$ | $-10$ | ATR 波动率处于高位（高波动风险） |
| $> 3\%$ | $-4$ | ATR 波动率处于中等水平 |
| $\leq 3\%$ | $+3$ | ATR 波动率适中 |

---

## 12. 动量面因子

### 12.1 5 日动量

$$M_{5d} = \left( \frac{\text{Close}_t}{\text{Close}_{t-4}} - 1 \right) \times 100\%$$

| $M_{5d}$ | 调整量 | 信号说明 |
|----------|------------|------------|
| $> 3\%$ | $+12$ | 5 日动量强势上涨 |
| $> 1\%$ | $+6$ | 5 日动量温和上涨 |
| $> -1\%$ | $-2$ | 5 日动量横盘 |
| $> -3\%$ | $-8$ | 5 日动量温和下跌 |
| $\leq -3\%$ | $-12$ | 5 日动量弱势下跌 |

### 12.2 多周期均线排列

| 条件 | 调整量 | 信号说明 |
|-----------|------------|------------|
| $\text{MA}_5 > \text{MA}_{10} > \text{MA}_{20}$ | $+8$ | 多周期均线多头排列（趋势确认） |
| $\text{MA}_5 < \text{MA}_{10} < \text{MA}_{20}$ | $-8$ | 多周期均线空头排列（下跌趋势） |
| 排列不一致 | $0$ | 多周期均线排列不一致（趋势不明） |

---

## 13. 基本面因子

### 13.1 市盈率（PE）

| PE 区间 | 调整量 | 信号说明 |
|----------|------------|------------|
| $< 15$ | $+12$ | 低估值区间 |
| $[15, 25)$ | $+6$ | 合理偏低 |
| $[25, 40)$ | $0$ | 估值合理 |
| $[40, 60)$ | $-6$ | 估值偏高 |
| $\geq 60$ | $-12$ | 高估值区间（风险） |

### 13.2 市净率（PB）

| PB 区间 | 调整量 | 信号说明 |
|----------|------------|------------|
| $< 1.5$ | $+5$ | 低于净资产（安全边际高） |
| $> 8$ | $-5$ | 市净率偏高 |

### 13.3 净资产收益率（ROE）

| ROE | 调整量 | 信号说明 |
|-----|------------|------------|
| $> 20\%$ | $+10$ | 高盈利能力 |
| $> 15\%$ | $+6$ | 良好盈利能力 |
| $> 8\%$ | $+2$ | 一般盈利能力 |
| $[3\%, 8\%]$ | $0$ | 盈利能力一般 |
| $< 3\%$ | $-4$ | 盈利能力弱 |

### 13.4 营收增长率

| 营收增长 | 调整量 | 信号说明 |
|----------------|------------|------------|
| $> 20\%$ | $+8$ | 高成长 |
| $> 10\%$ | $+5$ | 稳定增长 |
| $> 0\%$ | $+2$ | 小幅增长 |
| $[-10\%, 0\%]$ | $0$ | 营收持平 |
| $< -10\%$ | $-7$ | 营收下滑（警惕） |

### 13.5 净利润增长率

| 利润增长 | 调整量 | 信号说明 |
|---------------|------------|------------|
| $> 30\%$ | $+6$ | 高速增长 |
| $> 10\%$ | $+3$ | 稳健 |
| $[-20\%, 10\%]$ | $0$ | 利润持平 |
| $< -20\%$ | $-8$ | 利润大降（基本面恶化） |

---

## 14. ETF 跟踪误差

当标的为 A 股 ETF 时（代码以 `51`/`56`/`58`/`59` 开头），用跟踪误差替代基本面因子评分。

### 14.1 ETF 判定规则

A 股 ETF 代码范围：510xxx/588xxx（沪市），159xxx/512xxx/513xxx/515xxx/516xxx/517xxx/560xxx/561xxx/562xxx/563xxx（深市）。

### 14.2 跟踪误差公式

ETF 日收益率对基准指数日收益率的差异标准差，年化：

$$r_t^{\text{etf}} = \frac{C_t - C_{t-1}}{C_{t-1}},\quad r_t^{\text{idx}} = \frac{I_t - I_{t-1}}{I_{t-1}}$$

$$\text{TE} = \sigma(r_t^{\text{etf}} - r_t^{\text{idx}}) \times \sqrt{252} \times 100\%$$

| 跟踪误差 | 调整量 | 信号说明 |
|----------------|------------|------------|
| $< 0.5\%$ | $+10$ | 跟踪误差优秀，紧密跟踪指数 |
| $[0.5\%, 1.0\%)$ | $+5$ | 跟踪误差良好，较紧密跟踪 |
| $[1.0\%, 2.0\%)$ | $0$ | 跟踪误差一般 |
| $[2.0\%, 3.0\%)$ | $-5$ | 跟踪误差偏大，不够紧密 |
| $\geq 3.0\%$ | $-10$ | 跟踪误差严重偏离指数 |

---

## 15. 市场环境调整

计算个股相对 MA60 的偏离度，给出小幅调整（通常 $\pm 5$ 分以内）。这是市场相对排名的简化替代——完整实现应使用大盘指数（沪深 300 / 深证 50）进行 Beta 相对评分。

### 15.1 MA60 偏离度

$$\text{MA}_{60} = \frac{1}{60} \sum_{i=0}^{59} \text{Close}_{t-i}$$

$$\delta = \left( \frac{\text{Close}_t}{\text{MA}_{60}} - 1 \right) \times 100\%$$

### 15.2 调整规则

| 偏离度 $\delta$ | 调整量 | 含义 |
|---------------------|------------|----------------|
| $> 15\%$ | $-4.0$ | 价格高于 MA60 过度延伸（注意回调） |
| $(5\%, 15\%]$ | $+2.0$ | 价格高于 MA60（中期趋势向上） |
| $[-5\%, 5\%]$ | $0$ | 价格在 MA60 附近（中性） |
| $[-15\%, -5\%)$ | $-3.0$ | 价格低于 MA60（中期趋势偏弱） |
| $< -15\%$ | $+3.0$ | 价格低于 MA60 深度超跌（可能反弹） |

---

## 16. 目标价与止损

基于短期 ATR（平均真实波幅）计算。

$$\text{ATR}_{\text{short}} = \frac{1}{P} \sum_{i=0}^{P-1} |C_{t-i} - C_{t-i-1}|$$

其中 $P = 10$（atr_period_ts）。当 ATR 不可用时回退为 $\text{LastPrice} \times 0.02$。

### 16.1 目标价（仅当 composite ≥ 40）

$$P_{\text{target}} = \begin{cases}
\text{LastPrice} + 2.5 \times \text{ATR} & \text{若 composite} \geq 60 \\
\text{LastPrice} + 1.5 \times \text{ATR} & \text{若 composite} \geq 40 \\
\text{None} & \text{其他情况}
\end{cases}$$

### 16.2 止损价（仅当 composite ≥ 40）

$$P_{\text{stop}} = \text{LastPrice} - 1.5 \times \text{ATR}$$

---

## 17. 多周期确认

### 17.1 周线数据获取

优先查询 `1w` 间隔的 K 线数据。若无，从日线重采样：每 5 根日线合成 1 根周线（开/高/低/收/量）。

### 17.2 周线-日线一致性调整（旧版，保留在代码中）

旧版在 composite 上直接乘系数：

- 日线看多 + 周线看空 → $\times 0.80$
- 日线看空 + 周线看多 → $\times 0.85$
- 日周一致 → $\times 1.05$

当前版本将周线技术面评分作为子因子嵌入日线评分（日线 70% + 周线 30%），不再使用乘数方式。

---

## 18. 信号相关性折扣

防止同一信息源的多个相关信号因共线性而放大评分。

### 18.1 信号分组

| 组名 | 包含信号（关键词匹配） |
|-------|-----------------|
| oscillator（震荡类） | KDJ + RSI |
| trend（趋势类） | 均线交叉 + MACD 柱方向 + 多周期 MA 排列 |
| volume（量能类） | 量价背离 + CMF 资金流 + 量比信号 |

### 18.2 折扣规则

同组内若有 $\geq 2$ 个活跃信号：保留绝对值最大的信号保持全权重，其余同组信号打折至 50%。

$$\forall i \in \text{group} \setminus \{\text{argmax}_i |a_i|\}: \quad a_i \leftarrow a_i \times 0.5$$

---

## 19. 配置系数速查

所有系数可通过 `backend/config/decision_coefficients.xlsx` 调整，运行时首次访问时从 Excel 加载，如文件不存在则使用代码内置默认值。加载器实现见 `app/services/decision_config.py`。

| 分类 | Sheet 名 | 主要参数 |
|----------|-----------|----------------|
| 因子权重 | `weights` | 默认权重、动态基础/增/减值、钳位边界 |
| 状态检测 | `regime_detection` | ADX/BB sigmoid 中心与陡峭度、趋势偏差放大系数、转换乘数 |
| 综合评分 | `scoring` | tanh 除数、分歧惩罚系数/上限、一致性因子除数、推荐阈值、自适应扩展系数 |
| 技术面 | `technical` | 各指标调整量、周期参数、阈值比例 |
| 情绪面 | `sentiment` | 涨跌幅阈值、订单不平衡阈值 |
| 风险面 | `risk` | 仓位/盈亏/亏损/ATR 阈值与惩罚量、风险基线 |
| 动量面 | `momentum` | 5 日动量分档阈值与调整量、均线排列 |
| 基本面 | `fundamental` | PE/PB/ROE/增长率各估值区间的调整量 |
| 市场环境 | `market_context` | MA60 周期、偏离度分档阈值与调整量 |
| 目标止损 | `target_stop` | ATR 周期、各评分档的 ATR 倍数、回退比例 |
| 数据获取 | `data` | K 线数量限制、新鲜度阈值、Mock 参数、决策有效期 |
