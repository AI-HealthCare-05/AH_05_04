# #591 XML 제한 수집 Receipt 발급·검증 계약

작성일: 2026-09-16
상태: **2026-09-16 담당자 승인 목표 / 구현 브랜치 검증 중 / 서버 미적용**
책임 리뷰어 송은영, 전문 증빙: 송은영(DB·transaction·Source 오류/증빙 계약), 권가빈(기존 정책 범위 및 서버 설정 대조)
발급·Operation 등록·profile 반영: 김지혜

## 1. 승인한 범위

아래 A~D는 담당자가 승인한 규격입니다. 기존 승인 범위를 다시 요청하는 문서는 아닙니다.

| 항목 | 확정 기준 |
| --- | --- |
| A. manifest_hash 대상 | 제품 Receipt 자체의 검증 본문. acquisition-manifest.json 파일 hash가 아님 |
| B. Endpoint Receipt | XML 제한 수집 전용 버전의 별도 JSON. 제품 Receipt manifest_hash와 정확한 제품·Operation·문서집합을 포함 |
| C. 결속 | 원문 → 제품 Receipt → Endpoint Receipt → acquisition manifest 및 profile의 단방향 연결 |
| D. 발급 순서 | 원문/취득 증빙 검증 → 제품 Receipt → Endpoint Receipt → acquisition manifest → 사전검증 → Operation 등록·재조회 → profile READY |

기존 Source `MFDS_PRODUCT_LABEL`, Endpoint `MFDS_NEDRUG_LABEL_XML`을 재사용합니다. 제품별 Operation은 `COLLECT_MFDS_{ITEM_SEQ}_LABEL_XML`, 초기 상태는 합의된 APPROVED / ENABLED입니다. 기존 Source/Endpoint 승인 메타데이터를 덮어쓰지 않습니다.

범위는 남은 16개 EE·UD·NB 및 승인된 일반약 5개 NN입니다. 내부 제한 수집·보존·적재 범위이며, RAG 사용·CURRENT 선정·Runtime/Citation·서비스 공개 승인을 부여하지 않습니다.

## 2. 현재 코드에서 확인한 사실과 필요한 구현

대조 기준: 구현 base `b9ae2edf`의 Source 코드. 승인 근거: #591 규격안 A~D·4~7절에 대한 송은영의 2026-09-16 회신(김지혜 전달). 실제 발급은 추적 가능한 확정 ref를 별도 입력한다.

- `ai_worker/tasks/rag/source_client/receipts.py`의 `build_receipt_payload`는 일반 API용 `EndpointReceipt` dataclass를 받습니다. XML용 임의 dict를 그대로 넣는 함수가 아닙니다.
- 동일 파일의 `write_endpoint_receipt`는 임시 파일을 만들고 기존 파일을 replace합니다. 이번 제한 증빙의 0600·기존 파일 덮어쓰기 금지는 이 함수만으로 보장되지 않습니다.
- `ai_worker/tasks/rag/source_ingestion/receipt_validation.py`의 `calculate_endpoint_receipt_hash`는 일반 Mapping의 최상위 generated_at/receipt_hash를 제외한 canonical JSON을 hash합니다. 이 함수는 XML 필수 필드·승인 범위를 검증하지 않습니다.
- 같은 파일의 기존 product/detail Receipt loader는 일반 API 계약 전용입니다. XML Receipt를 기존 1.1/1.2 API Receipt라고 표기하거나 pagination·body code·검증 결과를 임의로 채우지 않습니다.
- `mfds_label.py`의 `load_mfds_label_plan`은 입력 Endpoint hash의 형식과 acquisition manifest의 동일 값을 확인합니다. Endpoint Receipt 파일 자체의 승인·필수 내용을 검증하는 기능으로 해석하지 않습니다.

XML 전용 발급·검증 도구를 구현했습니다. 기존 hash 계산 함수를 재사용하고 XML 스키마 검증과 제한 파일 저장을 추가합니다. 실제 서버 적용과 제품별 최종 발급은 구현 리뷰·반영 후 별도 수행합니다.

## 3. 네 종류의 hash 구분

| 이름 | 정확한 대상 | 사용처 |
| --- | --- | --- |
| raw_sha256 | 수집·보존한 XML 원본 bytes 그대로 | Artifact 크기/무결성 검증 |
| 문서별 canonical_sha256 | 아래 4절의 문서 canonical payload | 제품 Receipt의 문서별 증빙 |
| manifest_hash | 아래 5절의 제품 Receipt 검증 본문 | Endpoint Receipt가 제품 Receipt에 결속 |
| receipt_hash / endpoint_receipt_hash | 아래 6절의 Endpoint Receipt 검증 본문 | profile·acquisition manifest·ingestion에 동일하게 전달 |

Snapshot canonical_checksum은 기존 parser의 제품 전체 canonical manifest hash이며, 위 manifest_hash 또는 문서별 canonical_sha256으로 대체하지 않습니다. 기존 ingestion raw_manifest_checksum도 별도 값이며 이번 manifest_hash로 대체하지 않습니다.

## 4. 공통 JSON 및 문서 canonical 규칙 — 확정 기준

### JSON 직렬화

- UTF-8, BOM 없음. 객체 키는 문자열만 허용합니다.
- `json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`.
- JSON 중복 키, 미정의 필드, NaN/Infinity, float를 거부합니다. 크기는 양의 정수이며 bool을 크기로 허용하지 않습니다.
- 문서 배열은 EE → UD → NB → NN 고정 순서입니다. NN은 승인된 제품만 포함합니다.
- 문자열을 발급 시 임의 trim하거나 Unicode 정규화하지 않습니다. XML 구조의 정규화는 기존 parser 규칙을 따릅니다.
- 시간은 실제 증빙을 기반으로 UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` 형식으로 변환합니다. 입력의 시간대를 UTC로 변환하되 실제 시각을 현재시각으로 대체하지 않습니다.
- hash는 lowercase SHA-256 64자리입니다. 표시용 JSON의 들여쓰기·개행은 hash 대상이 아닙니다.
- 승인 ref는 확정된 #591 댓글 등 비밀값 없는 불변 식별자를 사용합니다. 작성 예정인 댓글이나 placeholder를 승인 근거로 발급하지 않습니다.

### 문서별 canonical_sha256

기존 parser의 XML 구조 생성 결과를 재사용하며 다음 정확한 객체를 위 JSON 규칙으로 hash합니다.

```text
{
  "canonicalization_spec_version": "mfds-label-selected-product@1",
  "item_seq": 실제 제품 식별자 문자열,
  "document_type": EE / UD / NB / NN,
  "document_title": 기존 parser가 읽은 DOC title,
  "structure": 기존 parser의 canonical_structure
}
```

이 문서별 hash의 payload 정의는 **이번 승인으로 추가된 계약**입니다. 기존 코드가 이미 문서별 canonical_sha256을 발급한다고 주장하지 않습니다. 기존 `_canonical_element`의 data-* 속성 제외, 텍스트 NFC·줄바꿈 정규화, 자식 순서를 재사용하며 별도 XML canonicalizer를 만들지 않습니다.

제품 전체 canonical_checksum은 기존 `_canonical_checksum`과 동일한 계산을 재사용합니다. placeholder Endpoint hash로 load_mfds_label_plan을 호출해 Receipt를 만드는 방식은 사용하지 않습니다. Receipt 없이 문서 canonical 구조를 계산할 수 있는 공통 순수 함수를 추출·재사용하고, 기존 계산 결과 불변을 회귀 검증합니다.

## 5. 제품 Receipt — 확정 기준 스키마

파일명: `product-receipt.json` (private 증빙 폴더).
스키마 버전: `mfds-label-product-receipt@1`.
아래 필드는 모두 필수이며 null/추가 필드는 허용하지 않습니다.

| 필드 | 값/의미 |
| --- | --- |
| schema_version | 위 버전 |
| item_seq | 9자리 문자열 |
| official_product_name | 공식 증빙과 대조한 제품명. 추정 이름 금지 |
| identity | source_code, endpoint_code, operation_code 세 문자열의 객체 |
| document_types | 승인된 정확한 문서집합, 고정 순서 |
| collected_at | 필수 문서 마지막 응답 수신 완료 시각. 각 received_at도 별도 보존 |
| versions | schema_version, parser_version, normalization_version, canonicalization_spec_version 네 필드 |
| documents | 아래 문서 객체의 배열 |
| canonical_checksum | 기존 parser의 제품 전체 canonical_checksum |
| policy_approval_ref | 기존 제품·문서범위 정책 승인 근거 |
| technical_approval_ref | 이번 최종 규격 확정 근거 |
| product_identity_evidence_ref | 제한 채널의 공식 제품명/ITEM_SEQ 대조 증빙 ID. 실제 경로 제외 |
| implementation_git_sha | 실제 계산에 사용한 코드 commit |
| generated_at | Receipt 생성 시각 |
| manifest_hash | 아래 규칙으로 계산한 제품 Receipt hash |

versions의 값은 현재 schema/canonicalization `mfds-label-selected-product@1`, parser `mfds-label-xml@1`, normalization `mfds-label-xml-structure@1`입니다. 변경된 구현으로 발급하면 실제 버전과 계약을 대조합니다.

각 documents 객체는 다음 필드만 가집니다.

| 필드 | 의미 |
| --- | --- |
| document_type | EE / UD / NB / NN |
| file_name | `{document_type}.xml` |
| source_url | `https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/{ITEM_SEQ}/{document_type}` |
| artifact_key | 기존 metadata key `mfds-label/{ITEM_SEQ}/{document_type}.xml` |
| byte_size / raw_sha256 | 보존한 실제 XML bytes의 크기/hash |
| content_type | 수집 시 관측한 헤더 문자열 그대로 |
| received_at | 해당 응답의 실제 수신 완료 UTC 시각 |
| http_status | 실제 관측값 200 |
| canonical_sha256 | 4절 문서별 payload hash |
| content_status | 기존 inspect_xml 결과 |
| empty_article_titles | 기존 parser가 확인한 공식 공백 목록; 없으면 빈 배열 |

artifact_key는 최종 저장소 경로, object_key, Member logical_locator와 구분합니다. Artifact UUID·Run UUID·Snapshot UUID는 적재 후 생성되는 값이므로 이 Receipt에 미리 넣지 않습니다.

**manifest_hash = SHA256(canonical JSON(제품 Receipt에서 최상위 generated_at과 manifest_hash만 제외))**.

제품 Receipt에는 Endpoint Receipt hash, acquisition-manifest.json의 hash 또는 경로를 넣지 않습니다. 이 규칙으로 역참조를 차단합니다. manifest_hash에 임의 파일 바이트 hash를 넣지 않습니다.

## 6. Endpoint Receipt — 확정 기준 스키마

파일명: `endpoint-receipt.json` (같은 private 증빙 폴더).
스키마 버전: `mfds-label-xml-endpoint-receipt@1`.
아래 필드는 모두 필수이며 null/추가 필드는 허용하지 않습니다.

| 필드 | 값/의미 |
| --- | --- |
| receipt_version | 위 XML 전용 버전 |
| identity | 제품 Receipt와 동일한 source_code / endpoint_code / operation_code |
| item_seq / official_product_name | 제품 Receipt와 동일 |
| document_types | 제품 Receipt와 동일한 순서·집합 |
| collected_at | 제품 Receipt와 동일 |
| product_receipt | 정확히 schema_version, manifest_hash 두 필드. 제품 Receipt의 값과 동일 |
| verified_http_method | GET |
| verified_scheme | https |
| verified_host | nedrug.mfds.go.kr |
| verified_path_template | `/pbp/cmn/xml/drb/{ITEM_SEQ}/{document_type}` |
| encoding | UTF-8 (실제 XML decode 검사 후) |
| verification_results | 아래 네 boolean 모두 true인 경우에만 최종 발급 |
| policy_approval_ref / technical_approval_ref | 제품 Receipt와 동일 |
| scope | 아래 고정 범위 객체 |
| implementation_git_sha | 실제 발급·검증 코드 commit |
| validated_at | 원문·제품 Receipt 재검증 완료 시각 |
| generated_at | Endpoint Receipt 생성 시각 |
| receipt_hash | 기존 calculate_endpoint_receipt_hash 규칙으로 계산 |

verification_results의 정확한 키:
`required_document_set_matches`, `raw_size_sha256_matches`, `xml_structure_valid`, `product_receipt_hash_matches`.
이는 수행한 검사 결과입니다. HTTP 성공·DB commit·현재 허가상태·consumer acceptance를 대신 선언하지 않습니다. HTTP 관측값은 제품 Receipt의 문서별 증빙에 포함되어 hash로 연결됩니다.

scope의 정확한 키와 값:

```json
{
  "limited_collection": true,
  "private_preservation": true,
  "source_snapshot_ingestion": true,
  "internal_consumer_handoff": true,
  "rag_use_authorized": false,
  "runtime_citation_publication_authorized": false,
  "service_publication_authorized": false
}
```

**receipt_hash = SHA256(canonical JSON(Endpoint Receipt에서 최상위 generated_at과 receipt_hash만 제외))**.

product_receipt.manifest_hash는 hash 대상에 **포함**합니다. 기존 `calculate_endpoint_receipt_hash`와 `verify_endpoint_receipt_hash`로 계산·검증하되 XML 전용 스키마 검증을 반드시 선행합니다. hash 일치만으로 승인 증빙으로 인정하지 않습니다.

## 7. acquisition manifest·profile 연결과 발급 순서

```text
XML bytes + 실제 취득 증빙
  → product-receipt.json [manifest_hash]
  → endpoint-receipt.json [receipt_hash]
  → acquisition-manifest.json.endpoint_receipt_hash
  → profile.endpoint_receipt_hash (같은 receipt_hash)
```

1. 실제 XML의 제품·문서집합·취득시각·크기/hash·구조 및 승인 근거를 검증합니다.
2. 제품 Receipt를 생성하고 manifest_hash를 독립 재계산합니다.
3. 제품 Receipt를 검증한 뒤 Endpoint Receipt를 생성하고 receipt_hash를 재계산합니다.
4. 기존 `mfds-label-acquisition-evidence@1`을 **변경하지 않고** acquisition-manifest.json을 생성합니다. 최상위는 schema_version, item_seq, collected_at, endpoint_receipt_hash, documents 다섯 필드만 사용합니다. 문서 필드는 document_type, file_name, source_url, raw_sha256, byte_size, content_type 여섯 개만 사용합니다.
5. profile과 acquisition manifest에 같은 Endpoint receipt_hash를 넣습니다. manifest_hash/원문 hash/노바스크 hash를 대신 넣지 않습니다.
6. 발급 도구가 네 파일 간 identity·문서집합·시간·hash 연결과 승인 범위를 대조하고, 기존 load_mfds_label_plan으로 전체 입력을 검증합니다. plan의 canonical_checksum을 제품 Receipt와 비교합니다.
7. 기존 Source/Endpoint 상태를 재조회하고 새 제품 Operation만 등록합니다. 기존 행 발견 시 덮어쓰지 않고 대조합니다. 새 session에서 실제 등록값·상태를 재조회합니다.
8. 실제 등록·Receipt 확인을 모두 통과한 profile만 READY로 반영합니다. 전역 writer env를 바꾸지 않습니다.
9. 최종 파일·hash·확정 근거를 제한 채널로 가빈님께 전달해 서버 설정을 대조합니다. 서버 반입 후 바이트 검증을 거쳐 3개 → 일반약 5개 → 8개 순서로 적재합니다.
10. 제품별 CREATED → 새 session 재조회 → 동일 입력 NO_CHANGE·동일 Snapshot을 확인하고 실제 DB 참조를 별도 인계합니다. 적재 결과를 쓰려고 이미 발급한 Receipt를 수정하지 않습니다.

Receipt 파일들은 입력 폴더와 분리합니다. 입력 폴더는 XML 3/4개와 acquisition-manifest.json만 포함합니다. 원문·실제 private 경로·credential은 공개 문서에 넣지 않습니다. 새 증빙 파일은 0600으로 배타 생성하고 기존 파일을 덮어쓰지 않습니다.

## 8. 발급 도구 필수 검증

- 동일 검증 본문은 생성 시각/표시용 공백이 달라도 동일 hash. 검증 필드 변경은 hash 변경.
- 제품·Operation·NN 승인 범위·문서 순서 불일치, 중복/누락 문서, 미정의 필드 거부.
- raw checksum / manifest_hash / Endpoint receipt_hash 오대입과 노바스크 Receipt 재사용 거부.
- 제품 Receipt hash를 변조하면 Endpoint 결속 검증 실패. acquisition manifest/profile hash가 다르면 실패.
- 공통 canonical 계산 리팩터링 시 기존 Snapshot canonical_checksum·source_version 불변 검증.
- symlink·기존 증빙 덮어쓰기 거부 및 private 파일 권한 검증.
- 승인 ref 누락·미완료 검증·null/placeholder 값은 최종 발급 및 READY 전환 불가.
- 발급만으로 DB 상태나 공개 게이트 변경 없음. 실제 수집 결과를 합성 fixture로 대체하지 않음.

## 9. 구현과 운용 경계

구현: `ai_worker/admin/mfds_label_receipts.py`. Receipt 발급/검증 전용이며 DB credential, catalog 등록, profile 변경, READY 선언, 네트워크 수집은 수행하지 않는다.
공통 XML 정규화는 `canonicalize_label_xml`, 기존 제품 전체 checksum은 `label_canonical_checksum`을 사용한다.

입력 `approval.json`은 정확히 item_seq, official_product_name, document_types, policy_approval_ref, technical_approval_ref, product_identity_evidence_ref, implementation_git_sha 7개 필드다.
입력 `evidence.json`은 정확히 item_seq, collected_at, documents 3개 필드다. documents는 5절 문서 필드 중 document_type, file_name, source_url, raw_sha256, byte_size, content_type, received_at, http_status 8개 필드만 받는다. canonical/artifact/content-status 값은 도구가 계산한다.
기존 collection-evidence 파일의 관리용 필드는 그대로 입력하지 않고 이 명시적 필드만 추출한다. 취득시각과 원문값은 보존한다.

승인 ref는 비밀값 없는 opaque ID(영숫자 시작, 영숫자 및 `_.:#@-`) 또는 이 저장소 #591 issue/comment URL이다. null·placeholder·pending·todo·tbd·example 및 꺾쇠 placeholder는 거부한다. implementation git sha는 실제 사용한 40자리 소문자 commit으로 운영자가 입력한다. 도구는 문자열 형식과 결속을 검사하지만 외부 승인 사실·공식 제품명·실제 배포 commit을 온라인 인증하지 않는다. 담당자가 원본 근거와 대조해야 한다. Hash는 승인 서명이나 독립적인 수집 증명이 아니다.

승인된 16개 ITEM_SEQ와 NN 5개는 도구의 allowlist로 제한한다. 제품 확대는 별도 승인·계약 변경이다. 원문/JSON 파일은 0600, 상위 입력·증빙 폴더는 0700이며 실행 OS 사용자 소유여야 한다. ancestor symlink, 비정규 파일, 읽기 상한 초과는 거부한다. 이 도구는 private staging 소유자 경계에서 실행하며 read-only consumer mount에서 파일을 발급하지 않는다.

명령(모든 경로는 운영자가 제한 채널에서 확인한 실제 경로 사용):

```sh
python -m ai_worker.admin.mfds_label_receipts issue --approval /PRIVATE/approval.json --evidence /PRIVATE/evidence.json --input-dir /PRIVATE/inputs --receipt-dir /PRIVATE/receipts
python -m ai_worker.admin.mfds_label_receipts verify --approval /PRIVATE/approval.json --evidence /PRIVATE/evidence.json --input-dir /PRIVATE/inputs --receipt-dir /PRIVATE/receipts --profile /PRIVATE/profile.json
```

위 PRIVATE는 설명용 표기이며 발급 JSON의 승인 ref로 사용하지 않는다. 기존 서버 고정 source591-python은 인자를 받지 않으므로 이 CLI 명령을 그 실행기에 그대로 붙이지 않는다. 서버 배포·호출은 별도 운영 경로로 대조한다.

verify는 product/endpoint/acquisition을 원문·승인 입력에서 재생성하여 정확한 필드까지 대조하고 load_mfds_label_plan으로 checksum을 검증한다. 선택적인 profile 검사는 identity·제품·NN 여부·endpoint_receipt_hash·policy/technical ref를 대조한다. 서버 mount input_dir 매핑과 실제 DB 등록·READY 여부는 검증 결과에 포함하지 않는다.

파일은 배타 생성하며 fchmod(0600), flush/fsync 후 재조회한다. 파일 세 개 전체가 filesystem transaction인 것은 아니다. 중간 I/O 오류는 이미 생성한 증빙을 보존하고, 재실행은 기존 파일을 발견하면 중단한다. 자동 삭제/재발급하지 않는다. 독립적인 새 배치로 재발급할지는 담당자가 실패 상태를 확인해 결정한다.

결과는 `XML_RECEIPTS_VERIFIED`, 두 hash, 제품 ID, database_changed=false, profile_ready_changed=false만 출력한다. CLI 실행 중 검증 실패는 고정 `XML_RECEIPT_STOP`을 출력하고 exit 1로 끝난다. private 경로·원문·외부 exception message를 출력하지 않는다.

이 계약 구현/테스트의 PR 승인·병합과 서버 설정 대조는 실제 16개 제품 적재와 별도다. 계약 확정만으로 READY, Source 검증 상태 또는 공개 승인이 바뀌지 않는다.
