import { expect, test } from '@playwright/test'

test('#507 390px keyboard verification, neutral failures, resend and signup conflict', async ({ page }) => {
  let requests = 0
  let confirms = 0
  let signups = 0
  await page.route('**/api/v1/auth/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/request')) {
      requests++
      return route.fulfill({ json: { detail: 'ok', verification_token: 'synthetic-local-hidden' } })
    }
    if (path.endsWith('/confirm')) {
      confirms++
      if (confirms === 1) return route.fulfill({ status: 422, json: {
        code: 'VALIDATION_FAILED', message: 'invalid', trace_id: 'synthetic',
        details: [{ field: 'token', reason: 'EMAIL_VERIFICATION_TOKEN_INVALID' }],
      } })
      return route.fulfill({ json: { detail: 'ok' } })
    }
    if (path.endsWith('/signup')) {
      signups++
      return route.fulfill({ status: 409, json: {
        code: 'CONFLICT', message: '이미 사용중인 이메일입니다.', trace_id: 'synthetic', details: [],
      } })
    }
    throw new Error(`Unexpected path: ${path}`)
  })
  await page.goto('/signup')
  await page.getByLabel('이름').fill('합성 사용자')
  await page.getByLabel('이메일', { exact: true }).fill('synthetic@example.com')
  await page.getByLabel('이메일', { exact: true }).press('Tab')
  await expect(page.getByRole('button', { name: '인증 요청', exact: true })).toBeFocused()
  await page.keyboard.press('Enter')
  const code = page.getByLabel('이메일 인증 코드')
  await expect(code).toBeFocused()
  await expect(code).toHaveValue('')
  await expect(page.getByText('synthetic-local-hidden')).toHaveCount(0)
  await code.fill('synthetic-invalid')
  await code.press('Enter')
  await expect(page.getByRole('alert')).toHaveText('인증을 완료하지 못했습니다. 코드를 확인하거나 인증 안내를 다시 요청해 주세요.')
  await expect(code).toBeFocused()
  await page.screenshot({ path: 'test-results/requirements/signup-507-mobile-failure.png', fullPage: true })
  await page.getByRole('button', { name: '인증 안내 다시 요청' }).click()
  await code.fill('synthetic-valid')
  await code.press('Enter')
  await expect(page.getByLabel('비밀번호')).toBeFocused()
  await page.getByLabel('비밀번호').fill('Synthetic1!')
  await expect(page.getByText('이메일 인증이 완료되었습니다.')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  for (const element of await page.locator('input, form button').all()) {
    const bounds = await element.boundingBox()
    expect(bounds).not.toBeNull()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
  }
  await page.screenshot({ path: 'test-results/requirements/signup-507-mobile-verified.png', fullPage: true })
  await page.getByRole('button', { name: '가입 완료' }).click()
  await expect(page.getByRole('alert')).toHaveText('이미 사용중인 이메일입니다.')
  await expect(page.getByLabel('이메일', { exact: true })).toBeFocused()
  await expect(page.getByText('이메일 인증이 완료되었습니다.')).toBeVisible()
  expect({ requests, confirms, signups }).toEqual({ requests: 2, confirms: 2, signups: 1 })
})
