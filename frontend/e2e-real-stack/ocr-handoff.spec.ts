import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

type OneCycleScenario = {
  scenario_version: string
  fixture_path: string
  fixture_sha256: string
  prescribed_date: string
  medications: Array<{
    medication_name: string
    strength_text: string
    dose_value: string
    dose_unit: string
    frequency_per_day: number
    timing_text: string
    duration_days: number
  }>
  question: string
}

const scenarioPath = fileURLToPath(
  new URL(
    '../../backend/app/release_validation/scenarios/ai-one-cycle-clova-openai-v1.json',
    import.meta.url,
  ),
)
const scenario = JSON.parse(readFileSync(scenarioPath, 'utf8')) as OneCycleScenario
const syntheticPrescription = fileURLToPath(
  new URL(`../../${scenario.fixture_path}`, import.meta.url),
)
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const fakeModelMarkers = ['fake', 'sentinel', 'synthetic', 'test-model']

function expectLiveProviderMetadata(
  data: {
    generation_status: string
    content: string | null
    model_name: string | null
    prompt_version: string | null
  },
  expectedPromptVersion: string,
) {
  expect(data.generation_status).toBe('COMPLETED')
  expect(data.content?.trim().length).toBeGreaterThan(0)
  expect(data.model_name?.trim().length).toBeGreaterThan(0)
  expect(data.prompt_version).toBe(expectedPromptVersion)
  expect(fakeModelMarkers.some((marker) => data.model_name!.toLowerCase().includes(marker))).toBe(false)
}

test('[REAL-STACK][REQ-USR-001][REQ-DOC-001][REQ-DOC-003][REQ-DOC-005][REQ-DOC-006][REQ-DOC-010][REQ-OCR-001][REQ-OCR-002][REQ-OCR-004][REQ-GEN-001][REQ-GEN-002][REQ-GEN-003][REQ-GEN-019][REQ-GEN-020] CLOVA 검수부터 OpenAI 가이드·챗 응답까지 실제 one-cycle이 연결된다', async ({ page }) => {
  test.setTimeout(180_000)
  const runId = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
  const email = `e2e-${runId}@example.com`
  const password = 'Synthetic1!'
  const medication = scenario.medications[0]
  const fixtureDigest = createHash('sha256')
    .update(readFileSync(syntheticPrescription))
    .digest('hex')
  expect(`sha256:${fixtureDigest}`).toBe(scenario.fixture_sha256)

  await page.goto('/signup')
  await page.getByLabel('이름').fill('Real Stack 합성 사용자')
  await page.getByLabel('이메일').fill(email)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '가입 완료' }).click()
  await expect(page).toHaveURL(/\/login$/)

  await page.getByLabel('이메일').fill(email)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page.getByText('오늘도 건강한 하루 되세요')).toBeVisible()

  await page.goto('/prescriptions/upload')
  await expect(page.getByRole('heading', { name: '처방전을 등록해 주세요' })).toBeVisible()
  await page.locator('input[type=file]').nth(1).setInputFiles(syntheticPrescription)

  const acceptedOcrJobPromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      /\/api\/v1\/documents\/[0-9a-f-]+\/ocr-jobs$/i.test(
        new URL(response.url()).pathname,
      ),
  )
  const completedOcrJobPromise = page.waitForResponse(async (response) => {
    if (
      response.request().method() !== 'GET' ||
      !/\/api\/v1\/jobs\/[0-9a-f-]+$/i.test(new URL(response.url()).pathname) ||
      response.status() !== 200
    ) {
      return false
    }
    const body = await response.json() as { data?: { status?: string } }
    return body.data?.status === 'COMPLETED'
  })
  await page.getByRole('button', { name: '처방전 읽기' }).click()

  const acceptedOcrJob = await acceptedOcrJobPromise
  expect(acceptedOcrJob.status()).toBe(202)
  const acceptedOcrBody = await acceptedOcrJob.json() as {
    data: {
      job_id: string
      status: string
      status_url: string
      result_url: string | null
    }
  }
  expect(acceptedOcrBody.data.job_id).toMatch(uuidPattern)
  expect(acceptedOcrBody.data.status).toBe('PENDING')
  expect(acceptedOcrBody.data.status_url).toBe(`/api/v1/jobs/${acceptedOcrBody.data.job_id}`)
  expect(acceptedOcrBody.data.result_url).toBeNull()
  expect(acceptedOcrJob.headers().location).toBe(acceptedOcrBody.data.status_url)
  const completedOcrJob = await completedOcrJobPromise
  expect(completedOcrJob.status()).toBe(200)

  await expect(page).toHaveURL(/\/prescriptions\/review\?document_id=[^&]+&job_id=[^&]+$/)
  await expect(
    page.getByRole('heading', {
      name: /처방전과 같은지 확인해 주세요|누락된 항목을 직접 입력해 주세요/,
    }),
  ).toBeVisible()
  const prescriptionCard = page.locator('.prescription-review__prescription-card')
  const medicationCard = page.locator('.prescription-review__medication-card').first()
  await prescriptionCard.getByText('원본 처방전 보기').click()
  await expect(prescriptionCard.getByTitle('원본 처방전')).toBeVisible()
  await expect(prescriptionCard).toContainText(scenario.prescribed_date.slice(0, 4))
  await expect(medicationCard).toContainText(medication.medication_name)
  await expect(medicationCard).toContainText(medication.strength_text)
  await expect(medicationCard).toContainText(medication.dose_value)
  await expect(medicationCard).toContainText(medication.dose_unit)
  await expect(medicationCard).toContainText(String(medication.frequency_per_day))
  await expect(medicationCard).toContainText(medication.timing_text)
  await expect(medicationCard).toContainText(String(medication.duration_days))

  await prescriptionCard.getByRole('button', { name: '검토 완료' }).click()
  await expect(prescriptionCard.getByText('✓ 검토 완료')).toBeVisible()
  await medicationCard.getByRole('button', { name: '검토 완료' }).click()
  await expect(medicationCard.getByText('✓ 검토 완료')).toBeVisible()

  const acknowledgement = page.getByRole('checkbox', {
    name: '원본 처방전의 모든 항목을 직접 확인했습니다.',
  })
  await expect(acknowledgement).toBeEnabled()
  await acknowledgement.check()

  const guideResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/v1/guides',
  )
  await page.getByRole('button', { name: '처방전 확정 및 가이드 만들기' }).click()
  const guideResponse = await guideResponsePromise
  expect(guideResponse.status()).toBe(201)
  const guideBody = await guideResponse.json() as {
    data: {
      guide_id: string
      prescription_id: string
      prescription_version_id: string
      generation_status: string
      content: string | null
      model_name: string | null
      prompt_version: string | null
    }
  }
  expect(guideBody.data.guide_id).toMatch(uuidPattern)
  expect(guideBody.data.prescription_id).toMatch(uuidPattern)
  expect(guideBody.data.prescription_version_id).toMatch(uuidPattern)
  expectLiveProviderMetadata(guideBody.data, 'guide-prompt-v3')
  expect(guideBody.data.content).toContain(medication.medication_name)
  expect(guideBody.data.content).toContain(medication.strength_text)
  expect(guideBody.data.content).toContain(`하루 ${medication.frequency_per_day}회`)
  expect(guideBody.data.content).toContain(medication.timing_text)

  await expect(page).toHaveURL(new RegExp(`/guides/${guideBody.data.guide_id}$`))
  await expect(
    page.getByRole('heading', { name: /확인된 약 목록 · 1개|확인된 복약 안내/ }),
  ).toBeVisible()
  await expect(page.getByText(medication.medication_name, { exact: false }).first()).toBeVisible()

  await page.getByRole('button', { name: '복약 챗봇 도지와 이야기하기' }).click()
  await expect(page).toHaveURL(new RegExp(`/chat\\?prescription_id=${guideBody.data.prescription_id}$`))
  await expect(page.getByText('안녕하세요, 도지입니다.')).toBeVisible()

  await page.getByLabel('복약 질문').fill(scenario.question)
  const chatResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      /\/api\/v1\/chat-sessions\/[0-9a-f-]+\/messages$/i.test(
        new URL(response.url()).pathname,
      ),
  )
  await page.getByRole('button', { name: '질문 전송' }).click()
  const chatResponse = await chatResponsePromise
  expect(chatResponse.status()).toBe(201)
  const chatBody = await chatResponse.json() as {
    data: {
      user_message_id: string
      assistant_message_id: string
      session_id: string
      generation_status: string
      content: string | null
      model_name: string | null
      prompt_version: string | null
    }
  }
  expect(chatBody.data.user_message_id).toMatch(uuidPattern)
  expect(chatBody.data.assistant_message_id).toMatch(uuidPattern)
  expect(chatBody.data.session_id).toMatch(uuidPattern)
  expectLiveProviderMetadata(chatBody.data, 'chat-prompt-v2')
  expect(chatBody.data.content).toContain(`${medication.frequency_per_day}회`)
  for (const timingToken of medication.timing_text.split(/\s+/)) {
    expect(chatBody.data.content).toContain(timingToken)
  }

  await expect(page.getByText(scenario.question)).toBeVisible()
  await expect(page.locator('.chat-message:not(.user)').last()).toContainText(
    chatBody.data.content!,
  )
})
