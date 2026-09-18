import { cleanup, render, screen } from '@testing-library/react'
import React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '../src/routes/AppRouter'

afterEach(() => {
  cleanup()
  window.history.pushState({}, '', '/')
})

describe('production dev Preview route gate', () => {
  it('production 조건에서는 /dev/preview route를 등록하지 않는다', () => {
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    render(
      <MemoryRouter initialEntries={['/dev/preview']}>
        <AppRoutes enableDevPreview={false} />
      </MemoryRouter>,
    )

    expect(screen.queryByText('DEV PREVIEW')).toBeNull()
    expect(document.body.textContent).not.toContain('Mock data only')
    consoleWarn.mockRestore()
  })

  it('production 조건에서는 /design-prototype route를 등록하지 않는다', () => {
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    render(
      <MemoryRouter initialEntries={['/design-prototype']}>
        <AppRoutes enableDesignPrototype={false} />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('heading', { name: '다섯알 전체 여정 + UX 상태' })).toBeNull()
    expect(document.querySelector('.prototype-workbench')).toBeNull()
    consoleWarn.mockRestore()
  })

  it.each([
    ['/track-c/occurrences/11111111-1111-4111-8111-111111111111'],
    ['/track-c/plans/22222222-2222-4222-8222-222222222222'],
  ])('VITE_PUBLIC_TRACK_C가 false면 %s route를 등록하지 않는다', (entry) => {
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    render(
      <MemoryRouter initialEntries={[entry]}>
        <AppRoutes enableTrackC={false} />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('heading', { name: '현재 불편한 증상이 있나요?' })).toBeNull()
    expect(screen.queryByRole('heading', { name: '복약 도움 확인' })).toBeNull()
    expect(document.querySelector('.track-c-page')).toBeNull()
    consoleWarn.mockRestore()
  })
})
