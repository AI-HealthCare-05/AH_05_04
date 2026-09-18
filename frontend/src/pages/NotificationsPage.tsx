import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  createNotificationOccurrenceHandoff,
  createNotificationOccurrenceRoute,
  createNotificationReadIdempotencyKey,
  listNotifications,
  markNotificationRead,
  type NotificationData,
  type NotificationOccurrenceHandoff,
} from '../api/notifications'
import {
  isOccurrenceMedicationUnavailableError,
  resolveNotificationOccurrenceMedication,
} from '../api/medicationOccurrences'
import { MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import '../design-system/prototype.css'
import './MvpPages.css'
import './NotificationsPage.css'

export type NotificationsPageProps = {
  onHandoffReady?: (handoff: NotificationOccurrenceHandoff) => void
}

type ReadFailure = {
  notification: NotificationData
  idempotencyKey: string
  message: string
  requiresLogin: boolean
}

type LoadFailure = {
  message: string
  requiresLogin: boolean
}

type HandoffFailure = {
  notification: NotificationData
  message: string
  requiresLogin: boolean
}

type HandoffRequest = {
  token: number
  controller: AbortController
}

const notificationDay = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function getFailureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return '로그인 정보를 다시 확인한 뒤 시도해 주세요.'
    }
    if (error.status === 404) {
      return '알림 정보를 확인할 수 없어요.'
    }
    if (error.status >= 500) {
      return '알림 서비스에 잠시 연결할 수 없어요. 잠시 후 다시 시도해 주세요.'
    }
  }

  return '네트워크 연결을 확인한 뒤 다시 시도해 주세요.'
}

function isAuthenticationError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}

function getNotificationTitle(kind: NotificationData['kind']): string {
  return kind === 'REMINDER' ? '복약 재알림' : '복약 알림'
}

function getHandoffFailureMessage(error: unknown): string {
  if (
    isOccurrenceMedicationUnavailableError(error) ||
    (error instanceof ApiError && error.status === 404)
  ) {
    return '복약 기록을 확인할 수 없어요.'
  }
  if (error instanceof ApiError && error.status === 401) {
    return '로그인 정보를 다시 확인한 뒤 시도해 주세요.'
  }
  if (error instanceof ApiError && error.status >= 500) {
    return '복약 기록을 잠시 불러오지 못했어요. 잠시 후 다시 시도해 주세요.'
  }
  return '네트워크 연결을 확인한 뒤 다시 시도해 주세요.'
}

function NotificationsPage({ onHandoffReady }: NotificationsPageProps) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const pushNotificationId = searchParams.get('push_notification_id')
  const [notifications, setNotifications] = useState<NotificationData[] | null>(null)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [loadError, setLoadError] = useState<LoadFailure | null>(null)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)
  const [selectingId, setSelectingId] = useState<string | null>(null)
  const [readFailure, setReadFailure] = useState<ReadFailure | null>(null)
  const [handoffFailure, setHandoffFailure] = useState<HandoffFailure | null>(null)
  const isMountedRef = useRef(true)
  const nextHandoffTokenRef = useRef(0)
  const handoffRequestRef = useRef<HandoffRequest | null>(null)
  const handledPushNotificationRef = useRef<string | null>(null)
  const [pushHandoffMessage, setPushHandoffMessage] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoadError(null)
    setLoadMoreError(null)
    setNotifications(null)
    setNextOffset(null)

    try {
      const response = await listNotifications()
      setNotifications(response.data.items)
      setNextOffset(response.data.next_offset)
    } catch (error) {
      setLoadError({
        message: getFailureMessage(error),
        requiresLogin: isAuthenticationError(error),
      })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      handoffRequestRef.current?.controller.abort()
      handoffRequestRef.current = null
    }
  }, [])

  const startHandoffRequest = useCallback((): HandoffRequest => {
    handoffRequestRef.current?.controller.abort()
    const request = {
      token: nextHandoffTokenRef.current + 1,
      controller: new AbortController(),
    }
    nextHandoffTokenRef.current = request.token
    handoffRequestRef.current = request
    return request
  }, [])

  const isHandoffRequestActive = useCallback((request: HandoffRequest): boolean =>
    isMountedRef.current &&
    handoffRequestRef.current?.token === request.token &&
    !request.controller.signal.aborted, [])

  const prepareHandoff = useCallback(async (
    notification: NotificationData,
    request: HandoffRequest,
  ) => {
    const nextHandoff = createNotificationOccurrenceHandoff(notification)
    try {
      await resolveNotificationOccurrenceMedication(
        nextHandoff,
        request.controller.signal,
      )
      if (!isHandoffRequestActive(request)) return
      onHandoffReady?.(nextHandoff)
      if (!isHandoffRequestActive(request)) return
      navigate(createNotificationOccurrenceRoute(nextHandoff))
    } catch (error) {
      if (!isHandoffRequestActive(request)) return
      setHandoffFailure({
        notification,
        message: getHandoffFailureMessage(error),
        requiresLogin: isAuthenticationError(error),
      })
    }
  }, [isHandoffRequestActive, navigate, onHandoffReady])

  useEffect(() => {
    if (!pushNotificationId || notifications === null || loadError !== null) return
    if (handledPushNotificationRef.current === pushNotificationId) return
    handledPushNotificationRef.current = pushNotificationId

    let notification = notifications.find((item) => item.id === pushNotificationId)
    setSearchParams({}, { replace: true })
    setPushHandoffMessage('알림의 최신 복약 기록을 확인하고 있어요.')
    const request = startHandoffRequest()
    setSelectingId(pushNotificationId)
    void (async () => {
      let offset = nextOffset
      let pages = 0
      while (!notification && offset !== null && pages < 10) {
        const response = await listNotifications({ offset, signal: request.controller.signal })
        if (!isHandoffRequestActive(request)) return
        const items = response.data.items
        setNotifications((current) => {
          const existing = current ?? []
          const ids = new Set(existing.map((item) => item.id))
          return [...existing, ...items.filter((item) => !ids.has(item.id))]
        })
        notification = items.find((item) => item.id === pushNotificationId)
        offset = response.data.next_offset
        setNextOffset(offset)
        pages += 1
      }

      if (!notification) {
        setPushHandoffMessage('이 알림의 최신 기록을 찾을 수 없어 알림 목록을 표시해요.')
        return
      }
      await prepareHandoff(notification, request)
    })().catch((error: unknown) => {
      if (!isHandoffRequestActive(request)) return
      setLoadError({
        message: getFailureMessage(error),
        requiresLogin: isAuthenticationError(error),
      })
    }).finally(() => {
      if (isHandoffRequestActive(request)) setSelectingId(null)
    })
  }, [
    isHandoffRequestActive,
    loadError,
    notifications,
    nextOffset,
    prepareHandoff,
    pushNotificationId,
    setSearchParams,
    startHandoffRequest,
  ])

  const handleLoginRecovery = () => {
    const notificationId = pushNotificationId ?? handledPushNotificationRef.current
    clearAuthenticatedSession()
    navigate('/login', {
      state: notificationId
        ? { returnTo: `/notifications?push_notification_id=${encodeURIComponent(notificationId)}` }
        : null,
    })
  }

  const loadMore = async () => {
    if (nextOffset === null || isLoadingMore) return

    setIsLoadingMore(true)
    setLoadMoreError(null)
    try {
      const response = await listNotifications({ offset: nextOffset })
      setNotifications((current) => {
        const existing = current ?? []
        const existingIds = new Set(existing.map((item) => item.id))
        return [
          ...existing,
          ...response.data.items.filter((item) => !existingIds.has(item.id)),
        ]
      })
      setNextOffset(response.data.next_offset)
    } catch (error) {
      if (isAuthenticationError(error)) {
        setNotifications(null)
        setNextOffset(null)
        setLoadError({
          message: getFailureMessage(error),
          requiresLogin: true,
        })
        return
      }
      setLoadMoreError(getFailureMessage(error))
    } finally {
      setIsLoadingMore(false)
    }
  }

  const handleSelect = async (
    notification: NotificationData,
    idempotencyKey = createNotificationReadIdempotencyKey(),
  ) => {
    const request = startHandoffRequest()

    setReadFailure(null)
    setHandoffFailure(null)

    setSelectingId(notification.id)

    try {
      let selectedNotification = notification
      if (notification.read_at === null) {
        const response = await markNotificationRead(
          notification.id,
          idempotencyKey,
          request.controller.signal,
        )
        if (!isHandoffRequestActive(request)) return
        selectedNotification = {
          ...notification,
          read_at: response.data.read_at,
        }
        setNotifications((current) =>
          current?.map((item) =>
            item.id === notification.id ? selectedNotification : item,
          ) ?? current,
        )
      }
      if (!isHandoffRequestActive(request)) return
      await prepareHandoff(selectedNotification, request)
    } catch (error) {
      if (!isHandoffRequestActive(request)) return
      setReadFailure({
        notification,
        idempotencyKey,
        message: getFailureMessage(error),
        requiresLogin: isAuthenticationError(error),
      })
    } finally {
      if (isHandoffRequestActive(request)) {
        setSelectingId(null)
      }
    }
  }

  // Group by delivery day, independently of read state and the original dose date.
  const today = notificationDay.format(new Date())
  const notificationGroups = [
    { id: 'today', title: '오늘', items: notifications?.filter((item) => notificationDay.format(new Date(item.delivered_at)) === today) ?? [] },
    { id: 'previous', title: '이전 알림', items: notifications?.filter((item) => notificationDay.format(new Date(item.delivered_at)) !== today) ?? [] },
  ]

  return (
    <div className="mvp-page mvp-notifications-page">
      <MobileShell
        title="Dosey 도지"
        onBack={() => navigate('/')}
        hideNavigation
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main className="app-scroll mvp-page__content mvp-notifications">
          <div className="mvp-notifications__intro">
            <h2>알림</h2>
          </div>

          {pushHandoffMessage && (
            <p className="mvp-notifications__push-status" role="status" aria-live="polite">
              {pushHandoffMessage}
            </p>
          )}

          {notifications === null && loadError === null && (
            <section className="mvp-notifications__state" role="status" aria-live="polite">
              <h3>알림을 불러오는 중이에요</h3>
              <p>잠시만 기다려 주세요.</p>
            </section>
          )}

          {loadError !== null && (
            <section className="mvp-notifications__state mvp-notifications__state--error" role="alert">
              <h3>알림을 불러오지 못했어요</h3>
              <p>{loadError.message}</p>
              <button
                type="button"
                onClick={loadError.requiresLogin ? handleLoginRecovery : () => void load()}
              >
                {loadError.requiresLogin ? '다시 로그인' : '다시 시도'}
              </button>
            </section>
          )}

          {notifications?.length === 0 && (
            <section className="mvp-notifications__state" role="status">
              <h3>새로운 알림이 없어요</h3>
              <p>복약 알림이 도착하면 이곳에서 확인할 수 있어요.</p>
            </section>
          )}

          {notificationGroups.filter((group) => group.items.length > 0).map((group) => (
            <section className="mvp-notifications__group" key={group.id} aria-labelledby={`notifications-${group.id}`}>
              <h3 id={`notifications-${group.id}`}>{group.title}</h3>
              <ul className="mvp-notifications__list" aria-label={`${group.title} 복약 알림`}>
                {group.items.map((notification) => {
                  const isRead = notification.read_at !== null
                  const isSelecting = selectingId === notification.id

                  return (
                    <li key={notification.id}>
                      <button
                        className={`mvp-notifications__item-button ${isRead ? 'is-read' : 'is-unread'}`}
                        type="button"
                        disabled={isSelecting}
                        aria-busy={isSelecting}
                        aria-label={`${getNotificationTitle(notification.kind)}, 복약일 ${notification.occurrence_local_date}, ${isSelecting ? '복약 기록 확인 중' : isRead ? '읽음' : '읽지 않음'}`}
                        onClick={() => void handleSelect(notification)}
                      >
                        <span className={`mvp-notifications__read-state ${isRead ? 'is-read' : ''}`}>
                          <span className="mvp-notifications__read-mark" aria-hidden="true">{isRead ? '✓' : '●'}</span>
                          {isRead ? '읽음' : '새 알림'}
                        </span>
                        <span className="mvp-notifications__item-copy">
                          <strong>{isSelecting ? '복약 기록 확인 중...' : getNotificationTitle(notification.kind)}</strong>
                          <small>복약일 {notification.occurrence_local_date}</small>
                        </span>
                        {!isRead && !isSelecting && (
                          <span className="mvp-notifications__item-action" aria-hidden="true">
                            복용 여부 기록하기
                          </span>
                        )}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>
          ))}

          {notifications && notifications.length > 0 && nextOffset !== null && (
            <button
              className="mvp-notifications__load-more"
              type="button"
              disabled={isLoadingMore}
              aria-busy={isLoadingMore}
              onClick={() => void loadMore()}
            >
              {isLoadingMore ? '알림 더 불러오는 중...' : '알림 더 보기'}
            </button>
          )}

          {loadMoreError !== null && (
            <section className="mvp-notifications__state mvp-notifications__state--error" role="alert">
              <h3>알림을 더 불러오지 못했어요</h3>
              <p>{loadMoreError}</p>
              <button type="button" onClick={() => void loadMore()}>다시 시도</button>
            </section>
          )}

          {readFailure !== null && (
            <section className="mvp-notifications__state mvp-notifications__state--error" role="alert">
              <h3>알림을 읽음 처리하지 못했어요</h3>
              <p>{readFailure.message}</p>
              {readFailure.requiresLogin ? (
                <button type="button" onClick={handleLoginRecovery}>다시 로그인</button>
              ) : (
                <button
                  type="button"
                  onClick={() =>
                    void handleSelect(
                      readFailure.notification,
                      readFailure.idempotencyKey,
                    )
                  }
                >
                  읽음 처리 다시 시도
                </button>
              )}
            </section>
          )}

          {handoffFailure !== null && (
            <section className="mvp-notifications__handoff mvp-notifications__state--error" role="alert">
              <h3>복약 기록을 열지 못했어요</h3>
              <p>{handoffFailure.message}</p>
              <button
                type="button"
                onClick={handoffFailure.requiresLogin
                  ? handleLoginRecovery
                  : () => void handleSelect(handoffFailure.notification)}
              >
                {handoffFailure.requiresLogin ? '다시 로그인' : '복약 기록 다시 확인'}
              </button>
            </section>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default NotificationsPage
