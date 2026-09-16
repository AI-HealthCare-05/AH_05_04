const DATABASE_NAME = 'dosey-web-push'
const STORE_NAME = 'binding'
const GENERATION_KEY = 'generation'
const GENERIC_TITLE = '복약 기록 알림'
const GENERIC_BODY = '앱에서 기록을 확인해 주세요.'

function openBindingDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function readGeneration() {
  const database = await openBindingDatabase()
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, 'readonly')
    const request = transaction.objectStore(STORE_NAME).get(GENERATION_KEY)
    request.onsuccess = () => resolve(request.result ?? null)
    request.onerror = () => reject(request.error)
    transaction.oncomplete = () => database.close()
  })
}

async function writeGeneration(generation) {
  const database = await openBindingDatabase()
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    const store = transaction.objectStore(STORE_NAME)
    if (generation) store.put(generation, GENERATION_KEY)
    else store.delete(GENERATION_KEY)
    transaction.oncomplete = () => {
      database.close()
      resolve()
    }
    transaction.onerror = () => reject(transaction.error)
  })
}

function parsePushPayload(event) {
  try {
    const payload = event.data?.json()
    if (
      typeof payload?.notification_id !== 'string' ||
      typeof payload?.generation !== 'string'
    ) {
      return null
    }
    return payload
  } catch {
    return null
  }
}

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('message', (event) => {
  if (event.data?.type !== 'DOSEY_PUSH_GENERATION') return
  event.waitUntil((async () => {
    await writeGeneration(event.data.generation ?? null)
    event.ports[0]?.postMessage('stored')
  })())
})

self.addEventListener('push', (event) => {
  const payload = parsePushPayload(event)
  if (!payload) return

  event.waitUntil((async () => {
    const currentGeneration = await readGeneration()
    if (!currentGeneration || currentGeneration !== payload.generation) return

    await self.registration.showNotification(GENERIC_TITLE, {
      body: GENERIC_BODY,
      icon: '/icons/dosey-192.png',
      badge: '/icons/dosey-192.png',
      tag: `dosey-push-${payload.notification_id}`,
      renotify: false,
      data: {
        notificationId: payload.notification_id,
        generation: payload.generation,
      },
    })
  })())
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const notificationId = event.notification.data?.notificationId
  const generation = event.notification.data?.generation
  if (typeof notificationId !== 'string' || typeof generation !== 'string') return

  event.waitUntil((async () => {
    const currentGeneration = await readGeneration()
    if (!currentGeneration || currentGeneration !== generation) return

    const target = new URL('/notifications', self.location.origin)
    target.searchParams.set('push_notification_id', notificationId)

    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    for (const client of windows) {
      if (new URL(client.url).origin !== self.location.origin) continue
      await client.navigate(target.href)
      await client.focus()
      return
    }
    await self.clients.openWindow(target.href)
  })())
})
