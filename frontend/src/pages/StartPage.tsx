import { Link, useNavigate } from 'react-router-dom'
import { Button, MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'

function StartPage() {
  const navigate = useNavigate()

  return (
    <div className="mvp-page mvp-start-page">
      <MobileShell hideHeader hideNavigation>
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-start">
          <header className="mvp-start__brand">
            <div>
              <strong>Dosey</strong>
              <span>도지</span>
            </div>
            <span className="brand-mark mvp-start__brand-mark" aria-hidden="true">
              <span className="brand-mark__ring" />
            </span>
          </header>

          <section className="mvp-start__hero">
            <p>내 처방을 이해하고 함께하는</p>
            <h1>AI 복약 파트너</h1>
          </section>

          <div className="mvp-start__features" aria-label="Dosey 주요 기능">
            <span>처방전 등록</span>
            <span>쉬운 가이드</span>
            <span>복약 챗봇 도지</span>
            <span>복약 지속 도움</span>
          </div>

          <div className="notice attention mvp-start__notice">
            처방전 확인, 복약 가이드와 복약 질문 기능을 제공합니다.
          </div>

          <Button fullWidth onClick={() => navigate('/signup')}>
            회원가입하고 시작하기
          </Button>

          <p className="mvp-form__footer mvp-start__login">
            이미 계정이 있나요? <Link to="/login">로그인</Link>
          </p>
        </main>
      </MobileShell>
    </div>
  )
}

export default StartPage
