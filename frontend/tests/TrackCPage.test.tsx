import React, { StrictMode } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TrackCPage, { type TrackCServices } from '../src/pages/TrackCPage'
import { ApiError } from '../src/api/client'

const occurrenceId = '11111111-1111-4111-8111-111111111111'
const planId = '22222222-2222-4222-8222-222222222222'
const checkin = { checkin_id: 'checkin', occurrence_id: occurrenceId, status: 'NOT_TAKEN', revision: 3, taken_at: null, corrected: false }
const safety = { assessment_id: 'safety', medication_checkin_id: 'checkin', checkin_revision: 3, response_level: 'ROUTINE', safety_disposition: 'NORMAL', revision: 1 }
const barrier = { ...safety, barrier_response_id: 'barrier', safety_assessment_id: 'safety', response_status: 'ANSWERED', barrier_code: 'FORGOT' }
const support = {
  support_code: 'REMINDER_SETUP', rule_version: 'rule-v1', copy_version: 'copy-v1', priority: 1, rationale_code: 'FORGOT',
  action_config: { schema_version: 'track-c-handler-config-v1', rationale_code: 'FORGOT', parameters: { destination: 'MEDICATION_SCHEDULE_SETUP', prescription_version_medication_id: 'medication' } },
  support_copy: { title: '서버에서 받은 제안', body: '서버의 승인된 설명', confirmation_prompt: '이 제안을 계획으로 저장할까요?', primary_label: '계획 저장', secondary_label: '나중에' },
}
const plan = { support_action_plan_id: planId, barrier_response_id: 'barrier', support_code: 'REMINDER_SETUP', rule_version: 'rule-v1', copy_version: 'copy-v1', action_config_snapshot: support.action_config, status: 'ACTIVE', created_at: '2026-09-16T00:00:00Z', completed_at: null, cancelled_at: null }
function services(overrides: Partial<TrackCServices> = {}): TrackCServices {
  return {
    getDay: vi.fn().mockResolvedValue({ data: { occurrences: [{ occurrence_id: occurrenceId, scheduled_local_date: '2026-09-16', status: 'CLOSED', checkin }] } }),
    createSafety: vi.fn().mockResolvedValue(safety), putBarrier: vi.fn().mockResolvedValue(barrier),
    getOffers: vi.fn().mockResolvedValue({ ...barrier, supports: [support], reason_code: null }),
    createPlan: vi.fn().mockResolvedValue(plan), getPlan: vi.fn().mockResolvedValue(plan), patchPlan: vi.fn().mockResolvedValue(plan), ...overrides,
  }
}
function show(service: TrackCServices, entry = `/dev/track-c/occurrences/${occurrenceId}?date=2026-09-16`) {
  return render(<StrictMode><MemoryRouter initialEntries={[entry]}><Routes>
    <Route path="/dev/track-c/occurrences/:occurrenceId" element={<TrackCPage service={service} />} />
    <Route path="/dev/track-c/plans/:planId" element={<TrackCPage service={service} />} />
    <Route path="/login" element={<p>로그인 화면</p>} />
  </Routes></MemoryRouter></StrictMode>)
}
async function enterBarrier() { fireEvent.click(await screen.findByRole('button', { name: '증상이 없어요' })); await screen.findByRole('radio', { name: '깜빡했어요' }) }
async function enterOffer() { await enterBarrier(); fireEvent.click(screen.getByRole('radio', { name: '깜빡했어요' })); fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' })); await screen.findByText('서버에서 받은 제안') }
afterEach(cleanup)

describe('Track C API flow', () => {
  it('requires adoption and GETs current status after creation replay', async () => {
    const svc = services({ getPlan: vi.fn().mockResolvedValue({ ...plan, status: 'CANCELLED' }) }); show(svc); await enterOffer()
    expect(svc.createPlan).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: '계획 저장' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '계획 저장' }))
    await screen.findByText('취소됨')
    expect(svc.createPlan).toHaveBeenCalledWith({ barrier_response_id: 'barrier', support_code: 'REMINDER_SETUP', rule_version: 'rule-v1', copy_version: 'copy-v1', confirmed: true }, expect.any(String))
    expect(svc.patchPlan).not.toHaveBeenCalled()
  })
  it.each(['PENDING', 'UNCONFIRMED', 'TAKEN'])('does not start from %s', async status => {
    const svc = services({ getDay: vi.fn().mockResolvedValue({ data: { occurrences: [{ occurrence_id: occurrenceId, scheduled_local_date: '2026-09-16', status: 'CLOSED', checkin: status === 'PENDING' ? null : { ...checkin, status } }] } }) })
    show(svc); await screen.findByText('현재 도움을 계속 진행할 수 없어요'); expect(svc.createSafety).not.toHaveBeenCalled()
  })
  it.each(['URGENT', 'EMERGENCY', 'UNKNOWN'])('blocks %s before Barrier', async response_level => {
    const svc = services({ createSafety: vi.fn().mockResolvedValue({ ...safety, response_level }) }); show(svc)
    fireEvent.click(await screen.findByRole('button', { name: '증상이 없어요' }))
    await screen.findByText('현재 도움을 계속 진행할 수 없어요'); expect(svc.putBarrier).not.toHaveBeenCalled(); expect(svc.getOffers).not.toHaveBeenCalled()
  })
  it('does not invent symptom codes', async () => {
    const svc = services(); show(svc); fireEvent.click(await screen.findByRole('button', { name: '증상이 있거나 확실하지 않아요' }))
    await screen.findByText('현재 도움을 계속 진행할 수 없어요'); expect(svc.createSafety).not.toHaveBeenCalled()
  })
  it('submits one barrier with the fetched revision', async () => {
    const svc = services(); show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('radio', { name: '깜빡했어요' })); fireEvent.click(screen.getByRole('radio', { name: '복용 방법이 헷갈렸어요' }))
    expect(screen.getAllByRole('radio', { checked: true })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '선택한 어려움으로 도움 찾기' })); await screen.findByText('서버에서 받은 제안')
    expect(svc.putBarrier).toHaveBeenCalledWith('checkin', { response_status: 'ANSWERED', barrier_code: 'INSTRUCTIONS_UNCLEAR', checkin_revision: 3, expected_revision: 0 }, expect.any(String))
  })
  it('persists explicit decline and handles no eligible support', async () => {
    const svc = services({ getOffers: vi.fn().mockResolvedValue({ ...barrier, supports: [], reason_code: 'NO_ELIGIBLE_SUPPORT' }) }); show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('button', { name: '답하지 않고 계속하기' })); await screen.findByText('지금 제안할 수 있는 도움이 없어요')
    expect(svc.putBarrier).toHaveBeenCalledWith('checkin', { response_status: 'DECLINED', barrier_code: null, checkin_revision: 3, expected_revision: 0 }, expect.any(String))
    expect(svc.createPlan).not.toHaveBeenCalled()
  })
  it('retains the idempotency key after response loss and writes no storage', async () => {
    const createSafety = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(safety)
    show(services({ createSafety })); fireEvent.click(await screen.findByRole('button', { name: '증상이 없어요' }))
    fireEvent.click(await screen.findByRole('button', { name: '같은 요청 다시 시도' })); await screen.findByRole('radio', { name: '깜빡했어요' })
    expect(createSafety.mock.calls[0]).toEqual(createSafety.mock.calls[1]); expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  })
  it.each([403, 404, 409])('halts on %s without raw details', async status => {
    const svc = services({ putBarrier: vi.fn().mockRejectedValue(new ApiError(status, 'PRIVATE_RAW_DETAIL')) }); show(svc); await enterBarrier()
    fireEvent.click(screen.getByRole('button', { name: '답하지 않고 계속하기' })); await screen.findByText('현재 도움을 계속 진행할 수 없어요')
    expect(screen.queryByText('PRIVATE_RAW_DETAIL')).toBeNull(); expect(svc.getOffers).not.toHaveBeenCalled()
  })
  it('clears expired authentication', async () => {
    localStorage.setItem('access_token', 'synthetic'); show(services({ getDay: vi.fn().mockRejectedValue(new ApiError(401, 'secret')) }))
    await screen.findByText('로그인 화면'); expect(localStorage.getItem('access_token')).toBeNull()
  })
  it('requires reminder confirmation before completion', async () => {
    const svc = services({ getPlan: vi.fn().mockResolvedValueOnce(plan).mockResolvedValue({ ...plan, status: 'COMPLETED' }) }); show(svc, `/dev/track-c/plans/${planId}`)
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
    await screen.findByText('서버에서 받은 제안')
    expect(putBarrier.mock.calls[0]).toEqual(putBarrier.mock.calls[1])
  })
  it('does not reuse offers from another Safety assessment', async () => {
    const svc = services({ getOffers: vi.fn().mockResolvedValue({ ...barrier, safety_assessment_id: 'other', supports: [support], reason_code: null }) })
    show(svc); await enterBarrier(); fireEvent.click(screen.getByRole('button', { name: '답하지 않고 계속하기' }))
    await screen.findByText('현재 도움을 계속 진행할 수 없어요')
    expect(svc.createPlan).not.toHaveBeenCalled()
  })
  it('suppresses rapid double writes', async () => {
    let complete!: (value: typeof safety) => void
    const svc = services({ createSafety: vi.fn().mockReturnValue(new Promise(resolve => { complete = resolve })) })
    show(svc); const button = await screen.findByRole('button', { name: '증상이 없어요' })
    fireEvent.click(button); fireEvent.click(button)
    expect(svc.createSafety).toHaveBeenCalledTimes(1)
    complete(safety); await screen.findByRole('radio', { name: '깜빡했어요' })
  })
  it('stops stale completion without displaying success', async () => {
    const svc = services({ patchPlan: vi.fn().mockRejectedValue(new ApiError(409, 'CHECKIN_FLOW_STALE')) })
    show(svc, `/dev/track-c/plans/${planId}`)
    fireEvent.click(await screen.findByRole('button', { name: '완료 확인하기' }))
    fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '완료로 저장' }))
    await screen.findByText('현재 도움을 계속 진행할 수 없어요')
    expect(screen.queryByText('완료됨')).toBeNull()
  })

})
