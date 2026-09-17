import { afterEach, expect, it, vi } from 'vitest'
import { createSafety, putBarrier, createPlan, getOffers, getPlan, patchPlan } from '../src/api/trackC'
afterEach(() => vi.unstubAllGlobals())
it('sends exact contract bodies and caller-owned keys, and does not mutate on GET', async () => {
  const fetchMock = vi.fn()
  // A fresh response is needed for each read of its stream.
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ data: {} }), { headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  await createSafety({ medication_checkin_id: 'checkin', checkin_revision: 2, symptom_codes: [], expected_revision: 0 }, 'safety-attempt')
  await putBarrier('checkin', { response_status: 'DECLINED', barrier_code: null, checkin_revision: 2, expected_revision: 0 }, 'barrier-attempt')
  await getOffers('barrier')
  await createPlan({ barrier_response_id: 'barrier', support_code: 'REMINDER_SETUP', rule_version: 'rule1', copy_version: 'copy1', confirmed: true }, 'plan-attempt')
  await getPlan('plan')
  await patchPlan('plan', { status: 'CANCELLED', confirmed: true }, 'patch-attempt')
  const calls = fetchMock.mock.calls as unknown as [string, RequestInit][]
  expect(calls.map(([url]) => new URL(url).pathname)).toEqual(['/api/v1/safety-assessments', '/api/v1/medication-checkins/checkin/barrier-response', '/api/v1/barrier-responses/barrier/supports', '/api/v1/support-action-plans', '/api/v1/support-action-plans/plan', '/api/v1/support-action-plans/plan'])
  expect(calls.map(([, options]) => options.method ?? 'GET')).toEqual(['POST', 'PUT', 'GET', 'POST', 'GET', 'PATCH'])
  for (const index of [2, 4]) { expect(calls[index][1].body).toBeUndefined(); expect(calls[index][1].cache).toBe('no-store') }
  expect(new Headers(calls[0][1].headers).get('Idempotency-Key')).toBe('safety-attempt')
  expect(new Headers(calls[0][1].headers).get('Content-Type')).toBe('application/json')
  expect(JSON.parse(calls[5][1].body as string)).toEqual({ status: 'CANCELLED', confirmed: true })
})

it('transmits the chosen situation on offer GET and confirmed Plan POST', async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ data: {} }), { headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  await getOffers('barrier', 'MEDICATION_NOT_WITH_ME')
  await createPlan({ barrier_response_id: 'barrier', support_code: 'ROUTINE_OR_TRAVEL_PLAN', rule_version: 'rule1', copy_version: 'copy1', travel_situation: 'MEDICATION_NOT_WITH_ME', confirmed: true }, 'packing-attempt')
  const calls = fetchMock.mock.calls as unknown as [string, RequestInit][]
  expect(new URL(calls[0][0]).searchParams.get('travel_situation')).toBe('MEDICATION_NOT_WITH_ME')
  expect(calls[0][1].body).toBeUndefined()
  expect(JSON.parse(calls[1][1].body as string).travel_situation).toBe('MEDICATION_NOT_WITH_ME')
  expect(new Headers(calls[1][1].headers).get('Idempotency-Key')).toBe('packing-attempt')
})

it('uses followup envelope, exact body and caller-owned key', async () => {
  const { getFollowup, submitFollowup } = await import('../src/api/trackC')
  const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ data: null }), { headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  expect(await getFollowup('plan')).toBeNull()
  await submitFollowup('plan', { response: 'NOT_SURE', expected_revision: 2 }, 'followup-attempt-key')
  const calls = fetchMock.mock.calls as unknown as [string, RequestInit][]
  expect(new URL(calls[0][0]).pathname).toBe('/api/v1/support-action-plans/plan/followups')
  expect(calls[0][1].cache).toBe('no-store')
  expect(new Headers(calls[0][1].headers).has('Idempotency-Key')).toBe(false)
  expect(calls[1][1].method).toBe('POST')
  expect(JSON.parse(calls[1][1].body as string)).toEqual({ response: 'NOT_SURE', expected_revision: 2 })
  expect(new Headers(calls[1][1].headers).get('Idempotency-Key')).toBe('followup-attempt-key')
})
