import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
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

function renderPage(entry = '/guides/guide-1', withRouteControls = false) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      {withRouteControls && <GuideRouteControls />}
      <Routes>
        <Route path="/guides" element={<GuidePage />} />
        <Route path="/guides/:guideId" element={<GuidePage />} />
        <Route path="/prescriptions/upload" element={<div>처방전 업로드 화면</div>} />
        <Route path="/" element={<div>홈 화면</div>} />
        <Route path="/profile" element={<div>내 정보 화면</div>} />
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

    expect(await screen.findByText('확인된 복용 조건')).toBeTruthy()
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
    expect(screen.getByText('내 정보 화면')).toBeTruthy()

    firstRender.unmount()
    renderPage('/guides')
    await screen.findByText('아직 만들어진 가이드가 없어요')
    fireEvent.click(screen.getByRole('button', { name: '홈' }))
    expect(screen.getByText('홈 화면')).toBeTruthy()
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
})
