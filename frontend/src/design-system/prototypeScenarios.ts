export type FieldStatus = 'confirmed' | 'recommended' | 'required'
export type DoseStatus = 'scheduled' | 'taken' | 'skipped'
export type MedicationFieldKey = 'name' | 'dose' | 'frequency' | 'duration' | 'timing'

export type PrototypeScenario = {
  id: 'empty' | 'normal' | 'low-confidence' | 'error' | 'taken' | 'skipped'
  label: string
  description: string
  hasGuide: boolean
  hasRecords: boolean
  documentQuality: 'unreadable' | 'review' | 'confirmed'
  doseStatus: DoseStatus
  fieldStatuses: Record<MedicationFieldKey, FieldStatus>
}

const confirmedFields: Record<MedicationFieldKey, FieldStatus> = {
  name: 'confirmed', dose: 'confirmed', frequency: 'confirmed', duration: 'confirmed', timing: 'confirmed',
}

export const prototypeScenarios: PrototypeScenario[] = [
  {
    id: 'empty', label: '빈 상태', description: '등록된 처방·가이드·복약 기록이 없는 상태',
    hasGuide: false, hasRecords: false, documentQuality: 'unreadable', doseStatus: 'scheduled',
    fieldStatuses: { name: 'required', dose: 'required', frequency: 'required', duration: 'required', timing: 'required' },
  },
  {
    id: 'normal', label: '정상', description: '외부 데이터를 확인하고 일정·가이드를 생성한 상태',
    hasGuide: true, hasRecords: true, documentQuality: 'confirmed', doseStatus: 'scheduled', fieldStatuses: confirmedFields,
  },
  {
    id: 'low-confidence', label: 'OCR 저신뢰도', description: '일부 필드를 원본과 다시 대조해야 하는 상태',
    hasGuide: false, hasRecords: false, documentQuality: 'review', doseStatus: 'scheduled',
    fieldStatuses: { ...confirmedFields, dose: 'required', duration: 'recommended' },
  },
  {
    id: 'error', label: 'OCR 오류', description: '문서 품질이 임계값에 미달해 재촬영이 필요한 상태',
    hasGuide: false, hasRecords: false, documentQuality: 'unreadable', doseStatus: 'scheduled',
    fieldStatuses: { name: 'required', dose: 'required', frequency: 'required', duration: 'required', timing: 'required' },
  },
  {
    id: 'taken', label: '복용 완료', description: '외부 처방 데이터의 이번 회차 복용을 완료한 상태',
    hasGuide: true, hasRecords: true, documentQuality: 'confirmed', doseStatus: 'taken', fieldStatuses: confirmedFields,
  },
  {
    id: 'skipped', label: '미복용', description: '외부 처방 데이터의 이번 회차를 복용하지 못한 상태',
    hasGuide: true, hasRecords: true, documentQuality: 'confirmed', doseStatus: 'skipped', fieldStatuses: confirmedFields,
  },
]

export function getPrototypeScenario(id: string | null) {
  const aliases: Record<string, PrototypeScenario['id']> = {
    'new-user': 'empty',
    'ocr-review': 'low-confidence',
    'active-care': 'normal',
    caregiver: 'normal',
  }
  const resolvedId = id && aliases[id] ? aliases[id] : id
  return prototypeScenarios.find((scenario) => scenario.id === resolvedId) ?? prototypeScenarios[0]
}
