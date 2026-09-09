import type { Page, Route } from '@playwright/test'
import type {
  ChatMessageListResponse,
  ChatSessionResponse,
  SendChatMessageResponse,
} from '../../src/api/chat'
import type { GuideResponse } from '../../src/api/guides'
import type { PrescriptionResponse } from '../../src/api/prescriptions'
import { previewIds } from '../../src/dev-preview/syntheticIds'

export const ids = previewIds

export const syntheticToken = 'synthetic-e2e-access-token'
export const sensitiveSentinel = 'SYNTHETIC_PROVIDER_SECRET_MUST_NOT_RENDER'

type JobStatus =
  | 'PENDING'
  | 'PROCESSING'
  | 'RETRY_WAIT'
  | 'COMPLETED'
  | 'FAILED'
  | 'STALE'

type MockApiOptions = {
  existingPrescription?: boolean
  existingGuide?: boolean
  existingChat?: boolean
  missingPrescribedDate?: boolean
  failUploadOnce?: boolean
  jobStatuses?: JobStatus[]
}

type Field = {
  field_id: string
  field_type: string
  medication_index: number
  raw_value: string | null
  normalized_value: string | null
  normalization_version: string | null
  confirmed_value: string | null
  confidence_score: number | null
  confirmation_status: 'CONFIRMED' | 'UNCONFIRMED'
}

export type RequirementsApiState = {
  jobPollCount: number
  uploadCount: number
  profilePatchCount: number
  logoutCount: number
  unexpectedRequests: string[]
  idempotencyKeys: string[]
  manualMedicationRequests: Array<{
    idempotencyKey: string
    body: Record<string, unknown>
  }>
  confirmedMedicationCount: number | null
}

const now = '2026-09-08T09:00:00Z'

function field(
  fieldType: string,
  medicationIndex: number,
  value: string | null,
): Field {
  return {
    field_id: `${fieldType}-${medicationIndex}`,
    field_type: fieldType,
    medication_index: medicationIndex,
    raw_value: value,
    normalized_value: null,
    normalization_version: null,
    confirmed_value: value,
    confidence_score: value === null ? null : 0.99,
    confirmation_status: value === null ? 'UNCONFIRMED' : 'CONFIRMED',
  }
}

function json(route: Route, body: unknown, status = 200, headers = {}) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store', ...headers },
    body: JSON.stringify(body),
  })
}

function error(route: Route, status: number, code: string, message: string) {
  return json(route, {
    code,
    message,
    details: [],
    trace_id: 'synthetic-e2e-trace',
  }, status)
}

export async function installRequirementsApi(
  page: Page,
  options: MockApiOptions = {},
): Promise<RequirementsApiState> {
  const state: RequirementsApiState = {
    jobPollCount: 0,
    uploadCount: 0,
    profilePatchCount: 0,
    logoutCount: 0,
    unexpectedRequests: [],
    idempotencyKeys: [],
    manualMedicationRequests: [],
    confirmedMedicationCount: null,
  }
  let prescriptionExists = options.existingPrescription ?? false
  let guideExists = options.existingGuide ?? false
  let chatExists = options.existingChat ?? false
  let uploadShouldFail = options.failUploadOnce ?? false
  const statuses = options.jobStatuses ?? [
    'PENDING',
    'RETRY_WAIT',
    'PROCESSING',
    'COMPLETED',
  ]
  const fields = [
    field('PRESCRIBED_DATE', 0, options.missingPrescribedDate ? null : '2026-09-08'),
    field('MEDICATION_NAME', 1, '합성 처방약'),
    field('MEDICATION_STRENGTH', 1, '100mg'),
    field('DOSE_VALUE', 1, '1'),
    field('DOSE_UNIT', 1, '정'),
    field('FREQUENCY_PER_DAY', 1, '3'),
    field('DURATION_DAYS', 1, '7'),
    field('TIMING', 1, '식후'),
  ]

  const prescription = () => ({
    data: {
      prescription_id: ids.prescription,
      prescription_version_id: ids.prescriptionVersion,
      revision: 1,
      current: true,
      document_id: ids.document,
      prescribed_date: '2026-09-08',
      confirmed_at: now,
      medications: [
        {
          prescription_version_medication_id: ids.prescriptionVersionMedication,
          medication_name: '합성 처방약',
          strength_text: '100mg',
          dose_value: 1,
          dose_unit: '정',
          frequency_per_day: 3,
          timing_text: '식후',
          duration_days: 7,
          display_order: 1,
        },
        ...(fields.some((candidate) => candidate.medication_index === 2)
          ? [{
              prescription_version_medication_id:
                ids.prescriptionVersionManualMedication,
              medication_name: '직접입력약정',
              strength_text: '50mg',
              dose_value: 0.5,
              dose_unit: '정',
              frequency_per_day: 2,
              timing_text: '저녁 식후',
              duration_days: 5,
              display_order: 2,
            }]
          : []),
      ],
    },
  }) satisfies PrescriptionResponse
  const guide = () => ({
    data: {
      guide_id: ids.guide,
      prescription_id: ids.prescription,
      prescription_version_id: ids.prescriptionVersion,
      generation_status: 'COMPLETED',
      content: '합성 처방약은 확정된 처방 지시에 따라 복용하세요.',
      model_name: 'synthetic-guide-model',
      prompt_version: 'synthetic-guide-prompt',
      requested_at: now,
      completed_at: now,
    },
  }) satisfies GuideResponse
  const chatSession = () => ({
    data: {
      session_id: ids.session,
      prescription_id: ids.prescription,
      prescription_version_id: ids.prescriptionVersion,
      session_status: 'ACTIVE',
      created_at: now,
    },
  }) satisfies ChatSessionResponse

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname
    const key = `${method} ${path}`

    if (key === 'POST /api/v1/auth/signup') {
      return json(route, { detail: '회원가입이 완료되었습니다.' }, 201)
    }
    if (key === 'POST /api/v1/auth/login') {
      return json(route, { access_token: syntheticToken })
    }
    if (key === 'POST /api/v1/auth/logout') {
      state.logoutCount += 1
      return json(route, { detail: '로그아웃되었습니다.' })
    }
    if (key === 'GET /api/v1/users/me') {
      return json(route, {
        id: ids.user,
        name: '합성 사용자',
        email: 'synthetic@example.com',
        phone_number: null,
        birthday: null,
        gender: null,
        created_at: now,
      })
    }
    if (key === 'PATCH /api/v1/users/me') {
      state.profilePatchCount += 1
      const body = request.postDataJSON() as { name: string; email: string }
      return json(route, {
        id: ids.user,
        ...body,
        phone_number: null,
        birthday: null,
        gender: null,
        created_at: now,
      })
    }
    if (key === 'GET /api/v1/prescriptions/latest') {
      return prescriptionExists
        ? json(route, prescription())
        : error(route, 404, 'PRESCRIPTION_NOT_FOUND', '처방을 찾을 수 없습니다.')
    }
    if (key === `GET /api/v1/prescriptions/${ids.prescription}/guide`) {
      return guideExists
        ? json(route, guide())
        : error(route, 404, 'GUIDE_NOT_FOUND', '가이드를 찾을 수 없습니다.')
    }
    if (key === 'POST /api/v1/documents') {
      state.uploadCount += 1
      if (uploadShouldFail) {
        uploadShouldFail = false
        return error(route, 503, 'DEPENDENCY_UNAVAILABLE', sensitiveSentinel)
      }
      return json(route, {
        data: { document_id: ids.document, upload_status: 'UPLOADED', uploaded_at: now },
        message: '업로드 완료',
      }, 201)
    }
    if (key === `POST /api/v1/documents/${ids.document}/ocr-jobs`) {
      state.idempotencyKeys.push(request.headers()['idempotency-key'] ?? '')
      return json(route, {
        data: {
          job_id: ids.aiJob,
          job_type: 'OCR',
          status: 'PENDING',
          domain_type: 'OCR_JOB',
          domain_id: ids.ocrJob,
          prescription_version_id: null,
          status_url: `/api/v1/jobs/${ids.aiJob}`,
          result_url: null,
          retry_after_seconds: 0,
          error: null,
          created_at: now,
          updated_at: now,
        },
      }, 202, { Location: `/api/v1/jobs/${ids.aiJob}` })
    }
    if (key === `GET /api/v1/jobs/${ids.aiJob}`) {
      const status = statuses[Math.min(state.jobPollCount, statuses.length - 1)]
      state.jobPollCount += 1
      return json(route, {
        data: {
          job_id: ids.aiJob,
          job_type: 'OCR',
          status,
          domain_type: 'OCR_JOB',
          domain_id: ids.ocrJob,
          prescription_version_id: null,
          status_url: `/api/v1/jobs/${ids.aiJob}`,
          result_url: status === 'COMPLETED' ? `/api/v1/ocr-jobs/${ids.ocrJob}` : null,
          retry_after_seconds: status === 'RETRY_WAIT' ? 0 : null,
          error: status === 'STALE' ? { code: 'RETRY_EXHAUSTED', message: 'synthetic stale' } : null,
          created_at: now,
          updated_at: now,
        },
      }, 200, { 'Retry-After': '0' })
    }
    if (key === `GET /api/v1/ocr-jobs/${ids.ocrJob}`) {
      return json(route, {
        data: {
          job_id: ids.ocrJob,
          document_id: ids.document,
          ocr_status: 'COMPLETED',
          error_code: null,
          engine_name: 'SYNTHETIC_OCR',
          model_version: 'synthetic-v1',
          prompt_version: null,
          created_at: now,
          completed_at: now,
          fields,
        },
      })
    }
    if (key === `GET /api/v1/documents/${ids.document}/file`) {
      return route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+Xh4QAAAAAElFTkSuQmCC', 'base64'),
      })
    }
    if (key === `POST /api/v1/ocr-jobs/${ids.ocrJob}/manual-medications`) {
      const idempotencyKey = request.headers()['idempotency-key'] ?? ''
      const body = request.postDataJSON() as Record<string, unknown>
      state.manualMedicationRequests.push({ idempotencyKey, body })
      const medicationIndex = 2
      const values: Record<string, string | null> = {
        MEDICATION_NAME: String(body.medication_name),
        MEDICATION_STRENGTH: body.medication_strength as string | null,
        DOSE_VALUE: String(body.dose_value),
        DOSE_UNIT: body.dose_unit as string | null,
        FREQUENCY_PER_DAY: String(body.frequency_per_day),
        TIMING: body.timing as string | null,
        DURATION_DAYS: String(body.duration_days),
      }
      if (!fields.some((candidate) => candidate.medication_index === medicationIndex)) {
        fields.push(
          ...Object.entries(values).map<Field>(([fieldType, value]) => ({
            field_id: `manual-${fieldType}-${medicationIndex}`,
            field_type: fieldType,
            medication_index: medicationIndex,
            raw_value: null,
            normalized_value: null,
            normalization_version: 'manual-entry@1',
            confirmed_value: value,
            confidence_score: null,
            confirmation_status: 'CONFIRMED',
          })),
        )
      }
      return json(route, {
        data: {
          job_id: ids.ocrJob,
          document_id: ids.document,
          ocr_status: 'COMPLETED',
          error_code: null,
          engine_name: 'SYNTHETIC_OCR',
          model_version: 'synthetic-v1',
          prompt_version: null,
          created_at: now,
          completed_at: now,
          fields,
        },
      }, 201)
    }
    if (method === 'PATCH' && path.startsWith('/api/v1/extracted-fields/')) {
      const fieldId = path.split('/').at(-1) ?? ''
      const current = fields.find((candidate) => candidate.field_id === fieldId)
      if (!current) return error(route, 404, 'FIELD_NOT_FOUND', '필드를 찾을 수 없습니다.')
      const body = request.postDataJSON() as { confirmed_value: string | null }
      Object.assign(current, {
        confirmed_value: body.confirmed_value,
        confirmation_status: 'CONFIRMED',
      })
      return json(route, { data: current })
    }
    if (key === `POST /api/v1/documents/${ids.document}/prescription`) {
      prescriptionExists = true
      const response = prescription()
      state.confirmedMedicationCount = response.data.medications.length
      return json(route, response, 201)
    }
    if (key === 'POST /api/v1/guides') {
      guideExists = true
      return json(route, guide(), 201)
    }
    if (key === `GET /api/v1/guides/${ids.guide}`) {
      return json(route, guide())
    }
    if (key === `GET /api/v1/prescriptions/${ids.prescription}/chat-session`) {
      return chatExists
        ? json(route, chatSession())
        : error(route, 404, 'CHAT_SESSION_NOT_FOUND', '대화를 찾을 수 없습니다.')
    }
    if (key === `POST /api/v1/prescriptions/${ids.prescription}/chat-sessions`) {
      chatExists = true
      return json(route, chatSession(), 201)
    }
    if (key === `GET /api/v1/chat-sessions/${ids.session}/messages`) {
      return json(route, {
        data: {
          session_id: ids.session,
          messages: options.existingChat ? [
            { message_id: '77777777-7777-4777-8777-777777777777', role: 'USER', content: '기존 합성 질문입니다.', generation_status: 'NOT_APPLICABLE', created_at: now },
            { message_id: '88888888-8888-4888-8888-888888888888', role: 'ASSISTANT', content: '기존 합성 답변입니다.', generation_status: 'COMPLETED', created_at: now },
          ] : [],
        },
      } satisfies ChatMessageListResponse)
    }
    if (key === `POST /api/v1/chat-sessions/${ids.session}/messages`) {
      const body = request.postDataJSON() as { content: string }
      return json(route, {
        data: {
          user_message_id: '99999999-9999-4999-8999-999999999999',
          assistant_message_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          session_id: ids.session,
          generation_status: 'COMPLETED',
          content: `${body.content}에 대한 합성 안전 답변입니다.`,
          model_name: 'synthetic-chat-model',
          prompt_version: 'synthetic-chat-prompt',
          created_at: now,
          completed_at: now,
        },
      } satisfies SendChatMessageResponse, 201)
    }

    state.unexpectedRequests.push(key)
    return error(route, 501, 'UNEXPECTED_E2E_REQUEST', key)
  })

  return state
}
