// Sticky ticker header: animated price (Robinhood-style NumberFlow), day
// change, the 80% range headline, today's-move z-score, earnings countdown.
// Stays pinned while the user scrolls the section grid.
import NumberFlow from '@number-flow/react'
import { motion } from 'framer-motion'
import { CalendarClock, Eye, EyeOff, FileDown, MessageSquarePlus, Moon, RefreshCw } from 'lucide-react'
import { fmtMoney, fmtPct, plColor } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, Button, GlossaryTip } from '@/components/ui'
import { useExtendedHours } from '@/hooks'
import type { Analysis, GlossaryEntry } from '@/types'

export function SnapshotHeader({ a, onRefresh, refreshing, onAskClaude, watched, onToggleWatch, glossary }: {
  a: Analysis
  onRefresh: () => void
  refreshing: boolean
  onAskClaude: () => void
  watched: boolean
  onToggleWatch: () => void
  glossary: Record<string, GlossaryEntry>
}) {
  const q = a.quote
  const { on: ahOn, toggle: ahToggle } = useExtendedHours()

  // After-hours display (on by default): when an extended session is live and
  // the toggle is on, the big number IS the AH price, with the official close
  // shown muted beneath. Toggle off → regular-session view only.
  const showAH = ahOn && !!q?.is_extended && q?.ah_price != null
  const price = showAH ? q!.ah_price! : (q?.regular_last ?? q?.last ?? 0)
  const chg = showAH ? (q!.ah_change_pct ?? null)
    : (q?.regular_change_pct ?? q?.net_change_pct ?? null)

  // A stale quote wearing a live face is the worst lie this header can tell —
  // surface it where the eye lands, not in a corner chip.
  const staleEnv = ['quote', 'history']
    .map((k) => a.sections?.[k])
    .find((e) => e && ['stale', 'unavailable'].includes(e.status))
  const staleAgeH = staleEnv?.age_seconds ? (staleEnv.age_seconds / 3600).toFixed(1) : null

  return (
    <motion.div
      layout
      className="sticky top-0 z-30 -mx-6 border-b border-border bg-bg/85 px-6 py-3 backdrop-blur-md"
      initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
      {staleEnv && (
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-3 py-1.5 text-xs text-yellow-500">
          <span className="font-semibold">PRICES ARE STALE</span>
          <span>
            provider failing ({staleEnv.error_reason ?? 'error'})
            {staleAgeH ? ` — showing data from ${staleAgeH}h ago` : ''}
            {staleEnv.error_reason === 'auth_expired' &&
              ' · fix: backend/.venv/bin/python backend/schwab_reauth.py'}
          </span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <div>
          <div className="flex items-baseline gap-2">
            <h2 className="text-xl font-bold tracking-tight">{a.symbol}</h2>
            <span className="max-w-56 truncate text-xs text-faint">{q?.name}</span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="tnum text-2xl font-bold">
              <NumberFlow
                value={price}
                format={{ style: 'currency', currency: 'USD' }}
                transformTiming={{ duration: 500, easing: 'ease-out' }}
              />
            </span>
            <span className={cn('tnum text-sm font-semibold', plColor(chg))}>
              {fmtPct(chg, 2, true)}
            </span>
            {showAH && (
              <Badge tone="accent" className="text-[10px]" title="Extended-hours trade — regular close shown below">
                <Moon className="h-2.5 w-2.5" /> AH
              </Badge>
            )}
          </div>
          {showAH && q?.regular_last != null && (
            <div className="tnum text-[11px] text-faint">
              at close {fmtMoney(q.regular_last)} ({fmtPct(q.regular_change_pct, 2, true)})
            </div>
          )}
        </div>

        {a.band && (
          <div className="border-l border-border pl-5">
            <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-faint">
              80% expected range <GlossaryTip entry={glossary.expected_range} />
            </div>
            <div className="tnum text-sm font-medium">
              {fmtMoney(a.band.low)} – {fmtMoney(a.band.high)}
              <span className="ml-1.5 text-xs text-muted">±{a.band.half_width_pct.toFixed(2)}%</span>
            </div>
          </div>
        )}

        {a.move && (
          <div className="border-l border-border pl-5">
            <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-faint">
              today's move <GlossaryTip entry={glossary.move_z} />
            </div>
            <Badge tone={a.move.outside_band ? 'warn' : 'neutral'} className="tnum mt-0.5">
              {a.move.z >= 0 ? '+' : ''}{a.move.z.toFixed(2)}σ
              {a.move.outside_band && ' · outside band'}
            </Badge>
          </div>
        )}

        {a.earnings?.available && a.earnings.days_until != null && (
          <div className="border-l border-border pl-5">
            <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-faint">
              earnings <GlossaryTip entry={glossary.earnings_countdown} />
            </div>
            <Badge tone={a.earnings.days_until <= 5 ? 'warn' : 'neutral'} className="mt-0.5">
              <CalendarClock className="h-3 w-3" />
              {a.earnings.days_until === 0 ? 'today' : `in ${a.earnings.days_until}d`}
            </Badge>
          </div>
        )}

        {/* Research stance: surfaced from the latest Claude discussion — the
            app never computes buy/sell/hold itself (signals can't call
            direction; a reasoned session judgment is a different animal). */}
        <div className="border-l border-border pl-5">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-faint">
            claude stance <GlossaryTip entry={glossary.research_stance} />
          </div>
          {a.claude_stance ? (
            <span className="mt-0.5 inline-flex items-center gap-1.5"
              title={`${a.claude_stance.stance_note ?? ''}\n${a.claude_stance.created_ts.slice(0, 10)}${a.claude_stance.drift_material ? ' — price has moved beyond the expected range since this call' : ''}`}>
              <Badge tone={
                a.claude_stance.stance === 'buy' ? 'up'
                  : a.claude_stance.stance === 'sell' || a.claude_stance.stance === 'trim' ? 'down'
                    : 'accent'}>
                {a.claude_stance.stance.toUpperCase()}
                {a.claude_stance.stance_horizon && ` · ${a.claude_stance.stance_horizon}`}
              </Badge>
              <span className="text-[10px] text-faint tnum">
                {a.claude_stance.created_ts.slice(5, 10)}
                {a.claude_stance.drift_material && a.claude_stance.drift_pct != null &&
                  ` · price ${a.claude_stance.drift_pct > 0 ? '+' : ''}${a.claude_stance.drift_pct}% since`}
              </span>
              {a.claude_stance.stance === 'buy' && a.bottom_context && (
                <Badge tone={a.bottom_context.bottom_decile ? 'accent' : 'warn'}
                  className="text-[10px] tnum"
                  title={`Where this buy sits: ${a.bottom_context.drawdown_pct}% off the 52w high, ${a.bottom_context.above_52w_low_pct}% above the low, cheaper than ${100 - a.bottom_context.range_percentile}% of the last year's closes. ${a.bottom_context.insider_buys_into_drawdown ? 'Insiders are buying this drawdown. ' : ''}${a.bottom_context.note}`}>
                  {a.bottom_context.bottom_decile ? 'near yearly lows'
                    : a.bottom_context.range_percentile >= 70 ? 'buying strength'
                      : 'mid-range'}
                </Badge>
              )}
            </span>
          ) : (
            <button onClick={onAskClaude}
              className="mt-0.5 text-xs text-faint underline decoration-dotted underline-offset-2 hover:text-accent"
              title="No stance yet — run a Claude discussion and its reasoned call will appear here">
              none yet — ask Claude
            </button>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={ahToggle}
            role="switch" aria-checked={ahOn}
            title={ahOn ? 'Showing extended-hours prices — click for regular session only'
              : 'Regular session only — click to show after-hours prices'}
            className={cn('flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors',
              ahOn ? 'border-accent/40 bg-accent/10 text-accent'
                : 'border-border text-muted hover:bg-surface-2')}>
            <Moon className="h-3.5 w-3.5" /> AH {ahOn ? 'on' : 'off'}
          </button>
          <Button variant={watched ? 'default' : 'ghost'} onClick={onToggleWatch}
            title={watched ? 'Remove from watchlist' : 'Add to watchlist'}>
            {watched
              ? <><EyeOff className="h-4 w-4" /> Watching ✓</>
              : <><Eye className="h-4 w-4" /> Watch</>}
          </Button>
          <Button variant="accent" onClick={onAskClaude}>
            <MessageSquarePlus className="h-4 w-4" /> Ask Claude
          </Button>
          <Button
            onClick={() => window.open(`/api/analysis/${a.symbol}/report`, '_blank')}
            title="Export the full research snapshot — analysis + Claude discussions — as a printable PDF">
            <FileDown className="h-4 w-4" /> Export
          </Button>
          <Button onClick={onRefresh} disabled={refreshing} title="Force-refresh all sections">
            <RefreshCw className={cn('h-4 w-4', refreshing && 'animate-spin')} />
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </Button>
        </div>
      </div>
    </motion.div>
  )
}
