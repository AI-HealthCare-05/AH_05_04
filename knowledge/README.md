# 의료 지식 소스

Post-MVP RAG에서 사용할 승인 의료·의약품 지식의 메타데이터 준비 영역입니다. 현재 manifest, 원문 수집, 인덱스와 검색 실행 경로는 구현되어 있지 않습니다. DB의 `knowledge_document`와 `knowledge_chunk` 모델은 schema-only 골격이며 이 디렉터리와 자동으로 동기화되지 않습니다.

RAG를 구현할 때 `manifests/`에는 다음 항목을 기록하는 소스 manifest를 둡니다.

- source_id, 제목과 공식 URL
- 발행 기관과 라이선스·이용 조건
- 버전·개정일·조회일
- 포함 범위와 제외 범위
- 인덱싱 버전과 검증 상태

실제 문서, 임베딩, Vector DB와 캐시는 승인된 외부 스토리지에서 관리합니다.
