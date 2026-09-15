import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { expect, test, type Page } from '@playwright/test'

const apiOn = 'http://127.0.0.1:18432/api/v1/auth'
const apiOff = 'http://127.0.0.1:18434/api/v1/auth'
const password = 'Synthetic1!'
const name = '합성 인증 사용자'
const emailAddress = () => `gate-${randomUUID().slice(0, 8)}@example.com`
const payload = (email: string) => ({ name, email, password, consents: [] })

function database(sql: string): string {
  const container = process.env.SIGNUP_GATE_DB_CONTAINER ?? ''
  if (process.env.SIGNUP_GATE_E2E !== '1' || !/^dosey-signup-gate-[a-zA-Z0-9-]+$/.test(container)) {
    throw new Error('Only the runner-owned temporary database is allowed.')
  }
  return execFileSync('docker', ['exec', '-i', container, 'psql', '-U', 'signup_e2e',
    '-d', 'signup_gate_e2e', '-v', 'ON_ERROR_STOP=1', '-At'], { input: sql, encoding: 'utf8' }).trim()
}

function expireVerification(email: string) {
  if (!/^gate-[a-z0-9-]+@example\.com$/.test(email)) throw new Error('Synthetic email required.')
  // Model the passage of time without changing the gate or adding a test-only HTTP endpoint.
  database(`UPDATE email_verification_token SET expires_at = now() - interval '1 minute',
    created_at = now() - interval '31 minutes' WHERE email = '${email}' AND purpose = 'SIGNUP';`)
}

async function fillForm(page: Page, email: string) {
  await page.goto('/signup')
  await page.getByLabel('이름').fill(name)
  await page.getByLabel('이메일', { exact: true }).fill(email)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('checkbox', { name: '필수 약관에 동의합니다' }).check()
}

async function requestToken(page: Page) {
  const responsePromise = page.waitForResponse(`${apiOn}/email-verification/request`)
  await page.getByRole('button', { name: '인증 요청', exact: true }).click()
  const response = await responsePromise
  expect(response.ok()).toBe(true)
  // Only LOCAL returns the synthetic token. Production UI never reads this field.
  const token = (await response.json()).verification_token
  if (typeof token !== 'string' || !token) throw new Error('LOCAL test token was not issued.')
  return token
}

async function verifyEmail(page: Page) {
  const token = await requestToken(page)
  await page.getByLabel('이메일 인증 코드').fill(token)
  await page.getByRole('button', { name: '인증 확인', exact: true }).click()
  await expect(page.getByText('이메일 인증이 완료되었습니다.')).toBeVisible()
}

test('gate ON rejects unverified signup; gate OFF accepts it without consent rows', async ({ page, request }) => {
  const email = emailAddress()
  const rejected = await request.post(`${apiOn}/signup`, { data: payload(email) })
  expect(rejected.status()).toBe(409)
  expect((await rejected.json()).code).toBe('EMAIL_VERIFICATION_REQUIRED')
  expect(database(`SELECT count(*) FROM "user" WHERE email = '${email}';`)).toBe('0')

  await fillForm(page, email)
  let signupRequests = 0
  page.on('request', (r) => { if (r.url() === `${apiOn}/signup`) signupRequests++ })
  await page.getByRole('button', { name: '가입 완료' }).click()
  await expect(page.getByText('회원가입 전에 이메일 인증을 완료해 주세요.')).toBeVisible()
  await expect(page.getByRole('button', { name: '인증 요청', exact: true })).toBeFocused()
  expect(signupRequests).toBe(0)

  const accepted = await request.post(`${apiOff}/signup`, { data: payload(email) })
  expect(accepted.status()).toBe(201)
  expect(database(`SELECT count(*) FROM user_consent c JOIN "user" u ON u.id = c.user_id WHERE u.email = '${email}';`)).toBe('0')
})

test('expired verified record returns 409, resets UI, and permits reverified signup with selected purposes', async ({ page }) => {
  const email = emailAddress()
  await fillForm(page, email)
  await verifyEmail(page)
  await page.getByRole('checkbox', { name: /복약 안내/ }).check()
  await page.getByRole('checkbox', { name: /도지에게 질문/ }).check()
  expireVerification(email)

  const rejectedPromise = page.waitForResponse(`${apiOn}/signup`)
  await page.getByRole('button', { name: '가입 완료' }).click()
  const rejected = await rejectedPromise
  expect(rejected.status()).toBe(409)
  expect((await rejected.json()).code).toBe('EMAIL_VERIFICATION_REQUIRED')
  await expect(page.getByText('이메일 인증이 필요하거나 인증 유효 시간이 지났습니다. 인증 안내를 다시 요청하고 인증을 완료해 주세요.')).toBeVisible()
  await expect(page.getByText('이메일 인증이 완료되었습니다.')).toHaveCount(0)
  await expect(page.getByLabel('이메일 인증 코드')).toHaveValue('')
  await expect(page.getByRole('button', { name: '인증 요청', exact: true })).toBeFocused()
  expect(database(`SELECT count(*) FROM "user" WHERE email = '${email}';`)).toBe('0')

  await verifyEmail(page)
  const acceptedPromise = page.waitForResponse(`${apiOn}/signup`)
  await page.getByRole('button', { name: '가입 완료' }).click()
  const accepted = await acceptedPromise
  expect(accepted.status()).toBe(201)
  expect(accepted.request().postDataJSON()).toEqual({
    name, email, password, consents: [{ purpose: 'GUIDE' }, { purpose: 'CHAT' }],
  })
  await expect(page).toHaveURL(/\/login$/)
  expect(database(`SELECT c.purpose || ':' || c.status FROM user_consent c
    JOIN "user" u ON u.id = c.user_id WHERE u.email = '${email}' ORDER BY c.purpose;`)).toBe('CHAT:GRANTED\nGUIDE:GRANTED')
})

test('verified browser signup succeeds with zero optional consents', async ({ page }) => {
  const email = emailAddress()
  await fillForm(page, email)
  await verifyEmail(page)
  const responsePromise = page.waitForResponse(`${apiOn}/signup`)
  await page.getByRole('button', { name: '가입 완료' }).click()
  const response = await responsePromise
  expect(response.status()).toBe(201)
  expect(response.request().postDataJSON()).toEqual(payload(email))
  await expect(page).toHaveURL(/\/login$/)
  expect(database(`SELECT count(*) FROM user_consent c JOIN "user" u ON u.id = c.user_id WHERE u.email = '${email}';`)).toBe('0')
})

test('duplicate signup stays an email conflict after verification', async ({ page, request }) => {
  const email = emailAddress()
  await fillForm(page, email)
  await verifyEmail(page)
  // Another signup completes after verification, before this tab submits.
  expect((await request.post(`${apiOn}/signup`, { data: payload(email) })).status()).toBe(201)
  const conflictPromise = page.waitForResponse(`${apiOn}/signup`)
  await page.getByRole('button', { name: '가입 완료' }).click()
  const conflict = await conflictPromise
  expect(conflict.status()).toBe(409)
  expect(await conflict.json()).toMatchObject({
    code: 'CONFLICT', details: [{ field: 'email', reason: 'ALREADY_EXISTS' }],
  })
  await expect(page.getByLabel('이메일', { exact: true })).toBeFocused()
  await expect(page.getByLabel('이메일', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await expect(page.getByText('이메일 인증이 완료되었습니다.')).toBeVisible()
  expect(database(`SELECT count(*) FROM user_consent c JOIN "user" u ON u.id = c.user_id WHERE u.email = '${email}';`)).toBe('0')
})
