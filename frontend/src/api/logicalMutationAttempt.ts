export type LogicalMutationAttempt<TOperation extends string, TPayload> = {
  operation: TOperation
  targetId: string
  requestPayload: TPayload
  expectedRevision: number
  idempotencyKey: string
}

/**
 * 네트워크 응답 유실 뒤 같은 논리적 저장을 재시도할 때 기존 멱등성 키를 보존합니다.
 * 대상, 본문, revision 중 하나라도 바뀌면 새로운 논리적 시도로 취급합니다.
 */
export function resolveLogicalMutationAttempt<
  TOperation extends string,
  TPayload,
>(
  current: LogicalMutationAttempt<string, unknown> | null,
  operation: TOperation,
  targetId: string,
  requestPayload: TPayload,
  expectedRevision: number,
  createIdempotencyKey: () => string,
): LogicalMutationAttempt<TOperation, TPayload> {
  if (
    current?.operation === operation &&
    current.targetId === targetId &&
    current.expectedRevision === expectedRevision &&
    JSON.stringify(current.requestPayload) === JSON.stringify(requestPayload)
  ) {
    return {
      operation,
      targetId,
      requestPayload,
      expectedRevision,
      idempotencyKey: current.idempotencyKey,
    }
  }

  return {
    operation,
    targetId,
    requestPayload,
    expectedRevision,
    idempotencyKey: createIdempotencyKey(),
  }
}
