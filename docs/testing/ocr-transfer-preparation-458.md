# #458 동의 계약 대기 중 Worker/OCR 사전 검증

- 날짜: 2026-09-13
- 기준: 로컬 `feat/458-ocr-consent-boundary`, `582d847c390a300cd5176836ef6bedc8d55805f5`
- 상태: 현행 코드 분석·합성 재현 완료. 최소화 selector·동의 Gate 구현 완료 아님.
- 담당: 지혜 Worker/OCR. 은영 Backend/Security, 가빈 Privacy, 한솔 Frontend 계약 확인은 별도.
- 외부 Provider·DB 호출 없음. 실제 환자 정보·API key 없음.

## 최초 외부 처리 위치

1. `frontend/src/pages/PrescriptionUploadPage.tsx`에서 `uploadPrescription(file)`을 기다린 후
   같은 실행 흐름에서 바로 `executeOcr(documentId, ...)`을 호출한다.
2. `frontend/src/api/prescriptions.ts`에서 업로드는 `POST /api/v1/documents`,
   접수는 `POST /api/v1/documents/{document_id}/ocr-jobs`로 분리되어 있다.
3. `backend/app/apis/v1/medical_document_routers.py`의 OCR 접수는
   `accept_ocr_job` 및 공통 Job 접수를 거쳐 202를 반환한다.
4. `ai_worker/tasks/ocr/handler.py`는 Job의 입력을 조회하고 Provider를 호출한다.
5. `ocr_runtime/clova_engine.py`는 `_recognize_provider`에서 CLOVA 요청을 수행한 뒤,
   `recognize`에서 `parsed_result.raw_fields`를 구조화기에 전달한다.
6. `ocr_runtime/llm/structurer.py`가 LLM `provider.generate`를 호출한다.

따라서 “등록 후 별도 화면에서 동의받는다”는 가정은 현재 Frontend 흐름에 맞지 않는다.
한솔 의견대로 업로드/처리 시작 동작 앞에 안내를 배치하는 안을 검토하고, 서버 접수 및
각 외부 호출 직전 재검사로 보완한다. Frontend 검사만으로 외부 호출을 허용하지 않는다.
이 분석은 OCR/LLM 호출 경계에 관한 것이며, 문서 저장 인프라의 외부 전송까지 조사 완료했다는
의미는 아니다. 문서 업로드 자체의 고지·동의 범위도 기존 정책과 대조해야 한다.

## 최소화 영향과 필요한 내부 설계

| 확인한 현행 코드 | 영향 | 후속 구현 방안 |
| --- | --- | --- |
| 전체 raw_fields를 enumerate(start=1)하여 전송 | 약품 외 token도 외부 입력에 포함됨 | 허용 영역·항목을 로컬에서 먼저 선별. 불명확하면 호출하지 않음 |
| center_x/center_y/height/confidence를 전송 DTO가 받음 | 전송 최소화에는 payload 변경 필요 | 외부 필드와 로컬 검증 metadata를 분리. 삭제만으로 호환된다고 주장하지 않음 |
| validator가 전체 raw_fields를 다시 enumerate | 부분 목록을 넣으면 원본 ID가 이동함 | 원본 ID 보존과 실제 전송한 ID 집합 검사 필요 |
| validator는 실제 전송 ID 집합을 받지 않음 | 전체 raw_fields에 존재하는 미전송 ID를 구분할 수 없음 | 출력의 모든 근거 ID를 전송 집합과 대조한 뒤 기존 grounding 수행 |
| 인접 숫자·단위 검증에서 ID + 1 및 위치 사용 | 삭제 후 재번호화하면 다른 token이 인접한 것으로 바뀔 수 있음 | 원래 위치·ID를 로컬에 유지, 잘못된 인접성 생성 방지 |
| confidence는 결과 필드 최소 confidence 산출에 사용 | 외부 전송 제외와 로컬 삭제는 다른 작업 | 결과 검증·검수용 로컬 metadata 유지 |
| LLM 예외는 현재 Worker 실패로 전달 | 최소화 생략을 예외로만 구현하면 일반 실패와 혼동 | 생략·동의 차단·실패 결과 연결은 Backend 계약 확정 뒤 구현 |

규칙 기반 파서의 최종값만 보내는 방식은 OCR 분류 오류 보완을 막을 수 있다. 허용 token 선택은
완성된 약품 레코드 존재만을 조건으로 삼지 않는 안을 검토한다. 반대로 넓은 행/영역을 통째로
허용하면 개인정보가 섞일 수 있다. 임의 confidence 임계치·정규식만으로 안전을 선언하지 않는다.

## 실행한 합성 테스트

`ai_worker/tests/ocr/test_transfer_boundary_baseline_458.py` 4건:

1. 약품과 합성 private sentinel이 섞인 입력에서 현재 mock Provider 요청에 sentinel과 위치 정보가
   들어감을 재현한다. **현재 결함의 기준선이며 개인정보 비전송 통과 증빙이 아니다.**
2. 원본 ID=2인 약품만 남긴 목록은 현행 validator에서 ID=1이 되어 원래 근거가 깨짐을 확인한다.
3. 현행 validator에는 전송 허용 ID 집합이 없고 전체 OCR 목록의 유효 참조를 받아들임을 확인한다.
4. 실제 OCR 목록에 없는 ID=999는 거부됨을 확인한다.

현재 경계 테스트 4건과 기존 Worker LLM 연결 테스트 12건: **16 passed**.
새 selector 연결 시 1·3번 현행 한계 재현을 각각 sentinel 비전송·미전송 ID 거부 인수 테스트로
교체한다. 현행 노출 동작을 영구 요구사항으로 유지하지 않는다.

## 구현 전 준비한 인수 시나리오 — 아직 실행 통과 아님

| 합성 입력/행동 | 기대 검증 | 아직 필요한 결정 |
| --- | --- | --- |
| 약품 행과 별도 이름·연락처 sentinel | 허용 약품 token만 전송, sentinel 비전송 | selector의 안전한 영역 구분 규칙 |
| 하나의 token에 약품명과 private sentinel 혼재 | 전체 token 전송 금지, 안전한 분리가 불가하면 호출 0건 | 최소화 생략 공개 상태 |
| 헤더 없는 숫자·일반 문장만 존재 | 임의 약품 분류·추론 전송 금지 | 판정 근거·생략 처리 |
| 약품명 두 줄 분할, 복용량 일부 누락 | 허용 근거로만 구조화, 없는 값은 추정하지 않고 검수/직접 입력 | 필드·라벨·추가 metadata 허용 범위 |
| 원래 ID 1,3 사이에 제외 token 2 | 필터 후 재번호화로 인접 근거를 만들지 않음 | 전송 ID·validator 입력 계약 |
| LLM이 미전송 ID를 처방일·약품·선택 복용 필드에 참조 | 모든 생성 필드에서 거부, 선택 필드 빈칸 대체로 위반을 숨기지 않음 | validator 앞 공통 검사 경계 |
| 유효 동의·최소화 실패 | LLM 호출 0건, 사용 가능한 규칙 결과만 인계 | 결과 metadata·Frontend 표시 |
| CLOVA 처리 중 철회·LLM 직전 동의 조회 장애 | LLM 호출 0건, 정상 완료와 구분 | 실제 저장소·종료·감사·재시도 계약 |

상세 payload allowlist·정책 버전·독립 직접 입력 API·동의 조회와 철회 경합·감사 저장은 은영/가빈
확인 전 고정하지 않았다. 한솔은 화면 검토만 먼저 진행하고 확정 DTO/OpenAPI로 연결한다.

## 변경·검증 범위

이번 준비는 Proposed 문서와 합성 테스트만 변경한다. 런타임 selector, API/DTO, migration,
Frontend, 실행 환경 설정은 변경하지 않는다. 신규 RLS·DB Trigger·업무용 DB 함수는 없다.
검증은 위 focused pytest와 Ruff·테스트 inventory·문서 링크/diff 검사로 제한한다.
전체 PostgreSQL/Redis CI·실제 Provider 평가·Frontend E2E는 수행하지 않았으며 통과로 주장하지 않는다.

관련 계약: [#458 후속 계약](../contracts/proposed/ocr-llm-transfer-458.md)
