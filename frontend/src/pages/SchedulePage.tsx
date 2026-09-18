import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import {
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  cancelMedicationSchedule,
  createScheduleIdempotencyKey,
  getMedicationDay,
  getOccurrenceMedication,
  getScheduleRecommendation,
  isOccurrenceMedicationNotFoundError,
  isPrescriptionMedicationNotFoundError,
  isPrescriptionVersionConflictError,
  isScheduleRevisionConflictError,
  putMedicationSchedule,
  type MedicationDayResponse,
  type MedicationOccurrenceData,
  type MedicationOccurrenceMedicationResponse,
  type MedicationScheduleItem,
  type PutMedicationScheduleInput,
  type RecommendationContext,
} from '../api/medicationSchedules'
import {
  createCheckinIdempotencyKey,
  isCheckinBeforeScheduledAtError,
  isCheckinConflictError,
  isCheckinRevisionConflictError,
  isCheckinValidationError,
  isOccurrenceNotFoundError,
  putMedicationCheckin,
  type MedicationCheckinUserStatus,
  type PutMedicationCheckinInput,
} from '../api/medicationCheckins'
import {
  resolveLogicalMutationAttempt,
  type LogicalMutationAttempt,
} from '../api/logicalMutationAttempt'
import {
  getLatestPrescription,
  type Medication,
  type PrescriptionResponse,
} from '../api/prescriptions'
import { ScheduleRecommendation } from './ScheduleRecommendation'
import bellIcon from '../assets/icon-bell-notification.svg'
import { Button, Card, MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import '../design-system/prototype.css'
import './MvpPages.css'
import './SchedulePage.css'

const KST_TIME_ZONE = 'Asia/Seoul'
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const LOCAL_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/
const MAX_TIMEOUT_MS = 2_147_483_647

export type SchedulePageServices = {
  getScheduleRecommendation?: typeof getScheduleRecommendation
  getMedicationDay: typeof getMedicationDay
  getOccurrenceMedication: typeof getOccurrenceMedication
  getLatestPrescription: typeof getLatestPrescription
  putMedicationSchedule: typeof putMedicationSchedule
  cancelMedicationSchedule: typeof cancelMedicationSchedule
  putMedicationCheckin: typeof putMedicationCheckin
  createScheduleIdempotencyKey: typeof createScheduleIdempotencyKey
  createCheckinIdempotencyKey: typeof createCheckinIdempotencyKey
}

const defaultServices: SchedulePageServices = {
  getMedicationDay,
  getOccurrenceMedication,
  getScheduleRecommendation,
  getLatestPrescription,
  putMedicationSchedule,
  cancelMedicationSchedule,
  putMedicationCheckin,
  createScheduleIdempotencyKey,
  createCheckinIdempotencyKey,
}

type MedicationDetail = MedicationOccurrenceMedicationResponse['data']
type LoadFailure = 'AUTH' | 'NOT_FOUND' | 'VALIDATION' | 'NETWORK' | 'SERVER'
type LogicalMutationOperation = 'SCHEDULE_PUT' | 'SCHEDULE_CANCEL' | 'CHECKIN_PUT'

function kstToday(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: KST_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

function hasElapsedTimeToday(draft: ScheduleDraft): boolean {
  if (draft.startDate !== kstToday()) return false

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: KST_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date())
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  const currentTime = `${value.hour}:${value.minute}`
  return draft.times.some(
    (localTime) => /^([01]\d|2[0-3]):[0-5]\d$/.test(localTime) && localTime <= currentTime,
  )
}

function isValidLocalDate(value: string | null): value is string {
  if (!value || !LOCAL_DATE_PATTERN.test(value)) return false
  const parsed = new Date(`${value}T12:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

function formatLocalDate(value: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
    timeZone: 'UTC',
  }).format(new Date(`${value}T12:00:00Z`))
}

function formatKstTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '--:--'
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: KST_TIME_ZONE,
  }).format(date)
}

function medicationDescription(
  medication: Pick<
    Medication,
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

function scheduleMedicationMap(
  scheduleItems: MedicationScheduleItem[],
  prescription: PrescriptionResponse['data'],
): Record<string, Medication> | null {
  if (!prescription.current) return null

  const medicationEntries = new Map<string, Medication>()
  for (const medication of prescription.medications) {
    const medicationId = medication.prescription_version_medication_id
    if (medicationEntries.has(medicationId)) return null
    medicationEntries.set(medicationId, medication)
  }

  const scheduleIds = new Set<string>()
  for (const item of scheduleItems) {
    const medicationId = item.prescription_version_medication_id
    if (scheduleIds.has(medicationId) || !medicationEntries.has(medicationId)) {
      return null
    }
    scheduleIds.add(medicationId)
  }

  return Object.fromEntries(
    scheduleItems.map((item) => {
      const medicationId = item.prescription_version_medication_id
      return [medicationId, medicationEntries.get(medicationId)!]
    }),
  )
}

function isMatchingMedication(
  occurrence: MedicationOccurrenceData,
  medication: MedicationDetail,
): boolean {
  return (
    medication.occurrence_id === occurrence.occurrence_id &&
    medication.prescription_version_id === occurrence.prescription_version_id &&
    medication.prescription_version_medication_id ===
      occurrence.prescription_version_medication_id
  )
}

function classifyLoadFailure(error: unknown): LoadFailure {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'AUTH'
    if (error.status === 404) return 'NOT_FOUND'
    if (error.status === 422) return 'VALIDATION'
    if (error.status >= 500) return 'SERVER'
  }
  return 'NETWORK'
}

function failureCopy(failure: LoadFailure): { title: string; body: string } {
  if (failure === 'AUTH') {
    return {
      title: '로그인을 다시 확인해 주세요',
      body: '로그인 정보가 만료되었거나 유효하지 않아요.',
    }
  }
  if (failure === 'NOT_FOUND') {
    return {
      title: '복약 기록을 찾을 수 없어요',
      body: '요청한 기록을 확인할 수 없어요.',
    }
  }
  if (failure === 'VALIDATION') {
    return {
      title: '날짜를 확인해 주세요',
      body: '유효한 날짜로 다시 시도해 주세요.',
    }
  }
  if (failure === 'SERVER') {
    return {
      title: '일정을 잠시 불러오지 못했어요',
      body: '잠시 후 다시 시도해 주세요.',
    }
  }
  return {
    title: '인터넷 연결을 확인해 주세요',
    body: '연결이 복구되면 다시 시도할 수 있어요.',
  }
}

function StatusCard({
  title,
  body,
  action,
  onAction,
  isAlert = false,
}: {
  title: string
  body: string
  action?: string
  onAction?: () => void
  isAlert?: boolean
}) {
  return (
    <section
      className="ds-card schedule-state-card"
      role={isAlert ? 'alert' : undefined}
      aria-live={isAlert ? 'assertive' : undefined}
    >
      <h2>{title}</h2>
      <p>{body}</p>
      {action && onAction && (
        <Button fullWidth onClick={onAction}>{action}</Button>
      )}
    </section>
  )
}

function NavigationShell({
  children,
  onBack,
}: {
  children: React.ReactNode
  onBack?: () => void
}) {
  const navigate = useNavigate()
  return (
    <MobileShell
      title="Dosey 도지"
      activeNavigation="일정"
      onBack={onBack}
      headerAction={
        !onBack ? (
          <button
            className="schedule-page__notification"
            type="button"
            onClick={() => navigate('/notifications')}
            aria-label="알림"
          >
            <img src={bellIcon} alt="" />
          </button>
        ) : undefined
      }
      onNavigate={(item) => {
        if (item === '홈') navigate('/')
        if (item === '일정') navigate('/schedule')
        if (item === '도지') navigate('/chat')
        if (item === '가이드') navigate('/guides')
        if (item === '메뉴') navigate('/menu')
      }}
    >
      {children}
    </MobileShell>
  )
}

type ScheduleDraft = {
  startDate: string
  endMode: 'DATE' | 'OPEN_ENDED'
  endDate: string
  times: string[]
  recommendationContext?: RecommendationContext
}

type TimePeriod = 'MORNING' | 'AFTERNOON'

type ScheduleSaveState = {
  status: 'IDLE' | 'SAVING' | 'SUCCESS' | 'ERROR'
  message: string
  kind?: 'VALIDATION' | 'MUTATION'
}

function preferredTimePeriods(
  timingText: string | null,
  frequencyPerDay: number | null,
): TimePeriod[] {
  if (frequencyPerDay === null || frequencyPerDay <= 0) return []
  const normalized = timingText?.replace(/\s+/g, '') ?? ''
  if (!normalized) return []

  const periods: TimePeriod[] = Array.from(
    normalized.matchAll(/아침|점심|저녁/g),
    (match) => match[0] === '아침' ? 'MORNING' : 'AFTERNOON',
  )
  return periods.length === frequencyPerDay ? periods : []
}

function preferredTimePeriodLabel(period: TimePeriod): string {
  return period === 'MORNING' ? '오전부터 선택' : '오후부터 선택'
}

function initialScheduleDraft(
  medication: Medication,
  item: MedicationScheduleItem,
): ScheduleDraft {
  if (item.schedule_item_status === 'READY' && item.schedule?.status === 'ACTIVE') {
    return {
      startDate: item.schedule.start_local_date,
      endMode: item.schedule.end_mode,
      endDate: item.schedule.end_local_date ?? '',
      times: [...item.schedule.local_times],
    }
  }
  const frequency =
    Number.isInteger(medication.frequency_per_day) &&
    (medication.frequency_per_day ?? 0) > 0
      ? medication.frequency_per_day!
      : 1
  return {
    startDate: '',
    endMode: 'DATE',
    endDate: '',
    times: Array.from({ length: frequency }, () => ''),
  }
}

function validateScheduleDraft(
  draft: ScheduleDraft,
  frequencyPerDay: number | null,
): string | null {
  if (!isValidLocalDate(draft.startDate)) return '복용 시작일을 확인해 주세요.'
  if (draft.endMode === 'DATE' && !isValidLocalDate(draft.endDate)) {
    return '종료일을 확인해 주세요.'
  }
  if (draft.endMode === 'DATE' && draft.endDate < draft.startDate) {
    return '종료일은 시작일보다 빠를 수 없어요.'
  }
  if (draft.times.some((time) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))) {
    return '모든 복용 시간을 확인해 주세요.'
  }
  if (new Set(draft.times).size !== draft.times.length) {
    return '같은 시간은 한 번만 입력해 주세요.'
  }
  if (frequencyPerDay === null || draft.times.length !== frequencyPerDay) {
    return frequencyPerDay === null
      ? '처방의 하루 복용 횟수를 확인할 수 없어요.'
      : `처방의 하루 복용 횟수(${frequencyPerDay}회)와 복용 시간 ${draft.times.length}개가 일치하지 않아요.`
  }
  return null
}

function ScheduleEditor({
  items,
  medications,
  selectedDate,
  services,
  onSaved,
  onAllSaved,
  onConflict,
  isReloading,
}: {
  items: MedicationScheduleItem[]
  medications: Record<string, Medication>
  selectedDate: string
  services: SchedulePageServices
  onSaved: () => Promise<boolean>
  onAllSaved: () => void
  onConflict: () => Promise<boolean>
  isReloading: boolean
}) {
  const orderedItems = useMemo(
    () => [...items].sort((left, right) => {
      const leftMedication = medications[left.prescription_version_medication_id]
      const rightMedication = medications[right.prescription_version_medication_id]
      return (leftMedication?.display_order ?? 0) - (rightMedication?.display_order ?? 0)
    }),
    [items, medications],
  )
  const [drafts, setDrafts] = useState<Record<string, ScheduleDraft>>(() =>
    Object.fromEntries(orderedItems.map((item) => {
      const id = item.prescription_version_medication_id
      return [id, initialScheduleDraft(medications[id], item)]
    })),
  )
  const [saveStates, setSaveStates] = useState<Record<string, ScheduleSaveState>>({})
  const [isSaving, setIsSaving] = useState(false)
  const [summaryMessage, setSummaryMessage] = useState('')
  const scheduleMutationAttemptsRef = useRef<Record<
    string,
    LogicalMutationAttempt<LogicalMutationOperation, unknown>
  >>({})

  useEffect(() => {
    setDrafts((current) => {
      const next: Record<string, ScheduleDraft> = {}
      for (const item of orderedItems) {
        const id = item.prescription_version_medication_id
        next[id] = current[id] ?? initialScheduleDraft(medications[id], item)
      }
      return next
    })
  }, [medications, orderedItems])

  const changeScheduleInput = (
    medicationId: string,
    update: (current: ScheduleDraft) => ScheduleDraft,
  ) => {
    delete scheduleMutationAttemptsRef.current[medicationId]
    setDrafts((current) => ({
      ...current,
      [medicationId]: update(current[medicationId]),
    }))
    setSaveStates((current) => ({
      ...current,
      [medicationId]: { status: 'IDLE', message: '' },
    }))
    setSummaryMessage('')
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (isSaving) return
    const validationStates: Record<string, ScheduleSaveState> = {}
    for (const item of orderedItems) {
      const id = item.prescription_version_medication_id
      const medication = medications[id]
      const frequencyPerDay =
        Number.isInteger(medication.frequency_per_day) &&
        (medication.frequency_per_day ?? 0) > 0
          ? medication.frequency_per_day
          : null
      const validationMessage = validateScheduleDraft(drafts[id], frequencyPerDay)
      if (validationMessage) {
        validationStates[id] = {
          status: 'ERROR',
          message: validationMessage,
          kind: 'VALIDATION',
        }
      }
    }
    if (Object.keys(validationStates).length > 0) {
      setSaveStates((current) => ({ ...current, ...validationStates }))
      setSummaryMessage('입력하지 않았거나 확인이 필요한 항목이 있어요.')
      const firstInvalidMedicationId = Object.keys(validationStates)[0]
      event.currentTarget
        .querySelector<HTMLInputElement>(`[data-medication-id="${firstInvalidMedicationId}"] input`)
        ?.focus()
      return
    }

    setIsSaving(true)
    setSummaryMessage('')
    let successCount = 0
    let failureCount = 0
    let shouldReload = false
    let stoppedForConflict = false
    let shouldRefreshAfterStop = false

    for (const item of orderedItems) {
      const id = item.prescription_version_medication_id
      if (saveStates[id]?.status === 'SUCCESS') continue
      const draft = drafts[id]
      const requestPayload: PutMedicationScheduleInput =
        draft.endMode === 'DATE'
          ? {
              startLocalDate: draft.startDate,
              endMode: draft.endMode,
              endLocalDate: draft.endDate,
              localTimes: draft.times,
              expectedRevision: item.revision ?? 0,
            }
          : {
              startLocalDate: draft.startDate,
              endMode: draft.endMode,
              localTimes: draft.times,
              expectedRevision: item.revision ?? 0,
            }
      if (draft.recommendationContext) requestPayload.recommendationContext = draft.recommendationContext
      const attempt = resolveLogicalMutationAttempt(
        scheduleMutationAttemptsRef.current[id] ?? null,
        'SCHEDULE_PUT',
        id,
        requestPayload,
        requestPayload.expectedRevision,
        services.createScheduleIdempotencyKey,
      )
      scheduleMutationAttemptsRef.current[id] = attempt
      setSaveStates((current) => ({
        ...current,
        [id]: { status: 'SAVING', message: '저장 중…' },
      }))

      try {
        await services.putMedicationSchedule(
          attempt.targetId,
          attempt.requestPayload,
          attempt.idempotencyKey,
        )
        delete scheduleMutationAttemptsRef.current[id]
        successCount += 1
        shouldReload = true
        setSaveStates((current) => ({
          ...current,
          [id]: { status: 'SUCCESS', message: '이 약의 일정이 저장됐어요.' },
        }))
      } catch (error) {
        failureCount += 1
        let message = '연결을 확인한 뒤 다시 시도해 주세요. 입력한 내용은 그대로 유지돼요.'
        if (error instanceof ApiError && error.code === 'SCHEDULE_RECOMMENDATION_CONFLICT') {
          delete scheduleMutationAttemptsRef.current[id]
          message = '후보의 기준이나 입력 시각이 달라졌어요. 식사 시각을 확인하고 다시 계산하거나 직접 입력으로 전환해 주세요.'
        } else if (isScheduleRevisionConflictError(error)) {
          delete scheduleMutationAttemptsRef.current[id]
          message = '일정이 다른 곳에서 변경됐어요. 최신 상태를 확인한 뒤 다시 저장해 주세요.'
          stoppedForConflict = true
          shouldRefreshAfterStop = true
        } else if (
          isPrescriptionVersionConflictError(error) ||
          isPrescriptionMedicationNotFoundError(error) ||
          (error instanceof ApiError && error.status === 404)
        ) {
          delete scheduleMutationAttemptsRef.current[id]
          message = '현재 처방 내용이 변경됐어요. 최신 처방을 확인한 뒤 다시 저장해 주세요.'
          stoppedForConflict = true
          shouldRefreshAfterStop = true
        } else if (error instanceof ApiError && error.status === 401) {
          delete scheduleMutationAttemptsRef.current[id]
          message = '로그인 정보를 다시 확인해 주세요.'
          stoppedForConflict = true
        } else if (error instanceof ApiError && error.status === 422) {
          delete scheduleMutationAttemptsRef.current[id]
          message = '입력한 날짜와 복용 시간을 다시 확인해 주세요.'
        } else if (error instanceof ApiError && error.status >= 500) {
          message = '일정을 저장하지 못했어요. 입력값을 유지한 채 다시 시도해 주세요.'
        } else if (error instanceof ApiError) {
          delete scheduleMutationAttemptsRef.current[id]
        }
        setSaveStates((current) => ({
          ...current,
          [id]: { status: 'ERROR', message, kind: 'MUTATION' },
        }))
        if (stoppedForConflict) break
      }
    }

    if (stoppedForConflict) {
      if (shouldRefreshAfterStop) {
        setSummaryMessage('최신 일정과 처방을 다시 불러오는 중이에요. 저장되지 않은 약은 내용을 확인한 뒤 다시 저장해 주세요.')
        if (!await onConflict()) {
          setSummaryMessage('최신 일정과 처방을 불러오지 못했어요. 입력한 내용을 유지한 채 다시 시도해 주세요.')
        }
      } else {
        setSummaryMessage('저장을 중단했어요. 로그인 정보를 확인한 뒤 다시 시도해 주세요.')
      }
    } else if (failureCount > 0) {
      setSummaryMessage(
        successCount > 0
          ? '일부 약만 저장됐어요. 실패한 약의 입력값을 확인하고 다시 시도해 주세요.'
          : '일정을 저장하지 못했어요. 입력값을 유지한 채 다시 시도할 수 있어요.',
      )
      if (shouldReload && !await onSaved()) {
        setSummaryMessage('일부 약만 저장됐지만 최신 일정을 불러오지 못했어요. 입력값을 유지한 채 다시 시도해 주세요.')
      }
    } else {
      setSummaryMessage('모든 약의 복약 일정이 저장됐어요.')
      if (await onSaved()) {
        onAllSaved()
      } else {
        setSummaryMessage('모든 약의 일정은 저장됐지만 최신 일정을 불러오지 못했어요. 입력 내용을 유지한 채 다시 불러올 수 있어요.')
      }
    }
    setIsSaving(false)
  }

  return (
    <form className="schedule-editor-form" onSubmit={handleSubmit} noValidate>
      <div className="schedule-editor-list">
        {orderedItems.map((item) => {
          const id = item.prescription_version_medication_id
          const medication = medications[id]
          const draft = drafts[id]
          const frequencyPerDay =
            Number.isInteger(medication.frequency_per_day) &&
            (medication.frequency_per_day ?? 0) > 0
              ? medication.frequency_per_day!
              : null
          const saveState = saveStates[id]
          const hasValidationError =
            saveState?.status === 'ERROR' && saveState.kind === 'VALIDATION'
          const errorMessageId = `schedule-error-${id}`
          const directionsId = `schedule-directions-${id}`
          const elapsedTimeNoticeId = `schedule-elapsed-time-notice-${id}`
          const showElapsedTimeNotice = hasElapsedTimeToday(draft)
          const preferredPeriods = preferredTimePeriods(
            medication.timing_text,
            frequencyPerDay,
          )
          return (
            <Card className="schedule-editor" key={id}>
              <div className="schedule-editor__heading">
                <div>
                  <h2>{medication.medication_name}</h2>
                  <p>{frequencyPerDay ? `하루 ${frequencyPerDay}회 복용` : medicationDescription(medication)}</p>
                </div>
                {saveState?.status === 'SUCCESS' && (
                  <span className="schedule-editor__success-badge">저장 완료</span>
                )}
              </div>
              {import.meta.env.DEV && item.schedule_id === null && (
                <ScheduleRecommendation
                  key={`${id}-${item.revision ?? 0}`}
                  load={services.getScheduleRecommendation}
                  medicationId={id} medicationName={medication.medication_name}
                  timingText={medication.timing_text} disabled={isSaving || isReloading}
                  onApply={(times, recommendationContext) => changeScheduleInput(id, (current) => ({ ...current, times, recommendationContext }))}
                  onInvalidate={() => changeScheduleInput(id, (current) => current.recommendationContext
                    ? { ...current, times: current.times.map(() => ''), recommendationContext: undefined }
                    : current)}
                  onManual={() => changeScheduleInput(id, (current) => ({ ...current, recommendationContext: undefined }))}
                />
              )}
              <div className="schedule-editor__fields" data-medication-id={id}>
                <label>
                  <span>시작일</span>
                  <input
                    aria-label={`${medication.medication_name} 복용 시작일`}
                    type="date"
                    value={draft.startDate}
                    placeholder={selectedDate}
                    disabled={isSaving || isReloading}
                    aria-invalid={hasValidationError || undefined}
                    aria-describedby={hasValidationError ? errorMessageId : undefined}
                    onChange={(event) => changeScheduleInput(id, (current) => ({
                      ...current,
                      startDate: event.target.value,
                    }))}
                    required
                  />
                </label>
                <fieldset>
                  <legend>복용 종료</legend>
                  <label>
                    <input
                      type="radio"
                      name={`end-mode-${id}`}
                      value="DATE"
                      checked={draft.endMode === 'DATE'}
                      disabled={isSaving || isReloading}
                      aria-invalid={hasValidationError || undefined}
                      aria-describedby={hasValidationError ? errorMessageId : undefined}
                      onChange={() => changeScheduleInput(id, (current) => ({
                        ...current,
                        endMode: 'DATE',
                      }))}
                    />
                    종료일 지정
                  </label>
                  <label>
                    <input
                      type="radio"
                      name={`end-mode-${id}`}
                      value="OPEN_ENDED"
                      checked={draft.endMode === 'OPEN_ENDED'}
                      disabled={isSaving || isReloading}
                      aria-invalid={hasValidationError || undefined}
                      aria-describedby={hasValidationError ? errorMessageId : undefined}
                      onChange={() => changeScheduleInput(id, (current) => ({
                        ...current,
                        endMode: 'OPEN_ENDED',
                      }))}
                    />
                    계속 복용
                  </label>
                </fieldset>
                {draft.endMode === 'DATE' && (
                  <label>
                    <span>종료일</span>
                    <input
                      aria-label={`${medication.medication_name} 복용 종료일`}
                      type="date"
                      value={draft.endDate}
                      min={draft.startDate || undefined}
                      disabled={isSaving || isReloading}
                      aria-invalid={hasValidationError || undefined}
                      aria-describedby={hasValidationError ? errorMessageId : undefined}
                      onChange={(event) => changeScheduleInput(id, (current) => ({
                        ...current,
                        endDate: event.target.value,
                      }))}
                      required
                    />
                  </label>
                )}
                <div className="schedule-editor__times">
                  <span>복용 시간{frequencyPerDay ? ` · ${frequencyPerDay}개 필요` : ''}</span>
                  <p className="schedule-editor__directions" id={directionsId}>
                    <strong>처방 복용 지시</strong>
                    <span>{medication.timing_text?.trim() || '복용 시점 미확인 · 처방전의 복용 지시를 확인해 주세요.'}</span>
                  </p>
                  <div className="schedule-editor__time-grid">
                    {draft.times.map((time, index) => {
                      const periodLabel = timeOfDayLabel(time)
                      const preferredPeriod = !time ? preferredPeriods[index] : undefined
                      return (
                      <label className="schedule-editor__time-field" key={index}>
                        <span className="sr-only">{medication.medication_name} {index + 1}번째 복용 시간</span>
                        {periodLabel && (
                          <span className="schedule-editor__time-period" aria-hidden="true">{periodLabel}</span>
                        )}
                        {preferredPeriod && (
                          <span className="schedule-editor__time-period schedule-editor__time-period--preferred">
                            {preferredTimePeriodLabel(preferredPeriod)}
                          </span>
                        )}
                        <input
                          aria-label={`${medication.medication_name} ${index + 1}번째 복용 시간`}
                          type="time"
                          value={time}
                          disabled={isSaving || isReloading}
                          aria-invalid={hasValidationError || undefined}
                          aria-describedby={[
                            directionsId,
                            showElapsedTimeNotice ? elapsedTimeNoticeId : '',
                            hasValidationError ? errorMessageId : '',
                          ].filter(Boolean).join(' ')}
                          onChange={(event) => changeScheduleInput(id, (current) => {
                            const nextTimes = [...current.times]
                            nextTimes[index] = event.target.value
                            return { ...current, times: nextTimes }
                          })}
                          required
                        />
                      </label>
                      )
                    })}
                  </div>
                  {showElapsedTimeNotice && (
                    <p className="schedule-editor__elapsed-time-notice" id={elapsedTimeNoticeId} role="note">
                      오늘 이미 지난 복용 시간은 오늘 일정에 표시되지 않아요. 이후 날짜에는 설정한 시간대로 표시돼요.
                    </p>
                  )}
                  {frequencyPerDay !== null && (
                    <small>
                      {frequencyPerDay > 1
                        ? '처방된 하루 복용 횟수와 같은 개수의 시간을 입력해 주세요.'
                        : '정확한 시각은 사용자가 직접 확인해요.'}
                    </small>
                  )}
                  {frequencyPerDay === null && (
                    <small>처방의 하루 복용 횟수를 확인할 수 없어 저장할 수 없어요.</small>
                  )}
                </div>
              </div>
              {saveState?.message && (
                <p
                  id={errorMessageId}
                  className={`schedule-editor__message schedule-editor__message--${saveState.status.toLowerCase()}`}
                  role={saveState.status === 'ERROR' ? 'alert' : 'status'}
                >
                  {saveState.message}
                </p>
              )}
            </Card>
          )
        })}
      </div>
      {summaryMessage && (
        <p className="schedule-editor-form__message" role="status" aria-live="polite">
          {summaryMessage}
        </p>
      )}
      <div className="schedule-editor-form__footer">
        <Button fullWidth type="submit" disabled={isSaving || isReloading}>
          {isSaving || isReloading ? '저장 중…' : '복약 일정 저장하기'}
        </Button>
      </div>
    </form>
  )
}

function statusContent(status: MedicationDayResponse['data']['schedule_status']) {
  if (status === 'SETUP_REQUIRED') {
    return {
      title: '일정 설정이 필요해요',
      body: '처방약의 복용 날짜와 시간을 직접 확인해 주세요.',
      action: '일정 설정하기',
    }
  }
  if (status === 'PARTIAL') {
    return {
      title: '일부 약의 시간이 비어 있어요',
      body: '아직 설정하지 않은 처방약의 날짜와 시간을 확인해 주세요.',
      action: '이어서 설정하기',
    }
  }
  if (status === 'INACTIVE') {
    return {
      title: '현재 사용 중인 일정이 없어요',
      body: '다시 복용을 시작할 때 새 일정을 설정할 수 있어요.',
      action: '새 일정 설정하기',
    }
  }
  if (status === 'NO_ACTIVE_PRESCRIPTION') {
    return {
      title: '활성 처방이 필요해요',
      body: '처방전을 등록하고 복약 안내를 먼저 확인해 주세요.',
      action: '처방전 등록하기',
    }
  }
  return null
}

function occurrenceStateLabel(occurrence: MedicationOccurrenceData): string {
  if (occurrence.status === 'CANCELLED') return '취소된 일정'
  if (occurrence.checkin?.status === 'TAKEN') return '복용 완료'
  if (occurrence.checkin?.status === 'NOT_TAKEN') return '복용하지 않음'
  if (occurrence.checkin?.status === 'UNCONFIRMED') return '확인 필요'
  return '복용 예정'
}

function occurrencePeriodLabel(value: string): string {
  const hour = Number(new Intl.DateTimeFormat('en-US', {
    hour: '2-digit',
    hour12: false,
    timeZone: KST_TIME_ZONE,
  }).format(new Date(value)))
  return periodLabelForHour(hour)
}

// 오늘의 복약(occurrencePeriodLabel)과 같은 아침(~10시)/점심(~16시)/저녁 경계를 쓴다.
// 편집 중인 시간 입력(HH:MM, 로컬 벽시계 값)에는 별도 타임존 변환 없이 시(hour)만 그대로 사용한다.
function periodLabelForHour(hour: number): string {
  if (hour < 11) return '아침'
  if (hour < 17) return '점심'
  return '저녁'
}

function timeOfDayLabel(time: string): string | null {
  const match = /^([01]\d|2[0-3]):[0-5]\d$/.exec(time)
  if (!match) return null
  return periodLabelForHour(Number(match[1]))
}

export function SchedulePage({
  services = defaultServices,
}: {
  services?: SchedulePageServices
}) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedSupportMedicationId = searchParams.get('support_medication')
  const supportMedicationId = import.meta.env.DEV && requestedSupportMedicationId && UUID_PATTERN.test(requestedSupportMedicationId)
    ? requestedSupportMedicationId : null
  const requestedDate = searchParams.get('date')
  const selectedDate = isValidLocalDate(requestedDate) ? requestedDate : kstToday()
  const [day, setDay] = useState<MedicationDayResponse['data'] | null>(null)
  const [medications, setMedications] = useState<Record<string, MedicationDetail | null>>({})
  const [scheduleMedications, setScheduleMedications] = useState<Record<string, Medication>>({})
  const [isScheduleIdentityUnavailable, setIsScheduleIdentityUnavailable] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null)
  const [reloadVersion, setReloadVersion] = useState(0)
  const [isEditingSchedule, setIsEditingSchedule] = useState(false)
  const [editingMedicationId, setEditingMedicationId] = useState<string | null>(null)
  const editorReloadCompletionRef = useRef<((succeeded: boolean) => void) | null>(null)

  const reload = useCallback(async () => {
    setIsLoading(true)
    setReloadVersion((version) => version + 1)
  }, [])

  const reloadEditor = useCallback(() => new Promise<boolean>((resolve) => {
    editorReloadCompletionRef.current?.(false)
    editorReloadCompletionRef.current = resolve
    setIsLoading(true)
    setReloadVersion((version) => version + 1)
  }), [])

  useEffect(() => {
    if (requestedDate !== selectedDate) {
      setSearchParams(supportMedicationId ? { date: selectedDate, support_medication: supportMedicationId } : { date: selectedDate }, { replace: true })
    }
  }, [requestedDate, selectedDate, setSearchParams, supportMedicationId])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setIsLoading(true)
    setLoadFailure(null)

    async function load() {
      try {
        const response = await services.getMedicationDay(selectedDate, controller.signal)
        if (!active) return

        let latestPrescriptionLoadFailed = false
        const scheduleIdentityPromise = response.data.schedule_items.length === 0
          ? Promise.resolve<Record<string, Medication> | null>({})
          : services.getLatestPrescription(controller.signal)
              .then((prescription) =>
                scheduleMedicationMap(response.data.schedule_items, prescription.data),
              )
              .catch((error: unknown) => {
                if (controller.signal.aborted) throw error
                latestPrescriptionLoadFailed = true
                return null
              })
        const occurrenceDetailsPromise = Promise.all(
          response.data.occurrences
            .filter((occurrence) => occurrence.scheduled_local_date === selectedDate)
            .map(async (occurrence) => {
              try {
                const detail = await services.getOccurrenceMedication(
                  occurrence.occurrence_id,
                  controller.signal,
                )
                return [
                  occurrence.occurrence_id,
                  isMatchingMedication(occurrence, detail.data) ? detail.data : null,
                ] as const
              } catch (error) {
                if (controller.signal.aborted) throw error
                return [occurrence.occurrence_id, null] as const
              }
            }),
        )
        const [scheduleIdentity, occurrenceEntries] = await Promise.all([
          scheduleIdentityPromise,
          occurrenceDetailsPromise,
        ])
        if (active) {
          const completeEditorReload = editorReloadCompletionRef.current
          editorReloadCompletionRef.current = null
          if (completeEditorReload && latestPrescriptionLoadFailed) {
            completeEditorReload(false)
            return
          }
          setDay(response.data)
          setScheduleMedications(scheduleIdentity ?? {})
          setIsScheduleIdentityUnavailable(scheduleIdentity === null)
          setMedications(Object.fromEntries(occurrenceEntries))
          completeEditorReload?.(true)
        }
      } catch (error) {
        if (!controller.signal.aborted && active) {
          const completeEditorReload = editorReloadCompletionRef.current
          editorReloadCompletionRef.current = null
          if (!completeEditorReload) {
            setDay(null)
            setMedications({})
            setScheduleMedications({})
            setIsScheduleIdentityUnavailable(false)
          }
          setLoadFailure(classifyLoadFailure(error))
          completeEditorReload?.(false)
        }
      } finally {
        if (active) setIsLoading(false)
      }
    }

    void load()
    return () => {
      active = false
      controller.abort()
    }
  }, [reloadVersion, selectedDate, services])

  const occurrences = useMemo(
    () =>
      (day?.occurrences ?? [])
        .filter((occurrence) => occurrence.scheduled_local_date === selectedDate)
        .sort((left, right) => left.scheduled_at.localeCompare(right.scheduled_at)),
    [day, selectedDate],
  )
  const currentOccurrences = occurrences.filter((occurrence) => occurrence.status !== 'CANCELLED')
  const cancelledOccurrences = occurrences.filter((occurrence) => occurrence.status === 'CANCELLED')
  const state = day ? statusContent(day.schedule_status) : null

  const openRelevantEditor = () => {
    if (isScheduleIdentityUnavailable) return
    if (day?.schedule_items.length) {
      setEditingMedicationId(null)
      setIsEditingSchedule(true)
    }
  }

  const goToLogin = () => {
    clearAuthenticatedSession()
    navigate('/login', { replace: true })
  }

  if (isEditingSchedule) {
    const editorItems = (day?.schedule_items ?? []).filter(
      (item) => !editingMedicationId || item.prescription_version_medication_id === editingMedicationId,
    )
    const hasCompleteEditorData = Boolean(
      editorItems.length > 0 &&
      !isScheduleIdentityUnavailable &&
      editorItems.every(
        (item) => scheduleMedications[item.prescription_version_medication_id],
      ),
    )
    return (
      <div className="mvp-page schedule-page schedule-page--editor">
        <NavigationShell onBack={() => setIsEditingSchedule(false)}>
          <main className="app-scroll schedule-page__content schedule-page__editor-content">
            {!day && isLoading ? (
              <Card className="schedule-state-card" aria-live="polite">
                <div role="status">일정을 불러오는 중입니다.</div>
              </Card>
            ) : hasCompleteEditorData && day ? (
              <>
              <header className="schedule-page__editor-intro">
                <p>복약 일정 설정</p>
                <h1>복용할 날짜와 시간을<br />확인해 주세요</h1>
                <span>처방 내용을 기준으로 약별 복용 날짜와 시간을 직접 확인해요.</span>
              </header>
              <p className="schedule-editor__notice">
                <span aria-hidden="true">ⓘ</span>
                <strong>{import.meta.env.DEV ? '명확한 처방과 입력한 식사 종료 시각이 있을 때만 후보를 계산해요.' : 'Dosey는 복용 시간을 추정하거나 추천하지 않아요.'}<br />정확한 시간을 직접 확인해 주세요.</strong>
              </p>
              <ScheduleEditor
                items={editorItems}
                medications={scheduleMedications}
                selectedDate={selectedDate}
                services={services}
                onSaved={reloadEditor}
                onAllSaved={() => {
                  setEditingMedicationId(null)
                  setIsEditingSchedule(false)
                }}
                onConflict={reloadEditor}
                isReloading={isLoading}
              />
              </>
            ) : (
              <StatusCard
                title="일정 설정 정보를 확인할 수 없어요"
                body="최신 처방과 일정을 다시 확인해 주세요."
                isAlert
                action="일정으로 돌아가기"
                onAction={() => setIsEditingSchedule(false)}
              />
            )}
          </main>
        </NavigationShell>
      </div>
    )
  }

  return (
    <div className="mvp-page schedule-page">
      <NavigationShell>
        <main className="app-scroll schedule-page__content">
          <header className="schedule-page__intro">
            <label htmlFor="schedule-date" className="schedule-page__date-label">
              <span>{formatLocalDate(selectedDate)}</span>
            </label>
            <input
              id="schedule-date"
              type="date"
              value={selectedDate}
              onChange={(event) => setSearchParams(supportMedicationId ? { date: event.target.value, support_medication: supportMedicationId } : { date: event.target.value })}
            />
            <h1>복약 일정</h1>
          </header>

          {isLoading && (
            <Card className="schedule-state-card" aria-live="polite">
              <div role="status">일정을 불러오는 중입니다.</div>
            </Card>
          )}

          {!isLoading && loadFailure && (() => {
            const copy = failureCopy(loadFailure)
            return (
              <StatusCard
                title={copy.title}
                body={copy.body}
                isAlert
                action={loadFailure === 'AUTH' ? '로그인하기' : '다시 시도'}
                onAction={loadFailure === 'AUTH' ? goToLogin : () => void reload()}
              />
            )
          })()}

          {!isLoading && day && supportMedicationId && (
            <Card className="schedule-state-card">
              {scheduleMedications[supportMedicationId] ? <>
                <h2>실천 계획의 복약 일정</h2>
                <p>{scheduleMedications[supportMedicationId].medication_name}</p>
                <Button fullWidth onClick={() => { setEditingMedicationId(supportMedicationId); setIsEditingSchedule(true) }}>이 약의 일정 확인·설정</Button>
                <p>확인이나 저장을 마친 뒤 실천 계획 탭으로 돌아가 완료 여부를 직접 확인해 주세요.</p>
              </> : <p role="alert">이 계획에 연결된 약의 일정을 확인할 수 없어요. 다른 약의 일정으로 대신 처리하지 않고 실천 계획 탭으로 돌아가 주세요.</p>}
            </Card>
          )}

          {!isLoading && day && state && (
            <StatusCard
              title={state.title}
              body={state.body}
              action={
                day.schedule_status === 'NO_ACTIVE_PRESCRIPTION' ||
                !isScheduleIdentityUnavailable
                  ? state.action
                  : undefined
              }
              onAction={day.schedule_status === 'NO_ACTIVE_PRESCRIPTION'
                ? () => navigate('/prescriptions/upload', { state: { intent: 'new-prescription' } })
                : openRelevantEditor}
            />
          )}

          {!isLoading && day && state && isScheduleIdentityUnavailable && (
            <Card className="schedule-page__empty schedule-page__identity-alert">
              <p role="alert">약 정보를 확인할 수 없어 일정을 설정할 수 없습니다. 처방 정보를 다시 확인해 주세요.</p>
            </Card>
          )}

          {!isLoading && day && day.schedule_status === 'READY' && (
            <p className="schedule-page__saved" role="status">
              <span aria-hidden="true">✓</span>
              <strong>일정이 저장됐어요.<br />복약 시간을 확인해 주세요.</strong>
            </p>
          )}

          {!isLoading && day && (day.schedule_status === 'READY' || occurrences.length > 0) && (
            <section className="schedule-page__occurrences" aria-labelledby="occurrence-list-title">
              <div className="schedule-page__section-heading">
                <h2 id="occurrence-list-title">오늘의 복약</h2>
              </div>
              {currentOccurrences.length === 0 ? (
                <Card className="schedule-page__empty">
                  <p>이 날짜에 표시할 복약 일정이 없어요.</p>
                </Card>
              ) : currentOccurrences.map((occurrence) => {
                const medication = medications[occurrence.occurrence_id]
                const route = `/schedule/occurrences/${occurrence.occurrence_id}?date=${encodeURIComponent(occurrence.scheduled_local_date)}`
                return (
                  <article
                    className="schedule-occurrence-card"
                    key={occurrence.occurrence_id}
                  >
                    <span className="schedule-occurrence-card__period-label">
                      {occurrencePeriodLabel(occurrence.scheduled_at)}
                    </span>
                    <strong className="schedule-occurrence-card__time">
                      {formatKstTime(occurrence.scheduled_at)}
                    </strong>
                    <span className={`schedule-occurrence-card__state schedule-occurrence-card__state--${occurrence.checkin?.status?.toLowerCase() ?? occurrence.status.toLowerCase()}`}>
                      {occurrenceStateLabel(occurrence)}
                    </span>
                    <span className="schedule-occurrence-card__medication">
                      {medication ? medicationDescription(medication) : '약 정보를 확인할 수 없어요'}
                    </span>
                    {occurrence.status !== 'CANCELLED' && (
                      <Button
                        fullWidth
                        className="schedule-occurrence-card__action"
                        onClick={() => navigate(route)}
                      >
                        {occurrence.checkin ? '복약 기록 수정하기' : '복용 여부 기록하기'}
                      </Button>
                    )}
                  </article>
                )
              })}
              {cancelledOccurrences.length > 0 && (
                <details className="schedule-page__cancelled-history" key={selectedDate}>
                  <summary>취소된 일정 {cancelledOccurrences.length}건 보기</summary>
                  <p>일정 변경 등으로 취소된 이력이에요. 현재 복용할 일정이 아니에요.</p>
                  <ul>
                    {cancelledOccurrences.map((occurrence) => {
                      const medication = medications[occurrence.occurrence_id]
                      return (
                      <li key={occurrence.occurrence_id}>
                        <span>{formatKstTime(occurrence.scheduled_at)} · 취소된 일정</span>
                        <span>{medication
                          ? medicationDescription(medication)
                          : '약 정보를 확인할 수 없어요'}</span>
                      </li>
                      )
                    })}
                  </ul>
                </details>
              )}
            </section>
          )}

          {!isLoading && day && day.schedule_items.length > 0 && day.schedule_status === 'READY' && (
            isScheduleIdentityUnavailable ? (
              <Card className="schedule-page__empty">
                <p role="alert">약 정보를 확인할 수 없어 일정을 설정할 수 없습니다. 처방 정보를 다시 확인해 주세요.</p>
              </Card>
            ) : (
              <button
                className="schedule-page__settings-action"
                type="button"
                onClick={openRelevantEditor}
              >
                <span>복약 일정 설정·수정</span>
                <span aria-hidden="true">›</span>
              </button>
            )
          )}
        </main>
      </NavigationShell>
    </div>
  )
}

function checkinStatusCopy(occurrence: MedicationOccurrenceData): string {
  if (occurrence.status === 'CANCELLED') return '취소된 일정이에요.'
  if (occurrence.checkin?.status === 'TAKEN') return '복용했어요.'
  if (occurrence.checkin?.status === 'NOT_TAKEN') return '복용하지 않았어요.'
  if (occurrence.checkin?.status === 'UNCONFIRMED') return '아직 복용 여부를 확인하지 못했어요.'
  return '아직 복약 기록이 없어요.'
}

export function ScheduleOccurrencePage({
  services = defaultServices,
}: {
  services?: SchedulePageServices
}) {
  const navigate = useNavigate()
  const { occurrenceId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const date = searchParams.get('date')
  const validRoute = UUID_PATTERN.test(occurrenceId) && isValidLocalDate(date)
  const [occurrence, setOccurrence] = useState<MedicationOccurrenceData | null>(null)
  const [medication, setMedication] = useState<MedicationDetail | null>(null)
  const [isLoading, setIsLoading] = useState(validRoute)
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(validRoute ? null : 'NOT_FOUND')
  const [reloadVersion, setReloadVersion] = useState(0)
  const [isSaving, setIsSaving] = useState(false)
  const [mutationMessage, setMutationMessage] = useState('')
  const [currentTime, setCurrentTime] = useState(() => Date.now())
  const headingRef = useRef<HTMLHeadingElement>(null)
  const checkinAttemptRef = useRef<
    LogicalMutationAttempt<LogicalMutationOperation, unknown> | null
  >(null)

  const reload = useCallback(async () => {
    setIsLoading(true)
    setReloadVersion((version) => version + 1)
  }, [])

  useEffect(() => {
    if (!validRoute || !date) return undefined
    const localDate = date
    const controller = new AbortController()
    let active = true
    setIsLoading(true)
    setLoadFailure(null)

    async function load() {
      try {
        const day = await services.getMedicationDay(localDate, controller.signal)
        const matched = day.data.occurrences.find(
          (item) =>
            item.occurrence_id === occurrenceId &&
            item.scheduled_local_date === localDate,
        )
        if (!matched) {
          if (active) {
            setOccurrence(null)
            setMedication(null)
            setLoadFailure('NOT_FOUND')
          }
          return
        }
        const detail = await services.getOccurrenceMedication(occurrenceId, controller.signal)
        if (!isMatchingMedication(matched, detail.data)) {
          if (active) setLoadFailure('NOT_FOUND')
          return
        }
        if (active) {
          setOccurrence(matched)
          setMedication(detail.data)
        }
      } catch (error) {
        if (!controller.signal.aborted && active) {
          const failure =
            isOccurrenceNotFoundError(error) || isOccurrenceMedicationNotFoundError(error)
              ? 'NOT_FOUND'
              : classifyLoadFailure(error)
          setOccurrence(null)
          setMedication(null)
          setLoadFailure(failure)
        }
      } finally {
        if (active) setIsLoading(false)
      }
    }

    void load()
    return () => {
      active = false
      controller.abort()
    }
  }, [date, occurrenceId, reloadVersion, services, validRoute])

  const scheduledAt = occurrence ? Date.parse(occurrence.scheduled_at) : Number.NaN
  const isBeforeScheduledTime = Number.isFinite(scheduledAt) && currentTime < scheduledAt
  useEffect(() => {
    if (!Number.isFinite(scheduledAt) || scheduledAt <= Date.now()) return undefined
    const timeout = window.setTimeout(
      () => setCurrentTime(Date.now()),
      Math.min(scheduledAt - Date.now() + 50, MAX_TIMEOUT_MS),
    )
    return () => window.clearTimeout(timeout)
  }, [currentTime, scheduledAt])

  const loadedOccurrenceId = occurrence?.occurrence_id
  const loadedMedicationOccurrenceId = medication?.occurrence_id
  useEffect(() => {
    if (loadedOccurrenceId && loadedMedicationOccurrenceId) {
      headingRef.current?.focus()
    }
  }, [loadedMedicationOccurrenceId, loadedOccurrenceId])

  const submitCheckin = async (status: MedicationCheckinUserStatus) => {
    if (!occurrence || isSaving || occurrence.status === 'CANCELLED') return
    if (Date.now() < Date.parse(occurrence.scheduled_at)) {
      setCurrentTime(Date.now())
      setMutationMessage(`${formatKstTime(occurrence.scheduled_at)}부터 복약 기록을 남길 수 있어요.`)
      return
    }
    const requestPayload: PutMedicationCheckinInput = {
      status,
      expectedRevision: occurrence.checkin?.revision ?? 0,
    }
    const attempt = resolveLogicalMutationAttempt(
      checkinAttemptRef.current,
      'CHECKIN_PUT',
      occurrence.occurrence_id,
      requestPayload,
      requestPayload.expectedRevision,
      services.createCheckinIdempotencyKey,
    )
    checkinAttemptRef.current = attempt

    setIsSaving(true)
    setMutationMessage('')
    try {
      const response = await services.putMedicationCheckin(
        attempt.targetId,
        attempt.requestPayload,
        attempt.idempotencyKey,
      )
      checkinAttemptRef.current = null
      setOccurrence({
        ...occurrence,
        status: 'CLOSED',
        checkin: response.data,
      })
      setMutationMessage('복약 기록을 저장했어요.')
    } catch (error) {
      if (isCheckinBeforeScheduledAtError(error)) {
        checkinAttemptRef.current = null
        setCurrentTime(Date.now())
        setMutationMessage(`${formatKstTime(occurrence.scheduled_at)}부터 복약 기록을 남길 수 있어요.`)
      } else if (isCheckinRevisionConflictError(error)) {
        checkinAttemptRef.current = null
        await reload()
        setMutationMessage('기록이 다른 곳에서 변경됐어요. 최신 상태를 확인한 뒤 다시 선택해 주세요.')
      } else if (isOccurrenceNotFoundError(error)) {
        checkinAttemptRef.current = null
        setLoadFailure('NOT_FOUND')
        setOccurrence(null)
        setMedication(null)
      } else if (isCheckinValidationError(error)) {
        setMutationMessage('선택한 복약 기록을 저장할 수 없어요. 상태를 확인해 주세요.')
      } else if (isCheckinConflictError(error)) {
        checkinAttemptRef.current = null
        await reload()
        setMutationMessage('이 일정의 상태가 변경됐어요. 최신 상태를 확인해 주세요.')
      } else if (error instanceof ApiError && error.status === 401) {
        checkinAttemptRef.current = null
        setLoadFailure('AUTH')
        setOccurrence(null)
        setMedication(null)
      } else if (error instanceof ApiError && error.status >= 500) {
        setMutationMessage('기록을 저장하지 못했어요. 잠시 후 다시 시도해 주세요.')
      } else {
        setMutationMessage('연결을 확인한 뒤 다시 시도해 주세요.')
      }
    } finally {
      setIsSaving(false)
    }
  }

  const backRoute = isValidLocalDate(date)
    ? `/schedule?date=${encodeURIComponent(date)}`
    : '/schedule'
  const goToLogin = () => {
    clearAuthenticatedSession()
    navigate('/login', { replace: true })
  }

  return (
    <div className="mvp-page schedule-page schedule-page--record">
      <NavigationShell onBack={() => navigate(backRoute)}>
        <main className="app-scroll schedule-page__content schedule-record">
          <header className="schedule-page__intro">
            <p>{isValidLocalDate(date) ? formatLocalDate(date) : '복약 기록'}</p>
            <h1 ref={headingRef} tabIndex={-1}>복약 기록</h1>
          </header>

          {isLoading && (
            <Card className="schedule-state-card"><div role="status">복약 기록을 불러오는 중입니다.</div></Card>
          )}
          {!isLoading && loadFailure && (() => {
            const copy = failureCopy(loadFailure)
            const canRetry = loadFailure === 'NETWORK' || loadFailure === 'SERVER'
            return (
              <StatusCard
                title={copy.title}
                body={copy.body}
                isAlert
                action={loadFailure === 'AUTH'
                  ? '로그인하기'
                  : canRetry
                    ? '다시 시도'
                    : '일정으로 돌아가기'}
                onAction={loadFailure === 'AUTH'
                  ? goToLogin
                  : canRetry
                    ? () => void reload()
                    : () => navigate(backRoute)}
              />
            )
          })()}

          {!isLoading && occurrence && medication && (
            <>
              <Card className="schedule-record__card">
                <span className="schedule-record__time">{formatKstTime(occurrence.scheduled_at)}</span>
                <h2>{medication.medication_name}</h2>
                <p>{medicationDescription(medication)}</p>
                <div className="schedule-record__status" role="status">
                  {checkinStatusCopy(occurrence)}
                </div>
              </Card>

              {mutationMessage && (
                <p className="schedule-record__message" role="alert" aria-live="assertive">
                  {mutationMessage}
                </p>
              )}

              {import.meta.env.DEV && occurrence.status !== 'CANCELLED' && occurrence.checkin?.status === 'NOT_TAKEN' && (
                <Button fullWidth disabled={isSaving} onClick={() => navigate(`/dev/track-c/occurrences/${occurrence.occurrence_id}?date=${encodeURIComponent(date ?? '')}`)}>
                  이유와 도움 찾기
                </Button>
              )}
              {occurrence.status !== 'CANCELLED' && (
                <section className="schedule-record__actions" aria-labelledby="checkin-question">
                  <h2 id="checkin-question">이 약을 복용했나요?</h2>
                  <p>{isBeforeScheduledTime
                    ? `${formatKstTime(occurrence.scheduled_at)}부터 복약 기록을 남길 수 있어요.`
                    : '현재 상태를 확인하고 직접 선택해 주세요.'}</p>
                  <Button fullWidth disabled={isSaving || isBeforeScheduledTime} onClick={() => void submitCheckin('TAKEN')}>
                    {isSaving ? '저장 중…' : '복용했어요'}
                  </Button>
                  <Button fullWidth variant="secondary" disabled={isSaving || isBeforeScheduledTime} onClick={() => void submitCheckin('NOT_TAKEN')}>
                    복용하지 않았어요
                  </Button>
                </section>
              )}
            </>
          )}
        </main>
      </NavigationShell>
    </div>
  )
}
