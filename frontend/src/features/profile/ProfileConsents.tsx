import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import {
  CONSENT_PURPOSES,
  getUserConsents,
  grantUserConsent,
  withdrawUserConsent,
  type ConsentPurpose,
  type UserConsent,
} from '../../api/userConsents'
import { Button, Card } from '../../design-system/components'

const LABELS: Record<ConsentPurpose, string> = {
  OCR: '처방전 외부 처리',
  GUIDE: '복약 가이드',
  CHAT: '복약 챗봇',
  NOTIFICATION: '알림',
}

function statusLabel(consent: UserConsent) {
  if (consent.is_granted) return '현재 동의한 상태입니다.'
  if (consent.status === 'WITHDRAWN') return '철회한 상태입니다.'
  if (consent.status === null) return '동의한 내역이 없습니다.'
  return '현재 유효한 동의가 없습니다.'
}

export default function ProfileConsents({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [consents, setConsents] = useState<UserConsent[]>([])
  const [loading, setLoading] = useState(true)
  const [withdrawing, setWithdrawing] = useState<ConsentPurpose | null>(null)
  const [granting, setGranting] = useState<ConsentPurpose | null>(null)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const busy = useRef(false)

  const load = useCallback(async (signal?: AbortSignal) => {
    if (busy.current) return
    busy.current = true
    setLoading(true)
    setError('')
    setFeedback('')
    try {
      const response = await getUserConsents(signal)
      if (!signal?.aborted) setConsents(response.data)
    } catch (cause) {
      if (signal?.aborted) return
      setConsents([])
      if (cause instanceof ApiError && cause.status === 401) {
        onSessionExpired()
        return
      }
      setError('동의 상태를 확인할 수 없어요. 잠시 후 다시 시도해 주세요.')
    } finally {
      if (!signal?.aborted) {
        busy.current = false
        setLoading(false)
      }
    }
  }, [onSessionExpired])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => {
      controller.abort()
      busy.current = false
    }
  }, [load])
  async function grant(consent: UserConsent) {
    if (busy.current || consent.is_granted) return

    const version = consent.current_policy_version.trim()
    if (!version) return

    busy.current = true
    setGranting(consent.purpose)
    setError('')
    setFeedback('')

    try {
      const response = await grantUserConsent(consent.purpose, version)

      setConsents((previous) =>
        previous.map((item) =>
          item.purpose === consent.purpose ? response.data : item,
        ),
      )

      setFeedback(`${LABELS[consent.purpose]} 동의를 저장했습니다.`)
    } catch (cause) {
      setConsents((previous) =>
        previous.filter((item) => item.purpose !== consent.purpose),
      )

      if (cause instanceof ApiError && cause.status === 401) {
        onSessionExpired()
        return
      }

      const mismatch =
        cause instanceof ApiError &&
        cause.status === 422 &&
        cause.details.some(
          (detail) =>
            detail.field === 'policy_version' &&
            detail.reason === 'POLICY_VERSION_MISMATCH',
        )

      setError(
        mismatch
          ? '동의 정책이 변경되었습니다. 동의 상태를 다시 확인한 뒤 다시 동의해 주세요.'
          : '동의 저장 결과를 확인할 수 없어요. 현재 상태를 다시 확인해 주세요.',
      )
    } finally {
      busy.current = false
      setGranting(null)
    }
  }

  async function withdraw(consent: UserConsent) {
    if (busy.current || consent.status !== 'GRANTED') return
    // OCR 정책 미설정 시에도 기존 동의는 저장된 버전으로 철회할 수 있습니다.
    const version = consent.current_policy_version.trim()
      ? consent.current_policy_version
      : consent.purpose === 'OCR' ? consent.policy_version : null
    if (!version) return
    busy.current = true
    setWithdrawing(consent.purpose)
    setError('')
    setFeedback('')
    try {
      const response = await withdrawUserConsent(consent.purpose, version)
      setConsents((previous) => previous.map((item) => (
        item.purpose === consent.purpose ? response.data : item
      )))
      setFeedback(`${LABELS[consent.purpose]} 동의 철회를 저장했습니다.`)
    } catch (cause) {
      setConsents((previous) => previous.filter((item) => item.purpose !== consent.purpose))
      if (cause instanceof ApiError && cause.status === 401) {
        onSessionExpired()
        return
      }
      const mismatch = cause instanceof ApiError && cause.status === 422 &&
        cause.details.some((detail) => detail.field === 'policy_version' && detail.reason === 'POLICY_VERSION_MISMATCH')
      setError(mismatch
        ? '동의 정책이 변경되었습니다. 동의 상태를 다시 확인한 뒤 철회해 주세요.'
        : '동의 철회 결과를 확인할 수 없어요. 현재 상태를 다시 확인해 주세요.')
    } finally {
      busy.current = false
      setWithdrawing(null)
    }
  }

  return (
    <section className="mvp-profile__section" aria-labelledby="purpose-consents-title">
      <h3 id="purpose-consents-title">목적별 동의 관리</h3>
      <p className="mvp-profile__readonly-note">목적별 동의 상태를 확인하고 개별 동의를 철회할 수 있어요. 동의 철회는 기존 데이터 삭제나 회원탈퇴와는 별개입니다.</p>
      {loading && <p role="status">동의 상태를 확인하는 중입니다.</p>}
      {feedback && <p role="status">{feedback}</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && CONSENT_PURPOSES.map((purpose) => {
        const consent = consents.find((item) => item.purpose === purpose)

        const canWithdraw = consent?.status === 'GRANTED' && (
          Boolean(consent.current_policy_version.trim()) ||
          (purpose === 'OCR' && Boolean(consent.policy_version))
        )
        const canGrant =
          consent != null &&
          !consent.is_granted &&
          Boolean(consent.current_policy_version.trim())

        return (
          <Card key={purpose} className="mvp-profile__card mvp-profile__consent-card">
            <h4>{LABELS[purpose]} 동의</h4>
            <p>{consent ? statusLabel(consent) : '동의 상태를 확인할 수 없습니다.'}</p>
            {consent && !consent.current_policy_version.trim() && <p>현재 동의 정책을 확인할 수 없습니다.</p>}
            {canGrant && (
              <Button
                fullWidth
                disabled={withdrawing !== null || granting !== null}
                aria-busy={granting === purpose}
                onClick={() => void grant(consent)}
              >
                {granting === purpose
                ? `${LABELS[purpose]} 동의 중...`
                 : `${LABELS[purpose]} 동의하기`}
              </Button>
            )}
            {canWithdraw && <Button
              fullWidth
              variant="secondary"
              disabled={withdrawing !== null}
              aria-busy={withdrawing === purpose}
              onClick={() => void withdraw(consent!)}
            >{withdrawing === purpose ? `${LABELS[purpose]} 동의 철회 중...` : `${LABELS[purpose]} 동의 철회`}</Button>}
          </Card>
        )
      })}
      {!loading && <Button fullWidth variant="secondary" disabled={withdrawing !== null || granting !== null} onClick={() => void load()}>
        동의 상태 다시 확인
      </Button>}
    </section>
  )
}
