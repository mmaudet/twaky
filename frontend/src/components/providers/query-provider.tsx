'use client'

import { QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { createQueryClient } from '@/lib/query-client'

export function QueryProvider({ children }: { children: ReactNode }) {
    // Use useState to guarantee a stable QueryClient across re-renders,
    // and one QueryClient per browser session (Next.js SSR/CSR boundary).
    const [client] = useState(() => createQueryClient())
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
