# 복약 챗봇 AI Backend 연동 설계

> **후속 정정:** 이 문서의 `2 × T + M`·`T + M` 값은 두 요청 참고 시나리오이며 Production 전체 요청의 하한이 아니다. 현재 구현에는 동일 세션 admission cap이 없으므로 최신 배포 판정은 `docs/deployment.md`를 따른다. 코드로 최대 동시 전송 `N`을 강제하기 전에는 Production 배포가 차단된다.

## 문서 정보

| 항목 | 내용 |
| --- | --- |
| 관련 Issue | [#38 복약 챗봇 AI Backend 연동](https://github.com/AI-HealthCare-05/AH_05_04/issues/38) |
| 선행 PR | [#35 복약 챗봇 AI 응답 생성 로직 구현](https://github.com/AI-HealthCare-05/AH_05_04/pull/35) |
| 반영된 선행 PR | [#33 CORS·trace ID·공통 오류 처리](https://github.com/AI-HealthCare-05/AH_05_04/pull/33) (`4e7df35`) |
| 최신 오류 계약 | [#46 Backend 오류 응답 통일](https://github.com/AI-HealthCare-05/AH_05_04/pull/46) (`d6359f6`) |
| 작업 브랜치 | `feat/38-chat-ai-backend-integration` |
| 대상 브랜치 | `develop` |
| 소유권 | `/app/`: `@phina-io`, `/docs/api.md`: `@phina-io`, `@hazelnutflavoured` |
| 관련 영역 | Backend, Database, Infrastructure, Documentation |

## 목적

PR #35에서 구현한 provider-neutral `ChatGenerator`를 기존 Backend의 채팅 세션, 확정 처방, 메시지 저장 및 HTTP 오류 계약에 연결한다. 활성 채팅 세션에서 현재 사용자 질문과 연결된 확정 약물만 AI에 전달하고, 생성 성공 또는 실패를 `CHAT_MESSAGE`에 일관되게 저장한다.

이 연동은 기존 동기 one-cycle API 계약을 유지한다. 요청 하나에서 USER·ASSISTANT 메시지를 만들고 AI 응답을 생성한 뒤 `201 Created`를 반환한다. AI Core는 FastAPI, SQLAlchemy, 사용자·세션 식별자와 저장 상태를 알지 않는다.

## 범위

### 포함

- 기존 `ChatEngine`을 구현하는 Backend Adapter
- Backend DTO에서 AI 입력 모델로의 명시적 변환
- `duration_days`와 `Decimal` 용량 전달
- `ChatService`와 실제 Engine의 dependency 조립
- 동일 세션 동시 전송 직렬화와 `message_seq` 보호
- 성공·실패 상태, 생성 metadata와 안전한 오류 문구 저장
- AI 오류에서 기존 `500`·`503`·`504` HTTP 계약으로의 변환
- Adapter, Repository, Service, API 및 Backend–AI 계약 테스트
- 모든 채팅 API 성공·오류 응답의 `Cache-Control: no-store` 보장
- SQL 예외·echo 로그의 질문·약물 bind parameter 비노출
- `tests/contract/`를 실행하는 CI와 로컬 테스트 스크립트 보완

### 제외

- 이전 대화 이력 전달
- Streaming 또는 비동기 Worker 전환
- RAG, Citation, OTC 성분 식별과 별도 의료 안전 분류기
- 프롬프트, Provider client와 AI 생성 결과 스키마 변경
- Frontend 변경
- DB 모델, migration과 API 응답 body 변경
- 새 환경변수 또는 dependency 추가

## 현재 상태와 문제

Backend에는 세션 소유권 확인, USER·ASSISTANT 메시지 생성, 상태 저장과 HTTP 응답 조립이 구현되어 있다. 그러나 `ChatService`가 기본값으로 `NotConfiguredChatEngine`을 사용하므로 실제 `ChatGenerator`를 호출하지 않는다.

또한 현재 메시지 번호는 잠금 없이 마지막 `message_seq`를 조회해 계산한다. 실제 외부 AI 호출이 연결된 상태에서 같은 세션으로 두 요청이 동시에 들어오면 두 요청이 같은 번호를 선택해 `uq_chat_message_session_seq` 충돌을 일으킬 수 있다.

현재 공개 `ChatMedicationInput`은 `dose_value`를 `float`로 정의하고 `duration_days`를 포함하지 않는다. ORM의 `Medication.dose_value`는 `Decimal`이고 AI 입력은 `duration_days`를 지원하므로, 현 상태로 연결하면 정밀도와 투약일수가 손실될 수 있다.

## 설계 원칙

1. 기존 `ChatEngine` 경계를 유지하고 얇은 Adapter에서만 Backend DTO와 AI 모델을 연결한다.
2. AI Core에 사용자·세션·처방·메시지 식별자와 DB 상태를 전달하지 않는다.
3. 확정 처방에 속한 전체 약물을 전달하며 일부 약물을 임의로 자르지 않는다.
4. 질문과 약물 정보는 승인된 도메인 저장 계약에만 저장하고 오류 metadata, HTTP 오류 응답, SQL 예외와 일반 애플리케이션 로그에는 남기지 않는다. Provider·SDK 오류 본문도 저장하거나 노출하지 않는다.
5. 같은 세션에서는 생성 요청을 직렬화하고 다른 세션의 요청은 독립적으로 처리한다.
6. 정상 결과와 실패 상태 모두 명시적인 테스트로 저장 계약을 검증한다.
7. 의료 입력을 포함할 수 있는 원본 예외 chain을 Adapter 밖으로 전달하지 않는다.
8. 저장소 테스트 전략의 공식 위치인 `tests/contract/`를 PR gate에서 실제 실행한다.

## 컴포넌트 구조

```text
ChatService
  └─ ChatEngine.reply(ChatReplyInput)
       └─ ChatGeneratorEngine
            ├─ Backend DTO → AI Pydantic 입력 변환
            ├─ AI 오류 → Backend 오류 변환
            └─ ChatGenerator.generate()
                 └─ ChatProvider
                      └─ OpenAIResponsesClient
                           └─ process-scoped AsyncOpenAI
```

### Backend 공개 계약

`app/services/chat_ai/__init__.py`의 기존 공개 계약은 유지하되 다음을 보완한다.

```python
@dataclass(frozen=True)
class ChatMedicationInput:
    medication_name: str
    dose_value: Decimal | None
    dose_unit: str | None
    frequency_per_day: int | None
    timing_text: str | None
    duration_days: int | None


@dataclass(frozen=True)
class ChatReplyInput:
    prescription_id: UUID
    medications: list[ChatMedicationInput]
    content: str


@dataclass(frozen=True)
class ChatReplyOutput:
    content: str
    model_name: str
    prompt_version: str


class ChatGenerationFailedError(Exception):
    """입력 검증·설정·응답 처리 실패를 Backend의 안전한 500 흐름으로 전달한다."""
```

`prescription_id`는 Backend의 조회·추적 계약을 위해 유지하지만 Adapter가 만드는 `ChatGenerationInput`과 Provider payload에는 포함하지 않는다.

`ChatGenerationFailedError`는 `app.services.chat_ai` package root에서 기존 timeout·unavailable 오류와 함께 export한다. `NotConfiguredChatEngine`과 `ChatService(engine=None)` 기본값은 제거한다. Engine 누락이 묵시적인 런타임 fallback으로 숨지 않고 필수 생성자 인자와 dependency 조립 테스트에서 드러나게 하며, 테스트는 Fake Engine을 명시적으로 주입한다.

이 package root는 저장소 내부 Backend–AI 경계이며 별도 배포·버전 관리되는 외부 Python API가 아니다. 따라서 `duration_days` 필수 필드 추가, `dose_value`의 `Decimal` 전환과 fallback 제거는 저장소 내부 source-incompatible 변경으로 허용하되, 모든 호출부와 테스트를 같은 PR에서 갱신한다. 외부 소비자가 발견되면 구현을 중단하고 호환 기본값 또는 별도 versioned contract를 소유자와 합의한다.

### ChatGeneratorEngine

신규 `app/services/chat_generator_engine.py`는 `ChatEngine`을 구현한다. 이 Adapter는 DB와 FastAPI를 import하지 않으며 다음 책임만 가진다.

- `ChatReplyInput.content`를 `ChatGenerationInput.question`으로 변환
- 각 Backend `ChatMedicationInput`을 AI `schemas.ChatMedicationInput`으로 변환
- `ChatGenerationResult`를 `ChatReplyOutput`으로 변환
- AI 입력 검증과 AI Core 오류를 Backend 오류로 변환

Adapter 생성자는 `ChatProvider`, model과 timeout을 받는다. `ChatGenerator` 생성은 `reply()` 내부에서 수행한다. model 또는 timeout 설정이 잘못됐을 때 FastAPI dependency 생성 단계에서 예외가 발생하면 USER·ASSISTANT 실패 상태를 남길 수 없기 때문이다. 지연 생성으로 설정 오류도 메시지 생성 이후 표준 실패 흐름에서 처리한다.

```python
class ChatGeneratorEngine:
    def __init__(self, *, provider: ChatProvider, model: str, timeout_seconds: float) -> None: ...

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        # ChatGenerator 생성, 입력 변환, 호출과 오류 변환
        ...
```

## Dependency 조립

`app/main.py`가 lifespan 동안 생성하는 process-scoped `AsyncOpenAI`를 그대로 재사용한다. 설정도 기존 `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`를 사용한다.

`app/dependencies/services.py`에는 Chat Engine dependency를 추가한다. Guide와 Chat client class가 모두 `OpenAIResponsesClient`라는 이름을 사용하므로 import alias로 소비자를 분명히 한다.

```python
from app.services.chat_ai import OpenAIResponsesClient as ChatOpenAIResponsesClient
from app.services.guide_ai import OpenAIResponsesClient as GuideOpenAIResponsesClient


def get_chat_engine(client: Annotated[AsyncOpenAI, Depends(get_openai_client)]) -> ChatEngine:
    return ChatGeneratorEngine(
        provider=ChatOpenAIResponsesClient(client),
        model=config.OPENAI_MODEL,
        timeout_seconds=config.OPENAI_TIMEOUT_SECONDS,
    )
```

`get_chat_service()`는 Repository 두 개와 `ChatEngine`을 모두 받아 `ChatService`에 명시적으로 주입한다. request-scoped Adapter와 Provider wrapper는 상태를 저장하지 않으며 실제 HTTP client만 process scope로 공유한다.

## 데이터 변환 계약

`ChatService`는 `CHAT_SESSION.prescription_id`로 `MEDICATION`을 `display_order` 오름차순 조회한다. ORM 필드를 다음처럼 Backend DTO로 변환한다.

| ORM 필드 | Backend DTO | AI 입력 | 규칙 |
| --- | --- | --- | --- |
| `medication_name` | `str` | `str` | 필수, 최대 255자 |
| `dose_value` | `Decimal \| None` | `Decimal \| None` | `float`로 변환하지 않음 |
| `dose_unit` | `str \| None` | `str \| None` | 최대 50자 |
| `frequency_per_day` | `int \| None` | `int \| None` | 양수 또는 `None` |
| `timing_text` | `str \| None` | `str \| None` | 최대 255자 |
| `duration_days` | `int \| None` | `int \| None` | 양수 또는 `None` |

AI Core는 최대 30개 약물을 허용한다. 확정 처방에 31개 이상이 있으면 앞의 30개만 보내지 않는다. 부분 처방을 근거로 답하는 의료 안전 위험을 피하기 위해 입력 검증 실패로 처리하고 Provider를 호출하지 않는다.

AI Core의 기존 규칙에 따라 `dose_value`와 `dose_unit` 중 하나만 존재하면 Provider JSON에서 두 필드를 모두 생략한다. 질문과 문자열 정규화, 금지 제어문자와 길이 검증도 AI 입력 모델이 담당한다.

## 요청과 트랜잭션 흐름

### 성공

1. 소유권 조건을 SQL에 포함한 `get_session_owned_for_update()`로 `CHAT_SESSION`을 `SELECT ... FOR UPDATE` 조회한다.
2. 세션이 `ACTIVE`인지 확인한다.
3. 확정 약물을 `display_order` 순서로 조회한다.
4. 마지막 메시지 다음 번호를 계산한다.
5. USER 메시지와 `PENDING` ASSISTANT 메시지를 연속 번호로 생성한다.
6. ASSISTANT를 `GENERATING`으로 변경한다.
7. Adapter를 통해 AI를 호출한다.
8. 결과 content, 실제 model ID와 prompt version을 저장하고 ASSISTANT를 `COMPLETED`로 변경한다.
9. `CHAT_SESSION.last_message_at`을 완료 시각으로 갱신한다.
10. request-scoped DB dependency가 USER, ASSISTANT와 세션 변경을 함께 commit한다.

```text
세션 잠금
  → USER/ASSISTANT 생성
  → GENERATING
  → AI 호출
  → COMPLETED + metadata
  → 요청 종료 commit
```

### 실패

AI 호출 이전까지는 성공 흐름과 같다. 오류가 발생하면 각 `except` 안에서는 원본 예외를 보존하지 않는 안전한 실패 분류와 `ApiError` 객체만 만든다. `except` 블록을 완전히 벗어난 뒤 ASSISTANT를 `FAILED`로 변경하고 안전한 `error_code`, `error_message`와 `completed_at`을 `commit_failed_message_pair()`로 즉시 commit한 다음 `ApiError`를 발생시킨다. request-scoped DB dependency의 후속 rollback에도 USER·ASSISTANT 실패 쌍은 유지된다.

```text
세션 잠금
  → USER/ASSISTANT 생성
  → GENERATING
  → AI 오류
  → except 종료
  → FAILED + 안전 오류 metadata + 즉시 commit
  → 안전한 ApiError
```

실패한 ASSISTANT의 `content`, `model_name`과 `prompt_version`은 `NULL`로 유지한다. USER 메시지는 보존되며 메시지 목록에서 USER와 FAILED ASSISTANT를 함께 조회할 수 있다.

`commit_failed_message_pair()`는 이름과 docstring에 단일 row update가 아니라 현재 `AsyncSession`의 전체 pending transaction을 commit한다는 점을 명시한다. 이 메서드는 `send_message()`가 만든 USER·ASSISTANT 쌍과 해당 실패 상태만 pending인 시점에 호출해야 하며, 호출 전에 다른 도메인의 write를 같은 session에 추가하지 않는다. AI 예외를 처리하는 `except`가 끝난 뒤 호출해 commit 또는 flush 자체가 실패하더라도 그 persistence 예외의 `__context__`에 Provider·AI 예외가 연결되지 않게 한다. 이 제약을 Service 테스트에서 commit 호출 순서와 횟수로 검증하고, 실제 MySQL 테스트에서 이후 request rollback에도 정확히 한 쌍만 남는지 확인한다. 향후 하나의 request transaction에 다른 도메인 write를 함께 조립해야 한다면 commit 책임을 명시적인 Service unit-of-work로 이동한다.

### 동시 전송

`get_session_owned_for_update()`는 조회 전용 `get_session_owned()`와 분리해 잠금 의도를 호출부에서 명확히 한다. 잠금 쿼리는 `CHAT_SESSION.id`의 unique index로 대상 세션 한 건만 조회하고, 소유권은 locking clause가 없는 correlated `EXISTS` subquery로 제한한다. locking statement에서 `PRESCRIPTION`과 `MEDICAL_DOCUMENT`를 JOIN하거나 `selectinload`하지 않는다. MySQL에서 외부 쿼리의 `FOR UPDATE`는 별도 locking clause가 없는 nested subquery row를 잠그지 않으므로, 이 형태로 처방·문서 row의 불필요한 잠금을 피한다.

```sql
SELECT chat_session.*
FROM chat_session
WHERE chat_session.id = :session_id
  AND EXISTS (
    SELECT 1
    FROM prescription
    JOIN medical_document ON medical_document.id = prescription.document_id
    WHERE prescription.id = chat_session.prescription_id
      AND medical_document.user_id = :user_id
  )
FOR UPDATE;
```

SQLAlchemy statement를 MySQL dialect로 compile했을 때도 `FOR UPDATE`는 외부 statement 끝에 한 번만 생성되어야 한다. Repository 단위 테스트는 SQL shape를 검증하고, 실제 두-session 동시성 동작과 `CHAT_SESSION` 외 row를 잠그지 않는지는 MySQL 통합 테스트로 검증한다.

같은 세션의 두 번째 전송은 첫 번째 전송이 완료 또는 실패해 commit할 때까지 세션 row lock에서 대기한다. 잠금을 얻은 후 최신 마지막 메시지를 조회하므로 USER–ASSISTANT 쌍은 `1·2`, `3·4`처럼 순서대로 저장된다. 다른 세션 row는 잠그지 않으므로 서로 다른 세션의 생성은 병렬로 진행된다.

잠금은 최대 OpenAI 전체 timeout 동안 유지된다. 현재 요청도 외부 호출 동안 DB transaction과 connection을 유지하므로, 이 설계는 동일 세션만 row-lock 수준에서 의도적으로 직렬화한다. 서로 다른 세션은 row lock으로 서로를 막지 않지만 장시간 DB connection을 함께 사용하므로 connection pool 수준에서는 완전히 독립적이지 않다. 동시 생성량이 pool의 수용량을 넘으면 채팅 외 DB 요청도 connection 대기를 겪을 수 있다. 성공 전 프로세스 종료나 요청 취소가 발생하면 transaction이 rollback되어 영구적인 `GENERATING` 메시지를 남기지 않는다.

같은 세션의 두 번째 요청은 첫 요청의 남은 생성 시간에 더해 자기 생성 시간까지 소비할 수 있다. 정확히 두 요청이 동시에 시작한 시나리오의 참고 지연은 `2 × OPENAI_TIMEOUT_SECONDS`에 애플리케이션 처리 여유를 더한 값이다. 이는 전체 시스템의 최대 대기시간 계약이 아니다. 같은 세션에 세 개 이상이 겹치면 뒤 요청은 앞선 요청 수에 비례해 더 오래 대기하며, 요청 수를 제한하지 않는 현재 설계에는 유한한 end-to-end 최대값이 없다.

이번 PR은 기존 동기 계약을 유지하므로 `NOWAIT`, 새 409 오류와 세션별 분산 queue를 추가하지 않는다. 동일 세션의 세 개 이상 동시 요청은 correctness는 row lock으로 보호하지만 응답시간 SLO를 보장하지 않는 과부하 상황으로 정의한다. DB lock wait timeout은 AI 오류가 아니므로 현재 공통 오류 계약의 `500 INTERNAL_SERVER_ERROR`로 처리되고, 잠금을 얻어 USER·ASSISTANT를 만들기 전 transaction이 rollback되어 새 메시지를 남기지 않는다. reverse proxy가 먼저 연결을 종료할 수도 있다. 별도 오류 계약이나 backend 동시성 제한이 필요해지면 관측 결과를 근거로 후속 Issue에서 다룬다. API 문서에는 두 요청 기준 참고 지연, 세 개 이상에는 최대시간 보장이 없다는 점과 lock wait timeout 시 재조회 결과가 변하지 않는다는 점을 함께 명시한다.

현재 `infra/nginx/*.conf`와 MySQL 8.0 compose 설정은 timeout을 별도로 덮어쓰지 않는다. 두 요청 참고 시나리오의 `2 × T + M`·`T + M`만으로 전체 배포 하한을 정하지 않는다. Production 전 lock 획득 전에 동일 세션 최대 동시 전송 `N`을 코드로 강제하고, 최신 [배포 가이드](../../deployment.md)에 따라 Nginx `proxy_read_timeout >= N × T + M`, MySQL `innodb_lock_wait_timeout > (N - 1) × T + M`을 확인한다. `N`이 강제되지 않은 현재 상태는 Production 배포 차단이다.

같은 배포 기록에 worker별 예상 동시 AI 생성량, `DB_CONNECTION_POOL_MAXSIZE`, SQLAlchemy engine의 실제 overflow·pool wait 정책과 허용 가능한 connection 대기를 함께 적는다. 외부 호출 동안 connection을 유지하는 MVP tradeoff를 이 수용량 안에서 명시적으로 승인한다. 예상 동시 생성량이 수용량에 근접하거나 채팅 외 요청의 connection 대기가 관측되면 #38의 잠금 범위를 조용히 완화하지 않고, 비동기 worker 또는 별도 동시성 제어로 전환하는 후속 설계를 진행한다.

수용량 계산에는 실제 AI를 호출 중인 요청만이 아니라 `SELECT ... FOR UPDATE`에서 기다리는 동일 세션 요청을 포함한 전체 in-flight chat 요청을 사용한다. worker별 pool·overflow 총 수용량에서 인증·처방 조회 등 비채팅 DB 요청을 위한 예비 connection을 먼저 제외하고, 남은 범위 안에서 허용 가능한 chat 요청 수를 승인한다. 이 기준을 충족하지 못하는 환경은 admission/rate limit 또는 비동기 worker 설계 없이 배포하지 않는다.

## SQL 예외와 로그 개인정보 보호

USER 질문은 `CHAT_MESSAGE.content`에 의도적으로 저장되지만 SQL bind parameter, ORM/DB 예외 문자열과 echo 로그에는 노출하지 않는다. `app/core/db/databases.py`의 `create_async_engine()`에 `hide_parameters=True`를 설정해 `SQLALCHEMY_ECHO` 값과 관계없이 statement parameter가 SQLAlchemy 로그와 `StatementError` 문자열에서 가려지게 한다. 운영 환경의 `SQLALCHEMY_ECHO`는 계속 `False`를 사용하며, 이를 디버깅 목적으로 켜더라도 실제 환자정보를 사용하지 않는다.

이 제어는 Provider 오류 chain 분리와 별개다. 메시지 insert·실패 update의 flush 또는 commit 자체가 실패해 Starlette/Uvicorn이 예상 밖 예외를 기록하더라도 질문·약물 bind 값이 예외 문자열에 포함되지 않아야 한다. 합성 sentinel 질문과 약물명을 bind parameter로 사용해 의도적인 SQL 실패를 발생시키고, 포착한 SQLAlchemy 예외 문자열과 로그에 sentinel이 없으며 parameter 숨김 표시는 존재하는지 검증한다. 실제 환자정보나 Provider 응답은 테스트에 사용하지 않는다.

## 오류 계약

Adapter는 AI Core 오류를 다음 Backend 오류로 변환한다.

| AI Core 오류 | Backend 오류 | DB `error_code` | HTTP |
| --- | --- | --- | --- |
| `ChatGenerationTimeoutError` | `ChatTimeoutError` | `OPENAI_API_TIMEOUT` | `504 GATEWAY_TIMEOUT` |
| `ChatGenerationUnavailableError` | `ChatServiceUnavailableError` | `OPENAI_API_ERROR` | `503 SERVICE_UNAVAILABLE` |
| 설정·인증·잘못된 응답·입력 검증 오류 | `ChatGenerationFailedError` | `OPENAI_RESPONSE_PROCESSING_FAILED` | `500 AI_RESPONSE_FAILED` |
| 예상하지 못한 내부 오류 | Service 최종 안전망 | `OPENAI_RESPONSE_PROCESSING_FAILED` | `500 AI_RESPONSE_FAILED` |

Adapter는 timeout과 unavailable을 먼저 처리하고, 나머지 `ChatGenerationError`와 입력 `ValidationError`를 `ChatGenerationFailedError`로 변환한다. 알 수 없는 프로그래밍 오류는 숨기지 않고 Service의 최종 안전망까지 전파한다.

Service는 `ChatTimeoutError`, `ChatServiceUnavailableError`, `ChatGenerationFailedError`, 그 밖의 `Exception` 순서로 처리한다. 앞의 세 분기는 표의 HTTP·DB 분류를 사용하고, 마지막 분기는 생성 처리 실패와 같은 안전한 `500`으로 수렴시킨다. `CancelledError` 같은 `BaseException`은 잡지 않아 request transaction rollback과 취소 전파를 유지한다.

Python의 `raise SafeError(...) from None`은 traceback 표시를 억제하지만 원본 오류 객체를 `SafeError.__context__`에 남긴다. 또한 `except` 안에서 실행한 실패 저장·commit이 새 예외를 발생시키면 그 persistence 예외의 `__context__`에도 처리 중인 원본 오류가 연결된다. 따라서 Adapter와 Service는 각 `except` 안에서 고정 메시지를 가진 안전한 오류 객체와 실패 metadata만 변수에 할당한다. Adapter는 `except`를 벗어난 뒤 안전한 Backend 오류를 raise하고, Service는 `except`를 벗어난 뒤 `commit_failed_message_pair()`를 호출한 다음 안전한 `ApiError`를 raise한다. 원본 예외 객체 자체는 변수, DTO 또는 metadata에 저장하지 않는다.

이 deferred failure 처리로 정상적인 오류 변환에서는 Backend 오류와 외부 `ApiError`의 `__cause__`·`__context__`가 모두 `None`이 된다. 실패 persistence 자체가 예외를 발생시키는 경우에는 공통 `500 INTERNAL_SERVER_ERROR`와 request rollback으로 수렴하며, 그 persistence 예외 chain에도 앞선 Provider·AI 오류가 포함되지 않는다. Adapter 오류 변환, 네 Service 오류 분기와 persistence 실패 테스트가 이 경계를 검증한다.

DB에는 다음 고정 문구만 저장한다.

```python
_TIMEOUT_ERROR_MESSAGE = "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
_UNAVAILABLE_ERROR_MESSAGE = "OpenAI 서비스 호출에 실패했습니다."
_GENERATION_FAILED_ERROR_MESSAGE = "챗봇 응답 생성 처리 중 오류가 발생했습니다."
```

HTTP 응답은 기존 계약을 유지한다.

| 상태 | `code` | detail field | detail reason |
| ---: | --- | --- | --- |
| 503 | `SERVICE_UNAVAILABLE` | `openai_api` | `OPENAI_API_ERROR` |
| 504 | `GATEWAY_TIMEOUT` | `openai_api` | `OPENAI_API_TIMEOUT` |
| 500 | `AI_RESPONSE_FAILED` | `assistant_message` | `OPENAI_RESPONSE_PROCESSING_FAILED` |

OpenAI SDK 예외 메시지, Provider 요청·응답, 질문, 약물 정보와 Pydantic rejected value는 오류 metadata 또는 HTTP 오류 응답에 포함하지 않는다. USER 질문의 도메인 저장은 앞서 정의한 `CHAT_MESSAGE.content` 계약만 허용한다. `ValidationError`와 Provider·SDK 오류에는 입력 또는 응답 정보가 남을 수 있으므로 Adapter는 앞서 정의한 deferred raise로 원본 객체를 분리한다. Service도 같은 방식으로 외부 `ApiError`에서 Backend 오류와 예상 밖 내부 오류 객체를 분리한다. 운영 관측에는 trace ID, 안전한 내부 오류 분류, model·prompt version과 소요 시간만 사용하고 질문·약물·예외 본문과 traceback은 기록하지 않는다.

## HTTP와 캐시 계약

외부 API path와 response body는 변경하지 않는다.

| Method | Path | 기존 성공 상태 | #38 동작 |
| --- | --- | ---: | --- |
| `POST` | `/api/v1/prescriptions/{prescription_id}/chat-sessions` | `201` | body 변경 없음 |
| `GET` | `/api/v1/chat-sessions/{session_id}/messages` | `200` | body 변경 없음 |
| `POST` | `/api/v1/chat-sessions/{session_id}/messages` | `201` | DB에 저장된 ASSISTANT content와 실제 `model_name`·`prompt_version` 반환 |

세 endpoint의 모든 기존 성공·오류 응답은 `Cache-Control: no-store`를 가진다. CORS preflight처럼 Router endpoint를 실행하지 않고 최외곽 `CORSMiddleware`가 직접 처리하는 응답은 이 정책의 대상이 아니다.

Router에는 성공 응답의 `no-store`가 이미 구현되어 있다. 그러나 전역 exception handler가 만드는 인증·검증·도메인·예상 밖 오류 응답에는 이 header가 유지되지 않는다. 특히 PR #33에서 도입된 Starlette stack은 예상 밖 `Exception`의 `500` response를 사용자 HTTP middleware 바깥의 `ServerErrorMiddleware`에서 생성하므로, `fastapi_app.middleware("http")`를 추가하는 방식으로는 모든 오류 응답을 덮을 수 없다.

`app/core/chat_cache_control.py`에 path 판정 pure helper와 순수 ASGI `ChatNoStoreMiddleware`를 둔다. 최종 stack은 `CORSMiddleware(ChatNoStoreMiddleware(fastapi_app))` 순서로 조립한다. 이 wrapper는 두 chat path에 대한 `http.response.start`만 가로채 기존 header를 보존하면서 `Cache-Control: no-store`를 덮어쓴다. 따라서 Router 성공 response, FastAPI exception handler의 인증·검증·도메인 오류, `ServerErrorMiddleware`의 예상 밖 `500`이 모두 같은 정책을 통과하고 CORS는 계속 최외곽에서 적용된다.

path 판정은 query string을 제외한 ASGI `scope["path"]`를 segment 단위로 비교한다. `/api/v1/chat-sessions/{session_id}/messages`와 `/api/v1/prescriptions/{prescription_id}/chat-sessions` 두 형태와 각각의 단일 trailing slash만 허용하고, 식별자의 UUID 유효성은 판정 조건으로 삼지 않아 잘못된 UUID의 `422`에도 정책을 적용한다. 추가 하위 path와 문자열 일부만 일치하는 path는 제외한다. 전체 의료 API의 공통 `no-store` 정책으로 확장하는 변경은 #38 범위를 넘으므로 별도 보안 정책 작업으로 다룬다.

## 테스트 설계

### Adapter 단위 테스트

`app/tests/chat_ai/test_engine_adapter.py`

- 질문과 모든 약물 필드의 정확한 변환
- `Decimal` 정밀도와 `duration_days` 보존
- `prescription_id`와 다른 식별자의 Provider payload 제외
- 성공 결과와 model·prompt metadata 전달
- timeout, unavailable, configuration, invalid response와 입력 검증 오류 변환
- 변환된 Backend 오류의 `__cause__`와 `__context__`가 모두 `None`
- 잘못된 model·timeout이 `reply()` 안에서 안전하게 변환됨
- 31개 약물에서 Provider가 호출되지 않음

### Repository·Service 통합 테스트

`app/tests/repositories/test_chat_repository.py`와 `app/tests/chat/test_chat_service.py`

- SQL 소유권 확인과 활성 세션 제한
- USER·ASSISTANT 연속 번호와 상태 전이
- 성공 content와 metadata, `last_message_at` 저장
- `commit_failed_message_pair()`의 전체 pending transaction commit 경계와 호출 순서
- 실패 즉시 commit 후 후속 rollback에서도 USER와 FAILED ASSISTANT 한 쌍만 유지
- 실패 commit 자체가 예외를 발생시켜도 그 예외 chain에 앞선 Provider·AI 오류가 없고 transaction이 rollback됨
- 고정 `error_code`, `error_message`, `completed_at` 저장
- 외부 `ApiError`의 `__cause__`와 `__context__`가 모두 `None`
- 실패 시 content와 생성 metadata가 `NULL`
- engine 호출 전 소유권·상태 검사
- 약물 `display_order` 전달

동시성은 `app/tests/chat_integration/`에서 시나리오에 따라 두 개 또는 세 개의 독립 DB session과 제어 가능한 Fake Engine으로 검증한다. 이 디렉터리의 `conftest.py`는 상위 `isolate_database` fixture를 대체하고, 각 connection에서 보이는 committed 합성 fixture를 준비한다. 테스트 종료 시 생성한 row를 명시적으로 정리하며 병렬 테스트 실행 대상에서 제외한다.

첫 요청이 AI 생성 중일 때 두 번째 요청이 Engine에 진입하지 못하고, 첫 요청 완료 후 진행되며 최종 번호가 `1·2·3·4`인지 확인한다. 같은 처방을 참조하는 서로 다른 두 세션은 서로를 막지 않는지도 검증해 locking query가 처방·문서 row까지 잠그지 않음을 확인한다. 두 번째 요청의 전체 소요 시간이 두 요청 기준 참고 지연 안인지 검증하되 이를 전체 최대 대기시간으로 표현하지 않는다. 세 요청을 동시에 보내도 최종 번호가 `1·2·3·4·5·6`으로 저장되는 별도 correctness 테스트를 두고, 세 번째 요청에는 `2 × timeout` 상한을 주장하지 않는다.

### API 테스트

`app/tests/test_chat_cache_control.py`

- path helper가 메시지 route와 처방 기반 세션 생성 route, 각각의 단일 trailing slash만 선택함
- 잘못된 UUID 문자열은 선택하고 추가 하위 path·부분 일치·무관한 API는 제외함
- ASGI wrapper가 대상 response의 기존 header를 보존하면서 `Cache-Control`만 `no-store`로 덮어씀
- 비대상 path와 HTTP가 아닌 ASGI scope는 그대로 통과시킴

`app/tests/chat_apis/test_chat_message_api.py`

- 성공 `201` body와 저장된 content 일치
- 성공, 인증·검증·도메인 오류와 `500`·`503`·`504`의 `Cache-Control: no-store`
- 잘못된 UUID로 발생한 `422`를 포함해 두 chat route 형태의 오류 응답에 `no-store`가 유지됨
- `ASGITransport(raise_app_exceptions=False)`로 예상 밖 `Exception`의 `500`도 `no-store`와 CORS를 함께 유지함
- 기존 `WWW-Authenticate` 같은 response header를 cache wrapper가 보존함
- `500`, `503`, `504` code·message·details
- 실패 후 목록에서 USER와 FAILED ASSISTANT 조회
- DB lock wait timeout의 공통 `500 INTERNAL_SERVER_ERROR`와 새 메시지 미생성
- 인증, 소유권과 종료 세션 오류
- 실제 OpenAI 대신 dependency override Fake Engine 사용

최신 공통 오류·header·CORS 회귀 계약은 기존 `tests/integration/test_cors_and_errors.py`를 수정하지 않고 그대로 실행한다. 이 테스트는 PR #46의 `ApiError.headers`, `WWW-Authenticate`, 공통 오류 body와 최외곽 CORS 동작이 `app/main.py`의 ASGI wrapper 추가 후에도 유지되는지 검증한다.

### SQL 예외·로그 안전 테스트

`app/tests/core/test_database_logging_safety.py`

- Async engine이 `hide_parameters=True`로 생성됨
- 합성 sentinel 질문·약물 bind parameter를 포함한 의도적 SQL 실패의 예외 문자열에 sentinel이 없음
- SQLAlchemy echo/logger를 활성화해 포착한 로그에도 sentinel이 없음
- 예외와 로그에 parameter 숨김 표시가 존재함
- 실제 환자정보, Provider payload와 API Key를 사용하지 않음

### Backend–AI 계약 테스트

`tests/contract/test_chat_ai_backend_contract.py`

Adapter, `ChatGenerator`와 Stub Provider를 한 번 통과시켜 다음을 검증한다.

- Backend DTO의 모든 약물 필드가 AI JSON에 반영됨
- `duration_days`가 누락되지 않음
- `Decimal`이 문자열로 직렬화됨
- 불완전한 용량 값·단위 쌍이 함께 제외됨
- 식별자와 과거 대화가 payload에 없음
- 결과 metadata가 Backend 출력 계약과 일치함

### 검증 명령

```bash
uv run pytest app/tests/chat_ai app/tests/chat app/tests/chat_apis app/tests/chat_integration app/tests/repositories/test_chat_repository.py -q
uv run pytest tests/contract/test_chat_ai_backend_contract.py -q
uv run pytest tests/integration/test_cors_and_errors.py -q
uv run ruff check .
uv run ruff format . --check
uv run mypy app ai_worker
bash scripts/ci/run_test.sh
git diff --check
```

PR gate와 로컬 전체 테스트 스크립트는 공식 계약 테스트 위치를 실제 실행하도록 다음처럼 맞춘다.

```bash
uv run coverage run -m pytest app tests/contract
```

`.github/workflows/checks.yml`과 `scripts/ci/run_test.sh`를 함께 변경해 CI와 로컬 완료 검증이 같은 범위를 사용하게 한다. `tests/integration/`와 `tests/e2e/` 전체를 새로 gate에 포함하는 변경은 #38 범위 밖이며 별도 테스트 인프라 작업으로 다룬다.

현재 `evals/`에는 Chat AI 동작을 평가하는 실행 가능한 suite가 없고, 이번 PR은 프롬프트·Provider client·생성 정책을 변경하지 않는다. 따라서 #38에서 근거 없는 새 품질 점수나 사례를 만들지 않는다. 대신 기존 `app/tests/chat_ai/` 전체를 회귀 baseline으로 실행하고 Backend–AI 계약 테스트로 동일 AI Core에 전달되는 최소 입력과 출력 metadata를 결정적으로 검증한다. 이 과정에서 기존 AI Core 테스트가 실패하면 프롬프트를 조정해 통과시키지 않고 Backend Adapter를 수정한다. 실제 OpenAI smoke test는 합성 입력만 사용하되 비밀 API Key가 필요한 선택 검증이며 PR 필수 조건으로 두지 않는다. Chat AI 의료 품질 eval suite 구축은 평가 기준과 dataset 합의가 필요한 별도 Issue로 분리한다.

## 문서 변경

`docs/api.md`에 다음 내용을 추가한다.

- 메시지 전송 endpoint와 동기 one-cycle 의미
- 성공 response와 `500`·`503`·`504` 오류
- 채팅 세션 생성과 메시지 조회·전송의 모든 성공·오류 응답 `Cache-Control: no-store`
- 현재 질문과 확정 약물만 AI에 전달하는 데이터 경계
- 실패 시 USER와 FAILED ASSISTANT를 보존하는 재조회 동작
- 동일 세션 동시 요청의 직렬화, 두 요청 기준 참고 지연과 세 개 이상 과부하 시 최대시간 비보장
- DB lock wait timeout의 공통 `500 INTERNAL_SERVER_ERROR`와 새 메시지 미생성·재조회 불변 동작

`docs/deployment.md`에는 배포 OpenAI timeout `T`, 처리 여유 `M`과 코드로 강제되는 동일 세션 최대 동시 전송 `N`을 기록하고, Nginx read timeout `>= N × T + M`, MySQL lock wait timeout `> (N - 1) × T + M`인지 확인하는 항목을 둔다. `N`이 없으면 배포하지 않는다. 모든 replica·worker의 process별 DB pool과 MySQL 전체 connection 예산, lock waiter를 포함한 in-flight chat, 비채팅 예비 connection과 장시간 점유 tradeoff도 같은 위치에서 확인한다. 기존 Chat AI Core 설계에는 후속 Backend 연동 문서 링크를 추가한다. API body와 application DB schema 문서는 변경하지 않는다.

## 예상 변경 파일

```text
app/services/chat_ai/__init__.py
app/services/chat_generator_engine.py
app/services/chat.py
app/repositories/chat_repository.py
app/repositories/prescription_repository.py
app/dependencies/services.py
app/core/chat_cache_control.py
app/core/db/databases.py
app/main.py

app/tests/core/test_database_logging_safety.py
app/tests/test_chat_cache_control.py
app/tests/chat_ai/test_engine_adapter.py
app/tests/chat/test_chat_service.py
app/tests/chat_apis/test_chat_message_api.py
app/tests/chat_integration/conftest.py
app/tests/chat_integration/test_chat_concurrency.py
app/tests/repositories/test_chat_repository.py
tests/contract/test_chat_ai_backend_contract.py

.github/workflows/checks.yml
scripts/ci/run_test.sh
docs/api.md
docs/deployment.md
docs/designs/ceohwj/medication-chat-ai-generation-design.md
docs/designs/ceohwj/medication-chat-ai-backend-integration-design.md
```

Router, DTO, DB model, migration, 환경변수와 dependency manifest는 변경하지 않는다. `app/main.py`는 PR #33의 `fastapi_app`과 최외곽 CORS 구조를 유지하면서 그 사이에 chat cache-control ASGI wrapper를 조립하는 범위로만 수정한다. `app/core/errors.py`의 공통 오류 body 계약과 handler는 변경하지 않는다.

## 완료 기준

- `NotConfiguredChatEngine` 없이 실제 `ChatGeneratorEngine`이 `ChatService`에 주입된다.
- 소유한 활성 세션에서만 AI가 호출된다.
- 확정 약물의 `Decimal` 용량과 `duration_days`가 손실 없이 전달된다.
- Provider payload에 사용자·세션·처방·메시지 식별자와 과거 대화가 없다.
- 동일 세션의 두 개와 세 개 동시 전송에서 메시지 번호 충돌이 발생하지 않는다.
- 성공 시 ASSISTANT content와 model·prompt metadata가 저장되고 `201` response와 일치한다.
- 실패 시 USER와 FAILED ASSISTANT, 안전한 오류 code·message와 완료 시각이 저장된다.
- timeout, unavailable과 생성 처리 실패가 `504`, `503`, `500`으로 구분된다.
- Adapter의 Backend 오류와 Service의 `ApiError`에 원본 오류 `__cause__`·`__context__`가 남지 않는다.
- 실패 persistence가 예외를 발생시켜도 그 예외 chain에 앞선 Provider·AI 오류가 남지 않는다.
- SQLAlchemy 예외 문자열과 echo 로그에 합성 질문·약물 bind parameter가 노출되지 않는다.
- 채팅 세션 생성과 메시지 조회·전송의 모든 성공·오류 응답에 `Cache-Control: no-store`가 적용된다.
- 관련 단위, MySQL 통합, API와 계약 테스트가 통과한다.
- PR #46의 공통 오류·header·CORS 통합 테스트가 통과한다.
- CI와 로컬 전체 테스트 스크립트가 `tests/contract/`를 실행한다.
- 두 요청 기준 참고 지연과 세 개 이상 동시 요청의 최대시간 비보장을 API 문서와 테스트에 구분해 명시한다.
- 새 dependency, 환경변수, migration과 API body 변경이 없다.
- 실제 환자정보, API Key, Provider·의료 본문 로그가 포함되지 않는다.

## 배포 전 운영 Gate

아래 항목은 환경별 실제 값과 운영 승인자가 있어야 판정할 수 있으므로 Issue #38 구현·merge 완료 기준과 분리한 production deployment gate다. 대상 환경이 확정되지 않은 구현 PR에서 값을 추정하거나 승인자를 대신 기록하지 않는다. `docs/deployment.md`의 해당 환경 기록이 비어 있거나 조건을 충족하지 않으면 코드를 production에 배포하지 않는다.

- 배포 대상의 OpenAI timeout, Nginx read timeout과 MySQL lock wait timeout이 설정 상대식 하한을 만족하는지 확인하고 기록한다.
- lock waiter를 포함한 전체 in-flight chat 요청 수, 비채팅 예비 connection과 DB pool·overflow·wait 설정을 기록한다.
- 외부 호출 중 DB transaction과 connection을 유지하는 tradeoff를 해당 환경의 책임자가 승인한다.
- 수용량이 부족하면 row lock을 약화하지 않고 admission/rate limiting 또는 비동기 worker 설계를 별도 계약과 Issue로 도입한다.

## PR 구성

- Issue: #38
- Branch: `feat/38-chat-ai-backend-integration`
- Base: `develop`
- 권장 제목: `✨ feat: 복약 챗봇 AI Backend 연동`
- 최초 상태: Draft
- 필수 리뷰: `@phina-io`, `@hazelnutflavoured`

Issue #38의 관련 영역에서 Database와 Infrastructure를 체크하고, 작업 내용에 동일 세션 row lock·MySQL 동시성 검증 및 `tests/contract/` CI 실행을 추가한다. 선행 PR #33의 middleware 계약과 PR #46의 `ApiError.headers`·공통 오류 계약이 현재 `develop`에 반영되어 있으며, 구현은 두 계약을 기준으로 진행한다. `.github/workflows/checks.yml` 변경은 `.github/` CODEOWNER인 `@ceohwj`, `@hazelnutflavoured`가 함께 검토한다.

AI Core 구현은 선행 PR #35의 계약을 소비하며 이 PR에서 프롬프트와 Provider 동작을 다시 변경하지 않는다. 구현, 테스트와 계약 문서를 하나의 #38 PR에 포함해 Backend CODEOWNER가 전체 연결 흐름을 검토할 수 있게 한다.
