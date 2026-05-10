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
      <el-alert
        v-if="alertLog.content && alertLog.content !== '暂无买入信号日志'"
        :title="`买入信号日志 (更新于 ${alertLog.updated})`"
        type="success"
        :closable="false"
        style="margin-bottom: 16px"
      >
        <pre style="white-space: pre-wrap; font-size: 12px; max-height: 200px; overflow-y: auto; margin: 0">{{ alertLog.content.slice(-3000) }}</pre>
      </el-alert>

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
import { ref, computed, onMounted } from 'vue'
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
const alertLog = ref<{ content: string; updated: string | null }>({ content: '', updated: null })

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
  } catch {
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
    alertLog.value = res.data || { content: '', updated: null }
  } catch {
    // ignore
  }
}

function onSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {}, 300)
}

onMounted(() => {
  fetchReports()
  fetchAlertLog()
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
