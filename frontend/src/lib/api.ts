// Typed API client. All calls proxy through Vite (/api → localhost:8001).
import type {
  ActionItem, Analysis, BandValidation, DecisionRow, Discussion, Glossary,
  Health, Lens, Portfolio, PortfolioIncome, PortfolioRisk, ScoreAudit,
  ScreenerRow, SearchResult, Settings, VrpReport, WatchItem, WhatIfResult,
} from '@/types'

class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) {
    let detail = `${r.status}`
    try {
      const body = await r.json()
      detail = body.detail ?? detail
    } catch { /* non-JSON error body */ }
    throw new ApiError(r.status, detail)
  }
  return r.json()
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  analysis: (symbol: string, refresh = false) =>
    req<Analysis>(`/api/analysis/${encodeURIComponent(symbol)}${refresh ? '?refresh=true' : ''}`),
  prompt: (symbol: string) =>
    req<{ prompt: string; context_hash: string }>(`/api/analysis/${encodeURIComponent(symbol)}/prompt`),
  sync: () => req<{ ok: boolean; invalidated: number }>(`/api/sync`, { method: 'POST' }),
  search: (q: string) => req<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}`),
  portfolio: (refresh = false) =>
    req<Portfolio>(`/api/portfolio${refresh ? '?refresh=true' : ''}`),

  watchlist: () => req<WatchItem[]>(`/api/watchlist`),
  addWatch: (symbol: string, note = '', tags: string[] = []) =>
    req<{ ok: boolean; symbol: string }>(`/api/watchlist`, json('POST', { symbol, note, tags })),
  patchWatch: (symbol: string, body: { note?: string; tags?: string[] }) =>
    req<{ ok: boolean }>(`/api/watchlist/${symbol}`, json('PATCH', body)),
  removeWatch: (symbol: string) =>
    req<{ ok: boolean }>(`/api/watchlist/${symbol}`, { method: 'DELETE' }),
  importPortfolioWatchlist: () =>
    req<{ ok: boolean; imported: number; seen: number }>(`/api/watchlist/import-portfolio`, { method: 'POST' }),
  importSchwabWatchlist: () =>
    req<{ ok: boolean; added: number; lists: { name: string; count: number }[] }>(
      `/api/watchlist/import-schwab`, { method: 'POST' }),
  bulkAddWatchlist: (symbols: string) =>
    req<{ ok: boolean; added: number }>(`/api/watchlist/bulk`, json('POST', { symbols })),
  watchlistQuotes: () =>
    req<Record<string, {
      price: number; day_pct: number
      ah_price: number | null; ah_change_pct: number | null; is_extended: boolean
    }>>(`/api/watchlist/quotes`),
  screener: (refresh = false, symbols?: string[]) => {
    const params = new URLSearchParams()
    if (refresh) params.set('refresh', 'true')
    if (symbols?.length) params.set('symbols', symbols.join(','))
    const qs = params.toString()
    return req<ScreenerRow[]>(`/api/watchlist/screener${qs ? `?${qs}` : ''}`)
  },
  portfolioIncome: () => req<PortfolioIncome>(`/api/portfolio/income`),
  portfolioRisk: () => req<PortfolioRisk>(`/api/portfolio/risk`),
  lens: (symbol: string) => req<Lens>(`/api/lens/${encodeURIComponent(symbol)}`),
  vrp: (symbol: string) => req<VrpReport>(`/api/vrp/${encodeURIComponent(symbol)}`),
  whatIf: (changes: Record<string, number>) =>
    req<WhatIfResult>(`/api/portfolio/whatif`, json('POST', { changes })),
  scoreAudit: () => req<ScoreAudit>(`/api/score-audit`),
  bandValidation: () => req<BandValidation>(`/api/settings/band-validation`),
  saveBandEngine: (engine: string) =>
    req<{ ok: boolean; engine: string }>(`/api/settings/band-engine`, json('PUT', { engine })),
  decisions: () => req<DecisionRow[]>(`/api/decisions`),
  actions: () => req<ActionItem[]>(`/api/actions`),
  addAction: (body: { symbol?: string; action: string; rationale?: string; priority?: number }) =>
    req<{ ok: boolean; id: number }>(`/api/actions`, json('POST', body)),
  patchAction: (id: number, body: { status?: string; rationale?: string }) =>
    req<ActionItem>(`/api/actions/${id}`, json('PATCH', body)),
  importActions: () =>
    req<{ ok: boolean; imported: number; seen: number }>(`/api/actions/import-portfolio`, { method: 'POST' }),

  discussions: (ticker?: string) =>
    req<Discussion[]>(`/api/discussions${ticker ? `?ticker=${ticker}` : ''}`),

  glossary: () => req<Glossary>(`/api/glossary`),
  settings: () => req<Settings>(`/api/settings`),
  saveWeights: (weights: Record<string, number>) =>
    req<{ ok: boolean; weights: Record<string, number> }>(`/api/settings/weights`, json('PUT', { weights })),
  health: () => req<Health>(`/api/health`),
}

export { ApiError }
