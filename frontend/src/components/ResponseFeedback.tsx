import { useId, useRef, useState } from 'react'
import { deleteFeedback, submitFeedback, type FeedbackRating, type FeedbackTarget } from '../api/feedback'
import './ResponseFeedback.css'

export function ResponseFeedback({ target }: { target: FeedbackTarget }) {
  const [rating, setRating] = useState<FeedbackRating | null>(null)
  const [comment, setComment] = useState('')
  const [saved, setSaved] = useState<FeedbackRating | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)
  const id = useId()

  async function send(remove = false) {
    if (lock.current || (!remove && !rating)) return
    lock.current = true
    setBusy(true)
    setNotice('')
    setError('')
    try {
      if (remove) {
        await deleteFeedback(target)
        setSaved(null)
        setRating(null)
        setComment('')
        setNotice('피드백을 삭제했어요.')
      } else if (rating) {
        const response = await submitFeedback(target, rating, comment)
        setSaved(response.data.rating)
        setNotice('피드백을 저장했어요. 언제든 평가를 바꿀 수 있어요.')
      }
    } catch {
      setError(remove ? '삭제하지 못했어요. 다시 시도해 주세요.' : '저장하지 못했어요. 입력한 내용을 확인하고 다시 시도해 주세요.')
    } finally {
      lock.current = false
      setBusy(false)
    }
  }

  return (
    <section className="response-feedback" aria-label="답변 피드백" aria-busy={busy}>
      <p>도움이 되었나요?</p>
      <div className="response-feedback__ratings">
        {([['POSITIVE', '👍 도움이 됐어요'], ['NEGATIVE', '👎 아쉬워요']] as const).map(([value, label]) => (
          <button key={value} type="button" disabled={busy} aria-pressed={rating === value}
            onClick={() => { setRating(value); setNotice(''); setError('') }}>
            {label}
          </button>
        ))}
      </div>
      {rating && (
        <form onSubmit={(event) => { event.preventDefault(); void send() }}>
          <label htmlFor={id}>의견 (선택)</label>
          <textarea id={id} value={comment} maxLength={1000} disabled={busy} rows={3}
            aria-describedby={`${id}-notice`}
            onChange={(event) => { setComment(event.target.value); setNotice('') }} />
          <p id={`${id}-notice`} className="response-feedback__notice">
            피드백은 답변 품질 개선에 사용됩니다. 의견 입력은 선택이며,
            개인정보·건강정보·처방전 원문은 입력하지 마세요.
            최초 제출 후 최대 30일 보관하며, 제출하지 않아도 서비스 이용에 영향이 없습니다.
          </p>
          <div className="response-feedback__ratings">
            <button type="submit" disabled={busy}>{busy ? '처리 중…' : '피드백 보내기'}</button>
            <button type="button" disabled={busy} onClick={() => void send(true)}>피드백 삭제</button>
          </div>
        </form>
      )}
      {saved && <p className="response-feedback__notice">저장된 평가: {saved === 'POSITIVE' ? '도움이 됐어요' : '아쉬워요'}</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
