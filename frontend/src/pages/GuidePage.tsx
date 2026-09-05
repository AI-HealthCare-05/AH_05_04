import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { getGuide, type GuideData } from '../api/guides'
import {
  Button,
  Card,
  MobileShell,
} from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
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

function getGuideLoadFailureMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return '로그인 정보를 다시 확인한 뒤 시도해 주세요.'
    if (error.status === 404) return '요청한 복약 가이드를 찾을 수 없어요.'
    if (error.status >= 500) return '서버 응답이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'
  }

  if (error instanceof TypeError) {
    return '네트워크 연결을 확인한 뒤 다시 시도해 주세요.'
  }

  return '복약 가이드를 불러오지 못했어요. 다시 시도해 주세요.'
}

type GuideDetail = {
  label: string
  value: string
}

type StructuredMedication = {
  name: string
  details: GuideDetail[]
  guidance: string
  notices: string[]
}

type StructuredGuide = {
  medications: StructuredMedication[]
  generalNotice: string
  safetyNotice: string
}

const GUIDE_DETAIL_LABELS: Record<string, string> = {
  '용량': '1회량',
  '복용 횟수': '하루 횟수',
  '복용 시점': '복용 시점',
  '복용 기간': '복용 기간',
}

const INCOMPLETE_DOSE_NOTICE =
  '용량 정보는 처방전 또는 의료진 안내를 확인해 주세요.'

function parseGuideContent(content: string): StructuredGuide | null {
  const blocks = content.replace(/\r\n?/g, '\n').trim().split(/\n{2}/)
  if (blocks.length < 3 || blocks[0].trim() !== '복약 가이드') return null

  const noticeLines = blocks.at(-1)?.split('\n') ?? []
  if (noticeLines.length !== 2) return null

  const generalNotice = noticeLines[0].match(/^공통 안내:\s*(.+)$/)?.[1]
  const safetyNotice = noticeLines[1].match(/^안전 안내:\s*(.+)$/)?.[1]
  if (!generalNotice || !safetyNotice) return null

  const medications: StructuredMedication[] = []

  for (const [medicationIndex, block] of blocks.slice(1, -1).entries()) {
    const lines = block.split('\n')
    const heading = lines.shift()?.match(/^\[(\d+)]\s+(.+)$/)
    if (!heading || Number(heading[1]) !== medicationIndex + 1) return null

    const details: GuideDetail[] = []
    const notices: string[] = []
    let guidance: string | null = null
    const seenLabels = new Set<string>()

    for (const line of lines) {
      if (line === INCOMPLETE_DOSE_NOTICE) {
        notices.push(line)
        continue
      }

      const field = line.match(/^([^:]+):\s*(.+)$/)
      if (!field) return null

      const [, sourceLabel, value] = field
      if (sourceLabel === '복약 안내') {
        if (guidance !== null) return null
        guidance = value
        continue
      }

      const displayLabel = GUIDE_DETAIL_LABELS[sourceLabel]
      if (!displayLabel || seenLabels.has(sourceLabel)) return null
      seenLabels.add(sourceLabel)
      details.push({ label: displayLabel, value })
    }

    if (!heading[2].trim() || !guidance) return null
    medications.push({
      name: heading[2],
      details,
      guidance,
      notices,
    })
  }

  if (medications.length === 0) return null
  return { medications, generalNotice, safetyNotice }
}

function StructuredGuideContent({ guide }: { guide: StructuredGuide }) {
  return (
    <section
      className="guide-page__structured-guide"
      aria-labelledby="guide-medications-heading"
    >
      <h2 id="guide-medications-heading">
        확인된 약 목록 · {guide.medications.length}개
      </h2>
      <div className="guide-page__medication-list">
        {guide.medications.map((medication, index) => (
          <details className="guide-page__medication-card" key={`${index}-${medication.name}`}>
            <summary>
              <span>
                <h3>{medication.name}</h3>
                <small>
                  {medication.details
                    .filter((detail) => detail.label === '하루 횟수' || detail.label === '복용 시점')
                    .map((detail) => detail.value)
                    .join(' · ') || '복용 정보를 확인해 주세요'}
                </small>
              </span>
              <span className="guide-page__chevron" aria-hidden="true" />
            </summary>
            <div className="guide-page__medication-body">
              {medication.details.length > 0 && (
                <dl className="guide-page__medication-details">
                  {medication.details.map((detail) => (
                    <div key={detail.label}>
                      <dt>{detail.label}</dt>
                      <dd>{detail.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {medication.notices.map((notice) => (
                <p className="guide-page__medication-notice" key={notice}>
                  {notice}
                </p>
              ))}
              <section className="guide-page__guidance" aria-labelledby={`guide-guidance-${index}`}>
                <h3 id={`guide-guidance-${index}`}>복약 안내</h3>
                <p>{medication.guidance}</p>
              </section>
            </div>
          </details>
        ))}
      </div>

      <aside className="guide-page__common-notice" aria-labelledby="guide-common-heading">
        <h3 id="guide-common-heading">공통 안내</h3>
        <p>{guide.generalNotice}</p>
      </aside>
      <aside className="guide-page__safety-notice" aria-labelledby="guide-safety-heading">
        <h3 id="guide-safety-heading">안전 안내</h3>
        <p>{guide.safetyNotice}</p>
      </aside>
    </section>
  )
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
      setMessage(getGuideLoadFailureMessage(error))
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
  const structuredGuide = currentGuide?.content
    ? parseGuideContent(currentGuide.content)
    : null
  return (
    <div className="guide-page">
      <MobileShell
        title="Dosey 도지"
        activeNavigation="가이드"
        onBack={() => navigate('/')}
        disabledNavigation={['일정']}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '가이드' && !guideId) navigate('/guides')
          if (item === '도지') {
            navigate(
              currentGuide?.prescription_id
                ? `/chat?prescription_id=${currentGuide.prescription_id}`
                : '/chat',
            )
          }
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main className="app-scroll guide-page__content">
          <h1 className="screen-title">복약 가이드</h1>
          {guideId && (
            <p className="screen-description">
              확인한 처방에 맞춰 복용 정보를 정리했어요.
            </p>
          )}

          {!currentIsLoading && !guideId && (
            <div className="guide-page__empty-state">
              <Card className="guide-page__empty">
                <span className="guide-page__spark" aria-hidden="true" />
                <h2>아직 만들어진 가이드가 없어요</h2>
                <p>처방전 등록부터 시작하면 복약 가이드를 확인할 수 있어요.</p>
              </Card>
              <Button
                fullWidth
                className="guide-page__empty-action"
                onClick={() => navigate('/prescriptions/upload')}
              >
                처방전 등록하기
              </Button>
            </div>
          )}

          {currentIsLoading && (
            <section className="guide-page__status" aria-live="polite" aria-busy="true">
              <div className="guide-page__status-visual guide-page__status-visual--loading">
                <DoseyMascot variant="chat" />
              </div>
              <h2>복약 가이드를 불러오고 있어요</h2>
              <p>잠시만 기다려 주세요.</p>
            </section>
          )}

          {!currentIsLoading && currentMessage && (
            <section className="guide-page__status" role="alert">
              <div className="guide-page__status-visual guide-page__status-visual--failed">
                <DoseyMascot variant="chat" />
              </div>
              <h2>가이드를 표시할 수 없어요</h2>
              <p role="alert">{currentMessage}</p>
              <Button fullWidth onClick={() => void loadGuide()}>
                다시 불러오기
              </Button>
            </section>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            currentGuide &&
            hasCompletedContent && (
            <>
              {structuredGuide ? (
                <StructuredGuideContent guide={structuredGuide} />
              ) : (
                <Card className="record-card guide-page__guide-card">
                  <span className="guide-page__guide-label">확인된 처방 기준</span>
                  <h2>확인된 복약 안내</h2>
                  <p className="guide-page__guide-intro">
                    원문 형식을 유지해 안전하게 표시해요.
                  </p>
                  <details className="guide-page__disclosure">
                    <summary>가이드 전체 내용</summary>
                    <div className="guide-page__guide-text">
                      {currentGuide.content}
                    </div>
                  </details>
                </Card>
              )}

              {completedAt && <p className="guide-page__completed-at">{completedAt} 생성</p>}

              <Button
                fullWidth
                className="guide-page__chat-button"
                onClick={() =>
                  navigate(
                    `/chat?prescription_id=${currentGuide.prescription_id}`,
                  )
                }
              >
                복약 챗봇 도지와 이야기하기
              </Button>
            </>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            currentGuide &&
            !hasCompletedContent && (
            <section
              className="guide-page__status"
              role={currentGuide.generation_status === 'FAILED' ? 'alert' : 'status'}
              aria-live="polite"
            >
              <div
                className={`guide-page__status-visual ${
                  currentGuide.generation_status === 'FAILED'
                    ? 'guide-page__status-visual--failed'
                    : currentGuide.generation_status === 'GENERATING'
                      ? 'guide-page__status-visual--generating'
                      : 'guide-page__status-visual--empty'
                }`}
              >
                <DoseyMascot variant="chat" />
              </div>
              <h2>
                {currentGuide.generation_status === 'FAILED'
                  ? '가이드를 만들지 못했어요'
                  : currentGuide.generation_status === 'COMPLETED'
                    ? '가이드 내용이 아직 없어요'
                    : '복약 가이드를 만들고 있어요'}
              </h2>
              <p>
                {currentGuide.generation_status === 'FAILED'
                  ? '다시 시도해 주세요.'
                  : currentGuide.generation_status === 'COMPLETED'
                    ? '생성된 내용을 확인할 수 없어 다시 불러와야 해요.'
                    : '도지가 복약 가이드를 준비하고 있어요.'}
              </p>
              {currentGuide.generation_status === 'GENERATING' && (
                <span className="guide-page__generating-label">가이드를 생성하고 있어요...</span>
              )}
              <Button
                fullWidth
                variant={currentGuide.generation_status === 'FAILED' ? 'primary' : 'secondary'}
                onClick={() => void loadGuide()}
              >
                {currentGuide.generation_status === 'FAILED'
                  ? '다시 시도하기'
                  : currentGuide.generation_status === 'COMPLETED'
                    ? '다시 불러오기'
                    : '다시 확인하기'}
              </Button>
            </section>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default GuidePage
