'use client'

import { useSearchParams } from 'next/navigation'
import { Button } from '@/components/ui/button'
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from '@/components/ui/card'
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import {
    useMailSentinelAuth,
    useForceRefresh,
    useDisconnect,
} from '@/hooks/use-mail-sentinel-auth'

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildConnectUrl(): string {
    const returnTo = encodeURIComponent(window.location.pathname + '?tab=auth')
    return `/api/oauth/jmap/login?return_to=${returnTo}`
}

function formatMinutesUntil(iso: string | null | undefined): string {
    if (!iso) return '—'
    const diff = Math.round((new Date(iso).getTime() - Date.now()) / 60_000)
    if (diff <= 0) return 'expired'
    return `${diff} minute${diff === 1 ? '' : 's'}`
}

function formatRelative(iso: string | null | undefined): string {
    if (!iso) return '—'
    const diff = Math.round((Date.now() - new Date(iso).getTime()) / 60_000)
    if (diff < 1) return 'just now'
    if (diff < 60) return `${diff} minute${diff === 1 ? '' : 's'} ago`
    const hours = Math.round(diff / 60)
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
    const days = Math.round(hours / 24)
    return `${days} day${days === 1 ? '' : 's'} ago`
}

// ── Error banner ──────────────────────────────────────────────────────────────

function ErrorBanner({ reason }: { reason: string }) {
    return (
        <div
            role="alert"
            className="mb-4 rounded-lg border border-red-300 bg-red-50 p-4 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
        >
            <p className="font-medium">OAuth error</p>
            <p className="mt-1 text-sm">{reason}</p>
            <Button
                className="mt-3"
                size="sm"
                variant="destructive"
                onClick={() => window.location.assign(buildConnectUrl())}
            >
                Retry
            </Button>
        </div>
    )
}

// ── AuthTab ───────────────────────────────────────────────────────────────────

export function AuthTab() {
    const { data, isLoading, error } = useMailSentinelAuth()
    const refresh = useForceRefresh()
    const disconnect = useDisconnect()
    const searchParams = useSearchParams()

    const oauthStatus = searchParams.get('status')
    const oauthReason = searchParams.get('reason') ?? 'Unknown error'

    if (isLoading) {
        return (
            <div className="p-4 text-muted-foreground" aria-label="loading">
                Loading…
            </div>
        )
    }

    if (error) {
        return (
            <p className="p-4 text-red-600">Error: {error.message}</p>
        )
    }

    return (
        <div className="space-y-4">
            {oauthStatus === 'error' && <ErrorBanner reason={oauthReason} />}

            {!data?.connected ? (
                // ── Not connected ────────────────────────────────────────────
                <Card>
                    <CardHeader>
                        <CardTitle>Connect your JMAP mailbox</CardTitle>
                        <CardDescription>
                            The mail sentinel needs access to your JMAP mailbox to monitor
                            and triage incoming messages.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Button
                            onClick={() => window.location.assign(buildConnectUrl())}
                        >
                            Connect JMAP account
                        </Button>
                    </CardContent>
                </Card>
            ) : (
                // ── Connected ────────────────────────────────────────────────
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <span
                                className="h-2.5 w-2.5 rounded-full bg-green-500"
                                aria-hidden="true"
                            />
                            {data.account_email ?? 'connected'}
                        </CardTitle>
                        <CardDescription>
                            {data.provider ? `Provider: ${data.provider}` : null}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <p className="text-sm text-muted-foreground">
                            Access token refreshes in{' '}
                            <span className="font-medium text-foreground">
                                {formatMinutesUntil(data.access_token_expires_at)}
                            </span>
                        </p>

                        {data.last_refresh_error ? (
                            <div
                                role="alert"
                                className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
                            >
                                Last refresh failed: {data.last_refresh_error}
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Last refresh:{' '}
                                <span className="font-medium text-foreground">
                                    {formatRelative(data.last_refresh_at)}
                                </span>
                            </p>
                        )}

                        <div className="flex flex-wrap gap-2 pt-2">
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={refresh.isPending}
                                onClick={() => refresh.mutate()}
                            >
                                Force refresh
                            </Button>

                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => window.location.assign(buildConnectUrl())}
                            >
                                Reconnect
                            </Button>

                            <AlertDialog>
                                <AlertDialogTrigger asChild>
                                    <Button
                                        size="sm"
                                        variant="destructive"
                                        disabled={disconnect.isPending}
                                    >
                                        Disconnect
                                    </Button>
                                </AlertDialogTrigger>
                                <AlertDialogContent>
                                    <AlertDialogHeader>
                                        <AlertDialogTitle>Disconnect JMAP account?</AlertDialogTitle>
                                        <AlertDialogDescription>
                                            This will delete your OAuth credentials. The mail sentinel
                                            will stop monitoring your mailbox until you reconnect.
                                        </AlertDialogDescription>
                                    </AlertDialogHeader>
                                    <AlertDialogFooter>
                                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                                        <AlertDialogAction
                                            onClick={() => disconnect.mutate()}
                                        >
                                            Disconnect
                                        </AlertDialogAction>
                                    </AlertDialogFooter>
                                </AlertDialogContent>
                            </AlertDialog>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    )
}
