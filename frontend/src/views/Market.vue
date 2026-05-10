<template>
  <div>
    <h3 style="margin-bottom: 20px">行情中心</h3>

    <!-- 查询条件 -->
    <el-card shadow="hover" style="margin-bottom: 16px">
      <el-row :gutter="16" align="middle">
        <el-col :span="5">
          <el-select v-model="symbol" filterable placeholder="选择标的" style="width: 100%">
            <el-option v-for="s in symbols" :key="s.symbol" :label="`${s.name} ${s.symbol}`" :value="s.symbol">
              <div>{{ s.name }}</div>
              <div style="font-size:11px;color:#999">({{ s.symbol }})</div>
            </el-option>
          </el-select>
        </el-col>
        <el-col :span="3">
          <el-select v-model="interval" style="width: 100%">
            <el-option label="1分钟" value="1m" />
            <el-option label="5分钟" value="5m" />
            <el-option label="15分钟" value="15m" />
            <el-option label="30分钟" value="30m" />
            <el-option label="60分钟" value="60m" />
            <el-option label="日线" value="1d" />
          </el-select>
        </el-col>
        <el-col :span="3">
          <el-button type="primary" @click="loadData" :loading="klineLoading">查询K线</el-button>
        </el-col>
        <el-col :span="13">
          <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap">
            <span style="font-size: 13px; color: #999; margin-right: 4px">指标:</span>
            <el-button v-for="ind in indicatorList" :key="ind.key" size="small"
              :type="ind.active ? 'primary' : 'default'"
              :plain="!ind.active"
              @click="toggleIndicator(ind.key)">
              {{ ind.label }}
            </el-button>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-row :gutter="16">
      <el-col :span="fullscreen ? 24 : 16">
        <el-card shadow="hover" :class="{ 'chart-fullscreen': fullscreen }">
          <template #header>
            <div style="display: flex; align-items: center; justify-content: space-between">
              <span>{{ symbol }} K线图</span>
              <el-button size="small" circle @click="toggleFullscreen" :title="fullscreen ? '退出全屏' : '全屏'">
                <el-icon><FullScreen v-if="!fullscreen" /><Close v-else /></el-icon>
              </el-button>
            </div>
          </template>
          <div class="chart-wrapper">
            <div v-if="klineError" class="chart-placeholder">{{ klineError }}</div>
            <div v-else-if="klineEmpty" class="chart-placeholder">暂无K线数据，请选择其他标的或周期</div>
            <div ref="klineChartRef" class="kline-chart" :style="{ height: fullscreen ? 'calc(100vh - 180px)' : '520px' }"></div>
            <div v-if="klineLoading" class="kline-loading-mask">
              <i class="el-icon-loading" style="font-size: 24px"></i>
              <p style="margin-top: 8px">加载K线数据中...</p>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="fullscreen ? 0 : 8" style="transition: all 0.3s">
        <el-card shadow="hover">
          <template #header>
            <div style="display: flex; align-items: center; justify-content: space-between">
              <span>实时行情</span>
              <div style="display: flex; align-items: center; gap: 6px">
                <el-tooltip :content="wsConnected ? '实时连接' : '轮询模式'" placement="bottom">
                  <span :style="{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', background: wsConnected ? '#52c41a' : '#faad14' }"></span>
                </el-tooltip>
                <el-tag size="small" type="info" v-if="tickerTime">{{ tickerTime }}</el-tag>
                <el-button size="small" @click="loadTickers" :loading="tickerLoading" circle>↻</el-button>
              </div>
            </div>
          </template>
          <el-table :data="tickerList" size="small" max-height="520" stripe v-loading="tickerLoading">
            <el-table-column prop="symbol" label="代码" width="90" />
            <el-table-column prop="name" label="名称" width="110">
              <template #default="{ row }">
                <span style="font-size: 12px">{{ row.name || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="last_price" label="最新价" width="100">
              <template #default="{ row }">
                <span :class="priceClass(row)" style="font-weight: 600">{{ row.last_price?.toFixed(2) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="change_24h" label="涨跌幅" width="85">
              <template #default="{ row }">
                <el-tag :type="(row.change_24h || 0) >= 0 ? 'danger' : 'success'" size="small" effect="dark">
                  {{ row.change_24h?.toFixed(2) }}%
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="涨跌" width="80">
              <template #default="{ row }">
                <span :class="priceClass(row)" style="font-size: 13px">
                  {{ row.change_24h ? (row.change_24h >= 0 ? '+' : '') + (row.last_price * row.change_24h / 100).toFixed(2) : '-' }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, reactive } from 'vue'
import { marketApi } from '@/api'
import type { SymbolInfo, Ticker } from '@/types/api'
import { useECharts } from '@/composables/useECharts'
import { useWebSocket } from '@/composables/useWebSocket'
import { calcMA, calcMACD, calcBOLL } from '@/utils/indicators'

const symbol = ref('BTC/USDT')
const interval = ref('1d')
const klineChartRef = ref<HTMLElement>()
const symbols = ref<SymbolInfo[]>([])
const tickerList = ref<Ticker[]>([])
const tickerTime = ref('')
const tickerLoading = ref(false)
const klineLoading = ref(false)
const klineError = ref('')
const klineEmpty = ref(false)
const fullscreen = ref(false)
const wsConnected = ref(false)
let tickerTimer: ReturnType<typeof setInterval> | null = null

const { initChart, setOption, getChart } = useECharts(klineChartRef)

const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/v1/ws/tickers`

useWebSocket({
  url: wsUrl,
  onMessage: (data) => {
    if (data.type === 'ticker' && Array.isArray(data.data)) {
      // 合并 WebSocket 数据到现有 ticker 列表（保留 name 等字段）
      const wsMap = new Map(data.data.map((t: any) => [t.symbol, t]))
      tickerList.value = tickerList.value.map(t => {
        const update = wsMap.get(t.symbol)
        return update ? { ...t, ...update } : t
      })
      tickerTime.value = new Date().toLocaleTimeString()
    }
  },
  onStatusChange: (connected) => {
    wsConnected.value = connected
  },
  maxRetries: 5,
})

// 指标开关
const indicatorList = reactive([
  { key: 'ma5', label: 'MA5', active: false },
  { key: 'ma20', label: 'MA20', active: false },
  { key: 'ma60', label: 'MA60', active: false },
  { key: 'macd', label: 'MACD', active: false },
  { key: 'boll', label: '布林带', active: false },
  { key: 'volume', label: '成交量', active: false },
])

function getActiveIndicators() {
  return indicatorList.filter(i => i.active).map(i => i.key)
}

function priceClass(row: Ticker) {
  return (row.change_24h || 0) >= 0 ? 'price-up' : 'price-down'
}

function toggleFullscreen() {
  fullscreen.value = !fullscreen.value
  nextTick(() => getChart()?.resize())
}

async function loadSymbols() {
  try {
    const res = await marketApi.getSymbols()
    symbols.value = res.data || []
  } catch (err) {
    console.error('Failed to load symbols', err)
  }
}

async function loadTickers() {
  tickerLoading.value = true
  try {
    const res = await marketApi.getTickers()
    tickerList.value = res.data || []
    tickerTime.value = new Date().toLocaleTimeString()
  } catch (err) {
    console.error('Failed to load tickers', err)
  } finally {
    tickerLoading.value = false
  }
}

function toggleIndicator(key: string) {
  const ind = indicatorList.find(i => i.key === key)
  if (ind) ind.active = !ind.active
  renderChart()
}

let lastKlineData: any[] = []

async function loadKlineChart() {
  if (!klineChartRef.value) return

  klineLoading.value = true
  klineError.value = ''
  klineEmpty.value = false

  try {
    const res = await marketApi.getKlines({ symbol: symbol.value, interval: interval.value, limit: 200 })
    const data = res.data || []

    if (!data.length) {
      klineEmpty.value = true
      return
    }

    lastKlineData = data
    await renderChart()
  } catch (err: unknown) {
    klineError.value = err instanceof Error ? err.message : 'K线数据加载失败'
  } finally {
    klineLoading.value = false
  }
}

async function renderChart() {
  const data = lastKlineData
  if (!data.length) return

  const chart = getChart()
  if (!chart) {
    // 首次初始化
    const c = await initChart()
    if (!c) return
  }

  const categories = data.map(d => d.timestamp?.slice(5, 16) || '')
  const closes = data.map((d: any) => d.close)
  const volumes = data.map((d: any) => d.volume)
  const opens = data.map((d: any) => d.open)

  // 基础网格：价格图
  const grid: any[] = [{ left: '8%', right: '8%', top: '8%', bottom: '18%' }]
  const series: any[] = []

  // 蜡烛图
  series.push({
    name: 'K线',
    type: 'candlestick',
    data: data.map(d => [d.open, d.close, d.low, d.high]),
    itemStyle: { color: '#f56c6c', color0: '#67c23a', borderColor: '#f56c6c', borderColor0: '#67c23a' },
    xAxisIndex: 0,
    yAxisIndex: 0,
  })

  // 计算各指标数据
  const active = getActiveIndicators()
  const ma5 = calcMA(closes, 5)
  const ma20 = calcMA(closes, 20)
  const ma60 = calcMA(closes, 60)
  const boll = calcBOLL(closes, 20, 2)
  const macd = calcMACD(closes, 12, 26, 9)

  // 成交量（独立 grid）
  if (active.includes('volume')) {
    grid.push({ left: '8%', right: '8%', top: '72%', bottom: '18%', height: '10%' })
    series.push({
      name: '成交量',
      type: 'bar',
      data: volumes.map((v: number, i: number) => ({
        value: v,
        itemStyle: { color: opens[i] <= closes[i] ? '#f56c6c' : '#67c23a' },
      })),
      xAxisIndex: 1,
      yAxisIndex: 1,
    })
  }

  // MACD（独立 grid）
  if (active.includes('macd')) {
    const macdGridIdx = grid.length
    // 如果已有 volume grid，放在 volume 下方；否则在底部
    const volGridCount = active.includes('volume') ? 1 : 0
    const macdTop = 72 + volGridCount * 12
    grid.push({ left: '8%', right: '8%', top: `${macdTop}%`, bottom: '4%', height: '12%' })

    series.push({
      name: 'MACD',
      type: 'bar',
      data: macd.histogram.map((v: number) => ({
        value: Math.abs(v),
        itemStyle: { color: v >= 0 ? '#f56c6c' : '#67c23a' },
      })),
      xAxisIndex: 1 + volGridCount,
      yAxisIndex: 1 + volGridCount,
    })
    series.push({
      name: 'DIF',
      type: 'line',
      data: macd.dif,
      symbol: 'none',
      lineStyle: { width: 1, color: '#1890ff' },
      xAxisIndex: 1 + volGridCount,
      yAxisIndex: 1 + volGridCount,
    })
    series.push({
      name: 'DEA',
      type: 'line',
      data: macd.dea,
      symbol: 'none',
      lineStyle: { width: 1, color: '#f5222d' },
      xAxisIndex: 1 + volGridCount,
      yAxisIndex: 1 + volGridCount,
    })
  }

  // MA 线（叠加在价格图上）
  if (active.includes('ma5')) {
    series.push({ name: 'MA5', type: 'line', data: ma5, symbol: 'none', smooth: true, lineStyle: { width: 1, color: '#f56c6c' }, xAxisIndex: 0, yAxisIndex: 0 })
  }
  if (active.includes('ma20')) {
    series.push({ name: 'MA20', type: 'line', data: ma20, symbol: 'none', smooth: true, lineStyle: { width: 1, color: '#1890ff' }, xAxisIndex: 0, yAxisIndex: 0 })
  }
  if (active.includes('ma60')) {
    series.push({ name: 'MA60', type: 'line', data: ma60, symbol: 'none', smooth: true, lineStyle: { width: 1, color: '#722ed1' }, xAxisIndex: 0, yAxisIndex: 0 })
  }

  // 布林带
  if (active.includes('boll')) {
    series.push({
      name: '布林上轨', type: 'line', data: boll.upper, symbol: 'none',
      lineStyle: { width: 1, color: '#faad14', type: 'dashed' }, xAxisIndex: 0, yAxisIndex: 0,
    })
    series.push({
      name: '布林中轨', type: 'line', data: boll.mid, symbol: 'none',
      lineStyle: { width: 1, color: '#faad14' }, xAxisIndex: 0, yAxisIndex: 0,
    })
    series.push({
      name: '布林下轨', type: 'line', data: boll.lower, symbol: 'none',
      lineStyle: { width: 1, color: '#faad14', type: 'dashed' }, xAxisIndex: 0, yAxisIndex: 0,
    })
  }

  // 构建 xAxis/yAxis
  const xAxes: any[] = []
  const yAxes: any[] = []
  for (let i = 0; i < grid.length; i++) {
    xAxes.push({ type: 'category', data: categories, gridIndex: i, axisLabel: i > 0 ? { show: false } : undefined })
    yAxes.push({ type: 'value', scale: true, gridIndex: i, splitLine: i > 0 ? { show: false } : undefined })
  }

  setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params: any[]) => {
        if (!params?.length) return ''
        const p = params[0]
        const idx = p.dataIndex
        const d = data[idx]
        let html = `<div style="font-size:12px">${d.timestamp}</div><hr style="margin:4px 0">`
        html += `开盘: ${d.open.toFixed(2)}<br>`
        html += `收盘: <b>${d.close.toFixed(2)}</b><br>`
        html += `最高: ${d.high.toFixed(2)}<br>`
        html += `最低: ${d.low.toFixed(2)}<br>`
        html += `成交量: ${d.volume?.toFixed(2) || '-'}<br>`
        if (active.includes('ma5') && ma5[idx] != null) html += `MA5: ${ma5[idx]!.toFixed(2)}<br>`
        if (active.includes('ma20') && ma20[idx] != null) html += `MA20: ${ma20[idx]!.toFixed(2)}<br>`
        if (active.includes('ma60') && ma60[idx] != null) html += `MA60: ${ma60[idx]!.toFixed(2)}<br>`
        return html
      },
    },
    grid,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: xAxes.map((_, i) => i) },
      { type: 'slider', xAxisIndex: 0, start: 50, end: 100 },
    ],
    series,
  })
}

async function loadData() {
  await loadKlineChart()
}

onMounted(async () => {
  await loadSymbols()
  await loadTickers()
  await nextTick()
  await loadKlineChart()
  tickerTimer = setInterval(loadTickers, 10000)
})

onUnmounted(() => {
  if (tickerTimer) {
    clearInterval(tickerTimer)
    tickerTimer = null
  }
})
</script>

<style scoped>
.chart-wrapper {
  position: relative;
  min-height: 450px;
}
.kline-chart {
  width: 100%;
}
.chart-placeholder {
  text-align: center;
  padding: 200px 0;
  color: #999;
}
.kline-loading-mask {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(255, 255, 255, 0.85);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 10;
  border-radius: 4px;
  pointer-events: none;
}
.chart-fullscreen {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1000;
  border-radius: 0;
}
.chart-fullscreen :deep(.el-card__body) {
  height: calc(100vh - 55px);
  overflow: hidden;
}
</style>