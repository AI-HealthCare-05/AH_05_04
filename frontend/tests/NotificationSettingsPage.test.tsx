import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { getWebPushState } from '../src/features/push/webPush'
import NotificationSettingsPage from '../src/pages/NotificationSettingsPage'

vi.mock('../src/features/push/webPush', () => ({
  getWebPushState: vi.fn(),
  enableWebPush: vi.fn(),
  disableWebPush: vi.fn(),
  hasStoredWebPushBinding: vi.fn(() => false),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('NotificationSettingsPage', () => {
  it('Android 설정에서 권한을 바꾸고 돌아오면 foreground에서 상태를 다시 확인한다', async () => {
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
