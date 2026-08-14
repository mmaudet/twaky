'use client'

import type { components } from '@/lib/api-types'

type ObservationSummary = components['schemas']['ObservationSummary']

type Props = { rows: ObservationSummary[] }

const outcomeEmoji: Record<string, string> = {
    extracted: '✅',
    skipped_trivial: '⏭️',
    skipped_no_match: '⏭️',
    error: '❌',
}

export function ObservationsList({ rows }: Props) {
    if (rows.length === 0) {
        return <div className="text-sm text-neutral-500">No observations yet.</div>
    }
    return (
        <table className="w-full text-sm">
            <thead>
                <tr className="text-left text-xs uppercase text-neutral-500">
                    <th className="py-1 pr-4">Time</th>
                    <th className="py-1 pr-4">Type</th>
                    <th className="py-1 pr-4">Email</th>
                    <th className="py-1">Outcome</th>
                </tr>
            </thead>
            <tbody>
                {rows.map((r) => (
                    <tr key={r.id} className="border-t">
                        <td className="py-1 pr-4 font-mono text-xs">
                            {new Date(r.observed_at).toLocaleString()}
                        </td>
                        <td className="py-1 pr-4">{r.observation_type}</td>
                        <td className="py-1 pr-4 font-mono text-xs">{r.email_id}</td>
                        <td className="py-1">
                            {outcomeEmoji[r.extraction_outcome] ?? '•'} {r.extraction_outcome}
                            {r.memory_ids.length > 0 && ` · ${r.memory_ids.length} memories`}
                            {r.pattern_ids.length > 0 && ` · ${r.pattern_ids.length} patterns`}
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    )
}
