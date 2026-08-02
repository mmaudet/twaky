'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { useResumeMission } from '@/hooks/use-resume-mission'
import { useCancelMission } from '@/hooks/use-cancel-mission'

interface DraftArtifact {
    kind: 'approve_draft'
    draft: string
    to?: string
    subject?: string
}

export function ApproveDraftForm({
    missionId,
    artifact,
}: {
    missionId: string
    artifact: DraftArtifact
}) {
    const [draft, setDraft] = useState(artifact.draft)
    const resume = useResumeMission()
    const cancel = useCancelMission()

    async function handleApprove() {
        try {
            await resume.mutateAsync({
                id: missionId,
                userResponse: { approved: true, draft },
            })
            toast.success('Draft approved')
        } catch { /* global handler */ }
    }

    async function handleReject() {
        try {
            await cancel.mutateAsync({
                id: missionId,
                reason: 'user_rejected_draft',
            })
            toast.success('Mission cancelled')
        } catch { /* global handler */ }
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle>Approve draft</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                {artifact.to && (
                    <div className="text-sm">
                        <span className="text-muted-foreground">To:</span> {artifact.to}
                    </div>
                )}
                {artifact.subject && (
                    <div className="text-sm">
                        <span className="text-muted-foreground">Subject:</span> {artifact.subject}
                    </div>
                )}
                <Textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={15}
                    className="font-mono text-sm"
                />
                <div className="flex justify-end gap-2">
                    <Button
                        variant="destructive"
                        onClick={handleReject}
                        disabled={resume.isPending || cancel.isPending}
                    >
                        Cancel mission
                    </Button>
                    <Button
                        onClick={handleApprove}
                        disabled={resume.isPending || cancel.isPending || !draft.trim()}
                    >
                        {resume.isPending ? 'Approving…' : 'Approve →'}
                    </Button>
                </div>
            </CardContent>
        </Card>
    )
}
