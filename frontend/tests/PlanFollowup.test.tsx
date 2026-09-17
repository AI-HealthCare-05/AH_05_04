import React, { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import PlanFollowup from '../src/components/PlanFollowup'
import { ApiError } from '../src/api/client'
import type { Followup } from '../src/api/trackC'
const planId = '11111111-1111-4111-8111-111111111111'
const row: Followup = { followup_id: 'synthetic', support_action_plan_id: planId, response: 'HELPED', revision: 1, created_at: '2026-09-16T00:00:00Z', updated_at: '2026-09-16T00:00:00Z' }
function services() { return { getPlan: vi.fn().mockResolvedValue({ support_action_plan_id: planId, status: 'COMPLETED' }), getFollowup: vi.fn().mockResolvedValue(null), submitFollowup: vi.fn().mockResolvedValue(row) } }
function show(service = services()) {
  const close = vi.fn()
  render(<StrictMode><MemoryRouter><Routes><Route path="/" element={<PlanFollowup planId={planId} service={service} onClose={close} />} /><Route path="/login" element={<p>로그인 화면</p>} /></Routes></MemoryRouter></StrictMode>)
  return close
}
async function choose(text = '도움이 됐어요') { fireEvent.click(await screen.findByRole('radio', { name: text })) }
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }))
afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear() })
it('reads without writes and defers without saving', async () => {
  const svc = services(); const close = show(svc); await choose()
  expect(screen.getByRole('heading')).toBe(document.activeElement)
  expect(svc.submitFollowup).not.toHaveBeenCalled(); click('나중에')
  expect(close).toHaveBeenCalledTimes(1); expect(svc.submitFollowup).not.toHaveBeenCalled()
  expect(localStorage.length + sessionStorage.length).toBe(0)
})
it.each(['도움이 됐어요', '도움이 되지 않았어요', '아직 모르겠어요'])('saves %s explicitly then reads current response', async text => {
  const svc = services(); show(svc)
  await screen.findByRole('radio', { name: text })
  expect((screen.getByRole('button', { name: '후기 저장' }) as HTMLButtonElement).disabled).toBe(true)
  await choose(text); const response = screen.getByRole('radio', { name: text }).getAttribute('value')
  svc.getFollowup.mockResolvedValue({ ...row, response }); click('후기 저장')
  await screen.findByText(`저장된 후기: ${text}`)
  expect(svc.submitFollowup).toHaveBeenCalledWith(planId, { response, expected_revision: 0 }, expect.any(String))
  expect(svc.getFollowup).toHaveBeenCalledTimes(2)
})
it('corrects using current revision and a fresh key', async () => {
  const svc = services(); show(svc); await choose(); svc.getFollowup.mockResolvedValue(row); click('후기 저장')
  await screen.findByText('저장된 후기: 도움이 됐어요'); click('후기 수정하기')
  expect(screen.queryAllByRole('radio', { checked: true })).toHaveLength(0); await choose('아직 모르겠어요')
  svc.getFollowup.mockResolvedValue({ ...row, response: 'NOT_SURE', revision: 2 }); click('후기 수정 저장')
  await screen.findByText('저장된 후기: 아직 모르겠어요')
  expect(svc.submitFollowup.mock.calls[1][1]).toEqual({ response: 'NOT_SURE', expected_revision: 1 })
  expect(svc.submitFollowup.mock.calls[1][2]).not.toBe(svc.submitFollowup.mock.calls[0][2])
})
it('retries lost responses identically and shows current GET instead of replay', async () => {
  const svc = services(); svc.submitFollowup.mockRejectedValueOnce(new TypeError('offline')); show(svc); await choose(); click('후기 저장')
  await screen.findByRole('button', { name: '같은 후기 다시 저장' }); svc.getFollowup.mockResolvedValue({ ...row, response: 'NOT_HELPED', revision: 3 }); click('같은 후기 다시 저장')
  await screen.findByText('저장된 후기: 도움이 되지 않았어요')
  expect(svc.submitFollowup.mock.calls[0]).toEqual(svc.submitFollowup.mock.calls[1])
})
it('retries only GET after POST succeeds', async () => {
  const svc = services(); show(svc); await choose(); svc.getFollowup.mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(row); click('후기 저장')
  fireEvent.click(await screen.findByRole('button', { name: '후기 다시 조회' })); await screen.findByText('저장된 후기: 도움이 됐어요')
  expect(svc.submitFollowup).toHaveBeenCalledTimes(1)
})
it('reloads revision conflict without resubmission and requires a fresh selection', async () => {
  const svc = services(); show(svc); await choose()
  svc.submitFollowup.mockRejectedValueOnce(new ApiError(409, 'PRIVATE', 'ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT'))
  svc.getFollowup.mockResolvedValue({ ...row, response: 'NOT_SURE', revision: 4 }); click('후기 저장')
  await screen.findByText('저장된 후기: 아직 모르겠어요'); expect(svc.submitFollowup).toHaveBeenCalledTimes(1)
  click('후기 수정하기'); expect(screen.queryAllByRole('radio', { checked: true })).toHaveLength(0); await choose('도움이 되지 않았어요'); click('후기 수정 저장')
  await waitFor(() => expect(svc.submitFollowup).toHaveBeenCalledTimes(2))
  expect(svc.submitFollowup.mock.calls[1][1]).toEqual({ response: 'NOT_HELPED', expected_revision: 4 })
  expect(svc.submitFollowup.mock.calls[1][2]).not.toBe(svc.submitFollowup.mock.calls[0][2])
})
it.each(['ACTIVE', 'CANCELLED'])('rejects %s plans', async status => {
  const svc = services(); svc.getPlan.mockResolvedValue({ support_action_plan_id: planId, status }); show(svc); await screen.findByRole('alert')
  expect(screen.queryByRole('radio')).toBeNull(); expect(svc.getFollowup).not.toHaveBeenCalled(); expect(svc.submitFollowup).not.toHaveBeenCalled()
})
it.each([403, 404, 409])('blocks mutation error %s without exposing raw data', async status => {
  const svc = services(); svc.submitFollowup.mockRejectedValue(new ApiError(status, 'PRIVATE')); show(svc); await choose(); click('후기 저장'); await screen.findByRole('alert')
  expect(screen.queryByText('PRIVATE')).toBeNull(); expect(screen.queryByRole('radio')).toBeNull()
})
it('clears expired auth', async () => {
  const svc = services(); svc.getFollowup.mockRejectedValue(new ApiError(401, 'PRIVATE')); localStorage.setItem('access_token', 'synthetic'); show(svc)
  await screen.findByText('로그인 화면'); expect(localStorage.getItem('access_token')).toBeNull()
})
it('rejects mismatched plan data', async () => {
  const svc = services(); svc.getFollowup.mockResolvedValue({ ...row, support_action_plan_id: 'other' }); show(svc); await screen.findByRole('alert'); expect(screen.queryByRole('radio')).toBeNull()
})
it('suppresses duplicate submission', async () => {
  const svc = services(); let finish!: (value: Followup) => void; svc.submitFollowup.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  show(svc); await choose(); const button = screen.getByRole('button', { name: '후기 저장' }); fireEvent.click(button); fireEvent.click(button)
  expect(svc.submitFollowup).toHaveBeenCalledTimes(1); svc.getFollowup.mockResolvedValue(row)
  await act(async () => finish(row)); await screen.findByText('저장된 후기: 도움이 됐어요')
})
