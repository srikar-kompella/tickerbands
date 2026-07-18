// Watchlist screener with PROGRESSIVE loading (no blank-until-done):
//   tickers paint instantly (DB) → one batched Schwab call fills price/day%
//   → heavy columns (band/options/earnings) stream in chunks of 6 with a
//   visible progress bar. Cells show a shimmer until their chunk lands.
// Row click → Analysis (that ticker's discussions ride along automatically).
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle, Download, Eye, Moon, Pencil, Plus, RefreshCw, Trash2,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { fmtDate, fmtNum, fmtPct, plColor } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, Button, Card, EmptyState, Modal, Skeleton } from '@/components/ui'
import { useExtendedHours, useProgressiveScreener, useWatchlistMutations } from '@/hooks'
import type { ScreenerRow } from '@/types'

type SortKey = 'attention' | 'symbol' | 'day_pct' | 'band_half_pct'
  | 'implied_vs_band' | 'days_to_earnings' | 'last_score'

const HEADERS: { key: SortKey; label: string; title: string }[] = [
  { key: 'symbol', label: 'Symbol', title: 'Ticker' },
  { key: 'day_pct', label: 'Today', title: "Today's move" },
  { key: 'band_half_pct', label: 'Band ±%', title: '80% expected range half-width (realized vol)' },
  { key: 'implied_vs_band', label: 'Impl/Band', title: 'Options implied move ÷ our band — >1.3 usually means an event is priced in' },
  { key: 'days_to_earnings', label: 'Earnings', title: 'Days until next report' },
  { key: 'last_score', label: 'Score', title: 'Last Setup Score (computed when you last analyzed — not auto-refreshed)' },
]

/** Shimmering placeholder for a cell whose enrichment chunk hasn't landed. */
function Pending({ active }: { active: boolean }) {
  return active
    ? <Skeleton className="ml-auto h-3.5 w-10" />
    : <span className="text-faint">—</span>
}

function EditRow({ row, onDone }: { row: ScreenerRow; onDone: () => void }) {
  const { patch } = useWatchlistMutations()
  const [note, setNote] = useState(row.note)
  const [tags, setTags] = useState(row.tags.join(', '))
  return (
    <div className="flex flex-wrap items-center gap-2 py-2">
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Research note…"
        aria-label="Note"
        className="min-w-52 flex-1 rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent/60" />
      <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tags, comma, separated"
        aria-label="Tags"
        className="w-56 rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent/60" />
      <Button variant="accent" onClick={() => {
        patch.mutate({ symbol: row.symbol, note,
          tags: tags.split(',').map((t) => t.trim()).filter(Boolean) })
        onDone()
      }}>Save</Button>
      <Button variant="ghost" onClick={onDone}>Cancel</Button>
    </div>
  )
}

export default function Watchlist({ onAnalyze }: { onAnalyze: (s: string) => void }) {
  const { base, baseLoading, baseError, quotes, enriched, progress, refreshAll } =
    useProgressiveScreener()
  const { add, remove, importPortfolio, importSchwab, bulkAdd } = useWatchlistMutations()
  const { on: ahOn, toggle: ahToggle } = useExtendedHours()

  const [input, setInput] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('attention')
  const [asc, setAsc] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkText, setBulkText] = useState('')

  // Merge the three layers: DB row (instant) ← batch quote (fast) ← enrichment.
  const rows: ScreenerRow[] = useMemo(() => {
    return (base ?? []).map((w) => {
      const e = enriched[w.symbol]
      if (e) return { ...e, note: w.note, tags: w.tags }
      const q = quotes?.[w.symbol]
      return {
        symbol: w.symbol, note: w.note, tags: w.tags,
        price: q?.price ?? null, day_pct: q?.day_pct ?? null,
        band_half_pct: null, implied_move_pct: null, implied_vs_band: null,
        days_to_earnings: null, yesterday_z: null,
        outside_band_yesterday: false, last_score: null, last_score_ts: null,
        stance: null, stance_ts: null,
      }
    })
  }, [base, quotes, enriched])

  const sorted = useMemo(() => {
    const copy = [...rows]
    if (sortKey === 'attention') {
      copy.sort((a, b) =>
        Number(b.outside_band_yesterday) - Number(a.outside_band_yesterday)
        || (a.days_to_earnings ?? 9999) - (b.days_to_earnings ?? 9999)
        || a.symbol.localeCompare(b.symbol))
      return copy
    }
    copy.sort((a, b) => {
      if (sortKey === 'symbol') return a.symbol.localeCompare(b.symbol)
      const va = a[sortKey], vb = b[sortKey]
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      return (va as number) - (vb as number)
    })
    return asc ? copy : copy.reverse()
  }, [rows, sortKey, asc])

  const breaks = rows.filter((r) => r.outside_band_yesterday).length
  const pct = progress.total ? Math.round((progress.done / progress.total) * 100) : 0

  const submit = () => {
    const t = input.trim().toUpperCase()
    if (/^[A-Z.\-]{1,10}$/.test(t)) {
      add.mutate({ symbol: t })
      setInput('')
    }
  }

  const clickHeader = (key: SortKey) => {
    if (sortKey === key) setAsc((a) => !a)
    else { setSortKey(key); setAsc(true) }
  }

  return (
    <div>
      {/* toolbar */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="Add ticker…" aria-label="Add ticker to watchlist"
          className="w-40 rounded-xl border border-border bg-surface px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-accent/60" />
        <Button variant="accent" onClick={submit} disabled={!input.trim()}>
          <Plus className="h-4 w-4" /> Add
        </Button>
        <Button onClick={() => setBulkOpen(true)} title="Paste a list of symbols">Bulk add</Button>
        <Button onClick={() => window.open('/api/morning-sheet', '_blank')}
          title="Printable daily digest: breaks, earnings this week, the whole screener">
          ☀ Morning sheet
        </Button>
        <Button onClick={() => importSchwab.mutate()} disabled={importSchwab.isPending}
          title="One-time pull of your Schwab watchlists (if Schwab's API exposes them)">
          <Download className="h-4 w-4" />
          {importSchwab.isPending ? 'Asking Schwab…' : 'Import from Schwab'}
        </Button>
        <Button onClick={() => importPortfolio.mutate()} disabled={importPortfolio.isPending}
          title="One-time copy from the old Portfolio app's watchlist">
          <Download className="h-4 w-4" /> From old app
        </Button>
        <span className="ml-auto flex items-center gap-2">
          <button onClick={ahToggle} role="switch" aria-checked={ahOn}
            title={ahOn ? 'Showing after-hours moves where an extended session is live' : 'Regular session values only'}
            className={cn('flex items-center gap-1 rounded-lg border px-2 py-1.5 text-xs font-medium transition-colors',
              ahOn ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border text-muted hover:bg-surface-2')}>
            <Moon className="h-3 w-3" /> AH
          </button>
          {breaks > 0 && (
            <Badge tone="warn"><AlertTriangle className="h-3 w-3" />
              {breaks} unusual move{breaks > 1 ? 's' : ''} yesterday
            </Badge>
          )}
          <Button variant="ghost" title="Re-fetch market data for all rows"
            onClick={refreshAll} disabled={progress.running}>
            <RefreshCw className={cn('h-4 w-4', progress.running && 'animate-spin')} />
          </Button>
        </span>
      </div>

      {/* the progress you asked for: which backend calls are done */}
      {progress.running && (
        <div className="mb-3">
          <div className="mb-1 flex justify-between text-[11px] text-muted">
            <span>Updating market data… {progress.done}/{progress.total} tickers</span>
            <span className="tnum">{pct}%</span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-surface-3">
            <motion.div className="h-full bg-accent"
              animate={{ width: `${pct}%` }} transition={{ duration: 0.3 }} />
          </div>
        </div>
      )}

      {baseLoading && (
        <Card><div className="space-y-2">{Array.from({ length: 6 }).map((_, i) =>
          <Skeleton key={i} className="h-8 w-full" />)}</div></Card>
      )}

      {!baseLoading && !!baseError && (
        <EmptyState icon={<AlertTriangle className="h-7 w-7" />}
          title="Watchlist failed to load" body={(baseError as Error).message} />
      )}

      {!baseLoading && !baseError && !rows.length && (
        <EmptyState icon={<Eye className="h-7 w-7" />} title="No research targets yet"
          body="Add tickers, bulk-paste a list, or import — then this becomes a one-glance screener: whose band broke yesterday, who reports soon, where options disagree with recent volatility." />
      )}

      {!!sorted.length && (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-faint">
                {HEADERS.map((h) => (
                  <th key={h.key} title={h.title}
                    className={cn('cursor-pointer pb-2 font-medium hover:text-muted',
                      h.key !== 'symbol' && 'text-right')}
                    onClick={() => clickHeader(h.key)}>
                    {h.label}{sortKey === h.key && (asc ? ' ↑' : ' ↓')}
                  </th>
                ))}
                <th className="pb-2 text-right font-medium" title="Latest Claude research stance (from discussions)">Claude</th>
                <th className="pb-2 text-right font-medium">
                  <button className="hover:text-muted" title="Reset to attention order"
                    onClick={() => setSortKey('attention')}>⚡</button>
                </th>
              </tr>
            </thead>
            <tbody>
              <AnimatePresence>
                {sorted.map((r) => {
                  const pending = progress.running && !enriched[r.symbol]
                  return (
                    <motion.tr key={r.symbol} layout
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                      className={cn('border-t border-border/60 transition-colors hover:bg-surface-2',
                        r.outside_band_yesterday && 'bg-warn/5')}>
                      <td className="py-2.5">
                        <button onClick={() => onAnalyze(r.symbol)}
                          className="font-bold hover:text-accent">{r.symbol}</button>
                        {r.outside_band_yesterday && (
                          <Badge tone="warn" className="ml-2 text-[10px]"
                            title={`Yesterday moved ${r.yesterday_z}σ — outside the 80% band`}>
                            {r.yesterday_z}σ
                          </Badge>
                        )}
                        {r.tags.slice(0, 2).map((t) => (
                          <Badge key={t} className="ml-1.5 text-[10px]">{t}</Badge>
                        ))}
                      </td>
                      {(() => {
                        const aq = quotes?.[r.symbol]
                        const useAh = ahOn && !!aq?.is_extended && aq.ah_change_pct != null
                        const v = useAh ? aq!.ah_change_pct : r.day_pct
                        return (
                          <td className={cn('tnum py-2.5 text-right', plColor(v))}
                            title={useAh ? `After-hours move (close: ${fmtPct(r.day_pct, 2, true)})` : undefined}>
                            {v != null ? (
                              <>
                                {fmtPct(v, 2, true)}
                                {useAh && <Moon className="mb-0.5 ml-1 inline h-2.5 w-2.5 text-accent" />}
                              </>
                            ) : <Pending active={!quotes} />}
                          </td>
                        )
                      })()}
                      <td className="tnum py-2.5 text-right text-muted">
                        {r.band_half_pct != null ? `±${fmtNum(r.band_half_pct, 2)}`
                          : <Pending active={pending} />}
                      </td>
                      <td className={cn('tnum py-2.5 text-right',
                        r.implied_vs_band != null && r.implied_vs_band > 1.3
                          ? 'text-warn font-semibold' : 'text-muted')}>
                        {r.implied_vs_band != null ? `${fmtNum(r.implied_vs_band, 2)}×`
                          : <Pending active={pending} />}
                      </td>
                      <td className={cn('tnum py-2.5 text-right',
                        r.days_to_earnings != null && r.days_to_earnings <= 5
                          ? 'text-warn font-semibold' : 'text-muted')}>
                        {r.days_to_earnings != null ? `${r.days_to_earnings}d`
                          : <Pending active={pending} />}
                      </td>
                      <td className="tnum py-2.5 text-right" title={r.last_score_ts
                        ? `As of ${fmtDate(r.last_score_ts)} — analyze to refresh`
                        : 'Analyze once to compute'}>
                        {r.last_score != null
                          ? <span className={r.last_score >= 70 ? 'text-up'
                              : r.last_score < 40 ? 'text-down' : 'text-warn'}>
                              {fmtNum(r.last_score, 0)}
                            </span>
                          : <span className="text-faint">—</span>}
                      </td>
                      <td className="py-2.5 text-right" title={r.stance_ts
                        ? `Claude's stance from ${fmtDate(r.stance_ts)} — open the ticker for reasoning`
                        : 'No Claude discussion with a stance yet'}>
                        {r.stance ? (
                          <Badge tone={r.stance === 'buy' ? 'up'
                            : r.stance === 'sell' || r.stance === 'trim' ? 'down' : 'accent'}
                            className="text-[10px]">
                            {r.stance.toUpperCase()}
                          </Badge>
                        ) : <span className="text-faint">—</span>}
                      </td>
                      <td className="py-2.5 text-right">
                        <button onClick={() => setEditing(editing === r.symbol ? null : r.symbol)}
                          className="mr-2 text-faint hover:text-text" title="Edit note/tags">
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button onClick={() => remove.mutate(r.symbol)}
                          className="text-faint hover:text-down" title="Remove">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </motion.tr>
                  )
                })}
              </AnimatePresence>
            </tbody>
          </table>
          <AnimatePresence>
            {editing && sorted.some((r) => r.symbol === editing) && (
              <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }} className="overflow-hidden border-t border-border">
                <EditRow row={sorted.find((r) => r.symbol === editing)!}
                  onDone={() => setEditing(null)} />
              </motion.div>
            )}
          </AnimatePresence>
          <p className="mt-2 text-[10px] text-faint">
            Prices arrive in one batched call; band/options/earnings stream in
            chunks (cached 5–30 min after that) · Score is the last computed
            value — open the ticker to refresh it · Impl/Band &gt;1.3× usually
            means an event is priced in · {rows.length} tracked
          </p>
        </Card>
      )}

      {/* bulk-paste modal */}
      <Modal open={bulkOpen} onClose={() => setBulkOpen(false)} title="Bulk add symbols">
        <p className="mb-2 text-sm text-muted">
          Paste anything — commas, spaces, newlines all work.
        </p>
        <textarea value={bulkText} onChange={(e) => setBulkText(e.target.value)}
          rows={5} placeholder={'AAPL, NVDA\nHIMS COIN'}
          className="w-full rounded-lg border border-border bg-bg p-2.5 text-sm outline-none focus:border-accent/60" />
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setBulkOpen(false)}>Cancel</Button>
          <Button variant="accent" disabled={!bulkText.trim()} onClick={() => {
            bulkAdd.mutate(bulkText)
            setBulkText('')
            setBulkOpen(false)
          }}>Add all</Button>
        </div>
      </Modal>
    </div>
  )
}
