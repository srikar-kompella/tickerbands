// Design C, as locked: big animated score, color band, lean badge (no
// confidence %), click-to-expand component table, organic history sparkline,
// permanent disclaimer. Fully reconstructible by eye — no black boxes.
import NumberFlow from '@number-flow/react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, Minus, TrendingDown, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import { Line, LineChart, ReferenceLine, ResponsiveContainer, YAxis } from 'recharts'
import { cn } from '@/lib/utils'
import { Badge, GlossaryTip, SectionShell } from '@/components/ui'
import type { Analysis, GlossaryEntry } from '@/types'

const COMPONENT_LABELS: Record<string, string> = {
  momentum: 'Momentum alignment',
  news_sentiment: 'News sentiment',
  options_positioning: 'Options positioning',
  analyst_trend: 'Analyst rec. trend',
  social_buzz: 'Social buzz',
}

function scoreTone(score: number): { ring: string; text: string } {
  if (score >= 70) return { ring: 'border-up/50', text: 'text-up' }
  if (score >= 40) return { ring: 'border-warn/50', text: 'text-warn' }
  return { ring: 'border-down/50', text: 'text-down' }
}

export function SetupScoreCard({ a, glossary }: {
  a: Analysis
  glossary: Record<string, GlossaryEntry>
}) {
  const [expanded, setExpanded] = useState(false)
  const s = a.setup_score
  if (!s) return null
  const tone = scoreTone(s.score)

  const LeanIcon = s.lean === 'BULLISH' ? TrendingUp : s.lean === 'BEARISH' ? TrendingDown : Minus
  const leanTone = s.lean === 'BULLISH' ? 'up' : s.lean === 'BEARISH' ? 'down' : 'neutral'

  return (
    <SectionShell title="Setup Score" glossary={glossary.setup_score}>
      <div className="flex items-center gap-5">
        {/* the big number — clickable, animated, color-banded */}
        <button
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
          title="Show component breakdown"
          className={cn(
            'flex h-24 w-24 shrink-0 flex-col items-center justify-center rounded-full border-4 bg-surface-2 transition-transform hover:scale-105',
            tone.ring)}>
          <span className={cn('tnum text-3xl font-bold leading-none', tone.text)}>
            <NumberFlow value={s.score} transformTiming={{ duration: 600, easing: 'ease-out' }} />
          </span>
          <span className="mt-0.5 text-[9px] uppercase tracking-wider text-faint">/ 100</span>
        </button>

        <div className="min-w-0 flex-1">
          <Badge tone={leanTone} className="text-xs">
            <LeanIcon className="h-3.5 w-3.5" /> {s.lean}
          </Badge>
          <p className="mt-1.5 text-xs text-muted">
            {s.components_available} of {s.components_total} signals available
            {s.vol_factor !== 1 && (
              <span className="ml-1 inline-flex items-center gap-1">
                · conviction ×{s.vol_factor}
                <GlossaryTip entry={glossary.vol_factor} />
              </span>
            )}
          </p>
          {/* organic history sparkline (Q8d — real rows only, no backfill) */}
          {a.score_history.length > 1 ? (
            <div className="mt-2 h-10">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={a.score_history}>
                  <YAxis domain={[0, 100]} hide />
                  <ReferenceLine y={50} stroke="rgba(255,255,255,0.08)" />
                  <Line type="monotone" dataKey="score" dot={false} strokeWidth={1.5}
                    stroke="var(--color-accent)" isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="mt-2 flex items-center gap-1 text-[11px] text-faint">
              History builds as you analyze — no fabricated backfill.
              <GlossaryTip entry={glossary.score_history} />
            </p>
          )}
        </div>
      </div>

      {/* expandable, fully transparent composition table */}
      <button
        onClick={() => setExpanded((e) => !e)}
        className="mt-3 flex w-full items-center justify-center gap-1 rounded-lg border border-border py-1 text-xs text-muted hover:bg-surface-2">
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', expanded && 'rotate-180')} />
        {expanded ? 'Hide' : 'Show'} composition
      </button>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="overflow-hidden">
            <table className="mt-3 w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-faint">
                  <th className="pb-2 font-medium">Signal</th>
                  <th className="pb-2 text-right font-medium">Value</th>
                  <th className="pb-2 text-right font-medium">Weight used</th>
                  <th className="pb-2 text-right font-medium">Points</th>
                </tr>
              </thead>
              <tbody>
                {s.components.map((c) => (
                  <tr key={c.component} className="border-t border-border">
                    <td className="flex items-center gap-1 py-2">
                      {COMPONENT_LABELS[c.component] ?? c.component}
                      <GlossaryTip entry={glossary[c.component]} />
                    </td>
                    {c.available ? (
                      <>
                        <td className="tnum py-2 text-right">{c.value?.toFixed(2)}</td>
                        <td className="tnum py-2 text-right text-muted">{c.weight_used_pct}%</td>
                        <td className={cn('tnum py-2 text-right font-semibold',
                          c.points > 0 ? 'text-up' : c.points < 0 ? 'text-down' : 'text-muted')}>
                          {c.points > 0 ? '+' : ''}{c.points}
                        </td>
                      </>
                    ) : (
                      <td colSpan={3} className="py-2 text-right text-faint">
                        unavailable — weight redistributed
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-faint">
              score = 50 + Σ points (conviction factor applied). Weights are yours
              to tune in Settings.
            </p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* the permanent honesty line — never hidden, never optional */}
      <p className="mt-3 border-t border-border pt-2.5 text-[11px] leading-relaxed text-faint">
        {s.disclaimer}
      </p>
    </SectionShell>
  )
}
