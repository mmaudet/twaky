'use client'

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { components } from '@/lib/api-types'

type MissionState = components['schemas']['MissionState']

interface MissionChangedPayload {
    mission_id: string
    state: MissionState
    at: string
}

export type SSEStatus = 'connected' | 'reconnecting' | 'disconnected'

const SSEStatusContext = createContext<SSEStatus>('disconnected')

export function useSSEStatus(): SSEStatus {
    return useContext(SSEStatusContext)
}

export function SSEProvider({ children }: { children: ReactNode }) {
    const qc = useQueryClient()
    const [status, setStatus] = useState<SSEStatus>('disconnected')

    useEffect(() => {
        const es = new EventSource('/api/events')

        es.onopen = () => setStatus('connected')

        es.addEventListener('mission_changed', (evt) => {
            try {
                const payload = JSON.parse(
                    (evt as MessageEvent).data
                ) as MissionChangedPayload
                qc.invalidateQueries({ queryKey: ['mission', payload.mission_id] })
                qc.invalidateQueries({ queryKey: ['missions'] })
            } catch {
                // Malformed payload — ignore (server contract violation, logged elsewhere).
            }
        })

        es.onerror = () => {
            // Browser EventSource auto-reconnects. On error we invalidate to force
            // a refetch once the connection is back — any missed events surface via
            // the fresh data.
            setStatus('reconnecting')
            qc.invalidateQueries({ queryKey: ['missions'] })
        }

        return () => {
            es.close()
            setStatus('disconnected')
        }
    }, [qc])

    return (
        <SSEStatusContext.Provider value={status}>
            {children}
        </SSEStatusContext.Provider>
    )
}
