import { apiRequest } from './client'

export type PushConfigResponse = {
  data: {
    public_key: string
  }
}

export type PushSubscriptionRequest = {
  endpoint: string
  keys: {
    p256dh: string
    auth: string
  }
}

export type PushSubscriptionResponse = {
  data: {
    id: string
    generation: string
  }
}

export function getPushConfig() {
  return apiRequest<PushConfigResponse>('/api/v1/push/config')
}

export function upsertPushSubscription(data: PushSubscriptionRequest) {
  return apiRequest<PushSubscriptionResponse>('/api/v1/push/subscriptions', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function deletePushSubscription(subscriptionId: string) {
  return apiRequest<void>(
    `/api/v1/push/subscriptions/${encodeURIComponent(subscriptionId)}`,
    { method: 'DELETE' },
  )
}
