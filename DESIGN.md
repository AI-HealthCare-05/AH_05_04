# Design

## Source of truth

- Status: Active
- Last refreshed: 2026-09-15
- Primary product surfaces: Plan B medication Report (`/report`), compact clinic view (`/report/clinic`), and the existing Frontend developer Preview (`/dev/preview`).
- Evidence reviewed: Figma Report section `1538:77`, clinic read-only source `1538:82` (`REPORT-02`), Loading `1958:2`, Empty / zero denominator `1960:119`, Retry / Error `1960:275`; `backend/app/apis/v1/medication_report_routers.py`; `backend/app/dtos/medication_reports.py`; `backend/app/services/medication_reports.py`; `backend/app/tests/medication_reports/test_medication_report_api.py`; `frontend/src/routes/AppRouter.tsx`; `frontend/src/pages/MenuPage.tsx`; `frontend/src/components/StatusPanel.tsx`; `frontend/src/design-system/`; and the existing Preview contract.
- Contract authority: Backend owns Report aggregation. Frontend displays the response and must not independently aggregate, recalculate, patch, or generate report values.
- Figma authority: The listed Report nodes govern the visual hierarchy and states. Clinic source `1538:82` exists inside section `1538:77` and is a read-only `REPORT-02` reference. Implement only the parts backed by the current Backend DTO; omit its barrier, symptom, and not-taken-time content because those fields are absent from the contract.

## Brand

- Personality: Calm, clear, objective, and trustworthy, matching the existing Dosey product UI.
- Trust signals: Explicit period and date context, visible numerator/denominator, distinct status labels, honest unavailable/empty states, and consistent values between Report and clinic views.
- Avoid: Medical interpretation, diagnosis, causality, adherence judgment, fabricated trends, AI-generated numbers or explanations, and production-like Preview content that could be mistaken for API, database, or medical validation.

## Product goals

- Goals: Let users review the same server-owned 7-day or 30-day medication record summary in a standard Report and a compact view suitable for showing during a clinic visit.
- Non-goals: A separate clinic aggregation pipeline; Frontend-owned calculations; barriers, symptoms, not-taken-time details, diagnoses, or medical recommendations absent from the DTO; PDF/export; clinician transmission; LLM-generated content; Backend/API/DTO/enum/database changes.
- Success signals: Period switching loads the selected server report; `TAKEN`, `NOT_TAKEN`, and `UNCONFIRMED` remain separate; both rates show Backend percentage and numerator/denominator unchanged; zero denominators never appear as `0%`; and both views show identical values for the same response.

## Personas and jobs

- Primary personas: A medication user reviewing recent records, a user showing an objective summary during a consultation, and product/design/Frontend/QA reviewers using the developer Preview.
- User jobs: Choose 7 or 30 days, distinguish recorded states, understand the two server-provided rates, correct an unconfirmed record through the existing flow, retry a failed request, and present a concise factual summary.
- Key contexts of use: One-handed mobile use, a time-limited clinic conversation, narrow screens, slow or failed networks, and local Preview review with synthetic data.

## Information architecture

- Primary navigation: Enable the existing `복약 리포트` menu entry and route authenticated users to `/report`.
- Core routes/screens: `/report` for the standard Report; `/report/clinic` for the compact clinic view; `/schedule/unconfirmed` and the existing Check-in correction path for record completion/correction; `/dev/preview` for development-only state inspection.
- Content hierarchy: Period selector → covered date range/as-of context → state counts → adherence rate → confirmation rate → objective record detail available from `records` → correction and clinic-view actions.
- Clinic hierarchy: Follow Figma `1538:82` for the read-only `REPORT-02` structure, but include only `최근 기록을 진료 시 보여줄 수 있는 요약` → selected period → the same three primary counts and two rates → objective record detail available from the same response. Omit barrier, symptom, and not-taken-time sections until the Backend DTO provides them; do not add clinical conclusions.
- Refresh boundary: After returning from unconfirmed completion or Check-in correction, request the Report from the server again. Do not retain stale values or patch local counts.

## Design principles

- Server response is the display contract: render `counts`, `adherence_rate`, `confirmation_rate`, dates, and `records` without deriving a competing source of truth. Report `records` use Backend-provided `medication_name`, nullable `strength_text`, and `time_slot`; do not display internal IDs as user-facing labels or recalculate meal slots from `scheduled_at`.
- Keep `TAKEN` (`복용`), `NOT_TAKEN` (`미복용`), and `UNCONFIRMED` (`미확인`) distinct in labels, numbers, structure, and assistive text. Never merge `UNCONFIRMED` into `NOT_TAKEN` or label both as `미이행`.
- The two rate meanings remain visible through their Backend-provided numerator and denominator: `adherence_rate` is confirmed-record adherence; `confirmation_rate` is record confirmation.
- Reuse one fetched response across the standard Report and clinic presentation. A route change or layout change must not create a second aggregation path.
- Tradeoffs: Because the current DTO has no trend series, barriers, symptoms, not-taken-time field, or medical interpretation, omit those surfaces even where they appear in a Figma reference. Do not reconstruct a date trend from `records` or fill contract gaps with invented data.

## Visual language

- Color: Reuse existing product tokens. Status colors may support recognition but never carry status meaning alone.
- Typography: Reuse the existing system typography; prioritize readable rate values, count labels, and numerator/denominator pairs.
- Spacing/layout rhythm: Follow the existing mobile card rhythm and Report Figma nodes; allow numerical content to wrap or reflow without clipping.
- Shape/radius/elevation: Reuse existing `Card`, control, and surface treatments; do not introduce a clinic-only visual system.
- Motion: No new motion. Loading feedback must remain understandable without animation.
- Imagery/iconography: Reuse existing Dosey icons only. Charts or trend illustrations must not imply data the Backend does not provide.

## Components

- Existing components to reuse: `MobileShell`, `Button`, `Card`, `StatusBadge`, `StatusPanel`, existing navigation, and existing Check-in/unconfirmed routes.
- New/changed components: 7/30-day period selector; labeled status-count group; reusable rate card; Report layout; clinic layout that receives the same response; objective records presentation when needed.
- Variants and states: 7-day, 30-day, loading, populated, no records, zero denominator, retryable request error, authentication failure, non-exposure/not-found, network/5xx unavailable, and clinic view.
- Token/component ownership: Product components and tokens remain under the existing design system. Report-specific composition may live with the Report page; clinic view must reuse its data-display components rather than fork them.
- Data boundary: The API provides `period_days`, `start_date`, `end_date`, `timezone`, `as_of`, `counts` (`taken_count`, `not_taken_count`, `unconfirmed_count`, `pending_count`, `cancelled_count`), `overdue_pending_count`, `adherence_rate`, `confirmation_rate`, and `records`. Each record includes user-facing medication snapshot fields and a Backend-provided `time_slot`. The required primary UI counts are `TAKEN`, `NOT_TAKEN`, and `UNCONFIRMED`; do not promote auxiliary fields into a new metric without a separate approved design.

## Accessibility

- Target standard: WCAG 2.1 AA behavior for the Report and clinic flows, consistent with existing semantic components.
- Keyboard/focus behavior: Every interactive element works by keyboard and has a visible `:focus-visible` state. The period selector exposes an accessible group name, each 7/30 option exposes its selected state, and focus remains predictable after navigation and retry.
- Contrast/readability: Text and controls meet contrast requirements. Statuses include a visible label and count; do not rely on color, bar height, or icon shape alone.
- Screen-reader semantics: Read period and date context before metrics; read each metric label, percentage when available, numerator, and denominator as one understandable group. Loading and non-error updates use status semantics; errors use alert semantics without repeated announcements.
- Reduced motion and sensory considerations: Respect reduced-motion preferences, avoid auto-advancing content, and provide text equivalents for any visual data presentation.

## Responsive behavior

- Supported breakpoints/devices: Required verification widths are 320px, 390px, and 412px. The existing design token `minWidth: 360px` is not permission for Report content to overflow at 320px.
- Layout adaptations: Keep the 7/30 selector operable; stack or wrap count/rate cards as needed; allow long numerator/denominator values to reflow; constrain any records visualization to the content width; preserve empty/error actions and the complete clinic summary.
- Touch/hover differences: Use touch targets suitable for mobile and never depend on hover to expose metric definitions or actions.

## Interaction states

- Loading: Follow Figma `1958:2`; announce progress and do not display previous-period numbers as though they belong to the requested period.
- Empty: Follow Figma `1960:119`. When no records are available, explain that there are no records for the selected period and provide only supported next actions.
- Zero denominator: When a Backend rate has `denominator: 0` and `percentage: null`, display `계산할 기록 없음` and the returned numerator/denominator context. Never display `0%` for that rate.
- Error: Follow Figma `1960:275`. Network and 5xx failures are retryable/unavailable; retry issues the same server request again. Authentication failure follows the existing protected-route/session behavior. A 404/non-exposure response must not reveal inaccessible resource details.
- Success: Display the selected period, exact server-returned counts, both rate values, and their numerator/denominator. The clinic view must match the standard Report for the same response.
- Disabled: Disable or mark a period control busy only while necessary to prevent ambiguous duplicate actions; preserve its accessible state and label.
- Offline/slow network, if applicable: Use the same neutral unavailable/loading patterns and never replace missing server data with cached-looking synthetic values.

## Content voice

- Tone: Neutral, factual, concise, and non-judgmental.
- Terminology: Use `복용`, `미복용`, `미확인`, `확인된 기록 중 복용률`, `기록 확인률`, `7일`, `30일`, and `계산할 기록 없음` consistently.
- Microcopy rules: Do not call `UNCONFIRMED` a missed dose. Do not infer why or when a dose was missed, claim a symptom or barrier, diagnose adherence, or recommend treatment. The clinic title may describe showing recent records, not sending them or interpreting them.
- Preview terminology: `DEV PREVIEW`, `Mock data only`, and `실제 API/DB 동작 검증용이 아님` remain mandatory for synthetic Preview states.

## Implementation constraints

- Framework/styling system: React 19, React Router, TypeScript, Vite, and existing CSS/design-system components.
- API contract: Use authenticated `GET /api/v1/medication-reports`. `period_days` is required and accepts only `7` or `30`; `end_date` is optional. There is no separate clinic endpoint.
- Calculation contract: Render Backend `adherence_rate` and `confirmation_rate` values directly. Frontend must not calculate percentages, numerators, denominators, counts, or date buckets as the report source of truth.
- Trend constraint: The DTO contains no trend field. Do not aggregate `records` into daily/weekly trend values. Until Backend adds an approved field, omit the trend rather than synthesize it.
- Design-token constraints: Reuse current tokens and components; do not create a parallel Report or clinic component library.
- Performance constraints: Avoid duplicate fetches caused by separate presentation pipelines. Period changes and post-correction returns must fetch fresh server data; ordinary layout changes must not trigger a new aggregation implementation.
- Compatibility constraints: Use protected routes and existing auth/error conventions. Preserve `/schedule/unconfirmed` and Schedule/Check-in ownership; do not modify those flows beyond the minimal navigation/refetch integration.
- Preview constraints: Existing developer Preview stays development-only, request-free, explicitly synthetic, outside authentication, lazy-loaded, and removable from production builds.
- Test/screenshot expectations: Verify 7/30 requests, exact counts and rates, numerator/denominator, zero denominators, standard/clinic equality, post-correction refetch, loading/empty/401/404/network/5xx/retry, keyboard and screen-reader semantics, visible focus, and 320/390/412px overflow. Preserve the Preview route/scenario/zero-fetch/production-exclusion checks.

## Open questions

- [ ] Should a future Backend contract add an explicit trend series? / Backend + Product / Until approved, no trend is shown and Frontend must not derive one from `records`.
- [ ] Should a future Backend contract add the barrier, symptom, or not-taken-time fields shown in clinic Figma source `1538:82`? / Backend + Product / Until approved, omit those sections rather than infer values.
- [ ] Should auxiliary `pending_count`, `cancelled_count`, and `overdue_pending_count` appear in Report UI? / Product + Design / They remain available in the response but are not promoted into the three required primary status counts without approval.
- [ ] Confirm whether developer Preview selector labels should later be localized for non-developer stakeholders. / Frontend owner / Low impact.
