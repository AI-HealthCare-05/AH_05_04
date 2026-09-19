import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../src/api/client'
import {
  getMedicationReport,
  type MedicationReportData,
  type MedicationReportPeriod,
} from '../src/api/medicationReports'
import ReportPage from '../src/pages/ReportPage'

vi.mock('../src/api/medicationReports', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/api/medicationReports')>()),
  getMedicationReport: vi.fn(),
}))

function reportFixture(
  periodDays: MedicationReportPeriod = 7,
  overrides: Partial<MedicationReportData> = {},
): MedicationReportData {
  return {
    period_days: periodDays,
    start_date: periodDays === 7 ? '2026-09-09' : '2026-08-17',
    end_date: '2026-09-15',
    timezone: 'Asia/Seoul',
    as_of: '2026-09-15T03:00:00Z',
    counts: {
      taken_count: 3,
      not_taken_count: 2,
      unconfirmed_count: 4,
      pending_count: 1,
      cancelled_count: 0,
    },
    overdue_pending_count: 1,
    adherence_rate: { numerator: 77, denominator: 88, percentage: 12.3 },
    confirmation_rate: { numerator: 55, denominator: 66, percentage: 45.6 },
    records: [
      {
        occurrence_id: '11111111-1111-4111-8111-111111111111',
        prescription_version_id: '22222222-2222-4222-8222-222222222222',
        prescription_version_medication_id: '33333333-3333-4333-8333-333333333333',
        scheduled_local_date: '2026-09-15',
        scheduled_at: '2026-09-15T00:00:00Z',
        confirmation_deadline_at: '2026-09-15T04:00:00Z',
        status: 'CLOSED',
        updated_at: '2026-09-15T03:00:00Z',
        checkin: {
          checkin_id: '44444444-4444-4444-8444-444444444444',
          occurrence_id: '11111111-1111-4111-8111-111111111111',
          status: 'TAKEN',
          taken_at: '2026-09-15T00:00:00Z',
          revision: 1,
          corrected: false,
          updated_at: '2026-09-15T03:00:00Z',
        },
      },
    ],
    clinic: null,
    ...overrides,
  }
}

const clinicFixture = {
  barriers: [
    {
      occurrence_id: '11111111-1111-4111-8111-111111111111',
      scheduled_local_date: '2026-09-15',
      medication_name: '메트포르민',
      barrier_code: 'FORGOT' as const,
      subreason_code: 'MISSED_ALERT' as const,
    },
    {
      occurrence_id: '44444444-4444-4444-8444-444444444444',
      scheduled_local_date: '2026-09-14',
      medication_name: '아토르바스타틴',
      barrier_code: 'FORGOT' as const,
      subreason_code: null,
    },
    {
      occurrence_id: '55555555-5555-4555-8555-555555555555',
      scheduled_local_date: '2026-09-13',
      medication_name: '메트포르민',
      barrier_code: 'MEDICATION_CONCERN' as const,
      subreason_code: 'LONG_TERM_USE' as const,
    },
  ],
  consultation_questions: [
    {
      question_id: 'CONCERN_LONG_TERM',
      text: '장기간 복용해도 괜찮은가요?',
      support_code: 'MEDICATION_CONCERN_GUIDANCE' as const,
      medication_name: '메트포르민',
      last_selected_date: '2026-09-13',
    },
  ],
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function OccurrenceProbe() {
  const navigate = useNavigate()
  return <button type="button" onClick={() => navigate(-1)}>리포트로 돌아가기</button>
}

function renderPage(path = '/report') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <Routes>
        <Route path="/report" element={<ReportPage />} />
        <Route path="/report/clinic" element={<ReportPage />} />
        <Route path="/schedule" element={<div>복약 일정 화면</div>} />
        <Route path="/schedule/unconfirmed" element={<div>미확인 복약 기록 화면</div>} />
        <Route path="/schedule/occurrences/:occurrenceId" element={<OccurrenceProbe />} />
        <Route path="/login" element={<div>로그인 화면</div>} />
        <Route path="/menu" element={<div>메뉴 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(getMedicationReport).mockResolvedValue({ data: reportFixture() })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Plan B 복약 리포트', () => {
  it('loading 후 서버 counts와 복용률을 계산 없이 그대로 표시한다', async () => {
    vi.mocked(getMedicationReport).mockImplementation(() => new Promise(() => undefined))
    const first = renderPage()
    expect(screen.getByText('복약 리포트를 불러오는 중이에요')).toBeTruthy()
    first.unmount()

    vi.mocked(getMedicationReport).mockResolvedValue({ data: reportFixture() })
    renderPage()
    expect(await screen.findByText('3회')).toBeTruthy()
    expect(screen.getByText('2회')).toBeTruthy()
    expect(screen.getByText('4회')).toBeTruthy()
    expect(
      screen.getByText(
        (_, element) => element?.textContent === '분자 77 / 분모 88회 · 12.3%',
      ),
    ).toBeTruthy()
    expect(screen.getByText('복용 77회 ÷ 확인된 기록 88회')).toBeTruthy()
    // confirmation_rate 는 REPORT-01 화면에 별도 카드로 노출하지 않는다.
    expect(
      screen.getByText('기록 확인률', { exact: false }),
    ).toBeTruthy()

    expect(
      screen.getByText('45.6%', { exact: false }),
    ).toBeTruthy()

    expect(screen.queryByText('오늘 남은 일정')).toBeNull()
    // counts 3종은 compact summary 로 계속 표시한다.
    const countNames = screen
      .getAllByRole('listitem')
      .map((item) => item.querySelector('.report-count__name')?.textContent)
      .filter(Boolean)
    expect(countNames).toEqual(['복용', '미복용', '미확인'])
  })

  it('overdue_pending_count가 있으면 예정 표시가 지연 때문일 수 있음을 알린다', async () => {
    const { container } = renderPage()

    expect(await screen.findByText('3회')).toBeTruthy()
    const notice = container.querySelector('.report-records__overdue')
    expect(notice?.textContent).toContain('확인 기한이 지난 기록 1건')
    expect(notice?.textContent).toContain('예정으로 보일 수 있어요')
  })

  it('overdue_pending_count가 0이면 지연 안내를 표시하지 않는다', async () => {
    vi.mocked(getMedicationReport).mockResolvedValue({
      data: reportFixture(7, { overdue_pending_count: 0 }),
    })
    const { container } = renderPage()

    expect(await screen.findByText('3회')).toBeTruthy()
    expect(container.querySelector('.report-records__overdue')).toBeNull()
  })

  it('7일·30일 버튼의 accessible state를 표시하고 전환 시 서버를 다시 조회한다', async () => {
    vi.mocked(getMedicationReport).mockImplementation(async (period) => ({ data: reportFixture(period) }))
    renderPage()
    expect(await screen.findByText('3회')).toBeTruthy()
    expect(screen.getByRole('button', { name: '7일' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: '30일' }).getAttribute('aria-pressed')).toBe('false')

    fireEvent.click(screen.getByRole('button', { name: '30일' }))

    await waitFor(() => expect(getMedicationReport).toHaveBeenLastCalledWith(30, undefined, undefined))
    expect(screen.getByRole('button', { name: '30일' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('location').textContent).toBe('/report?period=30')
  })

  it('percentage null은 계산할 기록 없음과 분자/분모만 표시하고 0%를 표시하지 않는다', async () => {
    vi.mocked(getMedicationReport).mockResolvedValue({
      data: reportFixture(7, {
        adherence_rate: { numerator: 0, denominator: 0, percentage: null },
        confirmation_rate: { numerator: 0, denominator: 0, percentage: null },
      }),
    })
    renderPage()
    expect((await screen.findAllByText('계산할 기록 없음')).length).toBe(2)
    expect(screen.queryByText('0%')).toBeNull()
    expect(screen.getByText('복용 0회 ÷ 확인된 기록 0회')).toBeTruthy()
    expect(screen.queryByText('0%')).toBeNull()
  })

  it('denominator가 0이면 percentage 값과 무관하게 계산할 기록 없음으로 표시한다', async () => {
    vi.mocked(getMedicationReport).mockResolvedValue({
      data: reportFixture(7, {
        adherence_rate: { numerator: 0, denominator: 0, percentage: 0 },
        confirmation_rate: { numerator: 0, denominator: 0, percentage: 0 },
      }),
    })
    renderPage()
    expect((await screen.findAllByText('계산할 기록 없음')).length).toBe(2)
    expect(screen.queryByText('0%')).toBeNull()
  })

  it('기록이 없으면 empty state를 표시한다', async () => {
    vi.mocked(getMedicationReport).mockResolvedValue({
      data: reportFixture(7, {
        records: [],
        counts: {
          taken_count: 0,
          not_taken_count: 0,
          unconfirmed_count: 0,
          pending_count: 0,
          cancelled_count: 0,
        },
        adherence_rate: { numerator: 0, denominator: 0, percentage: null },
        confirmation_rate: { numerator: 0, denominator: 0, percentage: null },
      }),
    })
    renderPage()
    expect(await screen.findByText('7일 동안 복약 기록이 없어요')).toBeTruthy()
    expect(
      screen.getByRole('heading', { name: '복약 상태 요약' }),
    ).toBeTruthy()
    expect(screen.getAllByText('0회')).toHaveLength(3)
    expect(screen.getByText('기록 확인률')).toBeTruthy()
    expect(screen.getAllByText('계산할 기록 없음')).toHaveLength(2)
    expect(screen.queryByText('오늘 남은 일정')).toBeNull()

  })

  it('계약에 없는 추이를 임의 생성하지 않고 서버 기록만 표시한다', async () => {
    renderPage()

    expect(
     await screen.findByRole('heading', { name: '복약 흐름' }),
    ).toBeTruthy()

     expect(screen.queryByText('기본 추이')).toBeNull()
    expect(
      screen.queryByText(
        '서버에서 제공하는 추이 데이터가 없어 표시하지 않았어요.',
      ),
    ).toBeNull()

     expect(document.querySelector('canvas')).toBeNull()
    expect(screen.getAllByText('복용').length).toBeGreaterThanOrEqual(2)
  })

  it('진료 보기에서만 미복용 사유와 상담 질문을 보여준다', async () => {
    vi.mocked(getMedicationReport).mockResolvedValue({
      data: reportFixture(7, { clinic: clinicFixture }),
    })

    renderPage('/report/clinic?period=7')

    expect(
      await screen.findByRole('heading', { name: '미복용 사유' }),
    ).toBeTruthy()

    // Tallied in check-in order, not in the order the entries arrived.
    const tally = document.querySelectorAll(
      '.report-clinic-barriers__tally li',
    )
    expect(tally.length).toBe(2)
    expect(tally[0].textContent).toContain('깜빡함')
    expect(tally[0].textContent).toContain('2회')
    expect(tally[1].textContent).toContain('약에 대한 걱정')

    const detail = document.querySelectorAll(
      '.report-clinic-barriers__detail li',
    )
    expect(detail.length).toBe(3)
    expect(detail[0].textContent).toContain('알림을 보거나 듣지 못했어요')
    // No subreason falls back to the barrier's own short label.
    expect(detail[1].textContent).toContain('깜빡함')
    expect(detail[1].textContent).toContain('아토르바스타틴')

    expect(
      screen.getByRole('heading', { name: '진료 때 확인하고 싶은 질문' }),
    ).toBeTruthy()
    expect(screen.getByText('장기간 복용해도 괜찮은가요?')).toBeTruthy()

    expect(getMedicationReport).toHaveBeenCalledWith(7, undefined, 'CLINIC')
  })

  it('기본 리포트는 미복용 사유나 상담 질문을 요청하지도 보여주지도 않는다', async () => {
    renderPage('/report?period=7')

    await screen.findByRole('heading', { name: '복약 리포트' })

    expect(getMedicationReport).toHaveBeenCalledWith(7, undefined, undefined)
    expect(screen.queryByRole('heading', { name: '미복용 사유' })).toBeNull()
    expect(
      screen.queryByRole('heading', { name: '진료 때 확인하고 싶은 질문' }),
    ).toBeNull()
  })

  it('진료 보기는 같은 응답 수치를 Figma 구조로 재배치하고 기간을 유지한다', async () => {
    vi.mocked(getMedicationReport).mockResolvedValue({
      data: reportFixture(30),
    })

    renderPage('/report/clinic?period=30')

    expect(
      await screen.findByRole('heading', {
        name: '진료 시 보여주기',
      }),
    ).toBeTruthy()

    expect(
      screen.getByText(
        '사용자가 직접 기록한 내용을 정리한 화면입니다.',
      ),
    ).toBeTruthy()

    expect(
      screen.getByRole('heading', { name: '핵심 요약' }),
    ).toBeTruthy()

    expect(
      screen.getByText(/복용률\s*12\.3%/),
    ).toBeTruthy()

    expect(screen.getByText('미복용 2회')).toBeTruthy()
    expect(screen.getByText(/최근\s*30일/)).toBeTruthy()

    expect(
      screen.getByRole('heading', { name: '확인된 기록' }),
    ).toBeTruthy()

    expect(
      screen.getByText('기록 확인률', { exact: false }),
    ).toBeTruthy()

    expect(
      screen.getByText('45.6%', { exact: false }),
    ).toBeTruthy()

    expect(screen.queryByText('오늘 남은 일정')).toBeNull()

    expect(getMedicationReport).toHaveBeenCalledWith(30, undefined, 'CLINIC')

    expect(
      screen.queryByRole('button', {
        name: '미확인 기록 보완',
      }),
    ).toBeNull()

    expect(
      screen.queryByRole('navigation', { name: '주요 메뉴' }),
    ).toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: '이전 화면' }),
    )

    expect(screen.getByTestId('location').textContent).toBe(
      '/report?period=30',
    )
  })

  it('복약 기록 보완으로 이동하고 돌아와 focus되면 서버 집계를 다시 GET한다', async () => {
    const refreshed = reportFixture(7, {
      counts: {
        taken_count: 9,
        not_taken_count: 1,
        unconfirmed_count: 0,
        pending_count: 0,
        cancelled_count: 0,
      },
    })
    vi.mocked(getMedicationReport)
      .mockResolvedValueOnce({ data: reportFixture() })
      .mockResolvedValueOnce({ data: refreshed })
    const first = renderPage()
    expect(await screen.findByText('3회')).toBeTruthy()
    fireEvent(window, new Event('focus'))
    expect(await screen.findByText('9회')).toBeTruthy()
    expect(getMedicationReport).toHaveBeenCalledTimes(2)
    first.unmount()

    vi.mocked(getMedicationReport).mockResolvedValue({ data: reportFixture() })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '미확인 기록 보완' }))
    expect(screen.getByText('미확인 복약 기록 화면')).toBeTruthy()
  })

  it('기존 occurrence 정정 경로에서 돌아오면 서버 집계로 stale 수치를 교체한다', async () => {
    vi.mocked(getMedicationReport)
      .mockResolvedValueOnce({ data: reportFixture() })
      .mockResolvedValueOnce({
        data: reportFixture(7, {
          counts: {
            taken_count: 8,
            not_taken_count: 1,
            unconfirmed_count: 0,
            pending_count: 0,
            cancelled_count: 0,
          },
        }),
      })
    renderPage()
    fireEvent.click(await screen.findByRole('link', { name: '기록 확인·정정' }))
    expect(screen.getByTestId('location').textContent).toBe(
      '/schedule/occurrences/11111111-1111-4111-8111-111111111111?date=2026-09-15',
    )
    fireEvent.click(screen.getByRole('button', { name: '리포트로 돌아가기' }))
    expect(await screen.findByText('8회')).toBeTruthy()
    expect(screen.queryByText('3회')).toBeNull()
    expect(getMedicationReport).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['404', new ApiError(404, 'hidden'), '복약 리포트가 노출되지 않았어요', false],
    ['network', new TypeError('Failed to fetch'), '네트워크 연결을 확인', true],
    ['5xx', new ApiError(503, 'private detail'), '리포트 서비스에 잠시 연결할 수 없어요', true],
  ])('%s 오류는 내부 상세 없이 계약대로 표시한다', async (_label, error, message, retryable) => {
    vi.mocked(getMedicationReport).mockRejectedValueOnce(error)
    renderPage()
    const state = await screen.findByText(message, { exact: false })
    expect(state).toBeTruthy()
    expect(document.body.textContent).not.toContain('private detail')
    expect(Boolean(screen.queryByRole('button', { name: '다시 시도' }))).toBe(retryable)
  })

  it('예상하지 못한 응답 처리 오류는 네트워크 재시도로 오인하지 않는다', async () => {
    vi.mocked(getMedicationReport).mockRejectedValueOnce(new SyntaxError('invalid json'))
    renderPage()
    expect(await screen.findByText('리포트 응답을 확인하지 못했어요.')).toBeTruthy()
    expect(screen.queryByText('네트워크 연결을 확인', { exact: false })).toBeNull()
    expect(screen.queryByRole('button', { name: '다시 시도' })).toBeNull()
  })

  it('재시도는 같은 기간의 서버 리포트를 다시 GET한다', async () => {
    vi.mocked(getMedicationReport)
      .mockRejectedValueOnce(new ApiError(503, 'down'))
      .mockResolvedValueOnce({ data: reportFixture() })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '다시 시도' }))
    expect(await screen.findByText('3회')).toBeTruthy()
    expect(getMedicationReport).toHaveBeenNthCalledWith(2, 7, undefined, undefined)
  })

  it('401은 세션을 정리하고 로그인 route로 복구한다', async () => {
    localStorage.setItem('access_token', 'expired-token')
    vi.mocked(getMedicationReport).mockRejectedValueOnce(new ApiError(401, 'private detail'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '다시 로그인' }))
    expect(screen.getByText('로그인 화면')).toBeTruthy()
    expect(localStorage.getItem('access_token')).toBeNull()
  })
})
