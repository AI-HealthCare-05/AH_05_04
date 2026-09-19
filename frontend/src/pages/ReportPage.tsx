import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  getMedicationReport,
  type ClinicBarrierEntry,
  type MedicationReportData,
  type MedicationReportPeriod,
  type MedicationReportRate,
} from '../api/medicationReports'
import type { BarrierCode } from '../api/trackC'
import StatusPanel from '../components/StatusPanel'
import { MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
import {
  BARRIER_ORDER,
  BARRIER_SHORT_LABELS,
  SUBREASON_LABELS,
} from '../features/trackC/labels'
import '../design-system/prototype.css'
import './MvpPages.css'
import './ReportPage.css'

type LoadFailure = {
  kind: 'authentication' | 'not-found' | 'retryable' | 'final'
  message: string
}

const PERIODS: readonly MedicationReportPeriod[] = [7, 30]

const RECORD_STATUS_LABELS: Record<string, string> = {
  TAKEN: '복용',
  NOT_TAKEN: '미복용',
  UNCONFIRMED: '미확인',
  PENDING: '예정',
  CANCELLED: '취소',
  CLOSED: '확인 완료',
}

function getLoadFailure(error: unknown): LoadFailure {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return {
        kind: 'authentication',
        message: '로그인 정보를 다시 확인해 주세요.',
      }
    }

    if (error.status === 404) {
      return {
        kind: 'not-found',
        message: '이 계정의 복약 리포트를 확인할 수 없어요.',
      }
    }

    if (error.status >= 500) {
      return {
        kind: 'retryable',
        message: '리포트 서비스에 잠시 연결할 수 없어요.',
      }
    }

    return {
      kind: 'final',
      message: '리포트 요청을 확인하지 못했어요.',
    }
  }

  if (error instanceof TypeError) {
    return {
      kind: 'retryable',
      message: '네트워크 연결을 확인한 뒤 다시 시도해 주세요.',
    }
  }

  return {
    kind: 'final',
    message: '리포트 응답을 확인하지 못했어요.',
  }
}

function AdherenceRateCard({ rate }: { rate: MedicationReportRate }) {
  const hasDenominator = rate.denominator > 0 && rate.percentage !== null

  return (
    <article
      className="report-rate-card"
      aria-label="확인된 기록 중 복용률"
    >
      <h3>확인된 기록 중 복용률</h3>

      {hasDenominator ? (
        <strong className="report-rate-card__value">
          <span className="sr-only">분자 </span>
          {rate.numerator}
          <span aria-hidden="true"> / </span>
          <span className="sr-only">분모 </span>
          {rate.denominator}회
          <span aria-hidden="true"> · </span>
          {rate.percentage}%
        </strong>
      ) : (
        <strong className="report-rate-card__empty">
          계산할 기록 없음
        </strong>
      )}

      <p className="report-rate-card__basis">
        복용 {rate.numerator}회 ÷ 확인된 기록 {rate.denominator}회
      </p>
    </article>
  )
}
function countBarriers(
  entries: ClinicBarrierEntry[],
): { barrier_code: BarrierCode; count: number }[] {
  const tally = new Map<BarrierCode, number>()

  for (const entry of entries) {
    tally.set(entry.barrier_code, (tally.get(entry.barrier_code) ?? 0) + 1)
  }

  // Fixed check-in order, so the same reason sits in the same place every visit.
  return BARRIER_ORDER.filter((code) => tally.has(code)).map((code) => ({
    barrier_code: code,
    count: tally.get(code) ?? 0,
  }))
}

function ReportSummary({
  report,
  clinic,
}: {
  report: MedicationReportData
  clinic: boolean
}) {
  const counts = report.counts
  const hasAdherenceRate =
    report.adherence_rate.denominator > 0 &&
    report.adherence_rate.percentage !== null

  const countsBlock = (
    <>
      {clinic && (
        <h2
          id="report-counts-title"
          className="report-block-title"
        >
          확인된 기록
        </h2>
      )}

      <section
        className="report-section report-counts-section"
        aria-labelledby="report-counts-title"
      >
        {!clinic && (
          <h2 id="report-counts-title" className="sr-only">
            복약 상태 요약
          </h2>
        )}

        <ul
          className={`report-counts report-counts--compact ${
            clinic ? 'report-counts--clinic' : ''
          }`.trim()}
        >
          <li className="report-count report-count--taken">
            <span
              className="report-count__icon"
              aria-hidden="true"
            >
              ✓
            </span>
            <span className="report-count__name">복용</span>
            <b className="report-count__value">
              {counts.taken_count}회
            </b>
          </li>

          <li className="report-count report-count--not-taken">
            <span
              className="report-count__icon"
              aria-hidden="true"
            >
              −
            </span>
            <span className="report-count__name">미복용</span>
            <b className="report-count__value">
              {counts.not_taken_count}회
            </b>
          </li>

          <li className="report-count report-count--unconfirmed">
            <span
              className="report-count__icon"
              aria-hidden="true"
            >
              ?
            </span>
            <span className="report-count__name">미확인</span>
            <b className="report-count__value">
              {counts.unconfirmed_count}회
            </b>
          </li>
        </ul>
      </section>
    </>
  )

  return (
    <>
      {clinic && (
        <section
          className="report-clinic-note"
          aria-labelledby="clinic-note-title"
        >
          <h2 id="clinic-note-title">
            사용자가 직접 기록한 내용을 정리한 화면입니다.
          </h2>
          <p>진단이나 의료진 분석 결과가 아닙니다.</p>
        </section>
      )}

      {clinic ? (
        <section
          className="report-clinic-summary"
          aria-labelledby="report-clinic-summary-title"
        >
          <h2 id="report-clinic-summary-title">핵심 요약</h2>

          {hasAdherenceRate ? (
            <strong className="report-clinic-summary__rate">
              복용률 {report.adherence_rate.percentage}% (
              {report.adherence_rate.numerator} /{' '}
              {report.adherence_rate.denominator}회)
            </strong>
          ) : (
            <strong className="report-clinic-summary__empty">
              복용률 계산할 기록 없음
            </strong>
          )}
          <p className="report-clinic-summary__sub">
            미복용 {counts.not_taken_count}회
          </p>
        </section>
      ) : (
        <section
          className="report-rate-section"
          aria-labelledby="report-rates-title"
        >
          <h2 id="report-rates-title">복용률</h2>

          <div className="report-rates">
            <AdherenceRateCard rate={report.adherence_rate} />
          </div>
        </section>
      )}

      {clinic && countsBlock}

      {clinic && (
        <section
          className="report-clinic-period"
          aria-labelledby="report-clinic-period-title"
        >
          <h2 id="report-clinic-period-title" className="sr-only">
            조회 기간
          </h2>

          <div>
            <span>기간</span>
            <strong>
              최근 {report.period_days}일 · {report.start_date} ~{' '}
              {report.end_date}
            </strong>
          </div>
        </section>
      )}

      {clinic && report.clinic && (
        <section
          className="report-clinic-barriers"
          aria-labelledby="report-clinic-barriers-title"
        >
          <h2 id="report-clinic-barriers-title">미복용 사유</h2>

          {report.clinic.barriers.length > 0 ? (
            <>
              <ul className="report-clinic-barriers__tally">
                {countBarriers(report.clinic.barriers).map((entry) => (
                  <li key={entry.barrier_code}>
                    <strong>
                      {BARRIER_SHORT_LABELS[entry.barrier_code]}
                    </strong>
                    <b>{entry.count}회</b>
                  </li>
                ))}
              </ul>

              <ul className="report-clinic-barriers__detail">
                {report.clinic.barriers.map((entry) => (
                  <li key={entry.occurrence_id}>
                    <time dateTime={entry.scheduled_local_date}>
                      {entry.scheduled_local_date}
                    </time>

                    <span>{entry.medication_name}</span>

                    <span>
                      {entry.subreason_code
                        ? SUBREASON_LABELS[entry.subreason_code]
                        : BARRIER_SHORT_LABELS[entry.barrier_code]}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p>기간 내 기록된 미복용 사유가 없습니다.</p>
          )}
        </section>
      )}

      {clinic &&
        report.clinic &&
        report.clinic.consultation_questions.length > 0 && (
          <section
            className="report-clinic-questions"
            aria-labelledby="report-clinic-questions-title"
          >
            <h2 id="report-clinic-questions-title">
              진료 때 확인하고 싶은 질문
            </h2>

            <ul>
              {report.clinic.consultation_questions.map((question) => (
                <li key={question.question_id}>
                  <p>{question.text}</p>
                  <small>
                    {question.medication_name} ·{' '}
                    {question.last_selected_date}
                  </small>
                </li>
              ))}
            </ul>

            <p className="report-clinic-questions__note">
              사용자가 안내 목록에서 직접 고른 질문입니다.
            </p>
          </section>
        )}

      {report.records.length > 0 ? (
        <section
          className="report-section"
          aria-labelledby="report-records-title"
        >
          <div className="report-section__heading">
            <h2 id="report-records-title">복약 흐름</h2>
            <span>날짜별 복약 기록</span>
          </div>

          {report.overdue_pending_count > 0 && (
            <p className="report-records__overdue" role="status">
              확인 기한이 지난 기록 {report.overdue_pending_count}건이 아직
              미확인으로 정리되지 않았어요. 아래 목록에서는 예정으로 보일 수
              있어요.
            </p>
          )}

          <ul className="report-records">
            {report.records.map((record) => {
              const status = record.checkin?.status ?? record.status

              return (
                <li key={record.occurrence_id}>
                  <time dateTime={record.scheduled_local_date}>
                    {record.scheduled_local_date}
                  </time>

                  <span>
                    {RECORD_STATUS_LABELS[status] ?? status}
                  </span>

                  {!clinic && (
                    <Link
                      to={`/schedule/occurrences/${record.occurrence_id}?date=${record.scheduled_local_date}`}
                    >
                      기록 확인·정정
                    </Link>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ) : (
        <StatusPanel
          variant="empty"
          title={`${report.period_days}일 동안 복약 기록이 없어요`}
          description="복약 기록이 생기면 서버에서 집계한 결과를 보여드려요."
        />
      )}

      {!clinic && countsBlock}
    </>
  )
}

function ReportPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()

  const clinic = location.pathname === '/report/clinic'
  const period: MedicationReportPeriod =
    searchParams.get('period') === '30' ? 30 : 7

  const [report, setReport] =
    useState<MedicationReportData | null>(null)
  const [failure, setFailure] =
    useState<LoadFailure | null>(null)

  const requestSequence = useRef(0)

  const loadReport = useCallback(async () => {
    const sequence = requestSequence.current + 1
    requestSequence.current = sequence

    setReport(null)
    setFailure(null)

    try {
      const response = await getMedicationReport(
        period,
        undefined,
        clinic ? 'CLINIC' : undefined,
      )

      if (requestSequence.current === sequence) {
        setReport(response.data)
      }
    } catch (error) {
      if (requestSequence.current === sequence) {
        setFailure(getLoadFailure(error))
      }
    }
  }, [period, clinic])

  useEffect(() => {
    void loadReport()
  }, [loadReport])

  useEffect(() => {
    const refresh = () => void loadReport()

    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') {
        refresh()
      }
    }

    window.addEventListener('focus', refresh)
    window.addEventListener('pageshow', refresh)
    document.addEventListener(
      'visibilitychange',
      refreshWhenVisible,
    )

    return () => {
      window.removeEventListener('focus', refresh)
      window.removeEventListener('pageshow', refresh)
      document.removeEventListener(
        'visibilitychange',
        refreshWhenVisible,
      )
    }
  }, [loadReport])

  const selectPeriod = (nextPeriod: MedicationReportPeriod) => {
    if (nextPeriod === period) return

    setSearchParams({
      period: String(nextPeriod),
    })
  }

  const handleLoginRecovery = () => {
    clearAuthenticatedSession()
    navigate('/login')
  }

  const navigateMain = (item: string) => {
    if (item === '홈') navigate('/')
    if (item === '일정') navigate('/schedule')
    if (item === '도지') navigate('/chat')
    if (item === '가이드') navigate('/guides')
    if (item === '메뉴') navigate('/menu')
  }

  return (
    <div className="mvp-page mvp-report-page">
      <MobileShell
        title={clinic ? '진료 시 보여주기' : undefined}
        onBack={() => {
          if (clinic) {
            navigate(`/report?period=${period}`)
            return
          }

          navigate('/menu')
        }}
        brandMark={clinic ? false : undefined}
        hideNavigation={clinic}
        activeNavigation="메뉴"
        onNavigate={navigateMain}
      >
        <main
          className="app-scroll mvp-page__content mvp-report"
          aria-busy={report === null && failure === null}
        >
          {!clinic && (
            <div className="report-intro">
              <div>
                <h2 className="mvp-page__title">
                  복약 리포트
                </h2>
                <p>
                  최근 복약 기록을 기간별로 확인해요.
                </p>
              </div>
            </div>
          )}

          {!clinic && (
            <fieldset className="report-period">
              <legend className="sr-only">조회 기간</legend>

              <div className="report-period__control">
                {PERIODS.map((days) => (
                  <button
                    key={days}
                    type="button"
                    aria-pressed={period === days}
                    onClick={() => selectPeriod(days)}
                  >
                    {days}일
                  </button>
                ))}
              </div>
            </fieldset>
          )}

          {!clinic && (
            <button
              type="button"
              className="report-view-link"
              aria-label="진료 시 보여주기"
              onClick={() =>
                navigate(`/report/clinic?period=${period}`)
              }
            >
              <span className="report-view-link__copy">
                <strong>진료 시 보여주기</strong>
                <small>
                  최근 기록을 의료진에게 간편하게 보여줘요.
                </small>
              </span>

              <span
                className="report-view-link__chevron"
                aria-hidden="true"
              >
                ›
              </span>
            </button>
          )}

          {report === null && failure === null && (
            <StatusPanel
              variant="loading"
              title="복약 리포트를 불러오는 중이에요"
              description="서버의 집계 결과를 확인하고 있어요."
            />
          )}

          {failure?.kind === 'authentication' && (
            <StatusPanel
              variant="error-final"
              title="다시 로그인해 주세요"
              description={failure.message}
              action={
                <button
                  type="button"
                  className="report-state-action"
                  onClick={handleLoginRecovery}
                >
                  다시 로그인
                </button>
              }
            />
          )}

          {failure?.kind === 'not-found' && (
            <StatusPanel
              variant="empty"
              title="복약 리포트가 노출되지 않았어요"
              description={failure.message}
            />
          )}

          {failure?.kind === 'retryable' && (
            <StatusPanel
              variant="error-retryable"
              title="복약 리포트를 불러오지 못했어요"
              description={failure.message}
              onRetry={() => void loadReport()}
            />
          )}

          {failure?.kind === 'final' && (
            <StatusPanel
              variant="error-final"
              title="복약 리포트를 확인할 수 없어요"
              description={failure.message}
            />
          )}

          {report && (
            <ReportSummary
              report={report}
              clinic={clinic}
            />
          )}

          {report && !clinic && (
            <section
              className="report-correction"
              aria-labelledby="report-correction-title"
            >
              <div>
                <h2 id="report-correction-title">
                  미확인 기록 보완·정정
                </h2>
              <p>
                기록을 바꾼 뒤 이 화면으로 돌아오면 서버 리포트를 다시 조회해요.
              </p>
            </div>

            <div className="report-correction__actions">
              <button
                type="button"
                onClick={() => navigate('/schedule/unconfirmed')}
              >
                미확인 기록 보완
              </button>

              <button
                type="button"
                className="report-correction__secondary"
                onClick={() => navigate('/schedule')}
              >
                전체 복약 기록 확인
              </button>
            </div>
          </section>
        )}
        </main>
      </MobileShell>
    </div>
  )
}

export default ReportPage
