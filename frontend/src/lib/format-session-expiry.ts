/**
 * Given a login timestamp (ms since epoch) and a TTL in seconds,
 * return "Xh Ym" or "Xm" or "expired".
 */
export function formatSessionExpiry(loginAt: number, ttlSeconds: number): string {
    const remaining = loginAt + ttlSeconds * 1000 - Date.now()
    if (remaining <= 0) return 'expired'
    const min = Math.floor(remaining / 60_000)
    if (min < 60) return `${min}m`
    const hr = Math.floor(min / 60)
    const rem = min % 60
    return rem > 0 ? `${hr}h ${rem}m` : `${hr}h`
}
