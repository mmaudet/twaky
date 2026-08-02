/**
 * Wraps the API's uniform error envelope in a throwable Error.
 *
 * Every non-2xx from twaky-api returns:
 *     {"error": {"code": "http_401", "message": "...", "detail": {...}}}
 * (See spec §4.5.)
 */
export interface ErrorEnvelope {
    error: {
        code: string
        message: string
        detail?: unknown
    }
}

export class ApiError extends Error {
    constructor(public envelope: ErrorEnvelope) {
        super(envelope.error.message)
        this.name = 'ApiError'
    }

    get code(): string {
        return this.envelope.error.code
    }
}

/**
 * Type guard for the envelope shape (some errors may not follow the contract,
 * e.g. 502 Bad Gateway from Traefik).
 */
export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
    if (typeof value !== 'object' || value === null) return false
    const v = value as { error?: unknown }
    if (typeof v.error !== 'object' || v.error === null) return false
    const e = v.error as { code?: unknown; message?: unknown }
    return typeof e.code === 'string' && typeof e.message === 'string'
}
