import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import {
  APP_VIEWPORT_HEIGHT_VAR,
  useViewportHeight,
} from '../src/hooks/useViewportHeight'

type VisualViewportStub = {
  height: number
  addEventListener: ReturnType<typeof vi.fn>
  removeEventListener: ReturnType<typeof vi.fn>
  dispatch: (type: string) => void
}

function stubVisualViewport(height: number): VisualViewportStub {
  const listeners = new Map<string, Set<() => void>>()

  const stub: VisualViewportStub = {
    height,
    addEventListener: vi.fn((type: string, handler: () => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type)?.add(handler)
    }),
    removeEventListener: vi.fn((type: string, handler: () => void) => {
      listeners.get(type)?.delete(handler)
    }),
    dispatch: (type: string) => {
      listeners.get(type)?.forEach((handler) => handler())
    },
  }

  vi.stubGlobal('visualViewport', stub)
  return stub
}

function Probe() {
  useViewportHeight()
  return null
}

function readViewportVar() {
  return document.documentElement.style.getPropertyValue(
    APP_VIEWPORT_HEIGHT_VAR,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.documentElement.style.removeProperty(APP_VIEWPORT_HEIGHT_VAR)
})

describe('useViewportHeight', () => {
  it('mount 시 visualViewport 높이로 CSS 변수를 설정한다', () => {
    stubVisualViewport(720)

    render(<Probe />)

    expect(readViewportVar()).toBe('720px')
  })

  it('visualViewport resize 시 값을 갱신한다', () => {
    const viewport = stubVisualViewport(720)
    render(<Probe />)
    expect(readViewportVar()).toBe('720px')

    // soft keyboard open
    viewport.height = 400
    viewport.dispatch('resize')
    expect(readViewportVar()).toBe('400px')

    // soft keyboard close -> 즉시 원래 높이로 복구
    viewport.height = 720
    viewport.dispatch('resize')
    expect(readViewportVar()).toBe('720px')
  })

  it('visualViewport scroll 이벤트에도 값을 동기화한다', () => {
    const viewport = stubVisualViewport(720)
    render(<Probe />)

    viewport.height = 540
    viewport.dispatch('scroll')

    expect(readViewportVar()).toBe('540px')
  })

  it('visualViewport 미지원 시 window.innerHeight로 fallback한다', () => {
    vi.stubGlobal('visualViewport', undefined)
    vi.stubGlobal('innerHeight', 812)

    render(<Probe />)

    expect(readViewportVar()).toBe('812px')
  })

  it('unmount 시 listener와 CSS 변수를 정리한다', () => {
    const viewport = stubVisualViewport(720)
    const { unmount } = render(<Probe />)

    expect(viewport.addEventListener).toHaveBeenCalledWith(
      'resize',
      expect.any(Function),
    )

    unmount()

    expect(viewport.removeEventListener).toHaveBeenCalledWith(
      'resize',
      expect.any(Function),
    )
    expect(viewport.removeEventListener).toHaveBeenCalledWith(
      'scroll',
      expect.any(Function),
    )
    expect(readViewportVar()).toBe('')

    // 정리 후에는 더 이상 갱신되지 않는다.
    viewport.height = 100
    viewport.dispatch('resize')
    expect(readViewportVar()).toBe('')
  })

  it('비정상 높이 값은 무시한다', () => {
    const viewport = stubVisualViewport(720)
    render(<Probe />)

    viewport.height = 0
    viewport.dispatch('resize')

    expect(readViewportVar()).toBe('720px')
  })
})
