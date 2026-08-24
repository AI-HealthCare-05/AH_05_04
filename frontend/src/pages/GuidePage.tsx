import { useCallback, useEffect, useState } from 'react'
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

  const loadGuide = useCallback(async () => {
    if (!guideId) {
      setMessage('가이드를 불러오는 데 필요한 정보가 없습니다.')
      setIsLoading(false)
      return
    }

    try {
      setIsLoading(true)
      setMessage('')
      const response = await getGuide(guideId)
      setGuide(response.data)
    } catch (error) {
      setGuide(null)
      setMessage(
        error instanceof ApiError
          ? error.message
          : '복약 가이드를 불러오는 중 오류가 발생했습니다.',
      )
    } finally {
      setIsLoading(false)
    }
  }, [guideId])

  useEffect(() => {
    void loadGuide()
  }, [loadGuide])

  const completedAt = formatCompletedAt(guide?.completed_at ?? null)
  const hasCompletedContent =
    guide?.generation_status === 'COMPLETED' && Boolean(guide.content?.trim())

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
          <h1 className="screen-title">내 복약 가이드</h1>
          <p className="screen-description">
            직접 확인하고 확정한 처방전 결과를 기준으로 만들었어요.
          </p>

          {isLoading && (
            <Card className="guide-page__state">
              <div aria-live="polite">
                <strong>복약 가이드를 불러오고 있어요</strong>
                <p>잠시만 기다려 주세요.</p>
              </div>
            </Card>
          )}

          {!isLoading && message && (
            <Card className="guide-page__state">
              <StatusBadge tone="attention">불러오기 실패</StatusBadge>
              <h2>가이드를 표시할 수 없어요</h2>
              <p role="alert">{message}</p>
              <Button fullWidth onClick={() => void loadGuide()}>
                다시 불러오기
              </Button>
            </Card>
          )}

          {!isLoading && !message && guide && hasCompletedContent && (
            <>
              <Card className="home-hero guide-page__hero">
                <StatusBadge>생성 완료</StatusBadge>
                <h2>복약 가이드가 준비됐어요</h2>
                <p>
                  처방전에서 직접 확인한 정보와 안전 안내를 함께 확인해
                  주세요.
                </p>
                {completedAt && <small>{completedAt} 생성</small>}
              </Card>

              <Card className="record-card guide-page__guide-card">
                <h2>복약 안내</h2>
                <div className="guide-page__guide-text">{guide.content}</div>
              </Card>

              <Button
                fullWidth
                className="guide-page__chat-button"
                onClick={() =>
                  navigate(`/chat?prescription_id=${guide.prescription_id}`)
                }
              >
                복약 챗봇에 질문하기
              </Button>
            </>
          )}

          {!isLoading && !message && guide && !hasCompletedContent && (
            <Card className="guide-page__state">
              <StatusBadge tone="attention">
                {guide.generation_status === 'FAILED'
                  ? '생성 실패'
                  : '생성 중'}
              </StatusBadge>
              <h2>
                {guide.generation_status === 'FAILED'
                  ? '가이드를 표시할 수 없어요'
                  : '가이드 내용을 준비하고 있어요'}
              </h2>
              <p>
                {guide.generation_status === 'FAILED'
                  ? '처방 검수 화면에서 가이드 생성을 다시 시도해 주세요.'
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
