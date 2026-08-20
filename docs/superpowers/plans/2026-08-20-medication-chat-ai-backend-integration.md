# Medication Chat AI Backend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the existing provider-neutral `ChatGenerator` to the synchronous Backend chat flow while preserving ownership, persistence, error, privacy, concurrency, and HTTP cache contracts.

**Architecture:** Keep `ChatService` behind the existing `ChatEngine` protocol and add a thin `ChatGeneratorEngine` adapter that performs all Backend-to-AI model conversion. Serialize sends only within one `CHAT_SESSION` by locking its primary-key row, persist one USER/ASSISTANT pair per request, and wrap the inner FastAPI app with a chat-path cache-control ASGI layer inside the outer CORS layer.

**Tech Stack:** Python 3.13, FastAPI/Starlette ASGI, SQLAlchemy 2 async ORM, MySQL 8.0/InnoDB, Pydantic 2, OpenAI Responses SDK, pytest/pytest-asyncio, httpx ASGI transport, Ruff, Mypy, GitHub Actions.

**Spec:** `docs/designs/ceohwj/medication-chat-ai-backend-integration-design.md`

## Global Constraints

- Work only on `feat/38-chat-ai-backend-integration`, based on merged PR #33 commit `4e7df35`; do not create or switch branches on the user's behalf.
- Before implementation, update Issue #38 to check Backend, Database, Infrastructure, and Documentation and add the row-lock/concurrency and `tests/contract/` CI work items.
- Do not change API paths, response bodies, database models, migrations, dependency manifests, or environment-variable names/defaults.
- Reuse `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, and the process-scoped `AsyncOpenAI` client from `app/main.py`.
- Send only the current question and confirmed medications; never send user, session, prescription, message, or document identifiers or previous chat history to the provider.
- Preserve `Decimal` dose precision and `duration_days`; do not truncate prescriptions with more than 30 medications and do not call the provider for invalid AI input.
- Store only fixed safe failure codes/messages. Provider/SDK/Pydantic bodies, medical inputs, and raw exception objects must not escape the adapter/service boundary or enter logs.
- Sanitized Backend errors and public `ApiError` objects must have both `__cause__ is None` and `__context__ is None`; raising inside an `except` block, even with `from None`, is forbidden.
- Same-session sends block until the prior transaction commits; different sessions remain independent. Do not introduce `NOWAIT`, a new 409 response, streaming, a worker, or RAG.
- All Router-generated success/error responses for the three chat endpoints receive `Cache-Control: no-store`; CORS preflight responses generated directly by outer `CORSMiddleware` remain outside this policy.
- Use only de-identified synthetic Korean test data. Never add API keys, `.env` content, real patient information, provider request/response bodies, or raw medical exception logs.
- Run deterministic tests without a real OpenAI call. `RUN_OPENAI_CHAT_SMOKE=1` remains optional and is not a PR gate.

## File Responsibility Map

- `app/services/chat_ai/__init__.py`: Backend-facing chat protocol, DTOs, and provider-neutral Backend error types.
- `app/services/chat_generator_engine.py`: the only Backend-to-Chat-AI adapter and AI-error sanitizer.
- `app/core/chat_cache_control.py`: exact chat path predicate and pure ASGI response-header wrapper.
- `app/repositories/chat_repository.py`: chat-session ownership locking and message state persistence.
- `app/repositories/prescription_repository.py`: deterministic medication ordering.
- `app/services/chat.py`: ownership/status gate, one-cycle orchestration, persistence, and HTTP error mapping.
- `app/dependencies/services.py`: request-scoped adapter/provider assembly around the process-scoped OpenAI client.
- `app/main.py`: final ASGI stack assembly only.
- `app/tests/**`, `tests/contract/**`: deterministic unit, API, repository, MySQL concurrency, and Backend–AI contract verification.
- `.github/workflows/checks.yml`, `scripts/ci/run_test.sh`: identical `app tests/contract` pytest scope.
- `docs/api.md`, `docs/deployment.md`, and the two design documents: public behavior, deployment prerequisites, and cross-document traceability.

---

### Task 1: Backend Chat Contract and `ChatGeneratorEngine`

**Files:**
- Modify: `app/services/chat_ai/__init__.py:1-76`
- Create: `app/services/chat_generator_engine.py`
- Modify: `app/services/chat.py:115-129`
- Modify: `app/tests/chat_ai/test_public_contract.py:1-35`
- Create: `app/tests/chat_ai/test_engine_adapter.py`

**Interfaces:**
- Consumes: `ChatGenerator(provider: ChatProvider, model: str, timeout_seconds: float)`, `ChatGenerationInput`, and `ChatGenerationResult` from `app.services.chat_ai`.
- Produces: `ChatGeneratorEngine(provider: ChatProvider, model: str, timeout_seconds: float)` implementing `ChatEngine.reply(ChatReplyInput) -> ChatReplyOutput`; `ChatMedicationInput.dose_value: Decimal | None`; `ChatMedicationInput.duration_days: int | None`; `ChatGenerationFailedError` exported at package root.
- Transitional rule: keep `NotConfiguredChatEngine` until Task 4 so intermediate imports remain valid; Task 4 removes it together with the optional service fallback.

- [ ] **Step 1: Update the public-contract test so the old float/no-duration contract fails**

Replace the Backend DTO construction in `test_public_contract_preserves_backend_medication_input_at_package_root` and add an error export assertion:

```python
from decimal import Decimal

from app.services.chat_ai import ChatGenerationFailedError

reply_medication = ChatReplyMedicationInput(
    medication_name="합성약",
    dose_value=Decimal("1.500"),
    dose_unit="mg",
    frequency_per_day=1,
    timing_text="저녁 식후",
    duration_days=7,
)

assert reply_medication.dose_value == Decimal("1.500")
assert reply_medication.duration_days == 7
assert issubclass(ChatGenerationFailedError, Exception)
```

- [ ] **Step 2: Create adapter tests covering success, conversion, provider exclusion, limits, mapping, and detached exception chains**

Use a deterministic provider double and exact Backend input:

```python
import json
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.chat_ai import (
    ChatGenerationFailedError,
    ChatMedicationInput,
    ChatReplyInput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)
from app.services.chat_ai.exceptions import (
    ChatGenerationInvalidResponseError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)
from app.services.chat_ai.schemas import ProviderChatResponse
from app.services.chat_generator_engine import ChatGeneratorEngine


class CapturingProvider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse:
        self.calls.append(
            {
                "model": model,
                "instructions": instructions,
                "input_json": input_json,
                "max_output_tokens": max_output_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return ProviderChatResponse(content="합성 답변", model_name="gpt-4o-mini-2024-07-18")


def reply_input(*, count: int = 1) -> ChatReplyInput:
    return ChatReplyInput(
        prescription_id=uuid4(),
        medications=[
            ChatMedicationInput(
                medication_name=f"합성약 {index}",
                dose_value=Decimal("0.500"),
                dose_unit="mg",
                frequency_per_day=2,
                timing_text="아침 식후",
                duration_days=7,
            )
            for index in range(count)
        ],
        content="현재 질문",
    )
```

Add these named cases:

```python
async def test_reply_preserves_decimal_duration_and_excludes_backend_identifiers() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)

    result = await engine.reply(reply_input())
    payload = json.loads(str(provider.calls[0]["input_json"]))

    assert payload == {
        "question": "현재 질문",
        "medications": [
            {
                "medication_name": "합성약 0",
                "dose_value": "0.500",
                "dose_unit": "mg",
                "frequency_per_day": 2,
                "timing_text": "아침 식후",
                "duration_days": 7,
            }
        ],
    }
    assert "prescription_id" not in str(provider.calls[0]["input_json"])
    assert result.content == "합성 답변"
    assert result.model_name == "gpt-4o-mini-2024-07-18"
    assert result.prompt_version == "chat-prompt-v1"


@pytest.mark.parametrize(
    ("provider_error", "backend_error"),
    [
        (ChatGenerationTimeoutError("raw timeout payload"), ChatTimeoutError),
        (ChatGenerationUnavailableError("raw unavailable payload"), ChatServiceUnavailableError),
        (ChatGenerationInvalidResponseError("raw response payload"), ChatGenerationFailedError),
    ],
)
async def test_reply_maps_known_errors_without_retaining_raw_exception_chain(
    provider_error: Exception,
    backend_error: type[Exception],
) -> None:
    engine = ChatGeneratorEngine(
        provider=CapturingProvider(error=provider_error),
        model="gpt-4o-mini",
        timeout_seconds=1,
    )

    with pytest.raises(backend_error) as raised:
        await engine.reply(reply_input())

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def test_reply_rejects_thirty_one_medications_without_calling_provider() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(reply_input(count=31))

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
```

Add the configuration/input and unexpected-programming-error cases explicitly:

```python
@pytest.mark.parametrize(
    ("model", "timeout_seconds"),
    [
        ("", 1.0),
        ("gpt-4o-mini", 0.0),
        ("gpt-4o-mini", -1.0),
        ("gpt-4o-mini", float("nan")),
        ("gpt-4o-mini", float("inf")),
    ],
)
async def test_reply_sanitizes_invalid_configuration(model: str, timeout_seconds: float) -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model=model, timeout_seconds=timeout_seconds)

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(reply_input())

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def test_reply_sanitizes_empty_medication_input() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)
    chat_input = ChatReplyInput(prescription_id=uuid4(), medications=[], content="현재 질문")

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(chat_input)

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def test_reply_does_not_hide_unexpected_programming_error() -> None:
    programming_error = RuntimeError("synthetic programming failure")
    engine = ChatGeneratorEngine(
        provider=CapturingProvider(error=programming_error),
        model="gpt-4o-mini",
        timeout_seconds=1,
    )

    with pytest.raises(RuntimeError) as raised:
        await engine.reply(reply_input())

    assert raised.value is programming_error
```

Add one invalid-medication boundary test:

```python
async def test_reply_sanitizes_invalid_medication_without_calling_provider() -> None:
    provider = CapturingProvider()
    engine = ChatGeneratorEngine(provider=provider, model="gpt-4o-mini", timeout_seconds=1)
    chat_input = reply_input()
    invalid = ChatMedicationInput(
        medication_name="합성약",
        dose_value=Decimal("0"),
        dose_unit="mg",
        frequency_per_day=1,
        timing_text="저녁 식후",
        duration_days=7,
    )
    chat_input = ChatReplyInput(
        prescription_id=chat_input.prescription_id,
        medications=[invalid],
        content=chat_input.content,
    )

    with pytest.raises(ChatGenerationFailedError) as raised:
        await engine.reply(chat_input)

    assert provider.calls == []
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
```

- [ ] **Step 3: Run the focused tests and verify they fail for the intended missing contract/adapter**

Run:

```bash
uv run pytest app/tests/chat_ai/test_public_contract.py app/tests/chat_ai/test_engine_adapter.py -q
```

Expected: collection or assertion failures because `ChatGenerationFailedError`, `duration_days`, and `app.services.chat_generator_engine` do not exist yet.

- [ ] **Step 4: Implement the public contract and adapter with deferred error raising**

Update the Backend DTO and error type:

```python
from decimal import Decimal


@dataclass(frozen=True)
class ChatMedicationInput:
    medication_name: str
    dose_value: Decimal | None
    dose_unit: str | None
    frequency_per_day: int | None
    timing_text: str | None
    duration_days: int | None


class ChatGenerationFailedError(Exception):
    """Backend-safe chat input/configuration/response processing failure."""
```

Export `ChatGenerationFailedError` in `__all__`. Implement `ChatGeneratorEngine.reply` with no DB/FastAPI imports and no raise inside an `except` block:

```python
from pydantic import ValidationError

from app.services.chat_ai import (
    ChatEngine,
    ChatGenerationFailedError,
    ChatMedicationInput as BackendMedicationInput,
    ChatProvider,
    ChatReplyInput,
    ChatReplyOutput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)
from app.services.chat_ai.exceptions import (
    ChatGenerationError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)
from app.services.chat_ai.generator import ChatGenerator
from app.services.chat_ai.schemas import ChatGenerationInput, ChatGenerationResult
from app.services.chat_ai.schemas import ChatMedicationInput as GenerationMedicationInput


class ChatGeneratorEngine(ChatEngine):
    def __init__(self, *, provider: ChatProvider, model: str, timeout_seconds: float) -> None:
        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        mapped_error: ChatTimeoutError | ChatServiceUnavailableError | ChatGenerationFailedError | None = None
        result: ChatGenerationResult | None = None
        try:
            generator = ChatGenerator(
                provider=self._provider,
                model=self._model,
                timeout_seconds=self._timeout_seconds,
            )
            generation_input = ChatGenerationInput(
                question=chat_input.content,
                medications=[self._to_generation_medication(item) for item in chat_input.medications],
            )
            result = await generator.generate(generation_input)
        except ChatGenerationTimeoutError:
            mapped_error = ChatTimeoutError("챗봇 응답 생성 시간이 초과되었습니다.")
        except ChatGenerationUnavailableError:
            mapped_error = ChatServiceUnavailableError("챗봇 생성 서비스를 사용할 수 없습니다.")
        except (ChatGenerationError, ValidationError):
            mapped_error = ChatGenerationFailedError("챗봇 응답 생성 처리에 실패했습니다.")

        if mapped_error is not None:
            raise mapped_error
        if result is None:
            raise RuntimeError("ChatGenerator returned without a result")
        return ChatReplyOutput(
            content=result.content,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
        )

    @staticmethod
    def _to_generation_medication(item: BackendMedicationInput) -> GenerationMedicationInput:
        return GenerationMedicationInput(
            medication_name=item.medication_name,
            dose_value=item.dose_value,
            dose_unit=item.dose_unit,
            frequency_per_day=item.frequency_per_day,
            timing_text=item.timing_text,
            duration_days=item.duration_days,
        )
```

Keep the existing unwired Service importable and fully typed by changing only its medication DTO construction in this task:

```python
ChatMedicationInput(
    medication_name=medication.medication_name,
    dose_value=medication.dose_value,
    dose_unit=medication.dose_unit,
    frequency_per_day=medication.frequency_per_day,
    timing_text=medication.timing_text,
    duration_days=medication.duration_days,
)
```

Do not change locking, persistence, error handling, constructor injection, or dependencies until Task 4.

- [ ] **Step 5: Run adapter and existing AI Core regression tests**

Run:

```bash
uv run pytest app/tests/chat_ai -q
uv run mypy app
```

Expected: all deterministic Chat AI tests pass; no real API call occurs; Mypy reports no errors in the adapter boundary.

- [ ] **Step 6: Commit the independently testable adapter contract**

```bash
git add app/services/chat_ai/__init__.py app/services/chat_generator_engine.py app/services/chat.py app/tests/chat_ai
git commit -m "✨ feat: 챗봇 AI Backend 어댑터 추가"
```

---

### Task 2: Chat Cache-Control ASGI Boundary

**Files:**
- Create: `app/core/chat_cache_control.py`
- Modify: `app/main.py:48-56`
- Create: `app/tests/test_chat_cache_control.py`

**Interfaces:**
- Produces: `is_chat_api_path(path: str) -> bool`; `ChatNoStoreMiddleware(app: ASGIApp)`.
- Stack invariant: `ChatNoStoreMiddleware(fastapi_app)` is the `app` argument of the existing outer `CORSMiddleware`; all existing CORS configuration arguments remain unchanged.

- [ ] **Step 1: Write pure path and ASGI wrapper tests**

Create parameterized path tests that distinguish one optional trailing slash from malformed or extra paths:

```python
import pytest

from app.core.chat_cache_control import is_chat_api_path


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions/not-a-uuid/messages",
        "/api/v1/chat-sessions/not-a-uuid/messages/",
        "/api/v1/prescriptions/not-a-uuid/chat-sessions",
        "/api/v1/prescriptions/not-a-uuid/chat-sessions/",
    ],
)
def test_is_chat_api_path_accepts_only_supported_route_shapes(path: str) -> None:
    assert is_chat_api_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions/id/messages/extra",
        "/api/v1/chat-sessions/id/messages//",
        "//api/v1/chat-sessions/id/messages",
        "/api/v1/prescriptions/id/chat-sessions/extra",
        "/api/v1/guides/id",
        "/health",
    ],
)
def test_is_chat_api_path_rejects_unrelated_or_malformed_paths(path: str) -> None:
    assert not is_chat_api_path(path)
```

Add an ASGI recorder test that starts with `WWW-Authenticate: Bearer` and `Cache-Control: private`:

```python
from starlette.types import Message, Receive, Scope, Send

from app.core.chat_cache_control import ChatNoStoreMiddleware


async def test_middleware_preserves_headers_and_overwrites_cache_control() -> None:
    sent: list[Message] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"www-authenticate", b"Bearer"), (b"cache-control", b"private")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/chat-sessions/id/messages",
        "raw_path": b"/api/v1/chat-sessions/id/messages",
        "query_string": b"",
        "headers": [],
        "client": None,
        "server": None,
        "root_path": "",
    }
    await ChatNoStoreMiddleware(downstream)(scope, receive, send)

    headers = dict(sent[0]["headers"])
    assert headers[b"www-authenticate"] == b"Bearer"
    assert headers[b"cache-control"] == b"no-store"
```

Repeat with `/api/v1/guides/id` and a websocket scope, recording the downstream scope/message and asserting the middleware forwards both without header mutation.

- [ ] **Step 2: Run the cache-control test and verify the missing module failure**

```bash
uv run pytest app/tests/test_chat_cache_control.py -q
```

Expected: collection fails because `app.core.chat_cache_control` does not exist.

- [ ] **Step 3: Implement exact path matching and pure ASGI response interception**

```python
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def is_chat_api_path(path: str) -> bool:
    segments = path.split("/")
    if segments and segments[-1] == "":
        segments = segments[:-1]
    message_path = (
        len(segments) == 6
        and segments[:4] == ["", "api", "v1", "chat-sessions"]
        and bool(segments[4])
        and segments[5] == "messages"
    )
    session_path = (
        len(segments) == 6
        and segments[:4] == ["", "api", "v1", "prescriptions"]
        and bool(segments[4])
        and segments[5] == "chat-sessions"
    )
    return message_path or session_path


class ChatNoStoreMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_chat_api_path(scope["path"]):
            await self._app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self._app(scope, receive, send_no_store)
```

Wrap `fastapi_app` inside this middleware and keep `CORSMiddleware` outermost:

```python
app = CORSMiddleware(
    app=ChatNoStoreMiddleware(fastapi_app),
    allow_origins=config.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 4: Run unit and merged-PR regression tests**

```bash
uv run pytest app/tests/test_chat_cache_control.py tests/integration/test_cors_and_errors.py -q
uv run mypy app/core/chat_cache_control.py app/main.py
```

Expected: cache tests pass, both PR #33 CORS/error tests pass, and Mypy reports no errors.

- [ ] **Step 5: Commit the cache boundary**

```bash
git add app/core/chat_cache_control.py app/main.py app/tests/test_chat_cache_control.py
git commit -m "✨ feat: 채팅 API 캐시 방지 경계 추가"
```

---

### Task 3: Session Ownership Lock and Medication Ordering

**Files:**
- Modify: `app/repositories/chat_repository.py:1-44`
- Modify: `app/repositories/prescription_repository.py:55-57`
- Create or modify: `app/tests/repositories/test_chat_repository.py`

**Interfaces:**
- Produces: `ChatRepository.get_session_owned_for_update(*, session_id: UUID, user_id: UUID) -> ChatSession | None`.
- Preserves: existing non-locking `get_session_owned` for message-list reads.
- Strengthens: `PrescriptionRepository.get_medications` returns `display_order` ascending.

- [ ] **Step 1: Write query-shape, ownership, and ordering tests**

For the query-shape test, capture the statement passed to an `AsyncMock` session and compile it with the MySQL dialect:

```python
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.chat_repository import ChatRepository


async def test_get_session_owned_for_update_locks_only_outer_chat_session_statement() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    repository = ChatRepository(cast(AsyncSession, session))

    await repository.get_session_owned_for_update(session_id=uuid4(), user_id=uuid4())

    statement = session.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert compiled.count("FOR UPDATE") == 1
    assert "FROM chat_session" in compiled
    assert "EXISTS (SELECT 1" in compiled
    assert "FROM prescription INNER JOIN medical_document" in compiled
    assert "FROM chat_session INNER JOIN" not in compiled
```

Using the repository DB fixture pattern from `test_guide_repository.py`, add synthetic owner/intruder records and assert owner returns the session while intruder returns `None`. Insert medications with display orders `3, 1, 2` and assert returned orders are `[1, 2, 3]`.

- [ ] **Step 2: Run repository tests and verify the new method/ordering failures**

```bash
uv run pytest app/tests/repositories/test_chat_repository.py -q
```

Expected: failure because `get_session_owned_for_update` does not exist and medication order is not guaranteed.

- [ ] **Step 3: Implement the correlated ownership `EXISTS` and outer row lock**

```python
from sqlalchemy import exists, select

from app.models.medical_documents import MedicalDocument


async def get_session_owned_for_update(self, *, session_id: UUID, user_id: UUID) -> ChatSession | None:
    owned_prescription = exists(
        select(1)
        .select_from(Prescription)
        .join(MedicalDocument, MedicalDocument.id == Prescription.document_id)
        .where(
            Prescription.id == ChatSession.prescription_id,
            MedicalDocument.user_id == user_id,
        )
    )
    result = await self.session.execute(
        select(ChatSession)
        .where(ChatSession.id == session_id, owned_prescription)
        .with_for_update()
    )
    return result.scalar_one_or_none()
```

Do not add eager loading, an outer JOIN, or a locking clause inside the subquery. Add deterministic medication ordering:

```python
result = await self.session.execute(
    select(Medication)
    .where(Medication.prescription_id == prescription_id)
    .order_by(Medication.display_order)
)
```

- [ ] **Step 4: Run repository tests and static checks**

```bash
uv run pytest app/tests/repositories/test_chat_repository.py -q
uv run mypy app/repositories/chat_repository.py app/repositories/prescription_repository.py
```

Expected: query-shape, ownership, and ordering tests pass; Mypy reports no errors.

- [ ] **Step 5: Commit the persistence primitives**

```bash
git add app/repositories/chat_repository.py app/repositories/prescription_repository.py app/tests/repositories/test_chat_repository.py
git commit -m "✨ feat: 채팅 세션 잠금과 약물 순서 보장"
```

---

### Task 4: One-Cycle Chat Service, Failure Persistence, and Dependency Wiring

**Files:**
- Modify: `app/services/chat.py:1-192`
- Modify: `app/repositories/chat_repository.py:88-101`
- Modify: `app/services/chat_ai/__init__.py:44-76`
- Modify: `app/dependencies/services.py:1-177`
- Create: `app/tests/chat/test_chat_service.py`
- Extend: `app/tests/repositories/test_chat_repository.py`
- Create: `app/tests/chat/test_chat_dependencies.py`

**Interfaces:**
- Consumes: `ChatGeneratorEngine` from Task 1 and `get_session_owned_for_update` from Task 3.
- Produces: `ChatService(prescription_repository: PrescriptionRepository, chat_repository: ChatRepository, engine: ChatEngine)`; `ChatRepository.mark_failed(message: ChatMessage, *, error_code: str, error_message: str, completed_at: datetime)`; `get_chat_engine(client: AsyncOpenAI) -> ChatEngine`; fully wired `get_chat_service`.
- Removes: `NotConfiguredChatEngine` and `ChatService(engine=None)`.

- [ ] **Step 1: Write service success/input/order tests with strict repository doubles**

Use `AsyncMock(spec=ChatRepository)`, `AsyncMock(spec=PrescriptionRepository)`, `SimpleNamespace`, fixed UUIDs, UTC datetimes, and a recording engine. The primary success test must assert:

```python
assert chat_repository.get_session_owned_for_update.await_args.kwargs == {
    "session_id": session_id,
    "user_id": user.id,
}
assert engine.inputs[0].prescription_id == prescription_id
assert engine.inputs[0].content == "현재 질문"
assert engine.inputs[0].medications[0].dose_value == Decimal("0.500")
assert engine.inputs[0].medications[0].duration_days == 7
assert [call.kwargs["message_seq"] for call in chat_repository.create_message.await_args_list] == [1, 2]
assert result.content == "합성 답변"
assert result.model_name == "gpt-4o-mini-2024-07-18"
assert result.prompt_version == "chat-prompt-v1"
```

Add owner-not-found and closed-session tests asserting `engine.reply` and `create_message` were not called. Add an ordering test that the engine receives medications in the exact order returned by the prescription repository.

- [ ] **Step 2: Write parameterized failure tests, persistence checks, and detached-chain assertions**

Parameterize these mappings:

```python
@pytest.mark.parametrize(
    ("engine_error", "status_code", "api_code", "field", "reason", "db_code", "db_message"),
    [
        (
            ChatTimeoutError("raw timeout"),
            504,
            "GATEWAY_TIMEOUT",
            "openai_api",
            "OPENAI_API_TIMEOUT",
            "OPENAI_API_TIMEOUT",
            "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다.",
        ),
        (
            ChatServiceUnavailableError("raw unavailable"),
            503,
            "SERVICE_UNAVAILABLE",
            "openai_api",
            "OPENAI_API_ERROR",
            "OPENAI_API_ERROR",
            "OpenAI 서비스 호출에 실패했습니다.",
        ),
        (
            ChatGenerationFailedError("raw invalid response"),
            500,
            "AI_RESPONSE_FAILED",
            "assistant_message",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "챗봇 응답 생성 처리 중 오류가 발생했습니다.",
        ),
        (
            RuntimeError("raw synthetic medical payload"),
            500,
            "AI_RESPONSE_FAILED",
            "assistant_message",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
            "챗봇 응답 생성 처리 중 오류가 발생했습니다.",
        ),
    ],
)
```

For every row, assert `mark_failed` receives the fixed DB values, USER and ASSISTANT were already created, and the raised `ApiError` has exact status/code/detail plus both exception links detached:

```python
assert raised.value.__cause__ is None
assert raised.value.__context__ is None
assert chat_repository.mark_failed.await_args.kwargs["error_code"] == db_code
assert chat_repository.mark_failed.await_args.kwargs["error_message"] == db_message
```

Extend the real repository failure test to reload after a subsequent rollback and assert `FAILED`, fixed `error_code`, fixed `error_message`, non-null `completed_at`, null content/model/prompt, and preserved USER row.

- [ ] **Step 3: Write dependency tests before changing assembly**

Verify required constructor injection and exact dependency identity:

```python
from typing import cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from app.dependencies.services import get_chat_engine, get_chat_service
from app.repositories.chat_repository import ChatRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.chat import ChatService
from app.services.chat_ai import ChatEngine
from app.services.chat_generator_engine import ChatGeneratorEngine


def test_get_chat_engine_builds_real_adapter() -> None:
    client = cast(AsyncOpenAI, AsyncMock())
    engine = get_chat_engine(client)
    assert isinstance(engine, ChatGeneratorEngine)


def test_get_chat_service_injects_exact_engine_and_repositories() -> None:
    prescription_repository = cast(PrescriptionRepository, object())
    chat_repository = cast(ChatRepository, object())
    engine = cast(ChatEngine, object())

    service = get_chat_service(prescription_repository, chat_repository, engine)

    assert service._prescription_repo is prescription_repository
    assert service._chat_repo is chat_repository
    assert service._engine is engine


def test_chat_service_requires_engine() -> None:
    with pytest.raises(TypeError):
        ChatService(cast(PrescriptionRepository, object()), cast(ChatRepository, object()))
```

- [ ] **Step 4: Run service/repository/dependency tests and confirm failures**

```bash
uv run pytest app/tests/chat app/tests/repositories/test_chat_repository.py -q
```

Expected: failures show the old non-locking lookup, float conversion, missing `duration_days`, missing safe DB message, retained exception context, optional engine fallback, and absent DI provider.

- [ ] **Step 5: Implement required engine injection and exact medication conversion**

Change constructor and send lookup:

```python
from app.models.chat import ChatGenerationStatus, ChatMessage, ChatSession, ChatSessionStatus
from app.services.chat_ai import ChatEngine, ChatMedicationInput, ChatReplyInput, ChatReplyOutput


class ChatService:
    def __init__(
        self,
        prescription_repository: PrescriptionRepository,
        chat_repository: ChatRepository,
        engine: ChatEngine,
    ) -> None:
        self._engine = engine
        self._prescription_repo = prescription_repository
        self._chat_repo = chat_repository
```

Use `get_session_owned_for_update` only in `send_message`; leave `list_messages` on `get_session_owned`. Convert medications without `float()` and include `duration_days`:

```python
ChatMedicationInput(
    medication_name=medication.medication_name,
    dose_value=medication.dose_value,
    dose_unit=medication.dose_unit,
    frequency_per_day=medication.frequency_per_day,
    timing_text=medication.timing_text,
    duration_days=medication.duration_days,
)
```

- [ ] **Step 6: Implement safe failure metadata and deferred public error raising**

Define the three fixed DB messages exactly:

```python
_TIMEOUT_ERROR_MESSAGE = "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
_UNAVAILABLE_ERROR_MESSAGE = "OpenAI 서비스 호출에 실패했습니다."
_GENERATION_FAILED_ERROR_MESSAGE = "챗봇 응답 생성 처리 중 오류가 발생했습니다."
```

Change `mark_failed` to assign `error_message` before committing. In `send_message`, never raise while inside an `except` block:

```python
async def mark_failed(
    self,
    message: ChatMessage,
    *,
    error_code: str,
    error_message: str,
    completed_at: datetime,
) -> ChatMessage:
    message.generation_status = ChatGenerationStatus.FAILED
    message.error_code = error_code
    message.error_message = error_message
    message.completed_at = completed_at
    await self.session.commit()
    return message
```

Use this deferred Service mapping:

```python
failure: tuple[str, str, ApiError] | None = None
try:
    result = await self._engine.reply(chat_input)
except ChatTimeoutError:
    failure = (
        "OPENAI_API_TIMEOUT",
        _TIMEOUT_ERROR_MESSAGE,
        ApiError(
            status_code=504,
            code="GATEWAY_TIMEOUT",
            message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
            details=[ErrorDetail(field="openai_api", reason="OPENAI_API_TIMEOUT")],
        ),
    )
except ChatServiceUnavailableError:
    failure = (
        "OPENAI_API_ERROR",
        _UNAVAILABLE_ERROR_MESSAGE,
        ApiError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            details=[ErrorDetail(field="openai_api", reason="OPENAI_API_ERROR")],
        ),
    )
except ChatGenerationFailedError:
    failure = (
        "OPENAI_RESPONSE_PROCESSING_FAILED",
        _GENERATION_FAILED_ERROR_MESSAGE,
        ApiError(
            status_code=500,
            code="AI_RESPONSE_FAILED",
            message="AI 답변 생성에 실패했습니다.",
            details=[ErrorDetail(field="assistant_message", reason="OPENAI_RESPONSE_PROCESSING_FAILED")],
        ),
    )
except Exception:
    failure = (
        "OPENAI_RESPONSE_PROCESSING_FAILED",
        _GENERATION_FAILED_ERROR_MESSAGE,
        ApiError(
            status_code=500,
            code="AI_RESPONSE_FAILED",
            message="AI 답변 생성에 실패했습니다.",
            details=[ErrorDetail(field="assistant_message", reason="OPENAI_RESPONSE_PROCESSING_FAILED")],
        ),
    )
else:
    return await self._complete_message_pair(
        chat_session=chat_session,
        user_message=user_message,
        assistant_message=assistant_message,
        result=result,
    )

assert failure is not None
error_code, error_message, api_error = failure
await self._chat_repo.mark_failed(
    assistant_message,
    error_code=error_code,
    error_message=error_message,
    completed_at=datetime.now(UTC),
)
raise api_error
```

Implement the success helper explicitly so `send_message` stays below the Ruff complexity limit:

```python
async def _complete_message_pair(
    self,
    *,
    chat_session: ChatSession,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
    result: ChatReplyOutput,
) -> SendChatMessageData:
    completed_at = datetime.now(UTC)
    completed_message = await self._chat_repo.mark_completed(
        assistant_message,
        content=result.content,
        model_name=result.model_name,
        prompt_version=result.prompt_version,
        completed_at=completed_at,
    )
    await self._chat_repo.update_last_message_at(chat_session, last_message_at=completed_at)
    return SendChatMessageData(
        user_message_id=user_message.id,
        assistant_message_id=completed_message.id,
        session_id=chat_session.id,
        generation_status=str(completed_message.generation_status),
        content=completed_message.content,
        model_name=completed_message.model_name,
        prompt_version=completed_message.prompt_version,
        created_at=completed_message.created_at,
        completed_at=completed_message.completed_at,
    )
```

Do not log or retain caught exceptions.

- [ ] **Step 7: Remove fallback and wire the process-scoped client through a request-scoped adapter**

Remove `NotConfiguredChatEngine` from the package and `__all__`. Alias the two clients and add dependencies:

```python
from app.services.chat_ai import OpenAIResponsesClient as ChatOpenAIResponsesClient
from app.services.guide_ai import OpenAIResponsesClient as GuideOpenAIResponsesClient
from app.services.chat_generator_engine import ChatGeneratorEngine


def get_chat_engine(
    client: Annotated[AsyncOpenAI, Depends(get_openai_client)],
) -> ChatEngine:
    return ChatGeneratorEngine(
        provider=ChatOpenAIResponsesClient(client),
        model=config.OPENAI_MODEL,
        timeout_seconds=config.OPENAI_TIMEOUT_SECONDS,
    )
```

Update the existing guide dependency to use `GuideOpenAIResponsesClient`. Inject `engine: Annotated[ChatEngine, Depends(get_chat_engine)]` into `get_chat_service` and call `ChatService(prescription_repository, chat_repository, engine)`.

- [ ] **Step 8: Run focused tests, Chat AI regressions, lint, and typing**

```bash
uv run pytest app/tests/chat app/tests/repositories/test_chat_repository.py app/tests/chat_ai -q
uv run ruff check app/services/chat.py app/services/chat_generator_engine.py app/dependencies/services.py app/repositories
uv run mypy app
```

Expected: all focused tests and existing Chat AI tests pass; Ruff and Mypy report no errors.

- [ ] **Step 9: Commit the complete service and DI slice**

```bash
git add app/services/chat.py app/services/chat_ai/__init__.py app/repositories/chat_repository.py app/dependencies/services.py app/tests/chat app/tests/repositories/test_chat_repository.py
git commit -m "✨ feat: 챗봇 생성 결과 저장과 오류 변환 연결"
```

---

### Task 5: Chat HTTP Contract Tests

**Files:**
- Create: `app/tests/chat_apis/__init__.py`
- Create: `app/tests/chat_apis/test_chat_message_api.py`

**Interfaces:**
- Consumes: `app`, `fastapi_app`, `get_request_user`, and `get_chat_service`.
- Verifies: unchanged response bodies/status codes and `no-store` across success, handled errors, validation, authentication, and unexpected 500.

- [ ] **Step 1: Build dependency-overridden API fixtures using synthetic values**

Use `ASGITransport(app=app, raise_app_exceptions=False)`, clear overrides in `finally`, and define a service double returning exact DTOs:

```python
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response

from app.core.errors import ApiError, ErrorDetail
from app.dependencies.security import get_request_user
from app.dependencies.services import get_chat_service
from app.dtos.chat import ChatMessageData, ChatRole, ChatSessionData, SendChatMessageData
from app.main import app, fastapi_app

TEST_ORIGIN = "http://localhost:5173"
SESSION_ID = UUID("00000000-0000-0000-0000-000000000101")


class StubChatService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def send_message(self, *, user: object, session_id: UUID, request: object) -> SendChatMessageData:
        if self.error is not None:
            raise self.error
        now = datetime.now(UTC)
        return SendChatMessageData(
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
            session_id=session_id,
            generation_status="COMPLETED",
            content="합성 답변",
            model_name="gpt-4o-mini-2024-07-18",
            prompt_version="chat-prompt-v1",
            created_at=now,
            completed_at=now,
        )

    async def create_session(self, *, user: object, prescription_id: UUID) -> ChatSessionData:
        if self.error is not None:
            raise self.error
        return ChatSessionData(
            session_id=SESSION_ID,
            prescription_id=prescription_id,
            session_status="ACTIVE",
            created_at=datetime.now(UTC),
        )

    async def list_messages(self, *, user: object, session_id: UUID) -> list[ChatMessageData]:
        if self.error is not None:
            raise self.error
        return [
            ChatMessageData(
                message_id=uuid4(),
                role=ChatRole.ASSISTANT,
                content="합성 답변",
                generation_status="COMPLETED",
                created_at=datetime.now(UTC),
            )
        ]
```

- [ ] **Step 2: Add success and domain/provider error assertions**

For success, override user/service, POST the message, and assert the unchanged body and headers:

```python
async def test_send_message_returns_stored_ai_result_with_no_store(client: AsyncClient) -> None:
    response = await client.post(
        f"/api/v1/chat-sessions/{SESSION_ID}/messages",
        json={"content": "현재 질문"},
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 201
    assert response.json()["data"]["session_id"] == str(SESSION_ID)
    assert response.json()["data"]["content"] == "합성 답변"
    assert response.json()["data"]["model_name"] == "gpt-4o-mini-2024-07-18"
    assert response.json()["data"]["prompt_version"] == "chat-prompt-v1"
    assert "trace_id" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
```

Add success coverage for the other two endpoints with the same override/cleanup fixture used by the message test:

```python
async def test_create_session_success_keeps_body_and_no_store(client: AsyncClient) -> None:
    prescription_id = uuid4()
    response = await client.post(
        f"/api/v1/prescriptions/{prescription_id}/chat-sessions",
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 201
    assert response.json()["data"]["session_id"] == str(SESSION_ID)
    assert response.json()["data"]["prescription_id"] == str(prescription_id)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_list_messages_success_keeps_body_and_no_store(client: AsyncClient) -> None:
    response = await client.get(
        f"/api/v1/chat-sessions/{SESSION_ID}/messages",
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 200
    assert response.json()["data"]["session_id"] == str(SESSION_ID)
    assert response.json()["data"]["messages"][0]["content"] == "합성 답변"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
```

Define the shared client fixture with deterministic override cleanup:

```python
from collections.abc import AsyncIterator

import pytest_asyncio


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as test_client:
            yield test_client
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)
```

Parameterize exact `ApiError` instances for 500/503/504 and assert the common error body:

```python
@pytest.mark.parametrize(
    "error",
    [
        ApiError(
            status_code=500,
            code="AI_RESPONSE_FAILED",
            message="AI 답변 생성에 실패했습니다.",
            details=[ErrorDetail(field="assistant_message", reason="OPENAI_RESPONSE_PROCESSING_FAILED")],
        ),
        ApiError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            details=[ErrorDetail(field="openai_api", reason="OPENAI_API_ERROR")],
        ),
        ApiError(
            status_code=504,
            code="GATEWAY_TIMEOUT",
            message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
            details=[ErrorDetail(field="openai_api", reason="OPENAI_API_TIMEOUT")],
        ),
    ],
)
async def test_send_message_errors_keep_common_body_cors_and_no_store(error: ApiError) -> None:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService(error=error)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/chat-sessions/{SESSION_ID}/messages",
                json={"content": "현재 질문"},
                headers={"Origin": TEST_ORIGIN},
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)

    body = response.json()
    assert response.status_code == error.status_code
    assert body["code"] == error.code
    assert body["details"] == [detail.model_dump(mode="json") for detail in error.details]
    assert body["trace_id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
```

- [ ] **Step 3: Add validation, authentication-header, and unexpected-error cases**

Use `not-a-uuid` on both supported path shapes to assert `422` and `no-store`. Override `get_request_user` with this dependency to prove header preservation:

```python
async def reject_user() -> None:
    raise HTTPException(
        status_code=401,
        detail="인증이 필요합니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )
```

Then assert `www-authenticate: Bearer`, CORS, and `no-store`. Override the service with `RuntimeError("synthetic internal failure")`, send with `raise_app_exceptions=False`, and assert `500`, `INTERNAL_SERVER_ERROR`, CORS, and `no-store`.

Use this exact assertion helper for the validation and unexpected-error cases:

```python
def assert_private_chat_error(response: Response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.json()["trace_id"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions/not-a-uuid/messages",
        "/api/v1/prescriptions/not-a-uuid/chat-sessions",
    ],
)
async def test_invalid_chat_identifiers_keep_no_store(path: str) -> None:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(path, json={"content": "현재 질문"})
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)
    assert_private_chat_error(response, status_code=422, code="VALIDATION_FAILED")
```

- [ ] **Step 4: Run API tests and resolve only contract mismatches**

```bash
uv run pytest app/tests/chat_apis/test_chat_message_api.py tests/integration/test_cors_and_errors.py -q
```

Expected: all tests pass. Do not change Router response bodies or the global error schema to make tests pass.

- [ ] **Step 5: Commit HTTP contract coverage**

```bash
git add app/tests/chat_apis tests/integration/test_cors_and_errors.py
git commit -m "✅ test: 챗봇 Backend API 계약 검증"
```

Do not modify `tests/integration/test_cors_and_errors.py` unless a genuine PR #33 regression requires an assertion-only extension; if unchanged, omit it from `git add`.

---

### Task 6: Real MySQL Same-Session Concurrency Verification

**Files:**
- Create: `app/tests/chat_integration/__init__.py`
- Create: `app/tests/chat_integration/conftest.py`
- Create: `app/tests/chat_integration/test_chat_concurrency.py`

**Interfaces:**
- Consumes: real `ChatService`, `ChatRepository`, `PrescriptionRepository`, and `test_engine`.
- Verifies: committed fixture visibility across two connections, same-session serialization, sequence `1,2,3,4`, and different-session independence.

- [ ] **Step 1: Override savepoint isolation for this directory and create committed synthetic fixtures**

In the nearer `conftest.py`, override the parent autouse fixture by name so each concurrency test owns cleanup:

```python
from collections.abc import AsyncIterator

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def isolate_database() -> AsyncIterator[None]:
    yield
```

Provide a fixture that inserts one synthetic user/document/OCR job/prescription, ordered medications, and two chat sessions in a standalone `AsyncSession`, commits them, yields their IDs, then deletes messages, sessions, medications, prescription, OCR job, document, and user in foreign-key-safe order and commits cleanup.

Use a typed ID bundle and explicit commit/cleanup:

```python
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.users import Gender, User
from app.tests.conftest import test_engine


@dataclass(frozen=True)
class CommittedChatFixture:
    user: User
    document_id: UUID
    ocr_job_id: UUID
    prescription_id: UUID
    session_ids: tuple[UUID, UUID]


@pytest_asyncio.fixture
async def committed_chat_fixture() -> AsyncIterator[CommittedChatFixture]:
    async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as session:
        user = User(
            email="chat-concurrency@example.com",
            hashed_password="synthetic-hash",
            name="합성 사용자",
            gender=Gender.FEMALE,
            birthday=date(1990, 1, 1),
            phone_number="01000009999",
        )
        session.add(user)
        await session.flush()
        document = MedicalDocument(
            user_id=user.id,
            original_file_name="synthetic-prescription.jpg",
            object_key="synthetic/prescription.jpg",
            file_mime_type="image/jpeg",
            file_size_bytes=100,
        )
        session.add(document)
        await session.flush()
        ocr_job = OcrJob(document_id=document.id)
        session.add(ocr_job)
        await session.flush()
        prescription = Prescription(
            document_id=document.id,
            source_ocr_job_id=ocr_job.id,
            prescribed_date=date(2026, 1, 1),
            confirmed_at=datetime.now(UTC),
        )
        session.add(prescription)
        await session.flush()
        session.add(
            Medication(
                prescription_id=prescription.id,
                medication_name="합성약",
                display_order=1,
                duration_days=7,
            )
        )
        first = ChatSession(prescription_id=prescription.id)
        second = ChatSession(prescription_id=prescription.id)
        session.add_all([first, second])
        await session.commit()
        fixture = CommittedChatFixture(
            user=user,
            document_id=document.id,
            ocr_job_id=ocr_job.id,
            prescription_id=prescription.id,
            session_ids=(first.id, second.id),
        )

    yield fixture

    async with AsyncSession(bind=test_engine, expire_on_commit=False) as cleanup:
        await cleanup.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(fixture.session_ids)))
        await cleanup.execute(delete(ChatSession).where(ChatSession.id.in_(fixture.session_ids)))
        await cleanup.execute(delete(Medication).where(Medication.prescription_id == fixture.prescription_id))
        await cleanup.execute(delete(Prescription).where(Prescription.id == fixture.prescription_id))
        await cleanup.execute(delete(OcrJob).where(OcrJob.id == fixture.ocr_job_id))
        await cleanup.execute(delete(MedicalDocument).where(MedicalDocument.id == fixture.document_id))
        await cleanup.execute(delete(User).where(User.id == fixture.user.id))
        await cleanup.commit()
        assert await cleanup.scalar(
            select(ChatMessage.id).where(ChatMessage.session_id.in_(fixture.session_ids))
        ) is None
        assert await cleanup.scalar(select(ChatSession.id).where(ChatSession.id.in_(fixture.session_ids))) is None
        assert await cleanup.scalar(
            select(Medication.id).where(Medication.prescription_id == fixture.prescription_id)
        ) is None
        assert await cleanup.scalar(select(Prescription.id).where(Prescription.id == fixture.prescription_id)) is None
        assert await cleanup.scalar(select(OcrJob.id).where(OcrJob.id == fixture.ocr_job_id)) is None
        assert await cleanup.scalar(select(MedicalDocument.id).where(MedicalDocument.id == fixture.document_id)) is None
        assert await cleanup.scalar(select(User.id).where(User.id == fixture.user.id)) is None
```

- [ ] **Step 2: Implement a controlled engine and same-session serialization test**

```python
class ControlledEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.first_entered = asyncio.Event()
        self.second_entered = asyncio.Event()
        self.release_first = asyncio.Event()

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        self.calls += 1
        if self.calls == 1:
            self.first_entered.set()
            await self.release_first.wait()
        else:
            self.second_entered.set()
        return ChatReplyOutput(
            content=f"합성 답변 {self.calls}",
            model_name="synthetic-model",
            prompt_version="chat-prompt-v1",
        )
```

Create two independent `AsyncSession` instances. Start request one, wait for `first_entered`, start request two, and prove `second_entered` does not occur within 200 ms. Release request one, commit session one immediately after `send_message` returns, then allow request two to acquire the lock and commit. Query with a third session and assert roles/statuses and sequences exactly `[1, 2, 3, 4]` with two completed ASSISTANT messages.

Use this transaction wrapper so the row lock is released only after the service returns:

```python
async def send_and_commit(
    *,
    session: AsyncSession,
    engine: ChatEngine,
    user: User,
    session_id: UUID,
) -> None:
    service = ChatService(PrescriptionRepository(session), ChatRepository(session), engine)
    await service.send_message(
        user=user,
        session_id=session_id,
        request=SendChatMessageRequest(content="현재 질문"),
    )
    await session.commit()


first_task = asyncio.create_task(
    send_and_commit(session=first_db, engine=engine, user=user, session_id=chat_session_id)
)
await asyncio.wait_for(engine.first_entered.wait(), timeout=1)
second_task = asyncio.create_task(
    send_and_commit(session=second_db, engine=engine, user=user, session_id=chat_session_id)
)
with pytest.raises(TimeoutError):
    await asyncio.wait_for(engine.second_entered.wait(), timeout=0.2)
engine.release_first.set()
await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
```

- [ ] **Step 3: Add a different-session independence test**

Use the two committed chat-session IDs with a barrier engine that sets `both_entered` only after two calls enter. Start both requests and require `both_entered` within one second before releasing either generation. This fails if the locking statement also locks prescription/document rows. Commit both sessions and assert each chat session has sequences `[1, 2]`.

```python
class BarrierEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.both_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        self.calls += 1
        if self.calls == 2:
            self.both_entered.set()
        await self.release.wait()
        return ChatReplyOutput(
            content="합성 병렬 답변",
            model_name="synthetic-model",
            prompt_version="chat-prompt-v1",
        )


first_task = asyncio.create_task(
    send_and_commit(session=first_db, engine=engine, user=user, session_id=first_chat_session_id)
)
second_task = asyncio.create_task(
    send_and_commit(session=second_db, engine=engine, user=user, session_id=second_chat_session_id)
)
await asyncio.wait_for(engine.both_entered.wait(), timeout=1)
engine.release.set()
await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
```

- [ ] **Step 4: Add timing bounds and robust cleanup assertions**

Use short controlled waits rather than the production 20-second timeout. Assert the blocked second request completes only after release and remains within the test's two-second ceiling. After fixture cleanup, query every inserted ID and assert no row remains; never rely on the parent savepoint rollback for these committed rows.

Query final state and assert exact ordered values:

```python
messages = list(
    (
        await verification_db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == chat_session_id)
            .order_by(ChatMessage.message_seq)
        )
    )
    .scalars()
    .all()
)
assert [message.message_seq for message in messages] == [1, 2, 3, 4]
assert [message.role for message in messages] == [
    ChatRole.USER,
    ChatRole.ASSISTANT,
    ChatRole.USER,
    ChatRole.ASSISTANT,
]
assert [message.generation_status for message in messages] == [
    ChatGenerationStatus.NOT_APPLICABLE,
    ChatGenerationStatus.COMPLETED,
    ChatGenerationStatus.NOT_APPLICABLE,
    ChatGenerationStatus.COMPLETED,
]
```

Keep all `asyncio.wait_for` ceilings at one or two seconds so a broken lock fails quickly instead of waiting for MySQL's production lock timeout.

- [ ] **Step 5: Run the MySQL concurrency suite repeatedly**

```bash
uv run pytest app/tests/chat_integration/test_chat_concurrency.py -q
uv run pytest app/tests/chat_integration/test_chat_concurrency.py -q
uv run pytest app/tests/chat_integration/test_chat_concurrency.py -q
```

Expected: all three consecutive runs pass without sequence conflicts, lock timeout, cross-test leakage, or flaky ordering assumptions.

- [ ] **Step 6: Commit the real locking proof**

```bash
git add app/tests/chat_integration
git commit -m "✅ test: 챗봇 세션 동시 전송 직렬화 검증"
```

---

### Task 7: Backend–AI Contract Gate and CI Scope

**Files:**
- Create: `tests/contract/test_chat_ai_backend_contract.py`
- Modify: `.github/workflows/checks.yml:99-102`
- Modify: `scripts/ci/run_test.sh:37-46`

**Interfaces:**
- Consumes: the real `ChatGeneratorEngine` and `ChatGenerator`, with a capturing stub provider.
- Produces: a deterministic cross-module contract test collected by both GitHub Actions and the local test script.

- [ ] **Step 1: Write one complete Backend-to-provider contract test**

Build `ChatReplyInput` with two medications, including `Decimal("0.500")`, `duration_days=7`, and an incomplete dose/unit pair. Pass it through the real adapter into a stub provider and assert:

```python
assert payload == {
    "question": "합성 질문",
    "medications": [
        {
            "medication_name": "합성약 A",
            "dose_value": "0.500",
            "dose_unit": "mg",
            "frequency_per_day": 2,
            "timing_text": "아침 식후",
            "duration_days": 7,
        },
        {
            "medication_name": "합성약 B",
            "duration_days": 3,
        },
    ],
}
assert result.content == "합성 계약 답변"
assert result.model_name == "synthetic-model-v1"
assert result.prompt_version == "chat-prompt-v1"
assert "prescription_id" not in captured_input_json
assert "session_id" not in captured_input_json
assert "message_id" not in captured_input_json
```

- [ ] **Step 2: Run the contract test before CI changes**

```bash
uv run pytest tests/contract/test_chat_ai_backend_contract.py -q
```

Expected: the test passes against Tasks 1–4; if it fails, fix the adapter contract rather than weakening assertions.

- [ ] **Step 3: Expand both test entrypoints to the same scope**

Change both commands from:

```bash
uv run coverage run -m pytest app
```

to:

```bash
uv run coverage run -m pytest app tests/contract
```

Do not add all `tests/integration` or `tests/e2e` to this PR's gate.

- [ ] **Step 4: Verify collection includes the new contract file**

```bash
uv run pytest app tests/contract --collect-only -q
uv run pytest tests/contract/test_chat_ai_backend_contract.py -q
```

Expected: collection output includes `test_chat_ai_backend_contract.py`; the focused contract test passes.

- [ ] **Step 5: Commit contract and CI scope together**

```bash
git add tests/contract/test_chat_ai_backend_contract.py .github/workflows/checks.yml scripts/ci/run_test.sh
git commit -m "💡 chore: Backend AI 계약 테스트를 CI에 포함"
```

---

### Task 8: API, Deployment, and Design Documentation plus Final Verification

**Files:**
- Modify: `docs/api.md`
- Modify: `docs/deployment.md`
- Modify: `docs/designs/ceohwj/medication-chat-ai-generation-design.md`
- Modify: `docs/designs/ceohwj/medication-chat-ai-backend-integration-design.md`
- Add: `docs/superpowers/plans/2026-08-20-medication-chat-ai-backend-integration.md`

**Interfaces:**
- Documents: the exact synchronous endpoints, persistence/error/cache semantics, timeout prerequisites, test evidence, and linkage between PR #35 and Issue #38.

- [ ] **Step 1: Add the three chat endpoints and one-cycle behavior to `docs/api.md`**

Document this exact table and surrounding rules:

```markdown
| Method | Path | Success | Cache policy |
| --- | --- | ---: | --- |
| POST | `/api/v1/prescriptions/{prescription_id}/chat-sessions` | 201 | Router responses use `no-store` |
| GET | `/api/v1/chat-sessions/{session_id}/messages` | 200 | Router responses use `no-store` |
| POST | `/api/v1/chat-sessions/{session_id}/messages` | 201 | Router responses use `no-store` |
```

State that message POST is synchronous; only the current question and ordered confirmed medications are sent; success returns the stored ASSISTANT content/model/prompt; failure preserves USER plus FAILED ASSISTANT; same-session sends serialize; normal worst-case second-request latency is `2 × OPENAI_TIMEOUT_SECONDS` plus application margin; 500/503/504 use the common error schema. State that CORS preflight is handled outside Router cache policy.

- [ ] **Step 2: Add deployment prerequisites and recording fields to `docs/deployment.md`**

For the current default `OPENAI_TIMEOUT_SECONDS=20`, require Nginx `proxy_read_timeout >= 45s` and MySQL `innodb_lock_wait_timeout > 20s`. Add a deployment checklist with fields for environment, observed values, verification date, and verifier. If a target environment is below either bound, require Infrastructure adjustment before deploy; do not shorten application timeout to hide the mismatch.

- [ ] **Step 3: Link the designs and correct the stale timeout statement**

In `medication-chat-ai-generation-design.md`, change the `OPENAI_TIMEOUT_SECONDS` default from 30 seconds to the actual Backend default 20 seconds and link the approved Backend integration design under `## 후속 단계` or `## 참고 자료`. Keep prompt/provider behavior unchanged. In the integration design, record the final implementation/test evidence without changing approved contracts.

- [ ] **Step 4: Run the complete required verification with fresh evidence**

Start/confirm the repository's MySQL test container, then run:

```bash
uv run pytest app/tests/chat_ai app/tests/chat app/tests/chat_apis app/tests/chat_integration app/tests/repositories/test_chat_repository.py -q
uv run pytest tests/contract/test_chat_ai_backend_contract.py -q
uv run pytest tests/integration/test_cors_and_errors.py -q
uv run ruff check .
uv run ruff format . --check
uv run mypy app ai_worker
bash scripts/ci/run_test.sh
git diff --check
git status --short
```

Expected: all test commands report zero failures, Ruff and Mypy report zero errors, the CI script includes `app tests/contract`, `git diff --check` emits no errors, and status contains only Issue #38 files. Record any skipped live OpenAI smoke test explicitly as skipped because it requires a secret and is not a PR gate.

- [ ] **Step 5: Perform privacy and scope review on the complete diff**

```bash
git diff -- . ':!uv.lock'
rg -n 'sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY' app tests docs .github scripts
```

Expected: the diff contains no unrelated refactor, migration, model/schema/body/dependency change, real patient data, API key, provider body logging, or raw exception-chain retention. The secret-pattern search returns no credential matches; documented placeholder names such as `OPENAI_API_KEY` are allowed.

- [ ] **Step 6: Commit documentation only after verification evidence is recorded**

```bash
git add docs/api.md docs/deployment.md docs/designs/ceohwj/medication-chat-ai-generation-design.md docs/designs/ceohwj/medication-chat-ai-backend-integration-design.md docs/superpowers/plans/2026-08-20-medication-chat-ai-backend-integration.md
git commit -m "📝 docs: 챗봇 Backend 연동 계약과 배포 조건 문서화"
```

- [ ] **Step 7: Prepare the PR handoff without pushing or creating the PR unless explicitly authorized**

Report commit list, changed-file scope, exact verification commands/results, optional smoke-test skip, deployment timeout observations, CODEOWNERS `@phina-io`, `@hazelnutflavoured`, and the fact that PR #33 commit `4e7df35` is included. Recommended PR title: `✨ feat: 복약 챗봇 AI Backend 연동`; open it as Draft when authorized.

## Spec Coverage Matrix

| Approved requirement | Implemented in | Proved by |
| --- | --- | --- |
| Thin `ChatEngine` adapter around existing AI Core | Task 1 | adapter unit tests and Chat AI regression suite |
| `Decimal` and `duration_days` preservation | Tasks 1, 4, 7 | public-contract, service, and Backend–AI JSON assertions |
| No Backend identifiers or chat history in provider payload | Tasks 1, 7 | exact captured JSON and forbidden-key assertions |
| No partial prescription for 31+ medications | Task 1 | provider-not-called validation test |
| Required real engine injection; no runtime fallback | Task 4 | constructor and dependency identity tests |
| Active owned session gate before AI call | Tasks 3, 4 | ownership SQL and service no-call assertions |
| Same-session sequence safety | Tasks 3, 6 | compiled SQL shape and three repeated real-MySQL concurrency runs |
| Different-session independence | Task 6 | barrier-engine two-session test |
| USER/ASSISTANT success persistence and metadata | Task 4 | service/repository success assertions |
| FAILED ASSISTANT plus safe fixed metadata survives rollback | Task 4 | real repository reload-after-rollback test |
| 500/503/504 mapping | Tasks 1, 4, 5 | adapter, service, and API parameterized tests |
| No raw exception object chain | Tasks 1, 4 | `__cause__` and `__context__` assertions |
| `no-store` on all Router chat successes/errors including unexpected 500 | Tasks 2, 5 | pure ASGI and full-app ASGI transport tests |
| CORS and existing headers preserved | Tasks 2, 5 | PR #33 regression, auth-header, and unexpected-500 tests |
| `tests/contract/` runs locally and in CI | Task 7 | collection output, focused test, and script diff |
| No new dependency, environment variable, migration, model, or body contract | Tasks 7, 8 | full diff and dependency/scope audit |
| Deployment timeout prerequisites documented and checked | Task 8 | deployment record and Nginx/MySQL observed values |
| No real patient data, key, or provider/medical body logs | Tasks 1, 4, 8 | synthetic fixtures, secret scan, and complete diff review |
