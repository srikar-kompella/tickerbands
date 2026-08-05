// Data hooks — TanStack Query wrappers + the SSE subscription.
import {
  keepPreviousData, useMutation, useQuery, useQueryClient,
} from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import type { ScreenerRow, StreamEvent } from '@/types'

export function useAnalysis(symbol: string | null) {
  return useQuery({
    queryKey: ['analysis', symbol],
    queryFn: () => api.analysis(symbol!),
    enabled: !!symbol,
    staleTime: 30_000,     // backend cache is the real freshness authority (Q6)
    retry: 1,              // provider failures already degrade server-side
    // Keep a mounted Analysis page alive: without this, a tab left open on a
    // ticker never updates (focus refetch only fires on focus CHANGES).
    // Backend TTLs absorb the cost — only quote (30s TTL) actually refetches
    // upstream each minute; the rest are server-cache hits.
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })
}

/** Universal refresh: force past every TTL, then swap in the fresh payload. */
export function useRefreshAnalysis(symbol: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.analysis(symbol!, true),
    onSuccess: (data) => {
      qc.setQueryData(['analysis', symbol], data)
      toast.success(`${symbol} refreshed`)
    },
    onError: (e: Error) => toast.error(`Refresh failed: ${e.message}`),
  })
}

/** Debounced name→ticker search ("apple" → AAPL). Results cached 1h. */
export function useSymbolSearch(q: string) {
  const [debounced, setDebounced] = useState(q)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 300)
    return () => clearTimeout(t)
  }, [q])
  const trimmed = debounced.trim()
  return useQuery({
    queryKey: ['search', trimmed.toLowerCase()],
    queryFn: () => api.search(trimmed),
    enabled: trimmed.length >= 2,
    staleTime: 3600_000,
    placeholderData: keepPreviousData, // no flicker while the next query types in
  })
}

export function useWatchlist() {
  return useQuery({ queryKey: ['watchlist'], queryFn: api.watchlist })
}

/** After-hours display toggle — ON by default, persisted, shared everywhere. */
export function useExtendedHours() {
  const [on, setOn] = useState(() => localStorage.getItem('tl-ah') !== '0')
  const toggle = () =>
    setOn((v) => {
      localStorage.setItem('tl-ah', v ? '0' : '1')
      return !v
    })
  return { on, toggle }
}

export function usePortfolio() {
  return useQuery({
    queryKey: ['portfolio'],
    queryFn: () => api.portfolio(),
    staleTime: 60_000, // backend caches 5m; UI shows the age badge
  })
}

export function useRefreshPortfolio() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.portfolio(true),
    onSuccess: (data) => {
      qc.setQueryData(['portfolio'], data)
      qc.invalidateQueries({ queryKey: ['analysis'] }) // ownership chips update too
      toast.success('Portfolio refreshed')
    },
    onError: (e: Error) => toast.error(`Portfolio refresh failed: ${e.message}`),
  })
}

export function useWatchlistMutations() {
  const qc = useQueryClient()
  const invalidate = () => qc.invalidateQueries({ queryKey: ['watchlist'] })
  const add = useMutation({
    mutationFn: ({ symbol, note, tags }: { symbol: string; note?: string; tags?: string[] }) =>
      api.addWatch(symbol, note, tags),
    onSuccess: (d) => { invalidate(); toast.success(`${d.symbol} added to watchlist`) },
    onError: (e: Error) => toast.error(e.message),
  })
  const patch = useMutation({
    mutationFn: ({ symbol, ...body }: { symbol: string; note?: string; tags?: string[] }) =>
      api.patchWatch(symbol, body),
    onSuccess: () => invalidate(),
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: (symbol: string) => api.removeWatch(symbol),
    onSuccess: () => { invalidate(); toast.success('Removed') },
    onError: (e: Error) => toast.error(e.message),
  })
  const importPortfolio = useMutation({
    mutationFn: api.importPortfolioWatchlist,
    onSuccess: (d) => {
      invalidate()
      qc.invalidateQueries({ queryKey: ['wl-quotes'] })
      toast.success(`Imported ${d.imported} of ${d.seen} Portfolio symbols`)
    },
    onError: (e: Error) => toast.error(`Import failed: ${e.message}`),
  })
  const importSchwab = useMutation({
    mutationFn: api.importSchwabWatchlist,
    onSuccess: (d) => {
      invalidate()
      qc.invalidateQueries({ queryKey: ['wl-quotes'] })
      toast.success(`Schwab import: ${d.added} symbols from ${d.lists.length} list(s)`)
    },
    // 501 = Schwab's API genuinely doesn't expose watchlists — say so plainly
    onError: (e: Error) => toast.error(e.message, { duration: 8000 }),
  })
  const bulkAdd = useMutation({
    mutationFn: (symbols: string) => api.bulkAddWatchlist(symbols),
    onSuccess: (d) => {
      invalidate()
      qc.invalidateQueries({ queryKey: ['wl-quotes'] })
      toast.success(`Added ${d.added} symbols`)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return { add, patch, remove, importPortfolio, importSchwab, bulkAdd }
}

/**
 * Progressive screener (no more blank-until-everything-loads):
 *   1. tickers render instantly from the DB (useWatchlist — zero API calls)
 *   2. ONE batched Schwab call fills price/day% for every row (~1s)
 *   3. heavy columns (band/options/earnings) stream in chunks of 6 with
 *      visible progress; each chunk also warms the shared cache
 * Chunk failures leave dashes on those rows — the loop keeps going.
 */
export function useProgressiveScreener() {
  const { data: base, isLoading: baseLoading, error: baseError } = useWatchlist()
  const { data: quotes } = useQuery({
    queryKey: ['wl-quotes'],
    queryFn: api.watchlistQuotes,
    enabled: !!base?.length,
    staleTime: 30_000,
  })
  const [enriched, setEnriched] = useState<Record<string, ScreenerRow>>({})
  const [progress, setProgress] = useState({ done: 0, total: 0, running: false })
  const [nonce, setNonce] = useState(0) // bump to force a re-enrich pass

  const symbolsKey = (base ?? []).map((b) => b.symbol).sort().join(',')

  useEffect(() => {
    const symbols = symbolsKey ? symbolsKey.split(',') : []
    if (!symbols.length) {
      setProgress({ done: 0, total: 0, running: false })
      return
    }
    let cancelled = false
    setProgress({ done: 0, total: symbols.length, running: true })
    ;(async () => {
      const CHUNK = 6
      for (let i = 0; i < symbols.length; i += CHUNK) {
        if (cancelled) return
        const chunk = symbols.slice(i, i + CHUNK)
        try {
          const rows = await api.screener(nonce > 0, chunk)
          if (cancelled) return
          setEnriched((prev) => ({
            ...prev,
            ...Object.fromEntries(rows.map((r) => [r.symbol, r])),
          }))
        } catch { /* chunk failed → dashes; keep streaming the rest */ }
        if (cancelled) return
        setProgress((p) => ({ ...p, done: Math.min(p.done + chunk.length, symbols.length) }))
      }
      if (!cancelled) setProgress((p) => ({ ...p, running: false }))
    })()
    return () => { cancelled = true }
  }, [symbolsKey, nonce])

  return {
    base, baseLoading, baseError,
    quotes, enriched, progress,
    refreshAll: () => { setEnriched({}); setNonce((n) => n + 1) },
  }
}

export function usePortfolioIncome() {
  return useQuery({
    queryKey: ['portfolio-income'],
    queryFn: api.portfolioIncome,
    staleTime: 3600_000, // yields move slowly; backend caches 24h anyway
  })
}

export function usePortfolioRisk() {
  return useQuery({ queryKey: ['portfolio-risk'], queryFn: api.portfolioRisk, staleTime: 300_000 })
}

export function useScoreAudit() {
  return useQuery({ queryKey: ['score-audit'], queryFn: api.scoreAudit, staleTime: 300_000 })
}

export function useBandValidation() {
  return useQuery({ queryKey: ['band-validation'], queryFn: api.bandValidation, staleTime: Infinity })
}

export function useDecisions() {
  return useQuery({ queryKey: ['decisions'], queryFn: api.decisions, staleTime: 60_000 })
}

export function useActions() {
  return useQuery({ queryKey: ['actions'], queryFn: api.actions })
}

export function useActionMutations() {
  const qc = useQueryClient()
  const invalidate = () => qc.invalidateQueries({ queryKey: ['actions'] })
  const add = useMutation({
    mutationFn: api.addAction,
    onSuccess: () => { invalidate(); toast.success('Action added') },
    onError: (e: Error) => toast.error(e.message),
  })
  const patch = useMutation({
    mutationFn: ({ id, ...body }: { id: number; status?: string; rationale?: string }) =>
      api.patchAction(id, body),
    onSuccess: () => invalidate(),
    onError: (e: Error) => toast.error(e.message),
  })
  const importOld = useMutation({
    mutationFn: api.importActions,
    onSuccess: (d) => { invalidate(); toast.success(`Imported ${d.imported} of ${d.seen} actions`) },
    onError: (e: Error) => toast.error(`Import failed: ${e.message}`),
  })
  return { add, patch, importOld }
}

export function useDiscussions(ticker?: string) {
  return useQuery({
    queryKey: ['discussions', ticker ?? '__all__'],
    queryFn: () => api.discussions(ticker),
  })
}

export function useGlossary() {
  return useQuery({ queryKey: ['glossary'], queryFn: api.glossary, staleTime: Infinity })
}

export function useSettings() {
  return useQuery({ queryKey: ['settings'], queryFn: api.settings })
}

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 60_000 })
}

/**
 * SSE subscription (locked architecture step 4): Claude CLI writes a file →
 * watcher ingests → event lands here → invalidate that ticker's discussion
 * queries → timeline updates within a second. EventSource auto-reconnects.
 */
export function useDiscussionStream() {
  const qc = useQueryClient()
  useEffect(() => {
    const es = new EventSource('/api/discussions/stream')
    es.onmessage = (e) => {
      let event: StreamEvent
      try {
        event = JSON.parse(e.data)
      } catch { return }
      if (event.type === 'created') {
        qc.invalidateQueries({ queryKey: ['discussions', event.ticker] })
        qc.invalidateQueries({ queryKey: ['discussions', '__all__'] })
        // the analysis payload carries claude_stance/claude_news — refetch so
        // the header chip updates the moment the discussion lands
        qc.invalidateQueries({ queryKey: ['analysis', event.ticker] })
        qc.invalidateQueries({ queryKey: ['decisions'] })
        toast.success(`New Claude discussion saved for ${event.ticker}`)
      } else if (event.type === 'deleted') {
        qc.invalidateQueries({ queryKey: ['discussions', event.ticker] })
        qc.invalidateQueries({ queryKey: ['discussions', '__all__'] })
        qc.invalidateQueries({ queryKey: ['analysis', event.ticker] })
        qc.invalidateQueries({ queryKey: ['decisions'] })
      } else if (event.type === 'error') {
        toast.error(`Discussion file failed to parse: ${event.file}`, {
          description: event.detail,
        })
      }
    }
    return () => es.close()
  }, [qc])
}
