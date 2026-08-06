// Discover: deduped candidate LEADS from evidence screens (insider clusters,
// PEAD, short-interest deltas) + agent web sweeps. Leads, not recommendations
// — every row carries its evidence, reasoning, and dates. Click → Analysis.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileDown, Check, Compass, Eye, RefreshCw, TerminalSquare, X as XIcon } from 'lucide-react'
import { toast } from 'sonner'
import { fmtDate, fmtMarketCap } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, Button, Card, EmptyState, Modal, Skeleton } from '@/components/ui'

interface Candidate {
  symbol: string; first_seen: string; last_seen: string
  sources: string[]; reasons: { source: string; reason: string; date: string }[]
  market_cap_m: number | null; status: string; returned: number
}

const SOURCE_TONE: Record<string, 'up' | 'warn' | 'accent' | 'neutral'> = {
  'insider-cluster': 'up', pead: 'accent', 'short-interest': 'warn', 'web-sweep': 'neutral',
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? `${r.status}`)
  return r.json()
}

export default function Discover({ onAnalyze }: { onAnalyze: (s: string) => void }) {
  const qc = useQueryClient()
  const [showDismissed, setShowDismissed] = useState(false)
  const [promptOpen, setPromptOpen] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const { data: rows, isLoading } = useQuery({
    queryKey: ['discovery', showDismissed],
    queryFn: () => j<Candidate[]>(`/api/discovery?include_dismissed=${showDismissed}`),
  })
  const { data: promptData } = useQuery({
    queryKey: ['discovery-prompt'],
    queryFn: () => j<{ prompt: string }>('/api/discovery/prompt'),
    enabled: promptOpen,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['discovery'] })
  const scan = useMutation({
    mutationFn: () => j<{ added: Record<string, number>; total_added: number }>(
      '/api/discovery/scan', { method: 'POST' }),
    onSuccess: (d) => {
      invalidate()
      toast.success(`Screens done — ${d.total_added} new lead${d.total_added === 1 ? '' : 's'} `
        + `(insider ${d.added.insider} · pead ${d.added.pead} · SI ${d.added.short_interest})`)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const patch = (symbol: string, status: string) =>
    j(`/api/discovery/${symbol}`, { method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }) }).then(invalidate)
  const promote = (symbol: string) =>
    j(`/api/discovery/${symbol}/promote`, { method: 'POST' }).then(() => {
      invalidate()
      qc.invalidateQueries({ queryKey: ['watchlist'] })
      toast.success(`${symbol} promoted to watchlist`)
    })

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button variant="accent" onClick={() => scan.mutate()} disabled={scan.isPending}>
          <RefreshCw className={cn('h-4 w-4', scan.isPending && 'animate-spin')} />
          {scan.isPending ? 'Screening…' : 'Run evidence screens'}
        </Button>
        <Button onClick={() => setPromptOpen(true)}>
          <TerminalSquare className="h-4 w-4" /> Web-sweep prompt
        </Button>
        <Button onClick={() => window.open('/api/discovery/report', '_blank')}
          title="Export the candidate digest as a printable PDF">
          <FileDown className="h-4 w-4" /> Export
        </Button>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={showDismissed}
            onChange={(e) => setShowDismissed(e.target.checked)} />
          show dismissed
        </label>
      </div>

      {isLoading && <Card><Skeleton className="h-40 w-full" /></Card>}
      {!isLoading && !rows?.length && (
        <EmptyState icon={<Compass className="h-7 w-7" />} title="No leads yet"
          body="Run the evidence screens (insider clusters · earnings-surprise drift · short-interest moves), or copy the web-sweep prompt into your agent — its file lands here automatically." />
      )}

      <div className="space-y-2">
        {rows?.map((r) => (
          <Card key={r.symbol} hover className={cn(r.status === 'dismissed' && 'opacity-50')}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <button onClick={() => onAnalyze(r.symbol)}
                    className="text-base font-bold hover:text-accent">{r.symbol}</button>
                  {r.sources.map((s) => (
                    <Badge key={s} tone={SOURCE_TONE[s] ?? 'neutral'} className="text-[10px]">{s}</Badge>
                  ))}
                  {r.sources.length > 1 && (
                    <Badge tone="accent" className="text-[10px]" title="Multiple independent evidence types — the interesting kind">
                      convergence
                    </Badge>
                  )}
                  {!!r.returned && (
                    <Badge tone="warn" className="text-[10px]" title="Dismissed before, but NEW evidence arrived">returned</Badge>
                  )}
                  <span className="text-[11px] text-faint tnum">
                    {fmtMarketCap(r.market_cap_m)} · seen {fmtDate(r.first_seen)}
                    {r.last_seen !== r.first_seen && ` → ${fmtDate(r.last_seen)}`}
                  </span>
                </div>
                <button className="mt-1 block text-left text-sm text-muted hover:text-text"
                  onClick={() => setExpanded(expanded === r.symbol ? null : r.symbol)}>
                  {r.reasons.at(-1)?.reason}
                </button>
                {expanded === r.symbol && r.reasons.length > 1 && (
                  <ul className="mt-1.5 space-y-1 border-t border-border pt-1.5">
                    {r.reasons.slice(0, -1).reverse().map((re, i) => (
                      <li key={i} className="text-xs text-faint">
                        <Badge className="mr-1.5 text-[9px]">{re.source}</Badge>
                        {re.reason} <span className="tnum">({fmtDate(re.date)})</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="flex shrink-0 gap-1">
                <Button variant="accent" onClick={() => onAnalyze(r.symbol)}>Analyze</Button>
                {r.status !== 'promoted' && (
                  <Button variant="ghost" onClick={() => promote(r.symbol)} title="Add to watchlist">
                    <Eye className="h-4 w-4" />
                  </Button>
                )}
                {r.status === 'dismissed' ? (
                  <Button variant="ghost" onClick={() => patch(r.symbol, 'new')} title="Restore">
                    <Check className="h-4 w-4" />
                  </Button>
                ) : (
                  <Button variant="ghost" onClick={() => patch(r.symbol, 'dismissed')} title="Dismiss (remembered)">
                    <XIcon className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>

      <p className="mt-4 text-[11px] text-faint">
        Leads, not recommendations: each row is one or more pieces of documented
        evidence (insider clusters, post-earnings drift, short-interest moves,
        or a reasoned web sweep) on a NON-mainstream name. Open Analysis to
        verify — the Insiders panel is the confirmation step for cluster leads.
        Small/less-covered names mean thinner data and higher risk, by nature.
      </p>

      <Modal open={promptOpen} onClose={() => setPromptOpen(false)} title="Web-sweep prompt" wide>
        <p className="mb-2 text-sm text-muted">
          Paste into any agent with web access — it writes
          <code className="mx-1 rounded bg-surface-3 px-1">discoveries/&#123;date&#125;.md</code>
          and the app ingests it automatically (dedupe by symbol).
        </p>
        <pre className="max-h-72 overflow-auto rounded-xl border border-border bg-bg p-3 text-xs text-muted">
          {promptData?.prompt ?? '…'}
        </pre>
        <div className="mt-3 flex justify-end">
          <Button variant="accent" onClick={async () => {
            await navigator.clipboard.writeText(promptData?.prompt ?? '')
            toast.success('Prompt copied')
          }}>Copy prompt</Button>
        </div>
      </Modal>
    </div>
  )
}
