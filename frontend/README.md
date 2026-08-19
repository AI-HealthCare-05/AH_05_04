# Frontend

사용자 화면과 UX를 구현하는 프론트엔드 영역입니다.

## 기술 스택

- React 19
- TypeScript
- Vite 8
- React Router
- pnpm
- Oxlint

## 개발 환경

현재 개발환경 기준:

- Node.js: 24.x
- pnpm: 11.x

버전 확인:

```bash
node --version
pnpm --version
```

## 설치

프로젝트 저장소를 clone한 뒤 Frontend 디렉터리로 이동합니다.

```bash
cd frontend
pnpm install
```

## 환경변수

Frontend 로컬 환경변수는 예시 파일을 기준으로 생성합니다.

```bash
cp .env.example .env.local
```

기본 예시:

```env
VITE_API_BASE_URL=http://localhost:8000
```

`.env.local`은 Git에 커밋하지 않습니다.

실제 API Key, Access Token, 환자 개인정보를 환경변수 예시 파일에 작성하지 않습니다.

## 개발 서버 실행

```bash
pnpm dev
```

기본 개발 서버:

```text
http://localhost:5173/
```

## Production Build

```bash
pnpm build
```

## Lint

```bash
pnpm lint
```

## Preview

Production build 결과를 로컬에서 확인할 경우:

```bash
pnpm preview
```

## 현재 프로젝트 구조

```text
frontend/
├── public/
├── src/
│   ├── api/          # Backend API 통신
│   ├── components/   # 공통 UI 컴포넌트
│   ├── hooks/        # 공통 React hooks
│   ├── pages/        # 화면 단위 컴포넌트
│   ├── routes/       # React Router 구성
│   ├── types/        # 공통 TypeScript 타입
│   ├── utils/        # 공통 유틸리티
│   ├── App.tsx
│   ├── index.css
│   └── main.tsx
├── tests/
├── .env.example
├── index.html
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
└── vite.config.ts
```

## Routing

React Router를 사용합니다.

현재 기본 경로:

```text
/
```

후속 MVP 기능 개발에서 회원가입·로그인, 처방전 업로드, OCR 확인·수정, 처방 확정, 복약 가이드, 챗봇 화면을 순차적으로 추가합니다.

## API Client

공통 Backend API 요청은 다음 파일을 사용합니다.

```text
src/api/client.ts
```

Backend API Base URL은 다음 환경변수로 설정합니다.

```text
VITE_API_BASE_URL
```

인증이 필요한 API 요청은 Access Token을 전달하여 Bearer Token을 설정할 수 있습니다.

Backend 공통 오류 응답은 다음 필드를 기준으로 처리합니다.

```text
code
message
details
trace_id
```

Frontend는 오류 처리 시 `message` 문자열이 아니라 `code`를 기준으로 분기합니다.

## 공통 상태 처리

후속 화면에서 사용할 기본 상태 컴포넌트를 제공합니다.

- `LoadingState`
- `ErrorState`

각 기능 화면에서 Loading 및 Error 상태를 공통 방식으로 처리하기 위한 기반입니다.

## 모바일 기준

MVP 핵심 사용자 흐름은 390px 모바일 화면을 기준으로 우선 구현합니다.

현재 기본 `main` 콘텐츠의 최대 너비는 390px로 설정되어 있습니다.

## 보안

다음 정보는 저장소에 커밋하지 않습니다.

- API Key
- Access Token
- 비밀번호
- 실제 환자 개인정보
- 실제 처방전 및 의료정보
- `.env.local`

테스트 및 개발에는 실제 의료정보 대신 비식별 합성 데이터를 사용합니다.
