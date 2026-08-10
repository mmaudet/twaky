'use client'

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
import { useSentinelRuns } from '@/hooks/use-sentinels'
import { AuthTab } from './auth-tab'
import { RecentSpamTab } from './recent-spam-tab'
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
                            <TableHead>Priority</TableHead>
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

    if (isLoading) return <div className="p-4 text-muted-foreground">Loading memories…</div>
    if (error) return <p className="p-4 text-red-600">Error: {error.message}</p>

    if (!memories || memories.length === 0) {
        return (
            <div className="rounded-lg border p-8 text-center">
                <p className="text-muted-foreground">No memories stored yet.</p>
            </div>
        )
    }

    return (
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead>Kind</TableHead>
                    <TableHead>Scope</TableHead>
                    <TableHead>Content</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead>Expires</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                {memories.map((m: MailMemorySummary) => (
                    <TableRow key={m.id}>
                        <TableCell>
                            <Badge variant="secondary">{m.kind}</Badge>
                        </TableCell>
                        <TableCell>
                            <span className="text-sm text-muted-foreground">{m.scope}</span>
                            {m.scope_value && (
                                <code className="ml-1 text-xs">{m.scope_value}</code>
                            )}
                        </TableCell>
                        <TableCell className="max-w-xs truncate text-sm">
                            {m.content.slice(0, 120)}{m.content.length > 120 ? '…' : ''}
                        </TableCell>
                        <TableCell className="text-sm tabular-nums">
                            {new Date(m.created_at).toLocaleString()}
                        </TableCell>
                        <TableCell className="text-sm tabular-nums">
                            {new Date(m.expires_at).toLocaleString()}
                        </TableCell>
                    </TableRow>
                ))}
            </TableBody>
        </Table>
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
                        <TableCell className="text-sm">{p.rule_name}</TableCell>
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

// ── Runs tab ──────────────────────────────────────────────────────────────────

function RunsTab() {
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
