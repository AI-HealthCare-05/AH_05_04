import { useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { NavigateFunction } from 'react-router-dom'
import ChatPage from '../pages/ChatPage'
import GuidePage from '../pages/GuidePage'
import PrescriptionReviewPage from '../pages/PrescriptionReviewPage'
import {
  createChatPreview,
  createGuidePreview,
  createPrescriptionReviewPreview,
  isPreviewScreen,
  previewScenarioIds,
  previewScreenLabels,
  resolvePreviewScenario,
  type PreviewScreen,
} from './previewFixtures'
import './DevPreviewPage.css'

const stayInPreview: NavigateFunction = (() => undefined) as NavigateFunction

function DevPreviewPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedScreen = searchParams.get('screen')
  const activeScreen: PreviewScreen = isPreviewScreen(requestedScreen)
    ? requestedScreen
    : 'prescription-review'
  const activeScenario = resolvePreviewScenario(
    activeScreen,
    searchParams.get('scenario'),
  )

  const reviewPreview = useMemo(
    () =>
      activeScreen === 'prescription-review'
        ? createPrescriptionReviewPreview(
            activeScenario as typeof previewScenarioIds['prescription-review'][number],
          )
        : null,
    [activeScenario, activeScreen],
  )
  const guidePreview = useMemo(
    () =>
      activeScreen === 'guide'
        ? createGuidePreview(
            activeScenario as typeof previewScenarioIds.guide[number],
          )
        : null,
    [activeScenario, activeScreen],
  )
  const chatPreview = useMemo(
    () =>
      activeScreen === 'chat'
        ? createChatPreview(
            activeScenario as typeof previewScenarioIds.chat[number],
          )
        : null,
    [activeScenario, activeScreen],
  )

  useEffect(() => {
    if (window.scrollY > 0) {
      window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
    }
  }, [activeScenario, activeScreen])

  const updateSelection = (screen: PreviewScreen, scenario?: string) => {
    const nextScenario = scenario ?? previewScenarioIds[screen][0]
    setSearchParams({ screen, scenario: nextScenario })
  }

  return (
    <div className="dev-preview">
      <header className="dev-preview__toolbar">
        <div className="dev-preview__title-row">
          <strong>DEV PREVIEW</strong>
          <span>Mock data only</span>
        </div>
        <p>실제 API/DB 동작 검증용이 아님</p>
        <div className="dev-preview__selectors">
          <label>
            화면
            <select
              aria-label="Preview 화면"
              value={activeScreen}
              onChange={(event) =>
                updateSelection(event.target.value as PreviewScreen)
              }
            >
              {Object.entries(previewScreenLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Scenario
            <select
              aria-label="Preview scenario"
              value={activeScenario}
              onChange={(event) =>
                updateSelection(activeScreen, event.target.value)
              }
            >
              {previewScenarioIds[activeScreen].map((scenario) => (
                <option key={scenario} value={scenario}>
                  {scenario}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <section
        className="dev-preview__canvas"
        aria-label={`${previewScreenLabels[activeScreen]} ${activeScenario} Preview`}
        key={`${activeScreen}:${activeScenario}`}
      >
        {reviewPreview && (
          <PrescriptionReviewPage
            navigation={stayInPreview}
            services={reviewPreview.services}
            previewState={reviewPreview.state}
          />
        )}
        {guidePreview && (
          <GuidePage
            navigation={stayInPreview}
            services={guidePreview.services}
            previewGuideId={guidePreview.guideId}
          />
        )}
        {chatPreview && (
          <ChatPage
            navigation={stayInPreview}
            services={chatPreview.services}
            previewState={chatPreview.state}
          />
        )}
      </section>
    </div>
  )
}

export default DevPreviewPage
