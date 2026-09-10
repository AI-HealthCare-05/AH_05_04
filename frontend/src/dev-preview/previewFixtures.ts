import type {
  ChatMessageData,
  ChatMessageListResponse,
  ChatSessionResponse,
  SendChatMessageResponse,
} from '../api/chat'
import { ApiError } from '../api/client'
import type { GuideResponse } from '../api/guides'
import type {
  ExtractedField,
  Medication,
  OcrJobResponse,
  PrescriptionResponse,
} from '../api/prescriptions'
import type {
  ChatPageServices,
  ChatPreviewState,
} from '../pages/ChatPage'
import type { GuidePageServices } from '../pages/GuidePage'
import type {
  PrescriptionReviewPreviewState,
  PrescriptionReviewServices,
} from '../pages/PrescriptionReviewPage'
import { previewIds } from './syntheticIds'

export type PreviewScreen = 'prescription-review' | 'guide' | 'chat'

export const previewScenarioIds = {
  'prescription-review': [
    'normal',
    'missing-required',
    'optional-empty',
    'missing-medication',
    'missing-required-edit',
    'review-in-progress',
    'completed-before-ack',
    'completed',
    'validation-error',
    'manual-add-form',
    'manual-add-validation',
    'manual-add-success',
  ],
  guide: ['completed', 'structured', 'loading', 'empty', 'failed'],
  chat: [
    'no-prescription',
    'existing-messages',
    'input-ready',
    'generating',
    'long-answer',
    'error',
  ],
} as const

export const previewScreenLabels: Record<PreviewScreen, string> = {
  'prescription-review': 'Prescription Review / DOC-03',
  guide: 'Guide',
  chat: 'Chat',
}

const now = '2026-09-08T09:00:00Z'

type ReviewScenario = typeof previewScenarioIds['prescription-review'][number]
type GuideScenario = typeof previewScenarioIds.guide[number]
type ChatScenario = typeof previewScenarioIds.chat[number]

function field(
  fieldType: string,
  medicationIndex: number,
  value: string | null,
  confirmed: boolean,
): ExtractedField {
  return {
    field_id: `${fieldType}-${medicationIndex}`,
    field_type: fieldType,
    medication_index: medicationIndex,
    raw_value: value,
    normalized_value: fieldType === 'PRESCRIBED_DATE' ? value : null,
    normalization_version: value ? 'preview-v1' : null,
    confirmed_value: confirmed ? value : null,
    confidence_score: value === null ? null : 0.99,
    confirmation_status: confirmed ? 'CONFIRMED' : 'UNCONFIRMED',
  }
}

function confirmedManualField(
  fieldType: string,
  medicationIndex: number,
  value: string,
): ExtractedField {
  return {
    field_id: `${fieldType}-${medicationIndex}`,
    field_type: fieldType,
    medication_index: medicationIndex,
    raw_value: null,
    normalized_value: null,
    normalization_version: 'manual-entry@1',
    confirmed_value: value,
    confidence_score: null,
    confirmation_status: 'CONFIRMED',
  }
}

function reviewFields(scenario: ReviewScenario): ExtractedField[] {
  if (scenario === 'missing-medication') {
    return [
      field('PRESCRIBED_DATE', 0, '2026-09-08', false),
      field('MEDICATION_NAME', 1, null, false),
      field('DOSE_VALUE', 1, '1', false),
      field('FREQUENCY_PER_DAY', 1, '3', false),
      field('DURATION_DAYS', 1, '7', false),
    ]
  }

  const confirmed =
    scenario === 'optional-empty' ||
    scenario === 'completed-before-ack' ||
    scenario === 'manual-add-success'
  const fields = [
    field('PRESCRIBED_DATE', 0, '2026-09-08', confirmed),
    field('MEDICATION_NAME', 1, '합성 처방약', confirmed),
    field('MEDICATION_STRENGTH', 1, '100mg', confirmed),
    field('DOSE_VALUE', 1, '1', confirmed),
    field('DOSE_UNIT', 1, '정', confirmed),
    field('FREQUENCY_PER_DAY', 1, '3', confirmed),
    field('TIMING', 1, '식후', confirmed),
    field('DURATION_DAYS', 1, '7', confirmed),
  ]

  if (scenario === 'manual-add-success') {
    return [
      ...fields,
      confirmedManualField('MEDICATION_NAME', 2, '수동 추가 약'),
      confirmedManualField('MEDICATION_STRENGTH', 2, '50mg'),
      confirmedManualField('DOSE_VALUE', 2, '1'),
      confirmedManualField('DOSE_UNIT', 2, '정'),
      confirmedManualField('FREQUENCY_PER_DAY', 2, '2'),
      confirmedManualField('TIMING', 2, '취침 전'),
      confirmedManualField('DURATION_DAYS', 2, '5'),
    ]
  }

  if (scenario === 'missing-required') {
    return fields.filter((candidate) => candidate.field_type !== 'DURATION_DAYS')
  }

  if (scenario === 'optional-empty') {
    return fields.map((candidate) =>
      ['MEDICATION_STRENGTH', 'DOSE_UNIT', 'TIMING'].includes(candidate.field_type)
        ? field(candidate.field_type, candidate.medication_index, null, false)
        : candidate,
    )
  }

  if (scenario === 'missing-required-edit') {
    return fields.map((candidate) =>
      candidate.field_type === 'DOSE_VALUE'
        ? field(candidate.field_type, candidate.medication_index, null, false)
        : candidate,
    )
  }

  if (scenario === 'validation-error') {
    return fields.map((candidate) =>
      candidate.field_type === 'FREQUENCY_PER_DAY'
        ? field(candidate.field_type, candidate.medication_index, '0', false)
        : candidate,
    )
  }

  return fields
}

function ocrResponse(
  scenario: ReviewScenario,
  fields: ExtractedField[],
): OcrJobResponse {
  return {
    data: {
      job_id: previewIds.ocrJob,
      document_id: previewIds.document,
      ocr_status: scenario === 'review-in-progress' ? 'PROCESSING' : 'COMPLETED',
      error_code: null,
      engine_name: 'SYNTHETIC_PREVIEW_OCR',
      model_version: 'preview-v1',
      prompt_version: null,
      created_at: now,
      completed_at: scenario === 'review-in-progress' ? null : now,
      fields,
    },
  }
}

function prescriptionResponseForScenario(
  scenario: ReviewScenario,
): PrescriptionResponse {
  const medications: Medication[] = [
    {
      prescription_version_medication_id:
        previewIds.prescriptionVersionMedication,
      medication_name: '합성 처방약',
      strength_text: '100mg',
      dose_value: 1,
      dose_unit: '정',
      frequency_per_day: 3,
      timing_text: '식후',
      duration_days: 7,
      display_order: 1,
    },
  ]
  if (scenario === 'manual-add-success') {
    medications.push({
      prescription_version_medication_id:
        previewIds.prescriptionVersionManualMedication,
      medication_name: '수동 추가 약',
      strength_text: '50mg',
      dose_value: 1,
      dose_unit: '정',
      frequency_per_day: 2,
      timing_text: '취침 전',
      duration_days: 5,
      display_order: 2,
    })
  }
  return {
    data: {
      prescription_id: previewIds.prescription,
      prescription_version_id: previewIds.prescriptionVersion,
      revision: 1,
      current: true,
      document_id: previewIds.document,
      prescribed_date: '2026-09-08',
      confirmed_at: now,
      medications,
    },
  } satisfies PrescriptionResponse
}

function guideResponse(
  status: GuideResponse['data']['generation_status'],
  content: string | null,
): GuideResponse {
  return {
    data: {
      guide_id: previewIds.guide,
      prescription_id: previewIds.prescription,
      prescription_version_id: previewIds.prescriptionVersion,
      generation_status: status,
      content,
      model_name: 'synthetic-preview-guide',
      prompt_version: 'synthetic-preview-v1',
      requested_at: now,
      completed_at: status === 'COMPLETED' ? now : null,
    },
  }
}

const structuredGuideContent = [
  '복약 가이드',
  '[1] 합성 처방약\n용량: 1 정\n복용 횟수: 하루 3회\n복용 시점: 식후\n복용 기간: 7일\n복약 안내: 처방에 안내된 복용 계획을 확인하고 지켜 주세요.',
  '공통 안내: 불명확한 내용은 의료진 또는 약사에게 확인해 주세요.\n안전 안내: 임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요.',
].join('\n\n')

export function createPrescriptionReviewPreview(scenario: ReviewScenario): {
  services: PrescriptionReviewServices
  state: PrescriptionReviewPreviewState
} {
  const fields = reviewFields(scenario)
  const services: PrescriptionReviewServices = {
    getOcrJob: async () => {
      if (scenario === 'completed') {
        throw new ApiError(
          409,
          '이미 확정된 처방입니다.',
          'PRESCRIPTION_ALREADY_CONFIRMED',
        )
      }
      return ocrResponse(scenario, fields)
    },
    getPrescriptionDocumentFile: async () =>
      new Blob(['SYNTHETIC PREVIEW DOCUMENT'], { type: 'text/plain' }),
    updateExtractedField: async (fieldId, confirmedValue) => {
      const current = fields.find((candidate) => candidate.field_id === fieldId)
      if (!current) {
        throw new ApiError(404, '합성 필드를 찾을 수 없습니다.', 'EXTRACTED_FIELD_NOT_FOUND')
      }
      return {
        data: {
          ...current,
          confirmed_value: confirmedValue,
          confirmation_status: 'CONFIRMED',
        },
      }
    },
    createManualMedication: async () => ocrResponse(scenario, fields),
    confirmPrescription: async () => prescriptionResponseForScenario(scenario),
    createGuide: async () => guideResponse('COMPLETED', structuredGuideContent),
  }

  return {
    services,
    state: {
      documentId: previewIds.document,
      jobId: previewIds.ocrJob,
      manualAddMode:
        scenario === 'manual-add-form'
          ? 'form'
          : scenario === 'manual-add-validation'
            ? 'validation'
            : undefined,
      unreviewedMedicationIndexes:
        scenario === 'manual-add-success' ? [2] : undefined,
    },
  }
}

export function createGuidePreview(scenario: GuideScenario): {
  services: GuidePageServices
  guideId: string
} {
  const response =
    scenario === 'structured'
      ? guideResponse('COMPLETED', structuredGuideContent)
      : scenario === 'completed'
        ? guideResponse(
            'COMPLETED',
            '합성 처방약은 확정된 처방 지시에 따라 복용하세요.',
          )
        : scenario === 'empty'
          ? guideResponse('COMPLETED', null)
          : scenario === 'failed'
            ? guideResponse('FAILED', null)
            : guideResponse('GENERATING', null)

  const services: GuidePageServices = {
    getGuide: () =>
      scenario === 'loading'
        ? new Promise<GuideResponse>(() => undefined)
        : Promise.resolve(response),
    getGuideForPrescription: async () => response,
    getLatestPrescription: async () => prescriptionResponseForScenario('normal'),
  }

  return { services, guideId: previewIds.guide }
}

function chatMessage(
  id: string,
  role: ChatMessageData['role'],
  content: string | null,
  generationStatus: ChatMessageData['generation_status'],
): ChatMessageData {
  return {
    message_id: id,
    role,
    content,
    generation_status: generationStatus,
    created_at: now,
  }
}

function chatMessages(scenario: ChatScenario): ChatMessageData[] {
  if (scenario === 'existing-messages' || scenario === 'generating') {
    return [
      chatMessage(
        '77777777-7777-4777-8777-777777777777',
        'USER',
        '아침 약은 언제 먹나요?',
        'NOT_APPLICABLE',
      ),
      chatMessage(
        '88888888-8888-4888-8888-888888888888',
        'ASSISTANT',
        '확정된 처방에 적힌 복용 시점을 먼저 확인해 주세요.',
        'COMPLETED',
      ),
    ]
  }

  if (scenario === 'long-answer') {
    return [
      chatMessage(
        '99999999-9999-4999-8999-999999999999',
        'USER',
        '복용할 때 확인할 점을 알려 주세요.',
        'NOT_APPLICABLE',
      ),
      chatMessage(
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'ASSISTANT',
        '확정된 처방에 표시된 약 이름, 1회 복용량, 하루 복용 횟수, 복용 시점과 복용 기간을 차례로 확인해 주세요. 내용이 불명확하거나 실제 처방전과 다르면 임의로 복용 방법을 바꾸지 말고 의료진 또는 약사에게 확인해 주세요. 불편한 증상이 있거나 안전이 걱정되는 경우에도 전문가에게 상담해 주세요.',
        'COMPLETED',
      ),
    ]
  }

  return []
}

export function createChatPreview(scenario: ChatScenario): {
  services: ChatPageServices
  state: ChatPreviewState
} {
  const sessionResponse: ChatSessionResponse = {
    data: {
      session_id: previewIds.session,
      prescription_id: previewIds.prescription,
      prescription_version_id: previewIds.prescriptionVersion,
      session_status: 'ACTIVE',
      created_at: now,
    },
  }
  const messageListResponse: ChatMessageListResponse = {
    data: {
      session_id: previewIds.session,
      messages: chatMessages(scenario),
    },
  }
  const sendResponse: SendChatMessageResponse = {
    data: {
      user_message_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      assistant_message_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      session_id: previewIds.session,
      generation_status: 'COMPLETED',
      content: '입력한 질문에 대한 합성 Preview 답변입니다.',
      model_name: 'synthetic-preview-chat',
      prompt_version: 'synthetic-preview-v1',
      created_at: now,
      completed_at: now,
    },
  }
  const services: ChatPageServices = {
    createChatSession: async () => sessionResponse,
    getChatSessionForPrescription: async () => {
      if (scenario === 'error') {
        throw new TypeError('synthetic preview network failure')
      }
      return sessionResponse
    },
    getChatMessages: async () => messageListResponse,
    sendChatMessage: async () => sendResponse,
  }

  return {
    services,
    state: {
      prescriptionId:
        scenario === 'no-prescription' ? '' : previewIds.prescription,
      draft: scenario === 'input-ready' ? '아침 약은 언제 먹나요?' : '',
      isSending: scenario === 'generating',
    },
  }
}

export function isPreviewScreen(value: string | null): value is PreviewScreen {
  return value !== null && Object.hasOwn(previewScenarioIds, value)
}

export function resolvePreviewScenario(
  screen: PreviewScreen,
  value: string | null,
) {
  const scenarios = previewScenarioIds[screen] as readonly string[]
  return value && scenarios.includes(value) ? value : scenarios[0]
}
