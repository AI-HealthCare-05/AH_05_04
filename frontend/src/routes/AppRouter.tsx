import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
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

function hasAccessToken() {
  return Boolean(localStorage.getItem('access_token'))
}

function RootRoute() {
  return hasAccessToken() ? <HomePage /> : <StartPage />
}

function PublicOnlyRoute({ children }: { children: ReactNode }) {
  // 로그인된 사용자는 회원가입/로그인 화면으로 되돌아가지 않고 홈 화면을 봅니다.
  return hasAccessToken() ? <Navigate to="/" replace /> : children
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  // 회원 전용 화면은 화면 렌더링 전에 토큰 존재 여부를 먼저 확인합니다.
  return hasAccessToken() ? children : <Navigate to="/login" replace />
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
