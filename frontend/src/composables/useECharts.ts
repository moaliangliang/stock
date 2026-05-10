import { onUnmounted, type Ref } from 'vue'

let _echartsModule: any = null
async function loadECharts() {
  if (!_echartsModule) {
    _echartsModule = await import('echarts')
  }
  return _echartsModule
}

export function useECharts(chartRef: Ref<HTMLElement | undefined>) {
  let chart: any = null
  let resizeHandler: (() => void) | null = null

  async function initChart() {
    if (!chartRef.value) return null
    const echarts = await loadECharts()
    chart = echarts.init(chartRef.value)
    resizeHandler = () => chart?.resize()
    window.addEventListener('resize', resizeHandler)
    return chart
  }

  function disposeChart() {
    if (resizeHandler) {
      window.removeEventListener('resize', resizeHandler)
      resizeHandler = null
    }
    chart?.dispose()
    chart = null
  }

  function setOption(option: any, opts?: any) {
    chart?.setOption(option, opts)
  }

  function getChart() {
    return chart
  }

  onUnmounted(disposeChart)

  return { initChart, disposeChart, setOption, getChart }
}
