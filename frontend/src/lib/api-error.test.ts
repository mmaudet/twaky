import { describe, expect, it } from 'vitest'
import { ApiError, isErrorEnvelope } from './api-error'

describe('ApiError', () => {
    it('exposes code and message', () => {
        const err = new ApiError({
            error: { code: 'http_401', message: 'unauthorized' },
        })
        expect(err.code).toBe('http_401')
        expect(err.message).toBe('unauthorized')
    })
})

describe('isErrorEnvelope', () => {
    it('accepts a valid envelope', () => {
        expect(isErrorEnvelope({ error: { code: 'x', message: 'y' } })).toBe(true)
    })
    it('rejects null', () => {
        expect(isErrorEnvelope(null)).toBe(false)
    })
    it('rejects missing code', () => {
        expect(isErrorEnvelope({ error: { message: 'y' } })).toBe(false)
    })
})
