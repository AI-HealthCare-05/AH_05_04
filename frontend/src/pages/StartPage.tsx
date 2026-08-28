import { Link, useNavigate } from 'react-router-dom'
import { Button, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

function StartPage() {
  const navigate = useNavigate()

  return (
    <div className="mvp-page mvp-start-page">
      <MobileShell hideHeader hideNavigation>
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-start">
          <header className="mvp-start__welcome">
            <div className="mvp-start__brand">
              <strong>Dosey</strong>
              <span>도지</span>
            </div>
            <DoseyMascot variant="welcome" />
            <div className="mvp-start__hero">
              <p>내 처방을 이해하고 함께하는</p>
              <h1>AI 복약 파트너</h1>
            </div>
          </header>

          <div className="mvp-start__features" aria-label="Dosey 주요 기능">
            <span>처방전 등록</span>
            <span>쉬운 가이드</span>
            <span>복약 챗봇 도지</span>
            <span>복약 지속 도움</span>
          </div>

          <div className="notice attention mvp-start__notice">
            <strong>AI가 처방을 바꾸지 않아요.</strong>
            <span>확인된 처방과 출처 범위에서만 안내합니다.</span>
          </div>

          <Button fullWidth onClick={() => navigate('/signup')}>
            회원가입하고 시작하기
          </Button>

          <Link className="mvp-start__login" to="/login">
            이미 계정이 있어요 · 로그인
          </Link>
        </main>
      </MobileShell>
    </div>
  )
}

export default StartPage
