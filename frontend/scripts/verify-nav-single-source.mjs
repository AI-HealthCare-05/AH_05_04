// bottom-nav 스타일 단일 출처 회귀 검증.
//
// 하단 네비게이션은 MobileShell이 렌더하는 컴포넌트 하나인데, 스타일은 한때 네 곳에
// 흩어져 있었다. prototype.css의 기본값, 같은 파일의 페이지 클래스 allowlist,
// SchedulePage.css의 복붙 중복, ChatPage.css의 독자 변형이다. 밀도를 컴포넌트 prop이
// 아니라 페이지 클래스 목록으로 제어하는 구조였기 때문에, 화면을 추가할 때마다
// allowlist에 넣는 것을 잊으면 그 화면만 다른 네비게이션을 받았다.
//
// 지금은 prototype.css 한 곳이 모양을 전부 정의한다. 페이지 CSS가 다시 모양을 덮어쓰면
// 같은 분기가 되살아나므로 이 스크립트가 막는다. 다만 배치는 화면마다 다르다. nav가
// 스크롤 영역 위에 떠 있는 화면(absolute)과 flex 컬럼의 마지막 아이템인 화면(relative)이
// 공존하므로, 아래 PLACEMENT_PROPERTIES에 한해 페이지 CSS의 override를 허용한다.
// 즉 "어디에 놓이는지"는 페이지가, "어떻게 생겼는지"는 design-system이 정한다.
//
// 사용: pnpm run verify:nav-single-source

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = 'src'
const OWNER = join('src', 'design-system')

// 배치 전용. 크기·색·여백·타이포그래피는 여기 없으므로 페이지에서 덮어쓸 수 없다.
const PLACEMENT_PROPERTIES = new Set([
  'position',
  'inset',
  'top',
  'right',
  'bottom',
  'left',
  'flex',
  'flex-grow',
  'flex-shrink',
  'flex-basis',
  'order',
  'z-index',
])

function cssFiles(dir, found = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      cssFiles(path, found)
      continue
    }
    if (name.endsWith('.css')) found.push(path)
  }
  return found
}

// 중첩 없는 평평한 CSS를 전제로 한 최소 파서. at-rule 안의 규칙도 같은 모양이라 그대로 걸린다.
function rules(text) {
  const parsed = []
  const pattern = /([^{}]+)\{([^{}]*)\}/g
  let match
  while ((match = pattern.exec(text)) !== null) {
    const selector = match[1].trim()
    if (!selector || selector.startsWith('@')) continue
    parsed.push({
      selector,
      body: match[2],
      line: text.slice(0, match.index).split('\n').length,
    })
  }
  return parsed
}

function declarations(body) {
  return body
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split(';')
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => entry.slice(0, entry.indexOf(':')).trim().toLowerCase())
    .filter(Boolean)
}

const failures = []

for (const file of cssFiles(SRC)) {
  if (file.startsWith(OWNER)) continue
  for (const rule of rules(readFileSync(file, 'utf8'))) {
    if (!rule.selector.includes('.bottom-nav')) continue
    const offending = declarations(rule.body).filter(
      (property) => !PLACEMENT_PROPERTIES.has(property),
    )
    if (offending.length === 0) continue
    failures.push(
      `${relative('.', file)}:${rule.line} "${rule.selector}"가 배치 외 속성을 덮어씁니다: ${offending.join(', ')}`,
    )
  }
}

if (failures.length > 0) {
  console.error('bottom-nav 스타일 단일 출처 검증 실패')
  for (const failure of failures) console.error(`  - ${failure}`)
  console.error('')
  console.error('  모양은 src/design-system/prototype.css 한 곳에서만 정의합니다.')
  console.error('  화면별로 달라야 하는 것이 정말 있다면 페이지 CSS가 아니라')
  console.error('  MobileShell에 명시적인 variant를 추가해 주세요.')
  process.exit(1)
}

console.log('bottom-nav 스타일 단일 출처 검증 통과: 페이지 CSS에 모양 override 없음')
