import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type { NavigateFunction } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  createGuide,
  getGuide,
  getGuideForPrescription,
  type GuideCitation,
  type GuideData,
  type GuideFallbackCode,
  type GuideReleaseDecision,
} from '../api/guides'
import { getLatestPrescription } from '../api/prescriptions'
import {
  clearAuthenticatedSession,
  isStaleTokenError,
} from '../features/auth/authSession'
import {
  Button,
  Card,
  MobileShell,
} from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './GuidePage.css'
import { ResponseFeedback } from '../components/ResponseFeedback'
import {
  getChatGuideErrorPresentation,
  hasChatGuideErrorPresentation,
  type ChatGuideErrorPresentation,
} from './chatGuideErrorPresentation'

export type GuidePageServices = {
  createGuide: typeof createGuide
  getGuide: typeof getGuide
  getGuideForPrescription: typeof getGuideForPrescription
  getLatestPrescription: typeof getLatestPrescription
}

export type GuidePageProps = {
  services?: GuidePageServices
  previewGuideId?: string | null
  navigation?: NavigateFunction
}

const defaultGuidePageServices: GuidePageServices = {
  createGuide,
  getGuide,
  getGuideForPrescription,
  getLatestPrescription,
}

function formatCompletedAt(value: string | null) {
  if (!value) return null

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null

  return new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function getGuideLoadFailurePresentation(error: unknown) {
  return getChatGuideErrorPresentation(error, {
    unauthorized: '로그인 정보를 다시 확인한 뒤 시도해 주세요.',
    notFound: '요청한 복약 가이드를 찾을 수 없어요.',
    server: '서버 응답이 원활하지 않아요. 잠시 후 다시 시도해 주세요.',
    network: '네트워크 연결을 확인한 뒤 다시 시도해 주세요.',
    unknown: '복약 가이드를 불러오지 못했어요. 다시 시도해 주세요.',
  })
}

function isNotFound(error: unknown) {
  return (
    error instanceof ApiError &&
    error.status === 404 &&
    !hasChatGuideErrorPresentation(error)
  )
}

const GUIDE_RELEASE_DECISIONS: ReadonlySet<string> = new Set([
  'PASS',
  'LIMITED',
  'REJECTED',
  'STALE',
])

const GUIDE_FALLBACK_CODES: ReadonlySet<string> = new Set([
  'NO_APPROVED_EVIDENCE',
  'CONFLICTING_EVIDENCE',
  'PROVIDER_TIMEOUT',
  'DEPENDENCY_UNAVAILABLE',
  'VALIDATION_FAILED',
  'PRESCRIPTION_STALE',
  'EXECUTION_CONTEXT_STALE',
  'UNSUPPORTED_REQUEST',
])

const GUIDE_FALLBACK_TITLES: Record<
  Exclude<GuideReleaseDecision, 'PASS'>,
  string
> = {
  LIMITED: '일부 안내만 제공할 수 있어요',
  REJECTED: '안전한 안내를 제공할 수 없어요',
  STALE: '처방 정보가 변경되었어요',
}

type GuideReleasePresentation =
  | { kind: 'LEGACY' }
  | { kind: 'PASS'; citations: GuideCitation[] }
  | {
      kind: 'FALLBACK'
      decision: Exclude<GuideReleaseDecision, 'PASS'>
      text: string
    }
  | { kind: 'INVALID' }

function isNonBlankText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isGuideReleaseDecision(value: unknown): value is GuideReleaseDecision {
  return typeof value === 'string' && GUIDE_RELEASE_DECISIONS.has(value)
}

function isGuideFallbackCode(value: unknown): value is GuideFallbackCode {
  return typeof value === 'string' && GUIDE_FALLBACK_CODES.has(value)
}

function hasValidCitationShape(value: unknown): value is GuideCitation {
  if (typeof value !== 'object' || value === null) return false
  const citation = value as Partial<GuideCitation>
  const displayOrder = citation.display_order
  return (
    citation.source_type === 'LIFESTYLE_GUIDELINE' &&
    isNonBlankText(citation.source_code) &&
    isNonBlankText(citation.source_version) &&
    isNonBlankText(citation.locator) &&
    typeof displayOrder === 'number' &&
    Number.isInteger(displayOrder) &&
    displayOrder > 0
  )
}

function hasOrderedCitations(citations: unknown[]): citations is GuideCitation[] {
  let previousDisplayOrder = 0
  for (const citation of citations) {
    if (
      !hasValidCitationShape(citation) ||
      citation.display_order <= previousDisplayOrder
    ) {
      return false
    }
    previousDisplayOrder = citation.display_order
  }
  return true
}

function getGuideReleasePresentation(
  guide: GuideData,
): GuideReleasePresentation {
  const citations: unknown = guide.citations
  if (!Array.isArray(citations)) return { kind: 'INVALID' }

  const decision: unknown = guide.release_decision
  if (decision === null) {
    return guide.release_is_current === null &&
      guide.fallback_code === null &&
      guide.fallback_text === null &&
      citations.length === 0
      ? { kind: 'LEGACY' }
      : { kind: 'INVALID' }
  }

  if (!isGuideReleaseDecision(decision)) {
    return { kind: 'INVALID' }
  }

  if (decision === 'PASS') {
    return guide.release_is_current === true &&
      isNonBlankText(guide.content) &&
      guide.fallback_code === null &&
      guide.fallback_text === null &&
      citations.length > 0 &&
      hasOrderedCitations(citations)
      ? { kind: 'PASS', citations }
      : { kind: 'INVALID' }
  }

  const fallbackCode: unknown = guide.fallback_code
  const isExpectedCurrentness = decision === 'STALE'
    ? guide.release_is_current === false
    : guide.release_is_current === true
  return guide.content === null &&
    isExpectedCurrentness &&
    isGuideFallbackCode(fallbackCode) &&
    isNonBlankText(guide.fallback_text) &&
    citations.length === 0
    ? {
        kind: 'FALLBACK',
        decision,
        text: guide.fallback_text,
      }
    : { kind: 'INVALID' }
}

function GuideCitations({ citations }: { citations: GuideCitation[] }) {
  return (
    <aside className="guide-page__citations" aria-labelledby="guide-citations-heading">
      <h2 id="guide-citations-heading">안내 근거</h2>
      <ol>
        {citations.map((citation) => (
          <li key={`${citation.display_order}:${citation.source_code}:${citation.source_version}:${citation.locator}`}>
            <strong>{citation.source_code}</strong>
            <span>{citation.source_version}</span>
            <span>{citation.locator}</span>
          </li>
        ))}
      </ol>
    </aside>
  )
}

type GuideDetail = {
  label: string
  value: string | null
}

type UnclassifiedGuideField = {
  sourceLabel: string
  value: string | null
}

type GuideSectionKey =
  | 'medicationCaution'
  | 'foodAndDrink'
  | 'alcoholAndSmoking'
  | 'possibleDiscomfort'
  | 'seekMedicalCare'
  | 'pregnancyAndBreastfeeding'

type StructuredMedication = {
  name: string
  details: GuideDetail[]
  sections: Record<GuideSectionKey, string | null>
  legacyGuidance: string | null
  unclassifiedFields: UnclassifiedGuideField[]
  notices: string[]
}

type StructuredGuide = {
  medications: StructuredMedication[]
  generalNotice: string | null
  safetyNotice: string | null
  unclassifiedFields: UnclassifiedGuideField[]
}

const EMPTY_GUIDE_SECTION = '현재 제공된 안내가 없어요.'

const GUIDE_DETAIL_FIELDS = [
  { key: 'dose', label: '1회량' },
  { key: 'frequency', label: '하루 횟수' },
  { key: 'timing', label: '복용 시점' },
  { key: 'duration', label: '복용 기간' },
] as const

type GuideDetailKey = typeof GUIDE_DETAIL_FIELDS[number]['key']

const GUIDE_DETAIL_LABELS: Record<string, GuideDetailKey> = {
  '용량': 'dose',
  '1회량': 'dose',
  '복용 횟수': 'frequency',
  '하루 횟수': 'frequency',
  '복용 시점': 'timing',
  '복용 기간': 'duration',
}

const GUIDE_SECTION_FIELDS: ReadonlyArray<{
  key: GuideSectionKey
  label: string
}> = [
  { key: 'medicationCaution', label: '복용 시 주의해야 할 점' },
  { key: 'foodAndDrink', label: '주의해야 할 음식·음료' },
  { key: 'alcoholAndSmoking', label: '음주/흡연 안내' },
  { key: 'possibleDiscomfort', label: '나타날 수 있는 불편감' },
  { key: 'seekMedicalCare', label: '이런 증상은 병원에 가세요' },
  { key: 'pregnancyAndBreastfeeding', label: '임신·수유 중 안내' },
]

const GUIDE_SECTION_LABELS: Record<string, GuideSectionKey> = {
  ...Object.fromEntries(GUIDE_SECTION_FIELDS.map(({ key, label }) => [label, key])),
}

const INCOMPLETE_DOSE_NOTICE =
  '용량 정보는 처방전 또는 의료진 안내를 확인해 주세요.'

function parseGuideContent(content: string): StructuredGuide | null {
  const normalizedContent = content.replace(/\r\n?/g, '\n').trim()
  if (!normalizedContent) return null

  const blocks = normalizedContent.split(/\n{2,}/)
  if (blocks.shift()?.trim() !== '복약 가이드') return null

  const medications: StructuredMedication[] = []
  let generalNotice: string | null = null
  let safetyNotice: string | null = null
  let hasGeneralNotice = false
  let hasSafetyNotice = false
  const unclassifiedFields: UnclassifiedGuideField[] = []

  for (const block of blocks) {
    const lines = block.split('\n')
    const heading = lines[0]?.match(/^\[(\d+)]\s+(.+)$/)

    if (!heading) {
      for (const line of lines) {
        const field = line.match(/^([^:]+):\s*(.*)$/)
        if (!field) return null
        const value = field[2].trim() || null

        if (field[1] === '공통 안내' && !hasGeneralNotice) {
          hasGeneralNotice = true
          generalNotice = value
        } else if (field[1] === '안전 안내' && !hasSafetyNotice) {
          hasSafetyNotice = true
          safetyNotice = value
        } else {
          unclassifiedFields.push({ sourceLabel: field[1], value })
        }
      }
      continue
    }

    if (Number(heading[1]) !== medications.length + 1) return null
    lines.shift()

    const detailValues = new Map<GuideDetailKey, string>()
    const sectionValues = new Map<GuideSectionKey, string>()
    let legacyGuidance: string | null = null
    let hasLegacyGuidance = false
    const medicationUnclassifiedFields: UnclassifiedGuideField[] = []
    const notices: string[] = []

    for (const line of lines) {
      if (line === INCOMPLETE_DOSE_NOTICE) {
        notices.push(line)
        continue
      }

      const field = line.match(/^([^:]+):\s*(.*)$/)
      if (!field) return null

      const [, sourceLabel, rawValue] = field
      const value = rawValue.trim()
      if (sourceLabel === '복약 안내') {
        if (hasLegacyGuidance) return null
        hasLegacyGuidance = true
        legacyGuidance = value || null
        continue
      }

      const detailKey = GUIDE_DETAIL_LABELS[sourceLabel]
      if (detailKey) {
        if (!value || detailValues.has(detailKey)) return null
        detailValues.set(detailKey, value)
        continue
      }

      const sectionKey = GUIDE_SECTION_LABELS[sourceLabel]
      if (!sectionKey) {
        medicationUnclassifiedFields.push({ sourceLabel, value: value || null })
        continue
      }
      if (sectionValues.has(sectionKey)) return null
      if (value) sectionValues.set(sectionKey, value)
    }

    if (!heading[2].trim()) return null
    medications.push({
      name: heading[2].trim(),
      details: GUIDE_DETAIL_FIELDS.map(({ key, label }) => ({
        label,
        value: detailValues.get(key) ?? null,
      })),
      sections: Object.fromEntries(
        GUIDE_SECTION_FIELDS.map(({ key }) => [key, sectionValues.get(key) ?? null]),
      ) as Record<GuideSectionKey, string | null>,
      legacyGuidance,
      unclassifiedFields: medicationUnclassifiedFields,
      notices,
    })
  }

  if (medications.length === 0) return null
  return { medications, generalNotice, safetyNotice, unclassifiedFields }
}

function UnclassifiedGuideFields({
  fields,
}: {
  fields: UnclassifiedGuideField[]
}) {
  if (fields.length === 0) return null

  return (
    <details className="guide-page__unclassified">
      <summary>추가 안내 원문</summary>
      <dl>
        {fields.map((field, index) => (
          <div key={`${index}-${field.sourceLabel}`}>
            <dt>{field.sourceLabel}</dt>
            <dd>{field.value ?? EMPTY_GUIDE_SECTION}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

function MedicationCard({
  medication,
  index,
}: {
  medication: StructuredMedication
  index: number
}) {
  const [isExpanded, setIsExpanded] = useState(false)
  const medicationPanelId = `guide-medication-panel-${index}`
  const summary = medication.details
    .filter((detail) =>
      detail.value && (detail.label === '하루 횟수' || detail.label === '복용 시점'),
    )
    .map((detail) => detail.value)
    .join(' · ')

  return (
    <article className={`guide-page__medication-card ${isExpanded ? 'is-expanded' : ''}`}>
      <button
        type="button"
        className="guide-page__medication-toggle"
        aria-expanded={isExpanded}
        aria-controls={medicationPanelId}
        onClick={() => setIsExpanded((expanded) => !expanded)}
      >
        <span>
          <span className="guide-page__medication-name" role="heading" aria-level={3}>
            {medication.name}
          </span>
          <small>{summary || '복용 정보를 확인해 주세요'}</small>
        </span>
        <span className="guide-page__chevron" aria-hidden="true" />
      </button>
      {isExpanded && (
        <div className="guide-page__medication-body" id={medicationPanelId}>
          <dl className="guide-page__medication-details">
            {medication.details.map((detail) => (
              <div key={detail.label}>
                <dt>{detail.label}</dt>
                <dd className={detail.value ? '' : 'is-empty'}>
                  {detail.value ?? EMPTY_GUIDE_SECTION}
                </dd>
              </div>
            ))}
          </dl>
          {medication.notices.map((notice) => (
            <p className="guide-page__medication-notice" key={notice}>
              {notice}
            </p>
          ))}
          <div className="guide-page__medical-sections">
            {GUIDE_SECTION_FIELDS.map(({ key, label }) => (
              <section
                className="guide-page__guidance"
                aria-labelledby={`guide-${key}-${index}`}
                key={key}
              >
                <h4 id={`guide-${key}-${index}`}>{label}</h4>
                <p className={medication.sections[key] ? '' : 'is-empty'}>
                  {medication.sections[key] ?? EMPTY_GUIDE_SECTION}
                </p>
              </section>
            ))}
          </div>
          {medication.legacyGuidance && (
            <section className="guide-page__source-guidance" aria-labelledby={`guide-source-${index}`}>
              <h4 id={`guide-source-${index}`}>복약 안내</h4>
              <p>{medication.legacyGuidance}</p>
            </section>
          )}
          <UnclassifiedGuideFields fields={medication.unclassifiedFields} />
        </div>
      )}
    </article>
  )
}

function StructuredGuideContent({ guide }: { guide: StructuredGuide }) {
  return (
    <section
      className="guide-page__structured-guide"
      aria-labelledby="guide-medications-heading"
    >
      <h2
        id="guide-medications-heading"
        aria-label={`확인된 약 목록 · ${guide.medications.length}개`}
      >
        확인된 약 목록
      </h2>
      <span className="guide-page__medication-count" aria-hidden="true">
        확인된 약 목록 · {guide.medications.length}개
      </span>
      <div className="guide-page__medication-list">
        {guide.medications.map((medication, index) => (
          <MedicationCard
            medication={medication}
            index={index}
            key={`${index}-${medication.name}`}
          />
        ))}
      </div>

      <aside className="guide-page__common-notice" aria-labelledby="guide-common-heading">
        <h3 id="guide-common-heading">공통 복약 안내</h3>
        <p className={guide.generalNotice ? '' : 'is-empty'}>
          {guide.generalNotice ?? EMPTY_GUIDE_SECTION}
        </p>
      </aside>
      <aside className="guide-page__safety-notice" aria-labelledby="guide-safety-heading">
        <h3 id="guide-safety-heading">안전 안내</h3>
        <p className={guide.safetyNotice ? '' : 'is-empty'}>
          {guide.safetyNotice ?? EMPTY_GUIDE_SECTION}
        </p>
      </aside>
      <UnclassifiedGuideFields fields={guide.unclassifiedFields} />
    </section>
  )
}

function GuidePage({
  services = defaultGuidePageServices,
  previewGuideId,
  navigation,
}: GuidePageProps = {}) {
  const routerNavigate = useNavigate()
  const navigate = navigation ?? routerNavigate
  const { guideId: routeParamGuideId } = useParams<{ guideId: string }>()
  const guideId = previewGuideId === undefined
    ? routeParamGuideId
    : previewGuideId ?? undefined
  const [guide, setGuide] = useState<GuideData | null>(null)
  const [message, setMessage] = useState('')
  const [errorPresentation, setErrorPresentation] =
    useState<ChatGuideErrorPresentation | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [stateGuideId, setStateGuideId] = useState<string | null>(null)
  const guideRequestIdRef = useRef(0)
  const retryRequestRef = useRef<number | null>(null)
  const [isRetrying, setIsRetrying] = useState(false)
  const [retryError, setRetryError] = useState('')

  const loadGuide = useCallback(async () => {
    const requestedGuideId = guideId ?? null
    const requestId = ++guideRequestIdRef.current
    const isCurrentRequest = () => guideRequestIdRef.current === requestId

    setStateGuideId(requestedGuideId)

    try {
      setIsLoading(true)
      setMessage('')
      setErrorPresentation(null)
      setGuide(null)

      if (!requestedGuideId) {
        let prescriptionResponse

        try {
          prescriptionResponse = await services.getLatestPrescription()
        } catch (error) {
          if (!isCurrentRequest()) return
          if (isNotFound(error)) return
          throw error
        }

        if (!isCurrentRequest()) return
        const prescriptionId = prescriptionResponse.data.prescription_id

        try {
          const response = await services.getGuideForPrescription(prescriptionId)
          if (!isCurrentRequest()) return

          if (response.data.prescription_id !== prescriptionId) {
            setMessage('확인한 처방과 다른 가이드 응답을 받았어요. 다시 불러와 주세요.')
            return
          }

          navigate(`/guides/${response.data.guide_id}`, { replace: true })
        } catch (error) {
          if (!isCurrentRequest()) return
          if (isNotFound(error)) return
          throw error
        }

        return
      }

      const response = await services.getGuide(requestedGuideId)
      if (!isCurrentRequest()) return

      if (response.data.guide_id !== requestedGuideId) {
        setMessage('요청한 가이드와 다른 응답을 받았어요. 다시 불러와 주세요.')
        return
      }

      setGuide(response.data)
    } catch (error) {
      if (!isCurrentRequest()) return
      if (isStaleTokenError(error)) {
        clearAuthenticatedSession()
        navigate('/login', { replace: true })
        return
      }
      if (isNotFound(error)) {
        setGuide(null)
        setMessage('')
        return
      }
      setGuide(null)
      setErrorPresentation(getGuideLoadFailurePresentation(error))
    } finally {
      if (isCurrentRequest()) {
        setIsLoading(false)
      }
    }
  }, [guideId, navigate, services])

  useEffect(() => {
    retryRequestRef.current = null
    setIsRetrying(false)
    setRetryError('')
    void loadGuide()
    return () => {
      guideRequestIdRef.current += 1
    }
  }, [loadGuide])

  const routeGuideId = guideId ?? null
  const isCurrentGuideState = stateGuideId === routeGuideId
  const currentGuide = isCurrentGuideState ? guide : null
  const currentMessage = isCurrentGuideState ? message : ''
  const currentErrorPresentation = isCurrentGuideState
    ? errorPresentation
    : null
  const currentIsLoading = isCurrentGuideState ? isLoading : Boolean(guideId)

  const retryGeneration = async () => {
    if (currentGuide?.generation_status !== 'FAILED' || retryRequestRef.current !== null) return

    const prescriptionId = currentGuide.prescription_id
    const requestId = ++guideRequestIdRef.current
    retryRequestRef.current = requestId
    setIsRetrying(true)
    setRetryError('')
    try {
      const response = await services.createGuide(prescriptionId)
      if (guideRequestIdRef.current !== requestId) return
      if (response.data.prescription_id !== prescriptionId) {
        setRetryError('확인한 처방과 다른 가이드 응답을 받았어요. 다시 확인해 주세요.')
        return
      }
      navigate(`/guides/${response.data.guide_id}`, { replace: true })
    } catch (error) {
      if (guideRequestIdRef.current !== requestId) return
      if (isStaleTokenError(error)) {
        clearAuthenticatedSession()
        navigate('/login', { replace: true })
        return
      }
      setRetryError(error instanceof ApiError
        ? error.message
        : '복약 가이드를 만드는 중 오류가 발생했습니다. 다시 시도해 주세요.')
    } finally {
      if (guideRequestIdRef.current === requestId) {
        retryRequestRef.current = null
        setIsRetrying(false)
      }
    }
  }

  const completedAt = formatCompletedAt(currentGuide?.completed_at ?? null)
  const releasePresentation = currentGuide
    ? getGuideReleasePresentation(currentGuide)
    : null
  const hasCompletedContent =
    currentGuide?.generation_status === 'COMPLETED' &&
    (releasePresentation?.kind === 'LEGACY' ||
      releasePresentation?.kind === 'PASS') &&
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
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
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
          {currentGuide && (
            <p className="screen-description">
              약마다 언제·어떻게 복용하는지,<br />
              어떤 점을 주의하면 좋은지 알려드려요.
            </p>
          )}

          {!currentIsLoading && !currentMessage && !currentErrorPresentation && !currentGuide && (
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

          {!currentIsLoading && (currentMessage || currentErrorPresentation) && (
            <section className="guide-page__status" role="alert">
              <div className="guide-page__status-visual guide-page__status-visual--failed">
                <DoseyMascot variant="chat" />
              </div>
              <h2>
                {currentErrorPresentation?.title ?? '가이드를 표시할 수 없어요'}
              </h2>
              {(currentErrorPresentation?.helper || currentMessage) && (
                <p>{currentErrorPresentation?.helper ?? currentMessage}</p>
              )}

              <Button
                fullWidth
                onClick={() => {
                  if (currentErrorPresentation?.action === 'CONSENT_SETTINGS') {
                    navigate('/profile')
                    return
                  }
                  void loadGuide()
                }}
              >
                {currentErrorPresentation?.action === 'CONSENT_SETTINGS'
                  ? '동의 설정 확인하기'
                  : currentErrorPresentation?.action === 'RETRY'
                    ? '다시 시도'
                    : '다시 불러오기'}
              </Button>
            </section>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            !currentErrorPresentation &&
            currentGuide?.generation_status === 'COMPLETED' &&
            releasePresentation?.kind === 'FALLBACK' && (
            <section className="guide-page__release-fallback" role="status" aria-live="polite">
              <DoseyMascot variant="chat" />
              <div>
                <h2>{GUIDE_FALLBACK_TITLES[releasePresentation.decision]}</h2>
                <p>{releasePresentation.text}</p>
              </div>
            </section>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            !currentErrorPresentation &&
            currentGuide?.generation_status === 'COMPLETED' &&
            releasePresentation?.kind === 'INVALID' && (
            <section className="guide-page__status" role="alert">
              <div className="guide-page__status-visual guide-page__status-visual--failed">
                <DoseyMascot variant="chat" />
              </div>
              <h2>가이드를 안전하게 표시할 수 없어요</h2>
              <p>최신 가이드 정보를 다시 불러와 주세요.</p>
              <Button fullWidth onClick={() => void loadGuide()}>
                다시 불러오기
              </Button>
            </section>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            !currentErrorPresentation &&
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

              {releasePresentation?.kind === 'PASS' && (
                <GuideCitations citations={releasePresentation.citations} />
              )}

              {import.meta.env.DEV && <ResponseFeedback key={currentGuide.guide_id} target={{ guideId: currentGuide.guide_id }} />}

              {completedAt && <p className="guide-page__completed-at">{completedAt} 생성</p>}

              <Button
                fullWidth
                variant="secondary"
                className="guide-page__schedule-button"
                onClick={() => navigate('/schedule')}
              >
                복용 일정 확인하기
              </Button>

              <Button
                fullWidth
                className="guide-page__chat-button"
                aria-label="복약 챗봇 도지와 이야기하기"
                onClick={() =>
                  navigate(
                    `/chat?prescription_id=${currentGuide.prescription_id}`,
                  )
                }
              >
                도지와 대화하기
              </Button>
            </>
          )}

          {!currentIsLoading &&
            !currentMessage &&
            !currentErrorPresentation &&
            currentGuide &&
            releasePresentation?.kind !== 'FALLBACK' &&
            releasePresentation?.kind !== 'INVALID' &&
            !hasCompletedContent && (
            <section
              className="guide-page__status"
              role={!isRetrying && currentGuide.generation_status === 'FAILED' ? 'alert' : 'status'}
              aria-busy={isRetrying}
              aria-live="polite"
            >
              <div
                className={`guide-page__status-visual ${
                  isRetrying
                    ? 'guide-page__status-visual--generating'
                    : currentGuide.generation_status === 'FAILED'
                      ? 'guide-page__status-visual--failed'
                      : currentGuide.generation_status === 'GENERATING'
                        ? 'guide-page__status-visual--generating'
                        : 'guide-page__status-visual--empty'
                }`}
              >
                <DoseyMascot variant="chat" />
              </div>
              <h2>
                {isRetrying
                  ? '복약 가이드를 만들고 있어요'
                  : currentGuide.generation_status === 'FAILED'
                    ? '가이드를 만들지 못했어요'
                    : currentGuide.generation_status === 'COMPLETED'
                      ? '가이드 내용이 아직 없어요'
                      : '복약 가이드를 만들고 있어요'}
              </h2>
              <p>
                {isRetrying
                  ? '잠시만 기다려 주세요.'
                  : currentGuide.generation_status === 'FAILED'
                    ? '다시 시도해 주세요.'
                    : currentGuide.generation_status === 'COMPLETED'
                      ? '생성된 내용을 확인할 수 없어 다시 불러와야 해요.'
                      : '도지가 복약 가이드를 준비하고 있어요.'}
              </p>
              {retryError && <p role="alert">{retryError}</p>}
              {currentGuide.generation_status === 'GENERATING' && (
                <span className="guide-page__generating-label">가이드를 생성하고 있어요...</span>
              )}
              <Button
                fullWidth
                variant={currentGuide.generation_status === 'FAILED' ? 'primary' : 'secondary'}
                disabled={isRetrying}
                onClick={() => void (currentGuide.generation_status === 'FAILED'
                  ? retryGeneration()
                  : loadGuide())}
              >
                {isRetrying
                  ? '가이드를 생성하고 있어요...'
                  : currentGuide.generation_status === 'FAILED'
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
