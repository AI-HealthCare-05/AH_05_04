import { cleanup, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { ApiError } from '../src/api/client'
import AiJobStatusState from '../src/components/AiJobStatusState'
import { adaptOcrJobStatus } from '../src/features/ai-jobs/ocrJobAdapter'
import {
  getJobFailurePresentation,
  getJobRequestErrorPresentation,
  getJobStatusPresentation,
  isTerminalJobStatus,
} from '../src/features/ai-jobs/jobState'
import {
  syntheticAiJobFixtures,
  syntheticFailureCodes,
} from './fixtures/aiJobFixtures'

afterEach(cleanup)

describe('AI Job 상태 view-model', () => {
  it.each([
    'PENDING',
    'PROCESSING',
    'COMPLETED',
    'FAILED',
  ] as const)('현재 OCR %s 상태만 공통 view-model에 연결한다', (status) => {
    expect(adaptOcrJobStatus(status)).toBe(status)
  })

  it.each([
    ['PENDING', false],
    ['PROCESSING', false],
    ['RETRY_WAIT', false],
    ['COMPLETED', true],
    ['FAILED', true],
    ['STALE', true],
  ] as const)('%s terminal 판정을 반환한다', (status, expected) => {
    expect(isTerminalJobStatus(status)).toBe(expected)
  })

  it.each(syntheticFailureCodes)('%s failure code를 안전한 사용자 문구로 매핑한다', (code) => {
    const presentation = getJobFailurePresentation(code)

    expect(presentation.title).not.toHaveLength(0)
    expect(presentation.description).not.toContain(code)
    expect(presentation.actionLabel).toBe('이전 화면으로 돌아가기')
  })

  it('알 수 없는 failure code는 내부 값을 노출하지 않는 fallback을 사용한다', () => {
    const presentation = getJobFailurePresentation('PROVIDER_SECRET_DETAIL')

    expect(presentation.title).toBe('작업을 완료하지 못했어요')
    expect(presentation.description).not.toContain('PROVIDER_SECRET_DETAIL')
  })

  it.each([
    [401, '로그인이 필요해요'],
    [403, '이 작업을 확인할 수 없어요'],
    [404, '작업을 찾을 수 없어요'],
    [409, '작업 상태가 변경되었어요'],
    [500, '서버 응답이 원활하지 않아요'],
  ] as const)('HTTP %s를 안전한 상태 UI로 매핑한다', (status, title) => {
    const presentation = getJobRequestErrorPresentation(
      new ApiError(status, 'backend detail', 'BACKEND_CODE'),
    )

    expect(presentation.title).toBe(title)
    expect(presentation.description).not.toContain('backend detail')
  })

  it('404는 타 사용자 소유 여부를 추정하거나 노출하지 않는다', () => {
    const presentation = getJobRequestErrorPresentation(
      new ApiError(404, 'not owned by requesting user', 'JOB_NOT_OWNED'),
    )
    const visibleCopy = `${presentation.title} ${presentation.description}`

    expect(visibleCopy).toBe('작업을 찾을 수 없어요 요청한 작업을 확인할 수 없습니다.')
    expect(visibleCopy).not.toMatch(/소유|다른 사용자|권한/)
  })

  it('network 오류를 Backend 실패와 구분해 표시한다', () => {
    expect(
      getJobRequestErrorPresentation(new TypeError('Failed to fetch')).title,
    ).toBe('네트워크 연결을 확인해 주세요')
  })
})

describe('AI Job 공통 상태 UI', () => {
  it.each([
    ['PENDING', '처리를 준비하고 있어요'],
    ['PROCESSING', '처방정보를 확인하고 있어요'],
  ] as const)('%s를 접근 가능한 진행 상태로 표시한다', (status, title) => {
    render(
      <AiJobStatusState
        status={status}
        presentation={getJobStatusPresentation(status)}
      />,
    )

    expect(screen.getByRole('status').getAttribute('aria-busy')).toBe('true')
    expect(screen.getByText(title)).toBeTruthy()
  })

  it('RETRY_WAIT fixture를 자동 재시도 대기 상태로 알린다', () => {
    const fixture = syntheticAiJobFixtures.RETRY_WAIT
    expect(fixture.status).toBe('RETRY_WAIT')
    render(
      <AiJobStatusState
        status="RETRY_WAIT"
        presentation={getJobStatusPresentation('RETRY_WAIT')}
      />,
    )

    const status = screen.getByRole('status')
    expect(status.getAttribute('data-job-status')).toBe('RETRY_WAIT')
    expect(status.getAttribute('aria-busy')).toBe('true')
    expect(screen.getByText('잠시 후 자동으로 다시 시도할게요')).toBeTruthy()
  })

  it('STALE fixture는 미확정 재생성 action을 disabled로 표시한다', () => {
    const fixture = syntheticAiJobFixtures.STALE
    expect(fixture.status).toBe('STALE')
    render(
      <AiJobStatusState
        status="STALE"
        presentation={getJobStatusPresentation('STALE')}
        actionDisabled
      />,
    )

    expect(
      (screen.getByRole('button', { name: '최신 정보 확인하기' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(screen.getByText('이 기능은 준비 중입니다.')).toBeTruthy()
  })

  it('FAILED는 alert로 즉시 알린다', () => {
    render(
      <AiJobStatusState
        status="FAILED"
        presentation={getJobFailurePresentation('INTERNAL_ERROR')}
      />,
    )

    expect(screen.getByRole('alert').getAttribute('aria-live')).toBe('assertive')
  })

  it('409 attention presentation도 request 오류이면 alert로 알린다', () => {
    render(
      <AiJobStatusState
        status="REQUEST_ERROR"
        presentation={getJobRequestErrorPresentation(
          new ApiError(409, 'conflict', 'CONFLICT'),
        )}
      />,
    )

    expect(screen.getByRole('alert')).toBeTruthy()
  })
})
