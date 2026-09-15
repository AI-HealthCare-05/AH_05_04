import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getMedicationReport } from '../src/api/medicationReports'

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
})

describe('Medication Reports API', () => {
  it.each([7, 30] as const)('period_days=%i로 공통 집계 API를 GET한다', async (periodDays) => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ data: { period_days: periodDays } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await getMedicationReport(periodDays)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe(`http://localhost:8000/api/v1/medication-reports?period_days=${periodDays}`)
    expect(options?.method).toBeUndefined()
  })
})
