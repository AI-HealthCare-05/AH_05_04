import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import StatusPanel, {
  type StatusPanelVariant,
} from '../src/components/StatusPanel'
import LoadingState from '../src/components/LoadingState'
import ErrorState from '../src/components/ErrorState'

afterEach(() => {
  cleanup()
})

const ERROR_VARIANTS: StatusPanelVariant[] = [
  'error',
  'error-retryable',
  'error-final',
]
const STATUS_VARIANTS: StatusPanelVariant[] = [
  'loading',
  'empty',
  'unavailable',
]

describe('StatusPanel', () => {
  it.each([...ERROR_VARIANTS, ...STATUS_VARIANTS])(
    '%s variant 에서 제목을 표시한다',
    (variant) => {
      render(<StatusPanel variant={variant} title="표시할 제목" />)
      expect(screen.getByText('표시할 제목')).toBeTruthy()
    },
  )

  it.each(ERROR_VARIANTS)('%s 는 alert 로 알린다', (variant) => {
    render(<StatusPanel variant={variant} title="오류가 발생했어요" />)
    expect(screen.getByRole('alert')).toBeTruthy()
  })

  it.each(STATUS_VARIANTS)('%s 는 status 로 알린다', (variant) => {
    render(<StatusPanel variant={variant} title="안내" />)
    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('상태를 색 외의 텍스트 라벨로도 구분한다', () => {
    render(<StatusPanel variant="unavailable" title="잠시 후 다시 시도해 주세요" />)
    expect(screen.getByText('일시적인 문제')).toBeTruthy()
  })

  it('error-retryable 에서만 재시도 버튼을 노출한다', () => {
    const onRetry = vi.fn()
    const { rerender } = render(
      <StatusPanel
        variant="error-retryable"
        title="불러오지 못했어요"
        onRetry={onRetry}
      />,
    )
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy()

    rerender(
      <StatusPanel
        variant="error-final"
        title="불러오지 못했어요"
        onRetry={onRetry}
      />,
    )
    expect(screen.queryByRole('button', { name: '다시 시도' })).toBeNull()
  })

  it('재시도 버튼은 포커스 가능한 button 이라 키보드로 실행된다', () => {
    const onRetry = vi.fn()
    render(
      <StatusPanel
        variant="error-retryable"
        title="불러오지 못했어요"
        onRetry={onRetry}
      />,
    )

    const retry = screen.getByRole('button', { name: '다시 시도' })
    // 네이티브 button 이므로 tabIndex 로 포커스 순서에서 빠지지 않아야 한다.
    expect(retry.tagName).toBe('BUTTON')
    expect(retry.getAttribute('type')).toBe('button')
    expect(retry.hasAttribute('disabled')).toBe(false)

    retry.focus()
    expect(document.activeElement).toBe(retry)

    // 브라우저는 포커스된 button 의 Enter/Space 를 click 으로 변환한다.
    fireEvent.click(retry)
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('onRetry 가 없으면 error-retryable 이어도 버튼을 만들지 않는다', () => {
    render(<StatusPanel variant="error-retryable" title="오류" />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('기존 래퍼 호환', () => {
  it('LoadingState 는 기본 문구를 status 로 표시한다', () => {
    render(<LoadingState />)
    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.getByText('불러오는 중입니다.')).toBeTruthy()
  })

  it('ErrorState 는 onRetry 유무로 재시도 버튼을 전환한다', () => {
    const { rerender } = render(<ErrorState />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()

    rerender(<ErrorState onRetry={() => {}} />)
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy()
  })
})
