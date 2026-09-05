# MFDS P0 Endpoint 검증

Issue #155는 Local 환경에서만 MFDS P0 Operation 3종을 검증합니다. Probe는
Provider 응답 원문, 인증정보가 포함된 URL 또는 API 인증키를 저장하지 않습니다.
`tests/fixtures/rag/mfds/`에 커밋된 fixture는 비식별 합성 데이터이며 실제
Provider 레코드를 포함하지 않습니다.

## Local 실행

공공데이터포털 인증키 중 Decoding 키를 사용하고 저장소 밖에서 관리합니다.

```bash
read -r -s "RAG_MFDS_API_KEY?MFDS Decoding key: "
echo
export RAG_MFDS_API_KEY
```

완료 Receipt는 전체 페이지 수집을 수행한 경우에만 기록할 수 있습니다.

```bash
RAG_MFDS_LIVE_VALIDATION=1 \
PYTHONPATH="$PWD/backend:$PWD" \
uv run python -m ai_worker.tasks.rag.source_client.probe \
  --operation LIST_APPROVED_PRODUCTS \
  --output-dir docs/validation/rag/endpoints \
  --full-scan \
  --write-receipt
```

다른 P0 Endpoint를 검증하려면 Operation을
`LIST_INGREDIENT_CONTRAINDICATIONS` 또는
`LIST_PATIENT_MEDICATION_GUIDES`로 변경합니다. 전체 수집이 실패하더라도
민감정보가 제거된 FAILED Receipt를 남기고 0이 아닌 종료 코드로 끝납니다.

정확한 Local opt-in 값이 없으면 Provider를 호출하지 않고 `NOT_RUN` Receipt만
기록합니다.

## 2026-09-05 실측 결과

| Operation | 건수 | 기본키 관찰 결과 | Endpoint parser gate |
| --- | ---: | --- | --- |
| `LIST_APPROVED_PRODUCTS` | 42,989 | `ITEM_SEQ`: null 0, duplicate 0 | Endpoint gate 허용 |
| `LIST_INGREDIENT_CONTRAINDICATIONS` | 1,836 | 후보키 null 0, duplicate 469; 완전 중복 행 1 | 차단 |
| `LIST_PATIENT_MEDICATION_GUIDES` | 4,782 | `itemSeq`: null 0, duplicate 17; 완전 중복 행 0 | 차단 |

DUR과 환자용 복약정보 Endpoint는 호출 및 Pagination 경계 검증에 성공했습니다.
다만 Provider payload에서 안정적인 고유 자연키가 확인되지 않아 차단했습니다.
Client는 임의 식별자를 만들거나 중복 행을 조용히 제거하지 않고 해당 결과를
fail-closed `SCHEMA_DRIFT`로 보존합니다.

DUR의 `NOTIFICATION_DATE`는 #155 실측 후보키 구성요소이면서 외부 버전 후보로도
기록되어 있습니다. 이는 확정된 canonical identity가 아닙니다. #165에서 날짜가
레코드 정체성의 일부인지 변경 버전인지 명시적으로 결정하기 전까지 DUR Parser와
Snapshot 후보 등록은 계속 차단합니다.

제품 Endpoint의 적합성 확인만으로 공개 RAG Runtime이 활성화되지는 않습니다.
Parser canonicalization과 불변 Source Snapshot은 #165, Catalog lifecycle은 #166의
범위입니다. Runtime 활성화 전에는 외부 Source 승인과 저장소 release gate도 모두
통과해야 합니다.
