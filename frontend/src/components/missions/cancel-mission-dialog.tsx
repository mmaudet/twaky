'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
    Dialog, DialogClose, DialogContent, DialogFooter,
    DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useCancelMission } from '@/hooks/use-cancel-mission'

export function CancelMissionDialog({ missionId }: { missionId: string }) {
    const router = useRouter()
    const [open, setOpen] = useState(false)
    const [reason, setReason] = useState('user_requested')
    const cancel = useCancelMission()

    async function handleConfirm() {
        try {
            await cancel.mutateAsync({ id: missionId, reason: reason.trim() || 'user_requested' })
            toast.success('Mission cancelled')
            setOpen(false)
            router.push('/')
        } catch { /* handled globally */ }
    }

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button variant="destructive" size="sm">Cancel mission</Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Cancel this mission?</DialogTitle>
                </DialogHeader>
                <p className="text-sm text-muted-foreground">
                    The mission will move to state <code>cancelled</code>. Optionally add a reason.
                </p>
                <Textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    rows={2}
                    maxLength={256}
                />
                <DialogFooter>
                    <DialogClose asChild><Button variant="ghost">Keep it</Button></DialogClose>
                    <Button
                        variant="destructive"
                        onClick={handleConfirm}
                        disabled={cancel.isPending}
                    >
                        {cancel.isPending ? 'Cancelling…' : 'Cancel mission'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
