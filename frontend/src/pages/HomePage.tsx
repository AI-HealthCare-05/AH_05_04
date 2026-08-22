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
      <MobileShell title="Dosey 도지" activeNavigation="홈">
        <main className="app-scroll mvp-page__content">
          <div className="mvp-home__date">{today}</div>
          <h1 className="mvp-page__title mvp-home__title">
            오늘 약도 챙겨볼까요?
          </h1>

          <Card className="mvp-page__hero">
            <small>첫 번째 할 일</small>
            <h2>처방전을 등록하고 내 복약 가이드를 받아보세요</h2>
            <p>등록한 처방은 원본과 대조한 뒤에만 가이드에 사용해요.</p>
            <Button fullWidth onClick={() => navigate('/prescriptions/upload')}>
              처방전 등록하기
            </Button>
          </Card>

          <h2 className="mvp-page__section-heading">바로가기</h2>
          <div className="mvp-home__quick-grid">
            <button
              className="mvp-home__quick-card"
              type="button"
              onClick={() => navigate('/prescriptions/upload')}
            >
              <strong>문서 등록</strong>
              <span>처방전 등록과 OCR 확인</span>
            </button>
            <button className="mvp-home__quick-card" type="button" disabled>
              <strong>복약 가이드</strong>
              <span>처방 확정 후 확인</span>
            </button>
          </div>

          <h2 className="mvp-page__section-heading">내 복약 흐름</h2>
          <Card>
            <div className="mvp-home__empty">
              <strong>아직 등록된 처방전이 없어요</strong>
              <p>처방전을 등록하면 확인한 정보로 가이드를 만들어요.</p>
            </div>
          </Card>
        </main>
      </MobileShell>
    </div>
  )
}

export default HomePage
