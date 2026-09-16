import { expect, test } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'
const occurrence = '11111111-1111-4111-8111-111111111111'
const planId = '22222222-2222-4222-8222-222222222222'
for (const width of [320, 390, 412]) {
  test(`Track C explicit plan lifecycle at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await installRequirementsApi(page)
    await page.addInitScript(token => localStorage.setItem('access_token', token), syntheticToken)
    const writes: { path: string; body: Record<string, unknown>; key: string | undefined }[] = []
    const safety = { assessment_id: 'safety', medication_checkin_id: 'checkin', checkin_revision: 2, response_level: 'ROUTINE', safety_disposition: 'NORMAL', revision: 1 }
    const barrier = { ...safety, barrier_response_id: 'barrier', safety_assessment_id: 'safety', response_status: 'ANSWERED', barrier_code: 'FORGOT' }
    const config = { schema_version: 'track-c-handler-config-v1', rationale_code: 'FORGOT', parameters: { destination: 'MEDICATION_SCHEDULE_SETUP', prescription_version_medication_id: 'medication' } }
    const plan = { support_action_plan_id: planId, barrier_response_id: 'barrier', support_code: 'REMINDER_SETUP', rule_version: 'rule1', copy_version: 'copy1', action_config_snapshot: config, status: 'ACTIVE', created_at: '2026-09-16T00:00:00Z', completed_at: null, cancelled_at: null }
    await page.route('**/api/v1/**', async route => {
      const request = route.request(); const path = new URL(request.url()).pathname
      if (!/medication-occurrences|safety-assessments|barrier-response|support-action-plans/.test(path)) return route.fallback()
      if (request.method() !== 'GET') writes.push({ path, body: request.postDataJSON(), key: request.headers()['idempotency-key'] })
      let data: unknown
      if (path === '/api/v1/medication-occurrences') data = { occurrences: [{ occurrence_id: occurrence, scheduled_local_date: '2026-09-16', status: 'CLOSED', checkin: { checkin_id: 'checkin', status: 'NOT_TAKEN', revision: 2 } }] }
      else if (path === '/api/v1/safety-assessments') data = safety
      else if (path.endsWith('/barrier-response')) data = barrier
      else if (path.endsWith('/supports')) data = { ...barrier, reason_code: null, supports: [{ support_code: 'REMINDER_SETUP', rule_version: 'rule1', copy_version: 'copy1', priority: 1, rationale_code: 'FORGOT', action_config: config, support_copy: { title: '복약 일정과 알림을 확인해 볼까요?', body: '현재 저장된 일정을 먼저 확인하고 필요한 경우 직접 변경해 주세요. '.repeat(12), confirmation_prompt: '이 방법을 실천 계획으로 저장할까요?', primary_label: '계획으로 저장', secondary_label: '나중에' } }] }
      else if (path.endsWith('/resources')) data = { support_action_plan_id: planId, barrier_code: 'FORGOT', occurrence_id: occurrence, occurrence_local_date: '2026-09-16', prescription_version_medication_id: 'medication', support_copy: { title: '복약 일정과 알림 확인', body: '저장 당시 안내', confirmation_prompt: '확인할까요?', primary_label: '확인', secondary_label: '나중에' } }
      else { if (request.method() === 'PATCH') plan.status = request.postDataJSON().status; data = plan }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data }) })
    })
    await page.goto(`/dev/track-c/occurrences/${occurrence}?date=2026-09-16`)
    await expect(page.getByRole('heading', { name: '현재 불편한 증상이 있나요?' })).toBeVisible()
    await expect(page.getByRole('navigation', { name: '주요 메뉴' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Dosey 도지' })).toBeVisible()
    await page.screenshot({ path: `test-results/track-c-safety-${width}.png`, fullPage: true })
    await page.getByRole('button', { name: '증상은 없어요' }).click()
    await expect(page.getByRole('heading', { level: 1 }).last()).toBeFocused()
    await expect(page.getByRole('radio')).toHaveCount(6)
    await page.getByRole('radio', { name: '깜빡했어요' }).focus()
    await page.keyboard.press('ArrowDown')
    await expect(page.getByRole('radio', { name: '일정이나 외출 때문에 어려웠어요' })).toBeChecked()
    await page.getByRole('radio', { name: '깜빡했어요' }).check()
    expect(await page.locator('.mobile-app').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `test-results/track-c-barrier-${width}.png`, fullPage: true })
    await page.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }).click()
    await expect(page.getByRole('button', { name: '계획으로 저장' })).toBeDisabled()
    expect(await page.locator('main').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `test-results/track-c-offer-${width}.png`, fullPage: true })
    await page.getByRole('checkbox').check(); await page.getByRole('button', { name: '계획으로 저장' }).click()
    await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    await page.screenshot({ path: `test-results/track-c-plan-${width}.png`, fullPage: true })
    await page.reload(); await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    expect(writes.filter(w => w.path.endsWith('support-action-plans'))).toHaveLength(1)
    await page.getByRole('button', { name: '계획 취소하기' }).click()
    await expect(page.getByRole('button', { name: '취소로 저장' })).toBeDisabled()
    await page.getByRole('checkbox', { name: '이 계획을 취소할게요.' }).check()
    await page.getByRole('button', { name: '취소로 저장' }).click()
    await expect(page.getByText('취소됨', { exact: true })).toBeVisible()
    expect(writes).toHaveLength(4); expect(writes.every(w => Boolean(w.key))).toBe(true)
    expect(writes[0].body).toEqual({ medication_checkin_id: 'checkin', checkin_revision: 2, symptom_codes: [], expected_revision: 0 })
    expect(writes[1].body.barrier_code).toBe('FORGOT')
    expect(await page.locator('.mobile-app').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `test-results/track-c-${width}.png`, fullPage: true })
  })
}

for (const [situation, label, supportCode] of [
  ['SCHEDULE_CHANGED', '생활 일정이 바뀌었어요', 'REMINDER_SETUP'],
  ['MEDICATION_NOT_WITH_ME', '약을 가지고 나오지 않았어요', 'ROUTINE_OR_TRAVEL_PLAN'],
] as const) {
  test(`Track C travel choice ${situation}`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 })
    await installRequirementsApi(page)
    await page.addInitScript(token => localStorage.setItem('access_token', token), syntheticToken)
    const safety = { assessment_id: 'safety', medication_checkin_id: 'checkin', checkin_revision: 2, response_level: 'ROUTINE', safety_disposition: 'NORMAL', revision: 1 }
    const barrier = { ...safety, barrier_response_id: 'barrier', safety_assessment_id: 'safety', response_status: 'ANSWERED', barrier_code: 'SCHEDULE_OR_TRAVEL' }
    const config = { schema_version: 'track-c-handler-config-v1', rationale_code: 'SYNTHETIC', parameters: supportCode === 'REMINDER_SETUP' ? { destination: 'MEDICATION_SCHEDULE_SETUP', prescription_version_medication_id: 'medication' } : { content_key: 'ROUTINE_OR_TRAVEL_PLAN' } }
    const copy = { title: supportCode === 'REMINDER_SETUP' ? '현재 복약 일정 확인' : '다음 외출 전 약 챙기기', body: '사용자가 선택한 상황에 맞는 합성 테스트 안내입니다.', confirmation_prompt: '이 계획을 선택할까요?', primary_label: '계획 만들기', secondary_label: '나중에' }
    const plan = { support_action_plan_id: planId, barrier_response_id: 'barrier', support_code: supportCode, rule_version: 'track-c-support-rule-2026-09-16.1', copy_version: 'track-c-support-copy-ko-2026-09-16.1', action_config_snapshot: config, status: 'ACTIVE', created_at: '2026-09-16T00:00:00Z', completed_at: null, cancelled_at: null }
    const creates: Record<string, unknown>[] = []
    await page.route('**/api/v1/**', async route => {
      const request = route.request(); const url = new URL(request.url()); const path = url.pathname
      if (!/medication-occurrences|safety-assessments|barrier-response|support-action-plans/.test(path)) return route.fallback()
      let data: unknown
      if (path === '/api/v1/medication-occurrences') data = { occurrences: [{ occurrence_id: occurrence, scheduled_local_date: '2026-09-16', status: 'CLOSED', checkin: { checkin_id: 'checkin', status: 'NOT_TAKEN', revision: 2 } }] }
      else if (path.endsWith('/safety-assessments')) data = safety
      else if (path.endsWith('/barrier-response')) data = barrier
      else if (path.endsWith('/supports')) {
        expect(url.searchParams.get('travel_situation')).toBe(situation)
        data = { ...barrier, supports: [{ support_code: supportCode, rule_version: plan.rule_version, copy_version: plan.copy_version, priority: 10, rationale_code: 'SYNTHETIC', action_config: config, support_copy: copy }], reason_code: null }
      } else if (path.endsWith('/resources')) data = { support_action_plan_id: planId, barrier_code: 'SCHEDULE_OR_TRAVEL', occurrence_id: occurrence, occurrence_local_date: '2026-09-16', prescription_version_medication_id: 'medication', support_copy: copy }
      else {
        if (request.method() === 'POST') creates.push(request.postDataJSON())
        if (request.method() === 'PATCH') plan.status = request.postDataJSON().status
        data = plan
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data }) })
    })
    await page.goto(`/dev/track-c/occurrences/${occurrence}?date=2026-09-16`)
    await page.getByRole('button', { name: '증상은 없어요' }).click()
    await page.getByRole('radio', { name: '일정이나 외출 때문에 어려웠어요' }).check()
    await page.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }).click()
    await expect(page.getByRole('button', { name: '선택한 상황으로 도움 찾기' })).toBeDisabled()
    await page.getByRole('radio', { name: label }).check()
    await page.screenshot({ path: `test-results/travel-${situation}.png`, fullPage: true })
    await page.getByRole('button', { name: '선택한 상황으로 도움 찾기' }).click()
    expect(creates).toHaveLength(0)
    await page.getByRole('checkbox').check(); await page.getByRole('button', { name: '계획 만들기' }).click()
    await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    expect(creates).toHaveLength(1); expect(creates[0].travel_situation).toBe(situation)
    await page.reload(); await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    if (supportCode === 'REMINDER_SETUP') await expect(page.getByRole('link', { name: '일정 확인·설정 (새 탭)' })).toHaveAttribute('href', '/schedule?support_medication=medication')
    await page.getByRole('button', { name: '완료 확인하기' }).click()
    await expect(page.getByRole('button', { name: '완료로 저장' })).toBeDisabled()
    await page.getByRole('checkbox').check(); await page.getByRole('button', { name: '완료로 저장' }).click()
    await expect(page.getByText('완료됨', { exact: true })).toBeVisible()
    expect(await page.locator('.mobile-app').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `test-results/travel-plan-${situation}.png`, fullPage: true })
  })
}
