// Data hooks — TanStack Query wrappers + the SSE subscription.
import {
  keepPreviousData, useMutation, useQuery, useQueryClient,
} from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { fmtAge, staleIsSerious } from '@/lib/format'
import type { ScreenerRow, StreamEvent } from '@/types'

/** In-page signal that a ticker's research notes changed ('*' = any/all).
 *  The Watchlist keeps its rows in component state, not in the query cache,
 *  so query invalidation alone never reached its Verdict column. */
const NOTES_CHANGED = 'tb:notes-changed'
const announceNotesChanged = (ticker: string) =>
  window.dispatchEvent(new CustomEvent(NOTES_CHANGED, { detail: { ticker } }))

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
      // "refreshed" while serving days-old cache is a lie — check what came back
      const bad = ['quote', 'history'].filter(
        (k) => ['stale', 'unavailable'].includes(data.sections?.[k]?.status ?? ''))
      const env = bad.length ? data.sections?.[bad[0]] : null
      if (env && staleIsSerious(env, data.price_basis?.state)) {
        const hint = env.error_reason === 'auth_expired'
          ? ' — Schwab token expired: run backend/schwab_reauth.py'
          : ` (${env.error_reason ?? 'provider error'})`
        toast.warning(`${symbol}: price feed failing — showing data from ${fmtAge(env.age_seconds)}${hint}`,
          { duration: 8000 })
      } else if (env) {
        // a blip: the cached copy is fresh enough and the next poll retries
        toast.message(`${symbol}: one price refresh failed — showing data from ${fmtAge(env.age_seconds)}`)
      } else {
        toast.success(`${symbol} refreshed`)
      }
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

  // a research note landed, changed or was removed → re-read just those rows
  // (stance/verdict come from the notes; the price sections are cache hits)
  useEffect(() => {
    const onChange = (e: Event) => {
      const t = (e as CustomEvent<{ ticker: string }>).detail?.ticker
      const all = symbolsKey ? symbolsKey.split(',') : []
      const want = t === '*' ? all : all.filter((x) => x === t)
      ;(async () => {
        for (let i = 0; i < want.length; i += 6) {
          try {
            const rows = await api.screener(false, want.slice(i, i + 6))
            setEnriched((prev) => ({
              ...prev, ...Object.fromEntries(rows.map((r) => [r.symbol, r])),
            }))
          } catch { /* keep the old row; the next event or reload retries */ }
        }
      })()
    }
    window.addEventListener(NOTES_CHANGED, onChange)
    return () => window.removeEventListener(NOTES_CHANGED, onChange)
  }, [symbolsKey])

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
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['discussions', ticker ?? '__all__'],
    queryFn: () => api.discussions(ticker),
    // safety net under the live stream: if the stream is down (backend
    // restart, sleeping laptop, a proxy that dropped it), a new or edited
    // note still shows up within half a minute
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })
  // when the notes themselves changed (a new one, an edit, a removal), the
  // verdict in the header and on the Watchlist is out of date too
  const seen = useRef<string | null>(null)
  const sig = q.data
    ? q.data.map((d) => `${d.id}:${d.file_mtime}`).join('|')
    : null
  useEffect(() => {
    if (sig == null) return
    if (seen.current != null && seen.current !== sig) {
      qc.invalidateQueries({ queryKey: ticker ? ['analysis', ticker] : ['analysis'] })
      qc.invalidateQueries({ queryKey: ['decisions'] })
      announceNotesChanged(ticker ?? '*')
    }
    seen.current = sig
  }, [sig, ticker, qc])
  return q
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
    // EventSource does NOT retry after a CLOSED error (e.g. a backend
    // restart/auto-reload) — without this loop the live stream dies silently
    // and discussions/stances stop updating until a browser refresh.
    let es: EventSource | null = null
    let retryMs = 1000
    let closed = false
    let everOpened = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      if (closed) return
      es = new EventSource('/api/discussions/stream')
      es.onopen = () => {
        retryMs = 1000
        // events may have been missed while disconnected — resync everything
        // the stream is responsible for
        qc.invalidateQueries({ queryKey: ['discussions'] })
        qc.invalidateQueries({ queryKey: ['decisions'] })
        // …including the verdict on an open analysis page and the Watchlist:
        // a note written while the stream was down changed those too
        if (everOpened) {
          qc.invalidateQueries({ queryKey: ['analysis'] })
          announceNotesChanged('*')
        }
        everOpened = true
      }
      es.onerror = () => {
        es?.close()
        if (closed) return
        timer = setTimeout(connect, retryMs)
        retryMs = Math.min(retryMs * 2, 15000)
      }
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
        qc.invalidateQueries({ queryKey: ['stance-scorecard'] })
        announceNotesChanged(event.ticker)
        toast.success(`New Claude discussion saved for ${event.ticker}`)
      } else if (event.type === 'deleted') {
        qc.invalidateQueries({ queryKey: ['discussions', event.ticker] })
        qc.invalidateQueries({ queryKey: ['discussions', '__all__'] })
        qc.invalidateQueries({ queryKey: ['analysis', event.ticker] })
        qc.invalidateQueries({ queryKey: ['decisions'] })
        qc.invalidateQueries({ queryKey: ['stance-scorecard'] })
        announceNotesChanged(event.ticker)
      } else if (event.type === 'discovery') {
        // a sweep file landed in ~/.tickerbands/discoveries/ — refresh the tab
        qc.invalidateQueries({ queryKey: ['discovery'] })
        if (event.added > 0) {
          toast.success(`Discovery sweep ingested — ${event.added} new candidate${event.added > 1 ? 's' : ''}`)
        } else {
          toast.message('Discovery sweep ingested — 0 new candidates', {
            description: 'All were duplicates, dismissed earlier, over the size cap, ' +
              'or the file\'s frontmatter lacked a candidates: [{ticker, thesis}] list.',
          })
        }
      } else if (event.type === 'error') {
        toast.error(`Discussion file failed to parse: ${event.file}`, {
          description: event.detail,
        })
      }
      }
    }
    connect()
    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      es?.close()
    }
  }, [qc])
}
