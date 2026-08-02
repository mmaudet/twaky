'use client'

import Link from 'next/link'
import { RelativeTime } from '@/components/missions/relative-time'
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
import { useAgents } from '@/hooks/use-agents'

export default function AgentsPage() {
    const { data: agents, isLoading, error } = useAgents()

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>
    if (!agents) return <p>No agents.</p>

    return (
        <div className="space-y-4">
            <h1 className="text-2xl font-semibold">Agents</h1>
            <p className="text-sm text-muted-foreground">
                Edit the built-in agents&apos; system prompt, model, or temperature.
                Changes apply on the next sub-agent invocation — no restart required.
            </p>
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Role</TableHead>
                        <TableHead>Model</TableHead>
                        <TableHead>Temperature</TableHead>
                        <TableHead>Updated</TableHead>
                        <TableHead className="w-16"></TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {agents.map((a) => (
                        <TableRow key={a.id}>
                            <TableCell className="font-medium">{a.display_name}</TableCell>
                            <TableCell>
                                <Badge variant={a.role === 'orchestrator' ? 'default' : 'secondary'}>
                                    {a.role}
                                </Badge>
                            </TableCell>
                            <TableCell>
                                {a.model
                                    ? <code className="text-xs">{a.effective_model}</code>
                                    : <span className="italic text-muted-foreground">
                                        {a.effective_model} (default)
                                    </span>
                                }
                            </TableCell>
                            <TableCell>
                                {a.temperature !== null
                                    ? <code className="text-xs">{a.temperature.toFixed(2)}</code>
                                    : <span className="italic text-muted-foreground">(default)</span>
                                }
                            </TableCell>
                            <TableCell className="text-sm text-muted-foreground">
                                <RelativeTime timestamp={a.updated_at} />
                            </TableCell>
                            <TableCell>
                                <Link href={`/agents/${a.id}`}>
                                    <Button variant="outline" size="sm">Edit</Button>
                                </Link>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </div>
    )
}
