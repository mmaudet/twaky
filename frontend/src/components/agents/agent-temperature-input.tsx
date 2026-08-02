'use client'

import { Slider } from '@/components/ui/slider'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'

type Props = {
    value: number | null
    onChange: (v: number | null) => void
}

export function AgentTemperatureInput({ value, onChange }: Props) {
    const useDefault = value === null
    const sliderValue = value ?? 0.7

    return (
        <div className="space-y-2">
            <Label htmlFor="agent-temperature">Temperature</Label>
            <div className="flex items-center gap-4">
                <Slider
                    id="agent-temperature"
                    disabled={useDefault}
                    min={0.0}
                    max={2.0}
                    step={0.05}
                    value={[sliderValue]}
                    onValueChange={([v]) => onChange(v)}
                    className="flex-1"
                />
                <code className="font-mono text-sm w-16 text-right">
                    {useDefault ? '—' : sliderValue.toFixed(2)}
                </code>
            </div>
            <div className="flex items-center gap-2 pt-1">
                <Checkbox
                    id="temperature-use-default"
                    checked={useDefault}
                    onCheckedChange={(checked) => onChange(checked ? null : 0.7)}
                />
                <label htmlFor="temperature-use-default" className="text-sm">
                    Use LiteLLM default (varies by provider)
                </label>
            </div>
        </div>
    )
}
