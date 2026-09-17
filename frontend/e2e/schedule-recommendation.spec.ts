import { expect, test } from '@playwright/test'
import { ids, syntheticToken } from './fixtures/requirementsApi'

for (const width of [320, 390]) {
  test(`${width}px explicit prescription candidate requires apply and save`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript((token) => localStorage.setItem('access_token', token), syntheticToken)
    const writes: unknown[] = []
    await page.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const json = (data: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) })
      if (path === '/api/v1/users/me') return json({ id: ids.user, name: '합성 사용자' })
      if (path === '/api/v1/medication-occurrences') return json({ data: {
        schedule_status: 'SETUP_REQUIRED', occurrences: [], schedule_items: [{
          prescription_version_medication_id: ids.prescriptionVersionMedication,
          schedule_item_status: 'SETUP_REQUIRED', schedule_id: null, revision: null, setup_reason: 'MISSING_START_DATE',
        }],
      } })
      if (path === '/api/v1/prescriptions/latest') return json({ data: {
        prescription_id: ids.prescription, prescription_version_id: ids.prescriptionVersion,
        revision: 1, current: true, document_id: ids.document, prescribed_date: '2026-09-17', confirmed_at: '2026-09-17T00:00:00Z',
        medications: [{ prescription_version_medication_id: ids.prescriptionVersionMedication,
          medication_name: '합성 검증약', strength_text: null, dose_value: 1, dose_unit: '정',
          frequency_per_day: 1, timing_text: '저녁 식후 30분', duration_days: 7, display_order: 0 }],
      } })
      if (path.endsWith('/schedule-recommendation')) return json({ data: {
        prescription_version_medication_id: ids.prescriptionVersionMedication, prescription_version_id: ids.prescriptionVersion,
        rule_version: 'explicit-after-meal-v1', timing_text: '저녁 식후 30분', local_times: ['20:00'], reason: 'EXPLICIT_AFTER_MEAL',
      } })
      if (path.endsWith('/schedule') && route.request().method() === 'PUT') {
        writes.push(route.request().postDataJSON())
        return json({ data: {} })
      }
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/schedule?date=2026-09-17')
    await page.getByRole('button', { name: '일정 설정하기' }).click()
    await page.getByLabel('합성 검증약 저녁 식사 종료 시각').fill('19:30')
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: '시간 후보 계산' }).click()
    await expect(page.getByText('20:00', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '후보 적용' }).click()
    await expect(page.getByLabel('합성 검증약 1번째 복용 시간')).toHaveValue('20:00')
    expect(writes).toHaveLength(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.locator('.schedule-recommendation').screenshot({ path: testInfo.outputPath(`candidate-${width}.png`) })
    await page.getByLabel('합성 검증약 복용 시작일').fill('2026-09-17')
    await page.getByLabel('합성 검증약 복용 종료일').fill('2026-09-23')
    await page.getByRole('button', { name: '복약 일정 저장하기' }).click()
    await expect.poll(() => writes.length).toBe(1)
    expect(writes[0]).toMatchObject({ local_times: ['20:00'], expected_revision: 0,
      recommendation_context: { meal_end_times: { DINNER: '19:30' }, same_times_every_day: true, rule_version: 'explicit-after-meal-v1' } })
  })
}
