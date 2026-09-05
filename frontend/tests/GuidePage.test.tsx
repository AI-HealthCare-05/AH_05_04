import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom'
import { ApiError } from '../src/api/client'
import { getGuide, type GuideResponse } from '../src/api/guides'
import GuidePage from '../src/pages/GuidePage'

vi.mock('../src/api/guides', () => ({
  getGuide: vi.fn(),
}))

function GuideRouteControls() {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate('/guides/guide-b')}>
      Guide B로 이동
    </button>
  )
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function ChatRouteProbe() {
  const navigate = useNavigate()
  return (
    <div>
      도지 대화 화면
      <button type="button" onClick={() => navigate(-1)}>뒤로가기</button>
    </div>
  )
}

function renderPage(entry = '/guides/guide-1', withRouteControls = false) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      {withRouteControls && <GuideRouteControls />}
      <LocationProbe />
      <Routes>
        <Route path="/guides" element={<GuidePage />} />
        <Route path="/guides/:guideId" element={<GuidePage />} />
        <Route path="/prescriptions/upload" element={<div>처방전 업로드 화면</div>} />
        <Route path="/" element={<div>홈 화면</div>} />
        <Route path="/menu" element={<div>메뉴 화면</div>} />
        <Route path="/chat" element={<ChatRouteProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

function completedGuideResponse(
  guideId: string,
  content: string | null,
): GuideResponse {
  return {
    data: {
      guide_id: guideId,
      prescription_id: `prescription-${guideId}`,
      generation_status: 'COMPLETED',
      content,
      model_name: 'guide-model',
      prompt_version: 'guide-prompt-v1',
      requested_at: '2026-08-22T00:00:00Z',
      completed_at: '2026-08-22T00:00:03Z',
    },
  }
}

function structuredGuideContent(medicationCount = 1) {
  const medications = Array.from({ length: medicationCount }, (_, index) => {
    const number = index + 1
    return [
      `[${number}] 합성 처방약 ${number} 매우 긴 이름`,
      '용량: 1 정',
      `복용 횟수: 하루 ${number}회`,
      '복용 시점: 아침 저녁 식후',
      `복용 기간: ${number + 4}일`,
      '복약 안내: 처방에 안내된 복용 계획을 확인하고 지켜 주세요.',
    ].join('\n')
  })

  return [
    '복약 가이드',
    ...medications,
    '공통 안내: 불명확한 내용은 의료진 또는 약사에게 확인해 주세요.\n안전 안내: 임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요.',
  ].join('\n\n')
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
})

describe('GuidePage', () => {
  it('표준 Guide 원문을 약별 카드와 의미 있는 라벨 구조로 표시한다', async () => {
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', structuredGuideContent()),
    )

    renderPage()

    expect(
      await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' }),
    ).toBeTruthy()
    const medicationCard = screen
      .getByRole('heading', { name: '합성 처방약 1 매우 긴 이름' })
      .closest('details')
    expect(medicationCard).not.toBeNull()
    expect(medicationCard!.hasAttribute('open')).toBe(false)

    fireEvent.click(within(medicationCard!).getByText('합성 처방약 1 매우 긴 이름'))

    expect(medicationCard!.hasAttribute('open')).toBe(true)
    expect(within(medicationCard!).getByText('1회량').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('1 정').tagName).toBe('DD')
    expect(within(medicationCard!).getByText('하루 횟수').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('하루 1회').tagName).toBe('DD')
    expect(within(medicationCard!).getByText('복용 시점').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('아침 저녁 식후').tagName).toBe('DD')
    expect(within(medicationCard!).getByText('복용 기간').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('5일').tagName).toBe('DD')
    expect(within(medicationCard!).getByRole('heading', { name: '복약 안내' })).toBeTruthy()
    expect(
      within(medicationCard!).getByText(
        '처방에 안내된 복용 계획을 확인하고 지켜 주세요.',
      ),
    ).toBeTruthy()
    const commonNotice = screen
      .getByRole('heading', { name: '공통 복약 안내' })
      .closest('aside')
    expect(commonNotice).not.toBeNull()
    expect(
      within(commonNotice!).getByText(
        '불명확한 내용은 의료진 또는 약사에게 확인해 주세요.',
      ),
    ).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '안전 안내' })).toBeNull()
    expect(
      screen.queryByText(
        '임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요.',
      ),
    ).toBeNull()
    expect(within(medicationCard!).getByText('하루 1회 · 아침 저녁 식후')).toBeTruthy()
    expect(screen.queryByText('가이드 전체 내용')).toBeNull()
  })

  it('약이 4개 이상이어도 모든 약을 독립된 카드로 표시한다', async () => {
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', structuredGuideContent(4)),
    )

    renderPage()

    expect(
      await screen.findByRole('heading', { name: '확인된 약 목록 · 4개' }),
    ).toBeTruthy()
    for (let number = 1; number <= 4; number += 1) {
      expect(
        screen.getByRole('heading', {
          name: `합성 처방약 ${number} 매우 긴 이름`,
        }),
      ).toBeTruthy()
    }
    expect(document.querySelectorAll('.guide-page__medication-card')).toHaveLength(4)
  })

  it('예상하지 못한 Guide 형식은 원문을 생략하지 않고 평문으로 표시한다', async () => {
    const content = '자유 형식 제목\n예상하지 못한 항목: 그대로 보존\n마지막 안내'
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', content),
    )

    renderPage()

    expect(await screen.findByText('가이드 전체 내용')).toBeTruthy()
    expect(document.querySelector('.guide-page__guide-text')?.textContent).toBe(
      content,
    )
    expect(screen.queryByText(/확인된 약 목록/)).toBeNull()
  })

  it('뒤쪽 약이 malformed이면 앞쪽 약만 카드로 표시하지 않고 원문 전체로 fallback한다', async () => {
    const content = [
      '복약 가이드',
      [
        '[1] 합성 처방약 1',
        '용량: 1 정',
        '복용 횟수: 하루 1회',
        '복용 시점: 아침 식후',
        '복용 기간: 5일',
        '복약 안내: 처방에 안내된 복용 계획을 지켜 주세요.',
      ].join('\n'),
      [
        '[2] 합성 처방약 2',
        '용량: 1 정',
        '복용 횟수: 하루 2회',
        '복용 시점 저녁 식후',
        '복용 기간: 7일',
        '복약 안내: 처방에 안내된 복용 계획을 지켜 주세요.',
      ].join('\n'),
      '공통 안내: 불명확한 내용은 의료진에게 확인해 주세요.\n안전 안내: 임의로 복용을 변경하지 마세요.',
    ].join('\n\n')
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', content),
    )

    renderPage()

    expect(await screen.findByText('가이드 전체 내용')).toBeTruthy()
    expect(document.querySelector('.guide-page__guide-text')?.textContent).toBe(content)
    expect(screen.queryByText(/확인된 약 목록/)).toBeNull()
    expect(document.querySelectorAll('.guide-page__medication-card')).toHaveLength(0)
  })

  it('실제 Guide 조회 응답의 평문 content를 표시한다', async () => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        generation_status: 'COMPLETED',
        content: '처방약 1\n- 하루 3회 복용하세요.\n\n일반 안전 안내',
        model_name: 'guide-model',
        prompt_version: 'guide-prompt-v1',
        requested_at: '2026-08-22T00:00:00Z',
        completed_at: '2026-08-22T00:00:03Z',
      },
    })

    renderPage()

    expect(await screen.findByText('확인된 복약 안내')).toBeTruthy()
    expect(screen.getByText(/하루 3회 복용하세요/)).toBeTruthy()
    await waitFor(() => expect(getGuide).toHaveBeenCalledWith('guide-1'))
  })

  it('완료된 content가 없으면 빈 상태를 표시한다', async () => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        generation_status: 'COMPLETED',
        content: null,
        model_name: null,
        prompt_version: null,
        requested_at: '2026-08-22T00:00:00Z',
        completed_at: null,
      },
    })

    renderPage()

    expect(
      await screen.findByText('가이드 내용이 아직 없어요'),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: '다시 불러오기' })).toBeTruthy()
  })

  it('Guide가 없는 경로에서 GUIDE-01 상태와 업로드 CTA를 표시한다', async () => {
    renderPage('/guides')

    expect(await screen.findByText('아직 만들어진 가이드가 없어요')).toBeTruthy()
    expect(getGuide).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '처방전 등록하기' }))
    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()
  })

  it('GENERATING 응답을 최신 생성 중 상태로 표시하고 실제 조회만 다시 시도한다', async () => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        generation_status: 'GENERATING',
        content: null,
        model_name: null,
        prompt_version: null,
        requested_at: '2026-08-22T00:00:00Z',
        completed_at: null,
      },
    })

    renderPage()

    expect(await screen.findByText('복약 가이드를 만들고 있어요')).toBeTruthy()
    expect(screen.getByText('가이드를 생성하고 있어요...')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '다시 확인하기' }))
    await waitFor(() => expect(getGuide).toHaveBeenCalledTimes(2))
  })

  it('FAILED 응답을 최신 실패 상태로 표시하고 raw 오류 상태를 만들지 않는다', async () => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        generation_status: 'FAILED',
        content: null,
        model_name: 'guide-model',
        prompt_version: 'guide-prompt-v1',
        requested_at: '2026-08-22T00:00:00Z',
        completed_at: '2026-08-22T00:00:03Z',
      },
    })

    renderPage()

    expect(await screen.findByText('가이드를 만들지 못했어요')).toBeTruthy()
    expect(screen.getByText('다시 시도해 주세요.')).toBeTruthy()
    expect(screen.getByRole('button', { name: '다시 시도하기' })).toBeTruthy()
  })

  it('공통 Navigation의 Guide active, 일정 disabled, 기존 route 이동을 유지한다', async () => {
    const firstRender = renderPage('/guides')

    await screen.findByText('아직 만들어진 가이드가 없어요')
    expect(screen.getByRole('button', { name: '가이드' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('button', { name: '일정 (준비 중)' })).toHaveProperty(
      'disabled',
      true,
    )
    fireEvent.click(screen.getByRole('button', { name: '메뉴' }))
    expect(screen.getByText('메뉴 화면')).toBeTruthy()

    firstRender.unmount()
    renderPage('/guides')
    await screen.findByText('아직 만들어진 가이드가 없어요')
    fireEvent.click(screen.getByRole('button', { name: '홈' }))
    expect(screen.getByText('홈 화면')).toBeTruthy()
  })

  it('상세 Guide에서 active 가이드 탭을 재클릭해도 현재 상세 route와 내용을 유지한다', async () => {
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', '현재 Guide 내용'),
    )

    renderPage('/guides/guide-1')

    expect(await screen.findByText('현재 Guide 내용')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-1')

    fireEvent.click(screen.getByRole('button', { name: '가이드' }))

    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-1')
    expect(screen.getByText('현재 Guide 내용')).toBeTruthy()
    expect(getGuide).toHaveBeenCalledTimes(1)
  })

  it('Guide A의 느린 응답이 route 전환 후 Guide B를 덮어쓰지 않는다', async () => {
    const guideA = deferred<GuideResponse>()
    const guideB = deferred<GuideResponse>()
    vi.mocked(getGuide).mockImplementation((guideId) =>
      guideId === 'guide-a' ? guideA.promise : guideB.promise,
    )

    renderPage('/guides/guide-a', true)
    await waitFor(() => expect(getGuide).toHaveBeenCalledWith('guide-a'))

    fireEvent.click(screen.getByRole('button', { name: 'Guide B로 이동' }))
    await waitFor(() => expect(getGuide).toHaveBeenCalledWith('guide-b'))

    await act(async () => {
      guideB.resolve(completedGuideResponse('guide-b', 'Guide B 내용'))
      await guideB.promise
    })
    expect(await screen.findByText('Guide B 내용')).toBeTruthy()

    await act(async () => {
      guideA.resolve(completedGuideResponse('guide-a', 'Guide A 내용'))
      await guideA.promise
    })

    await waitFor(() => {
      expect(screen.queryByText('Guide A 내용')).toBeNull()
      expect(screen.getByText('Guide B 내용')).toBeTruthy()
    })
  })

  it('route의 guide_id와 다른 Guide 응답을 표시하지 않는다', async () => {
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-other', '다른 Guide 내용'),
    )

    renderPage('/guides/guide-1')

    expect(
      await screen.findByText('요청한 가이드와 다른 응답을 받았어요. 다시 불러와 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByText('다른 Guide 내용')).toBeNull()
  })

  it('Guide 조회 실패에서 raw Backend 오류를 숨긴다', async () => {
    vi.mocked(getGuide).mockRejectedValue(
      new ApiError(503, 'provider stack and internal guide detail', 'PROVIDER_DOWN'),
    )

    renderPage()

    expect(
      await screen.findByText('서버 응답이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByText(/provider stack|PROVIDER_DOWN/)).toBeNull()
  })

  it('상세 Guide의 도지 탭은 현재 prescription_id를 보존한다', async () => {
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', structuredGuideContent()),
    )

    renderPage()
    await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' })

    fireEvent.click(screen.getByRole('button', { name: '도지' }))

    expect(screen.getByText('도지 대화 화면')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe(
      '/chat?prescription_id=prescription-guide-1',
    )

    fireEvent.click(screen.getByRole('button', { name: '뒤로가기' }))
    expect(await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' })).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-1')
  })
})
