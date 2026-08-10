'use client'

import Link from 'next/link'
import { toast } from 'sonner'
import { Switch } from '@/components/ui/switch'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { SentinelStatusDot } from '@/components/sentinels/sentinel-status-dot'
import { useSentinels, usePatchSentinel, type SentinelSummary } from '@/hooks/use-sentinels'

function SentinelRow({ sentinel }: { sentinel: SentinelSummary }) {
    const patch = usePatchSentinel(sentinel.name)

    async function handleToggle(checked: boolean) {
        try {
            await patch.mutateAsync({ enabled: checked })
            toast.success(
                `Sentinel '${sentinel.display_name}' ${checked ? 'enabled' : 'disabled'}`,
            )
        } catch {
            toast.error(`Failed to update '${sentinel.display_name}'`)
        }
    }

    return (
        <TableRow key={sentinel.name}>
            <TableCell>
                <code className="font-mono text-sm">{sentinel.name}</code>
            </TableCell>
            <TableCell>{sentinel.display_name}</TableCell>
            <TableCell>
                <div className="flex items-center gap-2">
                    <SentinelStatusDot enabled={sentinel.enabled} />
                    <Switch
                        checked={sentinel.enabled}
                        onCheckedChange={handleToggle}
                        disabled={patch.isPending}
                        aria-label={`Toggle ${sentinel.display_name}`}
                    />
                </div>
            </TableCell>
            <TableCell>
                <code className="text-xs text-muted-foreground">{sentinel.version}</code>
            </TableCell>
            <TableCell className="tabular-nums">{sentinel.stats_24h.total}</TableCell>
            <TableCell
                className={
                    'tabular-nums ' +
                    (sentinel.stats_24h.errors > 0 ? 'text-red-600 font-medium' : '')
                }
            >
                {sentinel.stats_24h.errors}
            </TableCell>
            <TableCell>
                <Button asChild size="sm" variant="outline">
                    <Link href={`/sentinels/${sentinel.name}`}>Detail</Link>
                </Button>
            </TableCell>
        </TableRow>
    )
}

export default function SentinelsPage() {
    const { data: sentinels, isLoading, error } = useSentinels()

    if (isLoading) return <div className="p-8 text-muted-foreground">Loading…</div>
    if (error) return <p className="p-8 text-red-600">Error: {error.message}</p>

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">Sentinels</h1>
            </div>

            {(!sentinels || sentinels.length === 0) ? (
                <div className="mx-auto max-w-md rounded-lg border p-8 text-center">
                    <p className="text-muted-foreground">No sentinels registered.</p>
                </div>
            ) : (
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>Display name</TableHead>
                            <TableHead>Enabled</TableHead>
                            <TableHead>Version</TableHead>
                            <TableHead>Runs 24h</TableHead>
                            <TableHead>Errors 24h</TableHead>
                            <TableHead className="w-28" />
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {sentinels.map((s) => (
                            <SentinelRow key={s.name} sentinel={s} />
                        ))}
                    </TableBody>
                </Table>
            )}
        </div>
    )
}
