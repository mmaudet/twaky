'use client'

import { ApproveDraftForm } from './approve-draft-form'
import { GenericResumeForm } from './generic-resume-form'

interface Artifact {
    kind?: string
    [key: string]: unknown
}

/**
 * Looks through the mission's artifacts for the most recent pending_user_input.
 *
 * By convention (see sub-project 2's cooperative pattern), the artifact that
 * triggered the pause has a `kind` matching a known handler:
 *     - "approve_draft" → specialised UI (Plume drafting)
 *     - anything else → generic JSON fallback
 */
function findPending(artifacts: Artifact[]): Artifact | undefined {
    for (let i = artifacts.length - 1; i >= 0; i--) {
        if (artifacts[i]?.kind) return artifacts[i]
    }
    return undefined
}

export function ResumeForm({
    missionId,
    artifacts,
}: {
    missionId: string
    artifacts: Artifact[]
}) {
    const artifact = findPending(artifacts)
    if (!artifact) {
        return <GenericResumeForm missionId={missionId} kind="unknown" />
    }

    if (artifact.kind === 'approve_draft') {
        return <ApproveDraftForm
            missionId={missionId}
            artifact={artifact as never}
        />
    }
    return <GenericResumeForm missionId={missionId} kind={artifact.kind ?? 'unknown'} />
}
