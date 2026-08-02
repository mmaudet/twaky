'use client'

import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { useMe } from '@/hooks/use-me'
import { formatSessionExpiry } from '@/lib/format-session-expiry'

const SESSION_TTL_SECONDS = 8 * 60 * 60

// LoginTimeTracker in the root layout initialises twaky_login_at on first page
// load site-wide. We only read it here so the expiry is accurate regardless of
// which page the user visited first.
function useSessionExpiry(): string {
    const [loginAt] = useState<number>(() => {
        const stored = typeof window !== 'undefined'
            ? sessionStorage.getItem('twaky_login_at')
            : null
        // Fall back to now if the tracker hasn't run yet (e.g. SSR or unit tests).
        return stored ? Number(stored) : Date.now()
    })
    const [display, setDisplay] = useState<string>(() =>
        formatSessionExpiry(loginAt, SESSION_TTL_SECONDS),
    )
    useEffect(() => {
        const iv = setInterval(() => {
            setDisplay(formatSessionExpiry(loginAt, SESSION_TTL_SECONDS))
        }, 60_000)
        return () => clearInterval(iv)
    }, [loginAt])
    return display
}

export default function MePage() {
    const { data: me, isLoading } = useMe()
    const expiry = useSessionExpiry()

    function handleSignOut() {
        const form = document.createElement('form')
        form.method = 'POST'
        form.action = '/api/oauth/logout'
        document.body.appendChild(form)
        form.submit()
    }

    return (
        <div className="mx-auto max-w-md pt-8">
            <Card>
                <CardContent className="space-y-4 py-6 text-center">
                    <p className="text-sm text-muted-foreground">Signed in as</p>
                    <p className="text-xl font-medium">
                        {isLoading ? '…' : (me?.owner_email ?? 'unknown')}
                    </p>
                    <p className="text-sm text-muted-foreground">
                        Session expires in {expiry}
                    </p>
                    {me?.langfuse_base_url && (
                        <p className="text-sm">
                            <a
                                href={me.langfuse_base_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-primary hover:underline"
                            >
                                Langfuse ↗
                            </a>
                        </p>
                    )}
                    <div className="pt-2">
                        <Button variant="outline" onClick={handleSignOut}>
                            Sign out
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}
