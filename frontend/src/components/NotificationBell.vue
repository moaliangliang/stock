<template>
  <el-popover placement="bottom" :width="350" trigger="click" @show="onShow">
    <template #reference>
      <el-badge :value="unreadCount" :hidden="unreadCount === 0" class="notification-badge">
        <el-button size="small" circle>
          <el-icon><Bell /></el-icon>
        </el-button>
      </el-badge>
    </template>
    <div>
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
        <span style="font-weight: 600; font-size: 14px">通知</span>
        <el-link v-if="unreadCount > 0" type="primary" :underline="false" size="small" @click="handleMarkAllRead">
          全部已读
        </el-link>
      </div>
      <div v-if="loading" style="text-align: center; padding: 20px; color: #999">加载中...</div>
      <div v-else-if="!list.length" style="text-align: center; padding: 20px; color: #999">暂无通知</div>
      <div v-else class="notification-list">
        <div
          v-for="item in list"
          :key="item.id"
          class="notification-item"
          :class="{ unread: !item.is_read }"
          @click="handleClick(item)"
        >
          <div class="notification-type">
            <el-tag :type="typeTag(item.type)" size="small" effect="plain">{{ typeLabel(item.type) }}</el-tag>
          </div>
          <div class="notification-title">{{ item.title }}</div>
          <div v-if="item.content" class="notification-content">{{ item.content }}</div>
          <div class="notification-time">{{ formatTime(item.created_at) }}</div>
        </div>
      </div>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { notificationApi } from '@/api/notification'

const unreadCount = ref(0)
const list = ref<any[]>([])
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const typeLabels: Record<string, string> = {
  trade: '交易',
  risk: '风控',
  strategy: '策略',
  system: '系统',
}

function typeLabel(type: string) {
  return typeLabels[type] || type
}

function typeTag(type: string) {
  return { trade: 'danger', risk: 'warning', strategy: 'primary', system: 'info' }[type] || 'info'
}

function formatTime(ts: string) {
  if (!ts) return ''
  const d = new Date(ts)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  return d.toLocaleDateString()
}

async function loadUnreadCount() {
  try {
    const res = await notificationApi.getUnreadCount()
    unreadCount.value = res.data?.count || 0
  } catch { /* ignore */ }
}

async function onShow() {
  loading.value = true
  try {
    const res = await notificationApi.getNotifications({ limit: 10 })
    list.value = res.data || []
  } catch { /* ignore */ }
  loading.value = false
}

async function handleClick(item: any) {
  if (!item.is_read) {
    try {
      await notificationApi.markRead(item.id)
      item.is_read = true
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    } catch { /* ignore */ }
  }
}

async function handleMarkAllRead() {
  try {
    await notificationApi.markAllRead()
    list.value.forEach((i: any) => (i.is_read = true))
    unreadCount.value = 0
    ElMessage.success('已全部标记为已读')
  } catch { /* ignore */ }
}

// WebSocket：实时接收决策强信号推送
let ws: WebSocket | null = null
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/api/v1/ws/tickers`
  try {
    ws = new WebSocket(url)
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'decision_alert') {
          const d = msg.data
          ElNotification({
            title: d.label + ' - ' + d.symbol,
            message: d.summary,
            type: d.recommendation === 'strong_buy' ? 'success' : 'error',
            duration: 8000,
          })
          loadUnreadCount()
        }
      } catch { /* ignore */ }
    }
  } catch { /* ignore */ }
}

onMounted(() => {
  loadUnreadCount()
  timer = setInterval(loadUnreadCount, 30000)
  connectWS()
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  ws?.close()
})
</script>

<style scoped>
.notification-badge {
  margin-right: 12px;
}
.notification-list {
  max-height: 400px;
  overflow-y: auto;
}
.notification-item {
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
}
.notification-item.unread {
  background: #f6f9ff;
  margin: 0 -12px;
  padding: 10px 12px;
}
.notification-item:last-child {
  border-bottom: none;
}
.notification-item:hover {
  opacity: 0.8;
}
.notification-type {
  margin-bottom: 4px;
}
.notification-title {
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 2px;
}
.notification-content {
  font-size: 12px;
  color: #666;
  margin-bottom: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.notification-time {
  font-size: 11px;
  color: #999;
}
</style>
