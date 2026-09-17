import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { createGuide } from '../src/api/guides'
import { ApiError } from '../src/api/client'
import { enableWebPush } from '../src/features/push/webPush'
import NotificationConsentPage from '../src/pages/NotificationConsentPage'

vi.mock('../src/api/guides', () => ({
  createGuide: vi.fn(),
}))

vi.mock('../src/features/push/webPush', () => ({
  enableWebPush: vi.fn(),
}))

function GuideRouteProbe() {
  const { guideId } = useParams()
  return <div>Guide route: {guideId}</div>
}

function renderPage(prescriptionId: unknown = 'prescription-1') {
  return render(
    <MemoryRouter
      initialEntries={[
        { pathname: '/notifications/consent', state: { prescriptionId } },
      ]}
    >
      <Routes>
        <Route path="/notifications/consent" element={<NotificationConsentPage />} />
        <Route path="/guides/:guideId" element={<GuideRouteProbe />} />
        <Route path="/prescriptions/review" element={<div>처방전 검토 화면</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function createDeferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(enableWebPush).mockResolvedValue('granted')
  vi.mocked(createGuide).mockResolvedValue({
    data: {
      guide_id: 'guide-created',
      prescription_id: 'prescription-1',
      generation_status: 'COMPLETED',
      content: '테스트 가이드',
      model_name: 'guide-model',
      prompt_version: 'guide-prompt-v1',
      requested_at: '2026-09-17T00:00:00Z',
      completed_at: '2026-09-17T00:00:01Z',
    },
  })
})

afterEach(() => {
  cleanup()
})

describe('NotificationConsentPage', () => {
  it('prescriptionId가 없으면 처방 검토 화면으로 돌려보낸다', () => {
    renderPage(null)

    expect(screen.getByText('처방전 검토 화면')).toBeTruthy()
    expect(createGuide).not.toHaveBeenCalled()
  })

  it('NOTIF-CONSENT-01 Figma 문구를 렌더링한다', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: '복약 알림을 받을까요?' })).toBeTruthy()
    expect(screen.getByText('아침·점심·저녁 등 설정한 시간에 알림을 보내요')).toBeTruthy()
    expect(screen.getByText('기기 알림 권한 요청이 한 번 표시돼요')).toBeTruthy()
  })

  it('"알림 받기"는 Push 권한 요청 후 실제 prescription_id로 Guide를 생성하고 이동한다', async () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '알림 받기' }))

    await waitFor(() => expect(enableWebPush).toHaveBeenCalledTimes(1))
    expect(createGuide).toHaveBeenCalledWith('prescription-1')
    expect(await screen.findByText('Guide route: guide-created')).toBeTruthy()
  })

  it('"나중에 하기"는 Push 권한을 요청하지 않고 바로 Guide를 생성한다', async () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '나중에 하기' }))

    expect(await screen.findByText('Guide route: guide-created')).toBeTruthy()
    expect(enableWebPush).not.toHaveBeenCalled()
    expect(createGuide).toHaveBeenCalledWith('prescription-1')
  })

  it('Push 권한 요청이 실패해도 Guide 생성은 계속 진행한다', async () => {
    vi.mocked(enableWebPush).mockRejectedValue(new Error('permission denied'))
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '알림 받기' }))

    expect(await screen.findByText('Guide route: guide-created')).toBeTruthy()
  })

  it('Guide 생성 중 중복 클릭을 막는다', async () => {
    const guideCreation = createDeferred<Awaited<ReturnType<typeof createGuide>>>()
    vi.mocked(createGuide).mockImplementation(() => guideCreation.promise)
    renderPage()

    const laterButton = screen.getByRole('button', { name: '나중에 하기' })
    fireEvent.click(laterButton)
    fireEvent.click(laterButton)

    await waitFor(() => expect(createGuide).toHaveBeenCalledTimes(1))
  })

  it('Guide 생성 실패 시 오류를 안내하고 다시 시도할 수 있다', async () => {
    vi.mocked(createGuide)
      .mockRejectedValueOnce(new ApiError(500, '복약 가이드를 만드는 중 오류가 발생했습니다.'))
      .mockResolvedValueOnce({
        data: {
          guide_id: 'guide-after-retry',
          prescription_id: 'prescription-1',
          generation_status: 'COMPLETED',
          content: '테스트 가이드',
          model_name: 'guide-model',
          prompt_version: 'guide-prompt-v1',
          requested_at: '2026-09-17T00:00:00Z',
          completed_at: '2026-09-17T00:00:01Z',
        },
      })
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '나중에 하기' }))

    expect(
      await screen.findByText('복약 가이드를 만드는 중 오류가 발생했습니다.'),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '나중에 하기' }))

    expect(await screen.findByText('Guide route: guide-after-retry')).toBeTruthy()
    expect(createGuide).toHaveBeenCalledTimes(2)
  })
})
