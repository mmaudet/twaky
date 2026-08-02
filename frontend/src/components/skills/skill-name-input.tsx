'use client'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const NAME_RE = /^[a-z][a-z0-9_]{0,63}$/

export function SkillNameInput({
  value, onChange, disabled,
}: {
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  const isValid = value === '' || NAME_RE.test(value)
  return (
    <div className="space-y-1">
      <Label htmlFor="skill-name">Name</Label>
      <Input
        id="skill-name"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="search_wikipedia"
        disabled={disabled}
        aria-invalid={!isValid}
      />
      {!isValid && (
        <p className="text-xs text-destructive">
          Must match <code>^[a-z][a-z0-9_]{'{'}0,63{'}'}$</code>
          {' '}(lowercase, digits, underscore; start with letter; 1-64 chars).
        </p>
      )}
    </div>
  )
}
