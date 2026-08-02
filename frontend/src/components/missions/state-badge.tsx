import { Badge } from '@/components/ui/badge'
import type { MissionState } from '@/hooks/use-missions'
import { cn } from '@/lib/utils'

const STYLE: Record<MissionState, string> = {
	declared:       'bg-slate-100 text-slate-700 border-slate-300',
	planning:       'bg-blue-100 text-blue-700 border-blue-300',
	running:        'bg-blue-200 text-blue-900 border-blue-400',
	awaiting_user:  'bg-yellow-100 text-yellow-800 border-yellow-400',
	done:           'bg-green-100 text-green-800 border-green-300',
	failed:         'bg-red-100 text-red-800 border-red-300',
	cancelled:      'bg-gray-100 text-gray-600 border-gray-300',
}

const ICON: Record<MissionState, string> = {
	declared: '○', planning: '◐', running: '●',
	awaiting_user: '⚑', done: '✓', failed: '✗', cancelled: '⊘',
}

export function StateBadge({ state }: { state: MissionState }) {
	return (
		<Badge variant="outline" className={cn('gap-1 font-mono', STYLE[state])}>
			<span aria-hidden>{ICON[state]}</span>
			<span>{state}</span>
		</Badge>
	)
}
