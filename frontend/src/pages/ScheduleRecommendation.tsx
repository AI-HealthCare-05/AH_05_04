import { useEffect, useRef, useState } from 'react'
import {
  getScheduleRecommendation,
  type RecommendationContext,
  type RecommendationInput,
  type RecommendationResponse,
} from '../api/medicationSchedules'
import { Button } from '../design-system/components'

const meals = [['BREAKFAST', '아침'], ['LUNCH', '점심'], ['DINNER', '저녁']] as const
const reasons = {
  EXPLICIT_AFTER_MEAL: '확정 처방에 적힌 식후 간격을 입력한 식사 종료 시각에 더했어요.',
  UNSUPPORTED_INSTRUCTION: '처방에 식사 종류와 식후 몇 분인지 명확히 적힌 경우만 계산할 수 있어요. 복용 시간을 직접 확인해 주세요.',
  FREQUENCY_MISMATCH: '처방의 식사 종류와 하루 복용 횟수가 맞지 않아 후보를 제시하지 않아요.',
  MISSING_MEAL_END: '처방에 해당하는 식사의 종료 시각을 입력해 주세요.',
  DAY_BOUNDARY: '계산한 시각이 다음 날이어서 후보를 제시하지 않아요. 날짜와 복용 시간을 직접 확인해 주세요.',
  DUPLICATE_TIME: '계산한 복용 시각이 겹쳐 후보를 제시하지 않아요.',
}

export function ScheduleRecommendation({
  medicationId, medicationName, timingText, disabled, onApply, onInvalidate, onManual,
  load = getScheduleRecommendation,
}: {
  medicationId: string
  medicationName: string
  timingText: string | null
  disabled: boolean
  onApply: (times: string[], context: RecommendationContext) => void
  onInvalidate: () => void
  onManual: () => void
  load?: typeof getScheduleRecommendation
}) {
  const [anchors, setAnchors] = useState<RecommendationInput['meal_end_times']>({})
  const [regular, setRegular] = useState(false)
  const [candidate, setCandidate] = useState<RecommendationResponse['data'] | null>(null)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const sequence = useRef(0)
  useEffect(() => () => { sequence.current += 1 }, [])

  function invalidate() {
    sequence.current += 1
    setCandidate(null)
    setPending(false)
    setMessage('')
    onInvalidate()
  }
  async function calculate() {
    const attempt = ++sequence.current
    setPending(true)
    setCandidate(null)
    setMessage('')
    onInvalidate()
    try {
      const response = await load(medicationId, { meal_end_times: anchors, same_times_every_day: true })
      if (sequence.current !== attempt) return
      if (response.data.prescription_version_medication_id !== medicationId) throw new Error('identity mismatch')
      setCandidate(response.data)
    } catch {
      if (sequence.current === attempt) setMessage('후보를 확인하지 못했어요. 최신 처방과 연결 상태를 확인한 뒤 다시 시도해 주세요.')
    } finally {
      if (sequence.current === attempt) setPending(false)
    }
  }
  return (
    <section className="schedule-recommendation" aria-label={`${medicationName} 시간 후보`}>
      <h3>처방 기준 시간 후보</h3>
      <p>확정 처방 원문: {timingText || '복용 시점 정보 없음'}</p>
      <p>처방에 명시된 식후 간격만 계산해요. 식사 구간의 끝이 아닌 실제 식사를 마치는 시각을 입력해 주세요.</p>
      <div className="schedule-editor__time-grid">
        {meals.map(([key, label]) => (
          <label key={key}>
            <span>{label} 종료</span>
            <input type="time" aria-label={`${medicationName} ${label} 식사 종료 시각`}
              value={anchors[key] ?? ''} disabled={disabled}
              onChange={(event) => {
                invalidate()
                const next = { ...anchors }
                if (event.target.value) next[key] = event.target.value
                else delete next[key]
                setAnchors(next)
              }} />
          </label>
        ))}
      </div>
      <label>
        <input type="checkbox" checked={regular} disabled={disabled}
          onChange={(event) => { invalidate(); setRegular(event.target.checked) }} />
        복용 기간 동안 매일 같은 시각에 식사를 마쳐요
      </label>
      <p>요일마다 식사 시각이 다르거나 식사를 거르는 날에는 이 후보를 사용할 수 없어요.</p>
      <Button type="button" variant="secondary" disabled={disabled || !regular || pending} onClick={() => void calculate()}>
        {pending ? '계산 중…' : '시간 후보 계산'}
      </Button>
      {candidate && <div role="status">
        <p>{reasons[candidate.reason]}</p>
        {candidate.local_times.length > 0 && <>
          <strong>{candidate.local_times.join(' · ')}</strong>
          <p>입력한 식사 종료 시각과 처방의 식후 간격이 유지되어야 해요. 식사 시각을 수정하면 다시 계산해 주세요.</p>
          <Button type="button" disabled={disabled || pending} onClick={() => {
            onApply(candidate.local_times, { meal_end_times: anchors, same_times_every_day: true, rule_version: candidate.rule_version })
            setMessage('후보를 입력에 적용했어요. 날짜와 시간을 확인한 뒤 일정 저장을 눌러 주세요.')
          }}>후보 적용</Button>
        </>}
      </div>}
      <Button type="button" variant="secondary" disabled={disabled} onClick={() => {
        sequence.current += 1
        setCandidate(null)
        setPending(false)
        onManual()
        setMessage('직접 입력으로 전환했어요. 입력된 시각은 추천 검증 대상이 아니므로 처방에 맞는지 직접 확인해 주세요.')
      }}>직접 입력으로 전환</Button>
      {message && <p role="status">{message}</p>}
    </section>
  )
}
