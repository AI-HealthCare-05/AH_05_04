import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { createIdempotencyKey } from '../api/idempotency'
import { resolveLogicalMutationAttempt, type LogicalMutationAttempt } from '../api/logicalMutationAttempt'
import { getMedicationOccurrencesByDate } from '../api/medicationOccurrences'
import type { MedicationCheckinResponse } from '../api/medicationCheckins'
import * as api from '../api/trackC'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import { Button, Card, MobileShell } from '../design-system/components'
import './TrackCPage.css'

const choices: [api.BarrierCode, string][] = [
  ['FORGOT', '깜빡했어요'], ['SCHEDULE_OR_TRAVEL', '일정이나 이동 때문에 어려웠어요'],
  ['INSTRUCTIONS_UNCLEAR', '복용 방법이 헷갈렸어요'], ['NEED_DOUBT', '복용이 필요한지 궁금했어요'],
  ['MEDICATION_CONCERN', '약에 대한 걱정이 있었어요'], ['ACCESS_OR_COST', '약을 구하거나 비용을 감당하기 어려웠어요'],
]
const supportNames: Record<api.SupportCode, string> = {
  REMINDER_SETUP: '복약 일정과 알림 확인', ROUTINE_OR_TRAVEL_PLAN: '일상·이동 중 복약 계획 확인',
  INSTRUCTION_REVIEW: '복용 방법 확인', PURPOSE_REVIEW: '복용 목적 확인',
  MEDICATION_CONCERN_GUIDANCE: '약에 대한 걱정 확인', ACCESS_SUPPORT: '약 접근·비용 도움 확인',
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const services = { ...api, getDay: getMedicationOccurrencesByDate }
export type TrackCServices = typeof services

// Each route gets fresh memory-only attempts; never store health data or keys in browser storage.
export default function TrackCPage({ service = services }: { service?: TrackCServices }) {
  const location = useLocation()
  return <TrackCFlow key={location.pathname + location.search} service={service} />
}

function TrackCFlow({ service }: { service: TrackCServices }) {
  const { occurrenceId = '', planId = '' } = useParams()
  const [query] = useSearchParams()
  const date = query.get('date') ?? ''
  const navigate = useNavigate()
  const [step, setStep] = useState<'loading' | 'safety' | 'barrier' | 'offer' | 'plan' | 'blocked'>('loading')
  const [checkin, setCheckin] = useState<MedicationCheckinResponse['data'] | null>(null)
  const [safety, setSafety] = useState<api.Safety | null>(null)
  const [barrier, setBarrier] = useState<api.Barrier | null>(null)
  const [offer, setOffer] = useState<api.Offer | null>(null)
  const [plan, setPlan] = useState<api.Plan | null>(null)
  const [selected, setSelected] = useState<api.BarrierCode | ''>('')
  const [confirmed, setConfirmed] = useState(false)
  const [terminal, setTerminal] = useState<'COMPLETED' | 'CANCELLED' | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState<(() => Promise<void>) | null>(null)
  const lock = useRef(false)
  const alive = useRef(true)
  const heading = useRef<HTMLHeadingElement>(null)
  const attempt = useRef<LogicalMutationAttempt<string, unknown> | null>(null)
  const back = occurrenceId && uuid.test(occurrenceId)
    ? `/schedule/occurrences/${occurrenceId}?date=${encodeURIComponent(date)}` : '/schedule'

  useEffect(() => { heading.current?.focus() }, [step])
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const fail = useCallback((cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) {
      clearAuthenticatedSession()
      navigate('/login', { replace: true })
    } else if (cause instanceof ApiError && [403, 404, 409].includes(cause.status)) {
      setStep('blocked')
      setRetry(null)
      setError(cause.status === 409
        ? '기록이나 계획 상태가 변경되었어요. 이전 응답으로 계속 진행할 수 없어요. 복약 기록으로 돌아가 현재 상태를 확인해 주세요.'
        : '이 기록을 사용할 수 없어요. 일정에서 기록을 다시 확인해 주세요.')
    } else {
      setError(cause instanceof ApiError && cause.status === 422
        ? '입력 내용을 확인해 주세요. 저장이 확인되지 않았어요.'
        : '저장 또는 조회 결과를 확인하지 못했어요. 연결을 확인한 뒤 같은 요청을 다시 시도해 주세요.')
    }
  }, [navigate])

  const run = useCallback(async (work: () => Promise<void>) => {
    if (lock.current) return
    lock.current = true
    setBusy(true); setError(''); setRetry(null)
    try { await work() } catch (cause) {
      if (alive.current) { setRetry(() => work); fail(cause) }
    } finally {
      lock.current = false
      if (alive.current) setBusy(false)
    }
  }, [fail])

  function key(operation: string, target: string, payload: unknown) {
    attempt.current = resolveLogicalMutationAttempt(attempt.current, operation, target, payload,
      checkin?.revision ?? 0, () => createIdempotencyKey('track-c'))
    return attempt.current.idempotencyKey
  }

  useEffect(() => {
    async function load() {
      if (planId) {
        if (!uuid.test(planId)) throw new ApiError(404, '')
        const result = await service.getPlan(planId)
        if (alive.current) { setPlan(result); setStep('plan') }
      } else {
        if (!uuid.test(occurrenceId) || !/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date) throw new ApiError(404, '')
        const result = await service.getDay(date)
        const row = result.data.occurrences.find(item => item.occurrence_id === occurrenceId && item.scheduled_local_date === date)
        if (!row || row.status === 'CANCELLED' || row.checkin?.status !== 'NOT_TAKEN') throw new ApiError(409, '')
        if (alive.current) { setCheckin(row.checkin); setStep('safety') }
      }
    }
    void run(load)
  }, [service, occurrenceId, planId, date, run])

  async function submitSafety() {
    if (!checkin) return
    const body: api.SafetyRequest = { medication_checkin_id: checkin.checkin_id, checkin_revision: checkin.revision, symptom_codes: [], expected_revision: 0 }
    const result = await service.createSafety(body, key('safety', checkin.checkin_id, body))
    if (!alive.current) return
    if (result.medication_checkin_id !== checkin.checkin_id || result.checkin_revision !== checkin.revision || result.response_level !== 'ROUTINE' || result.safety_disposition !== 'NORMAL') {
      setStep('blocked'); return
    }
    setSafety(result); setStep('barrier')
  }

  async function submitBarrier(code: api.BarrierCode | null) {
    if (!checkin || !safety) return
    const body: api.BarrierRequest = { response_status: code ? 'ANSWERED' : 'DECLINED', barrier_code: code, checkin_revision: checkin.revision, expected_revision: 0 }
    const result = await service.putBarrier(checkin.checkin_id, body, key('barrier', checkin.checkin_id, body))
    if (!alive.current) return
    if (result.medication_checkin_id !== checkin.checkin_id || result.checkin_revision !== checkin.revision || result.safety_assessment_id !== safety.assessment_id) throw new ApiError(409, '')
    setBarrier(result)
    // Replaying this write after a lost GET response retains the same key.
    const offered = await service.getOffers(result.barrier_response_id)
    if (!alive.current) return
    if (offered.barrier_response_id !== result.barrier_response_id || offered.medication_checkin_id !== checkin.checkin_id || offered.checkin_revision !== checkin.revision || offered.safety_assessment_id !== safety.assessment_id || offered.supports.length > 1 || (offered.supports.length === 0 && offered.reason_code !== 'NO_ELIGIBLE_SUPPORT') || (offered.supports.length === 1 && offered.reason_code !== null)) throw new ApiError(409, '')
    setOffer(offered); setConfirmed(false); setStep('offer')
  }

  async function savePlan() {
    const item = offer?.supports[0]
    if (!barrier || !item || !confirmed) return
    const body: api.CreatePlanRequest = { barrier_response_id: barrier.barrier_response_id, support_code: item.support_code, rule_version: item.rule_version, copy_version: item.copy_version, confirmed: true }
    const result = await service.createPlan(body, key('create-plan', barrier.barrier_response_id, body))
    if (alive.current) navigate(`/dev/track-c/plans/${result.support_action_plan_id}`, { replace: true })
    // Destination always GETs current status; creation replay is only a saved snapshot.
  }

  async function changePlan() {
    if (!plan || !terminal || !confirmed) return
    const body: api.PatchPlanRequest = { status: terminal, confirmed: true }
    await service.patchPlan(plan.support_action_plan_id, body, key('patch-plan', plan.support_action_plan_id, body))
    const result = await service.getPlan(plan.support_action_plan_id)
    if (alive.current) { setPlan(result); setTerminal(null); setConfirmed(false) }
  }

  const item = offer?.supports[0]
  const reminderTarget = plan?.support_code === 'REMINDER_SETUP' &&
    'prescription_version_medication_id' in plan.action_config_snapshot.parameters
    ? plan.action_config_snapshot.parameters.prescription_version_medication_id : null
  return <div className="mvp-page track-c-page"><MobileShell title="복약 도움" hideNavigation onBack={() => navigate(back)}>
    <main className="app-scroll track-c-content" aria-busy={busy}>
      <p className="track-c-eyebrow">개발 환경 · 합성 데이터 연결 확인</p>
      <h1 ref={heading} tabIndex={-1}>{step === 'barrier' ? '이번에는 어떤 점이 가장 크게 영향을 주었나요?' : step === 'plan' ? '실천 계획' : '복약 도움 확인'}</h1>
      {error && <p role="alert">{error}</p>}
      {retry && <Button disabled={busy} onClick={() => void run(retry)}>같은 요청 다시 시도</Button>}
      {step === 'loading' && <p role="status">기록을 확인하고 있어요.</p>}
      {step === 'safety' && <Card>
        <h2>증상 경험 여부를 먼저 확인해 주세요</h2>
        <p>증상이 없음을 직접 확인한 경우에만 다음 단계로 진행해요.</p>
        <Button fullWidth disabled={busy || !!retry} onClick={() => void run(submitSafety)}>증상이 없어요</Button>
        <Button fullWidth variant="secondary" disabled={busy} onClick={() => { setRetry(null); setStep('blocked') }}>증상이 있거나 확실하지 않아요</Button>
      </Card>}
      {step === 'blocked' && <Card><h2>현재 도움을 계속 진행할 수 없어요</h2><p>복약 기록은 그대로 유지돼요.</p><Button fullWidth onClick={() => navigate(back)}>복약 기록으로 돌아가기</Button>{planId && <Button fullWidth variant="secondary" onClick={() => { window.location.reload() }}>계획 상태 다시 조회</Button>}</Card>}
      {step === 'barrier' && <>
        <p>하나만 골라주세요. 답하지 않아도 복약 상태는 그대로 저장돼요.</p>
        <fieldset disabled={busy || !!retry}><legend>이번 복용의 어려움</legend>{choices.map(([code, label]) => <label className="track-c-choice" key={code}><input type="radio" name="barrier" value={code} checked={selected === code} onChange={() => setSelected(code)} /><span>{label}</span></label>)}</fieldset>
        <Button fullWidth disabled={!selected || busy || !!retry} onClick={() => void run(() => submitBarrier(selected || null))}>선택한 어려움으로 도움 찾기</Button>
        <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => void run(() => submitBarrier(null))}>답하지 않고 계속하기</Button>
      </>}
      {step === 'offer' && (item ? <Card>
        <h2>{item.support_copy.title}</h2><p>{item.support_copy.body}</p>
        <label className="track-c-choice"><input type="checkbox" disabled={busy || !!retry} checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /><span>{item.support_copy.confirmation_prompt}</span></label>
        <Button fullWidth disabled={!confirmed || busy || !!retry} onClick={() => void run(savePlan)}>{item.support_copy.primary_label}</Button>
        <Button fullWidth variant="secondary" disabled={busy} onClick={() => navigate(back)}>{item.support_copy.secondary_label}</Button>
      </Card> : <Card><h2>지금 제안할 수 있는 도움이 없어요</h2><p>복약 기록과 응답은 저장되어 있어요.</p><Button fullWidth onClick={() => navigate(back)}>복약 기록으로 돌아가기</Button></Card>)}
      {step === 'plan' && plan && <Card>
        <h2>{supportNames[plan.support_code]}</h2>
        <p role="status">{plan.status === 'ACTIVE' ? '진행 중' : plan.status === 'COMPLETED' ? '완료됨' : '취소됨'}</p>
        <p>계획 조회만으로 실행이나 완료가 처리되지 않아요.</p>
        {plan.status === 'ACTIVE' && <>
          {plan.support_code === 'REMINDER_SETUP' && <p><Link to={`/schedule?support_medication=${encodeURIComponent(reminderTarget ?? '')}`} target="_blank" rel="noopener noreferrer">일정 확인·설정 (새 탭)</Link></p>}
          {!terminal ? <>
            <Button fullWidth disabled={busy || !!retry} onClick={() => { setTerminal('COMPLETED'); setConfirmed(false) }}>완료 확인하기</Button>
            <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => { setTerminal('CANCELLED'); setConfirmed(false) }}>계획 취소하기</Button>
          </> : <>
            <label className="track-c-choice"><input type="checkbox" checked={confirmed} disabled={busy || !!retry} onChange={e => setConfirmed(e.target.checked)} /><span>{terminal === 'CANCELLED' ? '이 계획을 취소할게요.' : plan.support_code === 'REMINDER_SETUP' ? '기존 복약 일정을 확인했거나 일정 저장을 마쳤어요.' : '선택한 실천 계획의 실행을 마쳤어요.'}</span></label>
            <Button fullWidth disabled={!confirmed || busy || !!retry} onClick={() => void run(changePlan)}>{terminal === 'COMPLETED' ? '완료로 저장' : '취소로 저장'}</Button>
            <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => { setTerminal(null); setConfirmed(false) }}>돌아가기</Button>
          </>}
        </>}
      </Card>}
      {busy && <p role="status">처리 중이에요.</p>}
    </main>
  </MobileShell></div>
}
