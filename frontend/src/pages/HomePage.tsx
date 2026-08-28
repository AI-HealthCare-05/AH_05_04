import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getCurrentUser } from '../api/users'
import { Button, Card, MobileShell } from '../design-system/components'
import { DoseyMascot } from '../design-system/DoseyMascot'
import '../design-system/prototype.css'
import './MvpPages.css'

function HomePage() {
  const navigate = useNavigate()
  const [userName, setUserName] = useState<string | null>(null)
  const [userNameLoadFailed, setUserNameLoadFailed] = useState(false)
  const today = useMemo(
    () =>
      new Intl.DateTimeFormat('ko-KR', {
        month: 'long',
        day: 'numeric',
        weekday: 'long',
      }).format(new Date()),
    [],
  )

  useEffect(() => {
    let isMounted = true

    async function loadUserName() {
      try {
        const user = await getCurrentUser()
        const nextName = user.name.trim()
        if (!isMounted) return
        if (nextName) {
          setUserName(nextName)
        } else {
          setUserNameLoadFailed(true)
        }
      } catch {
        if (isMounted) setUserNameLoadFailed(true)
      }
    }

    void loadUserName()

    return () => {
      isMounted = false
    }
  }, [])

  return (
    <div className="mvp-page mvp-home-page">
      <MobileShell
        title="Dosey 도지"
        brandMark={<DoseyMascot variant="header" />}
        activeNavigation="홈"
        disabledNavigation={['일정']}
        onNavigate={(item) => {
          if (item === '홈') {
            navigate('/')
          }
          if (item === '가이드') {
            navigate('/guides')
          }
          if (item === '메뉴') {
            navigate('/profile')
          }
        }}
      >
        <main className="app-scroll mvp-page__content">
          <div className="mvp-home__date">기기 기준 {today}</div>
          <h1 className="mvp-page__title mvp-home__title" aria-live="polite">
            {userName
              ? `${userName}님, 오늘 복용할 약을 확인해 주세요`
              : '오늘 복용할 약을 확인해 주세요'}
          </h1>
          {!userName && (
            <p className="mvp-home__greeting-status" role="status">
              {userNameLoadFailed
                ? '사용자 이름을 불러오지 못했어요. 홈 기능은 계속 사용할 수 있어요.'
                : '사용자 이름을 불러오는 중이에요.'}
            </p>
          )}

          <Card className="mvp-page__hero">
            <small>첫 번째 할 일</small>
            <h2>처방전을 등록하고 내 복약 가이드를 만들어 주세요</h2>
            <Button fullWidth onClick={() => navigate('/prescriptions/upload')}>
              처방전 등록하기
            </Button>
            <button
              className="mvp-home__hero-link"
              type="button"
              aria-label="건강정보 입력하기 (준비 중)"
              disabled
            >
              건강정보 입력하기
              <span>준비 중</span>
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
            <button
              className="mvp-home__quick-card"
              type="button"
              onClick={() => navigate('/chat')}
            >
              <strong>복약 챗봇 도지</strong>
            </button>
            <button
              className="mvp-home__quick-card"
              type="button"
              aria-label="복약 일정 (준비 중)"
              disabled
            >
              <strong>복약 일정</strong>
              <span>준비 중</span>
            </button>
            <button
              className="mvp-home__quick-card"
              type="button"
              aria-label="다른 약 물어보기 (준비 중)"
              disabled
            >
              <strong>다른 약 물어보기</strong>
              <span>준비 중</span>
            </button>
          </div>

          <Card className="mvp-home__flow-card">
            <h2>이번 주 복약 흐름</h2>
            <div className="mvp-home__empty">
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
