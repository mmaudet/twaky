import { describe, expect, it, vi } from 'vitest'
import { formatSessionExpiry } from './format-session-expiry'

describe('formatSessionExpiry', () => {
    it('returns hours + minutes for long remaining time', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        // login was 30 min ago, TTL 8h → 7h 30m remaining
        const loginAt = Date.now() - 30 * 60 * 1000
        expect(formatSessionExpiry(loginAt, 8 * 3600)).toBe('7h 30m')
        vi.useRealTimers()
    })
    it('returns minutes when < 1h remains', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        const loginAt = Date.now() - 7.5 * 3600 * 1000  // 7.5h ago, TTL 8h → 30m
        expect(formatSessionExpiry(loginAt, 8 * 3600)).toBe('30m')
        vi.useRealTimers()
    })
    it('returns "expired" when past TTL', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        const loginAt = Date.now() - 9 * 3600 * 1000
        expect(formatSessionExpiry(loginAt, 8 * 3600)).toBe('expired')
        vi.useRealTimers()
    })
})
