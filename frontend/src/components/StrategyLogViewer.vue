<template>
  <div>
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px">
      <el-select v-model="statusFilter" placeholder="状态" size="small" style="width: 100px" clearable>
        <el-option label="全部" value="" />
        <el-option label="成功" value="success" />
        <el-option label="失败" value="error" />
      </el-select>
      <el-button size="small" @click="loadLogs" :loading="loading">刷新</el-button>
      <el-checkbox v-model="autoRefresh" label="自动刷新" size="small" />
    </div>
    <el-table :data="filteredLogs" size="small" stripe v-loading="loading" @expand-change="onExpand">
      <el-table-column type="expand">
        <template #default="{ row }">
          <pre v-if="row.signals" style="font-size:12px;background:#f5f5f5;padding:12px;border-radius:4px;max-height:300px;overflow:auto">{{ JSON.stringify(row.signals, null, 2) }}</pre>
          <div v-else style="color:#999;padding:12px">无信号数据</div>
        </template>
      </el-table-column>
      <el-table-column prop="run_time" label="运行时间" width="160" />
      <el-table-column prop="status" label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="message" label="消息" min-width="200" show-overflow-tooltip />
      <el-table-column prop="duration_ms" label="耗时(ms)" width="90">
        <template #default="{ row }">{{ row.duration_ms ?? '-' }}</template>
      </el-table-column>
    </el-table>
    <div v-if="!filteredLogs.length && !loading" style="text-align:center;padding:40px 0;color:#999">暂无运行日志</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { strategyApi } from '@/api'

const props = defineProps<{ strategyId: number }>()

const logs = ref<any[]>([])
const loading = ref(false)
const statusFilter = ref('')
const autoRefresh = ref(false)
let refreshTimer: ReturnType<typeof setInterval> | null = null

const filteredLogs = computed(() => {
  if (!statusFilter.value) return logs.value
  return logs.value.filter(l => l.status === statusFilter.value)
})

async function loadLogs() {
  loading.value = true
  try {
    const res = await strategyApi.getStrategyLogs(props.strategyId, { limit: 50 })
    logs.value = res.data || []
  } catch (err) {
    console.error('Failed to load logs', err)
  } finally {
    loading.value = false
  }
}

function onExpand(row: any, expanded: boolean) {
  // 展开行时暂无额外操作
}

watch(autoRefresh, (val) => {
  if (val) {
    loadLogs()
    refreshTimer = setInterval(loadLogs, 10000)
  } else if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

loadLogs()
</script>
