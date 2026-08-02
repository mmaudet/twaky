import { describe, expect, it } from 'vitest'
import { sanitizeReturnTo } from './sanitize-return-to'

describe('sanitizeReturnTo', () => {
    it('accepts a normal local path', () => {
        expect(sanitizeReturnTo('/missions')).toBe('/missions')
    })
    it('rejects an absolute URL', () => {
        expect(sanitizeReturnTo('https://evil.com/x')).toBe('/')
    })
    it('rejects a protocol-relative URL', () => {
        expect(sanitizeReturnTo('//evil.com/x')).toBe('/')
    })
    it('rejects backslash', () => {
        expect(sanitizeReturnTo('/valid\\path')).toBe('/')
    })
    it('rejects empty string', () => {
        expect(sanitizeReturnTo('')).toBe('/')
    })
    it('accepts path with query string', () => {
        expect(sanitizeReturnTo('/missions?state=running')).toBe('/missions?state=running')
    })
})
