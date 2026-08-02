'use client'

import Link from 'next/link'
import { useState } from 'react'
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
import { Button } from '@/components/ui/button'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { RelativeTime } from '@/components/missions/relative-time'
import { toast } from 'sonner'
import { useDeleteSkill, useSkills } from '@/hooks/use-skills'

export default function SkillsPage() {
    const { data: skills, isLoading } = useSkills()
    const deleteSkill = useDeleteSkill()
    const [pendingDelete, setPendingDelete] = useState<string | null>(null)

    async function handleDelete(id: string, name: string) {
        try {
            await deleteSkill.mutateAsync(id)
            toast.success(`Skill '${name}' deleted`)
        } catch {
            toast.error(`Failed to delete '${name}'`)
        } finally {
            setPendingDelete(null)
        }
    }

    if (isLoading) return <div className="p-8 text-muted-foreground">Loading…</div>

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">Skills</h1>
                <Button asChild>
                    <Link href="/skills/new">+ New skill</Link>
                </Button>
            </div>

            {(!skills || skills.length === 0) ? (
                <div className="mx-auto max-w-md rounded-lg border p-8 text-center space-y-4">
                    <p className="text-muted-foreground">No skills yet.</p>
                    <Button asChild>
                        <Link href="/skills/new">+ Create your first skill</Link>
                    </Button>
                </div>
            ) : (
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>Description</TableHead>
                            <TableHead>Bound to</TableHead>
                            <TableHead>Enabled</TableHead>
                            <TableHead>Updated</TableHead>
                            <TableHead className="w-40" />
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {skills.map((s) => (
                            <TableRow key={s.id}>
                                <TableCell>
                                    <code className="font-mono text-sm">{s.name}</code>
                                </TableCell>
                                <TableCell className="max-w-md truncate">{s.description}</TableCell>
                                <TableCell className="space-x-1">
                                    {s.bound_agents.map((a) => (
                                        <Badge key={a} variant="secondary">{a}</Badge>
                                    ))}
                                </TableCell>
                                <TableCell>
                                    <span
                                        className={
                                            'inline-block h-2.5 w-2.5 rounded-full ' +
                                            (s.enabled ? 'bg-green-500' : 'border border-muted-foreground')
                                        }
                                        aria-label={s.enabled ? 'enabled' : 'disabled'}
                                    />
                                </TableCell>
                                <TableCell>
                                    <RelativeTime timestamp={s.updated_at} />
                                </TableCell>
                                <TableCell className="space-x-2 text-right">
                                    <Button asChild size="sm" variant="outline">
                                        <Link href={`/skills/${s.id}`}>Edit</Link>
                                    </Button>
                                    <AlertDialog
                                        open={pendingDelete === s.id}
                                        onOpenChange={(o) => setPendingDelete(o ? s.id : null)}
                                    >
                                        <AlertDialogTrigger asChild>
                                            <Button size="sm" variant="destructive">Delete</Button>
                                        </AlertDialogTrigger>
                                        <AlertDialogContent>
                                            <AlertDialogHeader>
                                                <AlertDialogTitle>
                                                    Delete <code>{s.name}</code>?
                                                </AlertDialogTitle>
                                                <AlertDialogDescription>
                                                    Missions in flight that use it will fail on next call.
                                                    This cannot be undone.
                                                </AlertDialogDescription>
                                            </AlertDialogHeader>
                                            <AlertDialogFooter>
                                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                                <AlertDialogAction
                                                    onClick={() => handleDelete(s.id, s.name)}
                                                >
                                                    Delete
                                                </AlertDialogAction>
                                            </AlertDialogFooter>
                                        </AlertDialogContent>
                                    </AlertDialog>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            )}
        </div>
    )
}
