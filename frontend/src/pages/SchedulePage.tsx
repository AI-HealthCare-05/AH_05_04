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
} from '../api/medicationSchedules'
import {
  createCheckinIdempotencyKey,
  isCheckinConflictError,
  isCheckinRevisionConflictError,
  isCheckinValidationError,
  isOccurrenceNotFoundError,
  putMedicationCheckin,
  type MedicationCheckinUserStatus,
  type PutMedicationCheckinInput,
} from '../api/medicationCheckins'
import {
  getLatestPrescription,
  type Medication,
  type PrescriptionResponse,
} from '../api/prescriptions'
import { Button, Card, MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import '../design-system/prototype.css'
import './MvpPages.css'
import './SchedulePage.css'

const KST_TIME_ZONE = 'Asia/Seoul'
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const LOCAL_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/

export type SchedulePageServices = {
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

type LogicalMutationAttempt<TPayload = unknown> = {
  operation: LogicalMutationOperation
  targetId: string
  requestPayload: TPayload
  expectedRevision: number
  idempotencyKey: string
}

function resolveLogicalMutationAttempt<TPayload>(
  current: LogicalMutationAttempt | null,
  operation: LogicalMutationOperation,
  targetId: string,
  requestPayload: TPayload,
  expectedRevision: number,
  createIdempotencyKey: () => string,
): LogicalMutationAttempt<TPayload> {
  if (
    current?.operation === operation &&
    current.targetId === targetId &&
    current.expectedRevision === expectedRevision &&
    JSON.stringify(current.requestPayload) === JSON.stringify(requestPayload)
  ) {
    return {
      operation,
      targetId,
      requestPayload,
      expectedRevision,
      idempotencyKey: current.idempotencyKey,
    }
  }

  return {
    operation,
    targetId,
    requestPayload,
    expectedRevision,
    idempotencyKey: createIdempotencyKey(),
  }
}

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
}: {
  title: string
  body: string
  action?: string
  onAction?: () => void
}) {
  return (
    <Card className="schedule-state-card">
      <h2>{title}</h2>
      <p>{body}</p>
      {action && onAction && (
        <Button fullWidth onClick={onAction}>{action}</Button>
      )}
    </Card>
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

function ScheduleEditor({
  item,
  medication,
  selectedDate,
  services,
  onSaved,
  onConflict,
  onClose,
}: {
  item: MedicationScheduleItem
  medication: Medication
  selectedDate: string
  services: SchedulePageServices
  onSaved: () => Promise<void>
  onConflict: () => Promise<void>
  onClose: () => void
}) {
  const frequencyPerDay =
    Number.isInteger(medication.frequency_per_day) &&
    (medication.frequency_per_day ?? 0) > 0
      ? medication.frequency_per_day
      : null
  const [startDate, setStartDate] = useState('')
  const [endMode, setEndMode] = useState<'DATE' | 'OPEN_ENDED'>('DATE')
  const [endDate, setEndDate] = useState('')
  const [times, setTimes] = useState(() =>
    Array.from({ length: frequencyPerDay ?? 1 }, () => ''),
  )
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [isConfirmingCancel, setIsConfirmingCancel] = useState(false)
  const scheduleMutationAttemptRef = useRef<LogicalMutationAttempt | null>(null)

  const changeScheduleInput = (change: () => void) => {
    scheduleMutationAttemptRef.current = null
    change()
  }

  const validate = (): string | null => {
    if (!isValidLocalDate(startDate)) return '복용 시작일을 확인해 주세요.'
    if (endMode === 'DATE' && !isValidLocalDate(endDate)) {
      return '종료일을 확인해 주세요.'
    }
    if (endMode === 'DATE' && endDate < startDate) {
      return '종료일은 시작일보다 빠를 수 없어요.'
    }
    if (times.some((time) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))) {
      return '모든 복용 시간을 확인해 주세요.'
    }
    if (new Set(times).size !== times.length) return '같은 시간은 한 번만 입력해 주세요.'
    if (frequencyPerDay !== null && times.length !== frequencyPerDay) {
      return `처방의 하루 복용 횟수(${frequencyPerDay}회)와 복용 시간 ${times.length}개가 일치하지 않아요.`
    }
    return null
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (isSaving) return
    const validationMessage = validate()
    if (validationMessage) {
      setMessage(validationMessage)
      return
    }

    const requestPayload: PutMedicationScheduleInput =
      endMode === 'DATE'
        ? {
            startLocalDate: startDate,
            endMode,
            endLocalDate: endDate,
            localTimes: times,
            expectedRevision: item.revision ?? 0,
          }
        : {
            startLocalDate: startDate,
            endMode,
            localTimes: times,
            expectedRevision: item.revision ?? 0,
          }
    const attempt = resolveLogicalMutationAttempt(
      scheduleMutationAttemptRef.current,
      'SCHEDULE_PUT',
      item.prescription_version_medication_id,
      requestPayload,
      requestPayload.expectedRevision,
      services.createScheduleIdempotencyKey,
    )
    scheduleMutationAttemptRef.current = attempt

    setIsSaving(true)
    setMessage('')
    try {
      await services.putMedicationSchedule(
        attempt.targetId,
        attempt.requestPayload,
        attempt.idempotencyKey,
      )
      scheduleMutationAttemptRef.current = null
      await onSaved()
      onClose()
    } catch (error) {
      if (isScheduleRevisionConflictError(error)) {
        scheduleMutationAttemptRef.current = null
        await onConflict()
        setMessage('일정이 다른 곳에서 변경됐어요. 최신 상태를 불러왔으니 내용을 다시 확인해 주세요.')
      } else if (
        isPrescriptionVersionConflictError(error) ||
        isPrescriptionMedicationNotFoundError(error)
      ) {
        scheduleMutationAttemptRef.current = null
        await onConflict()
        setMessage('현재 처방 내용이 변경됐어요. 최신 일정을 확인해 주세요.')
      } else if (error instanceof ApiError && error.status === 401) {
        setMessage('로그인 정보를 다시 확인해 주세요.')
      } else if (error instanceof ApiError && error.status === 422) {
        setMessage(
          frequencyPerDay === null
            ? '입력한 날짜와 시간을 확인해 주세요.'
            : `처방의 하루 복용 횟수(${frequencyPerDay}회)와 복용 시간 개수를 확인해 주세요.`,
        )
      } else if (error instanceof ApiError && error.status >= 500) {
        setMessage('일정을 저장하지 못했어요. 입력값을 유지한 채 다시 시도해 주세요.')
      } else {
        setMessage('연결을 확인한 뒤 다시 시도해 주세요. 입력한 내용은 그대로 유지돼요.')
      }
    } finally {
      setIsSaving(false)
    }
  }

  const handleCancel = async () => {
    if (!isConfirmingCancel) {
      setIsConfirmingCancel(true)
      setMessage('일정을 중지하면 앞으로의 복약 시간이 생성되지 않아요. 한 번 더 눌러 확인해 주세요.')
      return
    }
    if (item.revision === null || isSaving) return

    const requestPayload = {
      status: 'CANCELLED' as const,
      expectedRevision: item.revision,
    }
    const attempt = resolveLogicalMutationAttempt(
      scheduleMutationAttemptRef.current,
      'SCHEDULE_CANCEL',
      item.prescription_version_medication_id,
      requestPayload,
      requestPayload.expectedRevision,
      services.createScheduleIdempotencyKey,
    )
    scheduleMutationAttemptRef.current = attempt

    setIsSaving(true)
    setMessage('')
    try {
      await services.cancelMedicationSchedule(
        attempt.targetId,
        attempt.requestPayload.expectedRevision,
        attempt.idempotencyKey,
      )
      scheduleMutationAttemptRef.current = null
      await onSaved()
      onClose()
    } catch (error) {
      if (isScheduleRevisionConflictError(error)) {
        scheduleMutationAttemptRef.current = null
        await onConflict()
        setMessage('일정이 다른 곳에서 변경됐어요. 최신 상태를 확인해 주세요.')
      } else if (
        isPrescriptionVersionConflictError(error) ||
        isPrescriptionMedicationNotFoundError(error)
      ) {
        scheduleMutationAttemptRef.current = null
        await onConflict()
        setMessage('현재 처방 내용이 변경됐어요. 최신 일정을 확인해 주세요.')
      } else {
        setMessage('일정을 중지하지 못했어요. 잠시 후 다시 시도해 주세요.')
      }
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Card className="schedule-editor">
      <div className="schedule-editor__heading">
        <div>
          <span>{medicationDescription(medication)}</span>
          <h3>복용할 날짜와 시간을 확인해 주세요</h3>
        </div>
        <button type="button" onClick={onClose} aria-label="일정 입력 닫기">×</button>
      </div>
      <p className="schedule-editor__notice">
        Dosey는 복용 시간을 추정하거나 추천하지 않아요. 정확한 날짜와 시간을 직접 확인해 주세요.
      </p>
      <form onSubmit={handleSubmit}>
        <label>
          <span>복용 시작일</span>
          <input
            type="date"
            value={startDate}
            placeholder={selectedDate}
            onChange={(event) => changeScheduleInput(() => setStartDate(event.target.value))}
            required
          />
        </label>
        <fieldset>
          <legend>복용 종료</legend>
          <label>
            <input
              type="radio"
              name={`end-mode-${item.prescription_version_medication_id}`}
              value="DATE"
              checked={endMode === 'DATE'}
              onChange={() => changeScheduleInput(() => setEndMode('DATE'))}
            />
            종료일 지정
          </label>
          <label>
            <input
              type="radio"
              name={`end-mode-${item.prescription_version_medication_id}`}
              value="OPEN_ENDED"
              checked={endMode === 'OPEN_ENDED'}
              onChange={() => changeScheduleInput(() => setEndMode('OPEN_ENDED'))}
            />
            계속 복용
          </label>
        </fieldset>
        {endMode === 'DATE' && (
          <label>
            <span>복용 종료일</span>
            <input
              type="date"
              value={endDate}
              min={startDate || undefined}
              onChange={(event) => changeScheduleInput(() => setEndDate(event.target.value))}
              required
            />
          </label>
        )}
        <div className="schedule-editor__times">
          <span>복용 시간</span>
          {times.map((time, index) => (
            <div className="schedule-editor__time-row" key={index}>
              <label>
                <span className="sr-only">{index + 1}번째 복용 시간</span>
                <input
                  type="time"
                  value={time}
                  onChange={(event) => {
                    const next = [...times]
                    next[index] = event.target.value
                    changeScheduleInput(() => setTimes(next))
                  }}
                  required
                />
              </label>
              {times.length > 1 && (
                <button
                  type="button"
                  onClick={() => changeScheduleInput(() => setTimes(
                    times.filter((_, candidate) => candidate !== index),
                  ))}
                  aria-label={`${index + 1}번째 복용 시간 삭제`}
                >
                  삭제
                </button>
              )}
            </div>
          ))}
          <button
            type="button"
            className="schedule-editor__add-time"
            onClick={() => changeScheduleInput(() => setTimes([...times, '']))}
          >
            + 복용 시간 추가
          </button>
          {frequencyPerDay !== null && (
            <small>하루 {frequencyPerDay}회 처방이에요. 복용 시간을 {frequencyPerDay}개 입력해 주세요.</small>
          )}
        </div>
        {message && <p className="schedule-editor__message" role="alert">{message}</p>}
        <Button fullWidth type="submit" disabled={isSaving}>
          {isSaving ? '저장 중…' : '복약 일정 저장하기'}
        </Button>
        {item.schedule_id && item.schedule_item_status === 'READY' && (
          <Button
            fullWidth
            variant="ghost"
            className={isConfirmingCancel ? 'schedule-editor__cancel-confirm' : ''}
            disabled={isSaving}
            onClick={() => void handleCancel()}
          >
            {isConfirmingCancel ? '사용 중지 확인' : '이 일정 사용 중지'}
          </Button>
        )}
      </form>
    </Card>
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

export function SchedulePage({
  services = defaultServices,
}: {
  services?: SchedulePageServices
}) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedDate = searchParams.get('date')
  const selectedDate = isValidLocalDate(requestedDate) ? requestedDate : kstToday()
  const [day, setDay] = useState<MedicationDayResponse['data'] | null>(null)
  const [medications, setMedications] = useState<Record<string, MedicationDetail | null>>({})
  const [scheduleMedications, setScheduleMedications] = useState<Record<string, Medication>>({})
  const [isScheduleIdentityUnavailable, setIsScheduleIdentityUnavailable] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null)
  const [reloadVersion, setReloadVersion] = useState(0)
  const [editingMedicationId, setEditingMedicationId] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setIsLoading(true)
    setReloadVersion((version) => version + 1)
  }, [])

  useEffect(() => {
    if (requestedDate !== selectedDate) {
      setSearchParams({ date: selectedDate }, { replace: true })
    }
  }, [requestedDate, selectedDate, setSearchParams])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setIsLoading(true)
    setLoadFailure(null)

    async function load() {
      try {
        const response = await services.getMedicationDay(selectedDate, controller.signal)
        if (!active) return
        setDay(response.data)

        const scheduleIdentityPromise = response.data.schedule_items.length === 0
          ? Promise.resolve<Record<string, Medication> | null>({})
          : services.getLatestPrescription(controller.signal)
              .then((prescription) =>
                scheduleMedicationMap(response.data.schedule_items, prescription.data),
              )
              .catch((error: unknown) => {
                if (controller.signal.aborted) throw error
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
          setScheduleMedications(scheduleIdentity ?? {})
          setIsScheduleIdentityUnavailable(scheduleIdentity === null)
          setMedications(Object.fromEntries(occurrenceEntries))
        }
      } catch (error) {
        if (!controller.signal.aborted && active) {
          setDay(null)
          setMedications({})
          setScheduleMedications({})
          setIsScheduleIdentityUnavailable(false)
          setLoadFailure(classifyLoadFailure(error))
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
  const state = day ? statusContent(day.schedule_status) : null

  const openRelevantEditor = () => {
    if (isScheduleIdentityUnavailable) return
    const preferred = day?.schedule_items.find(
      (item) => item.schedule_item_status !== 'READY',
    ) ?? day?.schedule_items[0]
    if (preferred) setEditingMedicationId(preferred.prescription_version_medication_id)
  }

  const goToLogin = () => {
    clearAuthenticatedSession()
    navigate('/login', { replace: true })
  }

  return (
    <div className="mvp-page schedule-page">
      <NavigationShell>
        <main className="app-scroll schedule-page__content">
          <header className="schedule-page__intro">
            <label htmlFor="schedule-date">복약 날짜</label>
            <input
              id="schedule-date"
              type="date"
              value={selectedDate}
              onChange={(event) => setSearchParams({ date: event.target.value })}
            />
            <p>{formatLocalDate(selectedDate)}</p>
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
                action={loadFailure === 'AUTH' ? '로그인하기' : '다시 시도'}
                onAction={loadFailure === 'AUTH' ? goToLogin : () => void reload()}
              />
            )
          })()}

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

          {!isLoading && day && day.schedule_status === 'READY' && (
            <p className="schedule-page__saved" role="status">
              일정이 저장되어 있어요. 선택한 날짜의 복약 시간을 확인해 주세요.
            </p>
          )}

          {!isLoading && day && day.schedule_status !== 'NO_ACTIVE_PRESCRIPTION' && (
            <section className="schedule-page__occurrences" aria-labelledby="occurrence-list-title">
              <div className="schedule-page__section-heading">
                <h2 id="occurrence-list-title">선택한 날짜의 복약</h2>
                <span>{occurrences.length}건</span>
              </div>
              {occurrences.length === 0 ? (
                <Card className="schedule-page__empty">
                  <p>이 날짜에 표시할 복약 일정이 없어요.</p>
                </Card>
              ) : occurrences.map((occurrence) => {
                const medication = medications[occurrence.occurrence_id]
                const route = `/schedule/occurrences/${occurrence.occurrence_id}?date=${encodeURIComponent(occurrence.scheduled_local_date)}`
                return (
                  <button
                    className="schedule-occurrence-card"
                    type="button"
                    key={occurrence.occurrence_id}
                    onClick={() => navigate(route)}
                    aria-label={`${formatKstTime(occurrence.scheduled_at)} ${
                      medication ? medication.medication_name : '약 정보 확인 필요'
                    } ${occurrenceStateLabel(occurrence)}`}
                  >
                    <span className="schedule-occurrence-card__period">
                      {formatKstTime(occurrence.scheduled_at)}
                    </span>
                    <strong>{medication ? medicationDescription(medication) : '약 정보를 확인할 수 없어요'}</strong>
                    <span className="schedule-occurrence-card__state">{occurrenceStateLabel(occurrence)}</span>
                    <span className="schedule-occurrence-card__chevron" aria-hidden="true">›</span>
                  </button>
                )
              })}
            </section>
          )}

          {!isLoading && day && day.schedule_items.length > 0 && (
            <section className="schedule-page__settings" aria-labelledby="schedule-settings-title">
              <div className="schedule-page__section-heading">
                <h2 id="schedule-settings-title">복약 일정 설정</h2>
              </div>
              {isScheduleIdentityUnavailable ? (
                <Card className="schedule-page__empty">
                  <p role="alert">약 정보를 확인할 수 없어 일정을 설정할 수 없습니다. 처방 정보를 다시 확인해 주세요.</p>
                </Card>
              ) : day.schedule_items.map((item) => {
                const medication = scheduleMedications[
                  item.prescription_version_medication_id
                ]
                if (!medication) return null
                return editingMedicationId === item.prescription_version_medication_id ? (
                  <ScheduleEditor
                    key={item.prescription_version_medication_id}
                    item={item}
                    medication={medication}
                    selectedDate={selectedDate}
                    services={services}
                    onSaved={reload}
                    onConflict={reload}
                    onClose={() => setEditingMedicationId(null)}
                  />
                ) : (
                  <button
                    className="schedule-setting-row"
                    type="button"
                    key={item.prescription_version_medication_id}
                    onClick={() => setEditingMedicationId(item.prescription_version_medication_id)}
                  >
                    <span>
                      <strong>{medicationDescription(medication)}</strong>
                      <small>{
                        item.schedule_item_status === 'READY'
                          ? '설정됨'
                          : item.schedule_item_status === 'INACTIVE'
                            ? '사용 중지됨'
                            : '설정 필요'
                      }</small>
                    </span>
                    <span>{item.schedule_item_status === 'READY' ? '설정·수정' : '설정'} ›</span>
                  </button>
                )
              })}
            </section>
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
  const checkinAttemptRef = useRef<LogicalMutationAttempt | null>(null)

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

  const submitCheckin = async (status: MedicationCheckinUserStatus) => {
    if (!occurrence || isSaving || occurrence.status === 'CANCELLED') return
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
      if (isCheckinRevisionConflictError(error)) {
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
    <div className="mvp-page schedule-page">
      <NavigationShell onBack={() => navigate(backRoute)}>
        <main className="app-scroll schedule-page__content schedule-record">
          <header className="schedule-page__intro">
            <p>{isValidLocalDate(date) ? formatLocalDate(date) : '복약 기록'}</p>
            <h1>복약 기록</h1>
          </header>

          {isLoading && (
            <Card className="schedule-state-card"><div role="status">복약 기록을 불러오는 중입니다.</div></Card>
          )}
          {!isLoading && loadFailure && (() => {
            const copy = failureCopy(loadFailure)
            return (
              <StatusCard
                title={copy.title}
                body={copy.body}
                action={loadFailure === 'AUTH' ? '로그인하기' : '일정으로 돌아가기'}
                onAction={loadFailure === 'AUTH' ? goToLogin : () => navigate(backRoute)}
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

              {occurrence.status !== 'CANCELLED' && (
                <section className="schedule-record__actions" aria-labelledby="checkin-question">
                  <h2 id="checkin-question">이 약을 복용했나요?</h2>
                  <p>현재 상태를 확인하고 직접 선택해 주세요.</p>
                  <Button fullWidth disabled={isSaving} onClick={() => void submitCheckin('TAKEN')}>
                    {isSaving ? '저장 중…' : '복용했어요'}
                  </Button>
                  <Button fullWidth variant="secondary" disabled={isSaving} onClick={() => void submitCheckin('NOT_TAKEN')}>
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
