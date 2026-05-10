/** 投资决策相关类型 */

export interface DecisionFactorDetail {
  score: number
  weight: number
  label: string
  details: Record<string, any>
}

export interface DecisionFactors {
  technical_score: number
  sentiment_score: number
  risk_score: number
  momentum_score: number
  fundamental_score: number
  composite_score: number
  technical: DecisionFactorDetail
  sentiment: DecisionFactorDetail
  risk: DecisionFactorDetail
  momentum: DecisionFactorDetail
  fundamental?: DecisionFactorDetail
  regime?: string
  weights?: Record<string, number>
  weekly_technical?: DecisionFactorDetail
}

export interface InvestmentDecision {
  id: number
  user_id: number
  symbol: string
  recommendation: 'strong_buy' | 'buy' | 'hold' | 'sell' | 'strong_sell'
  confidence: number
  target_price: number | null
  stop_loss: number | null
  factors: DecisionFactors | null
  reasoning: string | null
  status: 'active' | 'executed' | 'dismissed' | 'expired'
  valid_until: string | null
  created_at: string
  updated_at: string
}

export interface DecisionSummary {
  total_active: number
  strong_buy_count: number
  buy_count: number
  hold_count: number
  sell_count: number
  strong_sell_count: number
  avg_confidence: number
  top_picks: InvestmentDecision[]
  recent_decisions: InvestmentDecision[]
}

export interface DecisionOutcome {
  id: number
  decision_id: number
  symbol: string
  recommendation: string
  confidence: number
  entry_price: number | null
  actual_high_24h: number | null
  actual_low_24h: number | null
  actual_close_24h: number | null
  hit_target: boolean
  hit_stop: boolean
  pnl_pct: number | null
  outcome: 'win' | 'loss' | 'breakeven' | null
  checked_at: string | null
}

export interface OutcomeSummary {
  total: number
  wins: number
  losses: number
  breakeven_count: number
  win_rate: number
  avg_pnl_pct: number
  strong_buy_accuracy: number
  buy_accuracy: number
  hold_accuracy: number
  sell_accuracy: number
  strong_sell_accuracy: number
  recent_outcomes: DecisionOutcome[]
}

export const recommendationConfig: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' | '' }> = {
  strong_buy: { label: '强烈买入', type: 'success' },
  buy: { label: '买入', type: 'success' },
  hold: { label: '持有', type: 'info' },
  sell: { label: '卖出', type: 'danger' },
  strong_sell: { label: '强烈卖出', type: 'danger' },
}

export const outcomeConfig: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
  win: { label: '正确', type: 'success' },
  loss: { label: '错误', type: 'danger' },
  breakeven: { label: '平盘', type: 'info' },
}

export const regimeLabels: Record<string, string> = {
  trending_up: '上涨趋势',
  trending_down: '下跌趋势',
  ranging: '震荡整理',
  volatile: '高波动',
}
