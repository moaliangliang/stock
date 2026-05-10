<template>
  <div>
    <h3 style="margin-bottom: 20px">📉 回测系统</h3>

    <el-row :gutter="16">
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header><span>⚙️ 回测参数</span></template>
          <el-form :model="form" label-width="100px" size="small">
            <el-form-item label="策略">
              <el-select v-model="form.strategy_id" filterable style="width: 100%">
                <el-option v-for="s in strategies" :key="s.id" :label="s.name" :value="s.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="标的">
              <el-select v-model="form.symbol" filterable style="width: 100%">
                <el-option v-for="s in symbols" :key="s.symbol" :label="`${s.name} ${s.symbol}`" :value="s.symbol">
                  <div>{{ s.name }}</div>
                  <div style="font-size:11px;color:#999">({{ s.symbol }})</div>
                </el-option>
              </el-select>
            </el-form-item>
            <el-form-item label="周期">
              <el-select v-model="form.interval" style="width: 100%">
                <el-option label="1分钟" value="1m" />
                <el-option label="5分钟" value="5m" />
                <el-option label="15分钟" value="15m" />
                <el-option label="30分钟" value="30m" />
                <el-option label="60分钟" value="60m" />
                <el-option label="日线" value="1d" />
              </el-select>
            </el-form-item>
            <el-form-item label="开始日期">
              <el-date-picker v-model="form.start_date" type="date" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
            <el-form-item label="结束日期">
              <el-date-picker v-model="form.end_date" type="date" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
            <el-form-item label="初始资金">
              <el-input-number v-model="form.initial_capital" :min="1000" :step="10000" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="runBacktest" :loading="running" style="width: 100%">
                {{ running ? '回测中...' : '🚀 运行回测' }}
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="16">
        <el-card shadow="hover" v-if="result">
          <template #header><span>📊 回测结果</span></template>
          <el-row :gutter="16" style="margin-bottom: 20px">
            <el-col :span="6" v-for="m in metrics" :key="m.label">
              <div class="metric-card">
                <div class="metric-value" :style="{ color: m.color }">{{ m.value }}</div>
                <div class="metric-label">{{ m.label }}</div>
              </div>
            </el-col>
          </el-row>
          <div ref="equityChartRef" style="height: 300px"></div>
        </el-card>

        <el-card shadow="hover" style="margin-top: 16px" v-if="result?.trades?.length">
          <template #header><span>📋 交易明细</span></template>
          <el-table :data="result.trades" size="small" max-height="300" stripe>
            <el-table-column prop="time" label="时间" width="160" />
            <el-table-column prop="side" label="方向" width="80">
              <template #default="{ row }">
                <el-tag :type="row.side === 'buy' ? 'danger' : 'success'" size="small">
                  {{ row.side === 'buy' ? '买入' : '卖出' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="price" label="价格" width="100" />
            <el-table-column prop="quantity" label="数量" width="80" />
            <el-table-column prop="pnl" label="盈亏" width="100">
              <template #default="{ row }">
                <span :class="(row.pnl || 0) >= 0 ? 'price-up' : 'price-down'">
                  {{ row.pnl?.toFixed(2) }}
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
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { backtestApi, strategyApi, marketApi } from '@/api'

const running = ref(false)
const strategies = ref<any[]>([])
const symbols = ref<any[]>([])
const result = ref<any>(null)
const equityChartRef = ref<HTMLElement>()

const form = reactive({
  strategy_id: undefined as number | undefined,
  symbol: 'BTC/USDT',
  interval: '1d',
  start_date: '',
  end_date: '',
  initial_capital: 100000,
})

const metrics = computed(() => {
  if (!result.value) return []
  const r = result.value
  return [
    { label: '总收益率', value: `${(r.total_return * 100).toFixed(2)}%`, color: (r.total_return || 0) >= 0 ? '#52c41a' : '#f56c6c' },
    { label: '年化收益', value: `${(r.annual_return * 100).toFixed(2)}%`, color: '#1890ff' },
    { label: '最大回撤', value: `${(r.max_drawdown * 100).toFixed(2)}%`, color: '#faad14' },
    { label: '夏普比率', value: r.sharpe_ratio?.toFixed(2), color: '#722ed1' },
    { label: '胜率', value: `${(r.win_rate * 100).toFixed(1)}%`, color: '#52c41a' },
    { label: '交易次数', value: r.total_trades, color: '#1890ff' },
    { label: '盈亏比', value: r.profit_factor?.toFixed(2), color: '#faad14' },
  ]
})

async function runBacktest() {
  if (!form.strategy_id) { ElMessage.warning('请选择策略'); return }
  if (!form.start_date || !form.end_date) { ElMessage.warning('请选择回测日期'); return }

  running.value = true
  try {
    const res: any = await backtestApi.run(form as any)
    result.value = res.data
    ElMessage.success('回测完成')
    await nextTick()
    renderEquityChart()
  } catch (err) {
    console.error(err)
  } finally {
    running.value = false
  }
}

async function renderEquityChart() {
  if (!equityChartRef.value || !result.value?.equity_curve) return
  const echarts = (await import('echarts')).default
  const chart = echarts.init(equityChartRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: result.value.equity_curve.map((p: any) => p[0]?.toString().slice(5, 16) || ''), boundaryGap: false },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}' } },
    series: [{ type: 'line', data: result.value.equity_curve.map((p: any) => p[1]), smooth: true, areaStyle: { opacity: 0.3 }, itemStyle: { color: '#1890ff' } }],
  })
}

onMounted(async () => {
  try {
    const [sRes, symRes] = await Promise.all([strategyApi.getStrategies(), marketApi.getSymbols()])
    strategies.value = (sRes as any).data || []
    symbols.value = (symRes as any).data || []
  } catch {}
})
</script>

<style scoped>
.metric-card {
  text-align: center;
  padding: 12px;
  background: #fafafa;
  border-radius: 8px;
}
.metric-value {
  font-size: 22px;
  font-weight: bold;
  margin-bottom: 4px;
}
.metric-label {
  font-size: 13px;
  color: #999;
}
</style>
