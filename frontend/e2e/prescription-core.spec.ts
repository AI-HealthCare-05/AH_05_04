import { expect, test } from '@playwright/test'
import {
  ids,
  installRequirementsApi,
  sensitiveSentinel,
  syntheticToken,
} from './fixtures/requirementsApi'

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
  }, syntheticToken)
})

test('[REQ-USR-001][REQ-DOC-001][REQ-DOC-003][REQ-DOC-005][REQ-DOC-006][REQ-DOC-007][REQ-DOC-009][REQ-DOC-010][REQ-OCR-004] 업로드부터 누락값 직접 입력·확정·가이드·채팅까지 이어진다', async ({ page }) => {
  const api = await installRequirementsApi(page, { missingPrescribedDate: true })
  await page.goto('/prescriptions/upload')
  await expect(page.getByRole('heading', { name: '처방전을 등록해 주세요' })).toBeVisible()

  await page.locator('input[type=file]').nth(1).setInputFiles({
    name: 'synthetic-prescription.png',
    mimeType: 'image/png',
    buffer: Buffer.from('synthetic prescription image'),
  })
  await page.getByRole('button', { name: '처방전 읽기' }).click()

  await expect(page.getByText('누락된 항목을 직접 입력해 주세요')).toBeVisible({ timeout: 15_000 })
  const prescribedDate = page.getByLabel('처방일')
  await expect(prescribedDate).toHaveAttribute('placeholder', '필수 입력')
  await prescribedDate.fill('2026-09-08')
  await page.getByRole('button', { name: '수정완료' }).click()

  const acknowledgement = page.getByRole('checkbox')
  await expect(acknowledgement).toBeEnabled()
  await acknowledgement.check()
  await page.getByRole('button', { name: '처방전 확정 및 가이드 만들기' }).click()

  await expect(page).toHaveURL(new RegExp(`/guides/${ids.guide}$`))
  await expect(page.getByRole('heading', { name: '확인된 복약 안내' })).toBeVisible()
  await page.getByRole('button', { name: '복약 챗봇 도지와 이야기하기' }).click()
  await expect(page).toHaveURL(new RegExp(`/chat\\?prescription_id=${ids.prescription}$`))
  await expect(page.getByText('안녕하세요, 도지입니다.')).toBeVisible()

  await page.getByLabel('복약 질문').fill('언제 복용하나요?')
  await page.getByRole('button', { name: '질문 전송' }).click()
  await expect(page.getByText('언제 복용하나요?에 대한 합성 안전 답변입니다.')).toBeVisible()

  expect(api.jobPollCount).toBeGreaterThanOrEqual(4)
  expect(api.idempotencyKeys).toHaveLength(1)
  expect(api.idempotencyKeys[0]).not.toBe('')
  expect(api.unexpectedRequests).toEqual([])
})

test('[REQ-DOC-004][NFR-SYS-007] 업로드 실패는 내부 상세를 숨기고 재선택 경로를 제공한다', async ({ page }) => {
  const api = await installRequirementsApi(page, { failUploadOnce: true })
  await page.goto('/prescriptions/upload')
  await page.locator('input[type=file]').nth(1).setInputFiles({
    name: 'synthetic-prescription.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-synthetic'),
  })
  await page.getByRole('button', { name: '처방전 읽기' }).click()

  await expect(page.getByRole('heading', { name: '처방전을 등록하지 못했어요' })).toBeVisible()
  await expect(page.getByRole('button', { name: '다시 선택하기' })).toBeVisible()
  await expect(page.getByText(sensitiveSentinel)).toHaveCount(0)
  expect(api.uploadCount).toBe(1)
  expect(api.unexpectedRequests).toEqual([])
})

test('[NFR-SYS-003][REQ-OCR-002] STALE 작업은 자동 성공 처리하지 않고 새 업로드를 요구한다', async ({ page }) => {
  const api = await installRequirementsApi(page, { jobStatuses: ['STALE'] })
  await page.goto('/prescriptions/upload')
  await page.locator('input[type=file]').nth(1).setInputFiles({
    name: 'synthetic-prescription.png',
    mimeType: 'image/png',
    buffer: Buffer.from('synthetic'),
  })
  await page.getByRole('button', { name: '처방전 읽기' }).click()

  await expect(page.getByText('최신 정보 확인이 필요해요')).toBeVisible()
  await expect(page.getByRole('button', { name: '최신 정보 확인하기' })).toBeVisible()
  expect(api.unexpectedRequests).toEqual([])
})
