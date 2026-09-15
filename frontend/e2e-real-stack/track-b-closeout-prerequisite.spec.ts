import { expect, test } from '@playwright/test'

const seededEmail = process.env.TRACK_B_E2E_EMAIL
const seededPassword = process.env.TRACK_B_E2E_PASSWORD

test('[REAL-STACK][Track B prerequisite] synthetic account reaches the real unconfirmed contract without mutation', async ({ page }) => {
  const runId = `${Date.now()}${Math.random().toString(16).slice(2, 6)}`
  const email = `tb-${runId}@example.com`
  const password = 'Synthetic1!'
  let checkinPutCount = 0

  page.on('request', (request) => {
    if (
      request.method() === 'PUT' &&
      /\/api\/v1\/medication-occurrences\/[0-9a-f-]+\/check-in$/i.test(
        new URL(request.url()).pathname,
      )
    ) {
      checkinPutCount += 1
    }
  })

  await page.goto('/signup')
  await page.getByLabel('이름').fill('Real Stack 합성 사용자')
  await page.getByLabel('이메일', { exact: true }).fill(email)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '가입 완료' }).click()
  await expect(page).toHaveURL(/\/login$/)

  await page.getByLabel('이메일', { exact: true }).fill(email)
  await page.getByLabel('비밀번호').fill(password)
  const rediscoveryResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'GET' &&
      url.pathname === '/api/v1/medication-checkins/unconfirmed' &&
      url.searchParams.get('limit') === '1'
  })
  await page.getByRole('button', { name: '로그인' }).click()

  const rediscoveryResponse = await rediscoveryResponsePromise
  expect(rediscoveryResponse.status()).toBe(200)
  const rediscoveryBody = await rediscoveryResponse.json() as {
    data: { items: unknown[]; next_cursor: string | null }
  }
  expect(rediscoveryBody.data.items).toEqual([])
  expect(rediscoveryBody.data.next_cursor).toBeNull()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByText('오늘도 건강한 하루 되세요')).toBeVisible()

  await page.goto('/schedule/unconfirmed')
  await expect(page.getByRole('status').filter({ hasText: '확인할 미확인 기록이 없어요' })).toBeVisible()
  expect(checkinPutCount).toBe(0)
})

test('[REAL-STACK][Track B] seeded backlog supports rediscovery, corrections, defer, and conflict recovery', async ({ page }) => {
  test.skip(!seededEmail || !seededPassword, 'requires an isolated synthetic UNCONFIRMED seed')

  let uiCheckinPutCount = 0
  page.on('request', (request) => {
    if (
      request.method() === 'PUT' &&
      /\/api\/v1\/medication-occurrences\/[0-9a-f-]+\/check-in$/i.test(
        new URL(request.url()).pathname,
      )
    ) {
      uiCheckinPutCount += 1
    }
  })

  await page.goto('/login')
  await page.getByLabel('이메일', { exact: true }).fill(seededEmail!)
  await page.getByLabel('비밀번호').fill(seededPassword!)
  await page.getByRole('button', { name: '로그인' }).click()

  await expect(page).toHaveURL(/\/schedule\/unconfirmed$/)
  await expect(page.locator('.unconfirmed-card')).toHaveCount(3)
  await expect(page.getByText('합성테스트약', { exact: false })).toHaveCount(3)
  await expect(page.getByText('현재합성대체약', { exact: false })).toHaveCount(0)
  expect(uiCheckinPutCount).toBe(0)

  await page.locator('.unconfirmed-card').first()
    .getByRole('button', { name: /복용했어요/ }).click()
  await expect(page.locator('.unconfirmed-card')).toHaveCount(2)
  expect(uiCheckinPutCount).toBe(1)

  await page.locator('.unconfirmed-card').first()
    .getByRole('button', { name: /복용하지 않았어요/ }).click()
  await expect(page.locator('.unconfirmed-card')).toHaveCount(1)
  expect(uiCheckinPutCount).toBe(2)

  await page.getByRole('button', { name: '지금은 확인하기 어려워요' }).click()
  await expect(page).toHaveURL(/\/schedule(?:\?.*)?$/)
  expect(uiCheckinPutCount).toBe(2)

  await page.goto('/schedule/unconfirmed')
  await expect(page.locator('.unconfirmed-card')).toHaveCount(1)
  const token = await page.evaluate(() => localStorage.getItem('access_token'))
  expect(token).toBeTruthy()
  const backlog = await page.request.get(
    'http://127.0.0.1:18000/api/v1/medication-checkins/unconfirmed?limit=20',
    { headers: { Authorization: `Bearer ${token}` } },
  )
  expect(backlog.status()).toBe(200)
  const backlogBody = await backlog.json() as {
    data: { items: Array<{ occurrence_id: string; revision: number }> }
  }
  expect(backlogBody.data.items).toHaveLength(1)
  const remaining = backlogBody.data.items[0]

  const concurrentUpdate = await page.request.put(
    `http://127.0.0.1:18000/api/v1/medication-occurrences/${remaining.occurrence_id}/check-in`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'Idempotency-Key': `track-b-conflict-${Date.now()}`,
      },
      data: { status: 'TAKEN', expected_revision: remaining.revision },
    },
  )
  expect(concurrentUpdate.status()).toBe(200)

  const conflictResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'PUT' &&
    response.status() === 409 &&
    new URL(response.url()).pathname.endsWith(`/${remaining.occurrence_id}/check-in`),
  )
  await page.locator('.unconfirmed-card').first()
    .getByRole('button', { name: /복용하지 않았어요/ }).click()
  const conflictResponse = await conflictResponsePromise
  const conflictBody = await conflictResponse.json() as { code: string }
  expect(conflictBody.code).toBe('CHECKIN_REVISION_CONFLICT')
  await expect(page.getByText('이 기록은 다른 곳에서 이미 보완되어 목록에서 제외됐어요.')).toBeVisible()
  await expect(page.locator('.unconfirmed-card')).toHaveCount(0)
  expect(uiCheckinPutCount).toBe(3)
})
