// The premium-seller's lens: implied vs empirical breach probability per
// short-strike candidate. Surfaces RISK PRICING (variance risk premium),
// never direction. Every number's validation caveat rides along.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { api } from '@/lib/api'
import { fmtMoney, fmtNum, fmtPct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, SectionShell, Skeleton } from '@/components/ui'
import type { Analysis, GlossaryEntry, LensRow } from '@/types'

export function PremiumLensCard({ a, glossary }: {
  a: Analysis
  glossary: Record<string, GlossaryEntry>
}) {
  const [side, setSide] = useState<'calls' | 'puts'>('calls')
  const { data: lens, isLoading } = useQuery({
    queryKey: ['lens', a.symbol],
    queryFn: () => api.lens(a.symbol),
    staleTime: 300_000,
  })
  const { data: vrp } = useQuery({
    queryKey: ['vrp', a.symbol],
    queryFn: () => api.vrp(a.symbol),
    staleTime: 3600_000,
  })

  if (lens && !lens.available) return null // no options / thin history → hide

  const rows: LensRow[] = lens ? lens[side] : []

  return (
    <SectionShell title="Premium seller's lens" glossary={glossary.premium_lens}>
      {isLoading && <Skeleton className="h-40 w-full" />}
      {lens?.available && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <div className="inline-flex rounded-lg border border-border bg-bg p-0.5">
              {(['calls', 'puts'] as const).map((s) => (
                <button key={s} onClick={() => setSide(s)}
                  className={cn('rounded-md px-3 py-1 text-xs font-medium transition-colors',
                    side === s ? 'bg-surface-3 text-text' : 'text-muted hover:text-text')}>
                  {s === 'calls' ? 'Covered calls' : 'Cash-secured puts'}
                </button>
              ))}
            </div>
            {a.position?.owned && side === 'calls' && (
              <Badge tone="accent">you hold {fmtNum(a.position.qty, 0)} sh — {Math.floor((a.position.qty ?? 0) / 100)} contract{Math.floor((a.position.qty ?? 0) / 100) === 1 ? '' : 's'} coverable</Badge>
            )}
            {lens.skew_25d_pp != null && (
              <span className="text-xs text-muted tnum" title="25Δ put IV − 25Δ call IV: what the crowd pays for crash insurance vs upside">
                25Δ skew {lens.skew_25d_pp > 0 ? '+' : ''}{lens.skew_25d_pp}pp
              </span>
            )}
            {vrp && vrp.n_resolved > 0 && (
              <span className="text-xs text-muted tnum"
                title={`${vrp.n_resolved} resolved implied-move captures — ${vrp.note}`}>
                VRP {vrp.avg_ratio}× ({vrp.n_resolved} obs)
              </span>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-faint">
                  <th className="pb-1.5 font-medium">Strike</th>
                  <th className="pb-1.5 text-right font-medium">DTE</th>
                  <th className="pb-1.5 text-right font-medium">Bid</th>
                  <th className="pb-1.5 text-right font-medium" title="premium / capital at risk, annualized">Yield/yr</th>
                  <th className="pb-1.5 text-right font-medium" title="the market's breach probability (|delta|)">Market P(breach)</th>
                  <th className="pb-1.5 text-right font-medium" title="this stock's own 2-year history at this horizon (validated ±6-9.5pp)">History P(breach)</th>
                  <th className="pb-1.5 text-right font-medium" title="market minus history — positive = you're being overpaid for the risk">Edge</th>
                  <th className="pb-1.5 text-right font-medium">OI</th>
                  <th className="pb-1.5 text-right font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="tnum border-t border-border/60">
                    <td className="py-1.5">
                      {fmtMoney(r.strike)}
                      <span className="ml-1 text-faint">({r.moneyness_pct > 0 ? '+' : ''}{r.moneyness_pct}%)</span>
                    </td>
                    <td className="py-1.5 text-right text-muted">{r.dte}d</td>
                    <td className="py-1.5 text-right">{fmtMoney(r.bid)}</td>
                    <td className="py-1.5 text-right font-medium">{fmtPct(r.yield_ann_pct, 1)}</td>
                    <td className="py-1.5 text-right">{fmtPct(r.implied_breach * 100, 1)}</td>
                    <td className="py-1.5 text-right">{fmtPct(r.empirical_breach * 100, 1)}</td>
                    <td className={cn('py-1.5 text-right font-semibold',
                      r.edge_pp >= 3 ? 'text-up' : r.edge_pp <= -3 ? 'text-down' : 'text-muted')}>
                      {r.edge_pp > 0 ? '+' : ''}{r.edge_pp}pp
                    </td>
                    <td className="py-1.5 text-right text-faint">{r.oi.toLocaleString()}</td>
                    <td className="py-1.5 text-right">
                      {r.earnings_inside && (
                        <Badge tone="warn" className="text-[9px]" title="Earnings lands inside this DTE — history-based probabilities do NOT apply through a print">
                          <AlertTriangle className="h-2.5 w-2.5" />E
                        </Badge>
                      )}
                      {r.tail_zone && (
                        <Badge className="ml-1 text-[9px]" title="Tail zone: probabilities under ~15% historically UNDERESTIMATE breaches by up to ~5-9pp — the tail is worse than shown">
                          tail
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
                {!rows.length && (
                  <tr><td colSpan={9} className="py-3 text-center text-faint">
                    No liquid candidates pass the filters (OI ≥ 50, spread ≤ 12%).
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          <p className="mt-2 border-t border-border pt-2 text-[10px] leading-relaxed text-faint">
            Positive edge = options paying more than this stock's own 2-yr history
            prices the risk (validated: ±6.8pp @5d, ±6.1pp @10d, ±9.5pp @21d;
            horizons beyond ~35 calendar days REJECTED by validation and not shown).
            This measures risk PRICING, not direction — assignment still happens,
            and rows marked <b>tail</b>/<b>E</b> are where history flatters or
            doesn't apply. Selling premium caps upside (calls) or commits cash
            into weakness (puts). Not advice.
          </p>
        </>
      )}
    </SectionShell>
  )
}
