import { expect, test } from '@playwright/test'

for (const width of [320, 390, 412]) {
  test(`Profile 목적별 조회·철회와 재접근 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript(() => localStorage.setItem('access_token', 'synthetic-profile-token'))
    const purposes = ['OCR', 'GUIDE', 'CHAT', 'NOTIFICATION']
    let consents = purposes.map((purpose) => ({
      purpose, status: 'GRANTED', policy_version: `${purpose}-server-v7`,
      current_policy_version: `${purpose}-server-v7`, is_granted: true,
      granted_at: '2026-09-15T00:00:00Z', withdrawn_at: null as string | null,
      updated_at: '2026-09-15T00:00:00Z',
    }))
    const writes: string[] = []
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (path === '/api/v1/users/me') {
        await route.fulfill({ json: { id: '00000000-0000-4000-8000-000000000097', name: '합성 사용자', email: 'profile@example.com', phone_number: null, birthday: null, gender: null, created_at: '2026-09-15T00:00:00Z' } })
      } else if (path === '/api/v1/users/me/consents' && request.method() === 'GET') {
        await route.fulfill({ json: { data: consents } })
      } else if (path.startsWith('/api/v1/users/me/consents/') && request.method() === 'PUT') {
        const purpose = path.split('/').at(-1)!
        expect(request.postDataJSON()).toEqual({ status: 'WITHDRAWN', policy_version: `${purpose}-server-v7` })
        writes.push(purpose)
        consents = consents.map((item) => item.purpose === purpose ? { ...item, status: 'WITHDRAWN', is_granted: false, withdrawn_at: '2026-09-15T01:00:00Z' } : item)
        await route.fulfill({ json: { data: consents.find((item) => item.purpose === purpose) } })
      } else await route.fulfill({ status: 404, json: {} })
    })
    await page.goto('/profile')
    await expect(page.getByText('현재 동의한 상태입니다.')).toHaveCount(4)
    expect(writes).toEqual([])
    const section = page.getByRole('region', { name: '목적별 동의 관리' })
    await section.scrollIntoViewIfNeeded()
    for (const label of ['처방전 외부 처리', '복약 가이드', '복약 챗봇', '알림']) {
      const button = page.getByRole('button', { name: `${label} 동의 철회`, exact: true })
      await button.scrollIntoViewIfNeeded()
      const box = await button.boundingBox()
      expect(box!.x).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width).toBeLessThanOrEqual(width)
    }
    await page.getByRole('button', { name: '복약 가이드 동의 철회', exact: true }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText('철회한 상태입니다.')).toHaveCount(1)
    await expect(page.getByText('현재 동의한 상태입니다.')).toHaveCount(3)
    expect(writes).toEqual(['GUIDE'])
    await page.reload()
    await expect(page.getByText('철회한 상태입니다.')).toHaveCount(1)
    await expect(page.getByText('현재 동의한 상태입니다.')).toHaveCount(3)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await section.scrollIntoViewIfNeeded()
    await page.screenshot({ path: `test-results/profile-consents-${width}.png`, fullPage: true })
  })
}
