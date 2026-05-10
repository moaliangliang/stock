/** 通用 API 响应 */
export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

/** 登录请求 */
export interface LoginRequest {
  username: string
  password: string
}

/** 用户信息 */
export interface UserInfo {
  id: number
  username: string
  email: string
  nickname: string
  role: string
  is_active: boolean
  is_superuser: boolean
  max_position_ratio: number
  max_daily_loss: number
  created_at: string
}

/** 登录响应 */
export interface TokenData {
  access_token: string
  token_type: string
  user: UserInfo
}

/** 标的信息 */
export interface SymbolInfo {
  id: number
  symbol: string
  name: string
  exchange: string
  asset_type: string
  price_precision: number
  qty_precision: number
  min_qty: number
  tick_size: number
  status: string
  is_watched: boolean
}

/** K 线数据 */
export interface KLine {
  symbol: string
  interval: string
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
}

/** 实时行情 */
export interface Ticker {
  symbol: string
  name?: string
  last_price: number
  bid?: number
  ask?: number
  bid_volume?: number
  ask_volume?: number
  high_24h?: number
  low_24h?: number
  volume_24h?: number
  change_24h?: number
  updated_at: string
}
