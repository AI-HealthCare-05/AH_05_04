import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { createIdempotencyKey } from '../api/idempotency'
import type { Followup, FollowupResponse, SubmitFollowupRequest, getFollowup, getPlan, submitFollowup } from '../api/trackC'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import { Button, Card } from '../design-system/components'

type Services = { getFollowup: typeof getFollowup; submitFollowup: typeof submitFollowup; getPlan: typeof getPlan }
const choices: [FollowupResponse, string][] = [
  ['HELPED', '도움이 됐어요'], ['NOT_HELPED', '도움이 되지 않았어요'], ['NOT_SURE', '아직 모르겠어요'],
]
const label = (response: FollowupResponse) => choices.find(([value]) => value === response)?.[1]

export default function PlanFollowup({ planId, service, onClose }: { planId: string; service: Services; onClose: () => void }) {
  const navigate = useNavigate()
  const [saved, setSaved] = useState<Followup | null>(null)
  const [selected, setSelected] = useState<FollowupResponse | ''>('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(true)
  const [blocked, setBlocked] = useState(false)
  const [retry, setRetry] = useState<'load' | 'submit' | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const alive = useRef(true)
  const lock = useRef(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const attempt = useRef<{ body: SubmitFollowupRequest; key: string } | null>(null)

  const fail = useCallback((cause: unknown, action: 'load' | 'submit') => {
    if (cause instanceof ApiError && cause.status === 401) {
      clearAuthenticatedSession(); navigate('/login', { replace: true }); return
    }
    if (cause instanceof ApiError && [403, 404, 409].includes(cause.status)) {
      setBlocked(true); setRetry(null)
      setError('이 계획의 후기를 저장할 수 없어요. 계획 상태를 다시 확인해 주세요.')
    } else {
      setRetry(action)
      setError(action === 'submit' ? '저장 결과를 확인하지 못했어요. 같은 요청으로 다시 시도해 주세요.' : '현재 후기를 불러오지 못했어요. 다시 조회해 주세요.')
    }
  }, [navigate])

  const load = useCallback(async () => {
    const plan = await service.getPlan(planId)
    if (plan.support_action_plan_id !== planId || plan.status !== 'COMPLETED') throw new ApiError(409, '')
    const result = await service.getFollowup(planId)
    if (result && result.support_action_plan_id !== planId) throw new ApiError(404, '')
    if (alive.current) { setSaved(result); setSelected(''); setEditing(result === null); setRetry(null) }
  }, [planId, service])

  const reload = useCallback(async () => {
    if (lock.current) return
    lock.current = true; setBusy(true); setError('')
    try { await load() } catch (cause) { if (alive.current) fail(cause, 'load') }
    finally { lock.current = false; if (alive.current) setBusy(false) }
  }, [load, fail])

  useEffect(() => {
    alive.current = true; heading.current?.focus(); void reload()
    return () => { alive.current = false }
  }, [reload])

  async function submit() {
    if (lock.current || blocked || (!selected && !attempt.current)) return
    lock.current = true; setBusy(true); setError(''); setNotice('')
    const request = attempt.current ?? {
      body: { response: selected as FollowupResponse, expected_revision: saved?.revision ?? 0 },
      key: createIdempotencyKey('track-c-followup'),
    }
    attempt.current = request
    let readOnlyRetry = false
    try {
      try {
        await service.submitFollowup(planId, request.body, request.key)
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.code !== 'ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT') throw cause
        attempt.current = null; readOnlyRetry = true
        if (alive.current) setNotice('다른 화면에서 후기가 변경됐어요. 최신 후기를 확인한 뒤 다시 선택해 주세요.')
        await load()
        return
      }
      // A replay is an old snapshot. Show the current GET result, never the POST snapshot.
      attempt.current = null; readOnlyRetry = true
      await load()
      if (alive.current) setNotice('저장 후 현재 후기를 다시 확인했어요.')
    } catch (cause) { if (alive.current) fail(cause, readOnlyRetry ? 'load' : 'submit') }
    finally { lock.current = false; if (alive.current) setBusy(false) }
  }

  return <Card>
    <h2 ref={heading} tabIndex={-1}>선택한 방법이 도움이 되었나요?</h2>
    <p>실천 계획에 대한 후기예요. 약의 효과나 복용 여부를 평가하는 질문은 아니에요.</p>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">후기를 확인하고 있어요.</p>}
    {!busy && !blocked && !retry && <>
      {saved && <p>저장된 후기: {label(saved.response)}</p>}
      {editing ? <>
        <fieldset disabled={busy}><legend>계획 사용 후기</legend>
          {choices.map(([value, text]) => <label className="track-c-choice" key={value}>
            <input type="radio" name="plan-followup" value={value} checked={selected === value} onChange={() => setSelected(value)} />
            <span>{text}</span>
          </label>)}
        </fieldset>
        <Button fullWidth disabled={!selected || busy} onClick={() => void submit()}>{saved ? '후기 수정 저장' : '후기 저장'}</Button>
      </> : <Button fullWidth onClick={() => { setEditing(true); setSelected(''); setNotice('') }}>후기 수정하기</Button>}
    </>}
    {retry && <Button fullWidth disabled={busy} onClick={() => void (retry === 'submit' ? submit() : reload())}>{retry === 'submit' ? '같은 후기 다시 저장' : '후기 다시 조회'}</Button>}
    <Button fullWidth variant="secondary" disabled={busy} onClick={onClose}>{editing && !saved && !retry ? '나중에' : '계획으로 돌아가기'}</Button>
  </Card>
}
