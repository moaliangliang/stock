/**
 * 行情相关 API
 */
import request from '@/utils/request'
import type { ApiResponse, KLine, Ticker, SymbolInfo } from '@/types/api'

let _symbolsCache: Promise<ApiResponse<SymbolInfo[]>> | null = null

export const marketApi = {
  getKlines(params: {
    symbol: string
    interval?: string
    start_time?: string
    end_time?: string
    limit?: number
  }) {
    return request.get<unknown, ApiResponse<KLine[]>>('/market/klines', { params })
  },

  getTickers() {
    return request.get<unknown, ApiResponse<Ticker[]>>('/market/tickers')
  },

  getTicker(symbol: string) {
    return request.get<unknown, ApiResponse<Ticker>>(`/market/ticker/${symbol}`)
  },

  getSymbols(asset_type?: string) {
    if (!_symbolsCache) {
      _symbolsCache = request.get<unknown, ApiResponse<SymbolInfo[]>>('/market/symbols', { params: { asset_type } })
        .catch((err) => {
          _symbolsCache = null
          throw err
        })
    }
    return _symbolsCache
  },

  clearSymbolsCache() {
    _symbolsCache = null
  },

  refreshKlines(symbol: string, interval: string = '1d') {
    return request.post<unknown, ApiResponse<{ message: string; inserted: number }>>('/market/refresh-klines', null, { params: { symbol, interval } })
  },

  toggleWatched(symbol: string) {
    return request.put<unknown, ApiResponse<{ symbol: string; is_watched: boolean }>>(`/market/symbols/${symbol}/watched`)
  },
}
