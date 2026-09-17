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
import { inspectWebPushState, type WebPushState } from '../features/push/webPush'
import PlanFollowup from '../components/PlanFollowup'
import './TrackCPage.css'

const choices: [api.BarrierCode, string][] = [
  ['FORGOT', '깜빡했어요'], ['SCHEDULE_OR_TRAVEL', '일정이나 외출 때문에 어려웠어요'],
  ['INSTRUCTIONS_UNCLEAR', '복용 방법이 헷갈렸어요'], ['NEED_DOUBT', '약이 꼭 필요한지 모르겠어요'],
  ['MEDICATION_CONCERN', '약에 대한 걱정이 있었어요'], ['ACCESS_OR_COST', '약이 없거나 구하기 어려웠어요'],
]
const supportNames: Record<api.SupportCode, string> = {
  REMINDER_SETUP: '복약 일정과 알림 확인', ROUTINE_OR_TRAVEL_PLAN: '일상·이동 중 복약 계획 확인',
  INSTRUCTION_REVIEW: '복용 방법 확인', PURPOSE_REVIEW: '복용 목적 확인',
  MEDICATION_CONCERN_GUIDANCE: '약에 대한 걱정 확인', ACCESS_SUPPORT: '약 접근·비용 도움 확인',
}
const subreasonChoices: Record<api.BarrierCode, [api.SubreasonCode, string][]> = {
  FORGOT: [['MISSED_ALERT', '알림을 보거나 듣지 못했어요'], ['POSTPONED', '미뤘다가 잊었어요'], ['MEDICATION_CONFUSION', '약이나 복용 회차가 헷갈렸어요']],
  SCHEDULE_OR_TRAVEL: [['SCHEDULE_CHANGED', '생활 일정이 바뀌었어요'], ['MEDICATION_NOT_WITH_ME', '약을 가지고 나오지 않았어요'], ['PREPARATION_DIFFICULT', '미리 준비하기 어려웠어요']],
  INSTRUCTIONS_UNCLEAR: [['DOSE_AMOUNT_UNCLEAR', '한 번에 먹을 수량이 헷갈려요'], ['TIMING_OR_FOOD_UNCLEAR', '시간이나 식사 조건이 헷갈려요'], ['MEDICATION_IDENTITY_UNCLEAR', '약을 구분하기 어려워요'], ['LANGUAGE_TOO_COMPLEX', '설명이 길거나 어려워요']],
  NEED_DOUBT: [['NO_SYMPTOMS', '현재 증상이 없어요'], ['NO_NOTICEABLE_EFFECT', '효과를 체감하지 못해요'], ['VALUES_IMPROVED', '검사 수치가 좋아졌어요'], ['NEED_UNCLEAR', '왜 필요한지 잘 모르겠어요']],
  MEDICATION_CONCERN: [['LONG_TERM_USE', '장기간 복용이 걱정돼요'], ['DEPENDENCE_OR_TOLERANCE', '의존성이나 내성이 걱정돼요'], ['BODY_HARM', '몸에 해가 될까 걱정돼요'], ['PILL_BURDEN', '복용하는 약이 많아 부담돼요'], ['CONFLICTING_INFORMATION', '인터넷·지인 정보와 안내가 달라요']],
  ACCESS_OR_COST: [['RUNNING_LOW', '약이 곧 떨어져요'], ['REFILL_MISSED', '재처방 시기를 놓쳤어요'], ['VISIT_DIFFICULT', '병원이나 약국 방문이 어려워요'], ['COST_BURDEN', '비용이 부담돼요']],
}
// Only this historical copy predates the explicit packing plan. New copy versions retain it.
const LEGACY_TRAVEL_COPY = 'track-c-support-copy-ko-2026-09-15.1'
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const services = { ...api, getPushState: inspectWebPushState, getDay: getMedicationOccurrencesByDate }
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
  const [step, setStep] = useState<'loading' | 'safety' | 'barrier' | 'subreason' | 'travel' | 'offer' | 'plan' | 'blocked'>('loading')
  const [checkin, setCheckin] = useState<MedicationCheckinResponse['data'] | null>(null)
  const [safety, setSafety] = useState<api.Safety | null>(null)
  const [barrier, setBarrier] = useState<api.Barrier | null>(null)
  const [offer, setOffer] = useState<api.Offer | null>(null)
  const [showFollowup, setShowFollowup] = useState(false)
  const [plan, setPlan] = useState<api.Plan | null>(null)
  const [resourcesError, setResourcesError] = useState(false)
  const [resources, setResources] = useState<api.PlanResources | null>(null)
  const [pushState, setPushState] = useState<WebPushState | null>(null)
  const [selected, setSelected] = useState<api.BarrierCode | ''>('')
  const [travelSituation, setTravelSituation] = useState<api.TravelSituation | undefined>()
  const [subreason, setSubreason] = useState<api.SubreasonCode | undefined>()
  const [selectedSupport, setSelectedSupport] = useState<api.SupportCode | undefined>()
  const [selectedQuestions, setSelectedQuestions] = useState<string[]>([])
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

  const loadResources = useCallback(async (currentPlan: api.Plan) => {
    try {
      const details = await service.getPlanResources(currentPlan.support_action_plan_id)
      if (details.support_action_plan_id !== currentPlan.support_action_plan_id) throw new ApiError(409, '')
      if (alive.current) { setResources(details); setResourcesError(false) }
    } catch (cause) {
      // Authentication, ownership and stale-flow errors still stop the entire flow.
      if (cause instanceof ApiError && [401, 403, 404, 409].includes(cause.status)) throw cause
      if (alive.current) { setResources(null); setResourcesError(true) }
    }
  }, [service])

  useEffect(() => {
    async function load() {
      if (planId) {
        if (!uuid.test(planId)) throw new ApiError(404, '')
        const result = await service.getPlan(planId)
        await loadResources(result)
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
  }, [service, occurrenceId, planId, date, run, loadResources])

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
    if (!code) {
      await loadOffers(result); return
    }
    if (code === 'SCHEDULE_OR_TRAVEL') {
      setTravelSituation(undefined); setStep('travel'); return
    }
    setSubreason(undefined); setStep('subreason')
  }

  async function loadOffers(result: api.Barrier, situation?: api.TravelSituation, selectedSubreason?: api.SubreasonCode) {
    if (!checkin || !safety) return
    const offered = await service.getOffers(result.barrier_response_id, situation, selectedSubreason)
    if (!alive.current) return
    if (offered.barrier_response_id !== result.barrier_response_id || offered.medication_checkin_id !== checkin.checkin_id || offered.checkin_revision !== checkin.revision || offered.safety_assessment_id !== safety.assessment_id || offered.supports.length > 2 || (offered.supports.length === 0 && offered.reason_code !== 'NO_ELIGIBLE_SUPPORT') || (offered.supports.length > 0 && offered.reason_code !== null)) throw new ApiError(409, '')
    setOffer(offered); setSubreason(selectedSubreason ?? situation); setSelectedSupport(offered.supports[0]?.support_code); setSelectedQuestions([]); setConfirmed(false); setStep('offer')
  }

  async function savePlan() {
    const item = offer?.supports.find(candidate => candidate.support_code === selectedSupport)
    if (!barrier || !item || !confirmed) return
    const body: api.CreatePlanRequest = { barrier_response_id: barrier.barrier_response_id, support_code: item.support_code, rule_version: item.rule_version, copy_version: item.copy_version, confirmed: true, selected_question_ids: selectedQuestions, ...(subreason ? { subreason_code: subreason } : {}), ...(travelSituation ? { travel_situation: travelSituation } : {}) }
    const result = await service.createPlan(body, key('create-plan', barrier.barrier_response_id, body))
    if (alive.current) navigate(`/dev/track-c/plans/${result.support_action_plan_id}`, { replace: true })
    // Destination always GETs current status; creation replay is only a saved snapshot.
  }

  async function changePlan() {
    if (!plan || !terminal || !confirmed || (terminal === 'COMPLETED' && !resources)) return
    if (terminal === 'COMPLETED' && needsPushSetup) {
      const current = await service.getPushState()
      if (!alive.current) return
      setPushState(current)
      if (current !== pushState) { setConfirmed(false); return }
    }
    const body: api.PatchPlanRequest = { status: terminal, confirmed: true }
    await service.patchPlan(plan.support_action_plan_id, body, key('patch-plan', plan.support_action_plan_id, body))
    const result = await service.getPlan(plan.support_action_plan_id)
    if (alive.current) { setPlan(result); setTerminal(null); setConfirmed(false) }
  }

  const item = offer?.supports.find(candidate => candidate.support_code === selectedSupport)
  const needsPushSetup = plan?.support_code === 'REMINDER_SETUP' && resources?.barrier_code === 'FORGOT'
  const instructionPlan = plan?.support_code === 'INSTRUCTION_REVIEW'
  const purposePlan = plan?.support_code === 'PURPOSE_REVIEW'
  const concernPlan = plan?.support_code === 'MEDICATION_CONCERN_GUIDANCE'
  const packingPlan = plan?.support_code === 'ROUTINE_OR_TRAVEL_PLAN' && plan.copy_version !== LEGACY_TRAVEL_COPY
  const reminderTarget = plan?.support_code === 'REMINDER_SETUP' &&
    'prescription_version_medication_id' in plan.action_config_snapshot.parameters
    ? plan.action_config_snapshot.parameters.prescription_version_medication_id : null
  return <div className="mvp-page track-c-page"><MobileShell activeNavigation="일정" onBack={() => navigate(back)} onNavigate={item => {
    const destinations = { 홈: '/', 일정: '/schedule', 도지: '/chat', 가이드: '/guides', 메뉴: '/menu' }
    navigate(destinations[item])
  }}>
    <main className={`app-scroll track-c-content track-c-content--${step}`} aria-busy={busy}>
      {step === 'safety' && <p className="track-c-eyebrow">복약 안전 확인</p>}
      {step === 'offer' && <p className="track-c-eyebrow">나에게 맞는 도움</p>}
      {step === 'plan' && plan && <p className={`track-c-status track-c-status--${plan.status.toLowerCase()}`} role="status">{plan.status === 'ACTIVE' ? '진행 중' : plan.status === 'COMPLETED' ? '완료됨' : '취소됨'}</p>}
      <h1 ref={heading} tabIndex={-1}>{step === 'barrier' ? '이번에는 어떤 점이 가장 크게 영향을 주었나요?' : step === 'subreason' ? '어떤 상황에 더 가까웠나요?' : step === 'safety' ? '현재 불편한 증상이 있나요?' : step === 'offer' ? '도움 방법을 확인해 주세요' : step === 'plan' ? '내 실천 계획' : '복약 도움 확인'}</h1>
      {error && <p role="alert">{error}</p>}
      {retry && <Button disabled={busy} onClick={() => void run(retry)}>같은 요청 다시 시도</Button>}
      {step === 'loading' && <p role="status">기록을 확인하고 있어요.</p>}
      {step === 'safety' && <>
        <p className="track-c-description">복용하지 못한 이유를 확인하기 전에 현재 몸 상태를 먼저 확인할게요.</p>
        <div className="track-c-safety-actions">
          <Button fullWidth variant="secondary" disabled={busy} onClick={() => { setRetry(null); setStep('blocked') }}>증상이 있어요</Button>
          <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => void run(submitSafety)}>증상은 없어요</Button>
        </div>
        <p className="track-c-help">확실하지 않다면 증상이 있어요를 선택해 주세요.</p>
      </>}
      {step === 'blocked' && <Card><h2>현재 도움을 계속 진행할 수 없어요</h2><p>복약 기록은 그대로 유지돼요.</p><Button fullWidth onClick={() => navigate(back)}>복약 기록으로 돌아가기</Button>{planId && <Button fullWidth variant="secondary" onClick={() => { window.location.reload() }}>계획 상태 다시 조회</Button>}</Card>}
      {step === 'barrier' && <>
        <p>하나만 골라주세요. 답하지 않아도 복약 상태는 그대로 저장돼요.</p>
        <fieldset disabled={busy || !!retry}><legend className="track-c-sr-only">이번 복용의 어려움</legend>{choices.map(([code, label]) => <label className="track-c-choice" key={code}><input type="radio" name="barrier" value={code} checked={selected === code} onChange={() => setSelected(code)} /><span>{label}</span></label>)}</fieldset>
        <Button fullWidth disabled={!selected || busy || !!retry} onClick={() => void run(() => submitBarrier(selected || null))}>선택한 어려움으로 도움 찾기</Button>
        <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => void run(() => submitBarrier(null))}>답하지 않고 복약 상태만 저장</Button>
      </>}
      {step === 'subreason' && barrier?.barrier_code && <>
        <p>가장 가까운 상황 하나를 골라주세요. 세부 이유를 저장하지 않고 일반 도움을 볼 수도 있어요.</p>
        <fieldset disabled={busy || !!retry}><legend className="track-c-sr-only">복약이 어려웠던 구체적인 상황</legend>{subreasonChoices[barrier.barrier_code].map(([code, label]) => <label className="track-c-choice" key={code}><input type="radio" name="subreason" value={code} checked={subreason === code} onChange={() => setSubreason(code)} /><span>{label}</span></label>)}</fieldset>
        <Button fullWidth disabled={!subreason || busy || !!retry} onClick={() => { if (subreason) void run(() => loadOffers(barrier, undefined, subreason)) }}>선택한 상황으로 도움 찾기</Button>
        <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => void run(() => loadOffers(barrier))}>세부 이유 없이 도움 보기</Button>
      </>}
      {step === 'travel' && <Card>
        <h2>어떤 상황이었나요?</h2>
        <fieldset disabled={busy || !!retry}>
          <legend>일정 변경·외출 상황</legend>
          <label className="track-c-choice"><input type="radio" name="travel" checked={travelSituation === 'SCHEDULE_CHANGED'} onChange={() => { setTravelSituation('SCHEDULE_CHANGED'); setSubreason('SCHEDULE_CHANGED') }} /><span>생활 일정이 바뀌었어요</span></label>
          <label className="track-c-choice"><input type="radio" name="travel" checked={travelSituation === 'MEDICATION_NOT_WITH_ME'} onChange={() => { setTravelSituation('MEDICATION_NOT_WITH_ME'); setSubreason('MEDICATION_NOT_WITH_ME') }} /><span>약을 가지고 나오지 않았어요</span></label>
          <label className="track-c-choice"><input type="radio" name="travel" checked={subreason === 'PREPARATION_DIFFICULT'} onChange={() => { setTravelSituation(undefined); setSubreason('PREPARATION_DIFFICULT') }} /><span>외출 준비가 어려웠어요</span></label>
        </fieldset>
        <Button fullWidth disabled={(!travelSituation && subreason !== 'PREPARATION_DIFFICULT') || busy || !!retry} onClick={() => { if (barrier) void run(() => loadOffers(barrier, travelSituation, subreason ?? travelSituation)) }}>선택한 상황으로 도움 찾기</Button>
        <Button fullWidth variant="secondary" disabled={busy} onClick={() => navigate(back)}>나중에</Button>
      </Card>}
      {step === 'offer' && (item ? <>
        {offer && offer.supports.length > 1 && <fieldset disabled={busy || !!retry}><legend>도움 방법을 하나 골라주세요</legend>{offer.supports.map(candidate => <label className="track-c-choice" key={candidate.support_code}><input type="radio" name="support" checked={selectedSupport === candidate.support_code} onChange={() => { setSelectedSupport(candidate.support_code); setSelectedQuestions([]); setConfirmed(false) }} /><span>{candidate.support_copy.title}</span></label>)}</fieldset>}
        <Card className="track-c-offer"><h2>{item.support_copy.title}</h2><p>{item.support_copy.body}</p>{item.questions.length > 0 && <fieldset><legend>상담 때 확인할 질문을 하나 이상 골라주세요</legend>{item.questions.map(question => <label className="track-c-choice" key={question.question_id}><input type="checkbox" disabled={!selectedQuestions.includes(question.question_id) && selectedQuestions.length >= 3} checked={selectedQuestions.includes(question.question_id)} onChange={event => { setSelectedQuestions(current => event.target.checked ? [...current, question.question_id] : current.filter(id => id !== question.question_id)); setConfirmed(false) }} /><span>{question.text}</span></label>)}</fieldset>}</Card>
        <div className="track-c-actions">
          <label className="track-c-choice"><input type="checkbox" disabled={busy || !!retry} checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /><span>{item.support_copy.confirmation_prompt}</span></label>
          <Button fullWidth disabled={!confirmed || (item.questions.length > 0 && selectedQuestions.length === 0) || busy || !!retry} onClick={() => void run(savePlan)}>{item.support_copy.primary_label}</Button>
          <Button fullWidth variant="secondary" disabled={busy} onClick={() => navigate(back)}>{item.support_copy.secondary_label}</Button>
        </div>
      </> : <Card><h2>지금 제안할 수 있는 도움이 없어요</h2><p>복약 기록과 응답은 저장되어 있어요.</p><Button fullWidth onClick={() => navigate(back)}>복약 기록으로 돌아가기</Button></Card>)}
      {step === 'plan' && plan && <>
        {plan.status === 'COMPLETED' && (showFollowup
          ? <PlanFollowup key={plan.support_action_plan_id} planId={plan.support_action_plan_id} service={service} onClose={() => setShowFollowup(false)} />
          : <Button fullWidth onClick={() => setShowFollowup(true)}>도움 사용 후기</Button>)}
        <Card className="track-c-plan-field"><p>방법</p><h2>{packingPlan ? '다음 외출 전 약 챙기기' : supportNames[plan.support_code]}</h2></Card>
        {resources && <p>{resources.support_copy.body}</p>}
        {resourcesError && <>
          <p role="alert">계획 안내를 불러오지 못했어요. 상태 확인과 취소는 가능하며, 완료하려면 안내를 다시 조회해 주세요.</p>
          <Button variant="secondary" disabled={busy} onClick={() => void run(() => loadResources(plan))}>안내 다시 조회</Button>
        </>}
        {(instructionPlan || purposePlan) && resources && <>
          <p><Link to={`/schedule/occurrences/${encodeURIComponent(resources.occurrence_id)}?date=${encodeURIComponent(resources.occurrence_local_date)}`} target="_blank" rel="noopener noreferrer">이 기록의 확인된 약 정보 보기 (새 탭)</Link></p>
          {instructionPlan && <><p>현재 설정한 일정은 승인된 복용법 설명과 구분해서 확인해 주세요.</p><p><Link to={`/schedule?support_medication=${encodeURIComponent(resources.prescription_version_medication_id)}`} target="_blank" rel="noopener noreferrer">현재 복약 일정 확인 (새 탭)</Link></p></>}
          <p>이 약에 연결된 {instructionPlan ? '복용법' : '복용 목적'} 설명과 근거를 아직 제공할 수 없어요. 필요한 내용은 약사나 의료진에게 확인해 주세요.</p>
        </>}
        {resources && resources.selected_questions.length > 0 && <Card><h2>상담 때 확인할 질문</h2><ul>{resources.selected_questions.map(question => <li key={question.question_id}>{question.text}</li>)}</ul><p>질문은 자동으로 전송되지 않아요.</p></Card>}
        {concernPlan && plan.status === 'ACTIVE' && <Button variant="secondary" disabled={busy} onClick={() => { setTerminal(null); setConfirmed(false); setStep('blocked') }}>증상이 생겼거나 확실하지 않아요</Button>}
        <p>계획 조회만으로 실행이나 완료가 처리되지 않아요.</p>
        {plan.status === 'ACTIVE' && <div className="track-c-actions">
          {resources && plan.support_code === 'REMINDER_SETUP' && <p><Link to={`/schedule?support_medication=${encodeURIComponent(reminderTarget ?? '')}`} target="_blank" rel="noopener noreferrer">일정 확인·설정 (새 탭)</Link></p>}
          {needsPushSetup && <>
            <p><Link to="/settings/notifications" target="_blank" rel="noopener noreferrer">이 기기 알림 설정 (새 탭)</Link></p>
            <Button variant="secondary" disabled={busy || !!retry} onClick={() => void run(async () => { const state = await service.getPushState(); if (alive.current) { setPushState(state); setConfirmed(false) } })}>알림 설정 상태 확인</Button>
            <p role="status">{pushState === 'granted' ? '이 브라우저에 저장된 알림 권한과 구독을 확인했어요. 실제 도착 여부는 기기와 네트워크 상태에 따라 달라질 수 있어요.' : pushState === 'denied' ? '기기·브라우저 설정에서 알림 허용이 필요해요.' : pushState === 'unsupported' ? '이 환경에서는 알림 설정을 완료할 수 없어요. 알림 설정 화면에서 지원 환경을 확인해 주세요.' : pushState === 'subscription_failed' ? '알림 연결을 확인하지 못했어요. 설정 화면에서 다시 시도해 주세요.' : '알림 설정 화면에서 권한과 수신 등록을 확인한 뒤 설정 상태를 확인해 주세요.'}</p>
          </>}
          {needsPushSetup && pushState !== null && pushState !== 'granted' && <p>알림 설정 없이 복약 일정만 확인한 뒤 계획을 완료할 수 있어요. 알림 설정 완료로 기록하지 않아요.</p>}
          {!terminal ? <>
            <Button fullWidth disabled={busy || !!retry || !resources || (needsPushSetup && pushState === null)} onClick={() => { setTerminal('COMPLETED'); setConfirmed(false) }}>완료 확인하기</Button>
            <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => { setTerminal('CANCELLED'); setConfirmed(false) }}>계획 취소하기</Button>
          </> : <>
            <label className="track-c-choice"><input type="checkbox" checked={confirmed} disabled={busy || !!retry} onChange={e => setConfirmed(e.target.checked)} /><span>{terminal === 'CANCELLED' ? '이 계획을 취소할게요.' : needsPushSetup ? pushState === 'granted' ? '복약 일정을 확인했고 이 기기의 알림 설정을 마쳤어요.' : '알림 설정 없이 복약 일정만 확인했어요.' : plan.support_code === 'REMINDER_SETUP' ? '기존 복약 일정을 확인했거나 일정 저장을 마쳤어요.' : packingPlan ? '다음 외출에 필요한 약을 챙겼어요.' : '선택한 실천 계획의 실행을 마쳤어요.'}</span></label>
            <Button fullWidth disabled={!confirmed || busy || !!retry} onClick={() => void run(changePlan)}>{terminal === 'COMPLETED' ? '완료로 저장' : '취소로 저장'}</Button>
            <Button fullWidth variant="secondary" disabled={busy || !!retry} onClick={() => { setTerminal(null); setConfirmed(false) }}>돌아가기</Button>
          </>}
        </div>}
      </>}
      {busy && <p role="status">처리 중이에요.</p>}
    </main>
  </MobileShell></div>
}
