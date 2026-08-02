import createClient from 'openapi-fetch'
import type { paths } from './api-types'

/**
 * openapi-fetch client, typed against the generated OpenAPI schema.
 *
 * baseUrl is '/api' — relative, meaning requests go to the same origin
 * (the Next.js server), which rewrites them to twaky-api (see next.config.ts).
 *
 * The dynamic fetch wrapper ensures the client always delegates to the current
 * globalThis.fetch at call time rather than capturing it at module-init time.
 * This matters in tests where MSW replaces globalThis.fetch after module load.
 */
export const api = createClient<paths>({ baseUrl: '/api', fetch: (req: Request) => globalThis.fetch(req) })
