// `Idempotency-Key` 헤더의 정본 계약은 backend/app/core/utils/idempotency.py 입니다.
// 서버는 `\A[A-Za-z0-9\-._:]{16,255}\Z` 로 검증하므로 문자 집합과 길이를 함께 고정합니다.
// OpenAPI schema 의 `pattern` 은 문자 집합만 표현하고 길이는 `minLength`/`maxLength` 로
// 분리돼 있으므로, pattern 하나만 옮기면 서버가 거절하는 짧은 키를 통과시키게 됩니다.
//
// 키 원문은 저장하지 않습니다. localStorage·sessionStorage·로그에 남기지 않고 요청 1건의
// lifecycle(최초 시도와 그 재시도) 안에서만 메모리로 유지합니다. 서버도 원문 대신
// versioned HMAC 만 보관합니다.

export const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]+$/
export const IDEMPOTENCY_KEY_MIN_LENGTH = 16
export const IDEMPOTENCY_KEY_MAX_LENGTH = 255

export function isValidIdempotencyKey(key: string): boolean {
  return (
    key.length >= IDEMPOTENCY_KEY_MIN_LENGTH &&
    key.length <= IDEMPOTENCY_KEY_MAX_LENGTH &&
    IDEMPOTENCY_KEY_PATTERN.test(key)
  )
}

/**
 * `<prefix>:<uuid>` 형태의 키를 만듭니다.
 *
 * 접두사 뒤에 `crypto.randomUUID()` 를 붙이는 방식은 기존 OCR 접수
 * (PrescriptionUploadPage.tsx 의 `createOcrIdempotencyKey`) 관행과 같습니다.
 *
 * 같은 논리적 시도의 재시도는 반드시 같은 키를 다시 써야 하므로, 호출자는 재시도마다
 * 새 키를 만들지 말고 최초 생성한 값을 그대로 전달해야 합니다.
 */
export function createIdempotencyKey(prefix: string): string {
  const key = `${prefix}:${globalThis.crypto.randomUUID()}`

  // 서버가 400 으로 거절할 키를 조용히 보내지 않고 호출 지점에서 바로 드러냅니다.
  if (!isValidIdempotencyKey(key)) {
    throw new Error('Generated Idempotency-Key does not satisfy the contract format')
  }

  return key
}
