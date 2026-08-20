# API 명세

## 공통 규칙

- Base path: `/api/v1`
- 요청·응답 형식과 오류 코드는 팀 공통 규칙을 따릅니다.
- 인증이 필요한 API는 권한 조건을 함께 기록합니다.

## 공통 오류 응답 형식

모든 API의 오류 응답은 아래 형식(`app/core/errors.py`)을 따릅니다.

```json
{
  "code": "string",
  "message": "string",
  "details": [
    { "field": "string", "reason": "string", "rejected_value": null }
  ],
  "trace_id": "string"
}
```

- `trace_id`는 요청별 미들웨어(`app/main.py`)가 생성해 `request.state.trace_id`에 저장하고, 모든 에러 핸들러가 이 값을 재사용합니다(핸들러가 자체적으로 새 값을 만들지 않음). 성공 응답 body에는 아직 포함하지 않으며, 필요 시 로그·감사로그와 연결할 수 있도록 모든 요청에서 `request.state`에 존재합니다.
- 기존 `HTTPException` 기반 코드(`{"detail": "..."}`)도 전역 핸들러가 위 형식으로 자동 변환합니다. 이때 `code`는 `HTTP_ERROR`로 고정되고 `message`에 원래 `detail` 값이 들어갑니다.
- 예상치 못한 예외는 `code: INTERNAL_SERVER_ERROR`, 500으로 변환되며 내부 오류 내용은 노출하지 않습니다.

## CORS

- Backend API 서버는 로컬 개발 기준 `http://localhost:8000`에서 실행합니다.
- Frontend 개발 서버 origin은 `http://localhost:5173`으로 사용합니다.
- Frontend는 `VITE_API_BASE_URL=http://localhost:8000`으로 Backend API를 호출합니다.
- Backend는 `CORS_ALLOWED_ORIGINS=http://localhost:5173`을 허용 origin으로 사용합니다.
- `CORSMiddleware`가 `CORS_ALLOWED_ORIGINS` 환경변수(콤마로 구분된 origin 목록)를 기준으로 허용 origin을 관리합니다.

## API 목록

기능별 API가 확정되면 경로, 메서드, 요청, 응답 및 오류 사례를 추가합니다.

### 복약 챗봇 세션과 메시지

| Method | Path | Success | Cache policy |
| --- | --- | ---: | --- |
| POST | `/api/v1/prescriptions/{prescription_id}/chat-sessions` | 201 | Router responses use `no-store` |
| GET | `/api/v1/chat-sessions/{session_id}/messages` | 200 | Router responses use `no-store` |
| POST | `/api/v1/chat-sessions/{session_id}/messages` | 201 | Router responses use `no-store` |

메시지 전송은 동기 one-cycle 요청이다. Backend는 현재 질문과 세션에 연결된 확정 약물을 표시 순서대로만 AI에 전달하며, 이전 대화·사용자·세션·처방·메시지 식별자는 전달하지 않는다. 성공하면 저장된 ASSISTANT 메시지의 `content`, 실제 `model_name`, `prompt_version`을 `201 Created` 응답으로 반환한다.

AI 생성에 실패하면 USER 메시지와 안전한 고정 오류 metadata를 가진 `FAILED` ASSISTANT 메시지를 보존한다. 이후 메시지 조회로 두 메시지를 함께 확인할 수 있다. 같은 세션의 전송은 순서와 `message_seq` 보호를 위해 직렬화되며, 서로 다른 세션은 독립적으로 처리된다. 같은 세션에서 정상적인 두 번째 요청의 최악 지연은 `2 × OPENAI_TIMEOUT_SECONDS`에 애플리케이션 처리 여유를 더한 값이다.

생성 timeout·일시적 서비스 불가·그 밖의 안전한 생성 실패는 각각 `504`·`503`·`500`으로 공통 오류 응답 형식을 사용한다. 세 endpoint에서 Router가 생성하는 성공과 오류 응답에는 `Cache-Control: no-store`가 적용된다. 최외곽 CORS middleware가 Router 밖에서 직접 처리하는 preflight 응답은 이 cache 정책의 대상이 아니다.

## 변경 이력

API 계약이 변경되면 관련 Issue와 Pull Request를 기록합니다.
