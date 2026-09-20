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
import type { NavigateFunction } from 'react-router-dom'
import { ApiError } from '../src/api/client'
import {
  createGuide,
  getGuide,
  getGuideForPrescription,
  type GuideCitation,
  type GuideResponse,
} from '../src/api/guides'
import {
  getLatestPrescription,
  type PrescriptionResponse,
} from '../src/api/prescriptions'
import GuidePage from '../src/pages/GuidePage'

vi.mock('../src/api/guides', () => ({
  createGuide: vi.fn(),
  getGuide: vi.fn(),
  getGuideForPrescription: vi.fn(),
}))

vi.mock('../src/api/prescriptions', () => ({
  getLatestPrescription: vi.fn(),
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

function renderPage(
  entry = '/guides/guide-1',
  withRouteControls = false,
  navigation?: NavigateFunction,
) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      {withRouteControls && <GuideRouteControls />}
      <LocationProbe />
      <Routes>
        <Route path="/guides" element={<GuidePage navigation={navigation} />} />
        <Route path="/guides/:guideId" element={<GuidePage navigation={navigation} />} />
        <Route path="/prescriptions/upload" element={<div>처방전 업로드 화면</div>} />
        <Route path="/login" element={<div>로그인 화면</div>} />
        <Route path="/" element={<div>홈 화면</div>} />
        <Route path="/menu" element={<div>메뉴 화면</div>} />
        <Route path="/profile" element={<div>동의 설정 화면</div>} />
        <Route path="/chat" element={<ChatRouteProbe />} />
        <Route path="/schedule" element={<div>복약 일정 화면</div>} />
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
      prescription_version_id: `prescription-version-${guideId}`,
      generation_status: 'COMPLETED',
      content,
      model_name: 'guide-model',
      prompt_version: 'guide-prompt-v1',
      release_decision: null,
      release_is_current: null,
      fallback_code: null,
      fallback_text: null,
      citations: [],
      requested_at: '2026-08-22T00:00:00Z',
      completed_at: '2026-08-22T00:00:03Z',
    },
  }
}

function legacyGuideReleaseFields() {
  return {
    prescription_version_id: 'prescription-version-1',
    release_decision: null,
    release_is_current: null,
    fallback_code: null,
    fallback_text: null,
    citations: [],
  } as const
}

function runtimePassGuideResponse(): GuideResponse {
  return {
    data: {
      ...completedGuideResponse('guide-runtime-pass', structuredGuideContent()).data,
      release_decision: 'PASS',
      release_is_current: true,
      citations: [
        {
          source_type: 'LIFESTYLE_GUIDELINE',
          source_code: 'SYNTHETIC-GUIDELINE',
          source_version: '2026.1',
          locator: 'section-2',
          display_order: 1,
          source_snapshot_id: 'internal-snapshot-id',
          confidence: 0.99,
        } as unknown as GuideCitation,
      ],
    },
  }
}

function runtimeFallbackGuideResponse(
  decision: 'LIMITED' | 'REJECTED' | 'STALE',
): GuideResponse {
  return {
    data: {
      ...completedGuideResponse(`guide-${decision.toLowerCase()}`, null).data,
      release_decision: decision,
      release_is_current: decision !== 'STALE',
      fallback_code:
        decision === 'STALE'
          ? 'PRESCRIPTION_STALE'
          : 'NO_APPROVED_EVIDENCE',
      fallback_text: `${decision} 승인 fallback 안내`,
    },
  }
}

function prescriptionResponse(
  prescriptionId = 'prescription-latest',
): PrescriptionResponse {
  return {
    data: {
      prescription_id: prescriptionId,
      document_id: 'document-latest',
      prescribed_date: '2026-09-07',
      confirmed_at: '2026-09-07T08:00:00Z',
      medications: [],
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
  vi.mocked(getLatestPrescription).mockRejectedValue(
    new ApiError(404, '처방을 찾을 수 없습니다.', 'PRESCRIPTION_NOT_FOUND'),
  )
})

afterEach(() => {
  cleanup()
})

describe('GuidePage', () => {
  it('PASS 응답의 승인된 citation 공개 필드만 순서대로 표시한다', async () => {
    vi.mocked(getGuide).mockResolvedValue(runtimePassGuideResponse())

    renderPage('/guides/guide-runtime-pass')

    expect(
      await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' }),
    ).toBeTruthy()
    const citations = screen.getByRole('heading', { name: '안내 근거' })
      .closest('aside')
    expect(citations).not.toBeNull()
    expect(within(citations!).getByText('SYNTHETIC-GUIDELINE')).toBeTruthy()
    expect(within(citations!).getByText('2026.1')).toBeTruthy()
    expect(within(citations!).getByText('section-2')).toBeTruthy()
    expect(citations!.textContent).not.toContain('source_snapshot_id')
    expect(citations!.textContent).not.toContain('internal-snapshot-id')
    expect(citations!.textContent).not.toContain('confidence')
    expect(citations!.textContent).not.toContain('0.99')
  })

  it.each([
    ['LIMITED', '일부 안내만 제공할 수 있어요'],
    ['REJECTED', '안전한 안내를 제공할 수 없어요'],
    ['STALE', '처방 정보가 변경되었어요'],
  ] as const)('%s 승인 fallback을 빈 Guide로 오인하지 않는다', async (decision, title) => {
    vi.mocked(getGuide).mockResolvedValue(runtimeFallbackGuideResponse(decision))

    renderPage(`/guides/guide-${decision.toLowerCase()}`)

    expect(await screen.findByRole('heading', { name: title })).toBeTruthy()
    expect(screen.getByText(`${decision} 승인 fallback 안내`)).toBeTruthy()
    expect(screen.queryByText('가이드 내용이 아직 없어요')).toBeNull()
    expect(screen.queryByRole('button', { name: '다시 불러오기' })).toBeNull()
  })

  it('알 수 없는 release decision은 content를 표시하지 않고 fail-closed한다', async () => {
    const response = runtimePassGuideResponse()
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        ...response.data,
        release_decision: 'UNKNOWN_RELEASE_DECISION',
      },
    } as unknown as GuideResponse)

    renderPage('/guides/guide-runtime-pass')

    expect(
      await screen.findByRole('heading', {
        name: '가이드를 안전하게 표시할 수 없어요',
      }),
    ).toBeTruthy()
    expect(screen.queryByText('SYNTHETIC-GUIDELINE')).toBeNull()
    expect(screen.queryByRole('heading', { name: '확인된 약 목록 · 1개' })).toBeNull()
  })

  it('순서가 뒤집힌 citation payload는 부분 표시하지 않고 fail-closed한다', async () => {
    const response = runtimePassGuideResponse()
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        ...response.data,
        citations: [
          { ...response.data.citations[0], display_order: 2 },
          {
            ...response.data.citations[0],
            source_code: 'SECOND-GUIDELINE',
            display_order: 1,
          },
        ],
      },
    })

    renderPage('/guides/guide-runtime-pass')

    expect(
      await screen.findByRole('heading', {
        name: '가이드를 안전하게 표시할 수 없어요',
      }),
    ).toBeTruthy()
    expect(screen.queryByText('SYNTHETIC-GUIDELINE')).toBeNull()
    expect(screen.queryByText('SECOND-GUIDELINE')).toBeNull()
  })

  it('객체가 아닌 citation payload도 렌더 예외 없이 fail-closed한다', async () => {
    const response = runtimePassGuideResponse()
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        ...response.data,
        citations: [null],
      },
    } as unknown as GuideResponse)

    renderPage('/guides/guide-runtime-pass')

    expect(
      await screen.findByRole('heading', {
        name: '가이드를 안전하게 표시할 수 없어요',
      }),
    ).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '안내 근거' })).toBeNull()
  })

  it('승인 목록에 없는 fallback code는 안내로 노출하지 않고 fail-closed한다', async () => {
    const response = runtimeFallbackGuideResponse('LIMITED')
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        ...response.data,
        fallback_code: 'UNAPPROVED_FALLBACK',
      },
    } as unknown as GuideResponse)

    renderPage('/guides/guide-limited')

    expect(
      await screen.findByRole('heading', {
        name: '가이드를 안전하게 표시할 수 없어요',
      }),
    ).toBeTruthy()
    expect(screen.queryByText('LIMITED 승인 fallback 안내')).toBeNull()
  })

  it('완료 Guide의 복용 일정 CTA는 mutation 없이 기존 route로 한 번 이동한다', async () => {
    const navigation = vi.fn<NavigateFunction>()
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', structuredGuideContent()),
    )

    renderPage('/guides/guide-1', false, navigation)

    const scheduleCta = await screen.findByRole('button', {
      name: '복용 일정 확인하기',
    })
    expect(scheduleCta).toHaveProperty('disabled', false)
    fireEvent.click(scheduleCta)

    expect(navigation).toHaveBeenCalledTimes(1)
    expect(navigation).toHaveBeenCalledWith('/schedule')
    expect(getGuide).toHaveBeenCalledTimes(1)
  })

  it('표준 Guide 원문을 약별 카드와 의미 있는 라벨 구조로 표시한다', async () => {
    vi.mocked(getGuide).mockResolvedValue(
      completedGuideResponse('guide-1', structuredGuideContent()),
    )

    renderPage()

    expect(
      await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' }),
    ).toBeTruthy()
    const medicationToggle = screen.getByRole('button', {
      name: /합성 처방약 1 매우 긴 이름/,
    })
    const medicationCard = medicationToggle.closest('article')
    expect(medicationCard).not.toBeNull()
    expect(medicationToggle.getAttribute('aria-expanded')).toBe('false')
    expect(medicationToggle.getAttribute('aria-controls')).toBe(
      'guide-medication-panel-0',
    )

    fireEvent.click(medicationToggle)

    expect(medicationToggle.getAttribute('aria-expanded')).toBe('true')
    expect(within(medicationCard!).getByText('1회량').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('1 정').tagName).toBe('DD')
    expect(within(medicationCard!).getByText('하루 횟수').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('하루 1회').tagName).toBe('DD')
    expect(within(medicationCard!).getByText('복용 시점').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('아침 저녁 식후').tagName).toBe('DD')
    expect(within(medicationCard!).getByText('복용 기간').tagName).toBe('DT')
    expect(within(medicationCard!).getByText('5일').tagName).toBe('DD')
    expect(
      within(medicationCard!).getByRole('heading', {
        name: '복약 안내',
      }),
    ).toBeTruthy()
    expect(
      within(medicationCard!).getByText(
        '처방에 안내된 복용 계획을 확인하고 지켜 주세요.',
      ),
    ).toBeTruthy()
    const requestedSectionHeadings = [
      '복용 시 주의해야 할 점',
      '주의해야 할 음식·음료',
      '음주/흡연 안내',
      '나타날 수 있는 불편감',
      '이런 증상은 병원에 가세요',
      '임신·수유 중 안내',
    ]
    expect(
      within(medicationCard!.querySelector('.guide-page__medical-sections')!)
        .getAllByRole('heading', { level: 4 })
        .map((heading) => heading.textContent),
    ).toEqual(requestedSectionHeadings)
    expect(
      within(medicationCard!.querySelector('.guide-page__medical-sections')!)
        .getAllByText('현재 제공된 안내가 없어요.'),
    ).toHaveLength(6)
    const commonNotice = screen
      .getByRole('heading', { name: '공통 복약 안내' })
      .closest('aside')
    expect(commonNotice).not.toBeNull()
    expect(
      within(commonNotice!).getByText(
        '불명확한 내용은 의료진 또는 약사에게 확인해 주세요.',
      ),
    ).toBeTruthy()
    const safetyNotice = screen
      .getByRole('heading', { name: '안전 안내' })
      .closest('aside')
    expect(safetyNotice).not.toBeNull()
    expect(
      within(safetyNotice!).getByText(
        '임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요.',
      ),
    ).toBeTruthy()
    expect(within(medicationCard!).getByText('하루 1회 · 아침 저녁 식후')).toBeTruthy()
    expect(screen.queryByText('가이드 전체 내용')).toBeNull()
  })

  it('세부 의료 섹션 데이터가 전혀 없어도 여섯 섹션을 중립 문구로 유지한다', async () => {
    const content = [
      '복약 가이드',
      [
        '[1] 합성 처방약',
        '용량: 1 정',
        '복용 횟수: 하루 1회',
        '복용 시점: 아침 식후',
        '복용 기간: 5일',
      ].join('\n'),
      '공통 안내:\n안전 안내:',
    ].join('\n\n')
    vi.mocked(getGuide).mockResolvedValue(completedGuideResponse('guide-1', content))

    renderPage()

    const toggle = await screen.findByRole('button', { name: /합성 처방약/ })
    fireEvent.click(toggle)
    const medicationCard = toggle.closest('article')
    expect(medicationCard).not.toBeNull()
    expect(
      within(medicationCard!.querySelector('.guide-page__medical-sections')!)
        .getAllByRole('heading', { level: 4 }),
    ).toHaveLength(6)
    expect(
      within(medicationCard!.querySelector('.guide-page__medical-sections')!)
        .getAllByText('현재 제공된 안내가 없어요.'),
    ).toHaveLength(6)
    expect(screen.getAllByText('현재 제공된 안내가 없어요.')).toHaveLength(8)
    expect(screen.queryByText(/주의사항 없음|상호작용 없음|위험 없음/)).toBeNull()
  })

  it('향후 명시 라벨은 해당 섹션에만 매핑하고 누락 섹션은 추측하지 않는다', async () => {
    const content = [
      '복약 가이드',
      [
        '[1] 합성 처방약',
        '용량: 1 정',
        '복용 횟수: 하루 2회',
        '복용 시점: 아침 저녁 식후',
        '복용 기간: 7일',
        '주의해야 할 음식·음료: 자몽 관련 안내는 의료진 또는 약사에게 확인하세요.',
        '나타날 수 있는 불편감: 어지러움이 지속되면 의료진과 상담하세요.',
        '임신·수유 중 안내: 복용 전에 의료진과 상담하세요.',
      ].join('\n'),
      '공통 안내: 공통으로 확인할 내용입니다.\n안전 안내: 임의로 복용을 변경하지 마세요.',
    ].join('\n\n')
    vi.mocked(getGuide).mockResolvedValue(completedGuideResponse('guide-1', content))

    renderPage()

    const toggle = await screen.findByRole('button', { name: /합성 처방약/ })
    fireEvent.click(toggle)
    const medicationCard = toggle.closest('article')
    expect(medicationCard).not.toBeNull()
    expect(
      within(medicationCard!).getByText(
        '자몽 관련 안내는 의료진 또는 약사에게 확인하세요.',
      ),
    ).toBeTruthy()
    expect(
      within(medicationCard!).getByText(
        '어지러움이 지속되면 의료진과 상담하세요.',
      ),
    ).toBeTruthy()
    expect(
      within(medicationCard!).getByText('복용 전에 의료진과 상담하세요.'),
    ).toBeTruthy()
    expect(
      within(medicationCard!).getAllByText('현재 제공된 안내가 없어요.'),
    ).toHaveLength(3)
  })

  it('알려지지 않은 의료 라벨은 섹션 shell을 유지하고 추가 원문으로만 보존한다', async () => {
    const content = [
      '복약 가이드',
      '[1] 합성 처방약\n용량: 1 정\n새 의료 판단: 임의 분류하지 않을 내용',
      '공통 안내: 공통 안내\n안전 안내: 안전 안내',
    ].join('\n\n')
    vi.mocked(getGuide).mockResolvedValue(completedGuideResponse('guide-1', content))

    renderPage()

    const toggle = await screen.findByRole('button', { name: /합성 처방약/ })
    fireEvent.click(toggle)
    const medicationCard = toggle.closest('article')
    expect(medicationCard).not.toBeNull()
    expect(
      within(medicationCard!.querySelector('.guide-page__medical-sections')!)
        .getAllByRole('heading', { level: 4 }),
    ).toHaveLength(6)
    expect(
      within(medicationCard!.querySelector('.guide-page__medical-sections')!)
        .getAllByText('현재 제공된 안내가 없어요.'),
    ).toHaveLength(6)
    fireEvent.click(within(medicationCard!).getByText('추가 안내 원문'))
    expect(within(medicationCard!).getByText('새 의료 판단')).toBeTruthy()
    expect(within(medicationCard!).getByText('임의 분류하지 않을 내용')).toBeTruthy()
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
    expect(document.querySelector('.guide-page__guide-text')?.textContent).toContain(
      '안전 안내: 임의로 복용을 변경하지 마세요.',
    )
    expect(screen.queryByText(/확인된 약 목록/)).toBeNull()
    expect(document.querySelectorAll('.guide-page__medication-card')).toHaveLength(0)
  })

  it('실제 Guide 조회 응답의 평문 content를 표시한다', async () => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        ...legacyGuideReleaseFields(),
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

  it('미동의·철회 공통 CONSENT_REQUIRED 계약을 안내하고 별도 상태 추론 없이 동의 설정으로 이동한다', async () => {
    vi.mocked(getGuide).mockRejectedValue(
      new ApiError(403, '노출하면 안 되는 Backend 메시지', 'CONSENT_REQUIRED'),
    )
    renderPage()

    expect(
      await screen.findByRole('heading', {
        name: '이 기능을 이용하려면 동의가 필요해요.',
      }),
    ).toBeTruthy()
    expect(
      screen.getByText('동의 설정을 확인한 뒤 다시 이용해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByText('노출하면 안 되는 Backend 메시지')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '동의 설정 확인하기' }))

    expect(await screen.findByText('동의 설정 화면')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/profile')
    expect(getGuide).toHaveBeenCalledTimes(1)
  })

  it('PRESCRIPTION_VERSION_STALE은 동의로 보내지 않고 현재 Guide를 다시 조회한다', async () => {
    vi.mocked(getGuide)
      .mockRejectedValueOnce(
        new ApiError(409, '구버전 처방', 'PRESCRIPTION_VERSION_STALE'),
      )
      .mockResolvedValueOnce(
        completedGuideResponse('guide-1', structuredGuideContent()),
      )
    renderPage()

    expect(
      await screen.findByRole('heading', { name: '처방 정보가 변경되었어요.' }),
    ).toBeTruthy()
    expect(
      screen.getByText('최신 처방 정보를 다시 불러온 뒤 이용해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: '동의 설정 확인하기' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))

    expect(
      await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' }),
    ).toBeTruthy()
    expect(getGuide).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-1')
  })

  it('CONSENT_POLICY_UNAVAILABLE은 현재 Guide 요청만 사용자가 다시 시도한다', async () => {
    vi.mocked(getGuide)
      .mockRejectedValueOnce(
        new ApiError(503, '정책 원문', 'CONSENT_POLICY_UNAVAILABLE'),
      )
      .mockResolvedValueOnce(
        completedGuideResponse('guide-1', structuredGuideContent()),
      )
    renderPage()

    expect(
      await screen.findByRole('heading', {
        name: '동의 안내를 준비하고 있어요. 잠시 후 다시 시도해 주세요.',
      }),
    ).toBeTruthy()
    expect(getGuide).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: '동의 설정 확인하기' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))

    expect(
      await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' }),
    ).toBeTruthy()
    expect(getGuide).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-1')
  })

  it.each([null, '', '   \n'])('완료된 content가 %p이면 안전한 빈 상태를 표시한다', async (content) => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        ...legacyGuideReleaseFields(),
        generation_status: 'COMPLETED',
        content,
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
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    await waitFor(() => expect(getGuide).toHaveBeenCalledTimes(2))
    expect(createGuide).not.toHaveBeenCalled()
  })

  it('latest 처방이 없으면 GUIDE-01 empty state와 업로드 CTA를 표시한다', async () => {
    renderPage('/guides')

    expect(await screen.findByText('아직 만들어진 가이드가 없어요')).toBeTruthy()
    expect(getLatestPrescription).toHaveBeenCalledTimes(1)
    expect(getGuideForPrescription).not.toHaveBeenCalled()
    expect(getGuide).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '처방전 등록하기' }))
    expect(screen.getByText('처방전 업로드 화면')).toBeTruthy()
  })

  it('client ID가 없는 재로그인 상태에서 최신 처방과 가이드를 순서대로 복원한다', async () => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', 'relogin-access-token')
    vi.mocked(getLatestPrescription).mockResolvedValue(
      prescriptionResponse('prescription-restored'),
    )
    const restoredGuide = {
      ...completedGuideResponse('guide-restored', structuredGuideContent()),
      data: {
        ...completedGuideResponse('guide-restored', structuredGuideContent()).data,
        prescription_id: 'prescription-restored',
      },
    }
    vi.mocked(getGuideForPrescription).mockResolvedValue(restoredGuide)
    vi.mocked(getGuide).mockResolvedValue(restoredGuide)

    renderPage('/guides')

    expect(
      await screen.findByRole('heading', { name: '확인된 약 목록 · 1개' }),
    ).toBeTruthy()
    expect(getLatestPrescription).toHaveBeenCalledTimes(1)
    expect(getGuideForPrescription).toHaveBeenCalledWith('prescription-restored')
    expect(getGuide).toHaveBeenCalledWith('guide-restored')
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-restored')
    expect(localStorage.getItem('access_token')).toBe('relogin-access-token')
    expect(localStorage.length).toBe(1)
    expect(sessionStorage.length).toBe(0)
  })

  it('처방은 있지만 Guide가 없으면 기존 Guide empty state를 유지한다', async () => {
    vi.mocked(getLatestPrescription).mockResolvedValue(
      prescriptionResponse('prescription-without-guide'),
    )
    vi.mocked(getGuideForPrescription).mockRejectedValue(
      new ApiError(404, '가이드를 찾을 수 없습니다.', 'GUIDE_NOT_FOUND'),
    )

    renderPage('/guides')

    expect(await screen.findByText('아직 만들어진 가이드가 없어요')).toBeTruthy()
    expect(screen.getByRole('button', { name: '처방전 등록하기' })).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/guides')
  })

  it('직접 조회한 Guide 404도 GUIDE-01 empty copy와 CTA로 표시한다', async () => {
    vi.mocked(getGuide).mockRejectedValue(
      new ApiError(404, '가이드를 찾을 수 없습니다.', 'GUIDE_NOT_FOUND'),
    )

    renderPage('/guides/guide-missing')

    expect(await screen.findByText('아직 만들어진 가이드가 없어요')).toBeTruthy()
    expect(screen.getByRole('button', { name: '처방전 등록하기' })).toBeTruthy()
    expect(screen.queryByText('요청한 복약 가이드를 찾을 수 없어요.')).toBeNull()
  })

  it.each([
    [
      new ApiError(503, 'internal provider detail', 'PROVIDER_DOWN'),
      '서버 응답이 원활하지 않아요. 잠시 후 다시 시도해 주세요.',
    ],
    [
      new TypeError('Failed to fetch'),
      '네트워크 연결을 확인한 뒤 다시 시도해 주세요.',
    ],
  ])('rediscovery 오류를 empty state로 오인하지 않는다', async (error, expectedMessage) => {
    vi.mocked(getLatestPrescription).mockRejectedValue(error)

    renderPage('/guides')

    expect(await screen.findByText(expectedMessage)).toBeTruthy()
    expect(screen.queryByText('아직 만들어진 가이드가 없어요')).toBeNull()
    expect(screen.queryByRole('button', { name: '처방전 등록하기' })).toBeNull()
  })

  it.each([
    ['latest 처방', false],
    ['처방의 Guide', true],
  ])('%s rediscovery의 401은 기존 Auth 계약대로 세션을 정리하고 로그인으로 이동한다', async (_stage, failGuide) => {
    localStorage.setItem('access_token', 'expired-access-token')
    sessionStorage.setItem('dosey_ocr_job_recovery:v1', '{"job":"active"}')
    const authError = new ApiError(401, '만료된 토큰입니다.', 'EXPIRED_TOKEN')

    if (failGuide) {
      vi.mocked(getLatestPrescription).mockResolvedValue(
        prescriptionResponse('prescription-auth-error'),
      )
      vi.mocked(getGuideForPrescription).mockRejectedValue(authError)
    } else {
      vi.mocked(getLatestPrescription).mockRejectedValue(authError)
    }

    renderPage('/guides')

    expect(await screen.findByText('로그인 화면')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/login')
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(sessionStorage.getItem('dosey_ocr_job_recovery:v1')).toBeNull()
    expect(screen.queryByText('아직 만들어진 가이드가 없어요')).toBeNull()
  })

  it('latest 처방 성공 뒤 Guide 5xx를 empty state로 오인하지 않는다', async () => {
    vi.mocked(getLatestPrescription).mockResolvedValue(
      prescriptionResponse('prescription-guide-error'),
    )
    vi.mocked(getGuideForPrescription).mockRejectedValue(
      new ApiError(503, 'internal provider detail', 'PROVIDER_DOWN'),
    )

    renderPage('/guides')

    expect(
      await screen.findByText('서버 응답이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'),
    ).toBeTruthy()
    expect(screen.queryByText('아직 만들어진 가이드가 없어요')).toBeNull()
  })

  it('GENERATING 응답을 최신 생성 중 상태로 표시하고 실제 조회만 다시 시도한다', async () => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        ...legacyGuideReleaseFields(),
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
    expect(createGuide).not.toHaveBeenCalled()
  })

  it('FAILED 응답을 최신 실패 상태로 표시하고 raw 오류 상태를 만들지 않는다', async () => {
    vi.mocked(getGuide).mockResolvedValue({
      data: {
        guide_id: 'guide-1',
        prescription_id: 'prescription-1',
        ...legacyGuideReleaseFields(),
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

  it('공통 Navigation의 Guide active, 일정 활성화, 기존 route 이동을 유지한다', async () => {
    const firstRender = renderPage('/guides')

    await screen.findByText('아직 만들어진 가이드가 없어요')
    expect(screen.getByRole('button', { name: '가이드' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('button', { name: '일정' })).toHaveProperty('disabled', false)
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
    expect(getLatestPrescription).not.toHaveBeenCalled()
    expect(getGuideForPrescription).not.toHaveBeenCalled()
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


describe('실패한 가이드 생성 재시도', () => {
  beforeEach(() => {
    vi.mocked(getGuide).mockImplementation(async (id) => id === 'guide-1'
      ? { data: { ...completedGuideResponse(id, null).data, generation_status: 'FAILED' } }
      : completedGuideResponse(id, '새 가이드 내용'))
    vi.mocked(createGuide).mockReset()
  })

  it('해당 처방으로 한 번 생성하고 새 가이드 상세로 이동한다', async () => {
    const pending = deferred<GuideResponse>()
    vi.mocked(createGuide).mockReturnValue(pending.promise)
    renderPage()
    const button = await screen.findByRole('button', { name: '다시 시도하기' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(createGuide).toHaveBeenCalledExactlyOnceWith('prescription-guide-1')
    expect(screen.getByRole('button', { name: '가이드를 생성하고 있어요...' })).toHaveProperty('disabled', true)
    expect(getGuide).toHaveBeenCalledTimes(1)
    await act(async () => pending.resolve({ data: {
      ...completedGuideResponse('guide-new', '새 가이드 내용').data,
      prescription_id: 'prescription-guide-1',
    } }))
    expect(await screen.findByText('새 가이드 내용')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-new')
  })

  it('생성 실패 안내와 기존 처방을 보존하고 명시적으로 다시 시도할 수 있다', async () => {
    vi.mocked(createGuide).mockRejectedValue(new ApiError(503, '잠시 후 다시 시도해 주세요.', 'SERVICE_UNAVAILABLE'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '다시 시도하기' }))
    expect(await screen.findByText('잠시 후 다시 시도해 주세요.')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-1')
    fireEvent.click(screen.getByRole('button', { name: '다시 시도하기' }))
    await waitFor(() => expect(createGuide).toHaveBeenCalledTimes(2))
    expect(getGuide).toHaveBeenCalledTimes(1)
  })

  it('다른 처방의 생성 응답으로 이동하지 않는다', async () => {
    vi.mocked(createGuide).mockResolvedValue(completedGuideResponse('wrong-guide', '다른 처방 내용'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '다시 시도하기' }))
    expect(await screen.findByText('확인한 처방과 다른 가이드 응답을 받았어요. 다시 확인해 주세요.')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-1')
    expect(screen.queryByText('다른 처방 내용')).toBeNull()
  })

  it.each(['resolve', 'reject'] as const)('화면 전환 후 늦은 %s 응답을 무시한다', async (outcome) => {
    const pending = deferred<GuideResponse>()
    vi.mocked(createGuide).mockReturnValue(pending.promise)
    renderPage('/guides/guide-1', true)
    fireEvent.click(await screen.findByRole('button', { name: '다시 시도하기' }))
    fireEvent.click(screen.getByRole('button', { name: 'Guide B로 이동' }))
    await screen.findByText('새 가이드 내용')
    await act(async () => {
      if (outcome === 'resolve') pending.resolve({ data: {
        ...completedGuideResponse('guide-new', '오래된 생성 결과').data,
        prescription_id: 'prescription-guide-1',
      } })
      else pending.reject(new ApiError(401, '만료', 'TOKEN_EXPIRED'))
    })
    expect(screen.getByTestId('location').textContent).toBe('/guides/guide-b')
    expect(screen.getByText('새 가이드 내용')).toBeTruthy()
  })

  it('언마운트 후 완료되어도 다른 화면에서 이동하지 않는다', async () => {
    const pending = deferred<GuideResponse>()
    vi.mocked(createGuide).mockReturnValue(pending.promise)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '다시 시도하기' }))
    fireEvent.click(screen.getByRole('button', { name: '메뉴' }))
    await act(async () => pending.resolve({ data: {
      ...completedGuideResponse('guide-new', '늦은 결과').data,
      prescription_id: 'prescription-guide-1',
    } }))
    expect(screen.getByTestId('location').textContent).toBe('/menu')
  })

  it('현재 재시도 요청의 인증 만료는 로그인으로 이동한다', async () => {
    vi.mocked(createGuide).mockRejectedValue(new ApiError(401, '만료', 'TOKEN_EXPIRED'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '다시 시도하기' }))
    expect(await screen.findByText('로그인 화면')).toBeTruthy()
  })
})
