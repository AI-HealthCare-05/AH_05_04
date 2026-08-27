import { BrowserRouter, Route, Routes } from 'react-router-dom'
import DesignPrototypePage from '../pages/DesignPrototypePage'
import HomePage from '../pages/HomePage'
import LoginPage from '../pages/LoginPage'
import SignupPage from '../pages/SignupPage'
import PrescriptionUploadPage from '../pages/PrescriptionUploadPage'
import PrescriptionReviewPage from '../pages/PrescriptionReviewPage'
import GuidePage from '../pages/GuidePage'
import ChatPage from '../pages/ChatPage'
import StartPage from '../pages/StartPage'

function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/start" element={<StartPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/prescriptions/upload" element={<PrescriptionUploadPage />} />
        <Route path="/design-prototype" element={<DesignPrototypePage />} />
        <Route path="/prescriptions/review" element={<PrescriptionReviewPage />} />
        <Route path="/guides/:guideId" element={<GuidePage />} />
        <Route path="/guides" element={<GuidePage />} />
        <Route path="/chat" element={<ChatPage />} />
      </Routes>
    </BrowserRouter>
  )
}

export default AppRouter
