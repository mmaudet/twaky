import { describe, expect, it } from 'vitest'
import { NextRequest } from 'next/server'
import { middleware } from './middleware'

function makeReq(pathname: string, cookies: Record<string, string> = {}): NextRequest {
    const url = `https://twaky.example.com${pathname}`
    const cookieHeader = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ')
    return new NextRequest(url, {
        headers: cookieHeader ? { cookie: cookieHeader } : {},
    })
}

describe('middleware', () => {
    it('passes through /api/* without auth check', () => {
        const res = middleware(makeReq('/api/missions'))
        expect(res.headers.get('location')).toBeNull()
        // NextResponse.next() has status 200 (default)
        expect(res.status).toBe(200)
    })
    it('passes through /oauth/* without auth check', () => {
        const res = middleware(makeReq('/oauth/login'))
        expect(res.headers.get('location')).toBeNull()
        expect(res.status).toBe(200)
    })
    it('redirects to /oauth/login when cookie is missing', () => {
        const res = middleware(makeReq('/missions'))
        expect(res.status).toBe(307)
        const loc = res.headers.get('location')
        expect(loc).toContain('/api/oauth/login')
        expect(loc).toContain('return_to=%2Fmissions')
    })
    it('lets through when cookie is present (any value)', () => {
        const res = middleware(makeReq('/missions', { twaky_session: 'anything' }))
        expect(res.status).toBe(200)
    })
    it('sanitizes malicious return_to', () => {
        // A path like //evil.com/x is sanitized to /
        const res = middleware(makeReq('//evil.com/x'))
        const loc = res.headers.get('location')
        expect(loc).toContain('return_to=%2F')
    })
})
