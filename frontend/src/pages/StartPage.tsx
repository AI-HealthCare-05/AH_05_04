import { useNavigate } from 'react-router-dom'
import { Button, Card, MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'

function StartPage() {
  const navigate = useNavigate()

  return (
    <div className="mvp-page mvp-start-page">
      <MobileShell title="Dosey 도지" hideNavigation>
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-start">
          <Card className="mvp-start__compact">
            <strong>Dosey 도지</strong>
            <span>내 처방을 이해하고 함께하는 AI 복약 파트너</span>
          </Card>

          <Card className="mvp-start__hero">
            <span>처방전을 이해하는 가장 쉬운 방법</span>
            <h1>
              내 약을 알고,
              <br />
              <em>매일 잘 챙기는</em>
              <br />
              건강 습관
            </h1>
          </Card>

          <div className="mvp-start__features" aria-label="Dosey 주요 기능">
            <Card>처방전 등록</Card>
            <Card>쉬운 가이드</Card>
            <Card>복약 질문</Card>
          </div>

          <div className="notice attention mvp-start__notice">
            처방전 확인, 복약 가이드와 복약 질문 기능을 제공합니다.
          </div>

          <Button fullWidth onClick={() => navigate('/signup')}>
            회원가입하고 시작하기
          </Button>
        </main>
      </MobileShell>
    </div>
  )
}

export default StartPage
