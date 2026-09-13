# Catalog v2 합성 hash fixture

#166 DB 통합 2단계에서 기존 v2 계산 결과를 고정했다. 운영 승인 증빙이 아니다.
두 Product, 다른 Snapshot 출처의 승인 Alias, NFD 제품 표시값, 두 Source의 합성 승인 receipt를 포함한다.
입력 구성은 `ai_worker/tests/rag/catalog/test_hash_contract_v2.py::approved_export`에 고정되어 있다.

- `catalog.jsonl`: 마지막 LF를 포함한 실제 export bytes.
- `envelope-payload.json`: 자기 hash 필드와 마지막 LF를 제외한 계산 입력 bytes.
- `manifest.json`: envelope hash 필드를 포함하고 마지막 LF를 붙인 전달 파일.
- `expected.json`: 위 세 파일의 고정 SHA-256. manifest 파일 checksum은 비교 증빙이며 새로운 API 필드가 아니다.

최초 fixture는 기존 생산 함수로 산출하고, Python 표준 JSON 직렬화로 envelope 입력을 별도 복원해
고정했다. `shasum -a 256`으로 파일 digest를 독립 확인한다. 테스트 실행은 fixture나 기대값을
재생성하지 않는다. 생산 결과의 bytes와 고정 파일을 비교하고, 고정된 기대 digest와 비교한다.
새 버전으로 계약을 변경할 때만 입력·bytes·기대 digest의 변경 이유를 함께 리뷰한다.
기존 `docs/validation/rag/catalog/synthetic-catalog-v1` 증빙은 변경하지 않는다.
