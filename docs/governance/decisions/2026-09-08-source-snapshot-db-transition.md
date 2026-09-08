# Source Snapshot DB-owned 상태 전이 (#165 / #323)

상태: 현우님 변경 요청에 따른 구현·재리뷰 대상. 실제 Runtime 활성화 승인이 아니다.

근거: https://github.com/AI-HealthCare-05/AH_05_04/pull/323#pullrequestreview-5137532884

구현 담당 김지혜, Source 계약 검토 정현우, DB·보안 경계 검토 송은영.

일반 Runtime UPDATE가 service publication 검사와 immutable selection provenance를 우회하는 문제를 해결한다. 비소유자 역할의 상태·timestamp 변경과 non-PENDING INSERT를 trigger로 거부하고, 고정 search_path를 가진 migration-owner SECURITY DEFINER 함수에서 허용 전이·거부 Snapshot 승인·선택 이력 append를 원자적으로 강제한다. Runtime은 owner/superuser 또는 owner 역할의 멤버로 운영하지 않는다. owner/admin 직접 수정은 관리 권한 경계이며 Runtime 경로가 아니다.

기존 외부 Source 승인 전체나 CURRENT 논리 의미를 새로 정의하지 않는다. 실제 승인 인증, #335 정책, #164 normalization/provenance 정렬, Runtime 활성화는 기존 후속 범위를 유지한다. 구체 함수 계약과 실패·rollback 의미는 Source Target의 DB-owned 경계 절을 따른다.

#324가 먼저 병합됐으므로 `165a4b3c2d1e`의 부모를 `169a1b2c3d4e`로 연결하고 신규 보호 revision은 Source chain 끝에 추가한다. #329에는 동일 커밋을 병합해 migration을 중복 생성하지 않는다. 기존 Source revision을 이미 적용한 개발 DB는 별도 재생성 또는 명시적인 이행 검증이 필요하며, 과거 revision 파일 변경만으로 기존 DB가 새 부모를 실행했다고 간주하지 않는다.
