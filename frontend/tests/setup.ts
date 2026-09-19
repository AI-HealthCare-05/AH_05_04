import { afterEach, beforeEach } from 'vitest'

type JSDOMEnvironmentGlobal = typeof globalThis & {
  jsdom?: {
    window: {
      localStorage: Storage
      sessionStorage: Storage
    }
  }
}

const jsdomWindow = (globalThis as JSDOMEnvironmentGlobal).jsdom?.window

if (!jsdomWindow) {
  throw new Error('Vitest tests require the jsdom environment')
}

const jsdomLocalStorage = jsdomWindow.localStorage
const jsdomSessionStorage = jsdomWindow.sessionStorage

Object.defineProperties(globalThis, {
  localStorage: {
    configurable: true,
    value: jsdomLocalStorage,
  },
  sessionStorage: {
    configurable: true,
    value: jsdomSessionStorage,
  },
})

// jsdom은 PointerEvent를 구현하지 않아 pointerId가 유실된다.
// Pointer 기반 gesture 테스트를 위해 최소 구현만 보강한다.
type PointerEventInitLike = MouseEventInit & {
  pointerId?: number
  pointerType?: string
  isPrimary?: boolean
}

if (typeof (globalThis as { PointerEvent?: unknown }).PointerEvent !== 'function') {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number
    readonly pointerType: string
    readonly isPrimary: boolean

    constructor(type: string, init: PointerEventInitLike = {}) {
      super(type, init)
      this.pointerId = init.pointerId ?? 0
      this.pointerType = init.pointerType ?? 'mouse'
      this.isPrimary = init.isPrimary ?? true
    }
  }

  Object.defineProperty(globalThis, 'PointerEvent', {
    configurable: true,
    writable: true,
    value: PointerEventPolyfill,
  })
  Object.defineProperty(jsdomWindow, 'PointerEvent', {
    configurable: true,
    writable: true,
    value: PointerEventPolyfill,
  })
}

function clearWebStorage() {
  jsdomLocalStorage.clear()
  jsdomSessionStorage.clear()
}

// 테스트가 남긴 Web Storage 상태는 다음 테스트에 노출되지 않아야 하므로
// 시작과 종료 양쪽에서 정리합니다.
beforeEach(clearWebStorage)

afterEach(clearWebStorage)
