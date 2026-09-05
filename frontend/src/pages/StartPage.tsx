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
              <span>Dose + Easy</span>
            </div>
            <DoseyMascot variant="welcome" />
            <div className="mvp-start__hero">
              <p>복약 도우미 도지와 함께</p>
              <h1>처방과 일정을 쉽게 살펴봐요.</h1>
            </div>
          </header>

          <ul className="mvp-start__features" aria-label="Dosey 주요 기능">
            <li>처방전 등록</li>
            <li>쉬운 가이드</li>
            <li>도지에게 질문</li>
            <li>복약 지속 도움</li>
          </ul>

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
