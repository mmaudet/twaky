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
import { useDeclareMission } from '@/hooks/use-declare-mission'

export function NewMissionDialog() {
	const router = useRouter()
	const [open, setOpen] = useState(false)
	const [intent, setIntent] = useState('')
	const declare = useDeclareMission()

	async function handleSubmit() {
		const trimmed = intent.trim()
		if (!trimmed) return
		try {
			const mission = await declare.mutateAsync(trimmed)
			toast.success('Mission declared')
			setIntent('')
			setOpen(false)
			router.push(`/missions/${mission.id}`)
		} catch {
			// Error toast handled by global mutation onError.
		}
	}

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button>+ New mission</Button>
			</DialogTrigger>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Declare a new mission</DialogTitle>
				</DialogHeader>
				<Textarea
					value={intent}
					onChange={(e) => setIntent(e.target.value)}
					placeholder="What should the assistant do?"
					rows={6}
					maxLength={4096}
					autoFocus
				/>
				<DialogFooter>
					<DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
					<Button
						onClick={handleSubmit}
						disabled={declare.isPending || !intent.trim()}
					>
						{declare.isPending ? 'Declaring…' : 'Declare'}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	)
}
