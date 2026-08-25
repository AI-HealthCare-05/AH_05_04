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
5. PR 템플릿을 작성하고 최소 한 명에게 리뷰를 요청합니다.
6. Squash merge 후 작업 브랜치를 삭제합니다.

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
uv run mypy app ai_worker
bash scripts/ci/run_test.sh
```

DB 테스트는 PostgreSQL 컨테이너가 필요합니다. 의료·AI 변경은 관련 `evals/` 회귀 기준도 통과해야 합니다.

## 데이터와 보안

- `.env`, API Key, 토큰, 인증서와 비밀번호를 커밋하지 않습니다.
- 실제 환자 정보, 처방전, 진료기록과 재식별 가능한 데이터는 저장소에 두지 않습니다.
- 샘플은 비식별 합성 데이터만 사용합니다.
- OCR 미검토 값은 확정 처방으로 사용하지 않습니다.
- AI가 약 중단·용량·복용 시간을 임의로 변경하도록 구현하지 않습니다.
