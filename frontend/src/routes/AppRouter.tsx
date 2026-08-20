import { BrowserRouter, Route, Routes } from 'react-router-dom'
import DesignPrototypePage from '../pages/DesignPrototypePage'
import HomePage from '../pages/HomePage'
import LoginPage from '../pages/LoginPage'
import SignupPage from '../pages/SignupPage'
import PrescriptionUploadPage from '../pages/PrescriptionUploadPage'

function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/prescriptions/upload" element={<PrescriptionUploadPage />} />
        <Route path="/design-prototype" element={<DesignPrototypePage />} />
      </Routes>
    </BrowserRouter>
  )
}

export default AppRouter
