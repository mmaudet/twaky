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

// SP5c 5.2: friendly labels + icons per pipeline stage
const NODE_META: Record<string, { icon: string; label: string; description: string }> = {
    load_thread: {
        icon: '📩',
        label: 'Load thread',
        description: 'Fetched the email + its thread from JMAP',
    },
    match_rules: {
        icon: '🎯',
        label: 'Match rules',
        description: 'Selected which rule (or learned pattern) applies to this mail',
    },
    spam_triage: {
        icon: '🛡️',
        label: 'Spam triage',
        description: 'Checked rspamd + heuristics for spam / newsletter / phishing',
    },
    apply_actions: {
        icon: '⚡',
        label: 'Apply actions',
        description: 'Executed the actions declared by the matched rule',
    },
    thread_status: {
        icon: '🧭',
        label: 'Thread status',
        description: 'Decided whether the thread expects a reply (TO_REPLY / FYI / DONE)',
    },
    select_memories: {
        icon: '🧠',
        label: 'Select memories',
        description: 'Ranked & injected relevant memories into the reply prompt',
    },
    draft_reply: {
        icon: '✍️',
        label: 'Draft reply',
        description: 'Generated the LLM draft + saved it to the mailbox',
    },
}

type TraceEntry = Record<string, unknown> & { node?: string }

function TraceStepCard({ step, index }: { step: TraceEntry; index: number }) {
    const nodeName = typeof step.node === 'string' ? step.node : '—'
    const meta = NODE_META[nodeName] ?? { icon: '•', label: nodeName, description: '' }

    const extras = Object.entries(step).filter(
        ([k]) => k !== 'node' && k !== 'ts',
    )

    // Highlight learned-pattern short-circuits — the whole point of SP5c
    const isShortCircuit =
        nodeName === 'match_rules' && step.short_circuit === true

    return (
        <div className="border rounded p-3 flex gap-3 items-start bg-white dark:bg-neutral-900">
            <span className="tabular-nums text-neutral-500 w-6 shrink-0">{index + 1}</span>
            <span className="shrink-0 text-lg" title={meta.label}>{meta.icon}</span>
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono font-medium text-sm">{meta.label}</span>
                    {isShortCircuit && (
                        <Badge variant="secondary" className="text-xs">
                            ⚡ short-circuit (no LLM)
                        </Badge>
                    )}
                </div>
                {meta.description && (
                    <p className="text-xs text-neutral-500 mt-0.5">{meta.description}</p>
                )}
                {extras.length > 0 && (
                    <div className="mt-2 space-y-1">
                        {extras.map(([k, v]) => (
                            <TraceField key={k} name={k} value={v} />
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}

function TraceField({ name, value }: { name: string; value: unknown }) {
    if (value === null || value === undefined || value === '') return null

    const label = name.replace(/_/g, ' ')

    // Arrays → chip list
    if (Array.isArray(value)) {
        if (value.length === 0) return null
        return (
            <div className="text-xs">
                <span className="text-neutral-500">{label}:</span>{' '}
                {value.map((v, i) => (
                    <span
                        key={i}
                        className="inline-block ml-1 px-1.5 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 font-mono"
                    >
                        {String(v)}
                    </span>
                ))}
            </div>
        )
    }

    // Booleans → check/cross
    if (typeof value === 'boolean') {
        return (
            <div className="text-xs">
                <span className="text-neutral-500">{label}:</span>{' '}
                <span className="ml-1">{value ? '✓' : '✗'}</span>
            </div>
        )
    }

    // Long text (draft_preview) → dedicated block
    if (typeof value === 'string' && value.length > 80) {
        return (
            <details className="text-xs">
                <summary className="text-neutral-500 cursor-pointer">{label}</summary>
                <pre className="mt-1 p-2 rounded bg-neutral-100 dark:bg-neutral-800 whitespace-pre-wrap font-sans text-sm">
                    {value}
                </pre>
            </details>
        )
    }

    return (
        <div className="text-xs">
            <span className="text-neutral-500">{label}:</span>{' '}
            <span className="font-mono">{String(value)}</span>
        </div>
    )
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

            {/* Decision trace (SP5c 5.2) */}
            {run.trace && run.trace.length > 0 && (
                <section className="space-y-2">
                    <h2 className="text-lg font-semibold">Decision trace</h2>
                    <p className="text-xs text-neutral-500">
                        Why the pipeline reached the outcome above, step by step.
                    </p>
                    <div className="space-y-2">
                        {run.trace.map((step, i) => (
                            <TraceStepCard key={i} step={step as TraceEntry} index={i} />
                        ))}
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
