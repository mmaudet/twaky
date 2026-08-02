/**
 * Client mirror of the API's _safe_return_to (see 3a's oauth router).
 *
 * Only allow local paths starting with '/' but not '//' (protocol-relative)
 * and not containing '\'. Anything else falls back to '/'.
 */
export function sanitizeReturnTo(input: string): string {
    if (!input.startsWith('/')) return '/'
    if (input.startsWith('//')) return '/'
    if (input.includes('\\')) return '/'
    return input
}
