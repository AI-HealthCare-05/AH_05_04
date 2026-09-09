# 기여 가이드

이 문서는 저장소의 Git 협업 기준입니다. 다른 문서와 충돌하면 이 문서와 GitHub Ruleset을 우선하고, 변경이 필요하면 Issue와 Pull Request로 함께 갱신합니다.

## 브랜치

- `main`: 발표·배포 가능한 안정 버전
- `develop`: 팀 통합 브랜치
- `feat/<issue>-<name>`: 기능 개발
- `fix/<issue>-<name>`: 버그 수정
- `docs/<issue>-<name>`: 문서 변경
- `chore/<issue>-<name>`: 설정과 유지보수

```bash
git switch develop
git pull origin develop
git switch -c feature/12-prescription-upload
```

초기 세팅 완료 후 `main`과 `develop`에는 직접 push하지 않습니다. 작업 브랜치에서 `develop`으로 Pull Request를 생성하고, 배포할 때만 `develop`에서 `main`으로 Pull Request를 생성합니다.

## 작업 흐름

1. GitHub Issue를 만들고 담당자를 지정합니다.
2. Issue 번호를 포함한 작업 브랜치를 만듭니다.
3. 코드와 관련 테스트·문서를 함께 수정합니다.
4. Ruff, Mypy와 관련 테스트를 실행합니다.
5. 구현 담당자와 별도의 담당 리뷰어를 Issue와 PR에 지정하고 리뷰를 요청합니다.
6. 지정된 담당 리뷰어의 승인과 blocking comment 해소를 확인합니다.
7. Squash merge 후 작업 브랜치를 삭제합니다.

## 구현 및 설계 원칙

현재 요구사항을 충족하는 가장 단순하고 일관된 구조를 우선합니다. 예상되는 미래 요구만을 근거로 추상화, 계층, 상태, 비동기 처리, 캐시, 의존성 또는 인프라를 미리 추가하지 않습니다. 기존 구조와 유틸리티로 해결할 수 있다면 이를 재사용하고, 필요한 범위만 변경합니다.

### 구체적 적용 기준

- 요구사항이나 승인된 계약에 없는 DB Trigger, Stored Procedure, DB Scheduler 또는 RLS Policy를 임의로 도입하지 않습니다.
- DB 내부의 암묵적 동작보다 Application Service에서 명시적으로 추적할 수 있는 로직을 우선합니다. 단, DB 무결성·보안에 필요한 constraint, index, transaction과 승인된 DB 정책은 생략하지 않습니다.
- CQRS, Event Bus, Domain Event, Factory, Strategy, Registry 또는 Plugin 구조는 현재 요구사항에서 필요성이 확인될 때만 도입합니다.
- 실제 구현체가 하나뿐이라면 미래 확장만을 이유로 interface, abstract class 또는 provider abstraction을 추가하지 않습니다. 외부 API 격리, 테스트 대역, 보안 경계처럼 현재 필요한 역할이 있다면 그 근거를 기록합니다.
- 측정된 성능 문제가 없다면 cache를 추가하지 않습니다.
- retry를 추가하기 전에 작업의 멱등성과 중복 실행 영향을 확인하고, 안전한 재실행 조건을 테스트합니다.
- 새로운 status, enum, DB column, table, queue 또는 stream을 추가하기 전에 기존 모델로 표현할 수 없는 이유를 확인합니다.
- 같은 의미의 DTO, model 또는 schema를 계층마다 기계적으로 복제하지 않습니다. 외부 계약과 내부 모델의 경계 분리가 필요한 경우에는 변환 책임과 정본을 명확히 합니다.
- 현재 dependency나 표준 라이브러리로 해결할 수 있다면 새로운 dependency를 추가하지 않습니다.
- Docker, CI/CD, Redis, DB extension 또는 production configuration 변경은 현재 Issue 범위에 직접 포함되거나 선행 조건으로 합의된 경우에만 수행합니다.
- 미래 요구사항을 추측해 코드를 미리 구현하지 않고, 현재 Issue의 작업 범위와 완료 조건만 구현합니다.
- Backend의 비즈니스 흐름은 적용 가능한 경우 `Router → Service → Repository/External Client` 형태로 명시적으로 추적할 수 있게 유지합니다.
- AI Worker의 비즈니스 흐름은 적용 가능한 경우 `Task/Consumer → Service → Repository/External Client` 형태로 명시적으로 추적할 수 있게 유지합니다. 다른 영역에서는 기존 책임 경계와 프레임워크 관례를 따릅니다.

### Backend 변경 리뷰 기준

- 함수명, 변수명, 파일명, API 필드명, DB 컬럼명과 테스트명에서 같은 도메인 개념에 같은 이름을 사용합니다. 이름을 변경할 때는 영향 범위를 검색하고 관련 Router, Service, Repository, DTO, schema, fixture, test와 문서를 함께 정렬합니다. 공유 계약을 변경하는 이름 수정은 별도의 계약 변경 절차를 따릅니다.
- 사용자, 프로필, 문서, 처방, 가이드, 챗과 RAG 리소스의 조회·수정·삭제 경로에서 소유권과 접근 권한을 검증하고, 다른 사용자의 리소스에 접근할 수 있는 경로가 없는지 확인합니다.
- 인증·인가, 입력 검증, 에러 응답과 로그에서 API Key, token, cookie, 환자 정보, 원본 처방전과 Provider 원문 응답이 노출되지 않는지 확인합니다. 세부 기준은 `SECURITY.md`와 `docs/privacy-safety.md`를 따릅니다.
- 중복 로직은 기존 패턴과 유틸리티를 우선해 정리하되, 단일 사용처를 위한 새 추상화는 만들지 않습니다. 불필요한 주석, 죽은 코드, 디버그 로그, 임시 코드와 사용하지 않는 import를 남기지 않습니다.

새 구조가 필요하다면 구현과 리뷰 전에 Issue 또는 Pull Request에 다음 내용을 기록합니다.

1. 현재 단순한 구조로 해결할 수 없는 문제
2. 제안하는 구조
3. 추가되는 유지보수 비용
4. 검토한 대안
5. 지금 도입해야 하는 이유

다음은 복잡성 도입의 근거가 될 수 있습니다.

- 둘 이상의 실제 구현이나 소비자가 이미 존재합니다.
- 승인된 공유 계약이나 확정된 후속 작업이 해당 경계를 요구합니다.
- 보안, 개인정보, 의료 안전 또는 소유권 분리를 위해 독립된 경계가 필요합니다.
- 트랜잭션, 멱등성, 장애 복구 또는 비동기 처리가 실제 요구사항입니다.
- 같은 조건에서 측정한 성능 결과로 병목이 확인되었습니다.

이 원칙은 추상화나 책임 분리 자체를 금지하지 않습니다. API·DTO·DB·메시지 스키마 같은 공유 계약, 보안·Privacy·의료 안전 통제와 책임 경계는 단순화를 이유로 약화하거나 생략하지 않습니다. 복잡성이 필요하다는 설명도 기존 계약 변경, 담당자 조율, 문서화와 검증 요건을 대신하지 않습니다.

## 리뷰와 머지

- CODEOWNERS는 사용하지 않으며 변경 경로나 과거 작성자만으로 리뷰어를 자동 확정하지 않습니다.
- 담당 리뷰어는 실제 변경 영역을 검토할 수 있는 팀원으로 지정하며 PR 작성자의 self-approval은 승인으로 인정하지 않습니다.
- 여러 영역을 함께 변경하면 Backend·Frontend·Worker/OCR·AI/RAG·Security·Privacy 등 영향 영역별 리뷰어를 기록합니다.
- 공유 계약 변경은 Decision, 계약 문서, OpenAPI/DTO, migration, 구현과 계약·통합 테스트를 같은 변경 흐름에서 정렬합니다.
- 의료 안전·Privacy·외부 Source 공개 승인은 코드 리뷰와 별도 게이트이며 필요한 증빙이 없으면 `PUBLIC_TRACK_C` 또는 `PUBLIC_TRACK_F`를 해제하지 않습니다.

## 커밋 메시지

`.github/commit_template.txt`의 형식을 따릅니다.

```text
✨ feat: 처방전 업로드 API 추가
🐛 fix: OCR 실패 상태 처리 수정
♻️ refactor: 검색 서비스 경계 분리
📝 docs: API 계약 문서 갱신
✅ test: 의료 안전 회귀 사례 추가
💡 chore: 개발 환경 설정 정리
```

## 완료 전 검사

```bash
uv run ruff check .
uv run ruff format . --check
uv run mypy backend/app ai_worker
bash scripts/ci/run_test.sh
```

DB 테스트는 PostgreSQL 컨테이너가 필요합니다. 의료·AI 변경은 관련 `evals/` 회귀 기준도 통과해야 합니다.

## 데이터와 보안

- `.env`, API Key, 토큰, 인증서와 비밀번호를 커밋하지 않습니다.
- 실제 환자 정보, 처방전, 진료기록과 재식별 가능한 데이터는 저장소에 두지 않습니다.
- 샘플은 비식별 합성 데이터만 사용합니다.
- OCR 미검토 값은 확정 처방으로 사용하지 않습니다.
- AI가 약 중단·용량·복용 시간을 임의로 변경하도록 구현하지 않습니다.
