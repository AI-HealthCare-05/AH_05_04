import { expect, test } from '@playwright/test'
import { ids, installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
  }, syntheticToken)
})

test('[CURRENT-RUNTIME][REQ-HIS-009] 현재 처방의 가이드와 기존 채팅 메시지를 서버에서 다시 찾는다', async ({ page }) => {
  const api = await installRequirementsApi(page, {
    existingPrescription: true,
    existingGuide: true,
    existingChat: true,
  })

  await page.goto('/guides')
  await expect(page).toHaveURL(new RegExp(`/guides/${ids.guide}$`))
  await expect(page.getByRole('heading', { name: '확인된 복약 안내' })).toBeVisible()
  await page.getByRole('button', { name: '복약 챗봇 도지와 이야기하기' }).click()
  await expect(page.getByText('기존 합성 질문입니다.')).toBeVisible()
  await expect(page.getByText('기존 합성 답변입니다.')).toBeVisible()
  expect(await page.evaluate(() => sessionStorage.length)).toBe(1)
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
