# #669 일정 설정의 처방 복용 지시 표시

확정 처방의 기존 `Medication.timing_text`를 약별 시간 입력 바로 위에 표시한다.
횟수로 시점을 추정하거나 식전·식후 원문을 변환하지 않는다. null/공백은 복용 시점 미확인으로 안내한다.
시간 입력은 해당 약의 지시를 aria-describedby로 참조하고 검증 오류가 있으면 두 설명을 함께 참조한다.
긴 지시는 줄바꿈하며 날짜·시간 초깃값과 저장 payload는 유지한다. 자동 추천은 #670 범위다.

공유 API/DTO/DB/상태 의미 변경은 없다. 기존 확정 처방 응답을 소비하는 Frontend 표시 변경이다.

## 검증 (2026-09-17)

- SchedulePage 및 MedicationSchedulesApi: 59개 통과.
- 설정 진입의 여러 약 연결, 수정 진입의 아침·저녁/아침·점심/긴 지시/null/공백, 빈 시간 초깃값, 기존 저장 payload를 합성 데이터로 확인.
- oxlint, TypeScript 빌드, Vite production 빌드, git diff --check 통과.
- 최초 API 테스트는 VITE_API_BASE_URL 미설정으로 실패했고, 합성 localhost 설정 후 통과했다.
- 실제 모바일 브라우저 시각 검증 및 전체 CI는 미실행. Python/DB/의료 AI 구현 변경은 없다.
- 운영 미적용. 담당 리뷰어 확정·리뷰 승인·merge queue 이후 배포한다.
