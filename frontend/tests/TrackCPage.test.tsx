import supportFixture from '../../tests/fixtures/post_mvp_1/track_c/support-plan-v1.json'
import React, { StrictMode } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TrackCPage, { type TrackCServices } from '../src/pages/TrackCPage'
import { ApiError } from '../src/api/client'

// 중단 화면 문구는 원인별로 다르다. src/pages/TrackCPage.tsx 의 BLOCKED_COPY 와 짝을 이룬다.
const BLOCKED = {
  SELF_SYMPTOM: '증상이 있을 때는 도움 찾기를 멈춰요',
  PLAN_SYMPTOM: '증상이 있을 때는 계획 진행을 멈춰요',
  SAFETY_BLOCKED: '지금은 도움을 이어서 안내할 수 없어요',
  RECORD_UNAVAILABLE: '이 기록을 사용할 수 없어요',
  STALE_STATE: '기록이나 계획 상태가 변경되었어요',
} as const
const blockedByStatus = (status: number) => (status === 409 ? BLOCKED.STALE_STATE : BLOCKED.RECORD_UNAVAILABLE)

const activeCopyVersion = supportFixture.single_offer.data.supports[0].copy_version
const occurrenceId = '11111111-1111-4111-8111-111111111111'
const planId = '22222222-2222-4222-8222-222222222222'
const checkin = { checkin_id: 'checkin', occurrence_id: occurrenceId, status: 'NOT_TAKEN', revision: 3, taken_at: null, corrected: false }
const safety = { assessment_id: 'safety', medication_checkin_id: 'checkin', checkin_revision: 3, response_level: 'ROUTINE', safety_disposition: 'NORMAL', revision: 1 }
const barrier = { ...safety, barrier_response_id: 'barrier', safety_assessment_id: 'safety', response_status: 'ANSWERED', barrier_code: 'FORGOT', subreason_code: null }
const support = {
  support_code: 'REMINDER_SETUP', rule_version: 'rule-v1', copy_version: 'copy-v1', priority: 1, rationale_code: 'FORGOT',
  action_config: { schema_version: 'track-c-handler-config-v1', rationale_code: 'FORGOT', parameters: { destination: 'MEDICATION_SCHEDULE_SETUP', prescription_version_medication_id: 'medication' } },
  support_copy: { title: '서버에서 받은 제안', body: '서버의 승인된 설명', confirmation_prompt: '이 제안을 계획으로 저장할까요?', primary_label: '계획 저장', secondary_label: '나중에' },
  questions: [],
}
const plan = { support_action_plan_id: planId, barrier_response_id: 'barrier', support_code: 'REMINDER_SETUP', rule_version: 'rule-v1', copy_version: 'copy-v1', action_config_snapshot: support.action_config, status: 'ACTIVE', created_at: '2026-09-16T00:00:00Z', completed_at: null, cancelled_at: null }
const planListItem = { support_action_plan_id: planId, support_code: 'REMINDER_SETUP', status: 'ACTIVE', created_at: '2026-09-16T00:00:00Z', completed_at: null, cancelled_at: null }
function services(overrides: Partial<TrackCServices> = {}): TrackCServices {
  return {
    getFollowup: vi.fn().mockResolvedValue(null), submitFollowup: vi.fn(),
    getPushState: vi.fn().mockResolvedValue('granted'),
    getPlanResources: vi.fn().mockResolvedValue({ support_action_plan_id: planId, barrier_code: 'SCHEDULE_OR_TRAVEL', occurrence_id: occurrenceId, occurrence_local_date: '2026-09-16', prescription_version_medication_id: 'medication', support_copy: support.support_copy, subreason_code: null, selected_questions: [] }),
    getDay: vi.fn().mockResolvedValue({ data: { occurrences: [{ occurrence_id: occurrenceId, scheduled_local_date: '2026-09-16', status: 'CLOSED', checkin }] } }),
    createSafety: vi.fn().mockResolvedValue(safety), putBarrier: vi.fn().mockResolvedValue(barrier),
    getOffers: vi.fn().mockResolvedValue({ ...barrier, subreason_code: null, supports: [support], reason_code: null }),
    createPlan: vi.fn().mockResolvedValue(plan), listPlans: vi.fn().mockResolvedValue([planListItem]), getPlan: vi.fn().mockResolvedValue(plan), patchPlan: vi.fn().mockResolvedValue(plan), ...overrides,
  }
}
function show(service: TrackCServices, entry = `/track-c/occurrences/${occurrenceId}?date=2026-09-16`) {
  return render(<StrictMode><MemoryRouter initialEntries={[entry]}><Routes>
    <Route path="/track-c/occurrences/:occurrenceId" element={<TrackCPage service={service} />} />
    <Route path="/track-c/plans" element={<TrackCPage service={service} />} />
    <Route path="/track-c/plans/:planId" element={<TrackCPage service={service} />} />
    <Route path="/login" element={<p>로그인 화면</p>} />
  </Routes></MemoryRouter></StrictMode>)
}
async function enterBarrier() { fireEvent.click(await screen.findByRole('button', { name: '증상은 없어요' })); await screen.findByRole('radio', { name: '깜빡했어요' }) }
async function enterOffer() { await enterBarrier(); fireEvent.click(screen.getByRole('radio', { name: '깜빡했어요' })); fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' })); fireEvent.click(await screen.findByRole('button', { name: '세부 이유 없이 도움 보기' })); await screen.findByText('서버에서 받은 제안') }
afterEach(cleanup)

describe('Track C API flow', () => {
  it('requires adoption and moves to the saved plan list after creation replay', async () => {
    const svc = services(); show(svc); await enterOffer()
    expect(svc.createPlan).not.toHaveBeenCalled()

    fireEvent.click(await screen.findByRole('button', { name: '이 도움 확인하기' }))
    await screen.findByRole('heading', { name: '도움 내용을 설정해 주세요' })

    fireEvent.click(screen.getByRole('button', { name: '선택 내용 확인하기' }))
    await screen.findByRole('heading', { name: '선택한 내용을 확인해 주세요' })
    expect(svc.createPlan).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '이대로 사용하기' }))
    await screen.findByRole('heading', { name: '실천 계획을 확인해 주세요' })
    expect(svc.createPlan).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '이 계획을 저장하고 시작하기' }))
    await screen.findByRole('heading', { name: '실천 계획 목록' })
    expect(screen.getByRole('button', { name: /복약 일정과 알림 확인/ })).toBeTruthy()
    expect(svc.createPlan).toHaveBeenCalledWith({ barrier_response_id: 'barrier', support_code: 'REMINDER_SETUP', rule_version: 'rule-v1', copy_version: 'copy-v1', confirmed: true, selected_question_ids: [] }, expect.any(String))
    expect(svc.listPlans).toHaveBeenCalled()
    expect(svc.patchPlan).not.toHaveBeenCalled()
  })
  it('renders saved plans and opens the existing detail route', async () => {
    const svc = services()
    show(svc, '/track-c/plans')
    await screen.findByRole('heading', { name: '실천 계획 목록' })
    fireEvent.click(screen.getByRole('button', { name: /복약 일정과 알림 확인/ }))
    await screen.findByRole('heading', { name: '내 실천 계획' })
    expect(svc.listPlans).toHaveBeenCalled()
    expect(svc.getPlan).toHaveBeenCalledWith(planId)
  })
  it('shows an empty state without loading plan details', async () => {
    const svc = services({ listPlans: vi.fn().mockResolvedValue([]) })
    show(svc, '/track-c/plans')
    await screen.findByText('저장된 실천 계획이 없어요')
    expect(svc.getPlan).not.toHaveBeenCalled()
  })
  it('stores an allowlisted consultation question for the selected subreason', async () => {
    const instructionBarrier = { ...barrier, barrier_code: 'INSTRUCTIONS_UNCLEAR' }
    const instructionSupport = {
      ...support,
      support_code: 'INSTRUCTION_REVIEW',
      questions: [{ question_id: 'INSTRUCTION_TIMING', text: '이 약은 언제 복용해야 하나요?' }],
    }
    const svc = services({
      putBarrier: vi
        .fn()
        .mockResolvedValueOnce(instructionBarrier)
        .mockResolvedValue({ ...instructionBarrier, subreason_code: 'TIMING_OR_FOOD_UNCLEAR', revision: 2 }),
      getOffers: vi.fn().mockResolvedValue({
        ...instructionBarrier,
        subreason_code: 'TIMING_OR_FOOD_UNCLEAR',
        supports: [instructionSupport],
        reason_code: null,
      }),
    })
    show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('radio', { name: '복용 방법이 헷갈렸어요' }))
    fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }))
    fireEvent.click(await screen.findByRole('radio', { name: '시간이나 식사 조건이 헷갈려요' }))
    fireEvent.click(screen.getByRole('button', { name: '선택한 상황으로 도움 찾기' }))
    fireEvent.click(await screen.findByRole('button', { name: '이 도움 확인하기' }))
    await screen.findByRole('heading', { name: '도움 내용을 설정해 주세요' })

    const question = await screen.findByRole('checkbox', { name: '이 약은 언제 복용해야 하나요?' })
    expect(
      (screen.getByRole('button', { name: '선택 내용 확인하기' }) as HTMLButtonElement).disabled,
    ).toBe(true)

    fireEvent.click(question)
    fireEvent.click(screen.getByRole('button', { name: '선택 내용 확인하기' }))

    await screen.findByRole('heading', { name: '선택한 내용을 확인해 주세요' })
    fireEvent.click(screen.getByRole('button', { name: '이대로 사용하기' }))

    await screen.findByRole('heading', { name: '실천 계획을 확인해 주세요' })
    expect(svc.createPlan).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '이 계획을 저장하고 시작하기' }))
    await screen.findByText('진행 중')
    expect(svc.getOffers).toHaveBeenCalledWith('barrier', undefined, 'TIMING_OR_FOOD_UNCLEAR')
    expect(svc.createPlan).toHaveBeenCalledWith(expect.objectContaining({
      support_code: 'INSTRUCTION_REVIEW',
      subreason_code: 'TIMING_OR_FOOD_UNCLEAR',
      selected_question_ids: ['INSTRUCTION_TIMING'],
    }), expect.any(String))
  })
  it.each(['PENDING', 'UNCONFIRMED', 'TAKEN'])('does not start from %s', async status => {
    const svc = services({ getDay: vi.fn().mockResolvedValue({ data: { occurrences: [{ occurrence_id: occurrenceId, scheduled_local_date: '2026-09-16', status: 'CLOSED', checkin: status === 'PENDING' ? null : { ...checkin, status } }] } }) })
    show(svc); await screen.findByText(BLOCKED.STALE_STATE); expect(svc.createSafety).not.toHaveBeenCalled()
  })
  it.each(['URGENT', 'EMERGENCY', 'UNKNOWN'])('blocks %s before Barrier', async response_level => {
    const svc = services({ createSafety: vi.fn().mockResolvedValue({ ...safety, response_level }) }); show(svc)
    fireEvent.click(await screen.findByRole('button', { name: '증상은 없어요' }))
    await screen.findByText(BLOCKED.SAFETY_BLOCKED); expect(svc.putBarrier).not.toHaveBeenCalled(); expect(svc.getOffers).not.toHaveBeenCalled()
  })
  it('does not invent symptom codes', async () => {
    const svc = services(); show(svc); fireEvent.click(await screen.findByRole('button', { name: '증상이 있어요' }))
    await screen.findByText(BLOCKED.SELF_SYMPTOM); expect(svc.createSafety).not.toHaveBeenCalled()
  })
  it('스스로 신고한 증상 중단은 오류 문구가 아니라 다음 행동 안내를 보여준다', async () => {
    show(services()); fireEvent.click(await screen.findByRole('button', { name: '증상이 있어요' }))
    await screen.findByText(BLOCKED.SELF_SYMPTOM)
    expect(screen.getByText('증상이 심하거나 갑자기 생겼다면 약사나 의료진에게 먼저 확인해 주세요.')).toBeTruthy()
    expect(screen.queryByText(BLOCKED.STALE_STATE)).toBeNull()
    expect(screen.queryByText(BLOCKED.RECORD_UNAVAILABLE)).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('스스로 신고한 증상은 중단 화면에서 다시 선택할 수 있다', async () => {
    const svc = services(); show(svc); fireEvent.click(await screen.findByRole('button', { name: '증상이 있어요' }))
    await screen.findByText(BLOCKED.SELF_SYMPTOM)
    fireEvent.click(screen.getByRole('button', { name: '증상 선택 다시 하기' }))
    await screen.findByRole('heading', { name: '현재 불편한 증상이 있나요?' })
    expect(svc.createSafety).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '증상은 없어요' }))
    await screen.findByRole('heading', { name: '이번에는 어떤 점이 가장 크게 영향을 주었나요?' })
  })
  it.each(['URGENT', 'EMERGENCY', 'UNKNOWN'])('서버가 %s로 막은 경우에는 다시 선택 버튼을 주지 않는다', async response_level => {
    const svc = services({ createSafety: vi.fn().mockResolvedValue({ ...safety, response_level }) }); show(svc)
    fireEvent.click(await screen.findByRole('button', { name: '증상은 없어요' }))
    await screen.findByText(BLOCKED.SAFETY_BLOCKED)
    expect(screen.queryByRole('button', { name: '증상 선택 다시 하기' })).toBeNull()
  })
  it('submits one barrier with the fetched revision', async () => {
    const svc = services(); show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('radio', { name: '깜빡했어요' })); fireEvent.click(screen.getByRole('radio', { name: '복용 방법이 헷갈렸어요' }))
    expect(screen.getAllByRole('radio', { checked: true })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' })); await screen.findByText('어떤 상황에 더 가까웠나요?')
    expect(svc.putBarrier).toHaveBeenCalledWith('checkin', { response_status: 'ANSWERED', barrier_code: 'INSTRUCTIONS_UNCLEAR', checkin_revision: 3, expected_revision: 0 }, expect.any(String))
  })
  it('persists explicit decline and handles no eligible support', async () => {
    const svc = services({ getOffers: vi.fn().mockResolvedValue({ ...barrier, supports: [], reason_code: 'NO_ELIGIBLE_SUPPORT' }) }); show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('button', { name: '답하지 않고 복약 상태만 저장' })); await screen.findByText('지금 제안할 수 있는 도움이 없어요')
    expect(svc.putBarrier).toHaveBeenCalledWith('checkin', { response_status: 'DECLINED', barrier_code: null, checkin_revision: 3, expected_revision: 0 }, expect.any(String))
    expect(svc.createPlan).not.toHaveBeenCalled()
  })
  it('retains the idempotency key after response loss and writes no storage', async () => {
    const createSafety = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(safety)
    show(services({ createSafety })); fireEvent.click(await screen.findByRole('button', { name: '증상은 없어요' }))
    fireEvent.click(await screen.findByRole('button', { name: '같은 요청 다시 시도' })); await screen.findByRole('radio', { name: '깜빡했어요' })
    expect(createSafety.mock.calls[0]).toEqual(createSafety.mock.calls[1]); expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  })
  it.each([403, 404, 409])('halts on %s without raw details', async status => {
    const svc = services({ putBarrier: vi.fn().mockRejectedValue(new ApiError(status, 'PRIVATE_RAW_DETAIL')) }); show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('button', { name: '답하지 않고 복약 상태만 저장' })); await screen.findByText(blockedByStatus(status))
    expect(screen.queryByText('PRIVATE_RAW_DETAIL')).toBeNull(); expect(svc.getOffers).not.toHaveBeenCalled()
  })
  it('clears expired authentication', async () => {
    localStorage.setItem('access_token', 'synthetic'); show(services({ getDay: vi.fn().mockRejectedValue(new ApiError(401, 'secret')) }))
    await screen.findByText('로그인 화면'); expect(localStorage.getItem('access_token')).toBeNull()
  })
  it('requires reminder confirmation before completion', async () => {
    const svc = services({ getPlan: vi.fn().mockResolvedValueOnce(plan).mockResolvedValue({ ...plan, status: 'COMPLETED' }) }); show(svc, `/track-c/plans/${planId}`)
    fireEvent.click(await screen.findByRole('button', { name: '완료 확인하기' })); expect(svc.patchPlan).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: '완료로 저장' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox', { name: '기존 복약 일정을 확인했거나 일정 저장을 마쳤어요.' })); fireEvent.click(screen.getByRole('button', { name: '완료로 저장' }))
    await screen.findByText('완료됨'); expect(svc.patchPlan).toHaveBeenCalledWith(planId, { status: 'COMPLETED', confirmed: true }, expect.any(String))
  })
  it.each([422, 500])('preserves selection and replays the same attempt after %s', async status => {
    const putBarrier = vi.fn().mockRejectedValueOnce(new ApiError(status, 'PRIVATE_RAW_DETAIL')).mockResolvedValue(barrier)
    const svc = services({ putBarrier }); show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('radio', { name: '깜빡했어요' }))
    fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }))
    await screen.findByRole('alert')
    expect(screen.queryByText('PRIVATE_RAW_DETAIL')).toBeNull()
    expect(screen.getAllByRole('radio', { checked: true })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '같은 요청 다시 시도' }))
    fireEvent.click(await screen.findByRole('button', { name: '세부 이유 없이 도움 보기' })); await screen.findByText('서버에서 받은 제안')
    expect(putBarrier.mock.calls[0]).toEqual(putBarrier.mock.calls[1])
  })
  it('does not reuse offers from another Safety assessment', async () => {
    const svc = services({ getOffers: vi.fn().mockResolvedValue({ ...barrier, safety_assessment_id: 'other', supports: [support], reason_code: null }) })
    show(svc); await enterBarrier(); fireEvent.click(screen.getByRole('button', { name: '답하지 않고 복약 상태만 저장' }))
    await screen.findByText(BLOCKED.STALE_STATE)
    expect(svc.createPlan).not.toHaveBeenCalled()
  })
  it('suppresses rapid double writes', async () => {
    let complete!: (value: typeof safety) => void
    const svc = services({ createSafety: vi.fn().mockReturnValue(new Promise(resolve => { complete = resolve })) })
    show(svc); const button = await screen.findByRole('button', { name: '증상은 없어요' })
    fireEvent.click(button); fireEvent.click(button)
    expect(svc.createSafety).toHaveBeenCalledTimes(1)
    complete(safety); await screen.findByRole('radio', { name: '깜빡했어요' })
  })
  it('stops stale completion without displaying success', async () => {
    const svc = services({ patchPlan: vi.fn().mockRejectedValue(new ApiError(409, 'CHECKIN_FLOW_STALE')) })
    show(svc, `/track-c/plans/${planId}`)
    fireEvent.click(await screen.findByRole('button', { name: '완료 확인하기' }))
    fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '완료로 저장' }))
    await screen.findByText(BLOCKED.STALE_STATE)
    expect(screen.queryByText('완료됨')).toBeNull()
  })

})

it.each([
  ['생활 일정이 바뀌었어요', 'SCHEDULE_CHANGED', 'REMINDER_SETUP'],
  ['약을 가지고 나오지 않았어요', 'MEDICATION_NOT_WITH_ME', 'ROUTINE_OR_TRAVEL_PLAN'],
])('routes %s through server offers and explicit adoption', async (label, situation, code) => {
  const travelBarrier = { ...barrier, barrier_code: 'SCHEDULE_OR_TRAVEL' }
  const answered = { ...travelBarrier, subreason_code: situation, revision: 2 }
  const svc = services({ putBarrier: vi.fn().mockResolvedValueOnce(travelBarrier).mockResolvedValue(answered), getOffers: vi.fn().mockResolvedValue({ ...travelBarrier, supports: [{ ...support, support_code: code }], reason_code: null }) })
  show(svc); await enterBarrier()
  fireEvent.click(screen.getByRole('radio', { name: '일정이나 외출 때문에 어려웠어요' }))
  fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }))
  await screen.findByRole('heading', { name: '어떤 상황이었나요?' })
  expect(svc.getOffers).not.toHaveBeenCalled()
  expect((screen.getByRole('button', { name: '선택한 상황으로 도움 찾기' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('radio', { name: label }))
  fireEvent.click(screen.getByRole('button', { name: '선택한 상황으로 도움 찾기' }))
  await screen.findByText('서버에서 받은 제안')
  expect(svc.putBarrier).toHaveBeenNthCalledWith(2, 'checkin', { response_status: 'ANSWERED', barrier_code: 'SCHEDULE_OR_TRAVEL', subreason_code: situation, checkin_revision: 3, expected_revision: 1 }, expect.any(String))
  expect(svc.getOffers).toHaveBeenCalledWith('barrier', situation, situation)
  expect(svc.createPlan).not.toHaveBeenCalled()

  fireEvent.click(await screen.findByRole('button', { name: '이 도움 확인하기' }))
  await screen.findByRole('heading', { name: '도움 내용을 설정해 주세요' })

  fireEvent.click(screen.getByRole('button', { name: '선택 내용 확인하기' }))
  await screen.findByRole('heading', { name: '선택한 내용을 확인해 주세요' })

  fireEvent.click(screen.getByRole('button', { name: '이대로 사용하기' }))
  await screen.findByRole('heading', { name: '실천 계획을 확인해 주세요' })
  expect(svc.createPlan).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: '이 계획을 저장하고 시작하기' }))
  await screen.findByText('진행 중')
  expect(svc.createPlan).toHaveBeenCalledWith(expect.objectContaining({ travel_situation: situation, support_code: code }), expect.any(String))
  expect(svc.patchPlan).not.toHaveBeenCalled()
})

it('준비 중인 어려움은 답을 저장하고 도움 제안 대신 안내를 보여준다', async () => {
  const accessBarrier = { ...barrier, barrier_code: 'ACCESS_OR_COST' }
  const answered = { ...accessBarrier, subreason_code: 'RUNNING_LOW', revision: 2 }
  const svc = services({ putBarrier: vi.fn().mockResolvedValueOnce(accessBarrier).mockResolvedValue(answered) })
  show(svc); await enterBarrier()
  expect(screen.getByRole('radio', { name: /약이 없거나 구하기 어려웠어요/ }).closest('label')?.textContent).toContain('준비중')
  fireEvent.click(screen.getByRole('radio', { name: /약이 없거나 구하기 어려웠어요/ }))
  fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }))
  fireEvent.click(await screen.findByRole('radio', { name: '약이 곧 떨어져요' }))
  fireEvent.click(screen.getByRole('button', { name: '선택한 상황으로 도움 찾기' }))
  await screen.findByRole('heading', { name: '선택한 내용은 저장했어요' })
  // The answer is still written, so the clinic report keeps it; only the offer is skipped.
  expect(svc.putBarrier).toHaveBeenCalledTimes(2)
  expect(svc.getOffers).not.toHaveBeenCalled()
  expect(svc.createPlan).not.toHaveBeenCalled()
})

it('선택 목록에서 제외한 세부 이유는 더 이상 노출되지 않는다', async () => {
  const svc = services()
  show(svc); await enterBarrier()
  fireEvent.click(screen.getByRole('radio', { name: '깜빡했어요' }))
  fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }))
  await screen.findByRole('radio', { name: '알림을 보거나 듣지 못했어요' })
  expect(screen.queryByRole('radio', { name: '약이나 복용 회차가 헷갈렸어요' })).toBeNull()
})

it('retries the travel offer without writing another Barrier', async () => {
  const travelBarrier = { ...barrier, barrier_code: 'SCHEDULE_OR_TRAVEL' }
  const answered = { ...travelBarrier, subreason_code: 'MEDICATION_NOT_WITH_ME', revision: 2 }
  const svc = services({ putBarrier: vi.fn().mockResolvedValueOnce(travelBarrier).mockResolvedValue(answered), getOffers: vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue({ ...barrier, supports: [support], reason_code: null }) })
  show(svc); await enterBarrier()
  fireEvent.click(screen.getByRole('radio', { name: '일정이나 외출 때문에 어려웠어요' }))
  fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }))
  fireEvent.click(await screen.findByRole('radio', { name: '약을 가지고 나오지 않았어요' }))
  fireEvent.click(screen.getByRole('button', { name: '선택한 상황으로 도움 찾기' }))
  fireEvent.click(await screen.findByRole('button', { name: '같은 요청 다시 시도' }))
  await screen.findByText('서버에서 받은 제안')
  // Two writes total: the barrier answer, then the subreason. The retry adds neither.
  expect(svc.putBarrier).toHaveBeenCalledTimes(2)
  expect(svc.getOffers).toHaveBeenNthCalledWith(1, 'barrier', 'MEDICATION_NOT_WITH_ME', 'MEDICATION_NOT_WITH_ME')
  expect(svc.getOffers).toHaveBeenNthCalledWith(2, 'barrier', 'MEDICATION_NOT_WITH_ME', 'MEDICATION_NOT_WITH_ME')
})

it.each([activeCopyVersion, 'track-c-support-copy-ko-2099-01-01.1'])('retains packing confirmation for %s', async copy_version => {
  const packing = { ...plan, support_code: 'ROUTINE_OR_TRAVEL_PLAN', copy_version }
  const svc = services({ getPlan: vi.fn().mockResolvedValue(packing) })
  show(svc, `/track-c/plans/${planId}`)
  await screen.findByRole('heading', { name: '다음 외출 전 약 챙기기' })
  expect(screen.queryByRole('link', { name: '일정 확인·설정 (새 탭)' })).toBeNull()
  expect(svc.patchPlan).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '완료 확인하기' }))
  expect((screen.getByRole('button', { name: '완료로 저장' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('checkbox', { name: '다음 외출에 필요한 약을 챙겼어요.' }))
  fireEvent.click(screen.getByRole('button', { name: '완료로 저장' }))
  await screen.findByRole('button', { name: '완료 확인하기' })
  expect(svc.patchPlan).toHaveBeenCalledWith(planId, { status: 'COMPLETED', confirmed: true }, expect.any(String))
})

it.each(['INSTRUCTION_REVIEW', 'PURPOSE_REVIEW'])('shows original records and missing evidence for %s', async code => {
  const svc = services({ getPlan: vi.fn().mockResolvedValue({ ...plan, support_code: code }) })
  show(svc, `/track-c/plans/${planId}`)
  const link = await screen.findByRole('link', { name: '이 기록의 확인된 약 정보 보기 (새 탭)' })
  expect(link.getAttribute('href')).toBe(`/schedule/occurrences/${occurrenceId}?date=2026-09-16`)
  expect(screen.getByText(/설명과 근거를 아직 제공할 수 없어요/)).toBeTruthy()
  if (code === 'INSTRUCTION_REVIEW') expect(screen.getByRole('link', { name: '현재 복약 일정 확인 (새 탭)' }).getAttribute('href')).toBe('/schedule?support_medication=medication')
  expect(svc.patchPlan).not.toHaveBeenCalled()
})

it('halts general concern guidance when symptoms arise without marking the plan complete', async () => {
  const svc = services({ getPlan: vi.fn().mockResolvedValue({ ...plan, support_code: 'MEDICATION_CONCERN_GUIDANCE' }) })
  show(svc, `/track-c/plans/${planId}`)
  fireEvent.click(await screen.findByRole('button', { name: '증상이 생겼거나 확실하지 않아요' }))
  await screen.findByText(BLOCKED.PLAN_SYMPTOM)
  expect(screen.queryByRole('button', { name: '완료 확인하기' })).toBeNull()
  expect(svc.patchPlan).not.toHaveBeenCalled()
  expect(svc.createSafety).not.toHaveBeenCalled()
})

it.each([activeCopyVersion, 'track-c-support-copy-ko-2099-01-01.1'])('checks notifications and rechecks before completing copy %s', async copy_version => {
  const getPushState = vi.fn().mockResolvedValueOnce('denied').mockResolvedValueOnce('granted').mockResolvedValueOnce('revoked')
  const svc = services({
    getPlan: vi.fn().mockResolvedValue({ ...plan, copy_version }),
    getPlanResources: vi.fn().mockResolvedValue({ support_action_plan_id: planId, barrier_code: 'FORGOT', occurrence_id: occurrenceId, occurrence_local_date: '2026-09-16', prescription_version_medication_id: 'medication', support_copy: support.support_copy, subreason_code: null, selected_questions: [] }),
    getPushState,
  })
  show(svc, `/track-c/plans/${planId}`)
  const check = await screen.findByRole('button', { name: '알림 설정 상태 확인' })
  expect(screen.getByRole('link', { name: '이 기기 알림 설정 (새 탭)' }).getAttribute('href')).toBe('/settings/notifications')
  expect(getPushState).not.toHaveBeenCalled()
  expect((screen.getByRole('button', { name: '완료 확인하기' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(check); await screen.findByText('기기·브라우저 설정에서 알림 허용이 필요해요.')
  expect((screen.getByRole('button', { name: '완료 확인하기' }) as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(check); await screen.findByText(/이 브라우저에 저장된 알림 권한과 구독을 확인했어요/)
  fireEvent.click(screen.getByRole('button', { name: '완료 확인하기' }))
  fireEvent.click(screen.getByRole('checkbox', { name: '복약 일정을 확인했고 이 기기의 알림 설정을 마쳤어요.' }))
  fireEvent.click(screen.getByRole('button', { name: '완료로 저장' }))
  await screen.findByText(/알림 설정 화면에서 권한과 수신 등록을 확인한 뒤/)
  expect(svc.patchPlan).not.toHaveBeenCalled()
})


it.each(['granted', 'unsupported', 'denied', 'config_unavailable', 'subscription_failed', 'revoked', 'unrequested'])('allows explicitly confirmed completion for %s', async state => {
  const svc = services({
    getPlanResources: vi.fn().mockResolvedValue({ support_action_plan_id: planId, barrier_code: 'FORGOT', occurrence_id: occurrenceId, occurrence_local_date: '2026-09-16', prescription_version_medication_id: 'medication', support_copy: support.support_copy, subreason_code: null, selected_questions: [] }),
    getPushState: vi.fn().mockResolvedValue(state),
  })
  show(svc, `/track-c/plans/${planId}`)
  fireEvent.click(await screen.findByRole('button', { name: '알림 설정 상태 확인' }))
  await screen.findByText(state === 'granted' ? /이 브라우저에 저장된 알림 권한과 구독/ : /알림 설정 없이 복약 일정만 확인한 뒤/)
  fireEvent.click(screen.getByRole('button', { name: '완료 확인하기' }))
  fireEvent.click(screen.getByRole('checkbox', { name: state === 'granted' ? '복약 일정을 확인했고 이 기기의 알림 설정을 마쳤어요.' : '알림 설정 없이 복약 일정만 확인했어요.' }))
  fireEvent.click(screen.getByRole('button', { name: '완료로 저장' }))
  await screen.findByRole('button', { name: '완료 확인하기' })
  expect(svc.patchPlan).toHaveBeenCalledWith(planId, { status: 'COMPLETED', confirmed: true }, expect.any(String))
  expect(svc.getPushState).toHaveBeenCalledTimes(2)
})

it('keeps cancellation after resources fail, but blocks completion', async () => {
  const svc = services({ getPlanResources: vi.fn().mockRejectedValue(new ApiError(503, 'PRIVATE_RAW_DETAIL')) })
  show(svc, `/track-c/plans/${planId}`)
  await screen.findByText('진행 중')
  expect(screen.queryByText('PRIVATE_RAW_DETAIL')).toBeNull()
  expect((screen.getByRole('button', { name: '완료 확인하기' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '계획 취소하기' }))
  fireEvent.click(screen.getByRole('checkbox', { name: '이 계획을 취소할게요.' }))
  fireEvent.click(screen.getByRole('button', { name: '취소로 저장' }))
  await screen.findByRole('button', { name: '계획 취소하기' })
  expect(svc.patchPlan).toHaveBeenCalledWith(planId, { status: 'CANCELLED', confirmed: true }, expect.any(String))
})

it('restores completion after retrying only the resources read', async () => {
  const svc = services()
  vi.mocked(svc.getPlanResources).mockRejectedValueOnce(new TypeError('offline'))
  show(svc, `/track-c/plans/${planId}`)
  fireEvent.click(await screen.findByRole('button', { name: '안내 다시 조회' }))
  await screen.findByText('서버의 승인된 설명')
  expect((screen.getByRole('button', { name: '완료 확인하기' }) as HTMLButtonElement).disabled).toBe(false)
  expect(svc.patchPlan).not.toHaveBeenCalled()
})

it.each([401, 403, 404, 409])('blocks resources error %s', async status => {
  show(services({ getPlanResources: vi.fn().mockRejectedValue(new ApiError(status, 'PRIVATE')) }), `/track-c/plans/${planId}`)
  await screen.findByText(status === 401 ? '로그인 화면' : blockedByStatus(status))
  expect(screen.queryByRole('button', { name: '계획 취소하기' })).toBeNull()
})

it('preserves historical travel confirmation', async () => {
  show(services({ getPlan: vi.fn().mockResolvedValue({ ...plan, support_code: 'ROUTINE_OR_TRAVEL_PLAN', copy_version: 'track-c-support-copy-ko-2026-09-15.1' }) }), `/track-c/plans/${planId}`)
  fireEvent.click(await screen.findByRole('button', { name: '완료 확인하기' }))
  expect(screen.getByRole('checkbox', { name: '선택한 실천 계획의 실행을 마쳤어요.' })).toBeTruthy()
})

it.each(['ACTIVE', 'CANCELLED'])('does not show followup entry for %s', async status => {
  const svc = services({ getPlan: vi.fn().mockResolvedValue({ ...plan, status }) })
  show(svc, `/track-c/plans/${planId}`)
  await screen.findByText(status === 'ACTIVE' ? '진행 중' : '취소됨')
  expect(screen.queryByRole('button', { name: '도움 사용 후기' })).toBeNull()
  expect(svc.getFollowup).not.toHaveBeenCalled()
})
it('opens completed plan feedback even if the historical copy cannot be loaded', async () => {
  const svc = services({ getPlan: vi.fn().mockResolvedValue({ ...plan, status: 'COMPLETED' }), getPlanResources: vi.fn().mockRejectedValue(new ApiError(503, 'PRIVATE')) })
  show(svc, `/track-c/plans/${planId}`)
  fireEvent.click(await screen.findByRole('button', { name: '도움 사용 후기' }))
  fireEvent.click(await screen.findByRole('button', { name: '나중에' }))
  expect(screen.getByRole('button', { name: '도움 사용 후기' })).toBeTruthy()
  expect(svc.submitFollowup).not.toHaveBeenCalled()
  expect(svc.patchPlan).not.toHaveBeenCalled()
})
