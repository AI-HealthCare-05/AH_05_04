import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'

function HomePage() {
  const navigate = useNavigate()
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
    <div className="mvp-page">
      <MobileShell
        title="Dosey 도지"
        activeNavigation="홈"
        onNavigate={(item) => {
          if (item === '가이드') navigate('/guides')
        }}
      >
        <main className="app-scroll mvp-page__content">
          <div className="mvp-home__date">{today}</div>
          <h1 className="mvp-page__title mvp-home__title">
            오늘 약도 챙겨볼까요?
          </h1>

          <Card className="mvp-page__hero">
            <small>첫 번째 할 일</small>
            <h2>처방전을 등록하고 내 복약 가이드를 받아보세요</h2>
            <Button fullWidth onClick={() => navigate('/prescriptions/upload')}>
              처방전 등록하기
            </Button>
            <button className="mvp-home__hero-link" type="button">
              건강정보 입력하기
            </button>
          </Card>

          <div className="mvp-home__section-title">
            <h2 className="mvp-page__section-heading">바로가기</h2>
            <span>자주 쓰는 기능</span>
          </div>
          <div className="mvp-home__quick-grid">
            <button
              className="mvp-home__quick-card"
              type="button"
              onClick={() => navigate('/prescriptions/upload')}
            >
              <strong>문서 등록</strong>
            </button>
            <button className="mvp-home__quick-card" type="button">
              <strong>복약 챗봇</strong>
            </button>
            <button className="mvp-home__quick-card" type="button">
              <strong>복약 일정</strong>
            </button>
            <button className="mvp-home__quick-card" type="button">
              <strong>일반 의약품</strong>
            </button>
          </div>

          <Card className="mvp-home__flow-card">
            <h2>이번 주 복약 흐름</h2>
            <div className="mvp-home__empty">
              <span className="mvp-home__empty-mark" aria-hidden="true" />
              <strong>표시할 복약 기록이 아직 없어요</strong>
              <p>처방전을 등록하면 확인된 정보가 여기에 표시돼요.</p>
            </div>
          </Card>
        </main>
      </MobileShell>
    </div>
  )
}

export default HomePage
