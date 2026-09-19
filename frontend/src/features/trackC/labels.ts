import type { BarrierCode, SubreasonCode } from '../../api/trackC'

// Single source for Track C barrier wording. The check-in flow and the clinic
// report both read from here; duplicating either map lets the two screens drift.
// Codes mirror backend/app/services/track_c_personalization.py SUBREASONS.

export const BARRIER_ORDER: BarrierCode[] = [
  'FORGOT',
  'SCHEDULE_OR_TRAVEL',
  'INSTRUCTIONS_UNCLEAR',
  'NEED_DOUBT',
  'MEDICATION_CONCERN',
  'ACCESS_OR_COST',
]

export const BARRIER_LABELS: Record<BarrierCode, string> = {
  FORGOT: '깜빡했어요',
  SCHEDULE_OR_TRAVEL: '일정이나 외출 때문에 어려웠어요',
  INSTRUCTIONS_UNCLEAR: '복용 방법이 헷갈렸어요',
  NEED_DOUBT: '약이 꼭 필요한지 모르겠어요',
  MEDICATION_CONCERN: '약에 대한 걱정이 있었어요',
  ACCESS_OR_COST: '약이 없거나 구하기 어려웠어요',
}

// The clinic view lists many entries at once, so it uses the short form.
export const BARRIER_SHORT_LABELS: Record<BarrierCode, string> = {
  FORGOT: '깜빡함',
  SCHEDULE_OR_TRAVEL: '일정·외출',
  INSTRUCTIONS_UNCLEAR: '복용 방법 혼란',
  NEED_DOUBT: '필요성 의문',
  MEDICATION_CONCERN: '약에 대한 걱정',
  ACCESS_OR_COST: '약 확보 어려움',
}

export const BARRIER_SUBREASONS: Record<BarrierCode, SubreasonCode[]> = {
  FORGOT: ['MISSED_ALERT', 'POSTPONED', 'MEDICATION_CONFUSION'],
  SCHEDULE_OR_TRAVEL: ['SCHEDULE_CHANGED', 'MEDICATION_NOT_WITH_ME', 'PREPARATION_DIFFICULT'],
  INSTRUCTIONS_UNCLEAR: ['DOSE_AMOUNT_UNCLEAR', 'TIMING_OR_FOOD_UNCLEAR', 'MEDICATION_IDENTITY_UNCLEAR', 'LANGUAGE_TOO_COMPLEX'],
  NEED_DOUBT: ['NO_SYMPTOMS', 'NO_NOTICEABLE_EFFECT', 'VALUES_IMPROVED', 'NEED_UNCLEAR'],
  MEDICATION_CONCERN: ['LONG_TERM_USE', 'DEPENDENCE_OR_TOLERANCE', 'BODY_HARM', 'PILL_BURDEN', 'CONFLICTING_INFORMATION'],
  ACCESS_OR_COST: ['RUNNING_LOW', 'REFILL_MISSED', 'VISIT_DIFFICULT', 'COST_BURDEN'],
}

export const SUBREASON_LABELS: Record<SubreasonCode, string> = {
  MISSED_ALERT: '알림을 보거나 듣지 못했어요',
  POSTPONED: '미뤘다가 잊었어요',
  MEDICATION_CONFUSION: '약이나 복용 회차가 헷갈렸어요',
  SCHEDULE_CHANGED: '생활 일정이 바뀌었어요',
  MEDICATION_NOT_WITH_ME: '약을 가지고 나오지 않았어요',
  PREPARATION_DIFFICULT: '외출 준비가 어려웠어요',
  DOSE_AMOUNT_UNCLEAR: '한 번에 먹을 수량이 헷갈려요',
  TIMING_OR_FOOD_UNCLEAR: '시간이나 식사 조건이 헷갈려요',
  MEDICATION_IDENTITY_UNCLEAR: '약을 구분하기 어려워요',
  LANGUAGE_TOO_COMPLEX: '설명이 길거나 어려워요',
  NO_SYMPTOMS: '현재 증상이 없어요',
  NO_NOTICEABLE_EFFECT: '효과를 체감하지 못해요',
  VALUES_IMPROVED: '검사 수치가 좋아졌어요',
  NEED_UNCLEAR: '왜 필요한지 잘 모르겠어요',
  LONG_TERM_USE: '장기간 복용이 걱정돼요',
  DEPENDENCE_OR_TOLERANCE: '의존성이나 내성이 걱정돼요',
  BODY_HARM: '몸에 해가 될까 걱정돼요',
  PILL_BURDEN: '복용하는 약이 많아 부담돼요',
  CONFLICTING_INFORMATION: '인터넷·지인 정보와 안내가 달라요',
  RUNNING_LOW: '약이 곧 떨어져요',
  REFILL_MISSED: '재처방 시기를 놓쳤어요',
  VISIT_DIFFICULT: '병원이나 약국 방문이 어려워요',
  COST_BURDEN: '비용이 부담돼요',
}

export function isSubreasonCode(value: string): value is SubreasonCode {
  return Object.prototype.hasOwnProperty.call(SUBREASON_LABELS, value)
}
