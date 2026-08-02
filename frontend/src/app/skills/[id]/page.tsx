'use client'

import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { SkillBoundAgents } from '@/components/skills/skill-bound-agents'
import { SkillConfigEditors } from '@/components/skills/skill-config-editors'
import { SkillNameInput } from '@/components/skills/skill-name-input'
import { SkillPythonEditor } from '@/components/skills/skill-python-editor'
import { SkillTestDialog } from '@/components/skills/skill-test-dialog'
import {
  useCreateSkill, useSkill, useUpdateSkill, type Skill,
} from '@/hooks/use-skills'

const NAME_RE = /^[a-z][a-z0-9_]{0,63}$/
type AgentId = 'atlas' | 'chronos' | 'plume' | 'iris'

// Inner form receives the loaded skill (or undefined for new) so useState
// is lazily initialized from props without needing a useEffect hydration step.
function SkillEditForm({ skill, id }: { skill: Skill | undefined, id: string }) {
  const isNew = id === 'new'
  const router = useRouter()
  const createSkill = useCreateSkill()
  const updateSkill = useUpdateSkill(id)

  const [name, setName] = useState(skill?.name ?? '')
  const [description, setDescription] = useState(skill?.description ?? '')
  const [pythonSource, setPythonSource] = useState(skill?.python_source ?? '')
  const [boundAgents, setBoundAgents] = useState<AgentId[]>(
    (skill?.bound_agents ?? []) as AgentId[],
  )
  const [enabled, setEnabled] = useState(skill?.enabled ?? true)
  const [configSchema, setConfigSchema] = useState<Record<string, unknown>>(
    (skill?.config_schema ?? {}) as Record<string, unknown>,
  )
  const [configValues, setConfigValues] = useState<Record<string, unknown>>(
    (skill?.config_values ?? {}) as Record<string, unknown>,
  )
  const [dirty, setDirty] = useState(isNew)

  function mark<T>(setter: (v: T) => void) {
    return (v: T) => { setter(v); setDirty(true) }
  }

  const isFormValid =
    NAME_RE.test(name)
    && description.trim().length >= 1 && description.length <= 1000
    && pythonSource.trim().length >= 1 && pythonSource.length <= 32000

  async function handleSave() {
    const body = {
      name, description, python_source: pythonSource,
      bound_agents: boundAgents, enabled,
      config_schema: configSchema, config_values: configValues,
    }
    try {
      if (isNew) {
        const created = await createSkill.mutateAsync(body)
        toast.success(`Skill '${created!.name}' created`)
        router.push(`/skills/${created!.id}`)
      } else {
        await updateSkill.mutateAsync(body)
        toast.success('Saved')
        setDirty(false)
      }
    } catch (e) {
      toast.error(`Save failed: ${(e as Error).message}`)
    }
  }

  return (
    <div className="p-6 grid grid-cols-3 gap-6">
      <div className="col-span-2 space-y-2">
        <Label>Python source</Label>
        <SkillPythonEditor value={pythonSource} onChange={mark(setPythonSource)} />
      </div>

      <div className="space-y-4">
        <SkillNameInput value={name} onChange={mark(setName)} disabled={!isNew} />

        <div className="space-y-1">
          <Label htmlFor="desc">Description</Label>
          <Textarea
            id="desc"
            rows={3}
            value={description}
            onChange={(e) => { setDescription(e.target.value); setDirty(true) }}
          />
          <p className="text-xs text-muted-foreground text-right">
            {description.length} / 1000
          </p>
        </div>

        <SkillBoundAgents value={boundAgents} onChange={mark(setBoundAgents)} />

        <div className="flex items-center space-x-2">
          <Switch
            id="enabled"
            checked={enabled}
            onCheckedChange={mark(setEnabled)}
          />
          <Label htmlFor="enabled">Enabled</Label>
        </div>

        <SkillConfigEditors
          schema={configSchema} values={configValues}
          onSchemaChange={(o) => { setConfigSchema(o); setDirty(true) }}
          onValuesChange={(o) => { setConfigValues(o); setDirty(true) }}
        />
      </div>

      <div className="col-span-3 flex items-center justify-between border-t pt-4">
        <SkillTestDialog
          skillId={id}
          disabled={isNew}
          tooltip={isNew ? 'Save the skill first, then test.' : undefined}
        />
        <div className="space-x-2">
          <Button variant="outline" onClick={() => router.push('/skills')}>Cancel</Button>
          <Button onClick={handleSave} disabled={!isFormValid || !dirty}>
            {isNew ? 'Create' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  )
}

export default function SkillEditPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const isNew = id === 'new'

  const skillQuery = useSkill(isNew ? undefined : id)

  if (!isNew && skillQuery.isLoading) {
    return <div className="p-8 text-muted-foreground">Loading…</div>
  }

  if (!isNew && skillQuery.error) {
    return <p className="text-red-600">Error: {skillQuery.error.message}</p>
  }

  return <SkillEditForm skill={skillQuery.data} id={id} />
}
