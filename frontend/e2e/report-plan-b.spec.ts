import { expect, test, type Page, type Route } from '@playwright/test'

const syntheticToken = 'synthetic-report-e2e-token'

function fulfillJson(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify(body),
  })
}

async function installReportApi(page: Page) {
  let reportRequests = 0

  await page.route('**/api/v1/users/me', (route) => fulfillJson(route, {
    id: '11111111-1111-4111-8111-111111111111',
    name: '합성 리포트 사용자',
    email: 'synthetic-report@example.com',
    phone_number: null,
    birthday: null,
    gender: null,
    created_at: '2026-09-15T00:00:00Z',
  }))

  await page.route('**/api/v1/medication-reports?*', (route) => {
    reportRequests += 1
    const periodDays = Number(new URL(route.request().url()).searchParams.get('period_days')) as 7 | 30
    return fulfillJson(route, {
      data: {
        period_days: periodDays,
        start_date: periodDays === 7 ? '2026-09-09' : '2026-08-17',
        end_date: '2026-09-15',
        timezone: 'Asia/Seoul',
        as_of: '2026-09-15T03:00:00Z',
        counts: {
          taken_count: 12,
          not_taken_count: 2,
          unconfirmed_count: 1,
          pending_count: 0,
          cancelled_count: 0,
        },
        overdue_pending_count: 0,
        adherence_rate: { numerator: 123456789, denominator: 987654321, percentage: 85.7 },
        confirmation_rate: { numerator: 987654320, denominator: 987654321, percentage: 93.3 },
        records: [{
          occurrence_id: '22222222-2222-4222-8222-222222222222',
          prescription_version_id: '33333333-3333-4333-8333-333333333333',
          prescription_version_medication_id: '44444444-4444-4444-8444-444444444444',
          scheduled_local_date: '2026-09-15',
          scheduled_at: '2026-09-15T00:00:00Z',
          confirmation_deadline_at: '2026-09-15T04:00:00Z',
          status: 'CLOSED',
          updated_at: '2026-09-15T03:00:00Z',
          checkin: {
            checkin_id: '55555555-5555-4555-8555-555555555555',
            occurrence_id: '22222222-2222-4222-8222-222222222222',
            status: 'TAKEN',
            taken_at: '2026-09-15T00:00:00Z',
            revision: 1,
            corrected: false,
            updated_at: '2026-09-15T03:00:00Z',
          },
        }],
      },
    })
  })

  return { get reportRequests() { return reportRequests } }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
  }, syntheticToken)
})

test('[REPORT-420] 7일·30일과 진료 보기가 같은 서버 집계를 표시한다', async ({ page }) => {
  const api = await installReportApi(page)
  await page.goto('/report')

  await expect(page.getByText('85.7%')).toBeVisible()
  await expect(page.getByText('93.3%')).toHaveCount(0)
  await expect(page.getByText('12회', { exact: true })).toBeVisible()
  await expect(page.getByText('2회', { exact: true })).toBeVisible()
  await expect(page.getByText('1회', { exact: true })).toBeVisible()
  await expect(page.getByLabel('확인된 기록 중 복용률')).toContainText('분자 123456789 / 분모 987654321')
  await expect(page.getByRole('button', { name: '7일' })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: '7일' }).press('Tab')
  await expect(page.getByRole('button', { name: '30일' })).toBeFocused()
  expect(await page.getByRole('button', { name: '30일' }).evaluate((element) => element.matches(':focus-visible'))).toBe(true)

  await page.getByRole('button', { name: '30일' }).click()
  await expect(page).toHaveURL(/period=30/)
  await expect(page.getByRole('button', { name: '30일' })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: '진료 시 보여주기' }).click()
  await expect(page).toHaveURL(/\/report\/clinic\?period=30/)
  await expect(page.getByText('85.7%')).toBeVisible()
  await expect(page.getByText('93.3%')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '미확인 기록 보완' })).toHaveCount(0)
  expect(api.reportRequests).toBeGreaterThanOrEqual(2)
})

test('[REPORT-420] 320·390·412px에서 수치와 컨트롤이 가로로 넘치지 않는다', async ({ page }) => {
  await installReportApi(page)

  for (const width of [320, 390, 412]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/report')
    await expect(page.getByText('85.7%')).toBeVisible()
    await expect(page.getByRole('button', { name: '7일' })).toBeVisible()
    await expect(page.getByRole('button', { name: '30일' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)

    await page.goto('/report/clinic')
    await expect(page.getByRole('heading', { name: '핵심 요약' }),).toBeVisible()
    await expect(page.getByText('85.7%')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  }
})
