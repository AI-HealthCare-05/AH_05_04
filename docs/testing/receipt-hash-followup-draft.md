# 후속 Issue 초안: Source Receipt hash 불일치의 복구 안내 개선

담당 제안: 김지혜 (@Jye-rookie). 원격 Issue는 아직 생성하지 않았다.
근거: PR #436 review 5169535518의 WATCH. 이번 병합 차단 항목이 아니다.

Source target 문서를 수정하면 local_target.sha256, Receipt canonical hash,
traceability 문서 hash를 함께 갱신해야 하지만 현재 CI 오류는 복구 절차를 안내하지 않는다.

완료 조건:

- 불일치 검사에서 어떤 문서/해시가 달라졌는지와 저장소 도구를 이용한 복구 절차를 안내한다.
- `scripts/verify_rag_01_receipt.py <receipt> --write`는 canonical hash만 갱신하므로,
  local_target의 실제 byte hash와 traceability 갱신까지 구분해 안내한다.
- 원문 문서 무결성·승인 검사는 유지하고, 내용 검토 없이 CI에서 해시를 자동 승인하지 않는다.
- 의도적 불일치 fixture로 안내와 검출을 검증한다.
