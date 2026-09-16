import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  getMedicationReport,
  type MedicationReportData,
  type MedicationReportPeriod,
  type MedicationReportRate,
} from '../api/medicationReports'
import StatusPanel from '../components/StatusPanel'
import { MobileShell } from '../design-system/components'
import { clearAuthenticatedSession } from '../features/auth/authSession'
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

function RateCard({ label, rate }: { label: string; rate: MedicationReportRate }) {
  const hasDenominator = rate.denominator > 0 && rate.percentage !== null

  return (
    <article className="report-rate-card" aria-label={label}>
      <h3>{label}</h3>
      {hasDenominator ? (
        <strong className="report-rate-card__percentage">{rate.percentage}%</strong>
      ) : (
        <strong className="report-rate-card__empty">계산할 기록 없음</strong>
      )}
      <p>
        <span className="sr-only">분자 </span>{rate.numerator}
        <span aria-hidden="true"> / </span>
        <span className="sr-only">분모 </span>{rate.denominator}
      </p>
    </article>
  )
}

function ReportSummary({ report, clinic }: { report: MedicationReportData; clinic: boolean }) {
  const counts = report.counts

  return (
    <>
      {clinic && (
        <section className="report-clinic-note" aria-labelledby="clinic-note-title">
          <h2 id="clinic-note-title">진료 시 보여주기</h2>
          <p>최근 {report.period_days}일 기록을 진료 시 보여줄 수 있도록 객관적인 수치만 정리했어요.</p>
        </section>
      )}

      <section className="report-section" aria-labelledby="report-counts-title">
        <div className="report-section__heading">
          <h2 id="report-counts-title">상태별 횟수</h2>
          <span>{report.start_date} ~ {report.end_date}</span>
        </div>
        <ul className="report-counts">
          <li className="report-count report-count--taken">
            <span aria-hidden="true">✓</span>
            <div><strong>복용</strong><small>TAKEN</small></div>
            <b>{counts.taken_count}회</b>
          </li>
          <li className="report-count report-count--not-taken">
            <span aria-hidden="true">×</span>
            <div><strong>미복용</strong><small>NOT_TAKEN</small></div>
            <b>{counts.not_taken_count}회</b>
          </li>
          <li className="report-count report-count--unconfirmed">
            <span aria-hidden="true">?</span>
            <div><strong>미확인</strong><small>UNCONFIRMED</small></div>
            <b>{counts.unconfirmed_count}회</b>
          </li>
        </ul>
      </section>

      <section className="report-section" aria-labelledby="report-rates-title">
        <h2 id="report-rates-title">복약 지표</h2>
        <div className="report-rates">
          <RateCard label="확인된 기록 중 복용률" rate={report.adherence_rate} />
          <RateCard label="기록 확인률" rate={report.confirmation_rate} />
        </div>
      </section>

      <section className="report-section report-trend" aria-labelledby="report-trend-title">
        <h2 id="report-trend-title">기본 추이</h2>
        <div role="status">
          <span aria-hidden="true">—</span>
          <p>서버에서 제공하는 추이 데이터가 없어 표시하지 않았어요.</p>
        </div>
      </section>

      {report.records.length > 0 && (
        <section className="report-section" aria-labelledby="report-records-title">
          <div className="report-section__heading">
            <h2 id="report-records-title">서버 기록</h2>
            <span>서버 응답 순서</span>
          </div>
          <ul className="report-records">
            {report.records.map((record) => {
              const status = record.checkin?.status ?? record.status
              return (
                <li key={record.occurrence_id}>
                  <time dateTime={record.scheduled_local_date}>{record.scheduled_local_date}</time>
                  <span>{RECORD_STATUS_LABELS[status] ?? status}</span>
                  {!clinic && (
                    <Link to={`/schedule/occurrences/${record.occurrence_id}?date=${record.scheduled_local_date}`}>
                      기록 확인·정정
                    </Link>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </>
  )
}

function ReportPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const clinic = location.pathname === '/report/clinic'
  const period: MedicationReportPeriod = searchParams.get('period') === '30' ? 30 : 7
  const [report, setReport] = useState<MedicationReportData | null>(null)
  const [failure, setFailure] = useState<LoadFailure | null>(null)
  const requestSequence = useRef(0)

  const loadReport = useCallback(async () => {
    const sequence = requestSequence.current + 1
    requestSequence.current = sequence
    setReport(null)
    setFailure(null)

    try {
      const response = await getMedicationReport(period)
      if (requestSequence.current === sequence) {
        setReport(response.data)
      }
    } catch (error) {
      if (requestSequence.current === sequence) {
        setFailure(getLoadFailure(error))
      }
    }
  }, [period])

  useEffect(() => {
    void loadReport()
  }, [loadReport])

  useEffect(() => {
    const refresh = () => void loadReport()
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }

    window.addEventListener('focus', refresh)
    window.addEventListener('pageshow', refresh)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => {
      window.removeEventListener('focus', refresh)
      window.removeEventListener('pageshow', refresh)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [loadReport])

  const selectPeriod = (nextPeriod: MedicationReportPeriod) => {
    if (nextPeriod === period) return
    setSearchParams({ period: String(nextPeriod) })
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

  const targetView = clinic ? `/report?period=${period}` : `/report/clinic?period=${period}`

  return (
    <div className="mvp-page mvp-report-page">
      <MobileShell
        title="복약 리포트"
        onBack={() => navigate('/menu')}
        activeNavigation="메뉴"
        onNavigate={navigateMain}
      >
        <main className="app-scroll mvp-page__content mvp-report" aria-busy={report === null && failure === null}>
          <div className="report-intro">
            <div>
              <h2 className="mvp-page__title">{clinic ? '최근 복약 기록 요약' : '복약 리포트'}</h2>
              <p>{clinic ? '진료 시 확인할 객관적인 기록을 모았어요.' : '최근 복약 기록을 기간별로 확인해요.'}</p>
            </div>
          </div>

          <fieldset className="report-period">
            <legend>조회 기간</legend>
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

          <button
            type="button"
            className="report-view-link"
            aria-label={clinic ? '기본 보기' : '진료 시 보여주기'}
            onClick={() => navigate(targetView)}
          >
            <span>{clinic ? '기본 리포트로 돌아가기' : '진료 시 보여주기'}</span>
            <small>{clinic ? '기간별 복약 리포트를 확인해요.' : '최근 기록을 의료진에게 간편하게 보여줘요.'}</small>
          </button>

          {report === null && failure === null && (
            <StatusPanel variant="loading" title="복약 리포트를 불러오는 중이에요" description="서버의 집계 결과를 확인하고 있어요." />
          )}

          {failure?.kind === 'authentication' && (
            <StatusPanel
              variant="error-final"
              title="다시 로그인해 주세요"
              description={failure.message}
              action={<button type="button" className="report-state-action" onClick={handleLoginRecovery}>다시 로그인</button>}
            />
          )}

          {failure?.kind === 'not-found' && (
            <StatusPanel variant="empty" title="복약 리포트가 노출되지 않았어요" description={failure.message} />
          )}

          {failure?.kind === 'retryable' && (
            <StatusPanel variant="error-retryable" title="복약 리포트를 불러오지 못했어요" description={failure.message} onRetry={() => void loadReport()} />
          )}

          {failure?.kind === 'final' && (
            <StatusPanel variant="error-final" title="복약 리포트를 확인할 수 없어요" description={failure.message} />
          )}

          {report?.records.length === 0 && (
            <StatusPanel
              variant="empty"
              title={`${report.period_days}일 동안 복약 기록이 없어요`}
              description="복약 기록이 생기면 서버에서 집계한 결과를 보여드려요."
            />
          )}

          {report && <ReportSummary report={report} clinic={clinic} />}

          {report && !clinic && (
            <section className="report-correction" aria-labelledby="report-correction-title">
              <div>
                <h2 id="report-correction-title">미확인 기록 보완·정정</h2>
                <p>기록을 바꾼 뒤 이 화면으로 돌아오면 서버 리포트를 다시 조회해요.</p>
              </div>
              <div className="report-correction__actions">
                <button type="button" onClick={() => navigate('/schedule/unconfirmed')}>미확인 기록 보완</button>
                <button type="button" className="report-correction__secondary" onClick={() => navigate('/schedule')}>전체 복약 기록 확인</button>
              </div>
            </section>
          )}
        </main>
      </MobileShell>
    </div>
  )
}

export default ReportPage
