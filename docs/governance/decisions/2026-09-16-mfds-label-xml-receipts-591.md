# #591 XML Receipt 발급 계약

상태: 담당자 승인 목표, 구현 PR 리뷰 및 서버 적용 대기.
구현 담당 김지혜, 책임 리뷰어 송은영(DB·transaction·Source 증빙 및 hash 결속), 정책/서버 증빙 권가빈.

2026-09-16 김지혜가 전달한 송은영 회신으로 제품 Receipt와 Endpoint Receipt의 두 문서, A~D 및 규격안 4~7절의 필드·hash·발급 순서를 승인했다. 실제 발급에는 원본 승인 ref를 입력해야 하며 이 기록이 서명/외부 승인 검증을 대신하지 않는다.

정본: [XML Receipt 계약](../../contracts/targets/post-mvp-1/mfds-label-xml-receipts-591.md).
제품 manifest_hash는 acquisition manifest 파일 hash가 아니다. Endpoint receipt_hash와 구분하고, XML → 제품 Receipt → Endpoint Receipt → acquisition/profile 단방향으로 결속한다. 일반 API EndpointReceipt의 schema 1.1/1.2를 변경하거나 XML에 억지 적용하지 않는다.

기존 XML parser·Snapshot checksum·API source_version 계약은 유지한다. 문서별 canonical_sha256만 정본의 새 payload로 계산한다. DB migration/API/public enum 변경 없음.

제품별 Operation 등록·새 session 재조회, Receipt 검증 및 서버 설정 대조 후 READY로 전환한다. 도구는 발급·검증만 수행하며 DB/profile을 수정하지 않는다. CURRENT/RAG/Runtime/Citation/서비스 공개 승인과 분리한다.
