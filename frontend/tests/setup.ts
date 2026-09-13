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

function clearWebStorage() {
  jsdomLocalStorage.clear()
  jsdomSessionStorage.clear()
}

// 테스트가 남긴 Web Storage 상태는 다음 테스트에 노출되지 않아야 하므로
// 시작과 종료 양쪽에서 정리합니다.
beforeEach(clearWebStorage)

afterEach(clearWebStorage)
