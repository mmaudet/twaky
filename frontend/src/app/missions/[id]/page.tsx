'use client'

import Link from 'next/link'
import { use } from 'react'
import { StateBadge } from '@/components/missions/state-badge'
import { RelativeTime } from '@/components/missions/relative-time'
import { StateTimeline } from '@/components/missions/state-timeline'
import { ArtifactAccordion } from '@/components/missions/artifact-accordion'
import { CancelMissionDialog } from '@/components/missions/cancel-mission-dialog'
import { Button } from '@/components/ui/button'
import { useMission } from '@/hooks/use-mission'

export default function MissionDetailPage({
    params,
}: { params: Promise<{ id: string }> }) {
    const { id } = use(params)
    const { data: mission, isLoading, error } = useMission(id)

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>
    if (!mission) return <p>Not found.</p>

    const terminal = ['done', 'failed', 'cancelled'].includes(mission.state)

    return (
        <div className="space-y-6">
            <div>
                <Link href="/" className="text-sm text-muted-foreground hover:underline">
                    ← Back to missions
                </Link>
            </div>

            <div className="space-y-2">
                <h1 className="text-2xl font-semibold">{mission.intent_text}</h1>
                <div className="flex items-center gap-3 text-sm text-muted-foreground">
                    <StateBadge state={mission.state} />
                    <span>·</span>
                    <span>declared <RelativeTime timestamp={mission.declared_at} /></span>
                    <span>·</span>
                    <span>{mission.declared_by}</span>
                </div>
                <div className="flex items-center gap-2 pt-2">
                    {!terminal && <CancelMissionDialog missionId={mission.id} />}
                    <a href={`/api/missions/${mission.id}/trace`} target="_blank" rel="noreferrer">
                        <Button variant="outline" size="sm">Open in Langfuse ↗</Button>
                    </a>
                </div>
                {terminal && mission.state_reason && (
                    <p className="pt-2 text-sm">
                        Terminal reason: <code>{mission.state_reason}</code>
                    </p>
                )}
            </div>

            <section>
                <h2 className="text-sm font-semibold mb-2">State timeline</h2>
                <StateTimeline
                    currentState={mission.state}
                    declaredAt={mission.declared_at}
                />
            </section>

            <section>
                <h2 className="text-sm font-semibold mb-2">
                    Artifacts ({mission.artifacts?.length ?? 0})
                </h2>
                <ArtifactAccordion artifacts={mission.artifacts ?? []} />
            </section>

            {/* Resume form mounted here in T12 when state === 'awaiting_user'. */}
        </div>
    )
}
