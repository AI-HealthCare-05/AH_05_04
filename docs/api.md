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

## 변경 이력

API 계약이 변경되면 관련 Issue와 Pull Request를 기록합니다.
