import { expect, test, type Page, type Route } from '@playwright/test'

const token = 'synthetic-withdrawal-access-token'

type WithdrawalMock = {
  withdrawalRequests: Array<Record<string, unknown>>
  logoutCount: number
}

async function installWithdrawalApi(
  page: Page,
  response: 'success' | 'failed' | 'gate-off' = 'success',
): Promise<WithdrawalMock> {
  const state: WithdrawalMock = { withdrawalRequests: [], logoutCount: 0 }

  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const key = `${request.method()} ${path}`

    if (key === 'GET /api/v1/users/me') {
      return route.fulfill({ json: {
        id: '00000000-0000-4000-8000-000000000097',
        name: '합성 사용자',
        email: 'withdrawal@example.com',
        phone_number: null,
        birthday: null,
        gender: null,
        created_at: '2026-09-16T00:00:00Z',
      } })
    }

    if (key === 'GET /api/v1/users/me/consents') {
      return route.fulfill({ json: { data: [] } })
    }

    if (key === 'POST /api/v1/auth/logout') {
      state.logoutCount += 1
      return route.fulfill({ json: { detail: '로그아웃되었습니다.' } })
    }

    if (key === 'POST /api/v1/auth/account/withdrawal') {
      state.withdrawalRequests.push(request.postDataJSON() as Record<string, unknown>)
      expect(request.headers().authorization).toBe(`Bearer ${token}`)

      if (response === 'failed') {
        return route.fulfill({ json: {
          detail: '탈퇴 요청 처리에 실패했습니다. 관리자 확인이 필요합니다.',
        } })
      }

      if (response === 'gate-off') {
        return route.fulfill({
          status: 503,
          json: {
            code: 'SERVICE_UNAVAILABLE',
            message: '회원탈퇴 요청 접수 기능은 아직 공개되지 않았습니다.',
            details: [{
              field: 'account_withdrawal',
              reason: 'ACCOUNT_WITHDRAWAL_REQUEST_DISABLED',
              rejected_value: null,
            }],
            trace_id: 'synthetic-withdrawal-trace',
          },
        })
      }

      return route.fulfill({ json: {
        detail: '회원탈퇴가 완료되었습니다.',
      } })
    }

    return route.fulfill({ status: 404, json: {} })
  })

  return state
}

for (const width of [320, 390, 412]) {
  test(`회원탈퇴 요청 happy path와 모바일 배치 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript((accessToken) => {
      if (!sessionStorage.getItem('withdrawal-test-initialized')) {
        localStorage.setItem('access_token', accessToken)
        sessionStorage.setItem('dosey_chat_session:synthetic-prescription', 'synthetic-session')
        sessionStorage.setItem('dosey_ocr_job_recovery:v1', 'synthetic-ocr-recovery')
        localStorage.setItem('dosey_web_push_binding:v1', 'synthetic-push-binding')
        sessionStorage.setItem('unrelated-session', 'keep-me')
        sessionStorage.setItem('withdrawal-test-initialized', 'true')
      }
    }, token)
    const api = await installWithdrawalApi(page)

    await page.goto('/profile')
    await expect(page.getByText('withdrawal@example.com')).toBeVisible()
    await page.getByRole('button', { name: '회원탈퇴', exact: true }).scrollIntoViewIfNeeded()
    await page.getByRole('button', { name: '회원탈퇴', exact: true }).click()

    const password = page.getByLabel('현재 비밀번호')
    const confirmation = page.getByRole('checkbox', {
      name: '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
    })
    const submit = page.getByRole('button', { name: '회원탈퇴 요청' })
    await expect(password).toHaveAttribute('autocomplete', 'current-password')
    await password.fill('Synthetic1!')
    await expect(submit).toBeDisabled()
    await confirmation.check()
    await submit.scrollIntoViewIfNeeded()
    await expect(submit).toBeEnabled()

    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const appScroll = page.locator('.app-scroll')
    expect(await appScroll.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
    const submitBox = await submit.boundingBox()
    const navigationBox = await page.getByRole('navigation', { name: '주요 메뉴' }).boundingBox()
    expect(submitBox!.y + submitBox!.height).toBeLessThanOrEqual(navigationBox!.y)

    await submit.click()
    await expect(page.getByText('회원탈퇴가 완료되었습니다.')).toBeVisible()
    await expect(page.getByText(/삭제·보존은 서비스 정책에 따라 처리됩니다/)).toBeVisible()
    await expect(page.getByText('withdrawal@example.com')).toHaveCount(0)
    await expect(page.getByRole('navigation', { name: '주요 메뉴' })).toHaveCount(0)
    expect(api.withdrawalRequests).toEqual([{ password: 'Synthetic1!', confirmed: true }])
    expect(api.logoutCount).toBe(0)
    expect(await page.evaluate(() => ({
      token: localStorage.getItem('access_token'),
      chat: sessionStorage.getItem('dosey_chat_session:synthetic-prescription'),
      ocr: sessionStorage.getItem('dosey_ocr_job_recovery:v1'),
      push: localStorage.getItem('dosey_web_push_binding:v1'),
      unrelated: sessionStorage.getItem('unrelated-session'),
    }))).toEqual({ token: null, chat: null, ocr: null, push: null, unrelated: 'keep-me' })

    await page.screenshot({
      path: `test-results/requirements/account-withdrawal-success-${width}.png`,
      fullPage: true,
    })

    await page.getByRole('button', { name: '시작 화면으로 이동' }).click()
    await expect(page).toHaveURL(/\/start$/)
    await page.goto('/profile')
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByText('withdrawal@example.com')).toHaveCount(0)
  })
}

test('회원탈퇴 Gate OFF는 성공이나 local-only 탈퇴로 처리하지 않는다', async ({ page }) => {
  await page.addInitScript((accessToken) => localStorage.setItem('access_token', accessToken), token)
  const api = await installWithdrawalApi(page, 'gate-off')

  await page.goto('/profile')
  await page.getByRole('button', { name: '회원탈퇴', exact: true }).scrollIntoViewIfNeeded()
  await page.getByRole('button', { name: '회원탈퇴', exact: true }).click()
  await page.getByLabel('현재 비밀번호').fill('Synthetic1!')
  await page.getByRole('checkbox', {
    name: '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
  }).check()
  await page.getByRole('button', { name: '회원탈퇴 요청' }).click()

  await expect(page.getByText('회원탈퇴 요청을 현재 처리할 수 없어요.')).toBeVisible()
  await expect(page.getByText('회원탈퇴가 완료되었습니다.')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBe(token)
  expect(api.withdrawalRequests).toEqual([{ password: 'Synthetic1!', confirmed: true }])
  expect(api.logoutCount).toBe(0)
})


test('회원탈퇴 실패 detail 200은 완료 화면으로 소비하지 않는다', async ({ page }) => {
  await page.addInitScript((accessToken) => localStorage.setItem('access_token', accessToken), token)
  const api = await installWithdrawalApi(page, 'failed')

  await page.goto('/profile')
  await page.getByRole('button', { name: '회원탈퇴', exact: true }).scrollIntoViewIfNeeded()
  await page.getByRole('button', { name: '회원탈퇴', exact: true }).click()
  await page.getByLabel('현재 비밀번호').fill('Synthetic1!')
  await page.getByRole('checkbox', {
    name: '회원탈퇴 요청 후 계정을 더 이상 이용할 수 없음을 확인했습니다.',
  }).check()
  await page.getByRole('button', { name: '회원탈퇴 요청' }).click()

  await expect(page.getByText('회원탈퇴 처리를 완료하지 못했어요.')).toBeVisible()
  await expect(page.getByText('관리자 확인이 필요합니다. 완료 화면으로 이동하지 않습니다.')).toBeVisible()
  await expect(page.getByText('회원탈퇴가 완료되었습니다.')).toHaveCount(0)
  await expect(page.getByText('withdrawal@example.com')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull()
  expect(api.withdrawalRequests).toEqual([{ password: 'Synthetic1!', confirmed: true }])
  expect(api.logoutCount).toBe(0)
})
