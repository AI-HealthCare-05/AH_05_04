import { expect, test } from '@playwright/test'
import { ids, installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
  }, syntheticToken)
})

test('[CURRENT-RUNTIME][REQ-HIS-009] 현재 처방의 Guide와 기존 Chat session을 재발견해 새 진입 UI를 표시한다', async ({ page }) => {
  const api = await installRequirementsApi(page, {
    existingPrescription: true,
    existingGuide: true,
    existingChat: true,
  })

  await page.goto('/guides')
  await expect(page).toHaveURL(new RegExp(`/guides/${ids.guide}$`))
  await expect(page.getByRole('heading', { name: '확인된 복약 안내' })).toBeVisible()
  await page.getByRole('button', { name: '복약 챗봇 도지와 이야기하기' }).click()
  await expect(page).toHaveURL(new RegExp(`/chat[?]prescription_id=${ids.prescription}$`))
  await expect(page.getByText('안녕하세요, 도지입니다.')).toBeVisible()
  await expect(page.getByText('무엇을 도와드릴까요?')).toBeVisible()
  await expect(page.getByRole('button', { name: '아침 약은 언제 먹나요?' })).toBeVisible()
  await expect(page.getByText('기존 합성 질문입니다.')).toHaveCount(0)
  await expect(page.getByText('기존 합성 답변입니다.')).toHaveCount(0)
  expect(api.chatSessionRediscoveryCount).toBe(1)
  expect(api.chatSessionCreationCount).toBe(0)
  expect(api.chatMessagesGetCount).toBe(1)
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0)
  expect(api.unexpectedRequests).toEqual([])
})

test('[CURRENT-RUNTIME][REQ-HIS-009] 재로그인과 같은 빈 client state의 도지 진입은 최신 처방 session으로 복약 질문을 보낸다', async ({ page }) => {
  const api = await installRequirementsApi(page, {
    existingPrescription: true,
    existingChat: true,
  })

  await page.goto('/chat')
  await expect(page).toHaveURL(/\/chat$/)
  await expect(page.getByText('무엇을 도와드릴까요?')).toBeVisible()

  const question = '현재 복용 중인 약은 무엇인가요?'
  await page.getByLabel('복약 질문').fill(question)
  await page.getByRole('button', { name: '질문 전송' }).click()
  await expect(
    page.getByText(`${question}에 대한 합성 안전 답변입니다.`),
  ).toBeVisible()

  expect(api.chatSessionRediscoveryCount).toBe(1)
  expect(api.chatSessionCreationCount).toBe(0)
  expect(api.chatMessagesGetCount).toBe(1)
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0)
  expect(api.unexpectedRequests).toEqual([])
})

test('[NFR-SYS-009] 현재 처방이나 가이드가 없으면 빈 상태와 다음 행동을 표시한다', async ({ page }) => {
  const api = await installRequirementsApi(page)

  await page.goto('/guides')
  await expect(page.getByRole('heading', { name: '아직 만들어진 가이드가 없어요' })).toBeVisible()
  await expect(page.getByRole('button', { name: '처방전 등록하기' })).toBeVisible()

  await page.goto('/chat')
  await expect(page.getByText(/먼저 처방전을 등록해 주세요/)).toBeVisible()
  await expect(page.getByRole('button', { name: '처방전 등록하기' })).toBeVisible()
  expect(api.unexpectedRequests).toEqual([])
})
