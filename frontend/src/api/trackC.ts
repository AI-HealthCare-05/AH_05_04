import { apiRequest } from './client'

// Mirrors backend/app/dtos/track_c{,_support}.py. No client-side support selection.
export type BarrierCode = 'FORGOT' | 'SCHEDULE_OR_TRAVEL' | 'INSTRUCTIONS_UNCLEAR' | 'NEED_DOUBT' | 'MEDICATION_CONCERN' | 'ACCESS_OR_COST'
export type TravelSituation = 'SCHEDULE_CHANGED' | 'MEDICATION_NOT_WITH_ME'
export type SupportCode = 'REMINDER_SETUP' | 'ROUTINE_OR_TRAVEL_PLAN' | 'INSTRUCTION_REVIEW' | 'PURPOSE_REVIEW' | 'MEDICATION_CONCERN_GUIDANCE' | 'ACCESS_SUPPORT'
export type Safety = {
  assessment_id: string
  medication_checkin_id: string
  checkin_revision: number
  response_level: 'ROUTINE' | 'URGENT' | 'EMERGENCY' | 'UNKNOWN'
  safety_disposition: 'NORMAL' | 'URGENT_ROUTED' | 'EMERGENCY_ROUTED' | 'BLOCKED_ACTION' | 'UNKNOWN_RISK'
  revision: number
  message_code: string
  copy_version: string
  source_version: string
}
export type Barrier = {
  barrier_response_id: string
  medication_checkin_id: string
  checkin_revision: number
  safety_assessment_id: string
  response_status: 'ANSWERED' | 'DECLINED'
  barrier_code: BarrierCode | null
  revision: number
}
export type ActionConfig = {
  schema_version: 'track-c-handler-config-v1'
  rationale_code: string
  parameters: {
    destination: 'MEDICATION_SCHEDULE_SETUP'
    prescription_version_medication_id: string
  } | { content_key: Exclude<SupportCode, 'REMINDER_SETUP'> }
}
export type Support = {
  support_code: SupportCode
  rule_version: string
  copy_version: string
  priority: number
  rationale_code: string
  action_config: ActionConfig
  support_copy: {
    title: string
    body: string
    confirmation_prompt: string
    primary_label: string
    secondary_label: string
  }
}
export type Offer = {
  barrier_response_id: string
  medication_checkin_id: string
  checkin_revision: number
  safety_assessment_id: string
  supports: Support[]
  reason_code: 'NO_ELIGIBLE_SUPPORT' | null
}
export type Plan = {
  support_action_plan_id: string
  barrier_response_id: string
  support_code: SupportCode
  rule_version: string
  copy_version: string
  action_config_snapshot: ActionConfig
  status: 'ACTIVE' | 'COMPLETED' | 'CANCELLED'
  created_at: string
  completed_at: string | null
  cancelled_at: string | null
}
export type SafetyRequest = {
  medication_checkin_id: string
  checkin_revision: number
  symptom_codes: string[]
  expected_revision: number
}
export type BarrierRequest = {
  response_status: 'ANSWERED' | 'DECLINED'
  barrier_code: BarrierCode | null
  checkin_revision: number
  expected_revision: number
}
export type CreatePlanRequest = {
  travel_situation?: TravelSituation
  barrier_response_id: string
  support_code: SupportCode
  rule_version: string
  copy_version: string
  confirmed: true
}
export type PatchPlanRequest = {
  status: 'COMPLETED' | 'CANCELLED'
  confirmed: true
}

async function write<T>(path: string, method: string, body: unknown, key: string): Promise<T> {
  return (await apiRequest<{ data: T }>(path, { method, headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(body) })).data
}
export const createSafety = (body: SafetyRequest, key: string) => write<Safety>('/api/v1/safety-assessments', 'POST', body, key)
export const putBarrier = (id: string, body: BarrierRequest, key: string) => write<Barrier>(`/api/v1/medication-checkins/${encodeURIComponent(id)}/barrier-response`, 'PUT', body, key)
export const getOffers = async (id: string, situation?: TravelSituation) => {
  const query = situation ? `?travel_situation=${encodeURIComponent(situation)}` : ''
  return (await apiRequest<{ data: Offer }>(`/api/v1/barrier-responses/${encodeURIComponent(id)}/supports${query}`, { cache: 'no-store' })).data
}
export const createPlan = (body: CreatePlanRequest, key: string) => write<Plan>('/api/v1/support-action-plans', 'POST', body, key)
export const getPlan = async (id: string) => (await apiRequest<{ data: Plan }>(`/api/v1/support-action-plans/${encodeURIComponent(id)}`, { cache: 'no-store' })).data
export const patchPlan = (id: string, body: PatchPlanRequest, key: string) => write<Plan>(`/api/v1/support-action-plans/${encodeURIComponent(id)}`, 'PATCH', body, key)

export type PlanResources = {
  support_action_plan_id: string
  barrier_code: BarrierCode
  occurrence_id: string
  occurrence_local_date: string
  prescription_version_medication_id: string
  support_copy: Support['support_copy']
}
export const getPlanResources = async (id: string) => (await apiRequest<{ data: PlanResources }>(`/api/v1/support-action-plans/${encodeURIComponent(id)}/resources`, { cache: 'no-store' })).data
