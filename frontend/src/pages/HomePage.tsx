import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import type { CurrentUser } from '../api/users'
import bellIcon from '../assets/icon-bell-notification.svg'
import { Button, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

function HomeShortcutIcon({ type }: { type: 'prescription' | 'otc' | 'chat' }) {
  if (type === 'prescription') {
    return (
      <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <rect x="7" y="7" width="18" height="18" stroke="currentColor" strokeWidth="2.2" />
        <rect x="13" y="13" width="6" height="6" fill="currentColor" />
      </svg>
    )
  }

  if (type === 'otc') {
    return (
      <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <circle cx="16" cy="16" r="8" fill="currentColor" />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx="8" cy="16" r="2.5" fill="currentColor" />
      <circle cx="16" cy="16" r="2.5" fill="currentColor" />
      <circle cx="24" cy="16" r="2.5" fill="currentColor" />
    </svg>
  )
}

function HomeAdherenceCard() {
  return (
    <section
      className="mvp-home__adherence-card"
      aria-labelledby="home-adherence-heading"
    >
      <div className="mvp-home__adherence-header">
        <h2 id="home-adherence-heading">이번 주 복약 달성도</h2>
        <button type="button" aria-label="상세 보기 (준비 중)" disabled>
          상세 보기 &gt;
        </button>
      </div>
      <strong className="mvp-home__adherence-status">집계 준비 중</strong>
      <div
        className="mvp-home__adherence-progress"
        aria-label="이번 주 복약 달성도 집계 준비 중"
      >
        <span className="mvp-home__adherence-track" aria-hidden="true" />
        <DoseyMascot variant="progress" />
      </div>
      <p>복약 기록이 쌓이면 주간 달성도를 보여드려요.</p>
    </section>
  )
}

function HomePage({ currentUser }: { currentUser: CurrentUser }) {
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

  const onboardingDialogRef = useRef<HTMLElement>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const homePrescriptionButtonRef = useRef<HTMLButtonElement>(null)
  const wasOnboardingOpenRef = useRef(false)

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
            aria-label="알림 (준비 중)"
            disabled
          >
            <img src={bellIcon} alt="" aria-hidden="true" />
            <span aria-hidden="true" />
          </button>
        }
        activeNavigation="홈"
        disabledNavigation={['일정']}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/menu')
        }}
      >
        <main className="app-scroll mvp-page__content">
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

          <section className="mvp-home__card-stack" aria-label="Home 주요 기능">
            <button
              ref={homePrescriptionButtonRef}
              className="mvp-home__hub-card mvp-home__hub-card--prescription"
              type="button"
              onClick={() => navigate('/prescriptions/upload', {
                state: { intent: 'new-prescription' },
              })}
            >
              <span className="mvp-home__hub-icon">
                <HomeShortcutIcon type="prescription" />
              </span>
              <span className="mvp-home__hub-copy">
                <strong>처방약 복용 안내</strong>
                <small>처방전을 등록하고 복약 가이드를 확인해보세요.</small>
              </span>
              <span className="mvp-home__hub-arrow" aria-hidden="true">›</span>
            </button>
            <button
              className="mvp-home__hub-card mvp-home__hub-card--otc"
              type="button"
              aria-label="일반의약품 안내 (준비 중)"
              disabled
            >
              <span className="mvp-home__hub-icon">
                <HomeShortcutIcon type="otc" />
              </span>
              <span className="mvp-home__hub-copy">
                <strong>일반의약품 안내</strong>
                <small>
                  궁금한 일반 의약품과 처방된 약을 함께 먹어도 되는지 확인해보세요.
                </small>
              </span>
              <span className="mvp-home__hub-arrow" aria-hidden="true">›</span>
            </button>
            <button
              className="mvp-home__hub-card mvp-home__hub-card--doji"
              type="button"
              onClick={() => navigate('/chat')}
            >
              <span className="mvp-home__hub-icon">
                <HomeShortcutIcon type="chat" />
              </span>
              <span className="mvp-home__hub-copy">
                <strong>도지에게 질문하기</strong>
                <small>복약 중 궁금한 점을 도지와 대화해보세요.</small>
              </span>
              <span className="mvp-home__hub-arrow" aria-hidden="true">›</span>
            </button>
          </section>

          <HomeAdherenceCard />
        </main>
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
