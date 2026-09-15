import { expect, test, type Page } from '@playwright/test'

const seededEmail = process.env.REPORT_E2E_EMAIL
const seededPassword = process.env.REPORT_E2E_PASSWORD

type ReportRate = {
  numerator: number
  denominator: number
  percentage: number | null
}

type ReportData = {
  counts: {
    taken_count: number
    not_taken_count: number
    unconfirmed_count: number
  }
  adherence_rate: ReportRate
  confirmation_rate: ReportRate
}

function isReportGet(url: string, method: string): boolean {
  const parsed = new URL(url)
  return method === 'GET' &&
    parsed.pathname === '/api/v1/medication-reports' &&
    parsed.searchParams.get('period_days') === '7'
}

async function expectReportMatchesServer(page: Page, report: ReportData) {
  await expect(page.locator('.report-count--taken b')).toHaveText(`${report.counts.taken_count}회`)
  await expect(page.locator('.report-count--not-taken b')).toHaveText(`${report.counts.not_taken_count}회`)
  await expect(page.locator('.report-count--unconfirmed b')).toHaveText(`${report.counts.unconfirmed_count}회`)

  for (const [label, rate] of [
    ['확인된 기록 중 복용률', report.adherence_rate],
    ['기록 확인률', report.confirmation_rate],
  ] as const) {
    const card = page.getByLabel(label)
    await expect(card).toContainText(
      rate.denominator === 0 || rate.percentage === null
        ? '계산할 기록 없음'
        : `${rate.percentage}%`,
    )
    await expect(card).toContainText(`분자 ${rate.numerator} / 분모 ${rate.denominator}`)
  }
}

test('[REAL-STACK][Report #420] explicit correction refreshes the real Backend aggregate after returning', async ({ page }) => {
  test.skip(!seededEmail || !seededPassword, 'requires the isolated synthetic Report seed')

  let checkinPutCount = 0
  let reportGetCount = 0
  page.on('request', (request) => {
    if (isReportGet(request.url(), request.method())) reportGetCount += 1
    if (
      request.method() === 'PUT' &&
      /\/api\/v1\/medication-occurrences\/[0-9a-f-]+\/check-in$/i.test(new URL(request.url()).pathname)
    ) {
      checkinPutCount += 1
    }
  })

  await page.goto('/login')
  await page.getByLabel('이메일', { exact: true }).fill(seededEmail!)
  await page.getByLabel('비밀번호').fill(seededPassword!)
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page).toHaveURL(/\/schedule\/unconfirmed$/)

  const initialReportPromise = page.waitForResponse((response) =>
    isReportGet(response.url(), response.request().method()),
  )
  await page.goto('/report?period=7')
  const initialReportResponse = await initialReportPromise
  expect(initialReportResponse.status()).toBe(200)
  const initialReport = (await initialReportResponse.json() as { data: ReportData }).data
  await expectReportMatchesServer(page, initialReport)
  expect(initialReport.counts).toMatchObject({
    taken_count: 1,
    not_taken_count: 0,
    unconfirmed_count: 1,
  })
  expect(initialReport.adherence_rate).toEqual({
    numerator: 1,
    denominator: 1,
    percentage: 100,
  })
  expect(initialReport.confirmation_rate).toEqual({
    numerator: 1,
    denominator: 2,
    percentage: 50,
  })
  expect(checkinPutCount).toBe(0)

  await page.getByRole('button', { name: '미확인 기록 보완' }).click()
  await expect(page).toHaveURL(/\/schedule\/unconfirmed$/)
  expect(checkinPutCount).toBe(0)

  const correctionResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'PUT' &&
    /\/api\/v1\/medication-occurrences\/[0-9a-f-]+\/check-in$/i.test(new URL(response.url()).pathname),
  )
  await page.locator('.unconfirmed-card').getByRole('button', { name: /복용하지 않았어요/ }).click()
  const correctionResponse = await correctionResponsePromise
  expect(correctionResponse.status()).toBe(200)
  expect(checkinPutCount).toBe(1)
  await expect(correctionResponse.json()).resolves.toMatchObject({
    data: {
      status: 'NOT_TAKEN',
      revision: 2,
    },
  })
  await expect(page.getByRole('status').filter({ hasText: '확인할 미확인 기록이 없어요' })).toBeVisible()

  const refreshedReportPromise = page.waitForResponse((response) =>
    isReportGet(response.url(), response.request().method()),
  )
  await page.goBack()
  await expect(page).toHaveURL(/\/report\?period=7$/)
  const refreshedReportResponse = await refreshedReportPromise
  expect(refreshedReportResponse.status()).toBe(200)
  const refreshedReport = (await refreshedReportResponse.json() as { data: ReportData }).data
  await expectReportMatchesServer(page, refreshedReport)

  expect(refreshedReport.counts).toMatchObject({
    taken_count: 1,
    not_taken_count: 1,
    unconfirmed_count: 0,
  })
  expect(refreshedReport.adherence_rate).toEqual({
    numerator: 1,
    denominator: 2,
    percentage: 50,
  })
  expect(refreshedReport.confirmation_rate).toEqual({
    numerator: 2,
    denominator: 2,
    percentage: 100,
  })
  expect(refreshedReport.adherence_rate).not.toEqual(initialReport.adherence_rate)
  expect(refreshedReport.confirmation_rate).not.toEqual(initialReport.confirmation_rate)
  expect(reportGetCount).toBeGreaterThanOrEqual(2)
  expect(checkinPutCount).toBe(1)
})
