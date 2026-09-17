import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import {
  type MedicationDayResponse,
  type MedicationOccurrenceMedicationResponse,
  getMedicationOccurrenceMedication,
  getMedicationOccurrencesByDate,
  isMedicationOccurrenceNotFoundError,
  isOccurrenceMedicationUnavailableError,
  resolveNotificationOccurrenceMedication,
} from '../src/api/medicationOccurrences'

const occurrenceId = '22222222-2222-4222-8222-222222222222'
const versionId = '44444444-4444-4444-8444-444444444444'
const medicationId = '55555555-5555-4555-8555-555555555555'
const localDate = '2026-09-13'

function makeDayResponse(): MedicationDayResponse {
  return {
    data: {
      schedule_status: 'READY',
      schedule_items: [
        {
          prescription_version_medication_id: medicationId,
          schedule_item_status: 'READY',
          schedule_id: '66666666-6666-4666-8666-666666666666',
          revision: 1,
          setup_reason: null,
          schedule: {
            schedule_id: '66666666-6666-4666-8666-666666666666',
            prescription_version_medication_id: medicationId,
            revision: 1,
            status: 'ACTIVE',
            start_local_date: '2026-09-10',
            end_mode: 'OPEN_ENDED',
            end_local_date: null,
            local_times: ['09:00'],
          },
        },
      ],
      occurrences: [
        {
          occurrence_id: occurrenceId,
          prescription_version_id: versionId,
          prescription_version_medication_id: medicationId,
          scheduled_local_date: localDate,
          scheduled_at: '2026-09-14T00:30:00Z',
          confirmation_deadline_at: '2026-09-14T12:30:00Z',
          status: 'PENDING',
          checkin: null,
        },
      ],
    },
  }
}

function makeMedicationResponse(
  overrides: Partial<MedicationOccurrenceMedicationResponse['data']> = {},
): MedicationOccurrenceMedicationResponse {
  return {
    data: {
      occurrence_id: occurrenceId,
      prescription_version_id: versionId,
      prescription_version_medication_id: medicationId,
      medication_name: 'Historical medication snapshot',
      strength_text: '10 mg',
      dose_value: 1,
      dose_unit: 'tablet',
      ...overrides,
    },
  }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeErrorBody(code: string) {
  return { code, message: 'error', details: [], trace_id: 'trace-1' }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('medication occurrence API adapter', () => {
  it('queries occurrences with the supplied local date', async () => {
    const responseBody = makeDayResponse()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(responseBody))
    vi.stubGlobal('fetch', fetchMock)

    await expect(getMedicationOccurrencesByDate(localDate)).resolves.toEqual(
      responseBody,
    )
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/medication-occurrences?date=${localDate}`,
      expect.any(Object),
    )
  })

  it('returns the historical occurrence medication detail DTO unchanged', async () => {
    const responseBody = makeMedicationResponse()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(responseBody))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      getMedicationOccurrenceMedication(occurrenceId),
    ).resolves.toEqual(responseBody)
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8000/api/v1/medication-occurrences/${occurrenceId}/medication`,
      expect.any(Object),
    )
  })

  it('keeps SELF and missing occurrence detail in one 404 branch', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse(makeErrorBody('MEDICATION_OCCURRENCE_NOT_FOUND'), 404),
        ),
    )

    const error = await getMedicationOccurrenceMedication(occurrenceId).catch(
      (caught: unknown) => caught,
    )

    expect(error).toBeInstanceOf(ApiError)
    expect(isMedicationOccurrenceNotFoundError(error)).toBe(true)
  })
})

describe('notification occurrence medication resolver', () => {
  it('uses the notification local date and resolves the exact historical IDs', async () => {
    const day = makeDayResponse()
    const medication = makeMedicationResponse()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(day))
      .mockResolvedValueOnce(jsonResponse(medication))
    vi.stubGlobal('fetch', fetchMock)

    const resolved = await resolveNotificationOccurrenceMedication({
      occurrenceId,
      occurrenceLocalDate: localDate,
    })

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `http://localhost:8000/api/v1/medication-occurrences?date=${localDate}`,
    )
    expect(day.data.occurrences[0]?.scheduled_at).toBe(
      '2026-09-14T00:30:00Z',
    )
    expect(resolved).toEqual({
      occurrenceLocalDate: localDate,
      occurrence: day.data.occurrences[0],
      medication: medication.data,
    })
  })

  it.each([
    ['occurrence_id', '77777777-7777-4777-8777-777777777777'],
    ['prescription_version_id', '77777777-7777-4777-8777-777777777777'],
    [
      'prescription_version_medication_id',
      '77777777-7777-4777-8777-777777777777',
    ],
  ] as const)(
    'fails neutrally on %s mismatch without another fallback request',
    async (field, mismatchedId) => {
      const fetchMock = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(makeDayResponse()))
        .mockResolvedValueOnce(
          jsonResponse(makeMedicationResponse({ [field]: mismatchedId })),
        )
      vi.stubGlobal('fetch', fetchMock)

      const error = await resolveNotificationOccurrenceMedication({
        occurrenceId,
        occurrenceLocalDate: localDate,
      }).catch((caught: unknown) => caught)

      expect(isOccurrenceMedicationUnavailableError(error)).toBe(true)
      expect(fetchMock).toHaveBeenCalledTimes(2)
    },
  )

  it('fails neutrally when the date has no matching occurrence and makes no fallback', async () => {
    const day = makeDayResponse()
    day.data.occurrences = []
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(day))
    vi.stubGlobal('fetch', fetchMock)

    const error = await resolveNotificationOccurrenceMedication({
      occurrenceId,
      occurrenceLocalDate: localDate,
    }).catch((caught: unknown) => caught)

    expect(isOccurrenceMedicationUnavailableError(error)).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('fails neutrally when the returned occurrence local date disagrees with the handoff', async () => {
    const day = makeDayResponse()
    day.data.occurrences[0]!.scheduled_local_date = '2026-09-12'
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(day))
    vi.stubGlobal('fetch', fetchMock)

    const error = await resolveNotificationOccurrenceMedication({
      occurrenceId,
      occurrenceLocalDate: localDate,
    }).catch((caught: unknown) => caught)

    expect(isOccurrenceMedicationUnavailableError(error)).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('converts a hidden medication 404 to the same neutral unavailable error', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(makeDayResponse()))
      .mockResolvedValueOnce(
        jsonResponse(makeErrorBody('MEDICATION_OCCURRENCE_NOT_FOUND'), 404),
      )
    vi.stubGlobal('fetch', fetchMock)

    const error = await resolveNotificationOccurrenceMedication({
      occurrenceId,
      occurrenceLocalDate: localDate,
    }).catch((caught: unknown) => caught)

    expect(isOccurrenceMedicationUnavailableError(error)).toBe(true)
  })
})
