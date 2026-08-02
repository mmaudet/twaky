'use client'

import { useState } from 'react'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const DEFAULT_KNOWN = 'claude-sonnet-4-5-20250929,openai/gpt-4o,openai/gpt-4o-mini,openrouter/moonshotai/kimi-k2-0905,ollama/llama3'
const KNOWN_MODELS = (process.env.NEXT_PUBLIC_TWAKY_KNOWN_MODELS ?? DEFAULT_KNOWN)
    .split(',').map(s => s.trim()).filter(Boolean)

type Props = {
    value: string | null
    onChange: (v: string | null) => void
    effectiveDefault: string   // shown in the "Use default" option label
}

const USE_DEFAULT = '__use_default__'
const CUSTOM = '__custom__'

function valueToSelectKey(value: string | null): string {
    if (value === null) return USE_DEFAULT
    if (KNOWN_MODELS.includes(value)) return value
    return CUSTOM
}

export function AgentModelInput({ value, onChange, effectiveDefault }: Props) {
    // Derive the select key directly from the controlled value — no setState needed.
    const selectValue = valueToSelectKey(value)

    // customText is purely local: it buffers what the user types in the custom input.
    // Initialised once from the initial value; parent owns truth via onChange.
    const [customText, setCustomText] = useState<string>(
        value !== null && !KNOWN_MODELS.includes(value) ? value : '',
    )

    const handleSelect = (next: string) => {
        if (next === USE_DEFAULT) onChange(null)
        else if (next === CUSTOM) onChange(customText || '')
        else onChange(next)
    }

    const handleCustomText = (text: string) => {
        setCustomText(text)
        onChange(text)
    }

    return (
        <div className="space-y-2">
            <Label htmlFor="agent-model">Model</Label>
            <Select value={selectValue} onValueChange={handleSelect}>
                <SelectTrigger id="agent-model">
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value={USE_DEFAULT}>
                        Use default ({effectiveDefault})
                    </SelectItem>
                    {KNOWN_MODELS.map((m) => (
                        <SelectItem key={m} value={m}>{m}</SelectItem>
                    ))}
                    <SelectItem value={CUSTOM}>Custom…</SelectItem>
                </SelectContent>
            </Select>
            {selectValue === CUSTOM && (
                <Input
                    id="agent-model-custom"
                    aria-label="Custom model string"
                    value={customText}
                    onChange={(e) => handleCustomText(e.target.value)}
                    placeholder="e.g. openrouter/moonshotai/kimi-k2-0905"
                />
            )}
        </div>
    )
}
