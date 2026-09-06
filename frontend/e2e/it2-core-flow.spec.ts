import { expect, test, type Page, type Route } from '@playwright/test'

const documentId = '11111111-1111-4111-8111-111111111111'
const jobId = '22222222-2222-4222-8222-222222222222'
const prescriptionId = '33333333-3333-4333-8333-333333333333'
const guideId = '44444444-4444-4444-8444-444444444444'
const sessionId = '55555555-5555-4555-8555-555555555555'
const now = '2026-09-06T09:00:00Z'

type MockField = {
  field_id: string
  field_type: string
  medication_index: number
  raw_value: string
  normalized_value: string | null
  normalization_version: string | null
  confirmed_value: string | null
  confidence_score: number
  confirmation_status: 'UNCONFIRMED' | 'CONFIRMED'
}

function field(
  id: string,
  fieldType: string,
  medicationIndex: number,
  value: string,
): MockField {
  return {
    field_id: `field-${id}`,
    field_type: fieldType,
    medication_index: medicationIndex,
    raw_value: value,
    normalized_value: fieldType === 'PRESCRIBED_DATE' ? value : null,
    normalization_version: fieldType === 'PRESCRIBED_DATE' ? 'it2-v1' : null,
    confirmed_value: null,
    confidence_score: 0.99,
    confirmation_status: 'UNCONFIRMED',
  }
}

const initialFields: MockField[] = [
  field('date', 'PRESCRIBED_DATE', 0, '2026-09-06'),
  field('name', 'MEDICATION_NAME', 1, '합성약A'),
  field('strength', 'MEDICATION_STRENGTH', 1, '10mg'),
  field('dose', 'DOSE_VALUE', 1, '1'),
  field('unit', 'DOSE_UNIT', 1, '정'),
  field('frequency', 'FREQUENCY_PER_DAY', 1, '2'),
  field('timing', 'TIMING', 1, '식후'),
  field('duration', 'DURATION_DAYS', 1, '3'),
]

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify(body),
  })
}

async function installAuthenticatedUser(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'synthetic-it2-token')
  })
}

function ocrResponse(
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED',
  fields: MockField[],
) {
  return {
    data: {
      job_id: jobId,
      document_id: documentId,
      ocr_status: status,
      error_code: null,
      engine_name: 'synthetic-it2-ocr',
      model_version: 'it2-v1',
      prompt_version: null,
      created_at: now,
      completed_at: status === 'COMPLETED' ? now : null,
      fields,
    },
  }
}

function guideData() {
  return {
    guide_id: guideId,
    prescription_id: prescriptionId,
    generation_status: 'COMPLETED',
    content: [
      '복약 가이드',
      '[1] 합성약A 10mg\n용량: 1정\n복용 횟수: 하루 2회\n복용 시점: 식후\n복용 기간: 3일\n복약 안내: 처방전에서 확인한 복용 조건을 따르세요.',
      '공통 안내: 복용법을 임의로 바꾸지 마세요.\n안전 안내: 이상 반응이 있으면 의료진 또는 약사와 상담하세요.',
    ].join('\n\n'),
    model_name: 'synthetic-it2-provider',
    prompt_version: 'guide-prompt-it2-fixture',
    requested_at: now,
    completed_at: now,
  }
}

function errorBody(code: string, message: string) {
  return { code, message, details: [], trace_id: 'synthetic-it2-trace' }
}

async function installCoreJourneyApi(page: Page) {
  const fields = structuredClone(initialFields)
  let ocrPolls = 0

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (method === 'POST' && path === '/api/v1/auth/login') {
      return json(route, { access_token: 'synthetic-it2-token' })
    }

    if (method === 'GET' && path === '/api/v1/users/me') {
      return json(route, {
        id: '00000000-0000-4000-8000-000000000001',
        name: 'IT-2 테스트 사용자',
        email: 'it2@example.test',
        phone_number: null,
        birthday: null,
        gender: null,
        created_at: now,
      })
    }

    if (method === 'POST' && path === '/api/v1/documents') {
      return json(route, {
        data: {
          document_id: documentId,
          upload_status: 'UPLOADED',
          uploaded_at: now,
        },
        message: 'uploaded',
      }, 201)
    }

    if (
      method === 'POST' &&
      path === `/api/v1/documents/${documentId}/ocr-jobs`
    ) {
      return json(route, ocrResponse('PENDING', []), 201)
    }

    if (method === 'GET' && path === `/api/v1/ocr-jobs/${jobId}`) {
      ocrPolls += 1
      const status = ocrPolls === 1 ? 'PROCESSING' : 'COMPLETED'
      return json(route, ocrResponse(status, status === 'COMPLETED' ? fields : []))
    }

    if (method === 'GET' && path === `/api/v1/documents/${documentId}/file`) {
      return route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
          'base64',
        ),
      })
    }

    const fieldMatch = path.match(/^\/api\/v1\/extracted-fields\/(.+)$/)
    if (method === 'PATCH' && fieldMatch) {
      const target = fields.find((candidate) => candidate.field_id === fieldMatch[1])
      if (!target) {
        return json(
          route,
          errorBody('EXTRACTED_FIELD_NOT_FOUND', '항목을 찾을 수 없습니다.'),
          404,
        )
      }
      const body = request.postDataJSON() as { confirmed_value: string | null }
      target.confirmed_value = body.confirmed_value
      target.confirmation_status = 'CONFIRMED'
      return json(route, { data: target })
    }

    if (
      method === 'POST' &&
      path === `/api/v1/documents/${documentId}/prescription`
    ) {
      if (fields.some((candidate) => candidate.confirmation_status !== 'CONFIRMED')) {
        return json(
          route,
          errorBody(
            'PRESCRIPTION_REQUIRED_FIELD_MISSING',
            '검토되지 않은 항목이 있습니다.',
          ),
          409,
        )
      }
      return json(route, {
        data: {
          prescription_id: prescriptionId,
          document_id: documentId,
          prescribed_date: '2026-09-06',
          confirmed_at: now,
          medications: [{
            medication_name: '합성약A',
            strength_text: '10mg',
            dose_value: 1,
            dose_unit: '정',
            frequency_per_day: 2,
            timing_text: '식후',
            duration_days: 3,
            display_order: 1,
          }],
        },
      }, 201)
    }

    if (method === 'POST' && path === '/api/v1/guides') {
      return json(route, { data: guideData() }, 201)
    }

    if (method === 'GET' && path === `/api/v1/guides/${guideId}`) {
      return json(route, { data: guideData() })
    }

    if (
      method === 'POST' &&
      path === `/api/v1/prescriptions/${prescriptionId}/chat-sessions`
    ) {
      return json(route, {
        data: {
          session_id: sessionId,
          prescription_id: prescriptionId,
          session_status: 'ACTIVE',
          created_at: now,
        },
      }, 201)
    }

    if (
      method === 'GET' &&
      path === `/api/v1/chat-sessions/${sessionId}/messages`
    ) {
      return json(route, { data: { session_id: sessionId, messages: [] } })
    }

    if (
      method === 'POST' &&
      path === `/api/v1/chat-sessions/${sessionId}/messages`
    ) {
      const body = request.postDataJSON() as { content: string }
      const safeAnswer =
        '확정된 처방에는 합성약A가 하루 2회로 기록되어 있습니다. 임의로 용량을 바꾸지 말고 처방전 또는 의료진 안내를 확인해 주세요.'
      return json(route, {
        data: {
          user_message_id: '66666666-6666-4666-8666-666666666666',
          assistant_message_id: '77777777-7777-4777-8777-777777777777',
          session_id: sessionId,
          generation_status: 'COMPLETED',
          content: safeAnswer,
          model_name: 'synthetic-it2-provider',
          prompt_version: 'chat-prompt-it2-fixture',
          created_at: now,
          completed_at: now,
          echoed_question_for_test: body.content,
        },
      }, 201)
    }

    return json(
      route,
      errorBody('NOT_FOUND', `Unexpected mock request: ${method} ${path}`),
      404,
    )
  })
}

test('IT2-AUTH-01: 비로그인 사용자의 보호 화면 직접 접근을 차단한다', async ({
  page,
}) => {
  await page.goto('/prescriptions/upload')

  await expect(page).toHaveURL(/\/login$/)
  await expect(
    page.getByRole('heading', { name: '다시 만나서 반가워요' }),
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: '처방전을 등록해 주세요' }),
  ).toHaveCount(0)
})

test('IT2-CORE-01: 로그인부터 확정 처방 기반 가이드와 챗봇까지 이어진다', async ({
  page,
}) => {
  await installCoreJourneyApi(page)
  await page.goto('/login')

  await page.getByRole('textbox', { name: '이메일' }).fill('it2@example.test')
  await page.getByLabel('비밀번호').fill('synthetic-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()

  await expect(
    page.getByRole('heading', { name: /IT-2 테스트 사용자님/ }),
  ).toBeVisible()
  await page.getByRole('button', { name: '처방전 등록하기' }).first().click()
  await page.locator('input[type=file]').setInputFiles({
    name: 'synthetic-it2-prescription.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
      'base64',
    ),
  })
  await page.getByRole('button', { name: '처방전 읽기' }).click()

  await expect(
    page.getByRole('heading', { name: '처방전과 같은지 확인해 주세요' }),
  ).toBeVisible()
  await expect(page.getByText('합성약A 10mg')).toBeVisible()
  await expect(
    page.getByRole('button', { name: '처방전 확정 및 가이드 만들기' }),
  ).toBeDisabled()

  await page.getByRole('button', { name: '검토 완료' }).first().click()
  await page.getByRole('button', { name: '검토 완료' }).click()
  await page
    .getByRole('checkbox', {
      name: '원본 처방전의 모든 항목을 직접 확인했습니다.',
    })
    .check()
  await page
    .getByRole('button', { name: '처방전 확정 및 가이드 만들기' })
    .click()

  await expect(page).toHaveURL(`/guides/${guideId}`)
  await expect(page.getByRole('heading', { name: '합성약A 10mg' })).toBeVisible()
  await expect(page.getByText('하루 2회')).toBeVisible()
  await expect(page.getByText('복용법을 임의로 바꾸지 마세요.')).toBeVisible()
  await page
    .getByRole('button', { name: '복약 챗봇 도지와 이야기하기' })
    .click()

  await expect(page.getByRole('heading', { name: '복약 챗봇' })).toBeVisible()
  await page
    .getByRole('textbox', { name: '복약 질문' })
    .fill('이 약을 하루에 몇 번 먹나요?')
  await page.getByRole('button', { name: '질문 전송' }).click()
  await expect(page.getByText('이 약을 하루에 몇 번 먹나요?')).toBeVisible()
  await expect(page.getByText(/합성약A가 하루 2회로 기록/)).toBeVisible()
  await expect(page.getByText(/임의로 용량을 바꾸지 말고/)).toBeVisible()
})

test('IT2-CHAT-01: 확정 처방 식별자 없이 챗봇에 진입하면 질문 전송을 열지 않는다', async ({
  page,
}) => {
  await installAuthenticatedUser(page)
  await page.route('**/api/v1/users/me', (route) =>
    json(route, {
      id: '00000000-0000-4000-8000-000000000001',
      name: 'IT-2 테스트 사용자',
      email: 'it2@example.test',
      phone_number: null,
      birthday: null,
      gender: null,
      created_at: now,
    }),
  )
  await page.goto('/chat')

  await expect(
    page.getByRole('heading', { name: '먼저 확정된 처방이 필요해요' }),
  ).toBeVisible()
  await expect(page.getByRole('textbox', { name: '복약 질문' })).toHaveCount(0)
  await expect(page.getByText('현재 확인된 처방이 없어요.')).toBeVisible()
})
