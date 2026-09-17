import { expect, test, type Page } from '@playwright/test'

const seededEmail = process.env.NOTIFICATION_E2E_EMAIL
const seededPassword = process.env.NOTIFICATION_E2E_PASSWORD
const apiBaseUrl = process.env.REAL_STACK_API_URL ?? 'http://127.0.0.1:18000'

type NotificationItem = {
  id: string
  occurrence_id: string
  occurrence_local_date: string
  kind: 'SCHEDULED' | 'REMINDER'
  read_at: string | null
}

type OccurrenceItem = {
  occurrence_id: string
  prescription_version_id: string
  prescription_version_medication_id: string
  scheduled_local_date: string
  checkin: unknown | null
}

type MedicationDetail = {
  occurrence_id: string
  prescription_version_id: string
  prescription_version_medication_id: string
  medication_name: string
}

async function login(page: Page) {
  await page.goto('/login')
  await page.getByLabel('이메일', { exact: true }).fill(seededEmail!)
  await page.getByLabel('비밀번호').fill(seededPassword!)
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByText('오늘도 건강한 하루 되세요')).toBeVisible()
}

function kstDateAfter(days: number): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(Date.now() + days * 24 * 60 * 60 * 1000))
}

test('[REAL-STACK][Notification #421] unread/read handoff preserves historical identity without Check-in mutation', async ({ page }) => {
  test.skip(!seededEmail || !seededPassword, 'requires the isolated synthetic Notification seed')

  let notificationGetCount = 0
  let readPatchCount = 0
  let checkinMutationCount = 0
  let currentPrescriptionGetCount = 0
  const occurrenceDates: string[] = []
  const medicationOccurrenceIds: string[] = []

  page.on('request', (request) => {
    const url = new URL(request.url())
    if (request.method() === 'GET' && url.pathname === '/api/v1/notifications') {
      notificationGetCount += 1
    }
    if (
      request.method() === 'PATCH' &&
      /\/api\/v1\/notifications\/[0-9a-f-]+\/read$/i.test(url.pathname)
    ) {
      readPatchCount += 1
    }
    if (
      request.method() !== 'GET' &&
      (/\/api\/v1\/medication-occurrences\/[0-9a-f-]+\/check-in(?:\/|$)/i.test(url.pathname) ||
        /\/api\/v1\/medication-checkins(?:\/|$)/i.test(url.pathname))
    ) {
      checkinMutationCount += 1
    }
    if (request.method() === 'GET' && url.pathname === '/api/v1/prescriptions/latest') {
      currentPrescriptionGetCount += 1
    }
    if (request.method() === 'GET' && url.pathname === '/api/v1/medication-occurrences') {
      const date = url.searchParams.get('date')
      if (date) occurrenceDates.push(date)
    }
    const medicationMatch = url.pathname.match(
      /^\/api\/v1\/medication-occurrences\/([0-9a-f-]+)\/medication$/i,
    )
    if (request.method() === 'GET' && medicationMatch) {
      medicationOccurrenceIds.push(medicationMatch[1])
    }
  })

  await login(page)
  const token = await page.evaluate(() => localStorage.getItem('access_token'))
  expect(token).toBeTruthy()
  const authorization = { Authorization: `Bearer ${token}` }
  const current = await page.request.get(`${apiBaseUrl}/api/v1/prescriptions/latest`, {
    headers: authorization,
  })
  expect(current.status()).toBe(200)
  const currentBody = await current.json() as {
    data: {
      prescription_version_id: string
      medications: Array<{ prescription_version_medication_id: string; medication_name: string }>
    }
  }

  const notificationResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'GET' &&
    new URL(response.url()).pathname === '/api/v1/notifications',
  )
  await page.goto('/notifications')
  const notificationResponse = await notificationResponsePromise
  expect(notificationResponse.status()).toBe(200)
  const notificationBody = await notificationResponse.json() as {
    data: { items: NotificationItem[] }
  }
  expect(notificationBody.data.items).toHaveLength(1)
  const notification = notificationBody.data.items[0]
  expect(notification.read_at).toBeNull()
  expect(notification.kind).toBe('SCHEDULED')

  const unreadButton = page.getByRole('button', {
    name: `복약 알림, 복약일 ${notification.occurrence_local_date}, 읽지 않음`,
  })
  await expect(unreadButton).toBeVisible()

  const readResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'PATCH' &&
    new URL(response.url()).pathname === `/api/v1/notifications/${notification.id}/read`,
  )
  const dayResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'GET' &&
      url.pathname === '/api/v1/medication-occurrences' &&
      url.searchParams.get('date') === notification.occurrence_local_date
  })
  const medicationResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'GET' &&
    new URL(response.url()).pathname ===
      `/api/v1/medication-occurrences/${notification.occurrence_id}/medication`,
  )
  await unreadButton.click()

  const [readResponse, dayResponse, medicationResponse] = await Promise.all([
    readResponsePromise,
    dayResponsePromise,
    medicationResponsePromise,
  ])
  expect(readResponse.status()).toBe(200)
  expect(dayResponse.status()).toBe(200)
  expect(medicationResponse.status()).toBe(200)
  await expect(page).toHaveURL(
    new RegExp(
      `/schedule/occurrences/${notification.occurrence_id}\\?date=${notification.occurrence_local_date}$`,
    ),
  )
  await expect(page.getByRole('heading', { name: '복약 기록' })).toBeFocused()

  const dayBody = await dayResponse.json() as { data: { occurrences: OccurrenceItem[] } }
  const occurrence = dayBody.data.occurrences.find(
    (item) => item.occurrence_id === notification.occurrence_id,
  )
  expect(occurrence).toBeDefined()
  expect(occurrence?.scheduled_local_date).toBe(notification.occurrence_local_date)
  expect(occurrence?.checkin).toBeNull()
  const medicationBody = await medicationResponse.json() as { data: MedicationDetail }
  expect(medicationBody.data).toMatchObject({
    occurrence_id: occurrence?.occurrence_id,
    prescription_version_id: occurrence?.prescription_version_id,
    prescription_version_medication_id: occurrence?.prescription_version_medication_id,
    medication_name: '합성 과거 처방약',
  })
  expect(medicationBody.data.prescription_version_id).not.toBe(
    currentBody.data.prescription_version_id,
  )
  expect(currentBody.data.medications).toEqual([
    expect.objectContaining({ medication_name: '합성 현재 처방약' }),
  ])
  expect(currentBody.data.medications.map((item) => item.prescription_version_medication_id))
    .not.toContain(medicationBody.data.prescription_version_medication_id)
  await expect(page.getByRole('heading', { name: '합성 과거 처방약' })).toBeVisible()
  await expect(page.getByText('합성 현재 처방약')).toHaveCount(0)

  expect(notificationGetCount).toBeGreaterThanOrEqual(1)
  expect(readPatchCount).toBe(1)
  expect(occurrenceDates).toContain(notification.occurrence_local_date)
  expect(medicationOccurrenceIds).toContain(notification.occurrence_id)
  expect(currentPrescriptionGetCount).toBe(0)
  expect(checkinMutationCount).toBe(0)

  const persisted = await page.request.get(`${apiBaseUrl}/api/v1/notifications`, {
    headers: authorization,
  })
  expect(persisted.status()).toBe(200)
  const persistedBody = await persisted.json() as { data: { items: NotificationItem[] } }
  expect(persistedBody.data.items.find((item) => item.id === notification.id)?.read_at)
    .not.toBeNull()

  const readPatchCountBeforeAlreadyRead = readPatchCount
  await page.goto('/notifications')
  const readButton = page.getByRole('button', {
    name: `복약 알림, 복약일 ${notification.occurrence_local_date}, 읽음`,
  })
  await expect(readButton).toBeVisible()
  const secondMedicationResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'GET' &&
    new URL(response.url()).pathname ===
      `/api/v1/medication-occurrences/${notification.occurrence_id}/medication`,
  )
  await readButton.click()
  expect((await secondMedicationResponsePromise).status()).toBe(200)
  await expect(page).toHaveURL(
    new RegExp(
      `/schedule/occurrences/${notification.occurrence_id}\\?date=${notification.occurrence_local_date}$`,
    ),
  )
  await expect(page.getByRole('heading', { name: '합성 과거 처방약' })).toBeVisible()
  expect(readPatchCount - readPatchCountBeforeAlreadyRead).toBe(0)
  expect(currentPrescriptionGetCount).toBe(0)
  expect(checkinMutationCount).toBe(0)
})

test('[REAL-STACK][Schedule #699] full save exits editor and immediately renders generated occurrence', async ({ page }) => {
  test.skip(!seededEmail || !seededPassword, 'requires the isolated synthetic Notification seed')

  const targetDate = kstDateAfter(2)
  await login(page)
  await page.goto(`/schedule?date=${targetDate}`)
  await page.getByRole('button', { name: '일정 설정하기' }).click()
  await expect(page.getByRole('heading', { name: '합성 현재 처방약' })).toBeVisible()

  await page.getByRole('radio', { name: '계속 복용' }).click()
  await page.getByLabel('합성 현재 처방약 복용 시작일').fill(targetDate)
  await page.getByLabel('합성 현재 처방약 1번째 복용 시간').fill('09:00')

  const putResponse = page.waitForResponse((response) =>
    response.request().method() === 'PUT' &&
    /\/api\/v1\/prescription-version-medications\/[0-9a-f-]+\/schedule$/i.test(new URL(response.url()).pathname),
  )
  await page.getByRole('button', { name: '복약 일정 저장하기' }).click()
  expect((await putResponse).status()).toBe(200)

  await expect(page.getByRole('heading', { name: '오늘의 복약' })).toBeVisible()
  await expect(page.getByText('합성 현재 처방약', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '복용 여부 기록하기' })).toHaveCount(1)
  await expect(page.getByRole('heading', { name: '복용할 날짜와 시간을 확인해 주세요' })).toHaveCount(0)
})
