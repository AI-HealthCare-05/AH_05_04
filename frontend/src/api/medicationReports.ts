import { apiRequest } from './client'
import type { MedicationCheckinStatus } from './medicationCheckins'
import type { MedicationOccurrenceStatus } from './medicationOccurrences'
import type { BarrierCode, SubreasonCode, SupportCode } from './trackC'

// Contract sources:
// - backend/app/apis/v1/medication_report_routers.py
// - backend/app/dtos/medication_reports.py

export type MedicationReportPeriod = 7 | 30
export type MedicationReportTimeSlot = 'BREAKFAST' | 'LUNCH' | 'DINNER' | 'BEDTIME'

export type MedicationReportCounts = {
  taken_count: number
  not_taken_count: number
  unconfirmed_count: number
  pending_count: number
  cancelled_count: number
}

export type MedicationReportRate = {
  numerator: number
  denominator: number
  percentage: number | null
}

export type MedicationReportRecord = {
  occurrence_id: string
  prescription_version_id: string
  prescription_version_medication_id: string
  scheduled_local_date: string
  scheduled_at: string
  confirmation_deadline_at: string
  status: MedicationOccurrenceStatus
  medication_name: string
  strength_text: string | null
  time_slot: MedicationReportTimeSlot
  updated_at: string
  checkin: {
    checkin_id: string
    occurrence_id: string
    status: MedicationCheckinStatus
    taken_at: string | null
    revision: number
    corrected: boolean
    updated_at: string
  } | null
}

// Missed-dose reasons and consultation questions are returned only for the clinic
// view; the default report never carries them.
export type MedicationReportView = 'CLINIC'

export type ClinicBarrierEntry = {
  occurrence_id: string
  scheduled_local_date: string
  medication_name: string
  barrier_code: BarrierCode
  subreason_code: SubreasonCode | null
}

export type ClinicConsultationQuestion = {
  question_id: string
  text: string
  support_code: SupportCode
  medication_name: string
  last_selected_date: string
}

export type ClinicSections = {
  barriers: ClinicBarrierEntry[]
  consultation_questions: ClinicConsultationQuestion[]
}

export type MedicationReportData = {
  period_days: MedicationReportPeriod
  start_date: string
  end_date: string
  timezone: 'Asia/Seoul'
  as_of: string
  counts: MedicationReportCounts
  overdue_pending_count: number
  adherence_rate: MedicationReportRate
  confirmation_rate: MedicationReportRate
  records: MedicationReportRecord[]
  clinic: ClinicSections | null
}

export type MedicationReportResponse = {
  data: MedicationReportData
}

export async function getMedicationReport(
  periodDays: MedicationReportPeriod,
  signal?: AbortSignal,
  view?: MedicationReportView,
): Promise<MedicationReportResponse> {
  const viewQuery = view ? `&view=${view}` : ''

  return apiRequest<MedicationReportResponse>(
    `/api/v1/medication-reports?period_days=${periodDays}${viewQuery}`,
    { signal },
  )
}
