import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const frontendRoot = resolve(import.meta.dirname, '..')

describe('PWA 정적 자산', () => {
  it('installable manifest와 단일 Service Worker 진입점을 연결한다', () => {
    const html = readFileSync(resolve(frontendRoot, 'index.html'), 'utf8')
    const manifest = JSON.parse(readFileSync(resolve(frontendRoot, 'public/manifest.webmanifest'), 'utf8')) as Record<string, unknown>

    expect(html).toContain('rel="manifest" href="/manifest.webmanifest"')
    expect(manifest).toMatchObject({ start_url: '/', scope: '/', display: 'standalone' })
    expect(manifest.icons).toEqual(expect.arrayContaining([
      expect.objectContaining({ sizes: '192x192', type: 'image/png' }),
      expect.objectContaining({ sizes: '512x512', type: 'image/png' }),
    ]))
    const icon192 = readFileSync(resolve(frontendRoot, 'public/icons/dosey-192.png'))
    const icon512 = readFileSync(resolve(frontendRoot, 'public/icons/dosey-512.png'))
    expect([icon192.readUInt32BE(16), icon192.readUInt32BE(20)]).toEqual([192, 192])
    expect([icon512.readUInt32BE(16), icon512.readUInt32BE(20)]).toEqual([512, 512])
  })

  it('SW는 generation 일치 후 일반 문구만 표시하고 notification id로 앱 알림 route만 연다', () => {
    const worker = readFileSync(resolve(frontendRoot, 'public/sw.js'), 'utf8')

    expect(worker).toContain("const GENERIC_TITLE = '복약 기록 알림'")
    expect(worker).toContain("const GENERIC_BODY = '앱에서 기록을 확인해 주세요.'")
    expect(worker).toContain("new URL('/notifications', self.location.origin)")
    expect(worker).toContain('currentGeneration !== payload.generation')
    expect(worker).not.toContain('medication_name')
    expect(worker).not.toContain('dose_value')
    expect(worker).not.toContain('diagnosis')
    expect(worker).not.toContain('/read')
    expect(worker).not.toContain('checkin')
  })
})
