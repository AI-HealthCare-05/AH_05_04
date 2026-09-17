import {
  deletePushSubscription,
  getPushConfig,
  upsertPushSubscription,
  type PushSubscriptionRequest,
} from '../../api/push'
import { ApiError } from '../../api/client'

const BINDING_STORAGE_KEY = 'dosey_web_push_binding:v1'
const SERVICE_WORKER_PATH = '/sw.js'

export type WebPushState =
  | 'unsupported'
  | 'unrequested'
  | 'granted'
  | 'denied'
  | 'revoked'
  | 'config_unavailable'
  | 'subscription_failed'

export type WebPushLaunchContext = 'ios-browser' | 'standalone' | 'browser'

type IOSNavigator = Navigator & {
  standalone?: boolean
}

type StoredPushBinding = {
  id: string
  generation: string
}

function readStoredBinding(): StoredPushBinding | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(BINDING_STORAGE_KEY) ?? 'null') as unknown
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      typeof (parsed as StoredPushBinding).id === 'string' &&
      typeof (parsed as StoredPushBinding).generation === 'string'
    ) {
      return parsed as StoredPushBinding
    }
  } catch {
    // Corrupt local state is treated as absent and never sent to the Backend.
  }
  return null
}

function supportsWebPush(): boolean {
  return (
    window.isSecureContext &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

function isIOSDevice(): boolean {
  const isAppleMobileDevice = /iPad|iPhone|iPod/.test(navigator.userAgent)
  const isIPadDesktopMode = navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1
  return isAppleMobileDevice || isIPadDesktopMode
}

export function getWebPushLaunchContext(): WebPushLaunchContext {
  const displayModeStandalone = typeof window.matchMedia === 'function'
    && window.matchMedia('(display-mode: standalone)').matches
  const iosStandalone = (navigator as IOSNavigator).standalone === true

  if (displayModeStandalone || iosStandalone) return 'standalone'
  if (isIOSDevice()) return 'ios-browser'
  return 'browser'
}

function decodeApplicationServerKey(value: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (value.length % 4)) % 4)
  const decoded = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'))
  const bytes = new Uint8Array(new ArrayBuffer(decoded.length))
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index)
  }
  return bytes
}

function isPushConfigUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503
}

function subscriptionRequest(subscription: PushSubscription): PushSubscriptionRequest {
  const serialized = subscription.toJSON()
  if (
    typeof serialized.endpoint !== 'string' ||
    typeof serialized.keys?.p256dh !== 'string' ||
    typeof serialized.keys.auth !== 'string'
  ) {
    throw new Error('PUSH_SUBSCRIPTION_INVALID')
  }
  return {
    endpoint: serialized.endpoint,
    keys: {
      p256dh: serialized.keys.p256dh,
      auth: serialized.keys.auth,
    },
  }
}

async function postGeneration(generation: string | null) {
  if (!('serviceWorker' in navigator)) return
  const registration = await navigator.serviceWorker.ready
  const worker = registration.active ?? registration.waiting ?? registration.installing
  if (!worker) throw new Error('SERVICE_WORKER_NOT_READY')
  const channel = new MessageChannel()
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error('SERVICE_WORKER_MESSAGE_TIMEOUT')), 1500)
    channel.port1.onmessage = () => {
      window.clearTimeout(timeout)
      channel.port1.close()
      resolve()
    }
    worker.postMessage({ type: 'DOSEY_PUSH_GENERATION', generation }, [channel.port2])
  })
}

export async function registerDoseyServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!window.isSecureContext || !('serviceWorker' in navigator)) return null
  return navigator.serviceWorker.register(SERVICE_WORKER_PATH, { scope: '/' })
}

// Observe existing browser state only; subscription reconciliation belongs to settings.
export async function inspectWebPushState(): Promise<WebPushState> {
  if (!supportsWebPush() || getWebPushLaunchContext() === 'ios-browser') return 'unsupported'
  const binding = readStoredBinding()
  if (Notification.permission !== 'granted') {
    if (binding) return 'revoked'
    return Notification.permission === 'denied' ? 'denied' : 'unrequested'
  }
  try {
    const registration = await navigator.serviceWorker.getRegistration('/')
    const subscription = await registration?.pushManager.getSubscription()
    if (binding && subscription) return 'granted'
    return binding ? 'revoked' : 'unrequested'
  } catch {
    return 'subscription_failed'
  }
}

export async function getWebPushState(): Promise<WebPushState> {
  if (!supportsWebPush()) return 'unsupported'

  const binding = readStoredBinding()
  if (Notification.permission === 'denied' || Notification.permission === 'default') {
    if (!binding) return Notification.permission === 'denied' ? 'denied' : 'unrequested'
    try {
      await disableWebPush()
      return 'revoked'
    } catch {
      return 'subscription_failed'
    }
  }

  try {
    const registration = await registerDoseyServiceWorker()
    const subscription = await registration?.pushManager.getSubscription()
    if (binding && subscription) return enableWebPush()
    if (binding) {
      try {
        await disableWebPush()
        return 'revoked'
      } catch {
        return 'subscription_failed'
      }
    }
    return 'unrequested'
  } catch {
    return 'subscription_failed'
  }
}

export async function enableWebPush(): Promise<WebPushState> {
  if (getWebPushLaunchContext() === 'ios-browser') return 'unsupported'
  if (!supportsWebPush()) return 'unsupported'
  if (Notification.permission === 'denied') return 'denied'

  try {
    const config = await getPushConfig()
    const permission = Notification.permission === 'granted'
      ? 'granted'
      : await Notification.requestPermission()
    if (permission !== 'granted') return 'denied'

    const registration = await registerDoseyServiceWorker()
    if (!registration) return 'unsupported'
    const existing = await registration.pushManager.getSubscription()
    const subscription = existing ?? await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeApplicationServerKey(config.data.public_key),
    })
    let activeSubscription = subscription
    let response
    try {
      response = await upsertPushSubscription(subscriptionRequest(activeSubscription))
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== 'PUSH_SUBSCRIPTION_CONFLICT') throw error
      localStorage.removeItem(BINDING_STORAGE_KEY)
      await postGeneration(null)
      await activeSubscription.unsubscribe()
      activeSubscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: decodeApplicationServerKey(config.data.public_key),
      })
      response = await upsertPushSubscription(subscriptionRequest(activeSubscription))
    }
    const binding = response.data
    localStorage.setItem(BINDING_STORAGE_KEY, JSON.stringify(binding))
    await postGeneration(binding.generation)
    return 'granted'
  } catch (error) {
    return isPushConfigUnavailable(error) ? 'config_unavailable' : 'subscription_failed'
  }
}

export async function disableWebPush(): Promise<void> {
  const binding = readStoredBinding()
  let serverBindingRemoved = binding === null
  try {
    if (binding) {
      await deletePushSubscription(binding.id)
      serverBindingRemoved = true
    }
  } finally {
    await clearBrowserPushBindingAsync(!serverBindingRemoved)
  }
}

async function clearBrowserPushBindingAsync(preserveStoredBinding = false): Promise<void> {
  if (!preserveStoredBinding) localStorage.removeItem(BINDING_STORAGE_KEY)
  if (!('serviceWorker' in navigator)) return
  const registration = await navigator.serviceWorker.ready
  await postGeneration(null)
  const subscription = await registration.pushManager.getSubscription()
  await subscription?.unsubscribe()
}

export function clearBrowserPushBinding(): void {
  localStorage.removeItem(BINDING_STORAGE_KEY)
  void clearBrowserPushBindingAsync().catch(() => undefined)
}

export function beginWebPushLogoutCleanup(): void {
  const binding = readStoredBinding()
  clearBrowserPushBinding()
  if (binding) {
    void deletePushSubscription(binding.id).catch(() => undefined)
  }
}

export function hasStoredWebPushBinding(): boolean {
  return readStoredBinding() !== null
}
