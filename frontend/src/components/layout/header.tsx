import Link from 'next/link'
import { SSEIndicator } from './sse-indicator'
import { UserDropdown } from './user-dropdown'

export function Header() {
    return (
        <header className="border-b sticky top-0 z-10 bg-background">
            <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
                <div className="flex items-center gap-4">
                    <Link href="/" className="font-semibold">
                        Twaky
                    </Link>
                    <nav className="flex items-center gap-3 text-sm text-muted-foreground">
                        <Link href="/" className="hover:text-foreground">Dashboard</Link>
                        <span>·</span>
                        <Link href="/agents" className="hover:text-foreground">Agents</Link>
                        <span>·</span>
                        <Link href="/skills" className="hover:text-foreground">Skills</Link>
                        <span>·</span>
                        <Link href="/stats" className="hover:text-foreground">Stats</Link>
                    </nav>
                </div>
                <div className="flex items-center gap-3">
                    <UserDropdown />
                    <SSEIndicator />
                </div>
            </div>
        </header>
    )
}
