'use client'

import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'

const AGENTS = ['atlas', 'chronos', 'plume', 'iris'] as const
type AgentId = typeof AGENTS[number]

export function SkillBoundAgents({
  value, onChange,
}: {
  value: AgentId[]
  onChange: (next: AgentId[]) => void
}) {
  function toggle(agent: AgentId, checked: boolean) {
    onChange(
      checked
        ? [...value.filter((a) => a !== agent), agent]
        : value.filter((a) => a !== agent),
    )
  }
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Bound agents</legend>
      {AGENTS.map((a) => (
        <div key={a} className="flex items-center space-x-2">
          <Checkbox
            id={`bind-${a}`}
            checked={value.includes(a)}
            onCheckedChange={(c) => toggle(a, c === true)}
          />
          <Label htmlFor={`bind-${a}`} className="font-normal capitalize">{a}</Label>
        </div>
      ))}
    </fieldset>
  )
}
