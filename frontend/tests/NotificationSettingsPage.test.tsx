import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { getWebPushLaunchContext, getWebPushState } from '../src/features/push/webPush'
import NotificationSettingsPage from '../src/pages/NotificationSettingsPage'

vi.mock('../src/features/push/webPush', () => ({
  getWebPushLaunchContext: vi.fn(() => 'browser'),
  getWebPushState: vi.fn(),
  enableWebPush: vi.fn(),
  disableWebPush: vi.fn(),
  hasStoredWebPushBinding: vi.fn(() => false),
}))

beforeEach(() => {
  vi.mocked(getWebPushLaunchContext).mockReturnValue('browser')
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('NotificationSettingsPage', () => {
  it('iPhone Safari 일반 탭은 홈 화면 설치 안내만 표시하고 Push CTA를 노출하지 않는다', async () => {
    vi.mocked(getWebPushLaunchContext).mockReturnValue('ios-browser')
    render(
      <MemoryRouter>
        <NotificationSettingsPage />
      </MemoryRouter>,
    )

    expect(screen.getByText('홈 화면에 추가해 주세요')).toBeTruthy()
    expect(screen.getByText(/Safari의 공유 버튼/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '알림 켜기' })).toBeNull()
    expect(screen.getByRole('button', { name: '앱 안의 알림 목록 보기' })).toBeTruthy()
    expect(getWebPushState).not.toHaveBeenCalled()
  })

  it('iPhone 홈 화면 설치 웹앱은 사용자 클릭용 알림 켜기 CTA를 표시한다', async () => {
    vi.mocked(getWebPushLaunchContext).mockReturnValue('standalone')
    vi.mocked(getWebPushState).mockResolvedValue('unrequested')
    render(
      <MemoryRouter>
        <NotificationSettingsPage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Push 알림을 사용하지 않고 있어요')).toBeTruthy()
    expect(screen.getByRole('button', { name: '알림 켜기' })).toBeTruthy()
  })

  it('Android 설정에서 권한을 바꾸고 돌아오면 foreground에서 상태를 다시 확인한다', async () => {
    vi.mocked(getWebPushLaunchContext).mockReturnValue('browser')
    vi.mocked(getWebPushState)
      .mockResolvedValueOnce('denied')
      .mockResolvedValueOnce('unrequested')
    render(
      <MemoryRouter>
        <NotificationSettingsPage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('브라우저에서 알림이 차단되어 있어요')).toBeTruthy()
    fireEvent.focus(window)
    expect(await screen.findByText('Push 알림을 사용하지 않고 있어요')).toBeTruthy()
    expect(getWebPushState).toHaveBeenCalledTimes(2)
  })
})
