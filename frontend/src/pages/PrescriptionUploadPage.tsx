import { useId, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  executeOcr,
  getOcrJob,
  uploadPrescription,
  type OcrJobResponse,
} from '../api/prescriptions'
import { ApiError } from '../api/client'
import { Button, Card, MobileShell } from '../design-system/components'
import '../design-system/prototype.css'
import './MvpPages.css'

function PrescriptionUploadPage() {
  const navigate = useNavigate()
  const inputId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [ocrResult, setOcrResult] = useState<OcrJobResponse | null>(null)
  const [message, setMessage] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = event.target.files?.[0] ?? null
    setFile(selectedFile)
    setOcrResult(null)
    setMessage('')
  }

  const handleUpload = async () => {
    if (!file) {
      setMessage('처방전 파일을 선택해 주세요.')
      return
    }

    try {
      setIsLoading(true)
      setMessage('')
      setOcrResult(null)
      const uploadResponse = await uploadPrescription(file)
      const ocrResponse = await executeOcr(uploadResponse.data.document_id)
      const latestOcrResult = await getOcrJob(ocrResponse.data.job_id)
      setOcrResult(latestOcrResult)

      if (latestOcrResult.data.ocr_status === 'COMPLETED') {
        navigate(
          `/prescriptions/review?document_id=${uploadResponse.data.document_id}&job_id=${ocrResponse.data.job_id}`,
        )
      }
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : '처방전 처리 중 오류가 발생했습니다.',
      )
    } finally {
      setIsLoading(false)
    }
  }

  if (isLoading) {
    return (
      <div className="mvp-page">
        <MobileShell title="Dosey 도지" hideNavigation>
          <main className="app-scroll mvp-page__content mvp-page__content--no-nav mvp-processing" role="status">
            <div className="quality-illustration processing-document" aria-hidden="true" />
            <h1 className="mvp-page__title">처방전 내용을 확인하고 있어요</h1>
            <p className="mvp-page__description">
              파일 업로드 → 글자 인식 → 복약정보 구조화
            </p>
            <div className="processing-steps">
              <span>✓ 문서 업로드 확인</span>
              <span>● 약 이름과 복용법 인식 중</span>
              <span>○ 구조화 결과 확인</span>
            </div>
          </main>
        </MobileShell>
      </div>
    )
  }

  return (
    <div className="mvp-page">
      <MobileShell title="Dosey 도지" onBack={() => navigate('/')} hideNavigation>
        <main className="app-scroll mvp-page__content mvp-page__content--no-nav">
          <h1 className="mvp-page__title">처방전을 등록해 주세요</h1>
          <p className="mvp-page__description">
            촬영하거나 저장한 처방전을 읽은 뒤 원본과 인식 결과를 직접 비교합니다.
          </p>

          <Card className="mvp-upload__summary">
            <span className="brand-mark" aria-hidden="true">▧</span>
            <span>
              <strong>처방전</strong>
              <small>OCR 인식 · 복약 가이드 연결</small>
            </span>
          </Card>

          <input
            id={inputId}
            className="mvp-upload__input"
            type="file"
            accept="image/jpeg,image/png,application/pdf"
            onChange={handleFileChange}
          />
          <label className={`upload-zone mvp-upload__zone ${file ? 'selected' : ''}`} htmlFor={inputId}>
            <span className="mvp-upload__zone-icon" aria-hidden="true">▧</span>
            <strong>{file?.name ?? '사진 촬영 또는 파일 선택'}</strong>
            <small>{file ? '선택 완료 · 눌러서 변경' : 'JPG · PNG · PDF / 최대 10MB'}</small>
          </label>

          <div className="notice mvp-upload__notice">
            <strong>개인정보를 확인해 주세요.</strong><br />
            주민등록번호는 가리고 문서 전체가 선명하게 보이도록 촬영해 주세요.
          </div>

          {message && <p className="mvp-form__message" role="alert">{message}</p>}

          {ocrResult?.data.ocr_status === 'PENDING' && <p className="mvp-form__message">OCR 작업을 기다리고 있습니다.</p>}
          {ocrResult?.data.ocr_status === 'PROCESSING' && <p className="mvp-form__message">처방전을 인식하고 있습니다.</p>}
          {ocrResult?.data.ocr_status === 'FAILED' && <p className="mvp-form__message">처방전 인식에 실패했습니다.</p>}

          <Button fullWidth disabled={!file} onClick={handleUpload}>
            처방전 읽기
          </Button>
        </main>
      </MobileShell>
    </div>
  )
}

export default PrescriptionUploadPage
