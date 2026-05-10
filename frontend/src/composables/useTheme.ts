import { watchEffect } from 'vue'
import { useAppStore } from '@/store'

export function useTheme() {
  const appStore = useAppStore()

  function applyTheme(mode: 'light' | 'dark') {
    document.documentElement.classList.toggle('dark', mode === 'dark')
    document.documentElement.style.colorScheme = mode
  }

  watchEffect(() => {
    if (appStore.theme === 'auto') {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      applyTheme(mq.matches ? 'dark' : 'light')
    } else {
      applyTheme(appStore.theme)
    }
  })
}
