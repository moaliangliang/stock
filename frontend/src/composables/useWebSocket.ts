/**
 * WebSocket Composable - 自动重连、心跳、消息分发
 */
import { ref, onUnmounted } from 'vue'

interface UseWebSocketOptions {
  url: string
  reconnectInterval?: number
  maxRetries?: number
  onMessage?: (data: any) => void
  onStatusChange?: (connected: boolean) => void
}

export function useWebSocket(options: UseWebSocketOptions) {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let retries = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  const maxRetries = options.maxRetries ?? 10
  const reconnectInterval = options.reconnectInterval ?? 3000

  function connect() {
    if (ws?.readyState === WebSocket.OPEN) return

    try {
      ws = new WebSocket(options.url)
    } catch (err) {
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      retries = 0
      options.onStatusChange?.(true)
      // 心跳每 30s
      heartbeatTimer = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }))
      }, 30000)
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        options.onMessage?.(data)
      } catch { /* ignore non-JSON messages */ }
    }

    ws.onclose = () => {
      connected.value = false
      options.onStatusChange?.(false)
      clearHeartbeat()
      scheduleReconnect()
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  function scheduleReconnect() {
    if (retries >= maxRetries) return
    retries++
    const delay = Math.min(reconnectInterval * Math.pow(1.5, retries - 1), 30000)
    reconnectTimer = setTimeout(connect, delay)
  }

  function clearHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function disconnect() {
    clearHeartbeat()
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    ws?.close()
    ws = null
    connected.value = false
    retries = maxRetries // 阻止重连
  }

  onUnmounted(disconnect)

  connect()

  return { connected, connect, disconnect }
}
