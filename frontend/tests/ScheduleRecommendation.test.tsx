import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ScheduleRecommendation } from '../src/pages/ScheduleRecommendation'
import type { RecommendationResponse } from '../src/api/medicationSchedules'

const response: RecommendationResponse = { data: {
  prescription_version_medication_id: 'medication', prescription_version_id: 'version',
  rule_version: 'explicit-after-meal-v1', timing_text: '저녁 식후 30분',
  local_times: ['20:00'], reason: 'EXPLICIT_AFTER_MEAL',
} }
function setup(load = vi.fn().mockResolvedValue(response)) {
  const onApply = vi.fn(), onInvalidate = vi.fn(), onManual = vi.fn()
  render(<ScheduleRecommendation medicationId="medication" medicationName="합성약" timingText="저녁 식후 30분"
    disabled={false} onApply={onApply} onInvalidate={onInvalidate} onManual={onManual} load={load} />)
  return { onApply, onInvalidate, onManual, load }
}
function input() {
  fireEvent.change(screen.getByLabelText('합성약 저녁 식사 종료 시각'), { target: { value: '19:30' } })
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: '시간 후보 계산' }))
}
afterEach(cleanup)

it('requires regular meal confirmation and explicit apply, never auto-applies', async () => {
  const { onApply, load } = setup()
  expect(screen.getByRole('button', { name: '시간 후보 계산' }).hasAttribute('disabled')).toBe(true)
  input()
  await screen.findByText('20:00')
  expect(onApply).not.toHaveBeenCalled()
  expect(load).toHaveBeenCalledWith('medication', { meal_end_times: { DINNER: '19:30' }, same_times_every_day: true })
  fireEvent.click(screen.getByRole('button', { name: '후보 적용' }))
  expect(onApply).toHaveBeenCalledWith(['20:00'], { meal_end_times: { DINNER: '19:30' }, same_times_every_day: true, rule_version: 'explicit-after-meal-v1' })
})
it('invalidates applied candidates on meal changes or irregular days', async () => {
  const { onInvalidate } = setup()
  input()
  fireEvent.click(await screen.findByRole('button', { name: '후보 적용' }))
  onInvalidate.mockClear()
  fireEvent.change(screen.getByLabelText('합성약 저녁 식사 종료 시각'), { target: { value: '19:45' } })
  expect(onInvalidate).toHaveBeenCalledOnce()
  expect(screen.queryByText('20:00')).toBeNull()
  fireEvent.click(screen.getByRole('checkbox'))
  expect(screen.getByRole('button', { name: '시간 후보 계산' }).hasAttribute('disabled')).toBe(true)
})
it('discards an in-flight response after the input changes', async () => {
  let resolve!: (value: RecommendationResponse) => void
  const pending = new Promise<RecommendationResponse>((r) => { resolve = r })
  setup(vi.fn().mockReturnValue(pending))
  input()
  fireEvent.change(screen.getByLabelText('합성약 저녁 식사 종료 시각'), { target: { value: '19:45' } })
  resolve(response)
  await waitFor(() => expect(screen.getByRole('button', { name: '시간 후보 계산' })).toBeTruthy())
  expect(screen.queryByRole('button', { name: '후보 적용' })).toBeNull()
})
it('unsupported or failed requests offer no candidate', async () => {
  const load = vi.fn().mockResolvedValueOnce({ data: { ...response.data, local_times: [], reason: 'UNSUPPORTED_INSTRUCTION' } }).mockRejectedValueOnce(new Error('network'))
  setup(load)
  input()
  await screen.findByText(/처방에 식사 종류와/)
  expect(screen.queryByRole('button', { name: '후보 적용' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '시간 후보 계산' }))
  await screen.findByText(/후보를 확인하지 못했어요/)
  expect(screen.queryByRole('button', { name: '후보 적용' })).toBeNull()
})
it('manual mode explicitly removes candidate validation', async () => {
  const { onManual } = setup()
  input()
  await screen.findByText('20:00')
  fireEvent.click(screen.getByRole('button', { name: '직접 입력으로 전환' }))
  expect(onManual).toHaveBeenCalledOnce()
  expect(screen.queryByRole('button', { name: '후보 적용' })).toBeNull()
  expect(screen.getByText(/추천 검증 대상이 아니므로/)).toBeTruthy()
})
