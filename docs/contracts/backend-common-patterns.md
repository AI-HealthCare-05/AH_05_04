# Backend 공통 구현 패턴

## 목적

Backend 담당자가 기능을 나누어 구현하더라도 사용자 리소스 접근, 실패 상태 저장, 의존성 주입, Repository 테스트를 같은 기준으로 적용할 수 있도록 공통 구현 규칙을 정리합니다. 

공통 오류 응답 형식과 오류 코드는 APIError를 기준으로 합니다. 

## 1. 사용자 리소스 소유권 확인

### 1.1 왜 필요한가

여기서 소유권은 누가 코드를 작성하는지가 아니라 어떤 회원에게 데이터가 귀속되어 있는지를 의미합니다. 로그인 여부를 확인하는 것과 해당 사용자가 특정 리소스에 접근해도 되는지 확인하는 것은 다릅니다. - **인증(Authentication)**: 요청을 보낸 사람이 로그인한 사용자인지 확인합니다. - **인가(Authorization) / 소유권 확인**: 로그인한 사용자가 해당 리소스의 소유자인지 확인합니다. 인증만 확인하고 소유권 확인을 생략하면 요청 경로의 `document_id`나 `guide_id`를 다른 사람의 ID로 바꾸어 의료정보에 접근하는 IDOR 취약점이 발생할 수 있습니다. 예를 들어 사용자 A가 업로드한 처방전·OCR 결과·복약 가이드·채팅 세션을 사용자 B가 리소스 ID만 알고 조회·수정·삭제할 수 없어야 합니다. ### 1.2 기본 원칙

- 사용자 리소스의 조회·수정·삭제 API는 소유권을 확인합니다. - 사용자 리소스를 생성할 때도 요청에 포함된 상위 리소스의 소유권을 확인합니다. - 소유권 확인은 Repository의 `get_owned(...)` 또는 `get_*_owned(...)` 계열 메서드를 표준으로 사용합니다. - 소유권이 없거나 리소스가 존재하지 않으면 동일하게 `404`를 반환합니다. - 소유권 확인에 실패한 리소스는 반환하거나 수정·삭제하지 않습니다. - 오류 응답과 로그에 다른 사용자의 의료정보나 소유권 판단의 상세 사유를 남기지 않습니다. ### 1.3 표준 구현 패턴

소유자 컬럼이 직접 있는 리소스는 Repository의 조회 조건에 리소스 ID와 사용자 ID를 함께 넣습니다. ```python
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

가이드나 채팅 세션처럼 직접 `user_id`를 갖지 않는 리소스는 상위 관계를 통해 소유권을 확인합니다. ```text
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
 if guide is None or guide.prescription.document.user_id!= user_id:
 return None
 return guide
```

리소스가 없거나 다른 사용자의 리소스인 경우 Repository는 모두 `None`을 반환하고 Service가 도메인별 `ApiError`로 변환합니다. ### 1.4 생성·수정·삭제

하위 리소스를 생성할 때는 요청으로 전달된 상위 리소스의 소유권을 먼저 확인합니다. ```python
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

수정·삭제 API도 대상 리소스를 `get_owned(...)`로 조회한 뒤 작업합니다. 현재 사용자 리소스 삭제 API는 구현되어 있지 않으며, 추후 추가 시 같은 기준을 적용합니다. ### 1.5 지양하는 방식

Repository가 소유권 확인 없이 리소스를 조회하고 Service에서 수동으로 비교하는 방식은 표준으로 사용하지 않습니다. ```python
# 지양하는 방식
job = await self._ocr_repo.get_job_with_document(job_id=job_id)
if job is None or job.document.user_id!= user.id:
 raise ApiError(status_code=404, code="OCR_JOB_NOT_FOUND",...)
```

현재처럼 소유권 확인이 들어가 있으면 동작상 문제는 없지만 새로운 API를 만들 때 비교 로직을 빠뜨릴 가능성이 있습니다. 소유권 확인을 Repository의 `get_*_owned(...)` 메서드 안에 포함하면 Service마다 같은 검사를 반복하지 않아도 됩니다. ### 1.6 404와 403 기준

현재 사용자 리소스의 소유권 확인 실패는 `404`로 통일합니다. | 상황 | 처리 |
| --- | --- |
| 리소스 ID가 실제로 없음 | `404` + 도메인별 `*_NOT_FOUND` 코드 |
| 리소스는 존재하지만 다른 사용자의 소유임 | `404` + 동일한 `*_NOT_FOUND` 코드 |
| 로그인하지 않은 사용자의 요청 | `401` + 인증 오류 코드 |
| 로그인은 했지만 역할 권한이 부족함 | 리소스 존재를 공개해야 하는 정책일 때만 `403` 검토 |

다른 사용자의 리소스에 `403`을 반환하면 해당 ID가 실제로 존재한다는 정보가 노출될 수 있습니다. 따라서 일반 사용자 리소스는 존재 여부를 숨기기 위해 `404`를 사용합니다. ```json
{
 "code": "GUIDE_NOT_FOUND",
 "message": "가이드를 찾을 수 없습니다.",
 "details": [],
 "trace_id": "요청별 식별자"
}
```

### 1.7 현재 적용 현황

| 리소스 | 현재 확인 방식 | 상태 |
| --- | --- | --- |
| 의료문서 | `MedicalDocumentRepository.get_owned(...)` | 적용됨 |
| 처방전 | `PrescriptionRepository.get_owned(...)` | 적용됨 |
| OCR 추출 필드 | `OcrRepository.get_field_owned(...)` | 적용됨 |
| OCR 작업 | 작업 조회 후 Service에서 문서 소유자 비교 | 적용됨, 표준화 후속 작업 |
| 복약 가이드 | `GuideRepository.get_owned(...)` | 적용됨 |
| 채팅 세션 | `ChatRepository.get_session_owned(...)` | 적용됨 |
| 사용자 정보 | 인증된 `user` 객체 사용 | 적용됨 |

현재 코드에서 소유권 확인이 완전히 빠진 사용자 리소스 API는 확인되지 않았습니다. 교차 사용자 접근 테스트는 가이드 Repository에 작성되어 있으며, 의료문서·처방전·OCR·채팅 리소스 테스트는 추가 보강 대상입니다. ## 2. 실패 상태 저장과 commit 규칙

### 2.1 실패 상태를 저장하는 이유

AI·OCR·챗봇 호출이 실패해도 사용자와 운영자는 작업이 실패했다는 사실과 재시도 가능 여부를 확인할 수 있어야 합니다. 외부 호출 실패를 예외로만 처리하지 않고, 해당 작업의 실패 상태와 안전한 오류 코드를 DB에 남깁니다. ### 2.2 표준 처리 순서

1. 작업을 `GENERATING`, `PROCESSING` 등 진행 상태로 저장합니다. 2. AI·OCR·챗봇 외부 호출을 수행합니다. 3. 호출이 실패하면 `mark_failed(...)`로 실패 상태와 고정된 오류 정보를 저장합니다. 4. 실패 상태 저장을 별도로 `commit()`합니다. 5. 사용자에게는 `ApiError` 공통 형식으로 안전한 오류 응답을 반환합니다. ```python
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

`flush()`는 현재 트랜잭션의 변경 내용을 DB에 SQL로 전송할 뿐 트랜잭션을 확정하지 않습니다. 이후 Service가 `ApiError`를 다시 발생시키고 공통 DB 의존성이 `rollback()`을 수행하면 `flush()`로 저장한 실패 상태도 함께 취소됩니다. 따라서 외부 호출 실패 후 요청 전체가 rollback될 수 있는 구조에서는 실패 상태를 보존하기 위해 별도 `commit()` 또는 독립 트랜잭션이 필요합니다. ### 2.4 저장할 정보와 저장하지 않을 정보

- 저장: 상태값, 내부 분기용 고정 오류 코드, 안전한 요약 메시지, 완료 시각
- 저장하지 않음: API Key, Access Token, 환자·처방 원문, 외부 SDK 예외 원문, 요청 payload 전체
- 사용자 응답: 내부 오류 상세가 아닌 `ApiError`의 안전한 `code`, `message`, `details`, `trace_id`

현재 `GuideRepository`, `OcrRepository`, `ChatRepository`의 `mark_failed(...)`는 실패 상태를 저장한 뒤 `commit()`하는 방식이 적용되어 있습니다. ## 3. DI Provider 패턴

### 3.1 기본 구조

라우터는 Service Provider를 받고 Service Provider는 Repository Provider를 통해 필요한 Repository를 구성합니다. ```python
def get_x_repository(
 session: Annotated[AsyncSession, Depends(get_db_session)],
) -> XRepository:
 return XRepository(session)

def get_x_service(
 repository: Annotated[XRepository, Depends(get_x_repository)],
) -> XService:
 return XService(repository=repository)
```

라우터에서는 Service Provider를 주입합니다. ```python
async def get_resource(
 service: Annotated[XService, Depends(get_x_service)],
) -> Response:
 result = await service.get_resource()
 return Response(content=result)
```

### 3.2 직접 주입을 지양하는 이유

라우터에서 `Depends(SomeService)`를 직접 사용하면 Service의 생성자 의존성이 분산되거나 기본 생성 방식에 의존하게 됩니다. `get_x_repository() -> get_x_service()` 체인을 사용하면 DB Session과 Service 의존성이 한 경로로 관리되고 테스트에서 `dependency_overrides`로 Service·Repository를 교체하기 쉽습니다. ## 4. Repository 테스트 패턴

### 4.1 테스트 격리 원칙

Repository 테스트는 테스트 간 DB 상태가 섞이지 않도록 각 테스트를 독립적으로 실행합니다. 현재 프로젝트는 테스트용 MySQL DB에 스키마를 준비하고 테스트별 트랜잭션과 savepoint를 사용해 종료 시 변경 내용을 rollback합니다. ```python
async with test_engine.connect() as connection:
 transaction = await connection.begin()
 session = AsyncSession(
 bind=connection,
 expire_on_commit=False,
 autoflush=False,
 join_transaction_mode="create_savepoint",
 )
 try:
 yield session
 finally:
 await session.close()
 if transaction.is_active:
 await transaction.rollback()
```

일반 Repository 테스트에서는 공통 `db_session` fixture를 사용하고 테스트 데이터는 fixture 안에서 생성합니다. 테스트 데이터의 이메일·전화번호 등 고유값은 테스트마다 충돌하지 않도록 구분합니다. ### 4.2 기본 테스트 구성

- 생성: 필요한 필드와 기본 상태가 저장되는지 확인
- 조회: 존재하는 데이터와 없는 데이터를 구분해 확인
- 소유권: 소유자는 조회되고 다른 사용자는 `None` 또는 도메인 `404`가 되는지 확인
- 수정: 허용된 필드만 변경되는지 확인
- 삭제: 소유권 확인 후 삭제되는지 확인
- 실패 상태: `mark_failed(...)` 이후 rollback이 발생해도 실패 상태가 남는지 확인

테스트는 구현 세부사항보다 Repository가 보장하는 결과를 검증합니다. ## 5. OCR·챗봇 실패 상태 유지 QA

### 5.1 필수 시나리오

- OCR 외부 호출 timeout 시 OCR 작업이 실패 상태로 저장됩니다. - OCR 외부 호출 실패 시 내부 오류 코드가 저장되고 민감한 원문이 저장되지 않습니다. - 챗봇 외부 호출 timeout·실패 시 AI 메시지가 실패 상태로 저장됩니다. - 실패 상태 저장 후 Service가 `ApiError`를 반환해도 DB의 실패 상태가 남습니다. - 실패 응답에는 공통 `code`, `message`, `details`, `trace_id`가 포함됩니다. - Frontend가 재조회했을 때 실패 상태와 재시도에 필요한 정보를 확인할 수 있습니다. ## 변경 이력

| 날짜 | 변경 내용 | 변경자 | 비고 |
| --- | --- | --- | --- |
| 2026-08-20 | 소유권·실패 상태·DI·Repository 테스트·OCR·챗봇 QA 기준 통합 초안 작성 | 송은영 | |
