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
          <article className="guide-page__medication-card" key={`${index}-${medication.name}`}>
            <h3>{medication.name}</h3>
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
              <h4 id={`guide-guidance-${index}`}>복약 안내</h4>
              <p>{medication.guidance}</p>
            </section>
          </article>
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
  const structuredGuide = currentGuide?.content
    ? parseGuideContent(currentGuide.content)
    : null
  const confirmedTimings =
    structuredGuide?.medications.flatMap((medication) => {
      const timing = medication.details.find(
        (detail) => detail.label === '복용 시점',
      )
      return timing ? [{ name: medication.name, timing: timing.value }] : []
    }) ?? []

  return (
    <div className="guide-page">
      <MobileShell
        title="Dosey 도지"
        activeNavigation="가이드"
        disabledNavigation={['일정']}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '도지') navigate('/chat')
          if (item === '가이드' && !guideId) navigate('/guides')
          if (item === '메뉴') navigate('/profile')
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
              <h2>아직 만들어진 가이드가 없어요</h2>
              <p>처방전을 등록하고 시작하면 복약 가이드를 확인할 수 있어요.</p>
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
                <span className="guide-page__hero-label">오늘 확인한 복용</span>
                <h2>확인된 복용 조건</h2>
                {confirmedTimings.length > 0 ? (
                  <ul className="guide-page__timing-list" aria-label="확인된 복용 시점">
                    {confirmedTimings.map((item, index) => (
                      <li key={`${index}-${item.name}`}>
                        <span>{item.timing}</span>
                        <strong>{item.name}</strong>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>직접 확인한 처방을 기준으로 생성된 안내를 표시해요.</p>
                )}
                {completedAt && <small>{completedAt} 생성</small>}
                <button className="guide-page__schedule-button" type="button" disabled>
                  복용 일정 준비 중
                </button>
              </Card>

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
                복약 챗봇 도지와 이야기하기
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
