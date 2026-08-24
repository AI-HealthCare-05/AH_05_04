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
│   ├── design-system/# 디자인 토큰·프로토타입
│   ├── pages/        # 화면 단위 컴포넌트
│   ├── routes/       # React Router 구성
│   ├── types/        # 공통 TypeScript 타입
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

현재 경로와 구현 수준:

| 경로 | 상태 |
| --- | --- |
| `/` | 기본 Home 화면 |
| `/signup` | 회원가입 API 연결 |
| `/login` | 로그인 API 연결 |
| `/prescriptions/upload` | 처방전 업로드, 동기 OCR 실행과 결과 요약 연결 |
| `/design-prototype` | 실제 Backend 상태와 분리된 UX 프로토타입 |

OCR 필드 확인·수정, 처방 확정, 복약 가이드와 챗봇 화면은 아직 실제 API 사용자 여정으로 연결되지 않았습니다. 디자인 프로토타입에 화면이 있더라도 구현 완료로 간주하지 않습니다.

## API Client

공통 Backend API 요청은 다음 파일을 사용합니다.

```text
src/api/client.ts
```

Backend API Base URL은 다음 환경변수로 설정합니다.

```text
VITE_API_BASE_URL
```

로컬 예시값은 `frontend/.env.example`의 `http://localhost:8000`입니다. 환경변수가 없으면 API client가 오류를 발생시키므로 앞의 설정 절차처럼 `.env.local`에 명시해야 합니다. 배포 환경에서는 실제 Nginx/API 주소로 설정합니다.

인증이 필요한 API 요청은 Access Token을 전달하여 Bearer Token을 설정할 수 있습니다.

Backend 공통 오류 응답은 다음 필드를 기준으로 처리합니다.

```text
code
message
details
trace_id
```

공통 client는 위 오류 형식을 파싱하지만 화면별 처리는 아직 통일되지 않았습니다. 업로드 화면은 현재 `message`를 표시하고 로그인·회원가입은 일반 오류로 처리하므로, `code` 기반 분기는 후속 구현 대상입니다.

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
