# 프로젝트 문서

프로젝트의 설계와 팀 공유 문서는 이 디렉터리에서 관리합니다.

## 문서 구성

필요한 시점에 다음 문서를 추가합니다.

- `architecture.md`: 시스템 구성과 서비스 간 데이터 흐름
- `api.md`: API 명세와 요청·응답 예시
- `data-schema.md`: ERD, 테이블 및 주요 데이터 구조
- `ai-pipeline.md`: 현재 동기 AI 처리, schema-only 골격과 Post-MVP Worker·RAG·평가 흐름
- `deployment.md`: 개발·운영 환경과 배포 절차
- `privacy-safety.md`: 개인정보와 의료 안전 기준
- `testing.md`: 현재 자동 검증 범위와 Post-MVP E2E·AI 평가 전략
- `contracts/`: 모듈 간 공통 데이터 계약
  - `contracts/prescription-confirmation-mvp.md`: MVP 처방 확정 필수값·DB 경계값·Post-MVP `job_id` 검증 경계
- `adr/`: 주요 아키텍처 결정 기록
- `designs/`: 기능별 상세 설계와 구현 계획

외부 문서 도구를 사용한다면 문서의 원본 링크와 최종 갱신일을 이 파일에 기록합니다.

## 작성 원칙

- 실제 환자 정보나 원본 진료기록을 포함하지 않습니다.
- API Key, 비밀번호, 토큰 등 인증정보를 포함하지 않습니다.
- 코드 또는 API가 변경되면 관련 문서도 같은 Pull Request에서 갱신합니다.
- 현재 구현, schema-only 골격과 Post-MVP 목표를 같은 상태로 표현하지 않습니다.
