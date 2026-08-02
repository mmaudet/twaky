import type { Metadata } from 'next'
import { Toaster } from '@/components/ui/sonner'
import { QueryProvider } from '@/components/providers/query-provider'
import { SSEProvider } from '@/components/providers/sse-provider'
import { Header } from '@/components/layout/header'
import { LoginTimeTracker } from '@/components/providers/login-time-tracker'
import './globals.css'

export const metadata: Metadata = {
    title: 'Twaky',
    description: 'Twaky Control Tower',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="en" suppressHydrationWarning>
            <body>
                <LoginTimeTracker />
                <QueryProvider>
                    <SSEProvider>
                        <Header />
                        <main className="mx-auto max-w-6xl px-4 py-6">
                            {children}
                        </main>
                        <Toaster />
                    </SSEProvider>
                </QueryProvider>
            </body>
        </html>
    )
}
