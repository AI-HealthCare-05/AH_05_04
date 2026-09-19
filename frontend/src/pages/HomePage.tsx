import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  getMedicationReport,
  type MedicationReportData,
} from '../api/medicationReports'
import { getLatestPrescription } from '../api/prescriptions'
import type { CurrentUser } from '../api/users'
import bellIcon from '../assets/icon-bell-notification.svg'
import { Button, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

type HomeServices = {
  getLatestPrescription: typeof getLatestPrescription
  getMedicationReport: typeof getMedicationReport
}

const defaultHomeServices: HomeServices = {
  getLatestPrescription,
  getMedicationReport,
}

type HomePrescriptionState = 'loading' | 'empty' | 'active' | 'error'
type HomeReportState = 'idle' | 'loading' | 'ready' | 'error'

function HomeShortcutIcon({ type }: { type: 'prescription' | 'report' }) {
  return <span className={`mvp-home__shortcut-glyph is-${type}`} aria-hidden="true" />
}

function HomeAdherenceCrown() {
  return (
    <svg
      className="mvp-home__adherence-crown"
      viewBox="0 0 18 14"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M2 11.5L1.2 3.6L5.3 6.2L9 1.3L12.7 6.2L16.8 3.6L16 11.5H2Z"
        fill="#F8B84E"
        stroke="#D58A18"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path
        d="M3 12.5H15"
        stroke="#D58A18"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  )
}

function HomeAdherenceCard({
  report,
  state,
  onOpenReport,
}: {
  report: MedicationReportData | null
  state: HomeReportState
  onOpenReport: () => void
}) {
  const percentage = report?.adherence_rate.percentage ?? null
  const hasRate =
    state === 'ready' &&
    report !== null &&
    report.adherence_rate.denominator > 0 &&
    percentage !== null
  const safePercentage = hasRate
    ? Math.min(100, Math.max(0, percentage))
    : 0
  const progressRatio = safePercentage / 100
  const progressStyle = {
    '--home-adherence-fill': `calc(${safePercentage}% - ${48 * progressRatio}px)`,
    '--home-adherence-mascot': `clamp(0px, calc(${safePercentage}% + ${4 - 48 * progressRatio}px), calc(100% - 40px))`,
  } as CSSProperties

  const statusText = state === 'loading'
    ? '기록 확인 중'
    : state === 'error'
      ? '리포트에서 확인'
      : hasRate
        ? `${safePercentage}%`
        : '계산할 기록 없음'
  const message = state === 'loading'
    ? '최근 복약 기록을 불러오고 있어요.'
    : state === 'error'
      ? '상세 리포트에서 최근 기록을 확인해 주세요.'
      : !hasRate
        ? '이번 주 계산할 복약 기록이 아직 없어요.'
        : safePercentage === 100
          ? '일주일 동안 꾸준히 약을 챙기셨어요! 대단해요!'
          : '이번 주도 꾸준히 약을 챙기고 있어요!'

  return (
    <section
      className="mvp-home__adherence-card"
      aria-labelledby="home-adherence-heading"
    >
      <div className="mvp-home__adherence-header">
        <h2 id="home-adherence-heading">이번 주 복약 달성도</h2>
        <button type="button" onClick={onOpenReport}>
          상세 보기 &gt;
        </button>
      </div>
      <strong className="mvp-home__adherence-status">{statusText}</strong>
      <div
        className="mvp-home__adherence-progress"
        aria-label={hasRate ? `이번 주 복약 달성도 ${safePercentage}%` : statusText}
        role={hasRate ? 'progressbar' : 'status'}
        aria-valuemin={hasRate ? 0 : undefined}
        aria-valuemax={hasRate ? 100 : undefined}
        aria-valuenow={hasRate ? safePercentage : undefined}
        style={progressStyle}
      >
        <span className="mvp-home__adherence-track" aria-hidden="true" />
        <span className="mvp-home__adherence-fill" aria-hidden="true" />
        <DoseyMascot variant="progress" />
        {safePercentage === 100 && <HomeAdherenceCrown />}
      </div>
      <p>{message}</p>
    </section>
  )
}

function HomePage({
  currentUser,
  services = defaultHomeServices,
}: {
  currentUser: CurrentUser
  services?: HomeServices
}) {
  const navigate = useNavigate()
  const location = useLocation()

  const shouldShowOnboarding =
    (
      location.state as {
        showPrescriptionOnboarding?: boolean
      } | null
    )?.showPrescriptionOnboarding === true

  const [isOnboardingOpen, setIsOnboardingOpen] =
    useState(shouldShowOnboarding)
  const [prescriptionState, setPrescriptionState] =
    useState<HomePrescriptionState>('loading')
  const [reportState, setReportState] = useState<HomeReportState>('idle')
  const [report, setReport] = useState<MedicationReportData | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [showDojiHint, setShowDojiHint] = useState(false)
  const dojiHintTimerRef = useRef<number | null>(null)

  const onboardingDialogRef = useRef<HTMLElement>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const homePrescriptionButtonRef = useRef<HTMLButtonElement>(null)
  const wasOnboardingOpenRef = useRef(false)
  useEffect(() => {
    const scheduleDojiHint = () => {
      if (dojiHintTimerRef.current !== null) {
        window.clearTimeout(dojiHintTimerRef.current)
      }

      setShowDojiHint(false)

      dojiHintTimerRef.current = window.setTimeout(() => {
        setShowDojiHint(true)
        dojiHintTimerRef.current = null
      }, 1500)
    }

    const activityEvents = [
      'pointerdown',
      'pointermove',
      'keydown',
      'wheel',
      'touchstart',
    ] as const

    activityEvents.forEach((eventName) => {
      window.addEventListener(eventName, scheduleDojiHint, {
        passive: true,
      })
    })

    const scrollContainer =
      pageRef.current?.querySelector<HTMLElement>('.app-scroll')

    scrollContainer?.addEventListener(
      'scroll',
      scheduleDojiHint,
      { passive: true },
    )

    scheduleDojiHint()

    return () => {
      if (dojiHintTimerRef.current !== null) {
        window.clearTimeout(dojiHintTimerRef.current)
      }

      activityEvents.forEach((eventName) => {
        window.removeEventListener(eventName, scheduleDojiHint)
      })

      scrollContainer?.removeEventListener(
        'scroll',
        scheduleDojiHint,
      )
    }
  }, [])
  const handleOnboardingKeyDown = (
    event: KeyboardEvent<HTMLElement>,
  ) => {
    if (event.key === 'Escape') {
      setIsOnboardingOpen(false)
      return
    }

    if (event.key !== 'Tab') return

    const buttons =
      onboardingDialogRef.current?.querySelectorAll<HTMLButtonElement>('button')

    if (!buttons || buttons.length === 0) return

    const firstButton = buttons[0]
    const lastButton = buttons[buttons.length - 1]

    if (event.shiftKey && document.activeElement === firstButton) {
      event.preventDefault()
      lastButton.focus()
    } else if (!event.shiftKey && document.activeElement === lastButton) {
      event.preventDefault()
      firstButton.focus()
    }
  }

  useEffect(() => {
    if (!shouldShowOnboarding) return

    // 한 번 소비한 가입 직후 상태를 history에서 제거
    navigate('/', {
      replace: true,
      state: null,
    })
  }, [navigate, shouldShowOnboarding])

  useEffect(() => {
    const background =
      pageRef.current?.querySelector<HTMLElement>('.mobile-app')

    if (background) {
      background.inert = isOnboardingOpen

      if (isOnboardingOpen) {
        background.setAttribute('aria-hidden', 'true')
      } else {
        background.removeAttribute('aria-hidden')
      }
    }

    if (isOnboardingOpen) {
      const firstButton =
        onboardingDialogRef.current?.querySelector<HTMLButtonElement>('button')

      firstButton?.focus()
    } else if (wasOnboardingOpenRef.current) {
      homePrescriptionButtonRef.current?.focus()
    }

    wasOnboardingOpenRef.current = isOnboardingOpen

    return () => {
      if (background) {
        background.inert = false
        background.removeAttribute('aria-hidden')
      }
    }
  }, [isOnboardingOpen])

  useEffect(() => {
    const controller = new AbortController()

    setPrescriptionState('loading')
    setReportState('idle')
    setReport(null)

    const loadHomeState = async () => {
      try {
        await services.getLatestPrescription(controller.signal)
        if (controller.signal.aborted) return

        setPrescriptionState('active')
        setReportState('loading')

        try {
          const response = await services.getMedicationReport(7, controller.signal)
          if (controller.signal.aborted) return
          setReport(response.data)
          setReportState('ready')
        } catch {
          if (!controller.signal.aborted) setReportState('error')
        }
      } catch (error) {
        if (controller.signal.aborted) return
        setPrescriptionState(
          error instanceof ApiError &&
            error.status === 404 &&
            error.code === 'PRESCRIPTION_NOT_FOUND'
            ? 'empty'
            : 'error',
        )
      }
    }

    void loadHomeState()

    return () => controller.abort()
  }, [reloadKey, services])

  const userName = currentUser.name.trim()
  const today = useMemo(
    () =>
      new Intl.DateTimeFormat('ko-KR', {
        month: 'long',
        day: 'numeric',
        weekday: 'long',
      }).format(new Date()),
    [],
  )

  return (
    <div
      ref={pageRef}
      className="mvp-page mvp-home-page"
    >
      <MobileShell
        title="Dosey 도지"
        brandMark={<DoseyMascot variant="header" />}
        headerAction={
          <button
            className="mvp-home__notification"
            type="button"
            aria-label="알림"
            onClick={() => navigate('/notifications')}
          >
            <img src={bellIcon} alt="" aria-hidden="true" />
          </button>
        }
        activeNavigation="홈"
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '일정') navigate('/schedule')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main className={`app-scroll mvp-page__content is-${prescriptionState}`}>
          <div className="mvp-home__hero">
            <div className="mvp-home__greeting-row">
              <p>
                안녕하세요,{' '}
                <strong>{userName ? `${userName}님!` : '도지 사용자님!'}</strong>
              </p>
              <time dateTime={new Date().toISOString()}>{today}</time>
            </div>
            <h1 className="mvp-page__title mvp-home__title">오늘도 건강한 하루 되세요</h1>
            <p className="mvp-home__intro">도지가 복약 생활을 함께 도와드릴게요.</p>
            <DoseyMascot variant="hero" />
          </div>
          {!userName && (
            <p className="mvp-home__greeting-status" role="status">
              사용자 이름을 불러오지 못했어요. 홈 기능은 계속 사용할 수 있어요.
            </p>
          )}

          {prescriptionState === 'loading' && (
            <section className="mvp-home__state-card" role="status">
              <strong>홈 정보를 불러오고 있어요</strong>
              <p>현재 처방 상태를 확인하고 있어요.</p>
            </section>
          )}

          {prescriptionState === 'error' && (
            <section className="mvp-home__state-card is-error" role="alert">
              <strong>홈 정보를 불러오지 못했어요</strong>
              <p>네트워크 연결을 확인한 뒤 다시 시도해 주세요.</p>
              <button type="button" onClick={() => setReloadKey((key) => key + 1)}>
                다시 시도
              </button>
            </section>
          )}

          {prescriptionState === 'empty' && (
            <section
              className="mvp-home__card-stack is-empty"
              aria-label="Home 주요 기능"
            >
              <button
                ref={homePrescriptionButtonRef}
                className="mvp-home__hub-card mvp-home__hub-card--prescription"
                type="button"
                aria-label="처방약 복용 안내 · 내 처방전 등록하기"
                onClick={() =>
                  navigate('/prescriptions/upload', {
                    state: { intent: 'new-prescription' },
                  })
                }
              >
                <span className="mvp-home__hub-icon">
                  <HomeShortcutIcon type="prescription" />
                </span>
                <span className="mvp-home__hub-copy">
                  <strong>내 처방전 등록하기</strong>
                  <small>
                    처방전을 등록하고
                    <br />
                    복약 가이드를 확인해보세요.
                  </small>
                </span>
                <span className="mvp-home__hub-arrow" aria-hidden="true">
                  ›
                </span>
              </button>
            </section>
          )}
          {prescriptionState === 'active' && (
            <section
              className="mvp-home__active-stack"
              aria-label="Home 처방 등록 완료"
            >
              <HomeAdherenceCard
                report={report}
                state={reportState}
                onOpenReport={() => navigate('/report')}
              />

              <div className="mvp-home__action-stack">
                <button
                  className="mvp-home__hub-card mvp-home__hub-card--report"
                  type="button"
                  onClick={() => navigate('/report')}
                >
                  <span className="mvp-home__hub-icon">
                    <HomeShortcutIcon type="report" />
                  </span>
                  <span className="mvp-home__hub-copy">
                    <strong>복약 리포트 보기</strong>
                    <small>
                    7일/30일 복약 현황을
                    <br />
                    한눈에 확인해보세요.
                    </small>
                  </span>
                  <span className="mvp-home__hub-arrow" aria-hidden="true">
                    ›
                  </span>
                </button>

                <button
                  className="mvp-home__hub-card mvp-home__hub-card--new-prescription"
                  type="button"
                  aria-label="새 처방전 등록하기"
                  onClick={() =>
                    navigate('/prescriptions/upload', {
                      state: { intent: 'new-prescription' },
                    })
                  }
                >
                  <span className="mvp-home__hub-icon">
                    <HomeShortcutIcon type="prescription" />
                  </span>
                  <span className="mvp-home__hub-copy">
                    <strong>새 처방전 등록하기</strong>
                    <small>
                      새 처방전을 등록하고
                      <br />
                      최신 복약 가이드를 확인해보세요.
                    </small>
                  </span>
                  <span className="mvp-home__hub-arrow" aria-hidden="true">
                    ›
                  </span>
                </button>
              </div>
            </section>
          )}
        </main>

        {showDojiHint && (
          <div
            className="mvp-home__doji-hint"
            aria-label="도지 기능 안내"
          >
            처방약 이외에 다른 약을 복용해도 괜찮은지 물어볼 수 있어요!
          </div>
        )}

      </MobileShell>
      {isOnboardingOpen && (
        <div
          className="mvp-onboarding-backdrop"
          onClick={() => setIsOnboardingOpen(false)}
        >

          <section
            ref={onboardingDialogRef}
            className="mvp-onboarding-sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="prescription-onboarding-title"
            onKeyDown={handleOnboardingKeyDown}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mvp-onboarding-sheet__handle" aria-hidden="true" />

            <div className="mvp-onboarding-sheet__icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none">
                <path
                  d="M8.5 7.5 9.8 5.8h4.4l1.3 1.7H18A2 2 0 0 1 20 9.5v7A2 2 0 0 1 18 18.5H6A2 2 0 0 1 4 16.5v-7a2 2 0 0 1 2-2h2.5Z"
                  fill="currentColor"
                />
                <circle cx="12" cy="13" r="3" fill="white" />
              </svg>
            </div>

            <h2
              id="prescription-onboarding-title"
              className="mvp-onboarding-sheet__title"
            >
              처방전을 등록해 볼까요?
            </h2>

            <p className="mvp-onboarding-sheet__description">
              처방전을 사진으로 찍으면 도지가
              <br />
              복약 알림과 가이드를 자동으로 완성해 드려요
            </p>

              <div className="mvp-onboarding-sheet__actions">
                <Button
                  className="mvp-onboarding-sheet__primary"
                  type="button"
                  onClick={() =>
                    navigate('/prescriptions/upload', {
                      state: { intent: 'new-prescription' },
                    })
                  }
                >
                  지금 처방전 촬영하기
                </Button>

                <Button
                  className="mvp-onboarding-sheet__secondary"
                  type="button"
                  onClick={() => setIsOnboardingOpen(false)}
                >
                  나중에 촬영할게요
                </Button>
              </div>
            </section>
          </div>
        )}
    </div>
  )
}
export default HomePage
