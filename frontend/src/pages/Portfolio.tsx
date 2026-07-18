// Live Schwab account view — supersedes the old standalone Portfolio app.
// Read-only by design (this codebase never places orders). Every row click
// jumps to Analysis for that symbol; its Claude discussion history rides
// along automatically because discussions are keyed by ticker.
import NumberFlow from '@number-flow/react'
import { motion } from 'framer-motion'
import { Briefcase, Check, Download, Plus, RefreshCw, X as XIcon } from 'lucide-react'
import { useState } from 'react'
import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from 'recharts'
import { fmtAge, fmtMoney, fmtNum, fmtPct, plColor } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, Button, Card, CardTitle, EmptyState, SkeletonCard } from '@/components/ui'
import {
  useActionMutations, useActions, usePortfolio, usePortfolioIncome,
  usePortfolioRisk, useRefreshPortfolio,
} from '@/hooks'

export default function Portfolio({ onAnalyze }: { onAnalyze: (s: string) => void }) {
  const { data: p, isLoading, error } = usePortfolio()
  const { data: income } = usePortfolioIncome()   // P2: lazy — table paints first
  const refresh = useRefreshPortfolio()
  const yieldOf = (sym: string) => income?.holdings.find((h) => h.symbol === sym)

  if (isLoading) {
    return <div className="space-y-4"><SkeletonCard lines={2} /><SkeletonCard lines={8} /></div>
  }
  if (error || !p) {
    return (
      <EmptyState
        icon={<Briefcase className="h-7 w-7" />}
        title="Couldn't load your Schwab account"
        body={(error as Error | null)?.message ?? 'Unknown error'}
        action={<Button variant="accent" onClick={() => refresh.mutate()}>Retry</Button>}
      />
    )
  }

  const byType = p.positions.reduce<Record<string, number>>((acc, pos) => {
    acc[pos.asset_type] = (acc[pos.asset_type] ?? 0) + pos.market_value
    return acc
  }, {})

  return (
    <div className="space-y-4">
      {/* summary strip */}
      <Card>
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-faint">Total value</div>
            <div className="tnum text-2xl font-bold">
              <NumberFlow value={p.total_value} format={{ style: 'currency', currency: 'USD' }} />
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-faint">Today</div>
            <div className={cn('tnum text-lg font-semibold', plColor(p.day_pl))}>
              {fmtMoney(p.day_pl)} ({fmtPct(p.day_pl_pct, 2, true)})
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-faint">Cash</div>
            <div className="tnum text-lg font-semibold">{fmtMoney(p.cash)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-faint">Positions</div>
            <div className="tnum text-lg font-semibold">{p.positions.length}</div>
          </div>
          {income && (
            <div title={`Real dividend yields (Finnhub) over ${income.coverage_pct}% of the portfolio — not hand-maintained estimates`}>
              <div className="text-[10px] uppercase tracking-wide text-faint">Income /yr (proj.)</div>
              <div className="tnum text-lg font-semibold">
                {fmtMoney(income.projected_annual, 0)}
                <span className="ml-1.5 text-xs text-muted">
                  {fmtPct(income.blended_yield_pct, 2)} blended
                </span>
              </div>
            </div>
          )}
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[10px] text-faint">
              {p.source === 'stale' ? 'stale · ' : ''}{fmtAge(p.age_seconds)} · read-only
            </span>
            <Button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              <RefreshCw className={cn('h-4 w-4', refresh.isPending && 'animate-spin')} />
              {refresh.isPending ? 'Syncing…' : 'Sync'}
            </Button>
          </div>
        </div>
        {/* allocation by asset type (cash included) */}
        <div className="mt-4">
          <div className="flex h-2 overflow-hidden rounded-full bg-surface-3">
            {Object.entries(byType).map(([t, v], i) => (
              <div key={t}
                className={['bg-accent', 'bg-accent/60', 'bg-accent/35', 'bg-accent/20'][i % 4]}
                style={{ width: `${(v / p.total_value) * 100}%` }} title={t} />
            ))}
            <div className="bg-warn/50" style={{ width: `${(p.cash / p.total_value) * 100}%` }} title="CASH" />
          </div>
          <div className="mt-1.5 flex flex-wrap gap-3 text-[11px] text-muted">
            {Object.entries(byType).map(([t, v]) => (
              <span key={t} className="tnum">{t.toLowerCase()} {fmtPct((v / p.total_value) * 100, 1)}</span>
            ))}
            <span className="tnum">cash {fmtPct((p.cash / p.total_value) * 100, 1)}</span>
          </div>
        </div>
      </Card>

      {/* organic value history — grows with use, no backfill */}
      {p.history.length > 1 && (
        <Card>
          <CardTitle>Value over time <span className="normal-case text-faint">(recorded each live sync)</span></CardTitle>
          <div className="h-32">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={p.history}>
                <YAxis domain={['auto', 'auto']} hide />
                <Tooltip
                  contentStyle={{ background: 'var(--color-surface-2)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 12 }}
                  labelFormatter={(l) => String(l)}
                  formatter={(v) => [fmtMoney(Number(v)), 'total']}
                />
                <Line type="monotone" dataKey="total_value" dot={false} strokeWidth={1.5}
                  stroke="var(--color-accent)" isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {/* positions — click a row to analyze (discussion history is per-ticker,
          so it's already waiting on the Analysis page) */}
      <Card>
        <CardTitle>Holdings</CardTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-faint">
                <th className="pb-2 font-medium">Symbol</th>
                <th className="pb-2 text-right font-medium">Qty</th>
                <th className="pb-2 text-right font-medium">Price paid</th>
                <th className="pb-2 text-right font-medium">Value</th>
                <th className="pb-2 text-right font-medium">Today</th>
                <th className="pb-2 text-right font-medium">Gain</th>
                <th className="pb-2 text-right font-medium">Weight</th>
                <th className="pb-2 text-right font-medium" title="Trailing dividend yield (Finnhub)">Yield</th>
                <th className="pb-2 text-right font-medium" title="market value × yield">Income/yr</th>
              </tr>
            </thead>
            <tbody>
              {p.positions.map((pos) => (
                <motion.tr
                  key={pos.symbol}
                  layout
                  onClick={() => onAnalyze(pos.symbol)}
                  className="cursor-pointer border-t border-border/60 transition-colors hover:bg-surface-2"
                  title={`Analyze ${pos.symbol}`}>
                  <td className="py-2.5">
                    <span className="font-bold text-text">{pos.symbol}</span>
                    <span className="ml-2 hidden max-w-44 truncate text-xs text-faint md:inline-block align-bottom">
                      {pos.description}
                    </span>
                  </td>
                  <td className="tnum py-2.5 text-right">{fmtNum(pos.qty, 2)}</td>
                  <td className="tnum py-2.5 text-right text-muted">{fmtMoney(pos.avg_cost)}</td>
                  <td className="tnum py-2.5 text-right font-medium">{fmtMoney(pos.market_value)}</td>
                  <td className={cn('tnum py-2.5 text-right', plColor(pos.day_pl))}>
                    {fmtMoney(pos.day_pl)}
                  </td>
                  <td className={cn('tnum py-2.5 text-right', plColor(pos.gain))}>
                    {fmtMoney(pos.gain)}
                    <span className="ml-1 text-xs opacity-80">({fmtPct(pos.gain_pct, 1, true)})</span>
                  </td>
                  <td className="tnum py-2.5 text-right text-muted">{fmtPct(pos.weight_pct, 1)}</td>
                  <td className="tnum py-2.5 text-right text-muted">
                    {fmtPct(yieldOf(pos.symbol)?.yield_pct ?? null, 2)}
                  </td>
                  <td className="tnum py-2.5 text-right text-muted">
                    {(yieldOf(pos.symbol)?.income_annual ?? 0) > 0
                      ? fmtMoney(yieldOf(pos.symbol)!.income_annual, 0) : '—'}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
        {p.positions.length === 0 && (
          <p className="py-4 text-sm text-faint">No positions returned for this account.</p>
        )}
      </Card>

      <RiskCard />

      <SandboxCard symbols={p.positions.map((x) => x.symbol)} />

      <ActionPlan />

      <p className="text-[11px] text-faint">
        <Badge tone="neutral" className="mr-2">read-only</Badge>
        This view reads your Schwab account through the shared weekly-re-auth token.
        It never places, changes, or cancels orders — you trade in the Schwab app.
      </p>
    </div>
  )
}

// ─── Roadmap #6: the validated band machinery, applied to the whole account ────

function RiskCard() {
  const { data: r } = usePortfolioRisk()
  if (!r) return null
  if (!r.available) {
    return (
      <Card><CardTitle>Portfolio risk</CardTitle>
        <p className="py-1 text-sm text-faint">
          Not enough price history yet{r.excluded?.length ? ` (missing: ${r.excluded.join(', ')})` : ''}.
        </p>
      </Card>
    )
  }
  return (
    <Card>
      <CardTitle>Portfolio risk · tomorrow's 80% range for the WHOLE account</CardTitle>
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-faint">80% range (next session)</div>
          <div className="tnum text-lg font-semibold">
            {fmtMoney(r.band_low)} – {fmtMoney(r.band_high)}
            <span className="ml-1.5 text-xs text-muted">±{fmtMoney(r.band_dollars, 0)}</span>
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-faint">Annualized vol</div>
          <div className="tnum text-lg font-semibold">{fmtPct(r.annualized_vol_pct, 1)}</div>
        </div>
        <div title="Weighted-average holding vol ÷ portfolio vol — above 1 means diversification is genuinely removing volatility">
          <div className="text-[10px] uppercase tracking-wide text-faint">Diversification ratio</div>
          <div className="tnum text-lg font-semibold">{fmtNum(r.diversification_ratio, 2)}×</div>
        </div>
        <div title={`Herfindahl ${fmtNum(r.hhi, 3)} — lower is more diversified`}>
          <div className="text-[10px] uppercase tracking-wide text-faint">Effective positions</div>
          <div className="tnum text-lg font-semibold">{fmtNum(r.effective_positions, 1)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-faint">Portfolio beta</div>
          <div className="tnum text-lg font-semibold">{fmtNum(r.portfolio_beta, 2)}</div>
        </div>
      </div>
      <p className="mt-3 border-t border-border pt-2 text-[11px] leading-relaxed text-faint">
        Same math as the ticker bands (60d correlations × your live weights;
        cash counts as zero-vol). A range for tomorrow — never a direction.
        {!!r.excluded?.length && ` Excluded (no history): ${r.excluded.join(', ')}.`}
      </p>
    </Card>
  )
}

// ─── Rebalance sandbox: preview a trade's effect on the WHOLE book ─────────────

function SandboxCard({ symbols }: { symbols: string[] }) {
  const [changes, setChanges] = useState<{ symbol: string; amount: string }[]>([
    { symbol: '', amount: '' },
  ])
  const [result, setResult] = useState<import('@/types').WhatIfResult | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    const payload: Record<string, number> = {}
    for (const c of changes) {
      const v = Number(c.amount)
      if (c.symbol.trim() && !Number.isNaN(v) && v !== 0) payload[c.symbol.trim().toUpperCase()] = v
    }
    if (!Object.keys(payload).length) return
    setBusy(true)
    try {
      const { api } = await import('@/lib/api')
      setResult(await api.whatIf(payload))
    } catch (e) {
      const { toast } = await import('sonner')
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const D = ({ b, a, fmt, invert }: { b?: number | null; a?: number | null
    fmt: (v?: number | null) => string; invert?: boolean }) => {
    const up = a != null && b != null && a > b
    const good = invert ? !up : up
    return (
      <span className="tnum">
        {fmt(b)} → <span className={a === b ? '' : good ? 'text-up' : 'text-down'}>{fmt(a)}</span>
      </span>
    )
  }

  return (
    <Card>
      <CardTitle>Rebalance sandbox <span className="normal-case text-faint">(preview only — nothing executes)</span></CardTitle>
      <div className="space-y-2">
        {changes.map((c, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input list="sandbox-symbols" value={c.symbol}
              onChange={(e) => setChanges(changes.map((x, j) => j === i ? { ...x, symbol: e.target.value } : x))}
              placeholder="SYM" aria-label="Symbol"
              className="w-24 rounded-lg border border-border bg-bg px-2 py-1.5 text-sm uppercase outline-none focus:border-accent/60" />
            <input value={c.amount}
              onChange={(e) => setChanges(changes.map((x, j) => j === i ? { ...x, amount: e.target.value } : x))}
              placeholder="± $ amount (e.g. -3000 = trim)" aria-label="Dollar change"
              className="w-56 rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent/60" />
            {i === changes.length - 1 && (
              <Button variant="ghost" onClick={() => setChanges([...changes, { symbol: '', amount: '' }])}>
                <Plus className="h-4 w-4" />
              </Button>
            )}
          </div>
        ))}
        <datalist id="sandbox-symbols">
          {symbols.map((s) => <option key={s} value={s} />)}
        </datalist>
        <Button variant="accent" onClick={run} disabled={busy}>
          {busy ? 'Computing…' : 'Preview impact'}
        </Button>
      </div>
      {result && (
        <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 border-t border-border pt-3 text-sm md:grid-cols-2">
          <div>80% range (±$): <D b={result.before.risk.band_dollars} a={result.after.risk.band_dollars} fmt={(v) => fmtMoney(v, 0)} invert /></div>
          <div>Annualized vol: <D b={result.before.risk.annualized_vol_pct} a={result.after.risk.annualized_vol_pct} fmt={(v) => fmtPct(v, 1)} invert /></div>
          <div>Diversification: <D b={result.before.risk.diversification_ratio} a={result.after.risk.diversification_ratio} fmt={(v) => `${fmtNum(v, 2)}×`} /></div>
          <div>Effective positions: <D b={result.before.risk.effective_positions} a={result.after.risk.effective_positions} fmt={(v) => fmtNum(v, 1)} /></div>
          <div>Portfolio beta: <D b={result.before.risk.portfolio_beta} a={result.after.risk.portfolio_beta} fmt={(v) => fmtNum(v, 2)} invert /></div>
          <div>Income /yr: <D b={result.before.income.projected_annual} a={result.after.income.projected_annual} fmt={(v) => fmtMoney(v, 0)} /></div>
          <div>Blended yield: <D b={result.before.income.blended_yield_pct} a={result.after.income.blended_yield_pct} fmt={(v) => fmtPct(v, 2)} /></div>
          <div>Cash: <span className="tnum">{fmtMoney(result.cash_before, 0)} → {fmtMoney(result.cash_after, 0)}</span></div>
        </div>
      )}
      <p className="mt-3 border-t border-border pt-2 text-[11px] text-faint">
        Negative $ = trim toward cash; positive $ = buy from cash (guarded
        against overspending). Green/red = direction of change, judged per
        metric (lower vol = green, higher income = green). You still execute
        in the Schwab app.
      </p>
    </Card>
  )
}

// ─── P5: action plan (ported from the old Portfolio app) ────────────────────────

function ActionPlan() {
  const { data: actions } = useActions()
  const { add, patch, importOld } = useActionMutations()
  const [text, setText] = useState('')
  const [sym, setSym] = useState('')

  const submit = () => {
    if (!text.trim()) return
    add.mutate({ symbol: sym.trim() || undefined, action: text.trim() })
    setText(''); setSym('')
  }

  return (
    <Card>
      <CardTitle right={
        <Button variant="ghost" onClick={() => importOld.mutate()} disabled={importOld.isPending}
          title="One-time port of the old Portfolio app's action plan">
          <Download className="h-4 w-4" /> Import from old app
        </Button>
      }>
        Action plan
      </CardTitle>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={sym} onChange={(e) => setSym(e.target.value)} placeholder="SYM"
          aria-label="Action symbol"
          className="w-20 rounded-lg border border-border bg-bg px-2 py-1.5 text-sm uppercase outline-none focus:border-accent/60" />
        <input value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="e.g. TRIM — overlaps 401k, near breakeven"
          aria-label="Action description"
          className="min-w-64 flex-1 rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent/60" />
        <Button variant="accent" onClick={submit} disabled={!text.trim()}>
          <Plus className="h-4 w-4" /> Add
        </Button>
      </div>
      {!actions?.length && (
        <p className="py-1 text-sm text-faint">
          No actions yet — your trim/buy/sell plan lives here with a done/dismiss state.
        </p>
      )}
      <ul className="divide-y divide-border/60">
        {actions?.map((a) => (
          <li key={a.id} className={cn('flex items-center gap-3 py-2',
            a.status !== 'open' && 'opacity-45')}>
            {a.symbol && <Badge tone="accent">{a.symbol}</Badge>}
            <span className={cn('flex-1 text-sm', a.status === 'done' && 'line-through')}>
              {a.action}{a.rationale && <span className="text-faint"> — {a.rationale}</span>}
            </span>
            {a.status === 'open' ? (
              <span className="flex gap-1">
                <button title="Mark done" className="rounded-md border border-up/30 p-1 text-up hover:bg-up/10"
                  onClick={() => patch.mutate({ id: a.id, status: 'done' })}>
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button title="Dismiss" className="rounded-md border border-border p-1 text-muted hover:bg-surface-2"
                  onClick={() => patch.mutate({ id: a.id, status: 'dismissed' })}>
                  <XIcon className="h-3.5 w-3.5" />
                </button>
              </span>
            ) : (
              <button className="text-[11px] text-faint hover:text-text"
                onClick={() => patch.mutate({ id: a.id, status: 'open' })}>
                reopen
              </button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}
