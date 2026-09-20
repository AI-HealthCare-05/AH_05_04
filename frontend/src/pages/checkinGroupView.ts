import type {
  MedicationCheckinSnapshot,
  MedicationOccurrenceData,
} from '../api/medicationSchedules'

/** 사용자가 직접 선택할 수 있는 값. PENDING/UNCONFIRMED/SKIPPED 등은 포함하지 않는다. */
export type CheckinSelection = 'TAKEN' | 'NOT_TAKEN'

export type CheckinGroup = {
  /** 그룹 식별은 exact scheduled_at 문자열이다. 신규 Backend ID를 만들지 않는다. */
  scheduledAt: string
  /** scheduled_at 시간순 정렬에서의 표시용 회차(1-base). 시간 의미 추정이 아니다. */
  ordinal: number
  occurrences: MedicationOccurrenceData[]
}

export type CheckinGroupState = 'COMPLETE' | 'ATTENTION' | 'UPCOMING'

export type CheckinGroupProgress = {
  completed: number
  total: number
  state: CheckinGroupState
}

export const CHECKIN_GROUP_STATE_LABEL: Record<CheckinGroupState, string> = {
  COMPLETE: '완료',
  ATTENTION: '확인 필요',
  UPCOMING: '예정',
}

/** 취소된 일정은 기록 대상이 아니다. */
export function isCheckinTarget(occurrence: MedicationOccurrenceData): boolean {
  return occurrence.status !== 'CANCELLED'
}

/** 사용자가 이미 확정한 기록인지. UNCONFIRMED는 사용자 선택이 아니므로 제외한다. */
export function isUserRecorded(
  checkin: MedicationCheckinSnapshot | null,
): checkin is MedicationCheckinSnapshot & { status: CheckinSelection } {
  return checkin?.status === 'TAKEN' || checkin?.status === 'NOT_TAKEN'
}

export function selectionFromCheckin(
  checkin: MedicationCheckinSnapshot | null,
): CheckinSelection | null {
  return isUserRecorded(checkin) ? checkin.status : null
}

/**
 * 당일 occurrence를 exact scheduled_at으로 묶고 시간순 정렬한다.
 * 아침/점심/저녁 같은 시간대 의미는 추정하지 않는다.
 */
export function groupOccurrencesByScheduledAt(
  occurrences: MedicationOccurrenceData[],
): CheckinGroup[] {
  const buckets = new Map<string, MedicationOccurrenceData[]>()

  for (const occurrence of occurrences) {
    if (!isCheckinTarget(occurrence)) continue
    const bucket = buckets.get(occurrence.scheduled_at)
    if (bucket) bucket.push(occurrence)
    else buckets.set(occurrence.scheduled_at, [occurrence])
  }

  return [...buckets.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([scheduledAt, items], index) => ({
      scheduledAt,
      ordinal: index + 1,
      occurrences: items,
    }))
}

export function findGroupForOccurrence(
  groups: CheckinGroup[],
  occurrenceId: string,
): CheckinGroup | null {
  return (
    groups.find((group) =>
      group.occurrences.some((item) => item.occurrence_id === occurrenceId),
    ) ?? null
  )
}

/**
 * 그룹 진행 상태를 public occurrence/checkin 데이터만으로 계산한다.
 * 새 서버 집계 필드를 만들지 않는다.
 */
export function getGroupProgress(
  group: CheckinGroup,
  now: number,
): CheckinGroupProgress {
  const total = group.occurrences.length
  const completed = group.occurrences.filter((occurrence) =>
    isUserRecorded(occurrence.checkin),
  ).length

  const scheduledAt = Date.parse(group.scheduledAt)
  const isBeforeScheduled =
    Number.isFinite(scheduledAt) && now < scheduledAt

  const state: CheckinGroupState =
    total > 0 && completed === total
      ? 'COMPLETE'
      : isBeforeScheduled
        ? 'UPCOMING'
        : 'ATTENTION'

  return { completed, total, state }
}

/** 예정 시각 전에는 기록을 저장할 수 없다. */
export function isBeforeScheduledAt(scheduledAt: string, now: number): boolean {
  const parsed = Date.parse(scheduledAt)
  return Number.isFinite(parsed) && now < parsed
}
