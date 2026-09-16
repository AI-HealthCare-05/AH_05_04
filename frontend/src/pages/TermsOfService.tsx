function TermsOfService() {
  const approved = import.meta.env.VITE_SIGNUP_TERMS_APPROVED === 'true'
  return (
    <>
      {!approved && <aside
        className="notice attention mvp-signup-legal__review"
        role="note"
        aria-labelledby="terms-review-title"
      >
        <strong id="terms-review-title">승인 전 초안 · 검토용</strong>
        <p>
          최종 법무/Privacy 승인 전 문서입니다. 현재 사용자의 확정 필수 동의 계약으로 사용되지 않습니다.
        </p>
      </aside>}
      <article
        className="mvp-signup-legal__document"
        aria-labelledby="terms-of-service-title"
        aria-describedby={approved ? undefined : 'terms-review-title'}
      >
      <header className="mvp-signup-legal__header">
        <h2 id="terms-of-service-title">2. 이용약관</h2>
        <p><strong>시행일자: 2026년 09월 15일</strong></p>
      </header>

      <section aria-labelledby="terms-article-1">
        <h3 id="terms-article-1">제1조 (목적)</h3>
        <p>
          이 약관은 회사명이 제공하는 AI 기반 복약관리 서비스 &quot;Dosey(오즈)&quot;(이하 &quot;서비스&quot;)의 이용과 관련하여 회사와 이용자 간의 권리, 의무 및 책임사항을 규정함을 목적으로 합니다.
        </p>
      </section>

      <section aria-labelledby="terms-article-2">
        <h3 id="terms-article-2">제2조 (서비스의 내용)</h3>
        <ol>
          <li>이메일 기반 회원가입 및 로그인, 본인(SELF) 프로필 관리</li>
          <li>처방전 이미지·PDF 업로드 및 OCR을 통한 약품명·함량·용량·횟수·복용 시점·기간 인식, 사용자 검수 및 처방 확정</li>
          <li>공식 의약품(식품의약품안전처 승인 정보) 식별 및 근거 기반(RAG) AI 복약 가이드 생성·제공</li>
          <li>복약 관련 실시간 AI 챗봇 상담(처방약 질의 및 일반의약품(OTC) 성분 상호작용 확인)</li>
          <li>생활 스케줄 기반 복약 일정 추천·확정 및 제품 내 알림(Reminder)</li>
          <li>복약 이행 기록(Check-in), 선택형 증상 기록, 복약 어려움(Barrier) 분류 및 맞춤 지원(Support) 정보 제공</li>
          <li>복약 리포트(7일·30일 집계) 제공 및 &quot;진료 시 보여주기&quot; 화면 제공</li>
        </ol>
      </section>

      <section aria-labelledby="terms-article-3">
        <h3 id="terms-article-3">제3조 (의료 정보 제공에 관한 면책 및 주의사항) — 핵심 조항</h3>
        <ol>
          <li>서비스가 제공하는 복약 가이드, 챗봇 응답(일반의약품 상호작용 확인 포함), 복약 일정 추천은 인공지능(AI) 및 규칙 기반 로직이 생성한 참고용 정보이며, 의료법상 진단·처방·치료 행위에 해당하지 않습니다.</li>
          <li>회사는 AI가 생성한 정보의 완전성·정확성을 보장하지 않으며, 이용자는 실제 복약 방법 변경, 증상 판단, 응급 상황 대응은 반드시 의사·약사 등 의료 전문가와 상담해야 합니다.</li>
          <li>서비스는 사용자가 확정하지 않은 처방 정보를 기반으로 가이드·일정을 생성하지 않으며, 확정 전 정보를 확정 정보처럼 제공하지 않습니다. 복약 일정 추천은 처방 자체를 변경하지 않으며, 횟수·용량·복용 조건은 원 처방을 따릅니다.</li>
          <li>챗봇은 진단, 처방 변경 권고, 복용 중단·증량·감량 지시 등 의료 전문가의 판단을 대체하는 답변을 제공하지 않도록 설계되어 있으나, AI 응답 특성상 완전한 차단을 보장할 수 없으므로 이용자는 이를 최종적인 의학적 판단으로 신뢰해서는 안 됩니다.</li>
          <li>일반의약품 상호작용 확인 결과에서 &quot;확인된 근거 없음&quot;은 상호작용이 없다는 의미가 아니며, 제품·성분 식별에 실패하거나 근거가 부족한 경우 임의로 안전하다고 판단해서는 안 됩니다.</li>
          <li>선택형 증상 기록은 사용자가 스스로 기록한 사실의 저장일 뿐이며, 서비스는 증상의 원인이나 특정 약물과의 인과관계를 판단하지 않습니다. &quot;진료 시 보여주기&quot; 화면 역시 사용자가 기록한 내용을 재구성해 보여주는 것으로, 의료 기록이나 회사의 의학적 판단이 아닙니다.</li>
          <li>응급 증상이 의심되는 경우 서비스 이용 대신 즉시 119 또는 의료기관에 연락해야 합니다.</li>
        </ol>
      </section>

      <section aria-labelledby="terms-article-4">
        <h3 id="terms-article-4">제4조 (회원가입 및 계정 관리)</h3>
        <ol>
          <li>이용자는 이메일과 비밀번호를 이용하여 회원가입을 신청하며, 회사가 이를 승낙함으로써 이용계약이 체결됩니다.</li>
          <li>이용자는 본인의 계정 정보를 제3자에게 제공하거나 공유해서는 안 되며, 계정 도용 등 비인가 사용을 인지한 경우 즉시 회사에 통지해야 합니다.</li>
        </ol>
      </section>

      <section aria-labelledby="terms-article-5">
        <h3 id="terms-article-5">제5조 (이용자의 의무)</h3>
        <ol>
          <li>이용자는 처방전 정보를 정확히 업로드·확인·확정해야 하며, 고의로 허위 정보를 입력해서는 안 됩니다.</li>
          <li>이용자는 타인의 처방 정보 또는 계정에 무단으로 접근해서는 안 됩니다.</li>
          <li>이용자는 서비스를 통해 얻은 정보를 본인 복약 관리 목적 외로 상업적으로 이용해서는 안 됩니다.</li>
        </ol>
      </section>

      <section aria-labelledby="terms-article-6">
        <h3 id="terms-article-6">제6조 (서비스의 변경 및 중단)</h3>
        <p>
          회사는 운영상·기술상 필요에 따라 서비스의 전부 또는 일부를 변경하거나 중단할 수 있으며, 이 경우 사전에 공지합니다. 다만 긴급한 보안상의 사유가 있는 경우 사후에 통지할 수 있습니다.
        </p>
      </section>

      <section aria-labelledby="terms-article-7">
        <h3 id="terms-article-7">제7조 (책임의 제한)</h3>
        <ol>
          <li>회사는 천재지변, 불가항력적 사유로 서비스를 제공할 수 없는 경우 책임이 면제됩니다.</li>
          <li>회사는 이용자가 AI 생성 정보에만 의존하여 발생한 건강상 피해에 대해 관련 법령이 허용하는 범위 내에서 책임을 제한할 수 있으며, 이는 회사의 고의 또는 중과실로 인한 손해에는 적용되지 않습니다.</li>
          <li>이 조항은 소비자 보호 관련 강행법규에 반하지 않는 범위 내에서 적용됩니다.</li>
        </ol>
      </section>

      <section aria-labelledby="terms-article-8">
        <h3 id="terms-article-8">제8조 (계약 해지 및 탈퇴)</h3>
        <p>
          이용자는 언제든지 서비스 내 기능을 통해 탈퇴를 요청할 수 있으며, 회사는 관계 법령 및 개인정보처리방침에 따라 이용자 정보를 처리합니다.
        </p>
      </section>

      <section aria-labelledby="terms-article-9">
        <h3 id="terms-article-9">제9조 (분쟁 해결)</h3>
        <p>
          이 약관과 관련하여 회사와 이용자 간 분쟁이 발생한 경우, 양 당사자는 원만한 해결을 위해 노력하며, 해결되지 않을 경우 [관할 법원 – 예: 회사 소재지 관할 법원]을 전속 관할 법원으로 합니다.
        </p>
      </section>

      <section aria-labelledby="terms-article-10">
        <h3 id="terms-article-10">제10조 (약관의 개정)</h3>
        <p>
          회사는 관계 법령을 위배하지 않는 범위에서 약관을 개정할 수 있으며, 개정 시 적용일자 및 개정사유를 명시하여 최소 7일 전(이용자에게 불리한 변경은 30일 전)부터 공지합니다.
        </p>
      </section>
      </article>
    </>
  )
}

export default TermsOfService
