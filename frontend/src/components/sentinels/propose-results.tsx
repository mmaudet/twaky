'use client'

import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import type { components } from '@/lib/api-types'

type ProposeResultsProps = {
    data: components['schemas']['MailRuleProposeResponse']
    reviewed: boolean
    onReviewedChange: (v: boolean) => void
}

export function ProposeResults({ data, reviewed, onReviewedChange }: ProposeResultsProps) {
    const shadowedBy =
        data.would_shadow.length > 0 ? data.would_shadow.join(', ') : 'none'

    return (
        <div className="rounded-lg border p-4 space-y-4">
            {/* Summary bar */}
            <p className="text-sm font-medium">
                {data.matched_count} matches · {data.would_shadow_count} shadowed by {shadowedBy}
            </p>

            {/* Simulation partial warning */}
            {data.simulation_partial === true && (
                <div
                    role="alert"
                    className="rounded-md border border-yellow-400 bg-yellow-50 px-3 py-2 text-sm text-yellow-800"
                >
                    <span className="font-semibold">Partial simulation: </span>
                    {data.simulation_partial_reason || 'Simulation partial (no details provided)'}
                </div>
            )}

            {/* Matched examples table */}
            <div className="max-h-96 overflow-y-auto">
                {data.matched_examples.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                        No historical decision matched this rule.
                    </p>
                ) : (
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Sender</TableHead>
                                <TableHead>Subject</TableHead>
                                <TableHead>Current bucket</TableHead>
                                <TableHead>Shadowed by</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.matched_examples.map((ex) => (
                                <TableRow key={ex.decision_id}>
                                    <TableCell className="font-mono text-sm">
                                        {ex.sender}
                                    </TableCell>
                                    <TableCell className="text-sm max-w-xs truncate">
                                        {ex.subject}
                                    </TableCell>
                                    <TableCell className="text-sm">
                                        {ex.current_bucket}
                                    </TableCell>
                                    <TableCell className="text-sm">
                                        {ex.would_shadow_by != null ? (
                                            <span
                                                title={`would be pre-empted by ${ex.would_shadow_by}`}
                                                className="flex items-center gap-1"
                                            >
                                                <span aria-label="warning" role="img">⚠️</span>
                                                {ex.would_shadow_by}
                                            </span>
                                        ) : (
                                            <span className="text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                )}
            </div>

            {/* Reviewed checkbox */}
            <div className="flex items-center gap-2">
                <Checkbox
                    id="propose-reviewed"
                    checked={reviewed}
                    onCheckedChange={(checked) => onReviewedChange(checked === true)}
                />
                <Label htmlFor="propose-reviewed">I have reviewed the matches</Label>
            </div>
        </div>
    )
}
