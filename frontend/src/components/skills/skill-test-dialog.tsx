'use client'

import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useTestSkill } from '@/hooks/use-skills'

type Outcome = 'ok' | 'timeout' | 'crashed' | 'error'

const badgeVariant: Record<Outcome, 'default' | 'destructive'> = {
  ok: 'default',
  timeout: 'destructive',
  crashed: 'destructive',
  error: 'destructive',
}

export function SkillTestDialog({
  skillId, disabled, tooltip,
}: {
  skillId: string
  disabled?: boolean
  tooltip?: string
}) {
  const [open, setOpen] = useState(false)
  const [argsText, setArgsText] = useState('{}')
  const [parseError, setParseError] = useState<string | null>(null)
  const testSkill = useTestSkill(skillId)

  function handleRun() {
    let args: Record<string, unknown>
    try {
      const parsed = JSON.parse(argsText)
      if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
        throw new Error('args must be a JSON object')
      }
      args = parsed as Record<string, unknown>
      setParseError(null)
    } catch (e) {
      setParseError((e as Error).message)
      return
    }
    testSkill.mutate(args)
  }

  const outcome = testSkill.data?.outcome as Outcome | undefined

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" disabled={disabled} title={tooltip}>
          Test
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Test skill</DialogTitle>
        </DialogHeader>

        <Label htmlFor="test-args">Args (JSON object)</Label>
        <Textarea
          id="test-args"
          rows={4}
          value={argsText}
          onChange={(e) => setArgsText(e.target.value)}
          placeholder='{"query": "twake"}'
          className="font-mono"
        />
        {parseError && <p className="text-xs text-destructive">{parseError}</p>}

        {testSkill.isPending && (
          <p className="text-sm text-muted-foreground">Running…</p>
        )}

        {outcome && (
          <div className="space-y-2 border-t pt-3" role="status" aria-live="polite">
            <Badge variant={badgeVariant[outcome]}>outcome: {outcome}</Badge>
            {outcome === 'ok' ? (
              <pre className="text-xs bg-muted p-2 rounded overflow-auto">
                {JSON.stringify(testSkill.data!.result, null, 2)}
              </pre>
            ) : (
              <p className="text-sm text-destructive">{testSkill.data!.message}</p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Close</Button>
          <Button onClick={handleRun} disabled={testSkill.isPending}>Run</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
