'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { useResumeMission } from '@/hooks/use-resume-mission'
import { useCancelMission } from '@/hooks/use-cancel-mission'

export function GenericResumeForm({
    missionId,
    kind,
}: {
    missionId: string
    kind: string
}) {
    const [json, setJson] = useState('{"approved": true}')
    const [jsonError, setJsonError] = useState<string | null>(null)
    const resume = useResumeMission()
    const cancel = useCancelMission()

    async function handleSubmit() {
        let parsed: unknown
        try {
            parsed = JSON.parse(json)
        } catch (e) {
            setJsonError((e as Error).message)
            return
        }
        setJsonError(null)
        try {
            await resume.mutateAsync({ id: missionId, userResponse: parsed as Record<string, unknown> })
            toast.success('Response submitted')
        } catch { /* handled globally */ }
    }

    async function handleCancel() {
        try {
            await cancel.mutateAsync({ id: missionId, reason: 'user_cancelled_generic' })
            toast.success('Mission cancelled')
        } catch { /* handled globally */ }
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle>Action required (kind: {kind})</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                    This mission requires input of type <code>{kind}</code>.
                    Submit a JSON payload matching the agent&apos;s expected schema.
                </p>
                <Textarea
                    value={json}
                    onChange={(e) => { setJson(e.target.value); setJsonError(null) }}
                    rows={8}
                    className="font-mono text-xs"
                    spellCheck={false}
                />
                {jsonError && <p className="text-sm text-red-600">JSON error: {jsonError}</p>}
                <div className="flex justify-end gap-2">
                    <Button
                        variant="destructive"
                        onClick={handleCancel}
                        disabled={resume.isPending || cancel.isPending}
                    >
                        Cancel mission
                    </Button>
                    <Button
                        onClick={handleSubmit}
                        disabled={resume.isPending || cancel.isPending}
                    >
                        {resume.isPending ? 'Submitting…' : 'Submit →'}
                    </Button>
                </div>
            </CardContent>
        </Card>
    )
}
