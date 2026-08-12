'use client'

import { useState } from 'react'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
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
import { useSentinel, usePatchSentinel } from '@/hooks/use-sentinels'
import {
    useSpamDecisions,
    useSpamStats,
    useRestoreSpam,
    type SpamDecision,
} from '@/hooks/use-mail-sentinel-spam'

// ── Known mailbox roles ───────────────────────────────────────────────────────

const KNOWN_ROLES = new Set([
    'inbox',
    'newsletter',
    'archive',
    'drafts',
    'sent',
    'trash',
    'junk',
])

// ── OriginCell ────────────────────────────────────────────────────────────────

function OriginCell({
    role,
    id,
}: {
    role: string | null | undefined
    id: string | null | undefined
}) {
    if (role && KNOWN_ROLES.has(role)) {
        return <Badge variant="secondary">{role}</Badge>
    }
    if (!role && id) {
        return (
            <code title={id}>
                {id.slice(0, 8)}&hellip;
            </code>
        )
    }
    return (
        <span
            className="text-muted-foreground"
            title="Provenance not captured (decision predates SP6d capture or migration pending)"
        >
            &mdash;
        </span>
    )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function bucketIcon(bucket: string): string {
    switch (bucket) {
        case 'phishing-alert':
            return '🔴' // 🔴
        case 'newsletter':
            return '📰' // 📰
        default:
            return '🟠' // 🟠
    }
}

function formatRelative(iso: string): string {
    const diff = Math.round((Date.now() - new Date(iso).getTime()) / 60_000)
    if (diff < 1) return 'just now'
    if (diff < 60) return `${diff}m ago`
    const hours = Math.round(diff / 60)
    if (hours < 24) return `${hours}h ago`
    const days = Math.round(hours / 24)
    return `${days}d ago`
}

function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
    })
}

// ── RestoreButton ─────────────────────────────────────────────────────────────

function RestoreButton({ decision }: { decision: SpamDecision }) {
    const restore = useRestoreSpam()

    if (decision.restored_at) {
        return (
            <span className="text-sm text-muted-foreground">
                Restored on {formatDate(decision.restored_at)}
            </span>
        )
    }

    return (
        <AlertDialog>
            <AlertDialogTrigger asChild>
                <Button size="sm" variant="outline" disabled={restore.isPending}>
                    Restore
                </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Restore this email?</AlertDialogTitle>
                    <AlertDialogDescription>
                        Restore this email to your inbox? It will be marked as not-spam
                        and re-appear in your inbox.
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                        onClick={() => restore.mutate(decision.id)}
                    >
                        Restore
                    </AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    )
}

// ── RecentSpamTab ─────────────────────────────────────────────────────────────

export function RecentSpamTab() {
    const [cursorBefore, setCursorBefore] = useState<string | undefined>(
        undefined,
    )
    const [prevCursors, setPrevCursors] = useState<string[]>([])

    const { data: sentinel, isLoading: sentinelLoading } = useSentinel('mail')
    const patchSentinel = usePatchSentinel('mail')

    const spamFilterEnabled =
        (sentinel?.config_values?.spam_filter_enabled as boolean | undefined) ??
        false

    const { data: stats } = useSpamStats(30)
    const {
        data: decisions,
        isLoading: decisionsLoading,
    } = useSpamDecisions({ limit: 50, before: cursorBefore, withProvenance: true })

    // ── Toggle handler ────────────────────────────────────────────────────────

    function handleToggle(checked: boolean) {
        patchSentinel.mutate({
            config_values: {
                ...(sentinel?.config_values ?? {}),
                spam_filter_enabled: checked,
            },
        })
    }

    // ── Pagination ────────────────────────────────────────────────────────────

    function handleNext() {
        if (!decisions || decisions.length === 0) return
        const last = decisions[decisions.length - 1]
        setPrevCursors((prev) => [...prev, cursorBefore ?? ''])
        setCursorBefore(last.decided_at)
    }

    function handlePrev() {
        const newPrevCursors = [...prevCursors]
        const prev = newPrevCursors.pop()
        setPrevCursors(newPrevCursors)
        setCursorBefore(prev === '' ? undefined : prev)
    }

    // ── Render ────────────────────────────────────────────────────────────────

    if (sentinelLoading) {
        return (
            <div className="p-4 text-muted-foreground" aria-label="loading">
                Loading…
            </div>
        )
    }

    return (
        <div className="space-y-4">
            {/* ── Header section ─────────────────────────────────────────────── */}
            <div className="flex flex-col gap-2">
                <div className="flex items-center gap-3">
                    <Switch
                        id="spam-filter-toggle"
                        checked={spamFilterEnabled}
                        onCheckedChange={handleToggle}
                        disabled={patchSentinel.isPending}
                        aria-label="Spam filter"
                    />
                    <label
                        htmlFor="spam-filter-toggle"
                        className="text-sm font-medium cursor-pointer"
                    >
                        Spam filter
                    </label>
                </div>

                {stats && (
                    <p className="text-sm text-muted-foreground">
                        Last 30 days &middot; {stats.spam + stats.phishing_alert} archived &middot; {stats.newsletter} labeled &middot; {stats.restored} restored
                    </p>
                )}

                {sentinel?.config_values && (
                    <p className="text-xs text-muted-foreground">
                        Active retention:{' '}
                        {((sentinel.config_values.spam_purge_active_days as number | undefined) ?? 30)}d &middot; Restored retention:{' '}
                        {((sentinel.config_values.spam_purge_restored_days as number | undefined) ?? 90)}d
                    </p>
                )}
            </div>

            {/* ── Table / empty states ────────────────────────────────────────── */}
            {!spamFilterEnabled ? (
                <p className="text-sm text-muted-foreground">
                    Spam filter is off
                </p>
            ) : decisionsLoading ? (
                <div className="text-sm text-muted-foreground">Loading decisions…</div>
            ) : !decisions || decisions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                    Spam filter is on, no decisions yet
                </p>
            ) : (
                <>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead className="w-8">Bucket</TableHead>
                                <TableHead>From</TableHead>
                                <TableHead>Subject</TableHead>
                                <TableHead>Received</TableHead>
                                <TableHead>Origin</TableHead>
                                <TableHead>Signal</TableHead>
                                <TableHead>Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {decisions.map((decision) => (
                                <TableRow key={decision.id}>
                                    <TableCell
                                        className="text-center"
                                        title={decision.bucket}
                                    >
                                        {bucketIcon(decision.bucket)}
                                    </TableCell>
                                    <TableCell className="max-w-[180px] truncate text-sm">
                                        {decision.sender_email}
                                    </TableCell>
                                    <TableCell className="max-w-[260px] truncate text-sm">
                                        {decision.subject || '(no subject)'}
                                    </TableCell>
                                    <TableCell className="text-sm text-muted-foreground whitespace-nowrap">
                                        {formatRelative(decision.received_at)}
                                    </TableCell>
                                    <TableCell className="text-sm">
                                        <OriginCell
                                            role={decision.origin_mailbox_role}
                                            id={decision.origin_mailbox_id}
                                        />
                                    </TableCell>
                                    <TableCell className="text-sm">
                                        <span title={decision.signal_source}>
                                            {decision.signal_source}
                                        </span>
                                        {decision.score !== null && (
                                            <span className="ml-1 text-xs text-muted-foreground">
                                                ({Math.round(decision.score * 100)}%)
                                            </span>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        <RestoreButton decision={decision} />
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>

                    {/* Pagination */}
                    <div className="flex items-center gap-2">
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={prevCursors.length === 0}
                            onClick={handlePrev}
                        >
                            Previous
                        </Button>
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={decisions.length < 50}
                            onClick={handleNext}
                        >
                            Next
                        </Button>
                    </div>
                </>
            )}
        </div>
    )
}
