import { useEffect } from 'react'

export const APP_VIEWPORT_HEIGHT_VAR = '--app-viewport-height'

/**
 * Android Chrome은 soft keyboard가 닫힌 뒤 dynamic viewport(100dvh)를
 * 즉시 복원하지 않아 화면 하단에 흰 공백이 남는다. visualViewport의 실제
 * 높이를 CSS 변수로 동기화해 레이아웃이 곧바로 원래 높이로 돌아오게 한다.
 *
 * 앱 루트에서 한 번만 호출한다(전역 listener 중복 등록 금지).
 */
export function useViewportHeight() {
  useEffect(() => {
    if (typeof window === 'undefined') return

    const root = document.documentElement
    const visualViewport = window.visualViewport ?? null

    const applyViewportHeight = () => {
      const height = visualViewport?.height ?? window.innerHeight
      if (!Number.isFinite(height) || height <= 0) return
      root.style.setProperty(APP_VIEWPORT_HEIGHT_VAR, `${height}px`)
    }

    applyViewportHeight()

    // visualViewport가 있으면 keyboard open/close를 가장 정확히 반영한다.
    visualViewport?.addEventListener('resize', applyViewportHeight)
    // keyboard가 올라온 채 페이지가 밀리는 경우 offsetTop 변화만 오기도 한다.
    visualViewport?.addEventListener('scroll', applyViewportHeight)
    // visualViewport 미지원 브라우저용 fallback.
    window.addEventListener('resize', applyViewportHeight)
    window.addEventListener('orientationchange', applyViewportHeight)

    return () => {
      visualViewport?.removeEventListener('resize', applyViewportHeight)
      visualViewport?.removeEventListener('scroll', applyViewportHeight)
      window.removeEventListener('resize', applyViewportHeight)
      window.removeEventListener('orientationchange', applyViewportHeight)
      root.style.removeProperty(APP_VIEWPORT_HEIGHT_VAR)
    }
  }, [])
}
