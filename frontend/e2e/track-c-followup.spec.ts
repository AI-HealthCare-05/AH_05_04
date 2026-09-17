import { expect, test } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

const planId = '22222222-2222-4222-8222-222222222222'
for (const width of [320, 390, 412]) {
  test(`Track C completed plan followup at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await installRequirementsApi(page)
    await page.addInitScript(token => localStorage.setItem('access_token', token), syntheticToken)
    let saved: null | { followup_id: string; support_action_plan_id: string; response: string; revision: number; created_at: string; updated_at: string } = null
    const writes: { body: Record<string, unknown>; key?: string }[] = []
    await page.route('**/api/v1/support-action-plans/**', async route => {
      const request = route.request(); const path = new URL(request.url()).pathname
      let data: unknown
      if (path.endsWith('/followups')) {
        if (request.method() === 'POST') {
          const body = request.postDataJSON(); writes.push({ body, key: request.headers()['idempotency-key'] })
          expect(body.expected_revision).toBe(saved?.revision ?? 0)
          saved = { followup_id: 'synthetic', support_action_plan_id: planId, response: body.response, revision: (saved?.revision ?? 0) + 1, created_at: '2026-09-16T00:00:00Z', updated_at: '2026-09-16T00:00:00Z' }
        }
        data = saved
      } else if (path.endsWith('/resources')) {
        data = { support_action_plan_id: planId, barrier_code: 'SCHEDULE_OR_TRAVEL', occurrence_id: planId, occurrence_local_date: '2026-09-16', prescription_version_medication_id: 'medication', support_copy: { body: '다음 외출을 위해 준비하는 계획이에요.' } }
      } else {
        expect(request.method()).toBe('GET')
        data = { support_action_plan_id: planId, support_code: 'ROUTINE_OR_TRAVEL_PLAN', copy_version: 'track-c-support-copy-ko-2026-09-16.1', action_config_snapshot: { parameters: { content_key: 'ROUTINE_OR_TRAVEL_PLAN' } }, status: 'COMPLETED' }
      }
      await route.fulfill({ json: { data } })
    })
    await page.goto(`/dev/track-c/plans/${planId}`)
    await page.getByRole('button', { name: '도움 사용 후기' }).click()
    await expect(page.getByRole('heading', { name: '선택한 방법이 도움이 되었나요?' })).toBeFocused()
    await expect(page.getByRole('button', { name: '후기 저장', exact: true })).toBeDisabled()
    await page.getByRole('button', { name: '나중에' }).click()
    expect(writes).toHaveLength(0)
    await page.getByRole('button', { name: '도움 사용 후기' }).click()
    await page.getByRole('radio', { name: '도움이 됐어요', exact: true }).check()
    await page.getByRole('button', { name: '후기 저장', exact: true }).click()
    await expect(page.getByText('저장된 후기: 도움이 됐어요', { exact: true })).toBeVisible()
    await page.reload()
    await page.getByRole('button', { name: '도움 사용 후기' }).click()
    await expect(page.getByText('저장된 후기: 도움이 됐어요', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '후기 수정하기' }).click()
    await page.getByRole('radio', { name: '아직 모르겠어요' }).check()
    await page.screenshot({ path: `test-results/plan-followup-${width}.png`, fullPage: true })
    await page.getByRole('button', { name: '후기 수정 저장' }).click()
    await expect(page.getByText('저장된 후기: 아직 모르겠어요', { exact: true })).toBeVisible()
    expect(writes.map(w => w.body)).toEqual([{ response: 'HELPED', expected_revision: 0 }, { response: 'NOT_SURE', expected_revision: 1 }])
    expect(writes[0].key).toBeTruthy(); expect(writes[1].key).not.toBe(writes[0].key)
    expect(await page.locator('.mobile-app').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  })
}
