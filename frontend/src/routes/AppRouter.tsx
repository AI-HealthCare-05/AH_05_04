import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { ApiError } from '../api/client'
import { getCurrentUser } from '../api/users'
import DesignPrototypePage from '../pages/DesignPrototypePage'
import HomePage from '../pages/HomePage'
import LoginPage from '../pages/LoginPage'
import SignupPage from '../pages/SignupPage'
import PrescriptionUploadPage from '../pages/PrescriptionUploadPage'
import PrescriptionReviewPage from '../pages/PrescriptionReviewPage'
import GuidePage from '../pages/GuidePage'
import ChatPage from '../pages/ChatPage'
import StartPage from '../pages/StartPage'
import ProfilePage from '../pages/ProfilePage'

type AuthStatus = 'checking' | 'guest' | 'authenticated'

function hasAccessToken() {
  return Boolean(localStorage.getItem('access_token'))
}

function isStaleTokenError(error: unknown) {
  return (
    error instanceof ApiError &&
    (error.status === 401 ||
      error.code === 'UNAUTHORIZED' ||
      error.code === 'INVALID_TOKEN' ||
      error.code === 'EXPIRED_TOKEN')
  )
}

function useAuthStatus(): AuthStatus {
  const [authStatus, setAuthStatus] = useState<AuthStatus>(() =>
    hasAccessToken() ? 'checking' : 'guest',
  )

  useEffect(() => {
    let isMounted = true

    if (!hasAccessToken()) {
      setAuthStatus('guest')
      return undefined
    }

    async function verifyAccessToken() {
      try {
        await getCurrentUser()
        if (isMounted) setAuthStatus('authenticated')
      } catch (error) {
        if (isStaleTokenError(error)) {
          localStorage.removeItem('access_token')
        }
        if (isMounted) setAuthStatus('guest')
      }
    }

    void verifyAccessToken()

    return () => {
      isMounted = false
    }
  }, [])

  return authStatus
}

function AuthCheckingFallback() {
  return <div role="status">로그인 상태를 확인하는 중입니다.</div>
}

function RootRoute() {
  const authStatus = useAuthStatus()

  if (authStatus === 'checking') return <AuthCheckingFallback />
  return authStatus === 'authenticated' ? <HomePage /> : <StartPage />
}

function PublicOnlyRoute({ children }: { children: ReactNode }) {
  const authStatus = useAuthStatus()

  if (authStatus === 'checking') return <AuthCheckingFallback />
  // 로그인된 사용자는 회원가입/로그인 화면으로 되돌아가지 않고 홈 화면을 봅니다.
  return authStatus === 'authenticated' ? <Navigate to="/" replace /> : children
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const authStatus = useAuthStatus()

  if (authStatus === 'checking') return <AuthCheckingFallback />
  // 회원 전용 화면은 화면 렌더링 전에 토큰 존재 여부를 먼저 확인합니다.
  return authStatus === 'authenticated' ? children : <Navigate to="/login" replace />
}

function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<RootRoute />} />
        <Route path="/start" element={<StartPage />} />
        <Route path="/signup" element={<PublicOnlyRoute><SignupPage /></PublicOnlyRoute>} />
        <Route path="/login" element={<PublicOnlyRoute><LoginPage /></PublicOnlyRoute>} />
        <Route path="/prescriptions/upload" element={<ProtectedRoute><PrescriptionUploadPage /></ProtectedRoute>} />
        <Route path="/design-prototype" element={<DesignPrototypePage />} />
        <Route path="/prescriptions/review" element={<ProtectedRoute><PrescriptionReviewPage /></ProtectedRoute>} />
        <Route path="/guides/:guideId" element={<ProtectedRoute><GuidePage /></ProtectedRoute>} />
        <Route path="/guides" element={<ProtectedRoute><GuidePage /></ProtectedRoute>} />
        <Route path="/chat" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
        <Route path="/profile" element={<ProtectedRoute><ProfilePage /></ProtectedRoute>} />
      </Routes>
    </BrowserRouter>
  )
}

export default AppRouter
