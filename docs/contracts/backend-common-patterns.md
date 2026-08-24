# Backend 공통 구현 규칙

## 목적

Backend 담당자가 기능을 나누어 구현하더라도 사용자 리소스 소유권 확인과 외부 호출 실패 상태 저장을 같은 기준으로 적용할 수 있도록 공통 구현 규칙을 정리합니다.

공통 오류 응답 형식과 오류 코드는 [`backend-error-response.md`](./backend-error-response.md)를 기준으로 합니다.

이 문서의 패턴은 현재 MVP 구현(동기 요청 처리, 단일 DB 세션) 기준입니다. Post-MVP에서 공통 비동기 Job 기반으로 전환되면 실패 상태 저장·트랜잭션 경계가 바뀌므로, 새 기능에 그대로 적용하기 전에 Post-MVP 계획과 맞는지 먼저 확인합니다.

> **문서 성격**: 소유권 확인과 실패 상태 저장은 사용자 리소스 보호와 외부에서 관찰되는 상태에 영향을 주는 공통 Backend 규칙입니다. DI Provider와 Repository 테스트 패턴은 Backend 내부 구현 가이드 성격이므로 이번 계약 문서 범위에서 제외합니다.

## 1. 사용자 리소스 소유권 확인

### 1.1 왜 필요한가

여기서 소유권은 누가 코드를 작성하는지가 아니라 어떤 회원에게 데이터가 귀속되어 있는지를 의미합니다.

로그인 여부를 확인하는 것과 해당 사용자가 특정 리소스에 접근해도 되는지 확인하는 것은 다릅니다.

- **인증(Authentication)**: 요청을 보낸 사람이 로그인한 사용자인지 확인합니다.
- **인가(Authorization) / 소유권 확인**: 로그인한 사용자가 해당 리소스의 소유자인지 확인합니다.

인증만 확인하고 소유권 확인을 생략하면 요청 경로의 `document_id`나 `guide_id`를 다른 사람의 ID로 바꾸어 의료정보에 접근하는 IDOR 취약점이 발생할 수 있습니다.

예를 들어 사용자 A가 업로드한 처방전·OCR 결과·복약 가이드·채팅 세션을 사용자 B가 리소스 ID만 알고 조회·수정·삭제할 수 없어야 합니다.

### 1.2 기본 원칙

- 사용자 리소스의 조회·수정·삭제 API는 소유권을 확인합니다.
- 사용자 리소스를 생성할 때도 요청에 포함된 상위 리소스의 소유권을 확인합니다.
- 소유권 확인은 Repository의 `get_owned(...)` 또는 `get_*_owned(...)` 계열 메서드를 표준으로 사용합니다.
- 소유권이 없거나 리소스가 존재하지 않으면 동일하게 `404`를 반환합니다.
- 소유권 확인에 실패한 리소스는 반환하거나 수정·삭제하지 않습니다.
- 오류 응답과 로그에 다른 사용자의 의료정보나 소유권 판단의 상세 사유를 남기지 않습니다.

### 1.3 표준 구현 패턴

소유자 컬럼이 직접 있는 리소스는 Repository의 조회 조건에 리소스 ID와 사용자 ID를 함께 넣습니다.

```python
async def get_owned(
    self,
    *,
    document_id: UUID,
    user: User,
) -> MedicalDocument | None:
    result = await self.session.execute(
        select(MedicalDocument).where(
            MedicalDocument.id == document_id,
            MedicalDocument.user_id == user.id,
        )
    )
    return result.scalar_one_or_none()
```

가이드나 채팅 세션처럼 직접 `user_id`를 갖지 않는 리소스는 상위 관계를 통해 소유권을 확인합니다.

```text
Guide -> Prescription -> MedicalDocument -> User
```

```python
async def get_owned(
    self,
    *,
    guide_id: UUID,
    user_id: UUID,
) -> Guide | None:
    result = await self.session.execute(
        select(Guide)
        .options(selectinload(Guide.prescription).selectinload(Prescription.document))
        .where(Guide.id == guide_id)
    )
    guide = result.scalar_one_or_none()
    if guide is None or guide.prescription.document.user_id != user_id:
        return None
    return guide
```

리소스가 없거나 다른 사용자의 리소스인 경우 Repository는 모두 `None`을 반환하고 Service가 도메인별 `ApiError`로 변환합니다.

### 1.4 생성·수정·삭제

하위 리소스를 생성할 때는 요청으로 전달된 상위 리소스의 소유권을 먼저 확인합니다.

```python
prescription = await repository.get_prescription_owned(
    prescription_id=request.prescription_id,
    user_id=user.id,
)

if prescription is None:
    raise ApiError(
        status_code=404,
        code="PRESCRIPTION_NOT_FOUND",
        message="처방 정보를 찾을 수 없습니다.",
    )

guide = await repository.create(prescription_id=prescription.id)
```

수정·삭제 API도 대상 리소스를 `get_owned(...)`로 조회한 뒤 작업합니다. 현재 사용자 리소스 삭제 API는 구현되어 있지 않으며, 추후 추가 시 같은 기준을 적용합니다.

### 1.5 지양하는 방식

Repository가 소유권 확인 없이 리소스를 조회하고 Service에서 수동으로 비교하는 방식은 표준으로 사용하지 않습니다.

```python
# 지양하는 방식
job = await self._ocr_repo.get_job_with_document(job_id=job_id)
if job is None or job.document.user_id != user.id:
    raise ApiError(status_code=404, code="OCR_JOB_NOT_FOUND", ...)
```

이 방식은 소유권 확인 자체가 들어가 있으면 동작상 문제는 없지만, 새로운 API를 만들 때 비교 로직을 빠뜨릴 가능성이 있습니다. 소유권 확인을 Repository의 `get_*_owned(...)` 메서드 안에 포함하면 Service마다 같은 검사를 반복하지 않아도 됩니다.

### 1.6 404와 403 기준

현재 사용자 리소스의 소유권 확인 실패는 `404`로 통일합니다.

| 상황 | 처리 |
| --- | --- |
| 리소스 ID가 실제로 없음 | `404` + 도메인별 `*_NOT_FOUND` 코드 |
| 리소스는 존재하지만 다른 사용자의 소유임 | `404` + 동일한 `*_NOT_FOUND` 코드 |
| 로그인하지 않은 사용자의 요청 | `401` + 인증 오류 코드 |
| 로그인은 했지만 역할 권한이 부족함 | 리소스 존재를 공개해야 하는 정책일 때만 `403` 검토 |

다른 사용자의 리소스에 `403`을 반환하면 해당 ID가 실제로 존재한다는 정보가 노출될 수 있습니다. 따라서 일반 사용자 리소스는 존재 여부를 숨기기 위해 `404`를 사용합니다.

```json
{
  "code": "GUIDE_NOT_FOUND",
  "message": "가이드를 찾을 수 없습니다.",
  "details": [],
  "trace_id": "요청별 식별자"
}
```

### 1.7 현재 적용 현황

| 리소스 | 현재 확인 방식 |
| --- | --- |
| 의료문서 | `MedicalDocumentRepository.get_owned(...)` |
| 처방전 | `PrescriptionRepository.get_owned(...)` |
| OCR 추출 필드 | `OcrRepository.get_field_owned(...)` |
| OCR 작업 | 작업 조회 후 Service에서 문서 소유자 비교 (1.5의 지양하는 방식, 표준화 후속 작업 대상) |
| 복약 가이드 생성 | `GuideRepository.get_prescription_owned(...)` |
| 복약 가이드 조회 | `GuideRepository.get_owned(...)` |
| 채팅 세션 | `ChatRepository.get_session_owned(...)` (조회), `get_session_owned_for_update(...)` (메시지 전송, 행 잠금 포함) |
| 사용자 정보 | 인증된 `user` 객체 사용 (별도 리소스 ID 조회 없음) |

현재 코드에서 소유권 확인이 완전히 빠진 사용자 리소스 API는 확인되지 않았습니다. 교차 사용자 접근 테스트는 가이드 Repository(`test_get_prescription_owned_rejects_other_users_prescription`, `test_get_owned_guide_rejects_other_users_guide`)와 채팅 API(`test_foreign_ownership_and_closed_session_are_rejected_before_engine`)에 이미 작성되어 있으며, 의료문서·처방전·OCR 리소스 테스트는 추가 보강 대상입니다.

## 2. 실패 상태 저장과 commit 규칙

이 섹션은 현재 MVP가 요청 하나를 동기적으로 처리하고 단일 DB 세션을 쓰는 구조라서 나온 제약입니다. Post-MVP에서 공통 비동기 Job 구조로 바뀌면 실패 상태를 어디서 언제 커밋하는지, 트랜잭션 경계를 어떻게 나누는지가 함께 바뀝니다. 지금 새 기능을 추가할 때는 이 섹션의 패턴을 그대로 따르되, Post-MVP 전환 시 이 패턴 자체가 변경 대상이라는 점을 함께 인지합니다.

### 2.1 실패 상태를 저장하는 이유

AI·OCR·챗봇 호출이 실패해도 사용자와 운영자는 작업이 실패했다는 사실과 재시도 가능 여부를 확인할 수 있어야 합니다. 외부 호출 실패를 예외로만 처리하지 않고, 해당 작업의 실패 상태와 안전한 오류 코드를 DB에 남깁니다.

### 2.2 표준 처리 순서

1. 작업을 `GENERATING`, `PROCESSING` 등 진행 상태로 저장합니다.
2. AI·OCR·챗봇 외부 호출을 수행합니다.
3. 호출이 실패하면 실패 상태와 고정된 오류 정보를 기록하고 즉시 `commit()`합니다.
4. 사용자에게는 `ApiError` 공통 형식으로 안전한 오류 응답을 반환합니다.

```python
try:
    result = await external_client.call(request)
except ExternalTimeoutError as err:
    await repository.mark_failed(
        job,
        error_code="EXTERNAL_API_TIMEOUT",
        error_message="외부 처리 시간이 초과되었습니다.",
        completed_at=datetime.now(UTC),
    )
    raise ApiError(
        status_code=504,
        code="GATEWAY_TIMEOUT",
        message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
    ) from err
```

### 2.3 `flush()`만 사용하면 안 되는 이유

`flush()`는 현재 트랜잭션의 변경 내용을 DB에 SQL로 전송할 뿐 트랜잭션을 확정하지 않습니다. 이후 Service가 `ApiError`를 다시 발생시키고 공통 DB 의존성이 `rollback()`을 수행하면 `flush()`로 저장한 실패 상태도 함께 취소됩니다.

따라서 외부 호출 실패 후 요청 전체가 rollback될 수 있는 구조에서는 실패 상태를 보존하기 위해 그 시점에 바로 `commit()`해야 합니다.

**주의**: 이 `commit()`은 실패 상태 필드만 선택적으로 저장하는 것이 아니라, **같은 DB 세션에 그 시점까지 pending 상태로 남아있던 다른 모든 변경사항까지 함께 커밋**합니다. 실패 처리 직전에 다른 목적의 변경을 미리 `flush()`해 두면, 의도하지 않은 상태로 같이 확정될 수 있으므로 순서에 주의합니다.

**적용 조건**: 이 실패 시점 `commit()`은 해당 요청의 DB 세션에 함께 저장되어도 괜찮은 변경사항만 남아있는 경우에만 사용합니다. 같은 세션 안에서 savepoint나 nested transaction을 나누어도 바깥 세션의 `commit()` 영향이 완전히 격리되는 것은 아닙니다. 실패 기록과 함께 확정되면 안 되는 변경사항이 있다면, 그 작업은 독립된 DB 세션 또는 연결로 분리해 실패 시점의 `commit()`이 영향을 주지 않도록 합니다.

### 2.4 저장할 정보와 저장하지 않을 정보

- 저장: 상태값, 내부 분기용 고정 오류 코드, 안전한 요약 메시지, 완료 시각
- 실패 메타데이터에 저장하지 않음: API Key, Access Token, 외부 SDK 예외 원문, 외부 요청 payload 전체
- 사용자 질문·확정 처방·OCR 결과는 각 도메인의 기능상 필요한 저장 경계에 따라 관리합니다.
- 사용자 응답: 내부 오류 상세가 아닌 `ApiError`의 안전한 `code`, `message`, `details`, `trace_id`

### 2.5 도메인별 구현 차이

같은 원칙이지만 도메인마다 저장 단위가 다릅니다.

| 도메인 | 메서드 | 저장 단위 |
| --- | --- | --- |
| 복약 가이드 | `GuideRepository.mark_failed(...)` | GUIDE 레코드 하나 |
| OCR | `OcrRepository.mark_failed(...)` | OCR_JOB 레코드 하나 |
| 챗봇 | `ChatRepository.commit_failed_message_pair(...)` | USER 메시지와 FAILED ASSISTANT 메시지를 한 쌍으로 함께 커밋 |

챗봇은 사용자 질문과 실패한 답변을 한 쌍으로 남겨야 대화 흐름이 끊기지 않으므로, 단일 레코드만 갱신하는 `mark_failed(...)`가 아니라 메시지 쌍 전체를 커밋하는 별도 메서드를 씁니다. 새 도메인에 실패 상태 저장을 추가할 때는 이름을 `mark_failed`로 맞추기보다, 그 도메인에서 실패 시점에 실제로 무엇을 같이 확정해야 하는지부터 판단합니다.

## 3. OCR·챗봇 실패 상태 유지 QA

### 3.1 필수 시나리오

- OCR 외부 호출 timeout 시 OCR 작업이 실패 상태로 저장됩니다.
- OCR 외부 호출 실패 시 내부 오류 코드가 저장되고 민감한 원문이 저장되지 않습니다.
- 챗봇 외부 호출 timeout·실패 시 USER 메시지와 FAILED ASSISTANT 메시지가 한 쌍으로 저장됩니다.
- 실패 상태 저장 후 Service가 `ApiError`를 반환해도 DB의 실패 상태가 남습니다.
- 실패 응답에는 공통 `code`, `message`, `details`, `trace_id`가 포함됩니다.
- **MVP**: Backend DB에 실패 상태가 보존됩니다. 현재 OCR 실행 실패 응답에서는 Frontend가 실패한 `job_id`를 받지 못할 수 있으므로, 재조회로 실패 상태를 확인하는 흐름은 현재 MVP 계약으로 보장하지 않습니다.
- **Post-MVP**: 재시도 방식과 오류 상세 제공은 도메인별 API 계약으로 확장합니다.
