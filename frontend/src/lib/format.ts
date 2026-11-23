// Number/date formatters. One place, so "$1.2T" never fights "1200000M".

export const fmtMoney = (v: number | null | undefined, digits = 2): string =>
  v == null ? '—' : v.toLocaleString('en-US', {
    style: 'currency', currency: 'USD',
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })

export const fmtPct = (v: number | null | undefined, digits = 2, signed = false): string =>
  v == null ? '—' : `${signed && v > 0 ? '+' : ''}${v.toFixed(digits)}%`

export const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v == null ? '—' : v.toLocaleString('en-US', { maximumFractionDigits: digits })

/** Market cap arrives in $M from Finnhub → human units. */
export const fmtMarketCap = (millions: number | null | undefined): string => {
  if (millions == null) return '—'
  if (millions >= 1_000_000) return `$${(millions / 1_000_000).toFixed(2)}T`
  if (millions >= 1_000) return `$${(millions / 1_000).toFixed(1)}B`
  return `$${millions.toFixed(0)}M`
}

export const fmtCompact = (v: number | null | undefined): string =>
  v == null ? '—' : Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v)

/** "12s ago" / "45m ago" freshness badges (Q6/Q12). */
export const fmtAge = (seconds: number | null | undefined): string => {
  if (seconds == null) return ''
  if (seconds < 60) return `${Math.round(seconds)}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  return `${(seconds / 3600).toFixed(1)}h ago`
}

export const fmtDate = (iso: string | null | undefined): string => {
  if (!iso) return '—'
  // A bare date ("2027-02-23") is a calendar date, not an instant. new Date()
  // parses it as UTC midnight, which renders as the PREVIOUS day in US time
  // zones (the earnings card showed Feb 22 for a Feb 23 report — 11/21 audit).
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

export const fmtDateTime = (iso: string | null | undefined): string => {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

/** P&L color class — the ONLY place green/red get chosen (accent stays cyan). */
export const plColor = (v: number | null | undefined): string =>
  v == null || v === 0 ? 'text-muted' : v > 0 ? 'text-up' : 'text-down'

/** Is a failed price refresh worth an alarm? One failed request right after a
 *  good one is a blip — the page retries within a minute and the cached copy is
 *  seconds old. It is a real problem when the provider needs action (expired
 *  login), nothing is cached, or the copy is older than the market needs:
 *  10 minutes while the session is open, 6 hours when it is closed (the
 *  official close does not move). */
export const staleIsSerious = (
  env: { status?: string; age_seconds?: number; error_reason?: string } | null | undefined,
  marketState: string | null | undefined,
): boolean => {
  if (!env) return false
  if (env.error_reason === 'auth_expired' || env.status === 'unavailable') return true
  if (env.age_seconds == null) return true
  return env.age_seconds > (marketState === 'open' ? 10 * 60 : 6 * 3600)
}
