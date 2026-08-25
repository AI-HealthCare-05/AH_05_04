import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { getGuide, type GuideData } from '../api/guides'
import {
  Button,
  Card,
  MobileShell,
  StatusBadge,
} from '../design-system/components'
import '../design-system/prototype.css'
import './GuidePage.css'

function formatCompletedAt(value: string | null) {
  if (!value) return null

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null

  return new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function GuidePage() {
  const navigate = useNavigate()
  const { guideId } = useParams<{ guideId: string }>()
  const [guide, setGuide] = useState<GuideData | null>(null)
  const [message, setMessage] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [stateGuideId, setStateGuideId] = useState<string | null>(null)
  const guideRequestIdRef = useRef(0)

  const loadGuide = useCallback(async () => {
    const requestedGuideId = guideId ?? null
    const requestId = ++guideRequestIdRef.current
    const isCurrentRequest = () => guideRequestIdRef.current === requestId

    setStateGuideId(requestedGuideId)

    if (!requestedGuideId) {
      setGuide(null)
      setMessage('')
      setIsLoading(false)
      return
    }

    try {
      setIsLoading(true)
      setMessage('')
      setGuide(null)
      const response = await getGuide(requestedGuideId)
      if (!isCurrentRequest()) return

      if (response.data.guide_id !== requestedGuideId) {
        setMessage('요청한 가이드와 다른 응답을 받았어요. 다시 불러와 주세요.')
        return
      }

      setGuide(response.data)
    } catch (error) {
      if (!isCurrentRequest()) return
      setGuide(null)
      setMessage(
        error instanceof ApiError
          ? error.message
          : '복약 가이드를 불러오는 중 오류가 발생했습니다.',
      )
    } finally {
      if (isCurrentRequest()) {
        setIsLoading(false)
      }
    }
  }, [guideId])

  useEffect(() => {
    void loadGuide()
    return () => {
      guideRequestIdRef.current += 1
    }
  }, [loadGuide])

  const routeGuideId = guideId ?? null
  const isCurrentGuideState = stateGuideId === routeGuideId
  const currentGuide = isCurrentGuideState ? guide : null
  const currentMessage = isCurrentGuideState ? message : ''
  const currentIsLoading = isCurrentGuideState ? isLoading : Boolean(guideId)

  const completedAt = formatCompletedAt(currentGuide?.completed_at ?? null)
  const hasCompletedContent =
    currentGuide?.generation_status === 'COMPLETED' &&
    Boolean(currentGuide.content?.trim())

  return (
    <div className="guide-page">
      <MobileShell
        title="Dosey 도지"
        activeNavigation="가이드"
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
        }}
      >
        <main className="app-scroll guide-page__content">
          <h1 className="screen-title">복약 가이드</h1>
          <p className="screen-description">
            약마다 언제·어떻게 복용하는지 먼저 보여드려요.
          </p>

          {!currentIsLoading && !guideId && (
            <Card className="guide-page__empty">
              <span className="guide-page__spark" aria-hidden="true" />
              <h2>표시할 가이드 정보가 없어요</h2>
              <p>처방을 확정하고 가이드를 생성하면 해당 화면으로 이동해요.</p>
              <Button fullWidth onClick={() => navigate('/prescriptions/upload')}>
                처방전 등록하기
              </Button>
            </Card>
          )}

          {currentIsLoading && (
            <Card className="guide-page__state">
              <div aria-live="polite">
                <strong>복약 가이드를 불러오고 있어요</strong>
                <p>잠시만 기다려 주세요.</p>
              </div>
            </Card>
          )}

          {!currentIsLoading && currentMessage && (
            <Card className="guide-page__state">
              <StatusBadge tone="attention">불러오기 실패</StatusBadge>
              <h2>가이드를 표시할 수 없어요</h2>
              <p role="alert">{currentMessage}</p>
              <Button fullWidth onClick={() => void loadGuide()}>
                다시 불러오기
              </Button>
            </Card>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            currentGuide &&
            hasCompletedContent && (
            <>
              <Card className="guide-page__hero">
                <span className="guide-page__hero-label">확정된 처방 기준</span>
                <h2>복약 가이드가 준비됐어요</h2>
                <p>
                  직접 확인한 처방을 기준으로 생성된 안내를 표시해요.
                </p>
                {completedAt && <small>{completedAt} 생성</small>}
                <button className="guide-page__schedule-button" type="button" disabled>
                  복용 일정 준비 중
                </button>
              </Card>

              <Card className="record-card guide-page__guide-card">
                <span className="guide-page__guide-label">확인된 처방 기준</span>
                <h2>확인된 복약 안내</h2>
                <p className="guide-page__guide-intro">
                  실제 생성된 가이드 원문을 확인해 주세요.
                </p>
                <details className="guide-page__disclosure">
                  <summary>가이드 전체 내용</summary>
                  <div className="guide-page__guide-text">
                    {currentGuide.content}
                  </div>
                </details>
              </Card>

              <div className="notice attention guide-page__notice">
                이 화면은 생성된 복약 가이드 내용을 표시해요.
              </div>

              <Button
                fullWidth
                className="guide-page__chat-button"
                onClick={() =>
                  navigate(
                    `/chat?prescription_id=${currentGuide.prescription_id}`,
                  )
                }
              >
                복약 챗봇에 질문하기
              </Button>
            </>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            currentGuide &&
            !hasCompletedContent && (
            <Card className="guide-page__state">
              <StatusBadge tone="attention">
                {currentGuide.generation_status === 'FAILED'
                  ? '생성 실패'
                  : currentGuide.generation_status === 'COMPLETED'
                    ? '내용 없음'
                    : '생성 중'}
              </StatusBadge>
              <h2>
                {currentGuide.generation_status === 'FAILED'
                  ? '가이드를 표시할 수 없어요'
                  : currentGuide.generation_status === 'COMPLETED'
                    ? '가이드 내용이 아직 없어요'
                    : '복약 가이드를 만들고 있어요'}
              </h2>
              <p>
                {currentGuide.generation_status === 'FAILED'
                  ? '처방 검수 화면에서 가이드 생성을 다시 시도해 주세요.'
                  : currentGuide.generation_status === 'COMPLETED'
                    ? '생성된 내용을 확인할 수 없어 다시 불러와야 해요.'
                    : '잠시 후 다시 불러와 주세요.'}
              </p>
              <Button fullWidth variant="secondary" onClick={() => void loadGuide()}>
                다시 불러오기
              </Button>
            </Card>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default GuidePage
