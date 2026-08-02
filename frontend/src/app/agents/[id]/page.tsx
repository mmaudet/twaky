'use client'

import Link from 'next/link'
import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RelativeTime } from '@/components/missions/relative-time'
import { AgentPromptInput } from '@/components/agents/agent-prompt-input'
import { AgentModelInput } from '@/components/agents/agent-model-input'
import { AgentTemperatureInput } from '@/components/agents/agent-temperature-input'
import { useAgent, useUpdateAgent, type Agent, type AgentUpdate } from '@/hooks/use-agents'

// Inner form — receives the loaded agent, so useState can be lazily initialized
// from props without needing a useEffect hydration step.
function AgentEditForm({ agent }: { agent: Agent }) {
    const router = useRouter()
    const update = useUpdateAgent(agent.id)

    const [prompt, setPrompt] = useState(agent.system_prompt)
    const [model, setModel] = useState<string | null>(agent.model)
    const [temperature, setTemperature] = useState<number | null>(agent.temperature)

    const trimmedPrompt = prompt.trim()
    const isDirty =
        trimmedPrompt !== agent.system_prompt.trim() ||
        model !== agent.model ||
        temperature !== agent.temperature
    const isValid = trimmedPrompt.length >= 1 && trimmedPrompt.length <= 8000

    const handleSave = () => {
        const patch: AgentUpdate = {}
        if (trimmedPrompt !== agent.system_prompt.trim()) patch.system_prompt = trimmedPrompt
        if (model !== agent.model) patch.model = model
        if (temperature !== agent.temperature) patch.temperature = temperature

        update.mutate(patch, {
            onSuccess: () => {
                toast.success('Saved. Changes apply to the next mission.')
                router.push('/agents')
            },
            onError: (err) => {
                toast.error(err.message || 'Save failed')
            },
        })
    }

    return (
        <div className="space-y-6">
            <div>
                <Link href="/agents" className="text-sm text-muted-foreground hover:underline">
                    ← Back to agents
                </Link>
            </div>

            <div className="space-y-2">
                <h1 className="text-2xl font-semibold">Edit {agent.display_name}</h1>
                <div className="flex items-center gap-3 text-sm text-muted-foreground">
                    <Badge variant={agent.role === 'orchestrator' ? 'default' : 'secondary'}>
                        {agent.role}
                    </Badge>
                    <span>·</span>
                    <span>updated <RelativeTime timestamp={agent.updated_at} /></span>
                </div>
            </div>

            <AgentPromptInput value={prompt} onChange={setPrompt} />

            <AgentModelInput
                value={model}
                onChange={setModel}
                effectiveDefault={agent.effective_model}
            />

            <AgentTemperatureInput value={temperature} onChange={setTemperature} />

            <div className="flex items-center justify-end gap-2 pt-4">
                <Link href="/agents">
                    <Button variant="ghost">Cancel</Button>
                </Link>
                <Button
                    onClick={handleSave}
                    disabled={!isDirty || !isValid || update.isPending}
                >
                    {update.isPending ? 'Saving…' : 'Save'}
                </Button>
            </div>
        </div>
    )
}

export default function AgentEditPage({
    params,
}: { params: Promise<{ id: string }> }) {
    const { id } = use(params)
    const { data: agent, isLoading, error } = useAgent(id)

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>
    if (!agent) return <p>Not found.</p>

    return <AgentEditForm agent={agent} />
}
