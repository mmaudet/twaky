'use client'

import { useRouter } from 'next/navigation'
import { useMe } from '@/hooks/use-me'
import { Button } from '@/components/ui/button'
import {
    DropdownMenu, DropdownMenuContent, DropdownMenuItem,
    DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export function UserDropdown() {
    const router = useRouter()
    const { data: me, isLoading } = useMe()

    function handleSignOut() {
        // POST /oauth/logout — the API clears the cookie and 302s to LemonLDAP.
        // Use full navigation (not fetch) so the browser follows the 302 chain.
        const form = document.createElement('form')
        form.method = 'POST'
        form.action = '/api/oauth/logout'
        document.body.appendChild(form)
        form.submit()
    }

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" disabled={isLoading}>
                    {me?.owner_email ?? '…'} ▾
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => router.push('/me')}>
                    Profile
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleSignOut}>
                    Sign out
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    )
}
