import { NextResponse, type NextRequest } from 'next/server'
import { sanitizeReturnTo } from '@/lib/sanitize-return-to'

const SESSION_COOKIE_NAME = 'twaky_session'

export function middleware(req: NextRequest) {
    const { pathname } = req.nextUrl

    // /api/* and /oauth/* are proxied to twaky-api by next.config.js rewrites.
    // Don't intercept them.
    if (pathname.startsWith('/api/') || pathname.startsWith('/oauth/')) {
        return NextResponse.next()
    }

    // Presence check only — signature is validated server-side by twaky-api.
    // We deliberately don't decode the cookie value (it's HttpOnly).
    if (req.cookies.has(SESSION_COOKIE_NAME)) {
        return NextResponse.next()
    }

    // No session — redirect to OIDC login with the current path as return_to.
    const returnTo = sanitizeReturnTo(pathname + req.nextUrl.search)
    const loginUrl = new URL(
        `/api/oauth/login?return_to=${encodeURIComponent(returnTo)}`,
        req.url,
    )
    return NextResponse.redirect(loginUrl)
}

export const config = {
    matcher: [
        // Match everything except Next.js internal + favicon.
        '/((?!_next/static|_next/image|favicon.ico|robots.txt).*)',
    ],
}
