# Source Receipt 해시 불일치 복구 (#445)

Source 계약 문서의 bytes hash, Receipt canonical hash, traceability의 Source canonical hash는 순서대로 연결된다. `tests/contract/test_source_receipt_recovery.py`가 기존 CI의 계약 테스트에 자동 수집되어 세 값을 함께 검사한다. 실패 메시지에는 대상 파일·필드, 저장값(`stored`)·계산값(`calculated`), 아래 복구 순서가 표시된다.

저장소 루트에서 DB 없이 진단할 수도 있다.

```bash
uv run python -m scripts.verify_source_contract_receipt
```

## 복구 순서

1. `docs/contracts/targets/post-mvp-1/rag-source-ingestion-v1.md`의 변경 내용을 검토한다. 의도하지 않은 변경이면 먼저 원인을 수정한다.
2. 실제 파일 bytes의 SHA-256을 계산한다.

   ```bash
   python -c "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('docs/contracts/targets/post-mvp-1/rag-source-ingestion-v1.md').read_bytes()).hexdigest())"
   ```

   결과를 `tests/fixtures/rag/source_contract_receipt.json`의 `contract_authority.local_target.sha256`에 수동 반영한다. 줄바꿈과 UTF-8 bytes도 계산 대상이다.
3. 기존 도구로 Receipt canonical hash를 갱신한다.

   ```bash
   python scripts/verify_rag_01_receipt.py tests/fixtures/rag/source_contract_receipt.json --write
   ```

   이 명령은 `receipt_hash`만 갱신한다. 문서 bytes hash와 traceability는 갱신하지 않는다. 2단계 전에 실행한 출력값은 사용하지 않는다.
4. `docs/testing/post-mvp-1-contract-traceability.md`의 Source JSON fixture 링크가 있는 문단에서 `canonical hash는` 뒤의 SHA-256을 3단계 출력값으로 수동 갱신한다. 다른 Receipt의 hash는 변경하지 않는다.
5. 관련 계약 테스트를 실행한다.

   ```bash
   uv run pytest tests/contract/test_source_receipt_recovery.py tests/contract/test_rag_source_governance_receipt.py tests/contract/test_rag_01_ocr_input_receipt.py
   ```

진단은 현재 Receipt 내용의 canonical hash를 보여준다. 2단계에서 local_target을 수정하면 canonical hash도 바뀌므로 3단계에서 다시 계산해야 한다. 다른 검증(Decision·suite artifact hash·승인 조건 등)이 실패하면 해당 원인도 별도로 확인한다.

CI는 파일을 자동 수정하지 않는다. 해시 일치는 내용 연결을 검증할 뿐, 계약 변경 승인이나 실제 Source 활성화·공개 승인을 의미하지 않는다. 기존 승인·공개 게이트와 canonicalization 규칙은 유지한다.

## 회귀 검증

- 문서만 변경, Receipt 내용만 변경, traceability만 변경한 각각의 불일치를 검출하고 파일을 수정하지 않는지 확인한다.
- 합성 문서 변경 후 위 순서대로 local_target → 기존 `--write` → traceability를 갱신하여 세 값이 모두 일치하는지 검증한다.
- 잘못된 hash algorithm·canonicalization을 계속 거부한다.
- 다른 Receipt 문단의 hash 또는 중복 Source hash로 검사를 우회하지 못하는지 확인한다.
- 기존 Source Governance 계약 테스트를 함께 실행하여 승인·공개 경계 검증을 유지한다.
