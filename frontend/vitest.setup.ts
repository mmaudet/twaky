import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'

// Polyfill ResizeObserver for Radix UI components (Slider uses @radix-ui/react-use-size)
// jsdom does not implement ResizeObserver natively.
global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
}
// Polyfill global.fetch with undici so MSW 2.x can intercept openapi-fetch
// calls in the jsdom environment. jsdom ships its own fetch polyfill that MSW
// does not wrap; undici is the same fetch implementation Node 18+ uses
// natively and MSW's setupServer knows how to intercept it.
//
// Additionally, wrap globalThis.Request so that relative URLs (e.g. /api/me)
// are resolved against the jsdom origin (http://localhost:3000) before being
// passed to undici's Request constructor, which requires absolute URLs in Node.
import { fetch, Headers, Request as UndiciRequest, Response } from 'undici'

const _OriginalRequest = UndiciRequest
class PatchedRequest extends _OriginalRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
        if (typeof input === 'string' && input.startsWith('/')) {
            input = `http://localhost:3000${input}`
        }
        super(input as string, init as import('undici').RequestInit)
    }
}

Object.assign(globalThis, {
    fetch,
    Headers,
    Request: PatchedRequest,
    Response,
})

import { server } from './src/test/mocks/server'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
