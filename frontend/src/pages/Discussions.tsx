// Unified timeline across all tickers, filterable. When a ticker is selected,
// "Get prompt" regenerates its Ask-Claude prompt — so losing a terminal
// session never loses access to the prompt (it's static-per-snapshot and
// carries prior-discussion continuity from the backend).
import { Scale, TerminalSquare } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AskClaudeModal } from '@/components/discussions/AskClaudeModal'
import { DiscussionTimeline } from '@/components/discussions/DiscussionTimeline'
import { Badge, Button, Card, CardTitle, EmptyState, SkeletonCard } from '@/components/ui'
import { fmtDate, fmtMoney, fmtPct, plColor } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useDecisions, useDiscussions } from '@/hooks'

// ─── P3: the decision journal — your calls vs what actually happened ───────────

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
  return r.json()
}

interface ScorecardRow {
  ticker: string; stance: string; horizon: string | null; note: string | null
  date: string; days_elapsed: number | null
  price_then: number | null; price_now: number | null
  return_pct: number | null; spy_return_pct: number | null
  excess_pct: number | null; working: boolean | null
}
interface Scorecard {
  rows: ScorecardRow[]
  summary: Record<string, { n: number; avg_excess_pct: number; median_excess_pct: number }>
  n_scored: number
  caveat: string
}

function ScorecardView() {
  const { data, isLoading } = useQuery({
    queryKey: ['stance-scorecard'],
    queryFn: () => j<Scorecard>('/api/stances/scorecard'),
    staleTime: 60_000,
  })
  if (isLoading) return <SkeletonCard lines={5} />
  if (!data?.rows.length) {
    return (
      <EmptyState icon={<Scale className="h-7 w-7" />} title="No stances to score yet"
        body="Each research session's stance (buy / hold / watch / trim / sell) is tracked here against what price and SPY did afterwards — the only honest way to audit research judgment." />
    )
  }
  const tone = (st: string) =>
    st === 'buy' ? 'up' : st === 'sell' || st === 'trim' ? 'down' : 'neutral'
  return (
    <Card>
      <CardTitle>Stance scorecard — every call vs what happened (excess over SPY)</CardTitle>
      <div className="mb-3 flex flex-wrap gap-4">
        {(Object.entries(data.summary) as [string, { n: number; avg_excess_pct: number; median_excess_pct: number }][]).map(([st, sm]) => (
          <div key={st} className="rounded-lg border border-border px-3 py-1.5 text-xs">
            <Badge tone={tone(st)}>{st}</Badge>
            <span className="ml-2 text-muted">n={sm.n}</span>
            <span className={cn('ml-2 font-semibold tnum', plColor(sm.avg_excess_pct))}>
              {fmtPct(sm.avg_excess_pct, 1, true)} avg excess
            </span>
          </div>
        ))}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-faint">
            <th className="pb-2 font-medium">When</th>
            <th className="pb-2 font-medium">Ticker</th>
            <th className="pb-2 font-medium">Stance</th>
            <th className="pb-2 text-right font-medium">Then</th>
            <th className="pb-2 text-right font-medium">Now</th>
            <th className="pb-2 text-right font-medium">Return</th>
            <th className="pb-2 text-right font-medium">SPY</th>
            <th className="pb-2 text-right font-medium">Excess</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r: ScorecardRow, i: number) => (
            <tr key={i} className="border-t border-border/60" title={r.note ?? ''}>
              <td className="py-2 text-muted tnum">{r.date}
                {r.days_elapsed != null && <span className="text-faint"> · {r.days_elapsed}d</span>}</td>
              <td className="py-2 font-bold">{r.ticker}</td>
              <td className="py-2"><Badge tone={tone(r.stance)}>{r.stance}</Badge>
                {r.horizon && <span className="ml-1 text-[10px] text-faint">{r.horizon}</span>}</td>
              <td className="tnum py-2 text-right">{fmtMoney(r.price_then)}</td>
              <td className="tnum py-2 text-right">{fmtMoney(r.price_now)}</td>
              <td className={cn('tnum py-2 text-right', plColor(r.return_pct))}>{fmtPct(r.return_pct, 1, true)}</td>
              <td className="tnum py-2 text-right text-muted">{fmtPct(r.spy_return_pct, 1, true)}</td>
              <td className={cn('tnum py-2 text-right font-semibold', plColor(r.excess_pct))}>
                {fmtPct(r.excess_pct, 1, true)}
                {r.working != null && (r.working ? ' ✓' : ' ✗')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[10px] text-faint">{data.caveat}</p>
    </Card>
  )
}

function DecisionsView() {
  const { data: rows, isLoading } = useDecisions()
  if (isLoading) return <SkeletonCard lines={4} />
  if (!rows?.length) {
    return (
      <EmptyState icon={<Scale className="h-7 w-7" />} title="No decisions recorded yet"
        body="When you state a call (buy / trim / sell / hold / pass) in a Claude discussion, it lands here with the price at that moment — an honest ledger of how your calls age." />
    )
  }
  const tone = (d: string) =>
    d === 'buy' ? 'up' : d === 'sell' || d === 'trim' ? 'down' : 'neutral'
  return (
    <Card>
      <CardTitle>Your calls, and what the price did since</CardTitle>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-faint">
            <th className="pb-2 font-medium">When</th>
            <th className="pb-2 font-medium">Ticker</th>
            <th className="pb-2 font-medium">Call</th>
            <th className="pb-2 text-right font-medium">Price then</th>
            <th className="pb-2 text-right font-medium">Now</th>
            <th className="pb-2 text-right font-medium">Since</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-border/60" title={r.summary ?? ''}>
              <td className="py-2.5 text-muted tnum">{fmtDate(r.created_ts)}</td>
              <td className="py-2.5 font-bold">{r.ticker}</td>
              <td className="py-2.5"><Badge tone={tone(r.decision)}>{r.decision}</Badge></td>
              <td className="tnum py-2.5 text-right">{fmtMoney(r.decision_price)}</td>
              <td className="tnum py-2.5 text-right">{fmtMoney(r.current_price)}</td>
              <td className={cn('tnum py-2.5 text-right font-semibold', plColor(r.pct_since))}>
                {fmtPct(r.pct_since, 2, true)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[10px] text-faint">
        "Since" is raw price change after your call — context, not a scorecard;
        a good process can have bad outcomes and vice versa.
      </p>
    </Card>
  )
}

export default function Discussions() {
  const { data, isLoading } = useDiscussions()
  const [ticker, setTicker] = useState<string | null>(null)
  const [promptOpen, setPromptOpen] = useState(false)
  const [view, setView] = useState<'timeline' | 'decisions' | 'scorecard'>('timeline')

  const tickers = useMemo(
    () => Array.from(new Set((data ?? []).map((d) => d.ticker))).sort(),
    [data])
  const filtered = useMemo(
    () => (ticker ? (data ?? []).filter((d) => d.ticker === ticker) : data ?? []),
    [data, ticker])

  if (isLoading) return <div className="space-y-3"><SkeletonCard /><SkeletonCard /></div>

  return (
    <div>
      {/* Timeline | Decisions segmented toggle (P3) */}
      <div className="mb-4 inline-flex rounded-lg border border-border bg-surface p-0.5">
        {(['timeline', 'decisions', 'scorecard'] as const).map((v) => (
          <button key={v} onClick={() => setView(v)}
            className={cn('rounded-md px-3 py-1 text-sm capitalize transition-colors',
              view === v ? 'bg-surface-3 text-text' : 'text-muted hover:text-text')}>
            {v}
          </button>
        ))}
      </div>

      {view === 'decisions' && <DecisionsView />}
      {view === 'scorecard' && <ScorecardView />}

      {view === 'timeline' && <>
      {tickers.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-1.5">
          <button onClick={() => setTicker(null)}>
            <Badge tone={ticker === null ? 'accent' : 'neutral'}
              className={cn('cursor-pointer', ticker === null && 'ring-1 ring-accent/40')}>
              All ({data?.length})
            </Badge>
          </button>
          {tickers.map((t) => (
            <button key={t} onClick={() => setTicker(t === ticker ? null : t)}>
              <Badge tone={ticker === t ? 'accent' : 'neutral'}
                className={cn('cursor-pointer', ticker === t && 'ring-1 ring-accent/40')}>
                {t}
              </Badge>
            </button>
          ))}
          {ticker && (
            <span className="ml-auto">
              <Button variant="accent" onClick={() => setPromptOpen(true)}
                title={`Regenerate the Claude prompt for ${ticker} (includes prior discussion context)`}>
                <TerminalSquare className="h-4 w-4" /> Get prompt · {ticker}
              </Button>
            </span>
          )}
        </div>
      )}
      <DiscussionTimeline discussions={filtered} />
      {ticker && (
        <AskClaudeModal symbol={ticker} open={promptOpen} onClose={() => setPromptOpen(false)} />
      )}
      </>}
    </div>
  )
}
