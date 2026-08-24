# Post-MVP-1 외부 승인·공개 게이트

| 항목 | 값 |
| --- | --- |
| 상태 | Approved target gate — 모두 Pending |
| 구현과 공개 | 분리 추적 |
| 원본 동기화 | 2026-08-24 · SHA-256 `9935518b53eb9796082f61c060fd2998504674f01cbe6338713bc0444b058877` |

이 표는 기술 계약을 바꾸지 않는다. 미승인 상태에서도 synthetic fixture로 비공개 구조를 구현할 수 있지만, 승인 artifact에는 대상 version, fixture 또는 문구 ID, 검토 범위, 검토자 역할, 승인 시각과 제한 조건이 있어야 한다.

| ID | 적용 | 승인 대상 | 현재 상태 | 미승인 시 동작 |
| --- | --- | --- | --- | --- |
| `EXT-MED-001` | C | symptom code와 4단계 Safety 분기 | Pending | `PUBLIC_TRACK_C=false`; synthetic fixture만 허용 |
| `EXT-MED-002` | C·F | 한국어 긴급·응급·불명확 위험·fallback 문구와 CTA | Pending | 관련 문구 실제 사용자 노출 금지 |
| `EXT-PHARM-001` | D | OTC severity→public outcome, CTA와 문구 | Pending | `PUBLIC_TRACK_D=false`; demo whitelist만 허용 |
| `EXT-SOURCE-001` | D | 허가정보·e약은요·DUR 이용·license·attribution·갱신 책임 | Pending | production 판정에 사용 금지 |
| `EXT-SOURCE-002` | F | RAG corpus license·임상 범위·attribution·lifecycle | Pending | local/dev/closed demo retrieval만 허용 |
| `EXT-PRIV-001` | A·B·C·D·F | 보존·암호화·삭제·legal hold·키 관리 | Pending | production 보존 job과 공개 차단 |
| `EXT-PRIV-002` | C·D·F | 실제형 fixture 비식별·재식별 위험·사용 범위 | Pending | synthetic fixture만 허용 |
| `EXT-SAFETY-001` | C·D·F | 위험 회귀 suite와 공개 차단 기준 | Pending | `PUBLIC_TRACK_C/D/F=false` 유지 |

## Flag 해제 조건

- `PUBLIC_TRACK_C`: `EXT-MED-001`, `EXT-MED-002`, `EXT-PRIV-002`, `EXT-SAFETY-001` 승인과 해당 version 회귀 결과.
- `PUBLIC_TRACK_D`: `EXT-PHARM-001`, `EXT-SOURCE-001`, `EXT-PRIV-002`, `EXT-SAFETY-001` 승인과 demo whitelist 밖 실제 사용자 fixture 검증.
- `PUBLIC_TRACK_F`: `EXT-MED-002`, `EXT-SOURCE-002`, `EXT-PRIV-001`, `EXT-PRIV-002`, `EXT-SAFETY-001` 승인과 Citation UI·fallback 회귀 결과.

`EXT-PRIV-001`은 A·B·C·D·F의 공통 Production gate다. 위 목록에 없는 `PUBLIC_TRACK_C` 또는 `PUBLIC_TRACK_D` flag 해제 조건으로 추가 해석하지 않는다.

## `EXT-PRIV-001` 보존 기준

- terminal Job 90일.
- publish 완료 Outbox·quarantine·DLQ 30일.
- Idempotency 운영 기본값 7일.
- 동기 응답 snapshot은 최대 1MiB, 암호화 저장, 일반 로그 금지.
- 미발행 DLQ와 연결 quarantine은 TTL 삭제 대상에서 제외.
- 사용자 삭제, legal hold, 키 관리와 삭제 증빙을 승인 artifact에 기록.

구두 승인, 메신저 확인과 단순 체크박스는 증빙이 아니다. 조건 변경은 과거 결과를 소급 수정하지 않고 새 version과 재검증 기록으로 남긴다.
