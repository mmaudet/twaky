'use client'

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
import { Button } from '@/components/ui/button'
import { useDefaultPrompt } from '@/hooks/use-agents'

type Props = {
    agentId: string
    displayName: string
    /** Called with the default prompt string when the user confirms. */
    onReset: (defaultPrompt: string) => void
}

export function ResetToDefaultsDialog({ agentId, displayName, onReset }: Props) {
    const [open, setOpen] = useState(false)
    const { refetch, isFetching } = useDefaultPrompt(agentId)

    const handleConfirm = async () => {
        const { data } = await refetch()
        if (data?.system_prompt) {
            onReset(data.system_prompt)
        }
        setOpen(false)
    }

    return (
        <AlertDialog open={open} onOpenChange={setOpen}>
            <AlertDialogTrigger asChild>
                <Button variant="outline">Reset to defaults</Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Reset {displayName}?</AlertDialogTitle>
                    <AlertDialogDescription>
                        This resets the system prompt to the built-in default and
                        clears the model and temperature overrides. You still need
                        to click Save to persist the change.
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={handleConfirm} disabled={isFetching}>
                        {isFetching ? 'Loading…' : 'Reset'}
                    </AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    )
}
