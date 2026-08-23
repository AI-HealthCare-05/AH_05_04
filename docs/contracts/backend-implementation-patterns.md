# Backend 내부 구현 패턴

## 목적

Backend 내부의 의존성 주입과 Repository 테스트 방식을 통일하기 위한 구현 가이드입니다. 이 문서는 Frontend·AI 담당자와 공유하는 API 요청·응답 계약이 아니라 Backend 코드 구조와 테스트 작성 기준을 다룹니다.

공유 오류 응답과 사용자 리소스 소유권·실패 상태 규칙은 [`backend-common-patterns.md`](./backend-common-patterns.md)와 [`backend-error-response.md`](./backend-error-response.md)를 기준으로 합니다.

## 1. DI Provider 패턴

### 1.1 기본 구조

라우터는 Service Provider를 받고 Service Provider는 Repository Provider를 통해 필요한 Repository를 구성합니다.

```python
def get_x_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> XRepository:
    return XRepository(session)


def get_x_service(
    repository: Annotated[XRepository, Depends(get_x_repository)],
) -> XService:
    return XService(repository=repository)
```

라우터에서는 Service Provider를 주입합니다.

```python
async def get_resource(
    service: Annotated[XService, Depends(get_x_service)],
) -> Response:
    result = await service.get_resource()
    return Response(content=result)
```

### 1.2 직접 주입을 지양하는 이유

라우터에서 `Depends(SomeService)`를 직접 사용하면 Service의 생성자 의존성이 분산되거나 기본 생성 방식에 의존하게 됩니다. `get_x_repository() -> get_x_service()` 체인을 사용하면 DB Session과 Service 의존성이 한 경로로 관리되고 테스트에서 `dependency_overrides`로 Service·Repository를 교체하기 쉽습니다.

## 2. Repository 테스트 패턴

### 2.1 테스트 격리 원칙

Repository 테스트는 테스트 간 DB 상태가 섞이지 않도록 각 테스트를 독립적으로 실행합니다. 현재 프로젝트는 테스트용 MySQL DB에 스키마를 준비하고 테스트별 트랜잭션과 savepoint를 사용해 종료 시 변경 내용을 rollback합니다.

```python
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

일반 Repository 테스트에서는 공통 `db_session` fixture를 사용하고 테스트 데이터는 fixture 안에서 생성합니다. 테스트 데이터의 이메일·전화번호 등 고유값은 테스트마다 충돌하지 않도록 구분합니다.

### 2.2 기본 테스트 구성

- 생성: 필요한 필드와 기본 상태가 저장되는지 확인
- 조회: 존재하는 데이터와 없는 데이터를 구분해 확인
- 소유권: 소유자는 조회되고 다른 사용자는 `None` 또는 도메인 `404`가 되는지 확인
- 수정: 허용된 필드만 변경되는지 확인
- 삭제: 소유권 확인 후 삭제되는지 확인 (삭제 API 도입 후 적용)
- 실패 상태: 실패 처리 이후 rollback이 발생해도 실패 상태가 남는지 확인

테스트는 구현 세부사항보다 Repository가 보장하는 결과를 검증합니다.
