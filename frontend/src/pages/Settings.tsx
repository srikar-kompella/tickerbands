// Setup Score weight tuning (Q8b: sliders, live normalized shares, reset).
// Also surfaces provider health so a dead source is diagnosable at a glance.
import { RotateCcw, Save } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { fmtNum, fmtPct } from '@/lib/format'
import { Badge, Button, Card, CardTitle, SkeletonCard } from '@/components/ui'
import { useBandValidation, useHealth, useScoreAudit, useSettings } from '@/hooks'
import { cn } from '@/lib/utils'

const ENGINE_LABELS: Record<string, string> = {
  flat20: 'Flat 20-day (default — won the validation)',
  ewma: 'EWMA λ=0.94 (RiskMetrics)',
  conformal: 'Conformal (empirical quantile, 250d)',
}

/** Band engine picker — every option carries ITS measured backtest numbers. */
function BandEngineCard() {
  const { data: validation } = useBandValidation()
  const qc = useQueryClient()
  const [saving, setSaving] = useState<string | null>(null)
  const [current, setCurrent] = useState<string | null>(null)

  const pick = async (engine: string) => {
    setSaving(engine)
    try {
      await api.saveBandEngine(engine)
      setCurrent(engine)
      qc.invalidateQueries({ queryKey: ['analysis'] })
      toast.success(`Band engine: ${engine} — bands recompute on next analysis`)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(null)
    }
  }

  return (
    <Card>
      <CardTitle>Band engine</CardTitle>
      <div className="space-y-2">
        {Object.entries(ENGINE_LABELS).map(([key, label]) => {
          const v = validation?.overall?.[key]
          return (
            <button key={key} onClick={() => pick(key)} disabled={saving !== null}
              className={cn('flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                current === key ? 'border-accent/60 bg-accent/10' : 'border-border hover:bg-surface-2')}>
              <span>{label}</span>
              {v && (
                <span className="tnum text-xs text-muted">
                  {fmtPct(v.coverage_pct, 1)} coverage · ±{fmtNum(v.mean_halfwidth_pct, 2)}% avg width
                </span>
              )}
            </button>
          )
        })}
      </div>
      <p className="mt-3 border-t border-border pt-2 text-[11px] leading-relaxed text-faint">
        Numbers are from a {validation?.overall?.flat20?.days.toLocaleString() ?? '—'}-day
        point-in-time replay (target: 80% coverage; narrower width at the same
        coverage = better). Flat-20d won — EWMA over-covers wider, conformal is
        narrower but under target with wide per-ticker spread. The fashionable
        options lost to the boring one; that's why the numbers are shown.
      </p>
    </Card>
  )
}

/** Roadmap #10: does the tuned score correlate with anything? */
function ScoreAuditCard() {
  const { data: audit } = useScoreAudit()
  if (!audit) return null
  return (
    <Card>
      <CardTitle>Score honesty audit</CardTitle>
      {!audit.sufficient ? (
        <p className="py-1 text-sm text-muted">
          {audit.n_scored_days} scored day{audit.n_scored_days === 1 ? '' : 's'} with
          forward data so far ({audit.n_pending} pending a forward window) —
          needs ≥30 before the buckets mean anything. Keep using the app;
          this fills itself.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-faint">
              <th className="pb-2 font-medium">Score band</th>
              <th className="pb-2 text-right font-medium">Horizon</th>
              <th className="pb-2 text-right font-medium">n</th>
              <th className="pb-2 text-right font-medium">Mean fwd return</th>
              <th className="pb-2 text-right font-medium">% up</th>
            </tr>
          </thead>
          <tbody>
            {audit.buckets.map((b, i) => (
              <tr key={i} className="border-t border-border/60">
                <td className="py-1.5">{b.band}</td>
                <td className="tnum py-1.5 text-right text-muted">{b.horizon_days}d</td>
                <td className="tnum py-1.5 text-right text-muted">{b.n}</td>
                <td className={cn('tnum py-1.5 text-right font-medium',
                  b.mean_fwd_pct > 0 ? 'text-up' : b.mean_fwd_pct < 0 ? 'text-down' : '')}>
                  {fmtPct(b.mean_fwd_pct, 2, true)}
                </td>
                <td className="tnum py-1.5 text-right text-muted">{fmtPct(b.hit_rate_up, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-3 border-t border-border pt-2 text-[11px] leading-relaxed text-faint">
        {audit.note}
      </p>
    </Card>
  )
}

const LABELS: Record<string, string> = {
  momentum: 'Momentum alignment',
  news_sentiment: 'News sentiment',
  options_positioning: 'Options positioning',
  analyst_trend: 'Analyst rec. trend',
  social_buzz: 'Social buzz',
}

export default function Settings() {
  const { data: settings, isLoading } = useSettings()
  const { data: health } = useHealth()
  const qc = useQueryClient()
  const [weights, setWeights] = useState<Record<string, number> | null>(null)

  useEffect(() => {
    if (settings && weights === null) setWeights(settings.weights)
  }, [settings, weights])

  const save = useMutation({
    mutationFn: (w: Record<string, number>) => api.saveWeights(w),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['analysis'] })  // scores recompute live
      toast.success('Weights saved — scores recompute on next analysis')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (isLoading || !settings || !weights) return <SkeletonCard lines={6} />

  const total = Object.values(weights).reduce((a, b) => a + b, 0)
  const dirty = JSON.stringify(weights) !== JSON.stringify(settings.weights)

  return (
    <div className="max-w-2xl space-y-4">
      <Card>
        <CardTitle right={
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setWeights(settings.defaults)} title="Reset to defaults">
              <RotateCcw className="h-4 w-4" /> Defaults
            </Button>
            <Button variant="accent" disabled={!dirty || total <= 0}
              onClick={() => save.mutate(weights)}>
              <Save className="h-4 w-4" /> Save
            </Button>
          </div>
        }>
          Setup Score weights
        </CardTitle>
        <div className="space-y-4">
          {Object.keys(LABELS).map((key) => {
            const v = weights[key] ?? 0
            const share = total > 0 ? (v / total) * 100 : 0
            return (
              <div key={key}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span>{LABELS[key]}</span>
                  <span className="tnum text-muted">
                    {v.toFixed(1)} <span className="text-faint">→ {share.toFixed(0)}% of score</span>
                  </span>
                </div>
                <input
                  type="range" min={0} max={50} step={2.5} value={v}
                  aria-label={`${LABELS[key]} weight`}
                  onChange={(e) => setWeights({ ...weights, [key]: Number(e.target.value) })}
                  className="w-full accent-[var(--color-accent)]"
                />
              </div>
            )
          })}
        </div>
        <p className="mt-4 border-t border-border pt-3 text-[11px] leading-relaxed text-faint">
          Weights renormalize over the signals actually available for a ticker
          (an ETF without analyst coverage doesn't read as bearish — Q8c). The
          volatility regime is a conviction multiplier, not a weight — it scales
          the whole tilt. These defaults are NOT empirically calibrated; they're
          a starting point that's yours to tune. {settings.disclaimer}
        </p>
      </Card>

      <Card>
        <CardTitle>Data sources</CardTitle>
        <div className="flex flex-wrap gap-2">
          {health && Object.entries(health.providers).map(([name, ok]) => (
            <Badge key={name} tone={ok ? 'up' : 'down'}>
              <span className={cn('h-1.5 w-1.5 rounded-full', ok ? 'bg-up' : 'bg-down')} />
              {name}{!ok && ' — check auth/availability'}
            </Badge>
          ))}
          {health?.mock_mode && <Badge tone="warn">MOCK MODE — deterministic fake data</Badge>}
        </div>
        <p className="mt-3 text-[11px] text-faint">
          Schwab token re-auth is weekly (`schwab-reauth`). Finnhub key lives in
          the macOS Keychain. StockTwits needs no auth. Portfolio DB is opened
          read-only.
        </p>
      </Card>

      <BandEngineCard />

      <ScoreAuditCard />

      <p className="text-[11px] text-faint">
        Looking for the glossary and how-it-works? They moved to the
        <span className="text-muted"> Guide</span> tab.
      </p>
    </div>
  )
}
