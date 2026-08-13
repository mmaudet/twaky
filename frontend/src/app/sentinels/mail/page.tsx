'use client'

import { useState } from 'react'
import Link from 'next/link'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import {
    Tabs,
    TabsContent,
    TabsList,
    TabsTrigger,
} from '@/components/ui/tabs'
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
import { useMailRules, useDeleteMailRule, type MailRuleSummary } from '@/hooks/use-mail-sentinel-rules'
import { useMailMemories, type MailMemorySummary } from '@/hooks/use-mail-sentinel-memories'
import { useMailPatterns, useForgetMailPattern, type LearnedPatternSummary } from '@/hooks/use-mail-sentinel-patterns'
import { useMailObservations, usePatchMailMemory, useForgetMailMemory } from '@/hooks/use-mail-sentinel-observations'
import { useSentinelRuns } from '@/hooks/use-sentinels'
import { AuthTab } from './auth-tab'
import { RecentSpamTab } from './recent-spam-tab'
import { MemoryCard } from './components/MemoryCard'
import { ObservationsList } from './components/ObservationsList'
import type { components } from '@/lib/api-types'

type SentinelRunSummary = components['schemas']['SentinelRunSummary']

// ── Outcome badge ─────────────────────────────────────────────────────────────

const OUTCOME_VARIANTS: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
    processed: 'default',
    mission_created: 'secondary',
    ignored: 'outline',
    delegated: 'secondary',
    error: 'destructive',
}

function OutcomeBadge({ outcome }: { outcome: string }) {
    return (
        <Badge variant={OUTCOME_VARIANTS[outcome] ?? 'outline'}>
            {outcome}
        </Badge>
    )
}

// ── Rules tab ─────────────────────────────────────────────────────────────────

function RuleRow({ rule }: { rule: MailRuleSummary }) {
    const del = useDeleteMailRule()

    async function handleDelete() {
        try {
            await del.mutateAsync(rule.id)
            toast.success(`Rule '${rule.name}' deleted`)
        } catch {
            toast.error(`Failed to delete rule '${rule.name}'`)
        }
    }

    return (
        <TableRow>
            <TableCell>
                <code className="font-mono text-sm">{rule.name}</code>
            </TableCell>
            <TableCell className="max-w-xs truncate text-muted-foreground text-sm">
                {rule.description}
            </TableCell>
            <TableCell className="tabular-nums">{rule.condition_count}</TableCell>
            <TableCell className="tabular-nums">{rule.action_count}</TableCell>
            <TableCell>
                <Badge variant={rule.enabled ? 'default' : 'outline'}>
                    {rule.enabled ? 'enabled' : 'disabled'}
                </Badge>
            </TableCell>
            <TableCell className="tabular-nums">{rule.priority}</TableCell>
            <TableCell>
                <div className="flex items-center gap-2">
                    <Button asChild size="sm" variant="outline">
                        <Link href={`/sentinels/mail/rules/${rule.id}`}>Edit</Link>
                    </Button>
                    <AlertDialog>
                        <AlertDialogTrigger asChild>
                            <Button size="sm" variant="destructive" disabled={del.isPending}>
                                Delete
                            </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                            <AlertDialogHeader>
                                <AlertDialogTitle>Delete rule &ldquo;{rule.name}&rdquo;?</AlertDialogTitle>
                                <AlertDialogDescription>
                                    This action cannot be undone.
                                </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction onClick={handleDelete}>Delete</AlertDialogAction>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>
                </div>
            </TableCell>
        </TableRow>
    )
}

function RulesTab() {
    const { data: rules, isLoading, error } = useMailRules()

    if (isLoading) return <div className="p-4 text-muted-foreground">Loading rules…</div>
    if (error) return <p className="p-4 text-red-600">Error: {error.message}</p>

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                    {rules?.length ?? 0} rule{rules?.length !== 1 ? 's' : ''}
                </span>
                <Button asChild size="sm">
                    <Link href="/sentinels/mail/rules/new">+ New rule</Link>
                </Button>
            </div>

            {!rules || rules.length === 0 ? (
                <div className="rounded-lg border p-8 text-center">
                    <p className="text-muted-foreground mb-4">No rules yet.</p>
                    <Button asChild>
                        <Link href="/sentinels/mail/rules/new">Create your first rule</Link>
                    </Button>
                </div>
            ) : (
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>Description</TableHead>
                            <TableHead>Conditions</TableHead>
                            <TableHead>Actions</TableHead>
                            <TableHead>Enabled</TableHead>
                            <TableHead title="Lower priority runs first">
                                Priority <span aria-hidden="true">↑</span>
                            </TableHead>
                            <TableHead className="w-36" />
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {rules.map((r) => (
                            <RuleRow key={r.id} rule={r} />
                        ))}
                    </TableBody>
                </Table>
            )}
        </div>
    )
}

// ── Memories tab ──────────────────────────────────────────────────────────────

function MemoriesTab() {
    const { data: memories, isLoading, error } = useMailMemories()
    const patchMemory = usePatchMailMemory()
    const forgetMemory = useForgetMailMemory()

    const [filterSource, setFilterSource] = useState<string>('')
    const [filterScope, setFilterScope] = useState<string>('')
    const [filterKind, setFilterKind] = useState<string>('')

    if (isLoading) return <div className="p-4 text-muted-foreground">Loading memories…</div>
    if (error) return <p className="p-4 text-red-600">Error: {error.message}</p>

    const filtered = (memories ?? []).filter((m: MailMemorySummary) => {
        if (filterSource && m.source !== filterSource) return false
        if (filterScope && m.scope !== filterScope) return false
        if (filterKind && m.kind !== filterKind) return false
        return true
    })

    const sources = Array.from(new Set((memories ?? []).map((m: MailMemorySummary) => m.source)))
    const scopes = Array.from(new Set((memories ?? []).map((m: MailMemorySummary) => m.scope)))
    const kinds = Array.from(new Set((memories ?? []).map((m: MailMemorySummary) => m.kind)))

    async function handleForget(id: string) {
        try {
            await forgetMemory.mutateAsync(id)
            toast.success('Memory reset to 7-day TTL')
        } catch {
            toast.error('Failed to forget memory')
        }
    }

    async function handlePersist(id: string, persist: boolean) {
        try {
            await patchMemory.mutateAsync({ memory_id: id, persist })
            toast.success(persist ? 'Memory made permanent' : 'Memory reset to 7-day TTL')
        } catch {
            toast.error('Failed to update memory')
        }
    }

    if (!memories || memories.length === 0) {
        return (
            <div className="rounded-lg border p-8 text-center">
                <p className="text-muted-foreground">No memories stored yet.</p>
            </div>
        )
    }

    return (
        <div className="space-y-4">
            <div className="flex gap-3 flex-wrap items-center">
                <label className="text-sm text-muted-foreground flex items-center gap-1">
                    Source:
                    <select
                        className="ml-1 border rounded px-2 py-1 text-sm bg-background"
                        value={filterSource}
                        onChange={(e) => setFilterSource(e.target.value)}
                    >
                        <option value="">All</option>
                        {sources.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                </label>
                <label className="text-sm text-muted-foreground flex items-center gap-1">
                    Scope:
                    <select
                        className="ml-1 border rounded px-2 py-1 text-sm bg-background"
                        value={filterScope}
                        onChange={(e) => setFilterScope(e.target.value)}
                    >
                        <option value="">All</option>
                        {scopes.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                </label>
                <label className="text-sm text-muted-foreground flex items-center gap-1">
                    Kind:
                    <select
                        className="ml-1 border rounded px-2 py-1 text-sm bg-background"
                        value={filterKind}
                        onChange={(e) => setFilterKind(e.target.value)}
                    >
                        <option value="">All</option>
                        {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
                    </select>
                </label>
                <span className="text-xs text-muted-foreground ml-auto">
                    {filtered.length} / {memories.length}
                </span>
            </div>
            {filtered.length === 0 ? (
                <div className="rounded-lg border p-8 text-center">
                    <p className="text-muted-foreground">No memories match the current filters.</p>
                </div>
            ) : (
                <div>
                    {filtered.map((m: MailMemorySummary) => (
                        <MemoryCard
                            key={m.id}
                            memory={m}
                            onForget={handleForget}
                            onPersist={handlePersist}
                        />
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Learned patterns tab ──────────────────────────────────────────────────────

function PatternsTab() {
    const { data: patterns, isLoading, error } = useMailPatterns()
    const forget = useForgetMailPattern()

    if (isLoading) return <div className="p-4 text-muted-foreground">Loading patterns…</div>
    if (error) return <p className="p-4 text-red-600">Error: {error.message}</p>

    if (!patterns || patterns.length === 0) {
        return (
            <div className="rounded-lg border p-8 text-center">
                <p className="text-muted-foreground">No learned patterns yet.</p>
            </div>
        )
    }

    return (
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead>Sender</TableHead>
                    <TableHead>Rule</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Evidence</TableHead>
                    <TableHead>First seen</TableHead>
                    <TableHead>Last confirmed</TableHead>
                    <TableHead className="w-24" />
                </TableRow>
            </TableHeader>
            <TableBody>
                {patterns.map((p: LearnedPatternSummary) => (
                    <TableRow key={p.id}>
                        <TableCell className="font-mono text-sm">{p.sender_email}</TableCell>
                        <TableCell className="text-sm">
                            <span className="text-xs text-neutral-500">
                                {p.rule_name.startsWith('label:') && '🏷️'}
                                {p.rule_name === 'trust_sender' && '✅'}
                                {p.rule_name === 'block_sender' && '🚫'}
                                {' '}{p.rule_name}
                            </span>
                            {p.rule_name.startsWith('label:') && (
                                <span className="text-xs text-neutral-500 ml-2">Saves ~1 LLM call/msg</span>
                            )}
                        </TableCell>
                        <TableCell className="tabular-nums">
                            <span
                                className={
                                    p.confidence >= 0.8
                                        ? 'text-green-600 font-medium'
                                        : p.confidence >= 0.5
                                        ? 'text-yellow-600'
                                        : 'text-muted-foreground'
                                }
                            >
                                {(p.confidence * 100).toFixed(0)}%
                            </span>
                        </TableCell>
                        <TableCell className="tabular-nums">{p.evidence_count}</TableCell>
                        <TableCell className="text-sm tabular-nums">
                            {new Date(p.first_seen).toLocaleString()}
                        </TableCell>
                        <TableCell className="text-sm tabular-nums">
                            {new Date(p.last_confirmed).toLocaleString()}
                        </TableCell>
                        <TableCell>
                            <AlertDialog>
                                <AlertDialogTrigger asChild>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={forget.isPending}
                                    >
                                        Forget
                                    </Button>
                                </AlertDialogTrigger>
                                <AlertDialogContent>
                                    <AlertDialogHeader>
                                        <AlertDialogTitle>
                                            Delete pattern for &ldquo;{p.sender_email}&rdquo;?
                                        </AlertDialogTitle>
                                        <AlertDialogDescription>
                                            Future mails from this sender will re-run the full cascade.
                                        </AlertDialogDescription>
                                    </AlertDialogHeader>
                                    <AlertDialogFooter>
                                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                                        <AlertDialogAction
                                            onClick={() => {
                                                forget.mutate(
                                                    {
                                                        sender_email: p.sender_email,
                                                        rule_name: p.rule_name,
                                                    },
                                                    {
                                                        onSuccess: () =>
                                                            toast.success(
                                                                `Pattern for ${p.sender_email} forgotten`,
                                                            ),
                                                        onError: () =>
                                                            toast.error('Failed to forget pattern'),
                                                    },
                                                )
                                            }}
                                        >
                                            Forget
                                        </AlertDialogAction>
                                    </AlertDialogFooter>
                                </AlertDialogContent>
                            </AlertDialog>
                        </TableCell>
                    </TableRow>
                ))}
            </TableBody>
        </Table>
    )
}

// ── Runs sub-components ───────────────────────────────────────────────────────

function RunsList() {
    const { data: runs, isLoading, error } = useSentinelRuns('mail', { limit: 50 })

    if (isLoading) return <div className="p-4 text-muted-foreground">Loading runs…</div>
    if (error) return <p className="p-4 text-red-600">Error: {error.message}</p>

    if (!runs || runs.length === 0) {
        return (
            <div className="rounded-lg border p-8 text-center">
                <p className="text-muted-foreground">No runs yet.</p>
            </div>
        )
    }

    return (
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead>Started</TableHead>
                    <TableHead>Event ref</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead>Outcome</TableHead>
                    <TableHead>Mission</TableHead>
                    <TableHead>LLM calls</TableHead>
                    <TableHead className="w-20" />
                </TableRow>
            </TableHeader>
            <TableBody>
                {runs.map((run: SentinelRunSummary) => (
                    <TableRow key={run.id}>
                        <TableCell className="text-sm tabular-nums">
                            {new Date(run.started_at).toLocaleString()}
                        </TableCell>
                        <TableCell className="font-mono text-xs max-w-xs truncate">
                            {run.event_ref}
                        </TableCell>
                        <TableCell className="tabular-nums text-sm">
                            {run.duration_ms != null ? `${run.duration_ms}ms` : '—'}
                        </TableCell>
                        <TableCell>
                            <OutcomeBadge outcome={run.outcome} />
                        </TableCell>
                        <TableCell>
                            {run.mission_id ? (
                                <Button asChild size="sm" variant="link" className="p-0 h-auto">
                                    <Link href={`/missions/${run.mission_id}`}>
                                        {run.mission_id.slice(0, 8)}…
                                    </Link>
                                </Button>
                            ) : (
                                <span className="text-muted-foreground text-sm">—</span>
                            )}
                        </TableCell>
                        <TableCell className="tabular-nums">{run.llm_calls ?? 0}</TableCell>
                        <TableCell>
                            <Button asChild size="sm" variant="outline">
                                <Link href={`/sentinels/mail/runs/${run.id}`}>Detail</Link>
                            </Button>
                        </TableCell>
                    </TableRow>
                ))}
            </TableBody>
        </Table>
    )
}

function ObservationsSubTab() {
    const { data: observations, isLoading, error } = useMailObservations({ limit: 100 })

    if (isLoading) return <div className="p-4 text-muted-foreground">Loading observations…</div>
    if (error) return <p className="p-4 text-red-600">Error: {error.message}</p>

    return <ObservationsList rows={observations ?? []} />
}

// ── Runs tab ──────────────────────────────────────────────────────────────────

function RunsTab() {
    return (
        <Tabs defaultValue="runs-list">
            <TabsList>
                <TabsTrigger value="runs-list">Runs</TabsTrigger>
                <TabsTrigger value="observations">Observations</TabsTrigger>
            </TabsList>
            <TabsContent value="runs-list" className="mt-4">
                <RunsList />
            </TabsContent>
            <TabsContent value="observations" className="mt-4">
                <ObservationsSubTab />
            </TabsContent>
        </Tabs>
    )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function MailSentinelPage() {
    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center gap-3">
                <Button asChild variant="ghost" size="sm">
                    <Link href="/sentinels">← Sentinels</Link>
                </Button>
                <h1 className="text-2xl font-semibold">Mail Sentinel</h1>
            </div>

            <Tabs defaultValue="rules">
                <TabsList>
                    <TabsTrigger value="rules">Rules</TabsTrigger>
                    <TabsTrigger value="memories">Memories</TabsTrigger>
                    <TabsTrigger value="patterns">Learned Patterns</TabsTrigger>
                    <TabsTrigger value="runs">Runs</TabsTrigger>
                    <TabsTrigger value="auth">Auth</TabsTrigger>
                    <TabsTrigger value="recent-spam">Recent Spam</TabsTrigger>
                </TabsList>

                <TabsContent value="rules" className="mt-4">
                    <RulesTab />
                </TabsContent>
                <TabsContent value="memories" className="mt-4">
                    <MemoriesTab />
                </TabsContent>
                <TabsContent value="patterns" className="mt-4">
                    <PatternsTab />
                </TabsContent>
                <TabsContent value="runs" className="mt-4">
                    <RunsTab />
                </TabsContent>
                <TabsContent value="auth" className="mt-4">
                    <AuthTab />
                </TabsContent>
                <TabsContent value="recent-spam" className="mt-4">
                    <RecentSpamTab />
                </TabsContent>
            </Tabs>
        </div>
    )
}
