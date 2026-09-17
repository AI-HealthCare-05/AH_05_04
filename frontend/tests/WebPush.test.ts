import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { deletePushSubscription, getPushConfig, upsertPushSubscription } from '../src/api/push'
import { ApiError } from '../src/api/client'
import {
  beginWebPushLogoutCleanup,
  enableWebPush,
  getWebPushLaunchContext,
  getWebPushState,
  inspectWebPushState,
} from '../src/features/push/webPush'

vi.mock('../src/api/push', () => ({
  getPushConfig: vi.fn(),
  upsertPushSubscription: vi.fn(),
  deletePushSubscription: vi.fn(),
}))

const postMessage = vi.fn()
const getSubscription = vi.fn()
const subscribe = vi.fn()
const requestPermission = vi.fn()

function installBrowserSupport(permission: NotificationPermission = 'default') {
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  Object.defineProperty(window, 'PushManager', { configurable: true, value: class PushManager {} })
  Object.defineProperty(window, 'Notification', {
    configurable: true,
    value: { permission, requestPermission },
  })
  const registration = {
    active: { postMessage },
    waiting: null,
    installing: null,
    pushManager: { getSubscription, subscribe },
  }
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: {
      ready: Promise.resolve(registration),
      getRegistration: vi.fn().mockResolvedValue(registration),
      register: vi.fn().mockResolvedValue(registration),
    },
  })
}

function installDeviceContext({
  userAgent,
  platform,
  maxTouchPoints = 0,
  standalone = false,
  displayModeStandalone = false,
}: {
  userAgent: string
  platform: string
  maxTouchPoints?: number
  standalone?: boolean
  displayModeStandalone?: boolean
}) {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: userAgent })
  Object.defineProperty(navigator, 'platform', { configurable: true, value: platform })
  Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, value: maxTouchPoints })
  Object.defineProperty(navigator, 'standalone', { configurable: true, value: standalone })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(display-mode: standalone)' && displayModeStandalone,
      media: query,
    })),
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  postMessage.mockImplementation((_, transfer: MessagePort[] | undefined) => {
    transfer?.[0]?.postMessage('stored')
  })
  installBrowserSupport()
  installDeviceContext({
    userAgent: 'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36',
    platform: 'Linux armv8l',
  })
  vi.mocked(getPushConfig).mockResolvedValue({ data: { public_key: 'BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' } })
  vi.mocked(upsertPushSubscription).mockResolvedValue({ data: { id: 'subscription-id', generation: 'generation-id' } })
})

afterEach(() => {
  localStorage.clear()
})

describe('Web Push permission과 구독', () => {
  it('iPhone Safari 일반 탭은 설치 안내 대상으로 구분하고 권한을 요청하지 않는다', async () => {
    installDeviceContext({
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 Version/18.6 Mobile/15E148 Safari/604.1',
      platform: 'iPhone',
    })

    expect(getWebPushLaunchContext()).toBe('ios-browser')
    expect(await enableWebPush()).toBe('unsupported')
    expect(requestPermission).not.toHaveBeenCalled()
  })

  it('iPad 홈 화면 웹앱은 navigator.standalone으로 standalone을 감지한다', () => {
    installDeviceContext({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.6 Mobile/15E148 Safari/604.1',
      platform: 'MacIntel',
      maxTouchPoints: 5,
      standalone: true,
    })

    expect(getWebPushLaunchContext()).toBe('standalone')
  })

  it('데스크톱 UA를 사용하는 iPad Safari 일반 탭도 설치 안내 대상으로 구분한다', () => {
    installDeviceContext({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.6 Mobile/15E148 Safari/604.1',
      platform: 'MacIntel',
      maxTouchPoints: 5,
    })

    expect(getWebPushLaunchContext()).toBe('ios-browser')
  })

  it('설치 웹앱은 display-mode standalone도 감지한다', () => {
    installDeviceContext({
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
      platform: 'iPhone',
      displayModeStandalone: true,
    })

    expect(getWebPushLaunchContext()).toBe('standalone')
  })

  it('Android Chrome 일반 탭은 기존 browser 권한 흐름을 유지한다', () => {
    expect(getWebPushLaunchContext()).toBe('browser')
  })

  it('상태 확인만으로 권한 prompt를 띄우지 않는다', async () => {
    expect(await getWebPushState()).toBe('unrequested')
    expect(requestPermission).not.toHaveBeenCalled()
  })

  it('사용자가 알림 켜기를 실행한 뒤에만 권한과 Backend 등록을 연결한다', async () => {
    requestPermission.mockResolvedValue('granted')
    const subscription = {
      toJSON: () => ({
        endpoint: 'https://push.example.test/subscription',
        expirationTime: null,
        keys: { p256dh: 'synthetic-p256dh', auth: 'synthetic-auth' },
      }),
    }
    getSubscription.mockResolvedValue(null)
    subscribe.mockResolvedValue(subscription)

    expect(await enableWebPush()).toBe('granted')

    expect(requestPermission).toHaveBeenCalledTimes(1)
    expect(upsertPushSubscription).toHaveBeenCalledWith({
      endpoint: 'https://push.example.test/subscription',
      keys: { p256dh: 'synthetic-p256dh', auth: 'synthetic-auth' },
    })
    expect(upsertPushSubscription).not.toHaveBeenCalledWith(expect.objectContaining({ expirationTime: null }))
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'DOSEY_PUSH_GENERATION', generation: 'generation-id' },
      expect.any(Array),
    )
  })

  it('거절된 권한에는 prompt를 반복하지 않고 구독하지 않는다', async () => {
    installBrowserSupport('denied')

    expect(await enableWebPush()).toBe('denied')
    expect(requestPermission).not.toHaveBeenCalled()
    expect(subscribe).not.toHaveBeenCalled()
    expect(upsertPushSubscription).not.toHaveBeenCalled()
  })

  it('기존 binding 뒤 권한 철회는 denied와 구분한 revoked 상태다', async () => {
    localStorage.setItem('dosey_web_push_binding:v1', JSON.stringify({
      id: 'subscription-id',
      generation: 'generation-id',
    }))
    installBrowserSupport('denied')
    vi.mocked(deletePushSubscription).mockResolvedValue(undefined)

    expect(await getWebPushState()).toBe('revoked')
    expect(deletePushSubscription).toHaveBeenCalledWith('subscription-id')
    expect(localStorage.getItem('dosey_web_push_binding:v1')).toBeNull()
  })

  it('권한 철회 cleanup의 DELETE가 실패하면 generation은 끄고 binding id는 재시도용으로 보존한다', async () => {
    localStorage.setItem('dosey_web_push_binding:v1', JSON.stringify({
      id: 'subscription-id',
      generation: 'generation-id',
    }))
    installBrowserSupport('denied')
    vi.mocked(deletePushSubscription).mockRejectedValue(new TypeError('synthetic network failure'))

    expect(await getWebPushState()).toBe('subscription_failed')
    expect(localStorage.getItem('dosey_web_push_binding:v1')).toContain('subscription-id')
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'DOSEY_PUSH_GENERATION', generation: null },
      expect.any(Array),
    )
  })

  it('브라우저 구독 실패는 permission 거절과 다른 상태로 안내한다', async () => {
    requestPermission.mockResolvedValue('granted')
    getSubscription.mockResolvedValue(null)
    subscribe.mockRejectedValue(new Error('synthetic subscription failure'))

    expect(await enableWebPush()).toBe('subscription_failed')
    expect(upsertPushSubscription).not.toHaveBeenCalled()
  })

  it('Push 설정이 준비되지 않았으면 권한 prompt 없이 설정 미완료로 안내한다', async () => {
    vi.mocked(getPushConfig).mockRejectedValue(new ApiError(503, 'Push 설정 미완료', 'SERVICE_UNAVAILABLE'))

    expect(await enableWebPush()).toBe('config_unavailable')
    expect(requestPermission).not.toHaveBeenCalled()
    expect(navigator.serviceWorker.register).not.toHaveBeenCalled()
    expect(subscribe).not.toHaveBeenCalled()
    expect(upsertPushSubscription).not.toHaveBeenCalled()
  })

  it('다른 사용자 endpoint conflict는 기존 browser 구독을 버리고 새 endpoint를 등록한다', async () => {
    installBrowserSupport('granted')
    const unsubscribe = vi.fn().mockResolvedValue(true)
    const existing = {
      unsubscribe,
      toJSON: () => ({ endpoint: 'https://push.example.test/old', keys: { p256dh: 'old-key', auth: 'old-auth' } }),
    }
    const replacement = {
      toJSON: () => ({ endpoint: 'https://push.example.test/new', keys: { p256dh: 'new-key', auth: 'new-auth' } }),
    }
    getSubscription.mockResolvedValue(existing)
    subscribe.mockResolvedValue(replacement)
    vi.mocked(upsertPushSubscription)
      .mockRejectedValueOnce(new ApiError(409, '새 구독 필요', 'PUSH_SUBSCRIPTION_CONFLICT'))
      .mockResolvedValueOnce({ data: { id: 'replacement-id', generation: 'replacement-generation' } })

    expect(await enableWebPush()).toBe('granted')
    expect(unsubscribe).toHaveBeenCalledTimes(1)
    expect(subscribe).toHaveBeenCalledTimes(1)
    expect(upsertPushSubscription).toHaveBeenNthCalledWith(2, {
      endpoint: 'https://push.example.test/new',
      keys: { p256dh: 'new-key', auth: 'new-auth' },
    })
  })

  it('지원되지 않는 환경은 앱 내부 알림 fallback용 unsupported 상태를 반환한다', async () => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
    expect(await getWebPushState()).toBe('unsupported')
  })

  it('logout 시작 시 서버 해제와 로컬 generation 제거를 함께 시작한다', async () => {
    localStorage.setItem('dosey_web_push_binding:v1', JSON.stringify({
      id: 'subscription-id',
      generation: 'generation-id',
    }))
    vi.mocked(deletePushSubscription).mockResolvedValue(undefined)
    getSubscription.mockResolvedValue({ unsubscribe: vi.fn().mockResolvedValue(true) })

    beginWebPushLogoutCleanup()

    expect(localStorage.getItem('dosey_web_push_binding:v1')).toBeNull()
    expect(deletePushSubscription).toHaveBeenCalledWith('subscription-id')
    await vi.waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        { type: 'DOSEY_PUSH_GENERATION', generation: null },
        expect.any(Array),
      )
    })
  })

  it('기존 browser 구독도 settings 재진입 시 Backend와 generation을 재조정한다', async () => {
    localStorage.setItem('dosey_web_push_binding:v1', JSON.stringify({
      id: 'subscription-id',
      generation: 'old-generation',
    }))
    installBrowserSupport('granted')
    getSubscription.mockResolvedValue({
      toJSON: () => ({ endpoint: 'https://push.example.test/current', keys: { p256dh: 'current-key', auth: 'current-auth' } }),
    })
    vi.mocked(upsertPushSubscription).mockResolvedValue({
      data: { id: 'subscription-id', generation: 'current-generation' },
    })

    expect(await getWebPushState()).toBe('granted')
    expect(upsertPushSubscription).toHaveBeenCalledWith({
      endpoint: 'https://push.example.test/current',
      keys: { p256dh: 'current-key', auth: 'current-auth' },
    })
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'DOSEY_PUSH_GENERATION', generation: 'current-generation' },
      expect.any(Array),
    )
  })
})


describe('read-only notification inspection', () => {
  it.each([
    ['granted', true, true, 'granted'],
    ['granted', true, false, 'revoked'],
    ['granted', false, true, 'unrequested'],
    ['denied', true, true, 'revoked'],
    ['denied', false, true, 'denied'],
    ['default', false, false, 'unrequested'],
  ] as const)('observes %s binding=%s subscription=%s without mutation', async (permission, binding, subscribed, expected) => {
    installBrowserSupport(permission)
    const unsubscribe = vi.fn()
    getSubscription.mockResolvedValue(subscribed ? { unsubscribe } : null)
    if (binding) localStorage.setItem('dosey_web_push_binding:v1', JSON.stringify({ id: 'synthetic', generation: 'synthetic-generation' }))
    const before = localStorage.getItem('dosey_web_push_binding:v1')
    expect(await inspectWebPushState()).toBe(expected)
    expect(localStorage.getItem('dosey_web_push_binding:v1')).toBe(before)
    expect(navigator.serviceWorker.register).not.toHaveBeenCalled()
    expect(requestPermission).not.toHaveBeenCalled()
    expect(subscribe).not.toHaveBeenCalled()
    expect(unsubscribe).not.toHaveBeenCalled()
    expect(upsertPushSubscription).not.toHaveBeenCalled()
    expect(deletePushSubscription).not.toHaveBeenCalled()
    expect(postMessage).not.toHaveBeenCalled()
    expect(getPushConfig).not.toHaveBeenCalled()
  })
  it('reports read failure without reconciling subscriptions', async () => {
    installBrowserSupport('granted')
    vi.mocked(navigator.serviceWorker.getRegistration).mockRejectedValue(new Error('offline'))
    expect(await inspectWebPushState()).toBe('subscription_failed')
    expect(upsertPushSubscription).not.toHaveBeenCalled()
    expect(deletePushSubscription).not.toHaveBeenCalled()
  })
})


it('inspects an unsupported environment without registering a worker', async () => {
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
  expect(await inspectWebPushState()).toBe('unsupported')
  expect(navigator.serviceWorker.register).not.toHaveBeenCalled()
})
