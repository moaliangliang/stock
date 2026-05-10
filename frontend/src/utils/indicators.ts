/**
 * 客户端技术指标计算
 * 纯函数，输入 OHLCV 数组，输出指标数据
 */

/** 简单移动平均线 */
export function calcMA(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
    } else {
      let sum = 0
      for (let j = i - period + 1; j <= i; j++) sum += data[j]
      result.push(sum / period)
    }
  }
  return result
}

/** EMA 指数移动平均 */
function calcEMA(data: number[], period: number): number[] {
  const result: number[] = []
  const multiplier = 2 / (period + 1)
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(data[0])
    } else {
      result.push((data[i] - result[i - 1]) * multiplier + result[i - 1])
    }
  }
  return result
}

/** MACD */
export function calcMACD(data: number[], fast = 12, slow = 26, signal = 9) {
  const emaFast = calcEMA(data, fast)
  const emaSlow = calcEMA(data, slow)
  const dif: number[] = emaFast.map((v, i) => v - emaSlow[i])
  const dea = calcEMA(dif, signal)
  const histogram: number[] = dif.map((v, i) => v - dea[i])
  return { dif, dea, histogram }
}

/** 布林带 */
export function calcBOLL(data: number[], period = 20, stdDev = 2) {
  const mid = calcMA(data, period)
  const upper: (number | null)[] = []
  const lower: (number | null)[] = []

  for (let i = 0; i < data.length; i++) {
    if (mid[i] === null) {
      upper.push(null)
      lower.push(null)
    } else {
      let sum = 0
      let count = 0
      for (let j = Math.max(0, i - period + 1); j <= i; j++) {
        sum += Math.pow(data[j] - (mid[i] as number), 2)
        count++
      }
      const std = Math.sqrt(sum / count)
      upper.push((mid[i] as number) + stdDev * std)
      lower.push((mid[i] as number) - stdDev * std)
    }
  }

  return { upper, mid, lower }
}
