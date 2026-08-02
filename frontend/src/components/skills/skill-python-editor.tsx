'use client'

import dynamic from 'next/dynamic'

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), { ssr: false })

const STARTER = `def run(**kwargs) -> str:
    """One-line description shown to the LLM."""
    # kwargs come from the LLM; config injected via config_values
    return "hello"
`

export function SkillPythonEditor({
  value, onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="border rounded-md overflow-hidden" style={{ height: 500 }}>
      <MonacoEditor
        language="python"
        theme="vs-dark"
        value={value || STARTER}
        onChange={(v) => onChange(v ?? '')}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          tabSize: 4,
          scrollBeyondLastLine: false,
        }}
      />
    </div>
  )
}
