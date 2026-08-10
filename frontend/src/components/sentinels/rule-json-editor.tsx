'use client'

import dynamic from 'next/dynamic'
import { useState } from 'react'
import Ajv from 'ajv'

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), { ssr: false })

const ajv = new Ajv({ allErrors: true })

/**
 * JSON Schema for a mail sentinel rule, mirroring T14's server-side validation.
 */
const RULE_SCHEMA = {
    type: 'object',
    required: ['name', 'actions'],
    additionalProperties: true,
    properties: {
        name: { type: 'string', minLength: 1 },
        description: { type: 'string' },
        conditions: {
            type: 'array',
            items: {
                type: 'object',
                required: ['field', 'operator', 'value'],
                properties: {
                    field: {
                        type: 'string',
                        enum: ['from', 'to', 'subject', 'body', 'header'],
                    },
                    operator: {
                        type: 'string',
                        enum: ['equals', 'contains', 'regex', 'glob'],
                    },
                    value: { type: 'string' },
                },
            },
        },
        combinator: {
            type: 'string',
            enum: ['OR', 'AND'],
        },
        actions: {
            type: 'array',
            minItems: 1,
            items: {
                type: 'string',
                enum: [
                    'archive',
                    'label_important',
                    'label_newsletter',
                    'label_receipt',
                    'create_mission',
                    'delegate_to_atlas',
                ],
            },
        },
        priority: { type: 'integer', minimum: 0 },
        enabled: { type: 'boolean' },
        run_on_threads: { type: 'boolean' },
    },
}

const validateSchema = ajv.compile(RULE_SCHEMA)

/** Pure validation function — no React side effects. */
export function validateRule(raw: string): { isValid: boolean; errors: string[] } {
    if (!raw) {
        return { isValid: false, errors: ['JSON is empty'] }
    }
    let parsed: unknown
    try {
        parsed = JSON.parse(raw)
    } catch (e) {
        const msg = e instanceof SyntaxError ? e.message : 'Invalid JSON'
        return { isValid: false, errors: [msg] }
    }
    const valid = validateSchema(parsed)
    if (!valid && validateSchema.errors) {
        return {
            isValid: false,
            errors: validateSchema.errors.map(
                (err) => `${err.instancePath || '(root)'} ${err.message}`,
            ),
        }
    }
    return { isValid: true, errors: [] }
}

export interface RuleJsonEditorProps {
    value: string
    onChange: (value: string, meta: { isValid: boolean; errors: string[] }) => void
    disabled?: boolean
}

/**
 * Monaco-backed JSON editor with inline ajv validation feedback.
 *
 * Validation is tracked as local state to avoid calling setState in effects.
 * The initial validation is derived synchronously from the `value` prop.
 * onChange is only called when the user edits the content.
 */
export function RuleJsonEditor({ value, onChange, disabled }: RuleJsonEditorProps) {
    // displayErrors tracks what to show in the error panel.
    // Initialised from the prop so we show errors on first paint.
    const [displayErrors, setDisplayErrors] = useState<string[]>(
        () => validateRule(value).errors,
    )

    function handleChange(raw: string) {
        const result = validateRule(raw)
        setDisplayErrors(result.errors)
        onChange(raw, result)
    }

    return (
        <div className="flex flex-col gap-2">
            <div className="border rounded-md overflow-hidden" style={{ height: 420 }}>
                <MonacoEditor
                    language="json"
                    theme="vs-dark"
                    value={value}
                    onChange={(v) => handleChange(v ?? '')}
                    options={{
                        minimap: { enabled: false },
                        fontSize: 13,
                        tabSize: 2,
                        scrollBeyondLastLine: false,
                        readOnly: disabled,
                    }}
                />
            </div>
            {displayErrors.length > 0 && (
                <div
                    className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300"
                    role="alert"
                    aria-label="validation errors"
                >
                    <ul className="list-disc list-inside space-y-0.5">
                        {displayErrors.map((e, i) => (
                            <li key={i}>{e}</li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    )
}
