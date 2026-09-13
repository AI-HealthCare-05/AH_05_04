"""Read-only diagnostics for the three linked Source contract hashes."""

import hashlib
import re
from pathlib import Path

from scripts.verify_rag_01_receipt import calculate_receipt_hash, load_receipt, verify_receipt_hash

RECEIPT_PATH = Path("tests/fixtures/rag/source_contract_receipt.json")
TARGET_PATH = Path("docs/contracts/targets/post-mvp-1/rag-source-ingestion-v1.md")
TRACEABILITY_PATH = Path("docs/testing/post-mvp-1-contract-traceability.md")

RECOVERY_GUIDANCE = f"""복구 순서 (저장소 루트에서 실행):
1. {TARGET_PATH}의 계약 변경 내용을 먼저 검토합니다.
2. 실제 파일 bytes의 SHA-256을 계산하여 {RECEIPT_PATH}의
   contract_authority.local_target.sha256을 수동 갱신합니다:
   python -c "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('{TARGET_PATH}').read_bytes()).hexdigest())"
3. local_target 갱신 후 기존 도구로 Receipt canonical hash를 재계산합니다:
   python scripts/verify_rag_01_receipt.py {RECEIPT_PATH} --write
   --write는 receipt_hash만 갱신하며 문서 bytes hash·traceability는 수정하지 않습니다.
4. {TRACEABILITY_PATH}의 Source JSON fixture 문단에 있는 canonical hash를
   3단계에서 출력된 receipt_hash.value로 수동 갱신합니다.
5. 관련 계약 테스트를 다시 실행합니다:
   uv run pytest tests/contract/test_source_receipt_recovery.py tests/contract/test_rag_source_governance_receipt.py tests/contract/test_rag_01_ocr_input_receipt.py
CI 검사는 파일을 자동 수정하지 않습니다. 해시 재생성은 승인·공개 근거가 아닙니다.
"""


def verify_source_contract_receipt(project_root: Path) -> str:
    receipt_path = project_root / RECEIPT_PATH
    receipt = load_receipt(receipt_path)
    target_hash = hashlib.sha256((project_root / TARGET_PATH).read_bytes()).hexdigest()
    canonical_hash = calculate_receipt_hash(receipt)
    traceability = (project_root / TRACEABILITY_PATH).read_text(encoding="utf-8")
    source_lines = [
        line for line in traceability.splitlines() if "(../../tests/fixtures/rag/source_contract_receipt.json)" in line
    ]
    trace_hashes = [
        digest for line in source_lines for digest in re.findall(r"canonical hash는 `sha256:([^`]+)`", line)
    ]
    mismatches = []
    metadata = receipt.get("receipt_hash")
    stored_canonical_hash = metadata.get("value") if isinstance(metadata, dict) else None
    local_target = receipt["contract_authority"]["local_target"]
    if local_target["repository_path"] != TARGET_PATH.as_posix():
        mismatches.append(f"{RECEIPT_PATH}: contract_authority.local_target.repository_path must be {TARGET_PATH}")
    comparisons = [
        (
            f"{RECEIPT_PATH}: contract_authority.local_target.sha256 (bytes: {TARGET_PATH})",
            local_target["sha256"],
            target_hash,
        ),
        (f"{RECEIPT_PATH}: receipt_hash.value", stored_canonical_hash, canonical_hash),
        (f"{TRACEABILITY_PATH}: Source canonical hash", trace_hashes, [canonical_hash]),
    ]
    for field, stored, calculated in comparisons:
        if stored != calculated:
            mismatches.append(f"{field}\n  stored={stored!r}\n  calculated={calculated!r}")
    try:
        verify_receipt_hash(receipt_path)
    except ValueError as error:
        mismatches.append(f"{RECEIPT_PATH}: {error}")
    if mismatches:
        raise ValueError("Source Receipt 검증 실패:\n" + "\n".join(mismatches) + "\n\n" + RECOVERY_GUIDANCE)
    return canonical_hash


if __name__ == "__main__":
    print(verify_source_contract_receipt(Path.cwd()))
