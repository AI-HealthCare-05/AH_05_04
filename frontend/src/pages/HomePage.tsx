import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CurrentUser } from '../api/users'
import { Card, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

function HomeShortcutIcon({ type }: { type: 'prescription' | 'guide' | 'chat' }) {
  if (type === 'prescription') {
    return (
      <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <rect x="7" y="6" width="18" height="20" rx="2" stroke="currentColor" strokeWidth="2.2" />
        <path d="M11 12h10M11 17h10M11 22h6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
    )
  }

  if (type === 'guide') {
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

function HomePage({ currentUser }: { currentUser: CurrentUser }) {
  const navigate = useNavigate()
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
    <div className="mvp-page mvp-home-page">
      <MobileShell
        title="Dosey 도지"
        brandMark={<DoseyMascot variant="header" />}
        activeNavigation="홈"
        disabledNavigation={['일정']}
        onNavigate={(item) => {
          if (item === '홈') navigate('/')
          if (item === '도지') navigate('/chat')
          if (item === '가이드') navigate('/guides')
          if (item === '메뉴') navigate('/profile')
        }}
      >
        <main className="app-scroll mvp-page__content">
          <div className="mvp-home__greeting-row">
            <p>
              안녕하세요,{' '}
              <strong>{userName ? `${userName}님!` : '도지 사용자님!'}</strong>
            </p>
            <time dateTime={new Date().toISOString()}>{today}</time>
          </div>
          <h1 className="mvp-page__title mvp-home__title">오늘도 건강한 하루 되세요</h1>
          <p className="mvp-home__intro">도지가 복약 생활을 함께 도와드릴게요.</p>
          {!userName && (
            <p className="mvp-home__greeting-status" role="status">
              사용자 이름을 불러오지 못했어요. 홈 기능은 계속 사용할 수 있어요.
            </p>
          )}

          <Card className="mvp-home__today-card">
            <div>
              <span className="mvp-home__eyebrow">오늘의 복약</span>
              <h2>복약 일정 연결을 준비하고 있어요</h2>
              <p>처방전 등록은 지금 사용할 수 있어요.</p>
            </div>
            <button type="button" onClick={() => navigate('/prescriptions/upload')}>
              처방전 등록하기
            </button>
          </Card>

          <section className="mvp-home__section" aria-labelledby="home-shortcuts-heading">
            <div className="mvp-home__section-title">
              <h2 id="home-shortcuts-heading" className="mvp-page__section-heading">
                필요한 바로가기
              </h2>
            </div>
            <div className="mvp-home__card-stack">
              <button
                className="mvp-home__hub-card mvp-home__hub-card--prescription"
                type="button"
                onClick={() => navigate('/prescriptions/upload')}
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
                className="mvp-home__hub-card mvp-home__hub-card--guide"
                type="button"
                onClick={() => navigate('/guides')}
              >
                <span className="mvp-home__hub-icon">
                  <HomeShortcutIcon type="guide" />
                </span>
                <span className="mvp-home__hub-copy">
                  <strong>복약 가이드</strong>
                  <small>복약 가이드 화면으로 이동해요.</small>
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
            </div>
          </section>

          <section className="mvp-home__section" aria-labelledby="home-records-heading">
            <div className="mvp-home__section-title">
              <h2 id="home-records-heading" className="mvp-page__section-heading">
                미확인 기록
              </h2>
              <span>준비 중</span>
            </div>
            <button
              className="mvp-home__pending-card"
              type="button"
              aria-label="미확인 복약 기록 (준비 중)"
              disabled
            >
              <span>
                <strong>확인이 필요한 복약 기록</strong>
                <small>기록 기능이 연결되면 이곳에서 바로 확인할 수 있어요.</small>
              </span>
              <em>준비 중</em>
            </button>
          </section>
        </main>
      </MobileShell>
    </div>
  )
}

export default HomePage
