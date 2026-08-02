'use client'

import { useEffect } from 'react'

/**
 * Persists the login timestamp to sessionStorage on first page load site-wide.
 * Mounted in the root layout so that /me's session-expiry countdown is accurate
 * regardless of which page the user visits first.
 */
export function LoginTimeTracker() {
    useEffect(() => {
        if (!sessionStorage.getItem('twaky_login_at')) {
            sessionStorage.setItem('twaky_login_at', String(Date.now()))
        }
    }, [])
    return null
}
