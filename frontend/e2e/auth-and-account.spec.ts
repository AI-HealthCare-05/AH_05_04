import { expect, test } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

test('#562 필수 약관 보기와 복귀는 mutation 없이 선택 0개 가입을 허용한다', async ({ page }) => {
  const api = await installRequirementsApi(page)
  const mutations: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/') &&
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.url())
  })
  await page.goto('/signup')
  await page.getByLabel('이름').fill('합성 사용자')
  await page.getByLabel('이메일', { exact: true }).fill('synthetic@example.com')
  await page.getByLabel('비밀번호').fill('Synthetic1!')
  await expect(page.getByRole('button', { name: '가입 완료' })).toBeDisabled()
  await page.getByRole('button', { name: '약관 보기' }).click()
  await expect(page.getByRole('heading', { name: '필수 약관 보기' })).toBeFocused()
  await expect(page.getByRole('heading', { name: '2. 이용약관' })).toBeVisible()
  await expect(page.getByText('시행일자: 2026년 09월 15일')).toBeVisible()
  await expect(page.getByRole('heading', { name: '제10조 (약관의 개정)' })).toBeVisible()
  await expect(page.getByText('개인정보 수집·이용 안내 · 검토용')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/requirements/signup-562-legal.png', fullPage: true })
  await page.getByRole('button', { name: '확인', exact: true }).click()
  await expect(page.getByRole('button', { name: '약관 보기' })).toBeFocused()
  await page.getByRole('button', { name: '약관 보기' }).click()
  await expect(page.getByRole('heading', { name: '필수 약관 보기' })).toBeFocused()
  await page.goBack()
  await expect(page.getByRole('button', { name: '약관 보기' })).toBeFocused()
  await page.getByRole('button', { name: '약관 보기' }).click()
  await expect(page.getByRole('heading', { name: '필수 약관 보기' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '약관 보기' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '필수 약관 보기' })).toBeFocused()
  await page.getByRole('button', { name: '확인', exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: 'test-results/requirements/signup-562-legal-footer.png', fullPage: true })
  await page.getByRole('button', { name: '이전 화면' }).click()
  await expect(page.getByRole('button', { name: '약관 보기' })).toBeFocused()
  await expect(page.getByLabel('비밀번호')).toHaveValue('Synthetic1!')
  await expect(page.getByRole('checkbox', { name: '필수 약관에 동의합니다' })).not.toBeChecked()
  expect(mutations).toEqual([])
  await page.getByRole('checkbox', { name: '필수 약관에 동의합니다' }).check()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/requirements/signup-562-consents.png', fullPage: true })
  await page.getByRole('button', { name: '가입 완료' }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect(api.signupRequests).toEqual([{
    name: '합성 사용자', email: 'synthetic@example.com', password: 'Synthetic1!', consents: [],
  }])
  expect(api.unexpectedRequests).toEqual([])
})

test('[REQ-USR-007] 유효한 정보로 가입한 사용자는 로그인 화면으로 이동한다', async ({ page }) => {
  const api = await installRequirementsApi(page)
  await page.goto('/signup')

  await page.getByLabel('이름').fill('합성 사용자')
  await page.getByLabel('이메일', { exact: true }).fill('synthetic@example.com')
  await page.getByLabel('비밀번호').fill('Synthetic1!')
  await page.getByRole('checkbox', { name: /기능 이용 선택 동의/ }).check()
  await expect(page.getByLabel('이메일 인증 코드')).toHaveCount(0)
  await page.getByRole('checkbox', { name: '필수 약관에 동의합니다' }).check()
  await page.getByRole('button', { name: '가입 완료' }).click()

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: '다시 만나서 반가워요' })).toBeVisible()
  expect(api.signupRequests).toEqual([{
    name: '합성 사용자',
    email: 'synthetic@example.com',
    password: 'Synthetic1!',
    consents: [
      { purpose: 'OCR' },
      { purpose: 'GUIDE' },
      { purpose: 'CHAT' },
      { purpose: 'NOTIFICATION' },
    ],
  }])
  expect(api.unexpectedRequests).toEqual([])
})

test('[REQ-USR-010][REQ-USR-019][REQ-USR-020] 보호 화면·프로필 저장·로그아웃 경계를 확인한다', async ({ page }) => {
  const api = await installRequirementsApi(page)
  await page.goto('/profile')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('사용자 정보')).toHaveCount(0)

  await page.getByLabel('이메일', { exact: true }).fill('synthetic@example.com')
  await page.getByLabel('비밀번호').fill('Synthetic1!')
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page.getByText('오늘도 건강한 하루 되세요')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBe(syntheticToken)

  await page.goto('/profile')
  await expect(page.getByText('synthetic@example.com')).toBeVisible()
  await page.getByRole('button', { name: '사용자 정보 수정' }).click()
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
