import createClient from 'openapi-fetch'
import type { paths } from './api-types'

/**
 * openapi-fetch client, typed against the generated OpenAPI schema.
 *
 * baseUrl is '/api' — relative, meaning requests go to the same origin
 * (the Next.js server), which rewrites them to twaky-api (see next.config.ts).
 */
export const api = createClient<paths>({ baseUrl: '/api' })
