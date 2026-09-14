"""Regenerate or verify the Issue #368 protected runner evidence hash."""

import argparse
from pathlib import Path

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.protected_retrieval_infrastructure_evidence import (
    EVIDENCE_JSON_PATH,
    EVIDENCE_MARKDOWN_PATH,
    build_protected_retrieval_infrastructure_evidence,
    render_protected_retrieval_infrastructure_evidence,
    write_protected_retrieval_infrastructure_evidence,
)

RECOVERY_GUIDANCE = f"""복구 순서 (저장소 루트에서 실행):
1. 증빙이 참조하는 구현 파일이 바뀌었는지 확인합니다. evidence_sha256은 {EVIDENCE_JSON_PATH}의
   implementation_files[].raw_sha256을 포함하므로, 공용 파일 수정만으로도 값이 바뀝니다.
2. 상태값(effective_enforcement_status 등)을 바꿔야 한다면 먼저 근거 Decision과 담당 리뷰어를
   확인합니다. 해시 재생성은 승인·공개 근거가 아닙니다.
3. 재생성합니다:
   uv run python scripts/verify_protected_runner_evidence.py --write
4. 변경된 {EVIDENCE_JSON_PATH}와 {EVIDENCE_MARKDOWN_PATH}를 diff로 검토한 뒤 커밋합니다.
"""


def verify_protected_runner_evidence(project_root: Path) -> str:
    evidence = build_protected_retrieval_infrastructure_evidence(project_root)
    mismatches = []
    stored_json = (project_root / EVIDENCE_JSON_PATH).read_bytes()
    if stored_json != canonical_json_bytes(evidence):
        mismatches.append(f"{EVIDENCE_JSON_PATH}: committed canonical JSON differs from the builder output")
    stored_markdown = (project_root / EVIDENCE_MARKDOWN_PATH).read_text(encoding="utf-8")
    if stored_markdown != render_protected_retrieval_infrastructure_evidence(evidence):
        mismatches.append(f"{EVIDENCE_MARKDOWN_PATH}: committed Markdown differs from the rendered evidence")
    if mismatches:
        raise ValueError("Protected runner 증빙 검증 실패:\n" + "\n".join(mismatches) + "\n\n" + RECOVERY_GUIDANCE)
    return str(evidence["evidence_sha256"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate the evidence JSON and Markdown instead of verifying them.",
    )
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    if args.write:
        write_protected_retrieval_infrastructure_evidence(project_root)
    print(verify_protected_runner_evidence(project_root))


if __name__ == "__main__":
    main()
