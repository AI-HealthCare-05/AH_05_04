import type {
  AiJobFailureCode,
  AiJobViewStatus,
} from '../../src/features/ai-jobs/jobState'

export type SyntheticAiJobFixture = {
  id: string
  status: AiJobViewStatus
  failureCode?: AiJobFailureCode
}

export const syntheticAiJobFixtures: Record<AiJobViewStatus, SyntheticAiJobFixture> = {
  PENDING: { id: 'synthetic-pending', status: 'PENDING' },
  PROCESSING: { id: 'synthetic-processing', status: 'PROCESSING' },
  RETRY_WAIT: { id: 'synthetic-retry-wait', status: 'RETRY_WAIT' },
  COMPLETED: { id: 'synthetic-completed', status: 'COMPLETED' },
  FAILED: {
    id: 'synthetic-failed',
    status: 'FAILED',
    failureCode: 'INTERNAL_ERROR',
  },
  STALE: { id: 'synthetic-stale', status: 'STALE' },
}

export const syntheticFailureCodes: AiJobFailureCode[] = [
  'TIMEOUT',
  'DEPENDENCY_UNAVAILABLE',
  'INVALID_INPUT',
  'UNSUPPORTED_SCHEMA',
  'SAFETY_VALIDATION_FAILED',
  'RETRY_EXHAUSTED',
  'INTERNAL_ERROR',
]
