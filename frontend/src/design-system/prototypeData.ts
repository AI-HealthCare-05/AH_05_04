import type { MedicationFieldKey } from './prototypeScenarios'

export type MedicationData = Record<MedicationFieldKey, string>
export type PrototypeData = { personName: string; medication: MedicationData }

export const emptyPrototypeData: PrototypeData = {
  personName: '',
  medication: { name: '', dose: '', frequency: '', duration: '', timing: '' },
}

export const medicationFieldLabels: Record<MedicationFieldKey, string> = {
  name: '약 이름', dose: '1회 복용량', frequency: '하루 횟수', duration: '복용 기간', timing: '복용 시점',
}

export const medicationFieldKeys = Object.keys(medicationFieldLabels) as MedicationFieldKey[]
