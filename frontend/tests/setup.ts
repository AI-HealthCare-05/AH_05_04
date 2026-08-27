import { beforeEach } from 'vitest'

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

beforeEach(() => {
  jsdomLocalStorage.clear()
  jsdomSessionStorage.clear()
})
