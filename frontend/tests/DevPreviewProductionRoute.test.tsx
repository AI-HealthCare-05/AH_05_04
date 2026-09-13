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
})
