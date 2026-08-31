import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AiJobViewStatus } from '../src/features/ai-jobs/jobState'
import { useJobPolling } from '../src/features/ai-jobs/useJobPolling'

type TestJob = {
  status: AiJobViewStatus
}

const getStatus = (job: TestJob) => job.status

async function flushPromises() {
  await act(async () => {
    await Promise.resolve()
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useJobPolling', () => {
  it('PENDING → PROCESSING → COMPLETED를 조회하고 terminal에서 즉시 멈춘다', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ status: 'PENDING' })
      .mockResolvedValueOnce({ status: 'PROCESSING' })
      .mockResolvedValueOnce({ status: 'COMPLETED' })

    const { result } = renderHook(() => useJobPolling<TestJob>({
      jobKey: 'existing-job',
      fetcher,
      getStatus,
      intervalMs: 1000,
      maxAttempts: 80,
    }))

    await flushPromises()
    expect(result.current.status).toBe('PENDING')

    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(result.current.status).toBe('PROCESSING')

    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(result.current.phase).toBe('TERMINAL')
    expect(result.current.status).toBe('COMPLETED')
    expect(fetcher).toHaveBeenCalledTimes(3)
    expect(vi.getTimerCount()).toBe(0)

    await act(async () => vi.advanceTimersByTimeAsync(5000))
    expect(fetcher).toHaveBeenCalledTimes(3)
  })

  it('PROCESSING → FAILED에서 polling을 종료한다', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ status: 'PROCESSING' })
      .mockResolvedValueOnce({ status: 'FAILED' })

    const { result } = renderHook(() => useJobPolling<TestJob>({
      jobKey: 'existing-job',
      fetcher,
      getStatus,
      intervalMs: 1000,
    }))

    await flushPromises()
    await act(async () => vi.advanceTimersByTimeAsync(1000))

    expect(result.current.status).toBe('FAILED')
    expect(result.current.phase).toBe('TERMINAL')
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('unmount에서 예약 timer를 정리한다', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockResolvedValue({ status: 'PROCESSING' })
    const { unmount } = renderHook(() => useJobPolling<TestJob>({
      jobKey: 'existing-job',
      fetcher,
      getStatus,
      intervalMs: 1000,
    }))

    await flushPromises()
    expect(vi.getTimerCount()).toBe(1)

    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('unmount에서 in-flight fetch를 abort하고 stale update를 막는다', async () => {
    let requestSignal: AbortSignal | undefined
    const fetcher = vi.fn((_jobKey: string, signal: AbortSignal) => {
      requestSignal = signal
      return new Promise<TestJob>(() => undefined)
    })
    const { unmount } = renderHook(() => useJobPolling<TestJob>({
      jobKey: 'existing-job',
      fetcher,
      getStatus,
    }))

    await flushPromises()
    expect(requestSignal?.aborted).toBe(false)

    unmount()
    expect(requestSignal?.aborted).toBe(true)
  })

  it('job key가 바뀌면 이전 in-flight 요청을 abort하고 새 key만 추적한다', async () => {
    const requestSignals = new Map<string, AbortSignal>()
    const fetcher = vi.fn((jobKey: string, signal: AbortSignal) => {
      requestSignals.set(jobKey, signal)
      if (jobKey === 'next-job') {
        return Promise.resolve<TestJob>({ status: 'COMPLETED' })
      }
      return new Promise<TestJob>(() => undefined)
    })
    const { rerender, result } = renderHook(
      ({ jobKey }: { jobKey: string }) => useJobPolling<TestJob>({
        jobKey,
        fetcher,
        getStatus,
      }),
      { initialProps: { jobKey: 'previous-job' } },
    )

    await flushPromises()
    rerender({ jobKey: 'next-job' })
    await flushPromises()

    expect(requestSignals.get('previous-job')?.aborted).toBe(true)
    expect(result.current.jobKey).toBe('next-job')
    expect(result.current.status).toBe('COMPLETED')
  })

  it('network failure를 ERROR로 보존하고 자동으로 새 Job을 만들지 않는다', async () => {
    const networkError = new TypeError('Failed to fetch')
    const fetcher = vi.fn().mockRejectedValue(networkError)
    const { result } = renderHook(() => useJobPolling<TestJob>({
      jobKey: 'existing-job',
      fetcher,
      getStatus,
    }))

    await flushPromises()

    expect(result.current.phase).toBe('ERROR')
    expect(result.current.error).toBe(networkError)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('최대 조회 횟수 도달을 Backend FAILED와 다른 TIMED_OUT으로 유지한다', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockResolvedValue({ status: 'PROCESSING' })
    const { result } = renderHook(() => useJobPolling<TestJob>({
      jobKey: 'existing-job',
      fetcher,
      getStatus,
      intervalMs: 1000,
      maxAttempts: 2,
    }))

    await flushPromises()
    await act(async () => vi.advanceTimersByTimeAsync(1000))

    expect(result.current.phase).toBe('TIMED_OUT')
    expect(result.current.status).toBe('PROCESSING')
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('재진입 seam은 기존 job key를 fetcher에 전달할 뿐 새 Job 생성 동작을 갖지 않는다', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 'COMPLETED' })
    const { rerender } = renderHook(
      ({ jobKey }: { jobKey: string | null }) => useJobPolling<TestJob>({
        jobKey,
        fetcher,
        getStatus,
      }),
      { initialProps: { jobKey: null } },
    )

    expect(fetcher).not.toHaveBeenCalled()
    rerender({ jobKey: 'rediscovered-outside-this-hook' })
    await flushPromises()

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher).toHaveBeenCalledWith(
      'rediscovered-outside-this-hook',
      expect.any(AbortSignal),
    )
  })
})
