import { expect, test } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

test('[REQ-USR-007] 유효한 정보로 가입한 사용자는 로그인 화면으로 이동한다', async ({ page }) => {
  const api = await installRequirementsApi(page)
  await page.goto('/signup')

  await page.getByLabel('이름').fill('합성 사용자')
  await page.getByLabel('이메일').fill('synthetic@example.com')
  await page.getByLabel('비밀번호').fill('Synthetic1!')
  await page.getByRole('button', { name: '가입 완료' }).click()

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: '다시 만나서 반가워요' })).toBeVisible()
  expect(api.unexpectedRequests).toEqual([])
})

test('[REQ-USR-010][REQ-USR-019][REQ-USR-020] 보호 화면·프로필 저장·로그아웃 경계를 확인한다', async ({ page }) => {
  const api = await installRequirementsApi(page)
  await page.goto('/profile')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('사용자 정보')).toHaveCount(0)

  await page.getByLabel('이메일').fill('synthetic@example.com')
  await page.getByLabel('비밀번호').fill('Synthetic1!')
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page.getByText('오늘도 건강한 하루 되세요')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBe(syntheticToken)

  await page.goto('/profile')
  await expect(page.getByText('synthetic@example.com')).toBeVisible()
  await page.getByRole('button', { name: '이름·이메일 수정' }).click()
  await page.getByLabel('이름').fill('변경 합성 사용자')
  await page.getByRole('button', { name: '저장' }).click()
  await expect(page.getByText('내 정보가 저장되었습니다.')).toBeVisible()

  await page.goto('/menu')
  await page.getByRole('button', { name: '로그아웃' }).click()
  await expect(page).toHaveURL(/\/start$/)
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull()
  await page.goto('/profile')
  await expect(page).toHaveURL(/\/login$/)
  expect(api.profilePatchCount).toBe(1)
  expect(api.logoutCount).toBe(1)
  expect(api.unexpectedRequests).toEqual([])
})
