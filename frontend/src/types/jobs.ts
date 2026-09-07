export type JobType = 'OCR' | 'GUIDE' | 'CHAT'

export type JobStatus =
  | 'PENDING'
  | 'PROCESSING'
  | 'RETRY_WAIT'
  | 'COMPLETED'
  | 'FAILED'
  | 'STALE'

export type JobDomainType = 'OCR_JOB' | 'GUIDE' | 'CHAT_MESSAGE'

export type JobFailureCode =
  | 'TIMEOUT'
  | 'DEPENDENCY_UNAVAILABLE'
  | 'INVALID_INPUT'
  | 'UNSUPPORTED_SCHEMA'
  | 'SAFETY_VALIDATION_FAILED'
  | 'RETRY_EXHAUSTED'
  | 'INTERNAL_ERROR'

export type JobError = {
  code: JobFailureCode
  message: string
}

export type JobStatusResponse = {
  data: {
    job_id: string
    job_type: JobType
    status: JobStatus
    domain_type: JobDomainType
    domain_id: string
    prescription_version_id: string | null
    status_url: string
    result_url: string | null
    retry_after_seconds: number | null
    error: JobError | null
    created_at: string
    updated_at: string
  }
}
