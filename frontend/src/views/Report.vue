<template>
  <div class="report-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>分析报告</span>
          <el-input
            v-model="keyword"
            placeholder="搜索文件名..."
            clearable
            style="width: 280px"
            @input="onSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
      </template>

      <!-- 买入信号日志 -->
      <el-card v-if="alertLog.content && alertLog.content !== '暂无买入信号日志'" style="margin-bottom: 16px">
        <template #header>
          <div class="alert-header">
            <span>买入信号日志 (更新于 {{ alertLog.updated }})</span>
            <el-tag v-if="currentPrices?.updated" type="warning" size="small" style="margin-left: 12px">
              最新价格: {{ currentPrices.updated }}
            </el-tag>
          </div>
        </template>

        <!-- 当前价格速览 -->
        <div v-if="currentPrices?.stocks" class="current-prices-bar">
          <span
            v-for="(stock, code) in currentPrices.stocks"
            :key="code"
            class="price-tag"
          >
            {{ stock.name }}
            <b :class="priceChangeClass(code)">{{ stock.close?.toFixed(2) }}</b>
          </span>
        </div>

        <pre class="alert-log-pre">{{ alertLog.content.slice(-3000) }}</pre>
      </el-card>

      <!-- 无日志但有价格数据时，仅显示价格速览 -->
      <el-card v-else-if="currentPrices?.stocks" style="margin-bottom: 16px">
        <template #header>
          <span>股票价格速览 ({{ currentPrices.updated }})</span>
        </template>
        <div class="current-prices-bar">
          <span
            v-for="(stock, code) in currentPrices.stocks"
            :key="code"
            class="price-tag"
          >
            {{ stock.name }}
            <b>{{ stock.close?.toFixed(2) }}</b>
          </span>
        </div>
      </el-card>

      <el-table :data="filteredReports" stripe v-loading="loading" height="calc(100vh - 320px)">
        <el-table-column prop="name" label="文件名" min-width="400" show-overflow-tooltip>
          <template #default="{ row }">
            <span style="font-family: monospace; font-size: 13px">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="type" label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.type === 'xlsx' ? 'success' : ''" size="small">{{ row.type.toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="size_display" label="大小" width="100" align="right" sortable />
        <el-table-column prop="modified" label="修改时间" width="170" align="center" sortable />
        <el-table-column label="操作" width="120" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" @click="downloadReport(row)">
              <el-icon><Download /></el-icon>下载
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <span class="total-info">共 {{ filteredReports.length }} 个报告（仅显示 MD / XLSX）</span>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { reportApi } from '@/api'
import { ElMessage } from 'element-plus'

interface ReportItem {
  name: string
  size: number
  size_display: string
  modified: string
  type: string
  download_url: string
}

const reports = ref<ReportItem[]>([])
const loading = ref(false)
const keyword = ref('')
const alertLog = ref<{ content: string; updated: string | null; current_prices?: CurrentPrices | null }>({ content: '', updated: null, current_prices: null })

interface StockInfo {
  name: string
  strategy: string
  close: number
  ma5: number
  ma20: number
  ma60: number
  macd_diff: number
  macd_dea: number
  kdj_k: number
  kdj_d: number
  kdj_j: number
}

interface CurrentPrices {
  updated: string
  stocks: Record<string, StockInfo>
  active_signals: string[]
}

const currentPrices = computed<CurrentPrices | null>(() => alertLog.value.current_prices ?? null)

// Track previous prices to show change direction
const prevPrices = ref<Record<string, number>>({})

function priceChangeClass(code: string): string {
  const cur = currentPrices.value?.stocks?.[code]?.close
  const prev = prevPrices.value[code]
  if (!cur || !prev) return ''
  if (cur > prev) return 'price-up'
  if (cur < prev) return 'price-down'
  return ''
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

const allowedTypes = ['md', 'xlsx']

const filteredReports = computed(() => {
  let list = reports.value.filter(r => allowedTypes.includes(r.type))
  if (keyword.value) {
    const kw = keyword.value.toLowerCase()
    list = list.filter(r => r.name.toLowerCase().includes(kw))
  }
  return list
})

async function fetchReports() {
  loading.value = true
  try {
    const res = await reportApi.list()
    reports.value = res.data || []
  } catch (err) { console.error('Operation failed:', err); 
    ElMessage.error('加载报告列表失败')
  } finally {
    loading.value = false
  }
}

function downloadReport(row: ReportItem) {
  const url = reportApi.downloadUrl(row.name)
  const a = document.createElement('a')
  a.href = url
  a.download = row.name
  a.click()
}

async function fetchAlertLog() {
  try {
    const res = await reportApi.getAlertLog()
    const data = res.data || { content: '', updated: null, current_prices: null }
    // Save old prices for change comparison
    if (alertLog.value.current_prices?.stocks) {
      const oldPrices: Record<string, number> = {}
      for (const [code, stock] of Object.entries(alertLog.value.current_prices.stocks)) {
        oldPrices[code] = (stock as StockInfo).close
      }
      prevPrices.value = oldPrices
    }
    alertLog.value = data
  } catch (err) { console.error('Operation failed:', err); 
    // ignore
  }
}

function onSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {}, 300)
}

let alertTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  fetchReports()
  fetchAlertLog()
  // Auto-refresh alert log every 2 minutes to pick up new prices
  alertTimer = setInterval(fetchAlertLog, 2 * 60 * 1000)
})

onUnmounted(() => {
  if (alertTimer) {
    clearInterval(alertTimer)
    alertTimer = null
  }
})
</script>

<style scoped>
.report-page {
  padding: 0;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 16px;
  font-weight: 600;
}
.alert-header {
  display: flex;
  align-items: center;
  font-size: 15px;
  font-weight: 600;
}
.current-prices-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 12px;
}
.price-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  white-space: nowrap;
}
.price-tag b {
  color: #409eff;
}
.price-tag .price-up {
  color: #e53935;
}
.price-tag .price-down {
  color: #67c23a;
}
.alert-log-pre {
  white-space: pre-wrap;
  font-size: 12px;
  max-height: 200px;
  overflow-y: auto;
  margin: 0;
  color: #606266;
}
.pagination-bar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-top: 12px;
}
.total-info {
  color: #909399;
  font-size: 13px;
}
</style>
