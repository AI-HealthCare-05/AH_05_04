# Frontend

사용자 화면과 UX를 구현하는 프론트엔드 영역입니다.

## 기술 스택

- React 19
- TypeScript
- Vite 8
- pnpm
- Oxlint

## 개발 환경

현재 초기 개발환경 기준:

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

## 개발 서버 실행

```bash
pnpm dev
```

기본 개발 서버:

`http://localhost:5173`

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
├── tests/
├── index.html
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
└── vite.config.ts
```

현재는 React + Vite + pnpm 기반의 기본 Frontend 개발환경이 구성되어 있습니다.

UI 라이브러리, 스타일링 시스템, 상세 아키텍처 및 실제 화면 구현은 후속 작업에서 적용합니다.

## 보안

다음 정보는 저장소에 커밋하지 않습니다.

- API Key
- Access Token
- 비밀번호
- 실제 환자 개인정보
- 실제 처방전 및 의료정보

환경변수가 필요한 경우 팀에서 제공하는 예시 환경변수 파일을 기준으로 로컬 환경에 구성합니다.
