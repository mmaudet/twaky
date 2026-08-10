'use client'

import { useRouter, useParams } from 'next/navigation'
import Link from 'next/link'
import { useState, useCallback } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
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
import { Badge } from '@/components/ui/badge'
import { RuleJsonEditor, validateRule } from '@/components/sentinels/rule-json-editor'
import {
    useMailRule,
    useCreateMailRule,
    usePatchMailRule,
    useDeleteMailRule,
    type MailRuleCreate,
    type MailRulePatch,
} from '@/hooks/use-mail-sentinel-rules'

const DEFAULT_RULE = JSON.stringify(
    {
        name: 'my-rule',
        description: '',
        conditions: [
            { field: 'from', operator: 'contains', value: '@example.com' },
        ],
        combinator: 'OR',
        actions: ['archive'],
        priority: 100,
        enabled: true,
        run_on_threads: true,
    },
    null,
    2,
)

const FIELDS = ['from', 'to', 'subject', 'body', 'header']
const OPERATORS = ['equals', 'contains', 'regex', 'glob']
const ACTIONS = [
    { name: 'archive', desc: 'Move to archive' },
    { name: 'label_important', desc: 'Mark as important' },
    { name: 'label_newsletter', desc: 'Apply newsletter label' },
    { name: 'label_receipt', desc: 'Apply receipt label' },
    { name: 'create_mission', desc: 'Create a Twaky mission for review' },
    { name: 'delegate_to_atlas', desc: 'Delegate to Atlas for deeper reasoning' },
]

function ruleToJson(rule: {
    name: string
    description: string
    conditions: { [key: string]: unknown }[]
    combinator: string
    actions: string[]
    priority: number
    enabled: boolean
    run_on_threads: boolean
}): string {
    return JSON.stringify(
        {
            name: rule.name,
            description: rule.description,
            conditions: rule.conditions,
            combinator: rule.combinator,
            actions: rule.actions,
            priority: rule.priority,
            enabled: rule.enabled,
            run_on_threads: rule.run_on_threads,
        },
        null,
        2,
    )
}

export default function RuleEditorPage() {
    const params = useParams()
    const router = useRouter()
    const id = params.id as string
    const isNew = id === 'new'

    const { data: existingRule, isLoading } = useMailRule(isNew ? '' : id)
    const create = useCreateMailRule()
    const patch = usePatchMailRule(id)
    const del = useDeleteMailRule()

    // We derive the editor JSON from the fetched rule once it arrives.
    // Track which rule ID we last initialised from to avoid re-setting on every render.
    const [loadedRuleId, setLoadedRuleId] = useState<string | null>(null)
    const [json, setJson] = useState<string>(DEFAULT_RULE)
    const [isValid, setIsValid] = useState(() => validateRule(DEFAULT_RULE).isValid)
    const [isDirty, setIsDirty] = useState(false)

    // Derive updated json when the rule loads for the first time.
    // This is intentionally a synchronous "derived state during render" pattern,
    // not a useEffect, to comply with react-hooks/set-state-in-effect.
    if (existingRule && !isNew && loadedRuleId !== existingRule.id) {
        const ruleJson = ruleToJson(existingRule)
        setLoadedRuleId(existingRule.id)
        setJson(ruleJson)
        setIsValid(validateRule(ruleJson).isValid)
        setIsDirty(false)
    }

    const handleChange = useCallback(
        (value: string, meta: { isValid: boolean; errors: string[] }) => {
            setJson(value)
            setIsValid(meta.isValid)
            setIsDirty(true)
        },
        [],
    )

    async function handleSave() {
        let parsed: MailRuleCreate
        try {
            parsed = JSON.parse(json) as MailRuleCreate
        } catch {
            toast.error('JSON is not valid')
            return
        }

        try {
            if (isNew) {
                await create.mutateAsync(parsed)
                toast.success('Rule created')
                router.push('/sentinels/mail')
            } else {
                const patchBody: MailRulePatch = {
                    name: parsed.name,
                    description: parsed.description,
                    conditions: parsed.conditions,
                    combinator: parsed.combinator,
                    actions: parsed.actions,
                    priority: parsed.priority,
                    enabled: parsed.enabled,
                    run_on_threads: parsed.run_on_threads,
                }
                await patch.mutateAsync(patchBody)
                toast.success('Rule saved')
                setIsDirty(false)
            }
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Save failed')
        }
    }

    async function handleDelete() {
        try {
            await del.mutateAsync(id)
            toast.success('Rule deleted')
            router.push('/sentinels/mail')
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Delete failed')
        }
    }

    const isSaving = create.isPending || patch.isPending
    // For new rules, allow saving as soon as JSON is valid (even without dirty flag)
    const saveDisabled = !isValid || (!isDirty && !isNew) || isSaving

    if (isLoading) return <div className="p-8 text-muted-foreground">Loading…</div>

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center gap-3">
                <Button asChild variant="ghost" size="sm">
                    <Link href="/sentinels/mail">← Mail Sentinel</Link>
                </Button>
                <h1 className="text-2xl font-semibold">
                    {isNew ? 'New rule' : existingRule?.name ?? 'Edit rule'}
                </h1>
                {!isNew && existingRule && (
                    <Badge variant={existingRule.enabled ? 'default' : 'outline'}>
                        {existingRule.enabled ? 'enabled' : 'disabled'}
                    </Badge>
                )}
            </div>

            <div className="grid grid-cols-3 gap-6">
                {/* Left column: Monaco JSON editor */}
                <div className="col-span-2 space-y-2">
                    <p className="text-sm font-medium text-muted-foreground">
                        Rule JSON
                    </p>
                    <RuleJsonEditor
                        value={json}
                        onChange={handleChange}
                        disabled={isSaving}
                    />
                </div>

                {/* Right column: hints panel */}
                <div className="col-span-1 space-y-4 text-sm">
                    <div className="rounded-lg border p-4 space-y-3">
                        <h2 className="font-semibold">Fields</h2>
                        <ul className="space-y-1 text-muted-foreground">
                            {FIELDS.map((f) => (
                                <li key={f}>
                                    <code className="text-foreground">{f}</code>
                                </li>
                            ))}
                        </ul>
                    </div>

                    <div className="rounded-lg border p-4 space-y-3">
                        <h2 className="font-semibold">Operators</h2>
                        <ul className="space-y-1 text-muted-foreground">
                            {OPERATORS.map((op) => (
                                <li key={op}>
                                    <code className="text-foreground">{op}</code>
                                </li>
                            ))}
                        </ul>
                    </div>

                    <div className="rounded-lg border p-4 space-y-3">
                        <h2 className="font-semibold">Combinator</h2>
                        <p className="text-muted-foreground">
                            <code className="text-foreground">OR</code> — any condition matches
                        </p>
                        <p className="text-muted-foreground">
                            <code className="text-foreground">AND</code> — all conditions must match
                        </p>
                    </div>

                    <div className="rounded-lg border p-4 space-y-3">
                        <h2 className="font-semibold">Actions</h2>
                        <ul className="space-y-2">
                            {ACTIONS.map((a) => (
                                <li key={a.name}>
                                    <code className="text-foreground">{a.name}</code>
                                    <p className="text-muted-foreground text-xs">{a.desc}</p>
                                </li>
                            ))}
                        </ul>
                    </div>
                </div>
            </div>

            {/* Bottom bar */}
            <div className="flex items-center justify-between border-t pt-4">
                <div className="flex items-center gap-2">
                    <Button asChild variant="outline">
                        <Link href="/sentinels/mail">Cancel</Link>
                    </Button>
                    {!isNew && (
                        <AlertDialog>
                            <AlertDialogTrigger asChild>
                                <Button variant="destructive" disabled={del.isPending}>
                                    Delete rule
                                </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                                <AlertDialogHeader>
                                    <AlertDialogTitle>
                                        Delete rule &ldquo;{existingRule?.name}&rdquo;?
                                    </AlertDialogTitle>
                                    <AlertDialogDescription>
                                        This action cannot be undone.
                                    </AlertDialogDescription>
                                </AlertDialogHeader>
                                <AlertDialogFooter>
                                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                                    <AlertDialogAction onClick={handleDelete}>
                                        Delete
                                    </AlertDialogAction>
                                </AlertDialogFooter>
                            </AlertDialogContent>
                        </AlertDialog>
                    )}
                </div>
                <Button onClick={handleSave} disabled={saveDisabled}>
                    {isSaving ? 'Saving…' : 'Save rule'}
                </Button>
            </div>
        </div>
    )
}
