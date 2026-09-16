import { expect, test } from '@playwright/test'
import type { MedicationDayResponse } from '../src/api/medicationOccurrences'

// Uses the existing report_round_trip_fixture in a fresh isolated dosey_e2e DB.
// No interception: every request is served by the current FastAPI implementation.
test('[REAL-STACK][Track C #139] create, reload, complete and cancel plans through real APIs', async ({ page, request }) => {
  const api = process.env.TRACK_C_E2E_API_URL
  test.skip(!api || process.env.TRACK_C_E2E_SYNTHETIC !== '1', 'requires an isolated synthetic database')
  const login = await request.post(`${api}/api/v1/auth/login`, { data: { email: 'report-420@example.com', password: 'Synthetic1!' } })
  expect(login.status()).toBe(200)
  const { access_token: token } = await login.json()
  const headers = { Authorization: `Bearer ${token}` }
  const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(new Date())
  const dayResponse = await request.get(`${api}/api/v1/medication-occurrences?date=${date}`, { headers })
  expect(dayResponse.status()).toBe(200)
  const day: MedicationDayResponse = await dayResponse.json()
  const occurrences = day.data.occurrences.slice(0, 2)
  expect(occurrences).toHaveLength(2)
  await page.addInitScript(value => localStorage.setItem('access_token', value), token)

  for (const [index, occurrence] of occurrences.entries()) {
    const correction = await request.put(`${api}/api/v1/medication-occurrences/${occurrence.occurrence_id}/check-in`, {
      headers: { ...headers, 'Idempotency-Key': `track-c-139-checkin-${crypto.randomUUID()}` },
      data: { status: 'NOT_TAKEN', expected_revision: occurrence.checkin?.revision ?? 0 },
    })
    expect(correction.status()).toBe(200)
    await page.goto(`/schedule/occurrences/${occurrence.occurrence_id}?date=${date}`)
    await page.getByRole('button', { name: '이유와 도움 찾기' }).click()
    await page.getByRole('button', { name: '증상이 없어요' }).click()
    await page.getByRole('radio', { name: '깜빡했어요' }).check()
    await page.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }).click()
    await page.getByRole('checkbox').check()
    const creation = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/support-action-plans'))
    await page.locator('.track-c-content .ds-card button').first().click()
    const createdResponse = await creation
    expect(createdResponse.status()).toBe(200)
    const created = (await createdResponse.json()).data
    await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    if (index === 0) {
      const popupPromise = page.waitForEvent('popup')
      await page.getByRole('link', { name: '일정 확인·설정 (새 탭)' }).click()
      const schedule = await popupPromise
      await expect(schedule.getByRole('heading', { name: '실천 계획의 복약 일정' })).toBeVisible()
      await expect(schedule.getByRole('button', { name: '이 약의 일정 확인·설정' })).toBeVisible()
      await schedule.close()
      await page.getByRole('button', { name: '완료 확인하기' }).click()
      await page.getByRole('checkbox', { name: '기존 복약 일정을 확인했거나 일정 저장을 마쳤어요.' }).check()
      await page.getByRole('button', { name: '완료로 저장' }).click()
      await expect(page.getByText('완료됨', { exact: true })).toBeVisible()
    } else {
      await page.getByRole('button', { name: '계획 취소하기' }).click()
      await page.getByRole('checkbox', { name: '이 계획을 취소할게요.' }).check()
      await page.getByRole('button', { name: '취소로 저장' }).click()
      await expect(page.getByText('취소됨', { exact: true })).toBeVisible()
    }
    const savedResponse = await request.get(`${api}/api/v1/support-action-plans/${created.support_action_plan_id}`, { headers })
    expect(savedResponse.status()).toBe(200)
    const saved = (await savedResponse.json()).data
    expect(saved.status).toBe(index === 0 ? 'COMPLETED' : 'CANCELLED')
    expect(saved.action_config_snapshot).toEqual(created.action_config_snapshot)
  }
})
