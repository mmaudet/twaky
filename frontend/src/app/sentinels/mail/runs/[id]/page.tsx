'use client'

import { useParams } from 'next/navigation'
import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useSentinelRunDetail } from '@/hooks/use-sentinels'

const OUTCOME_VARIANTS: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
    processed: 'default',
    mission_created: 'secondary',
    ignored: 'outline',
    delegated: 'secondary',
    error: 'destructive',
}

export default function RunDetailPage() {
    const params = useParams()
    const runId = params.id as string

    const { data: run, isLoading, error } = useSentinelRunDetail(runId)

    if (isLoading) return <div className="p-8 text-muted-foreground">Loading…</div>
    if (error) return <p className="p-8 text-red-600">Error: {error.message}</p>
    if (!run) return <p className="p-8 text-muted-foreground">Run not found.</p>

    const durationLabel =
        run.duration_ms != null
            ? run.duration_ms >= 1000
                ? `${(run.duration_ms / 1000).toFixed(2)}s`
                : `${run.duration_ms}ms`
            : '—'

    return (
        <div className="p-6 space-y-6 max-w-4xl">
            {/* Header */}
            <div className="flex items-center gap-3">
                <Button asChild variant="ghost" size="sm">
                    <Link href="/sentinels/mail?tab=runs">← Runs</Link>
                </Button>
                <h1 className="text-2xl font-semibold">Run detail</h1>
            </div>

            <div className="rounded-lg border p-4 space-y-2">
                <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                        <span className="text-muted-foreground">Sentinel</span>
                        <p className="font-mono font-medium">{run.sentinel_name}</p>
                    </div>
                    <div>
                        <span className="text-muted-foreground">Outcome</span>
                        <div className="mt-0.5">
                            <Badge variant={OUTCOME_VARIANTS[run.outcome] ?? 'outline'}>
                                {run.outcome}
                            </Badge>
                        </div>
                    </div>
                    <div>
                        <span className="text-muted-foreground">Started</span>
                        <p className="tabular-nums">{new Date(run.started_at).toLocaleString()}</p>
                    </div>
                    <div>
                        <span className="text-muted-foreground">Duration</span>
                        <p className="tabular-nums">{durationLabel}</p>
                    </div>
                    <div>
                        <span className="text-muted-foreground">LLM calls</span>
                        <p className="tabular-nums">{run.llm_calls}</p>
                    </div>
                    {run.mission_id && (
                        <div>
                            <span className="text-muted-foreground">Mission</span>
                            <div className="mt-0.5">
                                <Button asChild size="sm" variant="link" className="p-0 h-auto">
                                    <Link href={`/missions/${run.mission_id}`}>
                                        {run.mission_id}
                                    </Link>
                                </Button>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Event payload */}
            <section className="space-y-2">
                <h2 className="text-lg font-semibold">Event ref</h2>
                <code className="block rounded bg-muted px-3 py-2 text-sm font-mono break-all">
                    {run.event_ref}
                </code>
            </section>

            {/* Trace */}
            {run.trace && run.trace.length > 0 && (
                <section className="space-y-2">
                    <h2 className="text-lg font-semibold">Trace</h2>
                    <div className="rounded-lg border divide-y">
                        {run.trace.map((step, i) => {
                            const s = step as Record<string, unknown>
                            return (
                                <div
                                    key={i}
                                    className="flex items-start gap-4 px-4 py-3 text-sm"
                                >
                                    <span className="shrink-0 tabular-nums text-muted-foreground w-6">
                                        {i + 1}
                                    </span>
                                    <code className="shrink-0 font-mono font-medium">
                                        {typeof s.node === 'string' ? s.node : '—'}
                                    </code>
                                    {typeof s.ts === 'string' && (
                                        <span className="text-muted-foreground tabular-nums ml-auto">
                                            {new Date(s.ts).toLocaleTimeString([], {
                                                hour12: false,
                                                hour: '2-digit',
                                                minute: '2-digit',
                                                second: '2-digit',
                                                fractionalSecondDigits: 3,
                                            })}
                                        </span>
                                    )}
                                    {Object.keys(s).filter(k => k !== 'node' && k !== 'ts').length > 0 && (
                                        <pre className="text-xs text-muted-foreground truncate max-w-sm">
                                            {JSON.stringify(
                                                Object.fromEntries(
                                                    Object.entries(s).filter(([k]) => k !== 'node' && k !== 'ts'),
                                                ),
                                            )}
                                        </pre>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                </section>
            )}

            {/* Error section */}
            {run.outcome === 'error' && run.error_repr && (
                <section className="space-y-2">
                    <h2 className="text-lg font-semibold text-red-600">Error</h2>
                    <pre className="rounded border border-red-300 bg-red-50 px-4 py-3 text-sm font-mono whitespace-pre-wrap break-all dark:border-red-700 dark:bg-red-950 dark:text-red-300">
                        {run.error_repr}
                    </pre>
                </section>
            )}
        </div>
    )
}
