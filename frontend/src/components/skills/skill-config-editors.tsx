'use client'

import Ajv from 'ajv'
import dynamic from 'next/dynamic'
import { useMemo, useState } from 'react'
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Button } from '@/components/ui/button'

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), { ssr: false })

const ajv = new Ajv({ allErrors: true, strict: false })

export function SkillConfigEditors({
  schema, values, onSchemaChange, onValuesChange,
}: {
  schema: Record<string, unknown>
  values: Record<string, unknown>
  onSchemaChange: (o: Record<string, unknown>) => void
  onValuesChange: (o: Record<string, unknown>) => void
}) {
  const [schemaText, setSchemaText] = useState(JSON.stringify(schema, null, 2))
  const [valuesText, setValuesText] = useState(JSON.stringify(values, null, 2))
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const [valuesError, setValuesError] = useState<string | null>(null)

  const validate = useMemo(() => {
    try {
      return ajv.compile(schema)
    } catch {
      return null
    }
  }, [schema])

  function handleSchemaEdit(v: string) {
    setSchemaText(v)
    try {
      const parsed = JSON.parse(v) as Record<string, unknown>
      onSchemaChange(parsed)
      setSchemaError(null)
    } catch {
      setSchemaError('Invalid JSON')
    }
  }

  function handleValuesEdit(v: string) {
    setValuesText(v)
    try {
      const parsed = JSON.parse(v) as Record<string, unknown>
      onValuesChange(parsed)
      if (validate && !validate(parsed)) {
        setValuesError(ajv.errorsText(validate.errors))
      } else {
        setValuesError(null)
      }
    } catch {
      setValuesError('Invalid JSON')
    }
  }

  return (
    <div className="space-y-2">
      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm">Config schema (JSON)</Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border rounded" style={{ height: 150 }}>
            <MonacoEditor
              language="json" theme="vs-dark"
              value={schemaText} onChange={(v) => handleSchemaEdit(v ?? '')}
              options={{ minimap: { enabled: false }, fontSize: 12 }}
            />
          </div>
          {schemaError && <p className="text-xs text-destructive">{schemaError}</p>}
        </CollapsibleContent>
      </Collapsible>

      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm">Config values (JSON)</Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border rounded" style={{ height: 150 }}>
            <MonacoEditor
              language="json" theme="vs-dark"
              value={valuesText} onChange={(v) => handleValuesEdit(v ?? '')}
              options={{ minimap: { enabled: false }, fontSize: 12 }}
            />
          </div>
          {valuesError && <p className="text-xs text-destructive">{valuesError}</p>}
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
