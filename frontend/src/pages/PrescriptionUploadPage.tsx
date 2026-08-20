import { useState } from 'react'
import {
  executeOcr,
  getOcrJob,
  uploadPrescription,
  type OcrJobResponse,
} from '../api/prescriptions'
import { ApiError } from '../api/client'

function PrescriptionUploadPage() {
  const [file, setFile] = useState<File | null>(null)
  const [ocrResult, setOcrResult] =
    useState<OcrJobResponse | null>(null)
  const [message, setMessage] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleFileChange = (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const selectedFile = event.target.files?.[0] ?? null

    setFile(selectedFile)
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

      const uploadResponse =
        await uploadPrescription(file)

      const ocrResponse = await executeOcr(
        uploadResponse.data.document_id,
      )

      const latestOcrResult = await getOcrJob(
        ocrResponse.data.job_id,
      )

      setOcrResult(latestOcrResult)

    } catch (error) {
      if (error instanceof ApiError) {
        setMessage(error.message)
      } else {
        setMessage('처방전 처리 중 오류가 발생했습니다.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main>
      <h1>처방전 업로드</h1>

      <input
        type="file"
        accept="image/jpeg,image/png"
        onChange={handleFileChange}
      />

      <button
        type="button"
        onClick={handleUpload}
        disabled={isLoading}
      >
        {isLoading ? '처리 중...' : '업로드 및 OCR 실행'}
      </button>

      {message && <p>{message}</p>}

      {ocrResult?.data.ocr_status === 'PENDING' && (
        <p>OCR 작업을 기다리고 있습니다.</p>
      )}

      {ocrResult?.data.ocr_status === 'PROCESSING' && (
        <p>처방전을 인식하고 있습니다.</p>
      )}

      {ocrResult?.data.ocr_status === 'COMPLETED' && (
        <section>
          <h2>OCR 결과</h2>
          <p>상태: COMPLETED</p>
          <p>
            추출 필드 수: {ocrResult.data.fields.length}
          </p>
        </section>
      )}

      {ocrResult?.data.ocr_status === 'FAILED' && (
        <p>처방전 인식에 실패했습니다.</p>
      )}

    </main>
  )
}

export default PrescriptionUploadPage
