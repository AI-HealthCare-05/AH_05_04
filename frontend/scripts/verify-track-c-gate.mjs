// PUBLIC_TRACK_C build 산출물 회귀 검증.
//
// #775 완료 조건은 route 미등록에 그치지 않고 flag=false production build에서 Track C
// page chunk 자체가 사라지는 것이다. 단위 테스트는 route 등록 여부만 확인하므로, gate
// 표현식이 상수로 접히지 않게 바뀌는 회귀(예: 공유 모듈로 추출해 모듈 경계를 넘김)를
// 잡지 못한다. 이 스크립트는 실제 build 산출물을 양방향으로 확인한다.
//
// 사용: pnpm run verify:track-c-gate

import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// flag=false 산출물에 남아서는 안 되는 것들. Track C route·API 경로·화면 문구와 진입 버튼이다.
//
// 'support_medication'은 의도적으로 제외했다. SchedulePage가 이 query parameter 이름을
// 문자열 리터럴로 들고 있어 minifier가 제거하지 못한다. gate가 닫히면 searchParams.get의
// 반환값을 받는 binding 자체가 상수 접기로 사라져 조회 결과는 어디에도 쓰이지 않으므로,
// 남는 것은 동작하지 않는 이름뿐이다. 이름을 지우려면 SchedulePage의 무관한 코드까지
// 재구성해야 해서 검증 대상에서 뺐다.
//
// 이 제외는 docs/deployment.md의 제거 범위 서술과 같은 기준이다. 둘 중 하나만 바꾸면
// 문서가 검증보다 넓게 보장하게 되므로 함께 고친다.
const MARKERS = [
  'track-c',
  'barrier-responses',
  'support-action-plans',
  '복약 안전 확인',
  '이유와 도움 찾기',
]
const VITE = join('node_modules', 'vite', 'bin', 'vite.js')

function build(flag) {
  const outDir = mkdtempSync(join(tmpdir(), `track-c-gate-${flag}-`))
  execFileSync(process.execPath, [VITE, 'build', '--outDir', outDir, '--emptyOutDir'], {
    stdio: 'pipe',
    env: {
      ...process.env,
      VITE_API_BASE_URL: process.env.VITE_API_BASE_URL || 'http://localhost:8000',
      VITE_PUBLIC_TRACK_C: flag,
    },
  })
  return outDir
}

function collect(dir, found = new Map()) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      collect(path, found)
      continue
    }
    const text = readFileSync(path, 'utf8')
    for (const marker of MARKERS) {
      if (text.includes(marker)) {
        found.set(marker, [...(found.get(marker) ?? []), name])
      }
    }
    if (name.includes('TrackCPage')) found.set('TrackCPage chunk', [name])
  }
  return found
}

const failures = []

const closed = build('false')
const leaked = collect(closed)
rmSync(closed, { recursive: true, force: true })
if (leaked.size > 0) {
  for (const [marker, files] of leaked) {
    failures.push(`VITE_PUBLIC_TRACK_C=false 산출물에 "${marker}"가 남아 있습니다: ${files.join(', ')}`)
  }
}

const open = build('true')
const present = collect(open)
rmSync(open, { recursive: true, force: true })
if (!present.has('TrackCPage chunk')) {
  failures.push('VITE_PUBLIC_TRACK_C=true 산출물에 TrackCPage chunk가 없습니다. gate가 항상 닫혀 있습니다.')
}

if (failures.length > 0) {
  console.error('PUBLIC_TRACK_C gate 검증 실패')
  for (const failure of failures) console.error(`  - ${failure}`)
  process.exit(1)
}

console.log('PUBLIC_TRACK_C gate 검증 통과: false 산출물에서 Track C 제거, true 산출물에 포함 확인')
