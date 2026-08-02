'use client'

import { useEffect, useState } from 'react'
import {
    Accordion, AccordionContent, AccordionItem, AccordionTrigger,
} from '@/components/ui/accordion'
import { Badge } from '@/components/ui/badge'
import { RelativeTime } from './relative-time'

interface Artifact {
    kind?: string
    at?: string
    [key: string]: unknown
}

async function highlightJson(json: string): Promise<string> {
    const { codeToHtml } = await import('shiki')
    return codeToHtml(json, {
        lang: 'json',
        theme: 'github-light',
    })
}

function ArtifactBody({ artifact }: { artifact: Artifact }) {
    const [html, setHtml] = useState<string>('')
    const json = JSON.stringify(artifact, null, 2)

    useEffect(() => {
        let cancelled = false
        highlightJson(json).then((h) => { if (!cancelled) setHtml(h) })
        return () => { cancelled = true }
    }, [json])

    if (!html) {
        return <pre className="text-xs overflow-x-auto"><code>{json}</code></pre>
    }
    return <div className="text-xs overflow-x-auto" dangerouslySetInnerHTML={{ __html: html }} />
}

export function ArtifactAccordion({ artifacts }: { artifacts: Artifact[] }) {
    if (artifacts.length === 0) {
        return <p className="text-sm text-muted-foreground">No artifacts yet.</p>
    }

    const defaultOpen = artifacts.slice(-2).map((_, i) =>
        `item-${artifacts.length - 2 + i}`,
    ).filter((k) => k.startsWith('item-') && !k.includes('-')  === false)

    return (
        <Accordion type="multiple" defaultValue={defaultOpen}>
            {artifacts.map((a, idx) => (
                <AccordionItem key={idx} value={`item-${idx}`}>
                    <AccordionTrigger>
                        <div className="flex items-center gap-2 text-sm">
                            <Badge variant="outline">{a.kind ?? 'artifact'}</Badge>
                            <span className="text-muted-foreground">
                                {a.at ? <RelativeTime timestamp={a.at} /> : ''}
                            </span>
                        </div>
                    </AccordionTrigger>
                    <AccordionContent>
                        <ArtifactBody artifact={a} />
                    </AccordionContent>
                </AccordionItem>
            ))}
        </Accordion>
    )
}
