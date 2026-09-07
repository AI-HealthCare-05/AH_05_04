import { useEffect, useState } from 'react'
import { isTerminalJobStatus, type AiJobViewStatus } from './jobState'

export type JobPollingPhase =
  | 'IDLE'
  | 'POLLING'
  | 'TERMINAL'
  | 'TIMED_OUT'
  | 'ERROR'

export type JobPollingState<T> = {
  jobKey: string | null
  data: T | null
  status: AiJobViewStatus | null
  phase: JobPollingPhase
  error: unknown
  attemptCount: number
}

type UseJobPollingOptions<T> = {
  jobKey: string | null
  fetcher: (jobKey: string, signal: AbortSignal) => Promise<T>
  getStatus: (data: T) => AiJobViewStatus
  getDelayMs?: (data: T, status: AiJobViewStatus) => number | null
  intervalMs?: number
  maxAttempts?: number
  restartKey?: number
}

const idleState: JobPollingState<never> = {
  jobKey: null,
  data: null,
  status: null,
  phase: 'IDLE',
  error: null,
  attemptCount: 0,
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function useJobPolling<T>({
  jobKey,
  fetcher,
  getStatus,
  getDelayMs,
  intervalMs = 1000,
  maxAttempts = 80,
  restartKey = 0,
}: UseJobPollingOptions<T>): JobPollingState<T> {
  const [state, setState] = useState<JobPollingState<T>>(idleState)

  useEffect(() => {
    if (!jobKey) {
      setState(idleState)
      return
    }

    let isActive = true
    let attemptCount = 0
    let timerId: number | undefined
    let controller: AbortController | undefined

    setState({
      jobKey,
      data: null,
      status: null,
      phase: 'POLLING',
      error: null,
      attemptCount: 0,
    })

    const poll = async () => {
      controller = new AbortController()

      try {
        const data = await fetcher(jobKey, controller.signal)
        if (!isActive) return

        attemptCount += 1
        const status = getStatus(data)

        if (isTerminalJobStatus(status)) {
          setState({
            jobKey,
            data,
            status,
            phase: 'TERMINAL',
            error: null,
            attemptCount,
          })
          return
        }

        if (attemptCount >= maxAttempts) {
          setState({
            jobKey,
            data,
            status,
            phase: 'TIMED_OUT',
            error: null,
            attemptCount,
          })
          return
        }

        setState({
          jobKey,
          data,
          status,
          phase: 'POLLING',
          error: null,
          attemptCount,
        })

        const responseDelayMs = getDelayMs?.(data, status)
        const nextDelayMs =
          responseDelayMs !== null && responseDelayMs !== undefined
            ? Math.max(responseDelayMs, intervalMs)
            : intervalMs

        timerId = window.setTimeout(() => {
          timerId = undefined
          void poll()
        }, nextDelayMs)
      } catch (error) {
        if (!isActive || isAbortError(error)) return

        setState({
          jobKey,
          data: null,
          status: null,
          phase: 'ERROR',
          error,
          attemptCount,
        })
      }
    }

    void poll()

    return () => {
      isActive = false
      if (timerId !== undefined) {
        window.clearTimeout(timerId)
      }
      controller?.abort()
    }
  }, [fetcher, getDelayMs, getStatus, intervalMs, jobKey, maxAttempts, restartKey])

  return state
}
