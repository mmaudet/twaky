'use client'

import Link from 'next/link'
import type { components } from '@/lib/api-types'
import {
	Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { StateBadge } from './state-badge'
import { RelativeTime } from './relative-time'

type Mission = components['schemas']['Mission']

export function MissionList({ missions }: { missions: Mission[] }) {
	if (missions.length === 0) {
		return (
			<div className="rounded-lg border p-8 text-center text-muted-foreground">
				No missions yet. Click <em>+ New mission</em> to declare one.
			</div>
		)
	}

	return (
		<Table>
			<TableHeader>
				<TableRow>
					<TableHead className="w-40">State</TableHead>
					<TableHead>Intent</TableHead>
					<TableHead className="w-32">Declared</TableHead>
					<TableHead className="w-10"></TableHead>
				</TableRow>
			</TableHeader>
			<TableBody>
				{missions.map((m) => (
					<TableRow
						key={m.id}
						className={m.state === 'awaiting_user'
							? 'bg-yellow-50 hover:bg-yellow-100'
							: 'hover:bg-muted/50'}
					>
						<TableCell><StateBadge state={m.state} /></TableCell>
						<TableCell className="truncate max-w-md">
							<Link href={`/missions/${m.id}`} className="hover:underline">
								{m.intent_text}
							</Link>
						</TableCell>
						<TableCell><RelativeTime timestamp={m.declared_at} /></TableCell>
						<TableCell>
							<Link href={`/missions/${m.id}`}>→</Link>
						</TableCell>
					</TableRow>
				))}
			</TableBody>
		</Table>
	)
}
