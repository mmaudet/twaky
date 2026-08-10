interface SentinelStatusDotProps {
    enabled: boolean
    className?: string
}

/**
 * A small dot indicator for sentinel enabled/disabled state.
 * Green for enabled, gray (bordered) for disabled.
 * Always carries an aria-label for screen-reader accessibility.
 */
export function SentinelStatusDot({ enabled, className }: SentinelStatusDotProps) {
    return (
        <span
            className={
                'inline-block h-2.5 w-2.5 rounded-full ' +
                (enabled
                    ? 'bg-green-500'
                    : 'border border-muted-foreground') +
                (className ? ' ' + className : '')
            }
            role="img"
            aria-label={enabled ? 'enabled' : 'disabled'}
        />
    )
}
