import type {
  MedicationOccurrenceData,
  MedicationOccurrenceMedicationResponse,
} from '../api/medicationSchedules'

export const KST_TIME_ZONE = 'Asia/Seoul'

export type RecordMedicationDetail = MedicationOccurrenceMedicationResponse['data']

export function kstToday(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: KST_TIME_ZONE,
  }).format(new Date())
}

export function isValidLocalDate(value: string | null): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
}

export function formatLocalDate(value: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
    timeZone: 'UTC',
  }).format(new Date(`${value}T12:00:00Z`))
}

export function formatKstTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '--:--'
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: KST_TIME_ZONE,
  }).format(date)
}

export function medicationDescription(
  medication: Pick<
    RecordMedicationDetail,
    'medication_name' | 'strength_text' | 'dose_value' | 'dose_unit'
  >,
): string {
  const amount =
    medication.dose_value !== null && medication.dose_unit
      ? `${medication.dose_value}${medication.dose_unit}`
      : null
  return [medication.medication_name, medication.strength_text, amount]
    .filter(Boolean)
    .join(' · ')
}

/**
 * Occurrence 상태 라벨.
 * time-slot grouping key가 계약에 없으므로 시간대 분류는 하지 않는다.
 */
export function occurrenceStateLabel(
  occurrence: MedicationOccurrenceData,
): string {
  if (occurrence.status === 'CANCELLED') return '취소된 일정'
  if (occurrence.checkin?.status === 'TAKEN') return '복용 완료'
  if (occurrence.checkin?.status === 'NOT_TAKEN') return '복용하지 않음'
  if (occurrence.checkin?.status === 'UNCONFIRMED') return '확인 필요'
  return '복용 예정'
}

const RECORD_WEEKDAY_LABELS = ['월', '화', '수', '목', '금', '토', '일'] as const

export function shiftLocalDate(value: string, days: number): string {
  const base = new Date(`${value}T12:00:00Z`)
  base.setUTCDate(base.getUTCDate() + days)
  return base.toISOString().slice(0, 10)
}

/** 선택한 날짜가 속한 주의 월요일. */
export function startOfLocalWeek(value: string): string {
  const base = new Date(`${value}T12:00:00Z`)
  // getUTCDay(): 0=일 … 6=토 → 월요일 기준으로 보정한다.
  const mondayOffset = (base.getUTCDay() + 6) % 7
  return shiftLocalDate(value, -mondayOffset)
}

export type RecordWeekCell = {
  date: string
  weekday: string
  dayOfMonth: number
  isToday: boolean
  isSelected: boolean
  isFuture: boolean
}

/**
 * RECORD-01 고정 7일(월~일) 셀.
 * Figma Navigation guardrail: 이전/다음 주 이동, swipe, 월간 calendar 없음.
 * 오늘 이후는 기록이 존재할 수 없으므로 선택 불가로 둔다.
 */
export function buildRecordWeek(
  selectedDate: string,
  today: string,
): RecordWeekCell[] {
  const weekStart = startOfLocalWeek(selectedDate)
  return RECORD_WEEKDAY_LABELS.map((weekday, index) => {
    const date = shiftLocalDate(weekStart, index)
    return {
      date,
      weekday,
      dayOfMonth: Number(date.slice(8, 10)),
      isToday: date === today,
      isSelected: date === selectedDate,
      isFuture: date > today,
    }
  })
}

/** 예정 시각 전 PENDING은 기록할 수 없으므로 read-only로 판정한다. */
export function isRecordReadOnly(
  occurrence: MedicationOccurrenceData,
  now: number,
): boolean {
  if (occurrence.status === 'CANCELLED') return true
  if (occurrence.checkin) return false
  const scheduledAt = Date.parse(occurrence.scheduled_at)
  return Number.isFinite(scheduledAt) && now < scheduledAt
}
