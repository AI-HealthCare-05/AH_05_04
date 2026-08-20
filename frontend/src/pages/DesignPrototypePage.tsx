import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Button,
  Card,
  FieldReviewRow,
  MobileShell,
  StatusBadge,
} from '../design-system/components'
import {
  getPrototypeScenario,
  prototypeScenarios,
  type PrototypeScenario,
} from '../design-system/prototypeScenarios'
import {
  emptyPrototypeData,
  medicationFieldKeys,
  medicationFieldLabels,
  type PrototypeData,
} from '../design-system/prototypeData'
import '../design-system/prototype.css'

type PrototypeScreen =
  | 'welcome'
  | 'signup'
  | 'home'
  | 'upload'
  | 'processing'
  | 'ocr-quality-failure'
  | 'ocr-field-review'
  | 'chat'
  | 'chat-gate'
  | 'schedule'
  | 'dose-decision'
  | 'barrier'
  | 'support'
  | 'support-plan'
  | 'guide'
  | 'health-info'
  | 'menu'
  | 'signed-out'
  | 'records'
  | 'insights'
  | 'lifestyle'
  | 'otc'
  | 'profile'
  | 'notifications'
  | 'support-hub'
  | 'support-review'

type MainNavigationItem = '홈' | '일정' | '가이드' | '메뉴'
type Navigate = (screen: PrototypeScreen) => void

const mainNavigationTarget: Record<MainNavigationItem, PrototypeScreen> = {
  홈: 'home',
  일정: 'schedule',
  가이드: 'guide',
  메뉴: 'menu',
}

const supportReasons = ['깜빡했어요', '일정이나 외출 때문에 어려웠어요', '복용 방법이 헷갈렸어요', '불편한 증상이 있었어요']
const supportPlans = ['기존 행동과 복용 연결하기', '한 차례 재알림 받기', '약사에게 확인할 질문 정리하기']

const productJourney = [
  { phase: '처음 시작', items: [['시작', 'welcome'], ['회원가입', 'signup'], ['홈', 'home']] },
  { phase: '처방·가이드', items: [['처방전 등록', 'upload'], ['OCR 처리', 'processing'], ['OCR 확인', 'ocr-field-review'], ['복약 가이드', 'guide'], ['복약 챗봇', 'chat'], ['챗봇 잠금', 'chat-gate']] },
  { phase: '매일 복약', items: [['오늘 일정', 'schedule'], ['복용 상태', 'dose-decision'], ['건너뜀 이유', 'barrier'], ['맞춤 복약 도움', 'support'], ['실행계획', 'support-plan'], ['내 복약 도움', 'support-hub'], ['도움 사용 후기', 'support-review']] },
  { phase: '기록·확장', items: [['문서·처방 기록', 'records'], ['복약 리포트', 'insights'], ['생활관리', 'lifestyle'], ['일반 의약품 확인', 'otc']] },
  { phase: '계정·설정', items: [['메뉴', 'menu'], ['내 정보', 'profile'], ['건강 프로필', 'health-info'], ['복약 알림', 'notifications']] },
] satisfies Array<{ phase: string; items: Array<[string, PrototypeScreen]> }>

const screens: { key: PrototypeScreen; label: string; annotation: string }[] = [
  { key: 'welcome', label: '시작', annotation: '서비스 가치와 안전 원칙을 확인하고 시작합니다.' },
  { key: 'signup', label: '회원가입', annotation: '계정 정보와 필수 동의 후 홈으로 이동합니다.' },
  {
    key: 'home',
    label: '홈 · 빈 상태',
    annotation: '현재 날짜를 사용하고 기록이 없을 때 빈 그래프 대신 시작 방법을 안내합니다.',
  },
  { key: 'upload', label: '처방전 등록', annotation: '촬영 또는 파일에서 처방전을 등록합니다.' },
  { key: 'processing', label: 'OCR 처리 중', annotation: '업로드와 인식·구조화 진행 상태를 분리해 표시합니다.' },
  {
    key: 'ocr-quality-failure',
    label: 'OCR · 재촬영',
    annotation: '문서 품질 실패 또는 핵심 필드를 읽을 수 없을 때 결과 확인 전에 진입합니다.',
  },
  {
    key: 'ocr-field-review',
    label: 'OCR · 필드 확인',
    annotation: '약 전체가 아니라 약명·용량·횟수·기간·시점 단위로 확인 상태를 표시합니다.',
  },
  {
    key: 'chat',
    label: '챗봇 · 키보드',
    annotation: '키보드가 열리면 하단 내비게이션을 숨기고 메시지 영역만 스크롤합니다.',
  },
  { key: 'chat-gate', label: '챗봇 · 가이드 없음', annotation: '가이드가 없을 때 문서 등록이 필요한 이유를 먼저 안내합니다.' },
  {
    key: 'schedule',
    label: '일정 · 상태 기록',
    annotation: '복용 완료와 미복용 상태를 구분하고 기록 정정 및 맞춤 지원 진입을 제공합니다.',
  },
  { key: 'dose-decision', label: '복용 상태 선택', annotation: '미복용·나중 예정·모름·의료진 중단·이상 증상을 구분합니다.' },
  { key: 'barrier', label: '건너뜀 이유', annotation: '복용이 어려웠던 이유를 다중 선택으로 기록합니다.' },
  {
    key: 'support',
    label: '맞춤 지원',
    annotation: '미복용 이유에서 세부 상황과 실행 가능한 지원으로 단계적으로 이동합니다.',
  },
  {
    key: 'support-plan',
    label: '맞춤 지원 · 실행계획',
    annotation: '선택한 상황을 실제로 실행할 수 있는 한 가지 계획으로 바꿉니다.',
  },
  {
    key: 'guide',
    label: '복약 가이드',
    annotation: '확인된 OCR 결과로 생성한 가이드에서 일정과 챗봇으로 이동합니다.',
  },
  {
    key: 'health-info',
    label: '건강정보',
    annotation: '개인화 안전 안내 전에 필요한 핵심 건강정보를 확인합니다.',
  },
  {
    key: 'menu',
    label: '메뉴',
    annotation: '탭과 중복되지 않는 관리 기능과 로그아웃 경로를 제공합니다.',
  },
  {
    key: 'signed-out',
    label: '로그아웃 완료',
    annotation: '로그아웃 후에는 개인 건강정보가 보이지 않는 별도 상태로 이동합니다.',
  },
  { key: 'records', label: '처방·문서 기록', annotation: '현재 처방과 이전 문서를 구분해 탐색합니다.' },
  { key: 'insights', label: '복약 리포트', annotation: '완료·미복용·미확인을 구분해 주간 흐름을 보여줍니다.' },
  { key: 'lifestyle', label: '생활관리', annotation: '복약 안내와 생활 행동을 분리해 제공합니다.' },
  { key: 'otc', label: '일반 의약품 확인', annotation: '제품의 성분·함량·제형을 확인한 뒤 처방과 비교합니다.' },
  { key: 'profile', label: '내 정보', annotation: '계정·동의·건강 프로필·로그아웃을 관리합니다.' },
  { key: 'notifications', label: '복약 알림', annotation: '알림별 사용 여부와 잠금 화면 노출 범위를 설정합니다.' },
  { key: 'support-hub', label: '내 복약 도움', annotation: '적용 중인 도움과 다시 확인할 시점을 관리합니다.' },
  { key: 'support-review', label: '도움 사용 후기', annotation: '도움의 유용성과 다음 행동을 확인합니다.' },
]

function WelcomeScreen({ onNavigate }: { onNavigate: Navigate }) {
  return (
    <MobileShell title="다섯알" hideNavigation>
      <div className="app-scroll welcome-content">
        <div className="welcome-mark" aria-hidden="true">●</div>
        <p className="welcome-eyebrow">내 처방을 쉬운 말로</p>
        <h1 className="screen-title">내 약을 알고,<br />매일 잘 챙기는<br />건강 습관</h1>
        <p className="screen-description">처방전을 확인하고 복용법·일정·안전한 질문까지 한 흐름으로 연결해요.</p>
        <div className="notice"><strong>안전 원칙</strong><br />이 서비스는 처방을 변경하거나 진단하지 않습니다. 처방전과 약 봉투의 지시를 우선해 주세요.</div>
        <div className="button-stack">
          <Button fullWidth onClick={() => onNavigate('signup')}>회원가입하고 시작하기</Button>
        </div>
      </div>
    </MobileShell>
  )
}

function SignupScreen({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  return (
    <MobileShell title="회원가입" onBack={onBack} hideNavigation>
      <form className="app-scroll" onSubmit={(event) => { event.preventDefault(); onDone() }}>
        <h1 className="screen-title">다섯알을<br />시작해 볼까요?</h1>
        <p className="screen-description">의료정보는 본인 확인과 동의 후 안전하게 관리합니다.</p>
        <label className="form-field">이름<input required defaultValue="홍길동" /></label>
        <label className="form-field">이메일<input required type="email" placeholder="hello@example.com" /></label>
        <label className="form-field">비밀번호<input required type="password" minLength={8} placeholder="8자 이상 입력" /></label>
        <label className="notice consent-row"><input required type="checkbox" />서비스 이용약관·개인정보·민감정보 처리에 동의합니다.</label>
        <Button fullWidth type="submit" style={{ marginTop: 18 }}>가입 완료</Button>
      </form>
    </MobileShell>
  )
}

function UploadScreen({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const [selected, setSelected] = useState(false)
  return (
    <MobileShell title="처방전 등록" onBack={onBack} hideNavigation>
      <div className="app-scroll">
        <h1 className="screen-title">처방전을<br />등록해 주세요</h1>
        <p className="screen-description">사진 또는 PDF에서 약 이름·용량·횟수·기간을 읽어요.</p>
        <button type="button" className={`upload-zone ${selected ? 'selected' : ''}`} onClick={() => setSelected(true)}>
          <span aria-hidden="true">▧</span><strong>{selected ? '처방전_20260820.jpg' : '사진 촬영 또는 파일 선택'}</strong><small>{selected ? '선택 완료 · 눌러서 변경' : 'JPG · PNG · PDF / 최대 10MB'}</small>
        </button>
        <div className="notice"><strong>가릴 수 있는 정보</strong><br />이름·주소·환자번호·주민등록번호·바코드·QR을 가린 뒤 등록할 수 있어요.</div>
        <Button fullWidth disabled={!selected} style={{ marginTop: 18 }} onClick={onNext}>처방전 읽기</Button>
      </div>
    </MobileShell>
  )
}

function ProcessingScreen({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  return (
    <MobileShell title="처방전 분석" onBack={onBack} hideNavigation>
      <div className="app-scroll processing-content">
        <div className="quality-illustration processing-document" aria-hidden="true" />
        <h1 className="screen-title">처방전 내용을<br />확인하고 있어요</h1>
        <p className="screen-description">문서 업로드 → 글자 인식 → 복약정보 구조화 순서로 처리합니다.</p>
        <div className="processing-steps">
          <span>✓ 문서 업로드 완료</span><span>● 약 이름과 복용법 인식 중</span><span>○ 구조화 결과 확인</span>
        </div>
        <Button fullWidth variant="secondary" onClick={onDone}>분석 완료 상태 보기</Button>
      </div>
    </MobileShell>
  )
}

function HomeScreen({ onNavigate, scenario, data }: { onNavigate: Navigate; scenario: PrototypeScenario; data: PrototypeData }) {
  const today = useMemo(
    () => new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date()),
    [],
  )

  return (
    <MobileShell onNavigate={(item) => onNavigate(mainNavigationTarget[item])}>
      <div className="app-scroll">
        <div style={{ color: 'var(--ds-text-muted)', fontSize: 14, fontWeight: 700 }}>{today}</div>
        <h1 className="screen-title" style={{ marginTop: 6, fontSize: 24 }}>{data.personName ? `${data.personName}님, ` : ''}오늘 약도 챙겨볼까요?</h1>
        <Card className="home-hero">
          <div style={{ color: 'rgb(255 255 255 / 72%)', fontSize: 13, fontWeight: 800 }}>{scenario.hasGuide ? '오늘 확인할 일' : '첫 번째 할 일'}</div>
          <h2 style={{ margin: '12px 0 8px', fontSize: 24 }}>{scenario.hasGuide ? '오늘 복약 일정을 확인해 주세요' : '처방전을 등록하고 내 복약 가이드를 받아보세요'}</h2>
          <p style={{ color: 'rgb(255 255 255 / 75%)', fontSize: 14 }}>{scenario.hasGuide ? '확인된 처방 데이터에서 생성한 일정이에요.' : '등록한 처방은 원본과 대조한 뒤에만 가이드에 사용해요.'}</p>
          <Button fullWidth className="hero-button" onClick={() => onNavigate(scenario.hasGuide ? 'guide' : 'upload')}>{scenario.hasGuide ? '내 복약 가이드 보기' : '처방전 등록하기'}</Button>
          {!scenario.hasGuide && <Button fullWidth variant="ghost" className="hero-link" onClick={() => onNavigate('health-info')}>건강정보 입력하기</Button>}
        </Card>
        <h2 style={{ margin: '26px 0 12px', fontSize: 19 }}>바로가기</h2>
        <div className="home-quick-grid">
          <button type="button" onClick={() => onNavigate('upload')}><strong>문서 등록</strong><span>처방전·진료기록</span></button>
          <button type="button" onClick={() => onNavigate(scenario.hasGuide ? 'chat' : 'chat-gate')}><strong>복약 챗봇</strong><span>{scenario.hasGuide ? '처방 기반 질문' : '가이드 생성 후 이용'}</span></button>
          <button type="button" onClick={() => onNavigate('schedule')}><strong>복약 일정</strong><span>오늘 복용 확인</span></button>
          <button type="button" onClick={() => onNavigate('otc')}><strong>일반 의약품</strong><span>함께 복용 확인</span></button>
        </div>
        <h2 style={{ margin: '26px 0 12px', fontSize: 19 }}>이번 주 복약 흐름</h2>
        <Card>
          <div style={{ padding: '12px 0', textAlign: 'center' }}>
            <div aria-hidden="true" style={{ fontSize: 30, color: '#7d8792' }}>▥</div>
            <strong style={{ display: 'block', marginTop: 10 }}>{scenario.hasRecords ? '이번 주 복약 기록이 있어요' : '아직 복약 기록이 없어요'}</strong>
            <p style={{ margin: '6px 0 0', color: 'var(--ds-text-muted)', fontSize: 14 }}>{scenario.hasRecords ? '일정에서 완료·미복용·미확인 기록을 구분해 확인해요.' : '일정에서 복용 상태를 기록하면 흐름을 보여드려요.'}</p>
          </div>
        </Card>
      </div>
    </MobileShell>
  )
}

function OcrQualityFailureScreen({ onBack, onRetry }: { onBack: () => void; onRetry: () => void }) {
  return (
    <MobileShell title="처방전 인식" onBack={onBack} hideNavigation>
      <div className="app-scroll" style={{ paddingBottom: 'calc(24px + var(--ds-safe-bottom))' }}>
        <div className="quality-illustration" aria-hidden="true" />
        <h1 className="screen-title">처방전 내용을<br />읽기 어려워요</h1>
        <p className="screen-description">처방전 전체가 선명하게 보이도록 다시 촬영해 주세요.</p>
        <Card>
          <strong>사진을 확인해 주세요</strong>
          <ul className="quality-reasons">
            <li><span aria-hidden="true">•</span> 문서 위·아래가 잘리지 않았는지 확인</li>
            <li><span aria-hidden="true">•</span> 빛 반사와 그림자를 피해 촬영</li>
            <li><span aria-hidden="true">•</span> 글자가 흔들리지 않도록 초점 확인</li>
          </ul>
        </Card>
        <div className="button-stack">
          <Button fullWidth onClick={onRetry}>다시 촬영하기</Button>
          <Button fullWidth variant="secondary" onClick={onRetry}>다른 사진 선택하기</Button>
          <Button fullWidth variant="ghost" onClick={onBack}>취소하고 돌아가기</Button>
        </div>
      </div>
    </MobileShell>
  )
}

function OcrFieldReviewScreen({
  onBack,
  onShowFailure,
  onConfirmed,
  scenario,
  data,
}: {
  onBack: () => void
  onShowFailure: () => void
  onConfirmed: () => void
  scenario: PrototypeScenario
  data: PrototypeData
}) {
  const [confirmed, setConfirmed] = useState(false)
  const fields = medicationFieldKeys.map((key) => ({
    key,
    label: medicationFieldLabels[key],
    value: data.medication[key],
    status: data.medication[key] ? scenario.fieldStatuses[key] : 'required' as const,
  }))
  const reviewCount = fields.filter((field) => field.status !== 'confirmed').length
  const hasCompleteData = fields.every((field) => field.value)

  return (
    <MobileShell title="OCR 결과 확인" onBack={onBack} hideNavigation>
      <div className="app-scroll" style={{ paddingBottom: 'calc(24px + var(--ds-safe-bottom))' }}>
        <h1 className="screen-title">처방전과 같은지<br />확인해 주세요</h1>
        <p className="screen-description">확인 전 정보는 일정이나 가이드에 사용하지 않아요.</p>
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
            <strong>처방약 1</strong>
            <StatusBadge tone={reviewCount === 0 ? 'neutral' : 'attention'}>{reviewCount === 0 ? '모든 필드 확인됨' : `${reviewCount}개 필드 확인 필요`}</StatusBadge>
          </div>
          <div className="field-review-list" style={{ marginTop: 12 }}>
            {fields.map((field) => (
              <FieldReviewRow key={field.key} label={field.label} value={field.value || '데이터 없음'} status={field.status === 'confirmed' ? '확인됨' : field.status === 'recommended' ? '확인 권장' : '확인 필요'} needsReview={field.status !== 'confirmed'} />
            ))}
          </div>
        </Card>
        {reviewCount > 0 && <div className="notice attention"><strong>확인 필요 필드</strong><br />원본의 숫자와 단위를 다시 대조하고 필요하면 직접 수정해 주세요.</div>}
        <label className="notice" style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} style={{ width: 20, height: 20 }} />
          <span>원본 처방전과 약 이름·용량·횟수·기간·복용 시점을 직접 확인했습니다.</span>
        </label>
        <div className="button-stack">
          <Button fullWidth disabled={!confirmed || !hasCompleteData} onClick={onConfirmed}>확정하고 가이드 만들기</Button>
          <Button fullWidth variant="secondary" onClick={onShowFailure}>재촬영 상태 보기</Button>
          <Button fullWidth variant="ghost" onClick={onBack}>취소하고 돌아가기</Button>
        </div>
      </div>
    </MobileShell>
  )
}

function ChatScreen({ keyboardOpen, onBack, onNavigate, scenario }: { keyboardOpen: boolean; onBack: () => void; onNavigate: Navigate; scenario: PrototypeScenario }) {
  const [message, setMessage] = useState('저녁 약을 깜빡했어요')
  const [sentMessage, setSentMessage] = useState('저녁 약을 깜빡했어요.')

  return (
    <MobileShell title="복약 챗봇" onBack={onBack} hideNavigation={keyboardOpen} activeNavigation="가이드" onNavigate={(item) => onNavigate(mainNavigationTarget[item])}>
      <div className={`chat-layout ${keyboardOpen ? 'keyboard-open' : ''}`}>
        <div className="chat-messages" aria-live="polite">
          <div className="chat-message">{scenario.hasGuide ? '현재 확인된 처방 데이터를 기준으로 복용 질문에 답해 드릴게요.' : '확인된 처방 데이터가 아직 없어요. 먼저 처방전을 등록하고 내용을 확인해 주세요.'}</div>
          <div className="chat-message user">{sentMessage}</div>
          <div className="chat-message">임의로 두 배 복용하지 마세요. 약마다 놓친 복용 지침이 다르므로 처방전과 약 봉투를 먼저 확인해 주세요.</div>
          <div className="chat-message">마지막으로 복용한 시간과 원래 복용 예정 시간을 알려주시면, 확인해야 할 내용을 정리해 드릴게요.</div>
        </div>
        <div className="chat-composer">
          <textarea className="chat-input" value={message} onChange={(event) => setMessage(event.target.value)} aria-label="복약 질문" rows={1} />
          <button className="chat-send" type="button" aria-label="질문 전송" onClick={() => message.trim() && setSentMessage(message.trim())}>↑</button>
        </div>
        {keyboardOpen && (
          <div className="simulated-keyboard" aria-label="모바일 키보드 시뮬레이션">
            {['ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ', 'ㅁㄴㅇㄹㅎㅗㅓㅏㅣ', 'ㅋㅌㅊㅍㅠㅜㅡ'].map((row) => (
              <div key={row} className="keyboard-row" style={{ gridTemplateColumns: `repeat(${row.length}, 1fr)` }}>
                {[...row].map((key) => <span key={key} className="keyboard-key">{key}</span>)}
              </div>
            ))}
            <div className="keyboard-action-row">
              <span className="keyboard-key">123</span>
              <span className="keyboard-key">띄어쓰기</span>
              <span className="keyboard-key">완료</span>
            </div>
          </div>
        )}
      </div>
    </MobileShell>
  )
}

function ChatGateScreen({ onBack, onRegister }: { onBack: () => void; onRegister: () => void }) {
  return (
    <MobileShell title="복약 챗봇" onBack={onBack} hideNavigation>
      <div className="app-scroll"><h1 className="screen-title">먼저 복약 가이드가<br />필요해요</h1><p className="screen-description">챗봇은 사용자가 확인한 처방 데이터와 검토된 근거 범위에서 답해요.</p><Card><strong>처방전 등록부터 시작해 주세요</strong><p style={{ color: 'var(--ds-text-muted)', lineHeight: 1.6 }}>처방전을 등록하고 약 이름·용량·횟수를 원본과 확인하면 챗봇을 이용할 수 있어요.</p><Button fullWidth onClick={onRegister}>처방전 등록하기</Button></Card></div>
    </MobileShell>
  )
}

function DoseDecisionScreen({ onBack, onBarrier, onLater }: { onBack: () => void; onBarrier: () => void; onLater: () => void }) {
  return (
    <MobileShell title="복용 상태 기록" onBack={onBack} hideNavigation><div className="app-scroll"><h1 className="screen-title">이번 복용 상태를<br />알려주세요</h1><p className="screen-description">나중에 복용할 예정인지, 이번 회차를 복용하지 못했는지 구분해요.</p><div className="button-stack"><Button fullWidth variant="secondary" onClick={onBarrier}>이번 복용을 건너뜀</Button><Button fullWidth variant="secondary" onClick={onLater}>나중에 복용 예정</Button><Button fullWidth variant="secondary" onClick={onBarrier}>복용 여부를 모르겠음</Button><Button fullWidth variant="secondary" onClick={onBarrier}>의사·약사 지시로 중단</Button><Button fullWidth variant="secondary" onClick={onBarrier}>불편한 증상 때문에 복용하지 못함</Button></div><div className="notice attention"><strong>임의로 두 배 복용하지 마세요.</strong><br />약마다 놓친 복용 지침이 다르므로 처방전과 약 봉투를 먼저 확인해 주세요.</div></div></MobileShell>
  )
}

function BarrierScreen({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const [selected, setSelected] = useState<string[]>([])
  const items = ['깜빡했어요', '일정이나 외출 때문에 어려웠어요', '복용 방법이 헷갈렸어요', '약이 꼭 필요한지 모르겠어요', '약을 먹는 것이 걱정돼요', '불편한 증상이 있었어요', '약이 없거나 구하기 어려웠어요', '다른 이유가 있어요']
  return (
    <MobileShell title="건너뜀 기록" onBack={onBack} hideNavigation><div className="app-scroll"><h1 className="screen-title">이번에는 어떤 점이<br />가장 어려웠나요?</h1><p className="screen-description">해당되는 항목을 여러 개 골라도 괜찮아요.</p><div className="barrier-list">{items.map((item) => <label key={item}><input type="checkbox" checked={selected.includes(item)} onChange={() => setSelected((current) => current.includes(item) ? current.filter((value) => value !== item) : [...current, item])} /><span>{item}</span></label>)}</div><Button fullWidth disabled={selected.length === 0} style={{ marginTop: 20 }} onClick={onNext}>선택한 어려움으로 도움 찾기</Button></div></MobileShell>
  )
}

function ScheduleScreen({ onDecision, onNavigate, scenario, data }: { onDecision: () => void; onNavigate: Navigate; scenario: PrototypeScenario; data: PrototypeData }) {
  const [status, setStatus] = useState<'scheduled' | 'taken' | 'skipped'>(scenario.doseStatus)
  const hasMedicationData = scenario.hasGuide && medicationFieldKeys.every((key) => data.medication[key])

  useEffect(() => setStatus(scenario.doseStatus), [scenario.doseStatus])

  return (
    <MobileShell activeNavigation="일정" onNavigate={(item) => onNavigate(mainNavigationTarget[item])}>
      <div className="app-scroll">
        <h1 className="screen-title">오늘 복약 일정</h1>
        <p className="screen-description">처방 원문의 식전·식후 지시를 임의의 시간으로 바꾸지 않아요.</p>
        <div className="notice"><strong>사용자가 원본과 확인한 OCR 일정</strong><br />제형·1회량·횟수·기간을 확인한 뒤 생성된 일정입니다.</div>
        {!hasMedicationData ? <Card>
          <div style={{ padding: '12px 0', textAlign: 'center' }}>
            <strong>아직 생성된 복약 일정이 없어요</strong>
            <p style={{ color: 'var(--ds-text-muted)', fontSize: 14 }}>처방전을 등록하고 원본과 확인하면 일정이 만들어져요.</p>
            <Button fullWidth onClick={() => onNavigate('ocr-quality-failure')}>처방전 등록하기</Button>
          </div>
        </Card> : <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <div style={{ color: 'var(--ds-text-muted)', fontSize: 13, fontWeight: 700 }}>{data.medication.timing}</div>
              <h2 style={{ margin: '8px 0 0', fontSize: 18 }}>{data.medication.name}</h2>
            </div>
            <StatusBadge>{status === 'scheduled' ? '복용 예정' : status === 'taken' ? '복용 완료' : '이번 회차 미복용'}</StatusBadge>
          </div>
          <p style={{ margin: '14px 0 0', padding: 12, borderRadius: 12, background: 'var(--ds-muted)', fontSize: 14, fontWeight: 700 }}>1회 {data.medication.dose} · {data.medication.frequency} · {data.medication.duration}</p>
          {status === 'scheduled' ? (
            <div className="compact-two-column" style={{ marginTop: 14 }}>
              <Button fullWidth onClick={() => setStatus('taken')}>복용했어요</Button>
              <Button fullWidth variant="secondary" onClick={onDecision}>다른 상태 기록</Button>
            </div>
          ) : (
            <div className="compact-two-column" style={{ marginTop: 14 }}>
              <Button fullWidth variant="secondary" onClick={() => setStatus('scheduled')}>기록 정정</Button>
              {status === 'skipped' && <Button fullWidth onClick={onDecision}>이유와 도움 찾기</Button>}
            </div>
          )}
        </Card>}
        {status === 'skipped' && <div className="notice attention"><strong>두 배 복용하지 마세요.</strong><br />약별 놓친 복용 지침을 확인하고 이유에 맞는 지원을 선택할 수 있어요.</div>}
      </div>
    </MobileShell>
  )
}

function SupportScreen({ onBack, onPlan }: { onBack: () => void; onPlan: () => void }) {
  const [reason, setReason] = useState('')

  return (
    <MobileShell title="맞춤 복약 도움" onBack={onBack} hideNavigation>
      <div className="app-scroll" style={{ paddingBottom: 'calc(24px + var(--ds-safe-bottom))' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--ds-text-muted)', fontSize: 13, fontWeight: 700 }}>
          <span>2단계 · 세부 상황</span><span>3단계 중 2</span>
        </div>
        <div style={{ height: 5, marginTop: 10, borderRadius: 5, background: '#dfe4e9' }}><div style={{ width: '66%', height: '100%', borderRadius: 5, background: 'var(--ds-action)' }} /></div>
        <h1 className="screen-title" style={{ marginTop: 22 }}>어떤 상황에<br />가까웠나요?</h1>
        <p className="screen-description">1~2개만 골라주세요. 원인을 진단하지는 않아요.</p>
        <div style={{ display: 'grid', gap: 10 }}>
          {supportReasons.map((item) => (
            <button key={item} type="button" className="prototype-control" aria-pressed={reason === item} onClick={() => setReason(item)} style={{ minHeight: 64, fontSize: 15 }}>
              {item}
            </button>
          ))}
        </div>
        <Button fullWidth disabled={!reason} style={{ marginTop: 18 }} onClick={onPlan}>맞춤 지원 선택하기</Button>
        <Button fullWidth variant="ghost" style={{ marginTop: 8 }} onClick={onBack}>지원은 나중에 고를게요</Button>
      </div>
    </MobileShell>
  )
}

function SupportPlanScreen({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const [plan, setPlan] = useState('')

  return (
    <MobileShell title="맞춤 복약 도움" onBack={onBack} hideNavigation>
      <div className="app-scroll" style={{ paddingBottom: 'calc(24px + var(--ds-safe-bottom))' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--ds-text-muted)', fontSize: 13, fontWeight: 700 }}>
          <span>3단계 · 실행계획</span><span>3단계 중 3</span>
        </div>
        <div style={{ height: 5, marginTop: 10, borderRadius: 5, background: 'var(--ds-action)' }} />
        <h1 className="screen-title" style={{ marginTop: 22 }}>다음 복용을 위해<br />무엇을 준비할까요?</h1>
        <p className="screen-description">지금 실천할 수 있는 한 가지를 골라주세요.</p>
        <div className="button-stack">
          {supportPlans.map((item) => (
            <Button key={item} fullWidth variant={plan === item ? 'primary' : 'secondary'} onClick={() => setPlan(item)}>{item}</Button>
          ))}
        </div>
        <Button fullWidth disabled={!plan} style={{ marginTop: 22 }} onClick={onDone}>계획 저장하고 일정으로</Button>
      </div>
    </MobileShell>
  )
}

function GuideScreen({ onNavigate, scenario, data }: { onNavigate: Navigate; scenario: PrototypeScenario; data: PrototypeData }) {
  const hasMedicationData = medicationFieldKeys.every((key) => data.medication[key])
  if (!scenario.hasGuide || !hasMedicationData) {
    return (
      <MobileShell activeNavigation="가이드" onNavigate={(item) => onNavigate(mainNavigationTarget[item])}>
        <div className="app-scroll">
          <h1 className="screen-title">내 복약 가이드</h1>
          <Card>
            <div style={{ padding: '14px 0', textAlign: 'center' }}>
              <strong>아직 만들어진 가이드가 없어요</strong>
              <p style={{ color: 'var(--ds-text-muted)', fontSize: 14 }}>처방전을 등록하고 내용을 직접 확인해 주세요.</p>
              <Button fullWidth onClick={() => onNavigate('ocr-quality-failure')}>처방전 등록하기</Button>
            </div>
          </Card>
        </div>
      </MobileShell>
    )
  }

  return (
    <MobileShell activeNavigation="가이드" onNavigate={(item) => onNavigate(mainNavigationTarget[item])}>
      <div className="app-scroll">
        <h1 className="screen-title">내 복약 가이드</h1>
        <p className="screen-description">{data.personName ? `${data.personName}님이 ` : ''}직접 확인한 처방전 결과를 기준으로 만들었어요.</p>
        <Card className="home-hero">
          <div style={{ color: 'rgb(255 255 255 / 72%)', fontSize: 13, fontWeight: 800 }}>확인된 처방약 2개</div>
          <h2 style={{ margin: '10px 0 8px', fontSize: 22 }}>{data.medication.timing} 복용</h2>
          <p style={{ color: 'rgb(255 255 255 / 75%)', fontSize: 14 }}>{data.medication.name} · {data.medication.dose}</p>
          <Button fullWidth className="hero-button" onClick={() => onNavigate('schedule')}>오늘 일정 확인하기</Button>
        </Card>
        <div className="button-stack">
          <Button fullWidth onClick={() => onNavigate('chat')}>복약 챗봇에 질문하기</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('ocr-field-review')}>처방전 내용 다시 확인하기</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('lifestyle')}>생활관리 가이드 보기</Button>
        </div>
      </div>
    </MobileShell>
  )
}

function HealthInfoScreen({ onBack, onSaved }: { onBack: () => void; onSaved: () => void }) {
  const [checked, setChecked] = useState<string[]>([])
  const items = ['약물·음식 알레르기', '신장·간 기능 또는 투석', '임신·수유', '현재 복용 중인 약·건강기능식품']

  return (
    <MobileShell title="건강정보" onBack={onBack} hideNavigation>
      <div className="app-scroll" style={{ paddingBottom: 'calc(24px + var(--ds-safe-bottom))' }}>
        <h1 className="screen-title">안전 안내에 필요한<br />정보를 확인해 주세요</h1>
        <p className="screen-description">해당되는 항목을 모두 고르고 상세 내용은 다음 단계에서 입력해요.</p>
        <div style={{ display: 'grid', gap: 10 }}>
          {items.map((item) => (
            <label key={item} className="notice" style={{ marginTop: 0, display: 'flex', gap: 12, alignItems: 'center', fontWeight: 700 }}>
              <input type="checkbox" checked={checked.includes(item)} onChange={() => setChecked((current) => current.includes(item) ? current.filter((value) => value !== item) : [...current, item])} style={{ width: 22, height: 22 }} />
              {item}
            </label>
          ))}
        </div>
        <Button fullWidth style={{ marginTop: 20 }} onClick={onSaved}>저장하고 가이드 보기</Button>
      </div>
    </MobileShell>
  )
}

function RecordsScreen({ onNavigate, scenario, data }: { onNavigate: Navigate; scenario: PrototypeScenario; data: PrototypeData }) {
  return (
    <MobileShell title="문서와 처방 기록" onBack={() => onNavigate('menu')} onNavigate={(item) => onNavigate(mainNavigationTarget[item])}>
      <div className="app-scroll">
        <h1 className="screen-title">문서와 처방 기록</h1>
        <p className="screen-description">확정된 처방과 이전 문서를 한곳에서 확인해요.</p>
        <div className="compact-two-column">
          <Button fullWidth onClick={() => onNavigate('upload')}>새 문서 등록</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('insights')}>복약 리포트</Button>
        </div>
        {scenario.hasGuide && data.medication.name ? <Card className="record-card"><StatusBadge>현재 처방</StatusBadge><h2>{data.medication.name}</h2><p>{data.medication.dose} · {data.medication.frequency} · {data.medication.duration}</p><Button fullWidth variant="secondary" onClick={() => onNavigate('guide')}>복약 가이드 다시 보기</Button></Card> : <Card className="record-card"><strong>등록된 처방이 없어요</strong><p>첫 처방전을 등록하면 기록이 여기에 표시돼요.</p></Card>}
        <h2 style={{ marginTop: 26, fontSize: 18 }}>이전 기록</h2>
        <Card><strong>건강검진 결과</strong><p style={{ color: 'var(--ds-text-muted)', fontSize: 14 }}>이전 기록 · 상세 화면 준비 중</p></Card>
      </div>
    </MobileShell>
  )
}

function InsightsScreen({ onBack, onSupport, hasRecords }: { onBack: () => void; onSupport: () => void; hasRecords: boolean }) {
  return (
    <MobileShell title="복약 리포트" onBack={onBack} hideNavigation>
      <div className="app-scroll">
        <h1 className="screen-title">이번 주 복약 흐름</h1>
        <p className="screen-description">미확인을 미복용으로 단정하지 않고 상태를 나눠 보여드려요.</p>
        {hasRecords ? <><Card><strong>복용 완료</strong><div className="report-score">86%</div><div className="report-bars">{[72, 92, 58, 100, 85, 92, 35].map((height, index) => <span key={index} style={{ height: `${height}%` }} />)}</div></Card><div className="report-summary"><span><b>18</b>완료</span><span><b>2</b>미복용</span><span><b>1</b>미확인</span></div><Card><strong>이번 주 관리 포인트</strong><p>저녁 복용이 어려웠던 기록을 바탕으로 다음 복용을 돕는 방법을 찾아볼 수 있어요.</p><Button fullWidth onClick={onSupport}>실행계획 만들기</Button></Card></> : <Card><strong>아직 복약 기록이 없어요</strong><p>일정에서 복용 상태를 기록하면 주간 흐름을 보여드려요.</p></Card>}
      </div>
    </MobileShell>
  )
}

function LifestyleScreen({ onBack }: { onBack: () => void }) {
  return (
    <MobileShell title="생활관리 가이드" onBack={onBack} hideNavigation>
      <div className="app-scroll"><h1 className="screen-title">오늘 실천할<br />생활관리</h1><p className="screen-description">복약 안내와 생활 습관 정보를 구분해 보여드려요.</p>{[['식사는 규칙적으로', '약 복용과 식사 시간을 기억하기 쉬운 일과에 연결해 보세요.'], ['가벼운 활동부터', '몸 상태와 의료진 안내를 우선해 무리하지 않는 범위에서 시작하세요.'], ['기록은 판단보다 관찰', '복용 여부와 불편했던 점은 다음 상담을 준비하는 데 도움이 돼요.']].map(([title, body]) => <Card key={title}><strong>{title}</strong><p style={{ color: 'var(--ds-text-muted)', lineHeight: 1.6 }}>{body}</p></Card>)}</div>
    </MobileShell>
  )
}

function OtcScreen({ onBack, medicationName }: { onBack: () => void; medicationName?: string }) {
  const [query, setQuery] = useState('')
  const [checked, setChecked] = useState(false)
  return (
    <MobileShell title="일반 의약품 확인" onBack={onBack} hideNavigation>
      <div className="app-scroll"><h1 className="screen-title">현재 처방과<br />함께 확인해요</h1><p className="screen-description">제품명만이 아니라 성분·함량·제형까지 확인해 주세요.</p><label className="form-field">일반 의약품 또는 성분<input value={query} onChange={(event) => { setQuery(event.target.value); setChecked(false) }} placeholder="성분명·함량·제형 입력" /></label><Button fullWidth disabled={!query.trim()} style={{ marginTop: 14 }} onClick={() => setChecked(true)}>현재 처방약과 비교</Button>{checked && <Card className="record-card"><StatusBadge tone="attention">복용 전 전문가 확인</StatusBadge><h2>추가 확인 항목이 있어요</h2><p>{medicationName || '현재 처방약'}과 함께 검토할 때 탈수·신장질환·위장관 출혈·다른 NSAID 복용 여부를 확인해야 해요.</p><div className="notice attention">현재 입력된 정보만으로 개인별 복용 가능 여부를 확정하지 않아요.</div></Card>}</div>
    </MobileShell>
  )
}

function NotificationsScreen({ onBack }: { onBack: () => void }) {
  const [settings, setSettings] = useState([true, true, false, false])
  const labels = ['아침 복약 알림', '저녁 복약 알림', '알림에 약 이름 표시', '잠금 화면 상세정보 표시']
  return (
    <MobileShell title="복약 알림 설정" onBack={onBack} hideNavigation><div className="app-scroll"><h1 className="screen-title">어떻게 알림을<br />받을까요?</h1><p className="screen-description">복용 여부와 알림 확인 여부는 서로 다르게 기록해요.</p>{labels.map((label, index) => <label key={label} className="notice setting-row"><span><strong>{label}</strong><small>{index < 2 ? '매일' : '개인정보 노출 설정'}</small></span><input type="checkbox" checked={settings[index]} onChange={() => setSettings((current) => current.map((value, itemIndex) => itemIndex === index ? !value : value))} /></label>)}</div></MobileShell>
  )
}

function ProfileScreen({ onNavigate, data }: { onNavigate: Navigate; data: PrototypeData }) {
  return (
    <MobileShell title="내 정보" onBack={() => onNavigate('menu')} hideNavigation><div className="app-scroll"><Card><h2>{data.personName || '사용자'}</h2><p style={{ color: 'var(--ds-text-muted)' }}>본인 계정 · 일반 사용자</p></Card><div className="button-stack"><Button fullWidth variant="secondary" onClick={() => onNavigate('health-info')}>건강 프로필</Button><Button fullWidth variant="secondary" onClick={() => onNavigate('notifications')}>복약 알림 설정</Button></div><Card className="record-card"><strong>의료정보 동의</strong><p>서비스 이용약관·개인정보·민감정보 처리 동의 상태를 확인하고 철회할 수 있어요.</p><Button fullWidth variant="secondary">동의 관리</Button></Card><Button fullWidth variant="ghost" style={{ marginTop: 18 }} onClick={() => onNavigate('signed-out')}>로그아웃</Button></div></MobileShell>
  )
}

function SupportHubScreen({ onBack, onNavigate }: { onBack: () => void; onNavigate: Navigate }) {
  return (
    <MobileShell title="내 복약 도움" onBack={onBack} hideNavigation><div className="app-scroll"><h1 className="screen-title">적용 중인<br />복약 도움</h1><p className="screen-description">선택한 방법과 다시 확인할 시점을 관리해요.</p><Card><StatusBadge>적용 중</StatusBadge><h2>저녁 식사와 복용 연결하기</h2><p style={{ color: 'var(--ds-text-muted)', lineHeight: 1.6 }}>저녁 식탁을 정리한 직후 약통을 확인하고 20분 뒤 한 번만 재알림을 받아요.</p><Button fullWidth onClick={() => onNavigate('support-review')}>도움 사용 후기 남기기</Button></Card><Button fullWidth variant="secondary" style={{ marginTop: 14 }} onClick={() => onNavigate('support')}>새로운 도움 찾기</Button></div></MobileShell>
  )
}

function SupportReviewScreen({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const [rating, setRating] = useState('')
  return (
    <MobileShell title="도움 사용 후기" onBack={onBack} hideNavigation><div className="app-scroll"><h1 className="screen-title">선택한 방법이<br />도움이 되었나요?</h1><p className="screen-description">평가가 아니라 다음 도움을 더 잘 맞추기 위한 기록이에요.</p><div className="button-stack">{['도움이 되었어요', '조금 바꾸면 좋겠어요', '다른 방법이 필요해요'].map((item) => <Button key={item} fullWidth variant={rating === item ? 'primary' : 'secondary'} onClick={() => setRating(item)}>{item}</Button>)}</div><Button fullWidth disabled={!rating} style={{ marginTop: 20 }} onClick={onDone}>후기 저장</Button></div></MobileShell>
  )
}

function MenuScreen({ onNavigate, hasGuide }: { onNavigate: Navigate; hasGuide: boolean }) {
  return (
    <MobileShell activeNavigation="메뉴" onNavigate={(item) => onNavigate(mainNavigationTarget[item])}>
      <div className="app-scroll">
        <h1 className="screen-title">메뉴</h1>
        <div className="button-stack" style={{ marginTop: 20 }}>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('health-info')}>건강정보 관리</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('ocr-field-review')}>처방전 확인</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate(hasGuide ? 'chat' : 'chat-gate')}>복약 챗봇</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('support')}>맞춤 복약 도움</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('records')}>처방·문서 기록</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('insights')}>복약 리포트</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('lifestyle')}>생활관리 가이드</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('otc')}>일반 의약품 확인</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('notifications')}>복약 알림 설정</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('support-hub')}>내 복약 도움</Button>
          <Button fullWidth variant="secondary" onClick={() => onNavigate('profile')}>내 정보</Button>
          <Button fullWidth variant="ghost" onClick={() => onNavigate('signed-out')}>로그아웃</Button>
        </div>
      </div>
    </MobileShell>
  )
}

function SignedOutScreen({ onLogin }: { onLogin: () => void }) {
  return (
    <MobileShell title="다섯알" hideNavigation>
      <div className="app-scroll" style={{ display: 'grid', alignContent: 'center', textAlign: 'center', paddingBottom: 'calc(24px + var(--ds-safe-bottom))' }}>
        <div aria-hidden="true" style={{ fontSize: 42 }}>✓</div>
        <h1 className="screen-title" style={{ marginTop: 12 }}>로그아웃했어요</h1>
        <p className="screen-description">이 기기에서는 개인 복약정보가 표시되지 않아요.</p>
        <Button fullWidth onClick={onLogin}>다시 로그인하기</Button>
      </div>
    </MobileShell>
  )
}

export default function DesignPrototypePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedScreen = searchParams.get('screen') as PrototypeScreen | null
  const initialScreen = screens.some((item) => item.key === requestedScreen) ? requestedScreen! : 'welcome'
  const [screen, setScreen] = useState<PrototypeScreen>(initialScreen)
  const [scenarioId, setScenarioId] = useState<PrototypeScenario['id']>(getPrototypeScenario(searchParams.get('scenario')).id)
  const [data, setData] = useState<PrototypeData>(emptyPrototypeData)
  const [width, setWidth] = useState<360 | 390 | 412>(390)
  const [keyboardOpen, setKeyboardOpen] = useState(searchParams.get('keyboard') === 'open')
  const screenDefinition = screens.find((item) => item.key === screen) ?? screens[0]
  const scenario = getPrototypeScenario(scenarioId)

  function changeScenario(nextScenario: PrototypeScenario['id']) {
    setScenarioId(nextScenario)
    const next = new URLSearchParams(searchParams)
    next.set('scenario', nextScenario)
    setSearchParams(next, { replace: true })
  }

  function navigate(nextScreen: PrototypeScreen) {
    setScreen(nextScreen)
    const next = new URLSearchParams(searchParams)
    next.set('screen', nextScreen)
    if (nextScreen !== 'chat') {
      setKeyboardOpen(false)
      next.delete('keyboard')
    } else if (keyboardOpen) {
      next.set('keyboard', 'open')
    }
    setSearchParams(next, { replace: true })
  }

  function toggleKeyboard() {
    const nextOpen = !keyboardOpen
    setKeyboardOpen(nextOpen)
    const next = new URLSearchParams(searchParams)
    next.set('screen', 'chat')
    if (nextOpen) next.set('keyboard', 'open')
    else next.delete('keyboard')
    setSearchParams(next, { replace: true })
  }

  function showScreenState(nextScreen: PrototypeScreen) {
    if (nextScreen !== 'chat') {
      navigate(nextScreen)
      return
    }

    setScreen('chat')
    setKeyboardOpen(true)
    const next = new URLSearchParams(searchParams)
    next.set('screen', 'chat')
    next.set('keyboard', 'open')
    setSearchParams(next, { replace: true })
  }

  return (
    <div className="prototype-workbench">
      <aside className="prototype-panel" aria-label="프로토타입 워크벤치">
        <h1>다섯알 전체 여정 + UX 상태</h1>
        <p>시작부터 Post‑MVP까지 전체 제품 여정과 추가 상태를 한 코드에서 검토합니다.</p>
        <button type="button" className="prototype-full-link" onClick={() => navigate('welcome')}>통합 프로토타입 처음부터 보기 →</button>
        <label className="scenario-selector">
          <strong>데이터 시나리오</strong>
          <select value={scenarioId} onChange={(event) => changeScenario(event.target.value as PrototypeScenario['id'])}>
            {prototypeScenarios.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
          <span>{scenario.description}</span>
        </label>
        <details className="journey-inventory data-slot-editor">
          <summary>외부 데이터 슬롯</summary>
          <label>사용자 이름<input value={data.personName} onChange={(event) => setData((current) => ({ ...current, personName: event.target.value }))} placeholder="API 데이터" /></label>
          {medicationFieldKeys.map((key) => (
            <label key={key}>{medicationFieldLabels[key]}<input value={data.medication[key]} onChange={(event) => setData((current) => ({ ...current, medication: { ...current.medication, [key]: event.target.value } }))} placeholder="API 데이터" /></label>
          ))}
        </details>
        <details className="journey-inventory" open>
          <summary>전체 화면 인벤토리</summary>
          {productJourney.map((group) => (
            <div key={group.phase} className="journey-group">
              <strong>{group.phase}</strong>
              <div>
                {group.items.map(([label, target]) => <button type="button" key={target} onClick={() => navigate(target)}>{label}</button>)}
              </div>
            </div>
          ))}
        </details>
        <div className="prototype-control-group">
          <strong>화면 상태</strong>
          {screens.map((item) => (
            <button key={item.key} type="button" className="prototype-control" aria-pressed={screen === item.key} onClick={() => showScreenState(item.key)}>
              {item.label}
            </button>
          ))}
        </div>
        <div className="prototype-control-group">
          <strong>프레임 폭</strong>
          <div className="compact-two-column">
            {([360, 390, 412] as const).map((item) => (
              <button key={item} type="button" className="prototype-control" aria-pressed={width === item} onClick={() => setWidth(item)}>{item}px</button>
            ))}
          </div>
        </div>
        {screen === 'chat' && <button type="button" className="prototype-control" aria-pressed={keyboardOpen} onClick={toggleKeyboard} style={{ width: '100%', marginTop: 14 }}>키보드 {keyboardOpen ? '닫기' : '열기'}</button>}
        <div className="prototype-annotation"><strong>상태 명세</strong><br />{screenDefinition.annotation}<br /><br />사용자 화면에는 Mock·Post-MVP·상태 전환 버튼이 노출되지 않습니다.</div>
      </aside>
      <div className="device-frame" style={{ '--prototype-width': `${width}px` } as CSSProperties}>
        {screen === 'welcome' && <WelcomeScreen onNavigate={navigate} />}
        {screen === 'signup' && <SignupScreen onBack={() => navigate('welcome')} onDone={() => navigate('home')} />}
        {screen === 'home' && <HomeScreen onNavigate={navigate} scenario={scenario} data={data} />}
        {screen === 'upload' && <UploadScreen onBack={() => navigate('home')} onNext={() => navigate('processing')} />}
        {screen === 'processing' && <ProcessingScreen onBack={() => navigate('upload')} onDone={() => navigate(scenario.documentQuality === 'unreadable' ? 'ocr-quality-failure' : 'ocr-field-review')} />}
        {screen === 'ocr-quality-failure' && <OcrQualityFailureScreen onBack={() => navigate('home')} onRetry={() => navigate('upload')} />}
        {screen === 'ocr-field-review' && <OcrFieldReviewScreen onBack={() => navigate('home')} onShowFailure={() => navigate('ocr-quality-failure')} onConfirmed={() => navigate('guide')} scenario={scenario} data={data} />}
        {screen === 'chat' && <ChatScreen keyboardOpen={keyboardOpen} onBack={() => navigate('guide')} onNavigate={navigate} scenario={scenario} />}
        {screen === 'chat-gate' && <ChatGateScreen onBack={() => navigate('home')} onRegister={() => navigate('upload')} />}
        {screen === 'schedule' && <ScheduleScreen onDecision={() => navigate('dose-decision')} onNavigate={navigate} scenario={scenario} data={data} />}
        {screen === 'dose-decision' && <DoseDecisionScreen onBack={() => navigate('schedule')} onBarrier={() => navigate('barrier')} onLater={() => navigate('schedule')} />}
        {screen === 'barrier' && <BarrierScreen onBack={() => navigate('dose-decision')} onNext={() => navigate('support')} />}
        {screen === 'support' && <SupportScreen onBack={() => navigate('schedule')} onPlan={() => navigate('support-plan')} />}
        {screen === 'support-plan' && <SupportPlanScreen onBack={() => navigate('support')} onDone={() => navigate('schedule')} />}
        {screen === 'guide' && <GuideScreen onNavigate={navigate} scenario={scenario} data={data} />}
        {screen === 'health-info' && <HealthInfoScreen onBack={() => navigate('home')} onSaved={() => navigate('guide')} />}
        {screen === 'menu' && <MenuScreen onNavigate={navigate} hasGuide={scenario.hasGuide} />}
        {screen === 'signed-out' && <SignedOutScreen onLogin={() => navigate('welcome')} />}
        {screen === 'records' && <RecordsScreen onNavigate={navigate} scenario={scenario} data={data} />}
        {screen === 'insights' && <InsightsScreen onBack={() => navigate('records')} onSupport={() => navigate('support')} hasRecords={scenario.hasRecords} />}
        {screen === 'lifestyle' && <LifestyleScreen onBack={() => navigate('guide')} />}
        {screen === 'otc' && <OtcScreen onBack={() => navigate('menu')} medicationName={data.medication.name} />}
        {screen === 'profile' && <ProfileScreen onNavigate={navigate} data={data} />}
        {screen === 'notifications' && <NotificationsScreen onBack={() => navigate('menu')} />}
        {screen === 'support-hub' && <SupportHubScreen onBack={() => navigate('menu')} onNavigate={navigate} />}
        {screen === 'support-review' && <SupportReviewScreen onBack={() => navigate('support-hub')} onDone={() => navigate('support-hub')} />}
      </div>
    </div>
  )
}
