'use client'

import { useEffect, useState } from 'react'

function humanize(ms: number): string {
	const sec = Math.floor(ms / 1000)
	if (sec < 60) return `${sec}s ago`
	const min = Math.floor(sec / 60)
	if (min < 60) return `${min}m ago`
	const hr = Math.floor(min / 60)
	if (hr < 24) return `${hr}h ago`
	const day = Math.floor(hr / 24)
	return `${day}d ago`
}

export function RelativeTime({ timestamp }: { timestamp: string }) {
	const [now, setNow] = useState(() => Date.now())

	useEffect(() => {
		const interval = setInterval(() => setNow(Date.now()), 30_000)
		return () => clearInterval(interval)
	}, [])

	const parsed = new Date(timestamp).getTime()
	if (isNaN(parsed)) return <span>—</span>

	return <span title={timestamp}>{humanize(now - parsed)}</span>
}
