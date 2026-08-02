'use client'

import Link from 'next/link'
import { StateBadge } from '@/components/missions/state-badge'
import { RelativeTime } from '@/components/missions/relative-time'
import { computeStateBreakdown } from '@/lib/compute-state-breakdown'
import { useMissions } from '@/hooks/use-missions'

export default function StatsPage() {
    const { data, isLoading, error } = useMissions()  // no filter — all live

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>

    const missions = data ?? []
    const { counts, totalLive, totalTerminal } = computeStateBreakdown(missions)
    const recentFailures = missions
        .filter((m) => m.state === 'failed')
        .sort((a, b) => b.declared_at.localeCompare(a.declared_at))
        .slice(0, 5)

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-semibold">Stats</h1>

            <section>
                <h2 className="text-sm font-semibold mb-2">State breakdown</h2>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                    {Object.entries(counts).map(([s, n]) => (
                        <div key={s} className="rounded border p-2">
                            <div className="text-muted-foreground text-xs">{s}</div>
                            <div className="text-lg font-medium">{n}</div>
                        </div>
                    ))}
                </div>
                <p className="text-sm text-muted-foreground pt-2">
                    Total live: {totalLive} · Total terminal: {totalTerminal}
                </p>
            </section>

            <section>
                <h2 className="text-sm font-semibold mb-2">
                    Recent failures ({recentFailures.length})
                </h2>
                {recentFailures.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No failures. 🎉</p>
                ) : (
                    <ul className="space-y-1 text-sm">
                        {recentFailures.map((m) => (
                            <li key={m.id} className="flex items-center gap-3">
                                <StateBadge state="failed" />
                                <Link href={`/missions/${m.id}`} className="hover:underline flex-1 truncate">
                                    {m.intent_text}
                                </Link>
                                <RelativeTime timestamp={m.declared_at} />
                            </li>
                        ))}
                    </ul>
                )}
            </section>
        </div>
    )
}
