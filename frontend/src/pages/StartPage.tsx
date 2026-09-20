import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Button, Card, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

type StartLocationState = {
  accountWithdrawalAccepted?: boolean
  accountWithdrawalCompleted?: boolean
  accountWithdrawalFailed?: boolean
}

function StartPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const startState = location.state as StartLocationState | null
  const accountWithdrawalCompleted = Boolean(
    startState?.accountWithdrawalCompleted || startState?.accountWithdrawalAccepted,
  )
  const accountWithdrawalFailed = Boolean(startState?.accountWithdrawalFailed)

  // 실패 detail을 성공(COMPLETED)으로 재해석하지 않고 분기는 그대로 유지한다.
  // 다만 이 시점에 계정 이용은 이미 종료되어 재로그인이 불가능하므로,
  // 사용자에게는 실제 상태와 일치하는 안내를 보여준다.
  if (accountWithdrawalFailed) {
    return (
      <div className="mvp-page mvp-start-page">
        <MobileShell hideHeader hideNavigation>
          <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-start mvp-start--withdrawal-complete">
            <Card className="mvp-start__withdrawal-status">
              <div role="status" aria-live="polite">
                <h1>회원탈퇴가 처리되었습니다.</h1>
                <p>
                  계정 이용이 종료되었습니다. 개인정보와 건강정보의 삭제·보존은
                  서비스 정책에 따라 처리됩니다.
                </p>
              </div>
              <Button fullWidth onClick={() => navigate('/start', { replace: true })}>
                시작 화면으로 이동
              </Button>
            </Card>
          </main>
        </MobileShell>
      </div>
    )
  }

  if (accountWithdrawalCompleted) {
    return (
      <div className="mvp-page mvp-start-page">
        <MobileShell hideHeader hideNavigation>
          <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-start mvp-start--withdrawal-complete">
            <Card className="mvp-start__withdrawal-status">
              <div role="status" aria-live="polite">
                <h1>회원탈퇴가 완료되었습니다.</h1>
                <p>
                  계정 이용이 종료되었습니다. 개인정보와 건강정보의 삭제·보존은
                  서비스 정책에 따라 처리됩니다.
                </p>
              </div>
              <Button fullWidth onClick={() => navigate('/start', { replace: true })}>
                시작 화면으로 이동
              </Button>
            </Card>
          </main>
        </MobileShell>
      </div>
    )
  }

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
            {[
              '처방전 등록',
              '쉬운 가이드',
              '도지에게 질문',
              '복약 지속 도움',
            ].map((feature) => (
              <li key={feature}>
                <span className="mvp-start__feature-check" aria-hidden="true">
                  ✓
                </span>
                <span>{feature}</span>
              </li>
            ))}
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
