import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  IDEMPOTENCY_KEY_MAX_LENGTH,
  IDEMPOTENCY_KEY_MIN_LENGTH,
  IDEMPOTENCY_KEY_PATTERN,
  createIdempotencyKey,
  isValidIdempotencyKey,
} from '../src/api/idempotency'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Idempotency-Key 형식', () => {
  it('backend/app/core/utils/idempotency.py 의 문자 집합·길이와 같은 값을 노출한다', () => {
    expect(IDEMPOTENCY_KEY_PATTERN.source).toBe('^[A-Za-z0-9._:-]+$')
    expect(IDEMPOTENCY_KEY_MIN_LENGTH).toBe(16)
    expect(IDEMPOTENCY_KEY_MAX_LENGTH).toBe(255)
  })

  it('생성된 키가 형식 정규식과 길이 범위를 통과한다', () => {
    const key = createIdempotencyKey('checkin')

    expect(key).toMatch(IDEMPOTENCY_KEY_PATTERN)
    expect(key.length).toBeGreaterThanOrEqual(IDEMPOTENCY_KEY_MIN_LENGTH)
    expect(key.length).toBeLessThanOrEqual(IDEMPOTENCY_KEY_MAX_LENGTH)
    expect(isValidIdempotencyKey(key)).toBe(true)
  })

  it('기존 OCR 키 관행과 같은 `<prefix>:<uuid>` 형태로 만든다', () => {
    const uuid = '11111111-1111-4111-8111-111111111111'
    vi.stubGlobal('crypto', { randomUUID: () => uuid })

    expect(createIdempotencyKey('checkin')).toBe(`checkin:${uuid}`)
  })

  it('호출마다 서로 다른 키를 만든다', () => {
    expect(createIdempotencyKey('checkin')).not.toBe(
      createIdempotencyKey('checkin'),
    )
  })

  it('허용되지 않는 문자가 섞이면 거절한다', () => {
    expect(isValidIdempotencyKey('checkin/1111-1111-1111')).toBe(false)
    expect(isValidIdempotencyKey('checkin 1111 1111 1111')).toBe(false)
    expect(isValidIdempotencyKey('checkin:한글1111111111')).toBe(false)
    expect(isValidIdempotencyKey('checkin:1111111111\n')).toBe(false)
  })

  it('16자 미만과 255자 초과를 거절한다', () => {
    expect(isValidIdempotencyKey('')).toBe(false)
    expect(isValidIdempotencyKey('a'.repeat(IDEMPOTENCY_KEY_MIN_LENGTH - 1))).toBe(
      false,
    )
    expect(isValidIdempotencyKey('a'.repeat(IDEMPOTENCY_KEY_MIN_LENGTH))).toBe(
      true,
    )
    expect(isValidIdempotencyKey('a'.repeat(IDEMPOTENCY_KEY_MAX_LENGTH))).toBe(
      true,
    )
    expect(isValidIdempotencyKey('a'.repeat(IDEMPOTENCY_KEY_MAX_LENGTH + 1))).toBe(
      false,
    )
  })

  it('접두사 때문에 형식을 벗어나면 조용히 보내지 않고 실패한다', () => {
    expect(() => createIdempotencyKey('check in')).toThrow(
      /Idempotency-Key/,
    )
  })
})
