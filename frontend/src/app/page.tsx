'use client'

import { useState } from 'react'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { MissionList } from '@/components/missions/mission-list'
import { NewMissionDialog } from '@/components/missions/new-mission-dialog'
import { useMissions, type MissionState } from '@/hooks/use-missions'

type Filter = 'all' | 'live' | MissionState

const LIVE_STATES = new Set<MissionState>([
	'declared', 'planning', 'running', 'awaiting_user',
])

export default function DashboardPage() {
	const [filter, setFilter] = useState<Filter>('live')

	const queryState: MissionState | undefined =
		filter === 'all' || filter === 'live' ? undefined : filter
	// Include terminal missions when the user browses done/failed/cancelled/all tabs.
	const includeTerminal = filter !== 'live'
	const { data, isLoading, error } = useMissions(queryState, includeTerminal)

	const missions = (data ?? []).filter((m) => {
		if (filter === 'all') return true
		if (filter === 'live') return LIVE_STATES.has(m.state)
		return m.state === filter
	})

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between">
				<h1 className="text-2xl font-semibold">Missions</h1>
				<NewMissionDialog />
			</div>

			<ToggleGroup
				type="single"
				value={filter}
				onValueChange={(v) => v && setFilter(v as Filter)}
			>
				<ToggleGroupItem value="all">All</ToggleGroupItem>
				<ToggleGroupItem value="live">Live</ToggleGroupItem>
				<ToggleGroupItem value="done">Done</ToggleGroupItem>
				<ToggleGroupItem value="failed">Failed</ToggleGroupItem>
				<ToggleGroupItem value="cancelled">Cancelled</ToggleGroupItem>
			</ToggleGroup>

			{isLoading && <p className="text-muted-foreground">Loading…</p>}
			{error && (
				<p className="text-red-600">Failed to load missions: {error.message}</p>
			)}
			{data && <MissionList missions={missions} />}
		</div>
	)
}
