# Post-MVP-1 외부 승인·공개 게이트

| 항목 | 값 |
| --- | --- |
| 상태 | Active Approved v4 target gate — 모두 Pending |
| 구현과 공개 | 분리 추적 |
| 원본 동기화 | 2026-08-27 · SHA-256 `466d52a7d52751490a8dde705d9d504163dc83df21fe94b228ad98182f1cc8ce` |

이 표는 기술 계약을 바꾸지 않는다. 미승인 상태에서도 synthetic fixture로 비공개 구조를 구현할 수 있지만, 승인 artifact에는 대상 version, fixture 또는 문구 ID, 검토 범위, 검토자 역할, 승인 시각과 제한 조건이 있어야 한다. Privacy 정책·승인 인수는 권가빈, 기술 통제 증빙은 송은영, 동의·철회·삭제 UX 증빙은 남한솔이 담당하며 코드 리뷰는 외부 승인을 대체하지 않는다.

| ID | 적용 | 승인 대상 | 현재 상태 | 미승인 시 동작 |
| --- | --- | --- | --- | --- |
| `EXT-MED-001` | C | symptom code와 4단계 Safety 분기 | Pending | `PUBLIC_TRACK_C=false`; synthetic fixture만 허용 |
| `EXT-MED-002` | C·F | 한국어 긴급·응급·불명확 위험·fallback 문구와 CTA | Pending | 관련 문구 실제 사용자 노출 금지 |
| `EXT-PHARM-001` | F(OTC) | OTC Chat 상호작용 답변 허용 범위, CTA·문구와 실제형 질문 fixture | Pending | `PUBLIC_TRACK_F=false`; 승인 fixture만 허용 |
| `EXT-SOURCE-001` | F(공식 Identity) | MFDS 제품·성분·복합제 Component·Alias Source 이용 범위, 수집·갱신·attribution과 Catalog lifecycle | Pending | 승인·검증된 Source Snapshot을 활성화하지 않고 실제 사용자 Identity·Preflight 차단 |
| `EXT-SOURCE-002` | F(OTC) | 준비된 OTC/RAG corpus license·임상 범위·attribution·lifecycle | Pending | local/dev/closed demo retrieval만 허용 |
| `EXT-PRIV-001` | A·B·C·E·F | 보존·암호화·삭제·legal hold·키 관리, Provider 최소 allowlist와 동의·철회 차단 | Pending | production 보존 job, 미승인 Provider 전송과 공개 차단 |
| `EXT-PRIV-002` | C·F | 실제형 fixture 비식별·재식별 위험·사용 범위 | Pending | synthetic fixture만 허용 |
| `EXT-SAFETY-001` | C 및 F(OTC 포함) | 위험 회귀 suite와 공개 차단 기준 | Pending | `PUBLIC_TRACK_C=false`, `PUBLIC_TRACK_F=false` 유지 |

## Flag 해제 조건

- `PUBLIC_TRACK_C`: `EXT-MED-001`, `EXT-MED-002`, `EXT-PRIV-002`, `EXT-SAFETY-001` 승인과 해당 version 회귀 결과.
- OTC Chat은 별도 `PUBLIC_TRACK_D`를 두지 않고 `PUBLIC_TRACK_F`를 공유한다. OTC 범위에는 `EXT-PHARM-001`, `EXT-SOURCE-002`, `EXT-PRIV-002`, `EXT-SAFETY-001` 승인과 실제형 질문 fixture 검증이 필요하다.
- `PUBLIC_TRACK_F`: `EXT-MED-002`, `EXT-PHARM-001`, `EXT-SOURCE-001`, `EXT-SOURCE-002`, `EXT-PRIV-001`, `EXT-PRIV-002`, `EXT-SAFETY-001` 승인과 공식 Identity·Preflight·Rule/Evidence·Citation UI·fallback 회귀 결과.

MFDS 공식 Identity는 `EXT-SOURCE-001` 승인, immutable artifact→정규화→Source Snapshot 검증, Single Candidate Gate 회귀와 rollback 훈련이 완료된 version만 `ACTIVE`로 전환한다. HIRA 적용약가 데이터는 품목 식별 입력·정답 원장으로 사용하지 않는다. Track E 비-RAG LLM은 `EXT-PRIV-001`의 최소전송·동의·철회 증빙을 충족해야 한다.

`EXT-PRIV-001`은 A·B·C·E·F의 공통 Production gate다. 위 목록에 없는 공개 flag 해제 조건으로 추가 해석하지 않는다.

## `EXT-PRIV-001` 보존 기준

- terminal Job 90일.
- publish 완료 Outbox·quarantine·DLQ 30일.
- Idempotency 운영 기본값 7일.
- 동기 응답 snapshot은 최대 1MiB, 암호화 저장, 일반 로그 금지.
- 미발행 DLQ와 연결 quarantine은 TTL 삭제 대상에서 제외.
- 사용자 삭제, legal hold, 키 관리와 삭제 증빙을 승인 artifact에 기록.

구두 승인, 메신저 확인과 단순 체크박스는 증빙이 아니다. 조건 변경은 과거 결과를 소급 수정하지 않고 새 version과 재검증 기록으로 남긴다.
