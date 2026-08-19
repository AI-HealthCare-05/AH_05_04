export type ApiErrorDetail = {
  field?: string | null
  reason?: string
  message?: string
}

export type ApiErrorResponse = {
  code: string
  message: string
  details: ApiErrorDetail[]
  trace_id: string
}
