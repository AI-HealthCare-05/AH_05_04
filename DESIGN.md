# Design

## Source of truth
- Status: Draft
- Last refreshed: 2026-09-09
- Primary product surfaces: Frontend developer Preview for Prescription Review, Guide, and Chat
- Evidence reviewed: `frontend/src/routes/AppRouter.tsx`, the three product pages, `frontend/src/design-system/`, Frontend unit tests, and PR #376 Playwright fixtures

## Brand
- Personality: Calm, clear, and trustworthy, matching the existing Dosey product UI.
- Trust signals: Persistent Preview labeling and explicit synthetic-data disclosure.
- Avoid: A production-like Preview that can be mistaken for API, database, or medical validation.

## Product goals
- Goals: Let team members inspect important Frontend states without Backend/API availability.
- Non-goals: Backend behavior, data persistence, provider behavior, or production validation.
- Success signals: Every requested scenario is directly selectable and renders through the actual product page JSX without network requests.

## Personas and jobs
- Primary personas: Product, design, Frontend, QA, and reviewers.
- User jobs: Select a screen, select a state, inspect responsive UI, and discuss acceptance criteria.
- Key contexts of use: Local development on desktop and connected mobile devices.

## Information architecture
- Primary navigation: `/dev/preview` selector toolbar.
- Core routes/screens: Prescription Review / DOC-03, Guide, and Chat.
- Content hierarchy: Preview warning, screen selector, scenario selector, actual product screen.

## Design principles
- Reuse actual product pages and their presentational markup.
- Keep synthetic fixtures behind explicit Preview dependencies.
- Tradeoffs: Small dependency-injection seams are preferred over duplicated Preview-only JSX.

## Visual language
- Color: Existing product tokens for screens; indigo developer toolbar for clear separation.
- Typography: Existing system typography.
- Spacing/layout rhythm: Existing 390px mobile layout with a compact responsive toolbar.
- Shape/radius/elevation: Existing product surfaces; modest elevation on Preview controls.
- Motion: No new motion.
- Imagery/iconography: Existing Dosey mascot and product icons only.

## Components
- Existing components to reuse: `MobileShell`, `Button`, `Card`, `StatusBadge`, `DoseyMascot`, and all three product pages.
- New/changed components: Dev Preview toolbar and typed fixture/service adapters.
- Variants and states: The scenario catalogs defined in the Preview fixture module.
- Token/component ownership: Product styling stays with product pages; Preview chrome stays under `src/dev-preview/`.

## Accessibility
- Target standard: Preserve current semantic roles and labels.
- Keyboard/focus behavior: Native labeled selects and existing product controls.
- Contrast/readability: High-contrast Preview banner and existing product styles.
- Screen-reader semantics: Canvas label includes the selected screen and scenario.
- Reduced motion and sensory considerations: No added motion or autoplay.

## Responsive behavior
- Supported breakpoints/devices: 320px and above, with the product canvas capped at 390px.
- Layout adaptations: Selectors stack on narrow screens.
- Touch/hover differences: Native select and existing touch targets.

## Interaction states
- Loading: Deterministic unresolved synthetic promise where required.
- Empty: Explicit Guide and Chat empty states.
- Error: Synthetic service errors or product validation states.
- Success: Confirmed Review, completed Guide, and available Chat composer.
- Disabled: Existing product rules remain visible.
- Offline/slow network, if applicable: Not simulated as real networking; Preview remains request-free.

## Content voice
- Tone: Existing Korean product copy plus concise English developer labels.
- Terminology: `DEV PREVIEW`, `Mock data only`, and `실제 API/DB 동작 검증용이 아님` are mandatory.
- Microcopy rules: Synthetic content must not be presented as medical advice or real user data.

## Implementation constraints
- Framework/styling system: React 19, React Router, TypeScript, Vite, and existing CSS.
- Design-token constraints: Reuse current product components; do not create a parallel component library.
- Performance constraints: Preview code must be lazy and removable from production builds.
- Compatibility constraints: `import.meta.env.DEV` is the route gate; Preview is outside authentication to prevent user lookup.
- Test/screenshot expectations: Verify route access, every scenario, zero fetch calls, production exclusion, regression tests, lint, typecheck/build, and `git diff --check`.

## Open questions
- [ ] Confirm whether the selector labels should later be localized for non-developer stakeholders / Frontend owner / low impact.
