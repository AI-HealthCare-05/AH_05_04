import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { getGuide } from '../src/api/guides'
import GuidePage from '../src/pages/GuidePage'

vi.mock('../src/api/guides', () => ({
  getGuide: vi.fn(),
}))

function renderPage(entry = '/guides/guide-1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/guides" element={<GuidePage />} />
        <Route path="/guides/:guideId" element={<GuidePage />} />
        <Route path="/prescriptions/upload" element={<div>처방전 업로드 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
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

    expect(await screen.findByText('복약 가이드가 준비됐어요')).toBeTruthy()
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
})
