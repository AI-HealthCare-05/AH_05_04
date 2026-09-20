import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { getCurrentUser, type CurrentUser } from '../api/users'
import {
  clearAuthenticatedSession,
  isStaleTokenError,
} from '../features/auth/authSession'
import HomePage from '../pages/HomePage'
import LoginPage from '../pages/LoginPage'
import SignupPage from '../pages/SignupPage'
import PrescriptionUploadPage from '../pages/PrescriptionUploadPage'
import PrescriptionReviewPage from '../pages/PrescriptionReviewPage'
import GuidePage from '../pages/GuidePage'
import ChatPage from '../pages/ChatPage'
import StartPage from '../pages/StartPage'
import ProfilePage from '../pages/ProfilePage'
import MenuPage from '../pages/MenuPage'
import { SchedulePage } from '../pages/SchedulePage'
import { MedicationRecordPage } from '../pages/MedicationRecordPage'
import { CheckinDetailPage, CheckinSummaryPage } from '../pages/CheckinPage'
import NotificationsPage from '../pages/NotificationsPage'
import UnconfirmedCheckinsPage from '../pages/UnconfirmedCheckinsPage'
import ReportPage from '../pages/ReportPage'
import NotificationSettingsPage from '../pages/NotificationSettingsPage'
import { useViewportHeight } from '../hooks/useViewportHeight'

const DevPreviewPage = import.meta.env.DEV
  ? lazy(() => import('../dev-preview/DevPreviewPage'))
  : null

const DesignPrototypePage = import.meta.env.DEV
  ? lazy(() => import('../pages/DesignPrototypePage'))
  : null

// PUBLIC_TRACK_C 공개 게이트. docs/release-gates/post-mvp-1-external-approvals.md의
// EXT-MED-001 · EXT-MED-002 · EXT-PRIV-002 · EXT-SAFETY-001 승인 전에는 false를 유지한다.
// 표현식을 그대로 두어야 Vite가 build 시점에 상수로 접어 production 번들에서
// route · page chunk · 진입 문구를 제거한다. 공유 상수로 빼지 않는다.
const TRACK_C_PUBLIC = import.meta.env.VITE_PUBLIC_TRACK_C === 'true' || import.meta.env.DEV

const TrackCPage = TRACK_C_PUBLIC
  ? lazy(() => import('../pages/TrackCPage'))
  : null

type AuthState =
  | { status: 'checking'; user: null }
  | { status: 'guest'; user: null }
  | { status: 'authenticated'; user: CurrentUser }

function hasAccessToken() {
  return Boolean(localStorage.getItem('access_token'))
}

function useAuthStatus(): AuthState {
  const [authState, setAuthState] = useState<AuthState>(() =>
    hasAccessToken()
      ? { status: 'checking', user: null }
      : { status: 'guest', user: null },
  )
  const currentUserRequest = useRef<Promise<CurrentUser> | null>(null)

  useEffect(() => {
    let isMounted = true

    if (!hasAccessToken()) {
      setAuthState({ status: 'guest', user: null })
      return undefined
    }

    async function verifyAccessToken() {
      try {
        const request = currentUserRequest.current ?? getCurrentUser()
        currentUserRequest.current = request
        const user = await request
        if (isMounted) setAuthState({ status: 'authenticated', user })
      } catch (error) {
        if (isStaleTokenError(error)) {
          clearAuthenticatedSession()
        }
        if (isMounted) setAuthState({ status: 'guest', user: null })
      }
    }

    void verifyAccessToken()

    return () => {
      isMounted = false
    }
  }, [])

  return authState
}

function AuthCheckingFallback() {
  return <div role="status">로그인 상태를 확인하는 중입니다.</div>
}

function RootRoute() {
  const authState = useAuthStatus()

  if (authState.status === 'checking') return <AuthCheckingFallback />
  return authState.status === 'authenticated' ? (
    <HomePage currentUser={authState.user} />
  ) : (
    <Navigate to="/start" replace />
  )
}

function PublicOnlyRoute({ children }: { children: ReactNode }) {
  const authState = useAuthStatus()

  if (authState.status === 'checking') return <AuthCheckingFallback />
  // 로그인된 사용자는 회원가입/로그인 화면으로 되돌아가지 않고 홈 화면을 봅니다.
  return authState.status === 'authenticated' ? <Navigate to="/" replace /> : children
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const authState = useAuthStatus()
  const location = useLocation()

  if (authState.status === 'checking') return <AuthCheckingFallback />
  // 회원 전용 화면은 화면 렌더링 전에 토큰 존재 여부를 먼저 확인합니다.
  return authState.status === 'authenticated' ? children : (
    <Navigate
      to="/login"
      replace
      state={{ returnTo: `${location.pathname}${location.search}` }}
    />
  )
}

export function AppRoutes({
  enableDevPreview = import.meta.env.DEV,
  enableDesignPrototype = import.meta.env.DEV,
  enableTrackC = TRACK_C_PUBLIC,
}: {
  enableDevPreview?: boolean
  enableDesignPrototype?: boolean
  enableTrackC?: boolean
} = {}) {
  return (
    <Routes>
      <Route path="/" element={<RootRoute />} />
      <Route path="/start" element={<PublicOnlyRoute><StartPage /></PublicOnlyRoute>} />
      <Route path="/signup" element={<PublicOnlyRoute><SignupPage /></PublicOnlyRoute>} />
      <Route path="/login" element={<PublicOnlyRoute><LoginPage /></PublicOnlyRoute>} />
      <Route path="/prescriptions/upload" element={<ProtectedRoute><PrescriptionUploadPage /></ProtectedRoute>} />
      {enableDesignPrototype && DesignPrototypePage && (
        <Route
          path="/design-prototype"
          element={
            <Suspense fallback={<div role="status">Prototype를 준비하고 있습니다.</div>}>
              <DesignPrototypePage />
            </Suspense>
          }
        />
      )}
      {enableDevPreview && DevPreviewPage && (
        <Route
          path="/dev/preview"
          element={
            <Suspense fallback={<div role="status">Preview를 준비하고 있습니다.</div>}>
              <DevPreviewPage />
            </Suspense>
          }
        />
      )}
      <Route path="/prescriptions/review" element={<ProtectedRoute><PrescriptionReviewPage /></ProtectedRoute>} />
      <Route path="/guides/:guideId" element={<ProtectedRoute><GuidePage /></ProtectedRoute>} />
      <Route path="/guides" element={<ProtectedRoute><GuidePage /></ProtectedRoute>} />
      <Route path="/chat" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
      {enableTrackC && TrackCPage && <>
        <Route path="/track-c/occurrences/:occurrenceId" element={<ProtectedRoute><Suspense fallback={<div role="status">불러오는 중입니다.</div>}><TrackCPage /></Suspense></ProtectedRoute>} />
        <Route path="/track-c/plans" element={<ProtectedRoute><Suspense fallback={<div role="status">불러오는 중입니다.</div>}><TrackCPage /></Suspense></ProtectedRoute>} />
        <Route path="/track-c/plans/:planId" element={<ProtectedRoute><Suspense fallback={<div role="status">불러오는 중입니다.</div>}><TrackCPage /></Suspense></ProtectedRoute>} />
      </>}
      <Route path="/schedule" element={<ProtectedRoute><SchedulePage /></ProtectedRoute>} />
      <Route
        path="/schedule/unconfirmed"
        element={<ProtectedRoute><UnconfirmedCheckinsPage /></ProtectedRoute>}
      />
      <Route
        path="/schedule/occurrences/:occurrenceId"
        element={<ProtectedRoute><CheckinDetailPage /></ProtectedRoute>}
      />
      <Route path="/schedule/checkin" element={<ProtectedRoute><CheckinSummaryPage /></ProtectedRoute>} />
      <Route path="/records" element={<ProtectedRoute><MedicationRecordPage /></ProtectedRoute>} />
      <Route path="/menu" element={<ProtectedRoute><MenuPage /></ProtectedRoute>} />
      <Route path="/profile" element={<ProtectedRoute><ProfilePage /></ProtectedRoute>} />
      <Route path="/notifications" element={<ProtectedRoute><NotificationsPage /></ProtectedRoute>} />
      <Route path="/settings/notifications" element={<ProtectedRoute><NotificationSettingsPage /></ProtectedRoute>} />
      <Route path="/report" element={<ProtectedRoute><ReportPage /></ProtectedRoute>} />
      <Route path="/report/clinic" element={<ProtectedRoute><ReportPage /></ProtectedRoute>} />
    </Routes>
  )
}

function AppRouter() {
  // Android keyboard close 후 남는 하단 공백을 막기 위해 앱 전체에서 1회만 동기화한다.
  useViewportHeight()

  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}

export default AppRouter
