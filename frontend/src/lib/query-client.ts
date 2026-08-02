import { QueryCache, QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ApiError } from './api-error'
import { sanitizeReturnTo } from './sanitize-return-to'

/**
 * Handles a 401 from any authenticated call: redirect to /oauth/login with
 * the current path as return_to. Called from both query and mutation error paths.
 */
function handleUnauthorized(): void {
    if (typeof window === 'undefined') return
    const returnTo = sanitizeReturnTo(window.location.pathname + window.location.search)
    window.location.href = `/api/oauth/login?return_to=${encodeURIComponent(returnTo)}`
}

export function createQueryClient(): QueryClient {
    return new QueryClient({
        queryCache: new QueryCache({
            onError: (error) => {
                if (error instanceof ApiError && error.code === 'http_401') {
                    toast.error('Session expired, redirecting...')
                    handleUnauthorized()
                    return
                }
                // Silent — component-level error UIs surface the message.
            },
        }),
        defaultOptions: {
            queries: {
                retry: (failureCount, error) => {
                    if (error instanceof ApiError) {
                        // Don't retry client errors — retry only server/network.
                        const code = error.code
                        if (['http_401', 'http_403', 'http_404', 'http_409', 'http_422']
                                .includes(code)) return false
                    }
                    return failureCount < 2
                },
                staleTime: 0,  // SSE drives invalidation; no need for staleTime cache.
            },
            mutations: {
                onError: (error) => {
                    if (error instanceof ApiError && error.code === 'http_401') {
                        toast.error('Session expired, redirecting...')
                        handleUnauthorized()
                        return
                    }
                    toast.error(error instanceof Error ? error.message : String(error))
                },
            },
        },
    })
}
