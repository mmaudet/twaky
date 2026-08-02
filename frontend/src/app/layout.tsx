import type { Metadata } from 'next'
import { Toaster } from '@/components/ui/sonner'
import { QueryProvider } from '@/components/providers/query-provider'
import { SSEProvider } from '@/components/providers/sse-provider'
import './globals.css'

export const metadata: Metadata = {
    title: 'Twaky',
    description: 'Twaky Control Tower',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="en" suppressHydrationWarning>
            <body>
                <QueryProvider>
                    <SSEProvider>
                        {children}
                        <Toaster />
                    </SSEProvider>
                </QueryProvider>
            </body>
        </html>
    )
}
