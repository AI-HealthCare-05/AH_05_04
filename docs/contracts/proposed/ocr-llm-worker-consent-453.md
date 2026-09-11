# #453 OCR LLM Worker 동의·전송 계약 개정안

상태: Proposed — #453 구현·리뷰용. 담당 리뷰 승인 및 실제 동의 저장소 연결 전.
구현: 김지혜. 담당 기술 리뷰: 송은영.
관련: #207/PD-207, #258/#268, #453.

## 목적과 변경 이유

기존 OCR 목적 안내는 CLOVA 호출만 설명한다. #453은 동일 OCR Job에 OpenAI
비-RAG 구조화를 추가하므로 기존 동의를 LLM 전송 동의로 임의 해석하지 않는다.
별도 OCR 세부 동의 목적은 만들지 않고 OCR 목적의 안내·policy version을 갱신한다.
기존 CLOVA 전용 policy version과 새 버전을 자동 호환 처리하지 않는다.

안내 제안: 처방전 인식에는 CLOVA OCR이, 인식 결과를 약명·복용 항목으로
정리하는 데에는 OpenAI가 사용됩니다. 결과는 직접 확인·수정한 뒤 확정합니다.

정확한 policy version 값과 목적별 동의 저장소의 정본은 #207 담당자와 정렬한다.
이 문서나 feature flag가 실제 동의 row를 대신하지 않는다.

## 호출 전 검사

Backend 접수 전, Worker의 CLOVA 호출 직전, CLOVA 응답 후 OpenAI 호출 직전에
현재 OCR 동의를 확인한다. WorkerMessage에 사용자 ID나 동의 snapshot을 싣지 않는다.
`ocr_job.document_id → medical_document.uploaded_by`로 같은 Job의 소유자를 확인한다.

- GRANTED 및 현재 OCR policy version 일치일 때만 허용한다.
- row 없음, 철회, 버전 불일치, 비활성 계정, 소유권 불일치, 조회 실패는 차단한다.
- CLOVA와 OpenAI 사이 철회 시 이미 완료된 CLOVA 호출을 취소했다고 주장하지 않는다.
  이후 OpenAI 호출은 0건이고 성공 OCR 결과를 저장하지 않는다.
- 재시도마다 다시 검사하며 과거 동의 판정값을 재사용하지 않는다.
- 철회와 조회 실패의 종결·저장 사유는 #207의 STALE/BLOCKED 및 OCR FAILED 경계와 정렬한다.
  공통 FailureCode를 임의 추가하지 않는다.
- 진행 중 외부 요청 강제 회수와 정교한 동시 철회 경합 제어는 #207 기존 제외 범위를 유지한다.

## 전송 범위

원본 이미지는 CLOVA에만 전달한다. OpenAI에는 구조화용 OCR token JSON만 전달하며,
Retrieval·RAG·외부 의약품 검색을 호출하지 않는다. 내부 사용자·문서·Job 식별자,
인증정보, 이미지 bytes·URL·storage key를 OCR 입력 JSON에 추가하지 않는다.
응답은 store=False이고 모델 응답의 근거 source_ids를 기존 validator로 검사한다.

주의: JSON 필드 allowlist만으로 token.text 내부의 환자명·생년월일 제거가 증명되지 않는다.
전체 OCR token을 사용하는 현재 기능과 목표의 의미상 입력 최소화를 구분한다.
규칙으로 이미 인식한 약물만 입력으로 제한하면 기존 LLM의 누락 약물 복원 기능이
축소될 수 있으므로 이를 단순 비퇴행 이관으로 처리하지 않는다.

사용자와 확인한 구현 범위는 기존 검수·약물 추가·수정 화면 유지이며, 약품 영역 선택 UI는 추가하지 않는다.
현재 이관은 Local 합성 검증에서 기존 전체 토큰 구조화를 유지한다. 실제 데이터용 의미상 입력
최소화 방식과 동의 저장소 연결은 미완료이며, 이 상태를 목표 계약 전체 충족으로 해석하지 않는다.
실제 환자 입력과 Production 공개는 이 구현의 승인 범위에 포함하지 않는다.

## 증빙과 완료 판정

합성 Provider 단위·통합 테스트와 실제 Provider 실행 증빙을 구분한다.
OCR 결과의 실제 model_version/prompt_version, 동일 trace의
PRESCRIPTION_RECOGNITION 및 OCR_STRUCTURING 호출 기록으로 연결을 확인한다.
GUIDE_GENERATION 로그를 OCR 구조화 증빙으로 사용하지 않는다.

동의 판정 fixture에는 CLOVA 이후 철회 및 정책 버전 변경을 추가한다.
금지 field·의료 원문·credential의 로그/Stream/DLQ 비노출을 검사한다.
동의 저장소가 미연결이면 외부 호출 활성화 완료로 표시하지 않는다.
