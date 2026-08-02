'use client'

import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'

type Props = {
    value: string
    onChange: (v: string) => void
    max?: number
}

export function AgentPromptInput({ value, onChange, max = 8000 }: Props) {
    const length = value.length
    const overLimit = length > max
    return (
        <div className="space-y-2">
            <Label htmlFor="agent-prompt">System prompt</Label>
            <Textarea
                id="agent-prompt"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                rows={15}
                className="font-mono text-sm resize-y"
            />
            <p className={`text-xs text-right ${overLimit ? 'text-red-600' : 'text-muted-foreground'}`}>
                {length.toLocaleString()} / {max.toLocaleString()}
            </p>
        </div>
    )
}
