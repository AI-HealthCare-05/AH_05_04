# Track B 생활 시간 입력 v1 — 검토안

| 항목 | 내용 |
| --- | --- |
| 상태 | **Proposed · Not implemented**. 아래 URL·DTO·상한·오류·저장 정책 전부 검토 대상 |
| 범위 | 생활 시간 입력·저장·조회. 추천·약별 조건 판정·일정 확정은 제외 |
| 제품 방향 / 구현 | [#422](https://github.com/AI-HealthCare-05/AH_05_04/issues/422) / [#556](https://github.com/AI-HealthCare-05/AH_05_04/issues/556) |
| Decision | [PD-422-LIFESTYLE-20260915](../../governance/decisions/2026-09-15-track-b-lifestyle-times.md) |
| 구현 / 단일 책임 리뷰 | 권가빈 / 송은영 — Backend·DB·Security와 Frontend 소비 정합성 |
| 협의 | 남한솔 — 입력·조회 UX; 권가빈 — 제품·Privacy. 전문 의견은 추가 필수 PR 승인이 아님 |

## 1. 첫 기능의 의미

사용자가 직접 입력한 평소 생활 패턴을 저장한다. 실제 식사·행동을 관측했다는 의미가 아니다.
식사·생활 시각 누락은 약의 식사 조건 부재, 복용 가능, 복용 완료를 뜻하지 않는다.
확정 처방이 없어도 입력 가능하며 저장 자체로 처방·일정·occurrence·알림·Check-in을 변경하지 않는다.
후속 추천은 별도 계약으로 생활 정보 revision과 처방/근거 version을 확인해야 한다.

## 2. 입력 단위 제안

KST(`Asia/Seoul`)의 반복되는 일주일 패턴을 한 세트로 저장한다. 화면에서는 동일 패턴을 여러 요일에 복사할 수 있지만 서버는 ISO 요일 1(월)~7(일)의 데이터를 명시적으로 받는다. 날짜별 예외·교대근무 달력·다른 시간대 자동 변환은 v1에서 제외한다. 반복 패턴을 표현할 수 없으면 임의 시각을 만들지 않고 불규칙/미입력으로 남긴다.

PUT body의 `expected_revision`, `days`는 필수다. `days`는 최대 7개이며 같은 weekday 중복은 거부한다. 전체 교체이므로 생략한 요일은 미입력으로 초기화된다. PATCH처럼 일부만 남기는 의미가 아니다.

각 day는 아래 4개 필수 필드를 가진다.

| 필드 | 제안 형식·의미 |
| --- | --- |
| `weekday` | 엄격한 정수 1..7 |
| `meals` | 최대 3개; `kind=BREAKFAST\|LUNCH\|DINNER` 중복 불가 |
| `anchors` | 최대 4개; 반복 행동의 완료/발생 시각. 자유 서술 대신 아래 종류 사용 |
| `unavailable_windows` | 최대 8개; 복용이 어려운 시간 구간. 구체적인 업무·이동 이유는 수집하지 않음 |

모든 배열은 빈 배열을 허용한다. 빠진 meal kind는 미입력이다. 명시적 meal은 `kind`, `pattern`, `window`를 모두 보낸다.

- `pattern=REGULAR`: `window` 필수 객체. 사용자 진술의 평소 식사 가능 시간 구간이며 실제 섭취 시각·식사 전체 지속 시간으로 해석하지 않는다.
- `pattern=IRREGULAR`: `window=null`. 식사는 하지만 안정적인 시간대를 입력할 수 없음.
- `pattern=NOT_USUALLY_EATEN`: `window=null`. 평소 해당 식사를 하지 않는다는 자기 보고. 공복 보장으로 해석하지 않는다.
- `window` 객체: `start_local_time`, `end_local_time`, `end_day_offset` 모두 필수. 시간은 엄격한 24시간 `HH:mm`, offset은 0 또는 1. 시작은 해당 weekday, 끝은 offset만큼 뒤의 날짜다. 길이는 0보다 크고 24시간 미만이다. 단일 시각을 임의로 시간 구간으로 확장하지 않는다.
- `anchors` 항목: `kind=WAKE_UP|LEAVE_HOME|RETURN_HOME|BEDTIME`, `local_time` 모두 필수. day 안에서 같은 kind 중복 불가. 식사처럼 구간이 아닌 사용자가 선택한 대표 시각이다. 실제로 일정하지 않으면 해당 항목을 생략한다. 사용자 설명의 모든 반복 행동을 이 목록이 포괄하지 않으므로 종류 확장은 Frontend 협의 대상이다.
- `unavailable_windows` 항목은 window 객체와 동일하다. 사용자 회피 선호이며 의료상 금기나 약의 복용 가능 판정이 아니다.

구간이 서로 겹치더라도 현실의 입력일 수 있어 거부하지 않는다(예: 점심과 회의가 겹침). 완전히 같은 unavailable 구간은 중복 오류로 거부한다. 일요일의 다음 날은 월요일로 이어지며 표시 시 자정 경계를 명시한다. 추천 가능 여부는 저장 API가 판단하지 않는다.

모든 객체의 알려지지 않은 필드, null 배열, 잘못된 enum·bool형 정수·24:00·동일/역전된 구간은 거부한다. 위 배열 상한은 의료 기준이 아닌 검토용 입력 상한이다.

## 3. HTTP 제안

Bearer 인증된 본인 SELF만 대상으로 하는 `GET /api/v1/lifestyle-times`, `PUT /api/v1/lifestyle-times`를 제안한다. user_id/profile_id를 요청에서 받지 않는다.

GET은 200 `data`에 `revision`, `updated_at`, `timezone`, `days`를 반환한다. 미저장은 `revision=0`, `updated_at=null`, `timezone=Asia/Seoul`, `days=[]`다. 저장된 빈 패턴은 revision>0, updated_at이 있어 미저장과 구분된다. 이는 추천 준비 완료 상태를 의미하지 않는다.

PUT은 엄격한 0 이상 정수 `expected_revision`과 전체 `days`를 받으며 200으로 GET과 같은 shape를 반환한다. timezone은 요청 필드가 아니고 v1 응답 상수다. 새로 접수된 PUT이 성공할 때마다 revision을 1 증가시킨다(같은 내용·새 키도 포함). 같은 멱등 키의 성공 replay는 증가하지 않는다. GET은 서버 저장 상태만 반환하며 입력값을 채워 넣지 않는다.

정렬은 weekday ASC, meals는 BREAKFAST/LUNCH/DINNER, anchors는 kind 문자열 ASC, unavailable은 시작 시각·offset·끝 시각 순이다. 순서만 다른 입력은 같은 정규화 payload로 취급한다.

합성 PUT 예시(정규화 전 입력 순서 허용):

```json
{
  "expected_revision": 0,
  "days": [{
    "weekday": 1,
    "meals": [
      {"kind": "BREAKFAST", "pattern": "REGULAR", "window": {"start_local_time": "07:30", "end_local_time": "08:00", "end_day_offset": 0}},
      {"kind": "LUNCH", "pattern": "IRREGULAR", "window": null},
      {"kind": "DINNER", "pattern": "NOT_USUALLY_EATEN", "window": null}
    ],
    "anchors": [{"kind": "LEAVE_HOME", "local_time": "08:30"}],
    "unavailable_windows": [{"start_local_time": "09:00", "end_local_time": "12:00", "end_day_offset": 0}]
  }]
}
```

이 예시는 월요일만 입력하며 전체 교체이므로 다른 요일은 미입력으로 남는다.

## 4. 저장·경합·실패 제안

이 절 역시 미승인 제안이며 구현 전에 책임 리뷰로 확정한다.

- SELF profile별 현재 데이터 1행을 저장하는 `lifestyle_times` 테이블 제안: `profile_id` PK/FK, `revision`, `days` JSONB, `updated_at`. 별도 과거 생활 이력/audit 테이블은 추가하지 않는다. Python에서 shape를 검증하고 일반 제약으로 revision을 보호한다.
- 동시 최초 생성도 직렬화하도록 대상 SELF profile 행을 잠근다. 소유권 확인 후 기존 SYNC_MUTATION 성공 snapshot replay → profile 잠금 → expected revision 검증 → 전체 교체·revision 증가 → 암호화 snapshot 저장을 단일 transaction으로 처리하는 안이다. 실제 공통 코드의 잠금 순서와 정합성은 구현 전 확인한다.
- PUT은 기존 형식의 Idempotency-Key 필수. scope는 SELF profile을 부모로 하는 생활 시간 PUT으로 검토한다. TTL·암호화·용량 제한은 기존 [멱등성 계약](../targets/post-mvp-1/idempotency-v1.md)을 재사용하고 새 값을 만들지 않는다.
- 초기화는 `days=[]` PUT이다. 이전 payload를 현재 행에 남기지 않는다. 기존 암호화 멱등 snapshot에는 공통 보존 기간 동안 남을 수 있으므로 즉시 완전 삭제로 안내하지 않는다.
- 401은 기존 인증, SELF 확인 실패는 고정 404(제안 코드 `LIFESTYLE_TIMES_NOT_FOUND`), stale revision은 409(제안 코드 `LIFESTYLE_TIMES_REVISION_CONFLICT`). 입력은 기존 `422 VALIDATION_FAILED`, 키 오류·snapshot 용량 초과는 공통 계약을 따른다. error details에 생활 시간이나 입력 원문을 반환하지 않는다.
- 모든 응답은 no-store·공통 trace/error envelope. Runtime은 필요한 최소 DML, Source Writer 등은 접근 금지. 기존 계정 삭제·권한 적용 경로와의 연결을 구현 전에 확인한다.

## 5. 리뷰 결정 사항과 검증

- 남한솔: 식사/불규칙/생략의 표현, 대표 행동 4종의 충분성, 요일 복사·전체 교체 UX, 자정·겹침 표시와 상한.
- 송은영: 새 route·오류·JSONB 저장과 profile 잠금, 기존 멱등성 scope/순서 정합성, Runtime 권한 및 삭제 흐름.
- 권가빈: 최소 입력의 제품 적합성, 보존·초기화 안내 및 필요한 동의 경계. 목적별 동의 enum을 임의 추가하지 않는다.
- 정현우 후속 협의: 식사 범위만으로 식전·식후 기준 시각을 확정할 수 없는 경우와 anchor 변동 처리. 본 저장 계약으로 약별 추천 규칙을 승인하지 않는다.

구현 검증은 미저장 GET, 최초/수정/빈 패턴 PUT, 누락·불규칙·식사 안 함 구분, 요일 복사 결과, 일요일 자정, 겹침 보존·정확한 중복 거부, 잘못된 형식·상한, 두 동시 최초/수정 요청, replay/키 충돌, snapshot 실패 rollback, 타 사용자 분리·no-store·비로그, OpenAPI 일치, 기존 일정·알림·Check-in 불변을 실제 HTTP/PostgreSQL 합성 테스트로 확인한다. 이번 문서 PR에서 실행한 테스트가 아니다.
