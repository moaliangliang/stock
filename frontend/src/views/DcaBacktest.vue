<template>
  <div>
    <h3 style="margin-bottom: 20px">DCA 定投回测 — 纳斯达克 / 标普500</h3>

    <el-row :gutter="16">
      <!-- 左侧：参数配置 -->
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header><span>参数配置</span></template>
          <el-form :model="form" label-width="100px" size="small">
            <el-form-item label="定投标的">
              <el-select v-model="form.symbols" multiple filterable style="width: 100%">
                <el-option
                  v-for="idx in indices"
                  :key="idx.symbol"
                  :label="`${idx.name} (${idx.symbol})`"
                  :value="idx.symbol"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="定投模式">
              <el-radio-group v-model="form.mode">
                <el-radio value="fixed">定额</el-radio>
                <el-radio value="smart">智能 (均线估值)</el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item label="开始日期">
              <el-date-picker
                v-model="form.start_date"
                type="date"
                style="width: 100%"
                value-format="YYYY-MM-DD"
              />
            </el-form-item>

            <el-form-item label="结束日期">
              <el-date-picker
                v-model="form.end_date"
                type="date"
                style="width: 100%"
                value-format="YYYY-MM-DD"
              />
            </el-form-item>

            <el-form-item label="月投入金额">
              <el-input-number v-model="form.amount" :min="100" :step="500" style="width: 100%" />
            </el-form-item>

            <!-- 智能模式参数 -->
            <template v-if="form.mode === 'smart'">
              <el-divider>智能模式参数</el-divider>

              <el-form-item label="敏感度">
                <el-slider
                  v-model="form.smart_aggressiveness"
                  :min="0.5"
                  :max="5"
                  :step="0.5"
                  show-input
                />
                <div style="font-size: 11px; color: #999; line-height: 1.4">
                  值越大，对价格偏离越敏感，投入金额波动越大
                </div>
              </el-form-item>

              <el-form-item label="投入倍数范围">
                <div style="width: 100%">
                  <el-row :gutter="8">
                    <el-col :span="12">
                      <span style="font-size: 12px; color: #999">最低</span>
                      <el-input-number
                        v-model="form.smart_min_multiplier"
                        :min="0.1"
                        :max="1.0"
                        :step="0.1"
                        size="small"
                      />
                    </el-col>
                    <el-col :span="12">
                      <span style="font-size: 12px; color: #999">最高</span>
                      <el-input-number
                        v-model="form.smart_max_multiplier"
                        :min="1.0"
                        :max="5.0"
                        :step="0.5"
                        size="small"
                      />
                    </el-col>
                  </el-row>
                </div>
              </el-form-item>

              <div style="font-size: 11px; color: #409eff; margin-top: -8px; line-height: 1.5">
                价格 &lt; 12月均线 → 低估，加大投入 (最多 {{ form.smart_max_multiplier }}x)
                <br />
                价格 &gt; 12月均线 → 高估，减少投入 (最少 {{ form.smart_min_multiplier }}x)
              </div>
            </template>

            <el-form-item style="margin-top: 16px">
              <el-button type="primary" @click="runBacktest" :loading="running" style="width: 100%">
                {{ running ? '回测中...' : '运行回测' }}
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <!-- 右侧：结果 -->
      <el-col :span="16">
        <!-- 暂无结果 -->
        <el-card v-if="!data && !errorMsg" shadow="hover">
          <el-empty description="配置参数后点击「运行回测」" />
        </el-card>

        <!-- 错误 -->
        <el-alert v-if="errorMsg" :title="errorMsg" type="error" show-icon closable @close="errorMsg = ''" />

        <!-- 结果 -->
        <template v-if="data">
          <!-- 组合汇总 -->
          <el-card shadow="hover" style="margin-bottom: 16px">
            <template #header>
              <span>组合汇总 ({{ form.mode === 'smart' ? '智能' : '定额' }}定投)</span>
            </template>
            <el-row :gutter="12">
              <el-col :span="4" v-for="m in summaryMetrics" :key="m.label">
                <div class="metric-card">
                  <div class="metric-value" :style="{ color: m.color }">{{ m.value }}</div>
                  <div class="metric-label">{{ m.label }}</div>
                </div>
              </el-col>
            </el-row>
          </el-card>

          <!-- 各标的分项 -->
          <el-card shadow="hover" style="margin-bottom: 16px">
            <template #header><span>标的明细</span></template>
            <el-table :data="detailRows" size="small" stripe>
              <el-table-column prop="name" label="标的" width="120" />
              <el-table-column prop="total_invested" label="总投入" width="110">
                <template #default="{ row }">¥{{ row.total_invested?.toLocaleString() }}</template>
              </el-table-column>
              <el-table-column prop="final_value" label="当前市值" width="110">
                <template #default="{ row }">¥{{ row.final_value?.toLocaleString() }}</template>
              </el-table-column>
              <el-table-column prop="total_return_pct" label="总收益率" width="90">
                <template #default="{ row }">
                  <span :class="(row.total_return_pct || 0) >= 0 ? 'price-up' : 'price-down'">
                    {{ row.total_return_pct?.toFixed(2) }}%
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="annualized_xirr_pct" label="年化IRR" width="90">
                <template #default="{ row }">
                  {{ row.annualized_xirr_pct != null ? row.annualized_xirr_pct.toFixed(2) + '%' : 'N/A' }}
                </template>
              </el-table-column>
              <el-table-column prop="max_drawdown_pct" label="最大回撤" width="90">
                <template #default="{ row }">{{ row.max_drawdown_pct?.toFixed(2) }}%</template>
              </el-table-column>
              <el-table-column prop="investment_count" label="定投期数" width="80" />
              <el-table-column prop="avg_invest_amount" label="均期投入" width="100">
                <template #default="{ row }">¥{{ Math.round(row.avg_invest_amount)?.toLocaleString() }}</template>
              </el-table-column>
              <el-table-column prop="cost_basis" label="成本价" width="80">
                <template #default="{ row }">{{ row.cost_basis?.toFixed(4) }}</template>
              </el-table-column>
              <el-table-column prop="last_price" label="现价" width="80">
                <template #default="{ row }">{{ row.last_price }}</template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- 定投 vs 一次性投入 -->
          <el-card shadow="hover" style="margin-bottom: 16px" v-if="Object.keys(data.lumpsum || {}).length">
            <template #header><span>定投 vs 一次性投入</span></template>
            <el-table :data="lumpsumVsRows" size="small" stripe>
              <el-table-column prop="label" label="对比项" width="120" />
              <el-table-column prop="dca_name" label="方式" width="80">
                <template #default="{ row }">
                  <el-tag size="small" :type="row._type === 'dca' ? 'primary' : 'warning'">
                    {{ row._type === 'dca' ? '定投' : '一次性' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="total_invested" label="总投入" width="120">
                <template #default="{ row }">¥{{ row.total_invested?.toLocaleString() }}</template>
              </el-table-column>
              <el-table-column prop="final_value" label="当前市值" width="120">
                <template #default="{ row }">¥{{ row.final_value?.toLocaleString() }}</template>
              </el-table-column>
              <el-table-column prop="total_return_pct" label="总收益率" width="100">
                <template #default="{ row }">
                  <span :class="(row.total_return_pct || 0) >= 0 ? 'price-up' : 'price-down'">
                    {{ row.total_return_pct?.toFixed(2) }}%
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="annual_return_pct" label="年化收益" width="90">
                <template #default="{ row }">
                  {{ row.annual_return_pct != null ? row.annual_return_pct.toFixed(2) + '%' : row.annualized_xirr_pct != null ? row.annualized_xirr_pct.toFixed(2) + '%' : '-' }}
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- 月度市值走势 -->
          <el-card shadow="hover" v-if="monthlySeries.length > 0">
            <template #header><span>月度市值走势</span></template>
            <div ref="chartRef" style="height: 350px"></div>
          </el-card>
        </template>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { dcaApi } from '@/api'

const running = ref(false)
const errorMsg = ref('')
const data = ref<any>(null)
const indices = ref<any[]>([])
const chartRef = ref<HTMLElement>()

const form = reactive({
  symbols: ['513100.SH', '513500.SH'],
  start_date: '2021-01-01',
  end_date: '2026-05-11',
  amount: 1000,
  mode: 'fixed' as 'fixed' | 'smart',
  smart_aggressiveness: 2.0,
  smart_min_multiplier: 0.5,
  smart_max_multiplier: 2.0,
})

const summaryMetrics = computed(() => {
  const c = data.value?.comparison
  if (!c) return []
  return [
    { label: '总投入', value: `¥${(c.total_invested || 0).toLocaleString()}`, color: '#666' },
    { label: '当前市值', value: `¥${(c.total_value || 0).toLocaleString()}`, color: '#1890ff' },
    {
      label: '总收益率',
      value: `${(c.total_return_pct || 0) >= 0 ? '+' : ''}${c.total_return_pct?.toFixed(2)}%`,
      color: (c.total_return_pct || 0) >= 0 ? '#52c41a' : '#f56c6c',
    },
    {
      label: '年化IRR',
      value: c.annualized_xirr_pct != null ? `${c.annualized_xirr_pct >= 0 ? '+' : ''}${c.annualized_xirr_pct?.toFixed(2)}%` : 'N/A',
      color: (c.annualized_xirr_pct || 0) >= 0 ? '#52c41a' : '#f56c6c',
    },
    { label: '最大回撤', value: `${c.max_drawdown_pct?.toFixed(2)}%`, color: '#faad14' },
  ]
})

const detailRows = computed(() => {
  if (!data.value?.results) return []
  return Object.values(data.value.results)
})

const lumpsumVsRows = computed(() => {
  const results = data.value?.results || {}
  const lumpsum = data.value?.lumpsum || {}
  const rows: any[] = []
  for (const sym of Object.keys(results)) {
    const dca = results[sym]
    rows.push({ ...dca, label: dca.name, _type: 'dca' })
    if (lumpsum[sym]) {
      rows.push({ ...lumpsum[sym], label: dca.name, _type: 'lumpsum' })
    }
  }
  return rows
})

const monthlySeries = computed(() => data.value?.monthly_series || [])

async function runBacktest() {
  if (!form.symbols.length) {
    ElMessage.warning('请选择至少一个定投标的')
    return
  }
  if (!form.start_date || !form.end_date) {
    ElMessage.warning('请选择日期范围')
    return
  }

  running.value = true
  errorMsg.value = ''
  data.value = null

  try {
    const res: any = await dcaApi.backtest({
      symbols: form.symbols,
      start_date: form.start_date,
      end_date: form.end_date,
      amount: form.amount,
      mode: form.mode,
      smart_aggressiveness: form.smart_aggressiveness,
      smart_min_multiplier: form.smart_min_multiplier,
      smart_max_multiplier: form.smart_max_multiplier,
    })

    if (res.code === 200) {
      data.value = res.data
      ElMessage.success('回测完成')
      await nextTick()
      renderChart()
    } else {
      errorMsg.value = res.message || '回测失败'
    }
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.message || err?.message || '请求失败'
  } finally {
    running.value = false
  }
}

async function renderChart() {
  if (!chartRef.value || !data.value?.monthly_series) return
  const echarts = (await import('echarts')).default
  const chart = echarts.init(chartRef.value)

  const series = data.value.monthly_series
  const labels = series.map((s: any) => s.label)
  const symbols = form.symbols.filter((s) => data.value?.results?.[s])

  const chartSeries: any[] = symbols.map((sym) => ({
    name: data.value?.results?.[sym]?.name || sym,
    type: 'line',
    data: series.map((s: any) => s[sym]),
    smooth: true,
    symbol: 'none',
  }))

  // 添加组合合计线
  chartSeries.push({
    name: '组合合计',
    type: 'line',
    data: series.map((s: any) => s.portfolio),
    smooth: true,
    symbol: 'none',
    lineStyle: { width: 3, color: '#ff4d4f' },
    areaStyle: { opacity: 0.15, color: '#ff4d4f' },
  })

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      valueFormatter: (val: number) => val != null ? `¥${val.toLocaleString()}` : '-',
    },
    legend: { data: [...symbols.map((s) => data.value?.results?.[s]?.name || s), '组合合计'] },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: labels, boundaryGap: false },
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `¥${(v / 1000).toFixed(0)}k` } },
    series: chartSeries,
  })
}

onMounted(async () => {
  try {
    const res: any = await dcaApi.getIndices()
    indices.value = res.data || []
  } catch (err) { console.error('Operation failed:', err); }
})
</script>

<style scoped>
.metric-card {
  text-align: center;
  padding: 10px 6px;
  background: #fafafa;
  border-radius: 8px;
}
.metric-value {
  font-size: 18px;
  font-weight: bold;
  margin-bottom: 4px;
}
.metric-label {
  font-size: 12px;
  color: #999;
}
.price-up {
  color: #52c41a;
  font-weight: 500;
}
.price-down {
  color: #f56c6c;
  font-weight: 500;
}
</style>
