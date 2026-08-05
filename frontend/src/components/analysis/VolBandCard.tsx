// The hero visual: 30d price line with tomorrow's 80% range drawn as price
// lines, the per-ticker backtest coverage receipt, and the implied-move
// comparison (options market's range vs ours — S4).
import { createChart, LineSeries, type IChartApi } from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import { fmtMoney } from '@/lib/format'
import { GlossaryTip, SectionShell } from '@/components/ui'
import type { Analysis, GlossaryEntry } from '@/types'

const css = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()

export function VolBandCard({ a, glossary }: {
  a: Analysis
  glossary: Record<string, GlossaryEntry>
}) {
  const el = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  // P6: inside a 5-day earnings window the band's calibration doesn't hold —
  // draw it visibly weaker (dotted amber) instead of confidently green/red.
  const earningsWindow = !!a.earnings?.available
    && a.earnings.days_until != null && a.earnings.days_until <= 5

  useEffect(() => {
    if (!el.current || !a.chart.length) return
    const chart = createChart(el.current, {
      height: 220,
      layout: {
        background: { color: 'transparent' },
        textColor: css('--color-muted') || '#8b8b96',
        fontFamily: 'Inter, system-ui, sans-serif',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      handleScroll: false,
      handleScale: false,
    })
    chartRef.current = chart

    const line = chart.addSeries(LineSeries, {
      color: css('--color-accent') || '#22d3ee',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    })
    // defensive: strictly-ascending unique times or setData throws and takes
    // the whole card down — dedupe by date (last wins), sort ascending
    const points = [...new Map(a.chart.map((p) => [p.date, p.close]))]
      .sort(([a1], [b1]) => (a1 < b1 ? -1 : 1))
      .map(([date, close]) => ({ time: date, value: close }))
    line.setData(points)

    // Tomorrow's 80% range as labeled price lines off the last close.
    if (a.band) {
      const warn = css('--color-warn') || '#f5a623'
      const common = earningsWindow
        ? ({ lineStyle: 3, lineWidth: 1, axisLabelVisible: true } as const) // dotted
        : ({ lineStyle: 2, lineWidth: 1, axisLabelVisible: true } as const) // dashed
      line.createPriceLine({ ...common, price: a.band.high,
        color: earningsWindow ? warn : (css('--color-up') || '#00c853'),
        title: earningsWindow ? '80% high (earnings!)' : '80% high' })
      line.createPriceLine({ ...common, price: a.band.low,
        color: earningsWindow ? warn : (css('--color-down') || '#ff5252'),
        title: earningsWindow ? '80% low (earnings!)' : '80% low' })
    }
    chart.timeScale().fitContent()

    const onResize = () => {
      if (el.current) chart.applyOptions({ width: el.current.clientWidth })
    }
    onResize()
    const ro = new ResizeObserver(onResize)
    ro.observe(el.current)
    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [a.chart, a.band, earningsWindow])

  const cov = a.band_coverage
  const opts = a.options

  return (
    <SectionShell title="Price · 30 days + tomorrow's range"
      envelope={a.sections.history} glossary={glossary.expected_range}
      collapsible={false} /* imperative chart — stays open; everything else collapses */>
      <div ref={el} className="w-full" />
      {earningsWindow && (
        <p className="mt-2 rounded-lg bg-warn/10 px-3 py-2 text-xs leading-snug text-warn">
          Earnings in {a.earnings!.days_until}d — the 80% range is calibrated for
          normal days and should not be trusted through the print. Options
          implied move is the better gauge this week.
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border pt-3 text-xs text-muted">
        {a.band && (
          <span className="tnum">
            Range: <span className="text-text">{fmtMoney(a.band.low)} – {fmtMoney(a.band.high)}</span>
            {' '}(±{a.band.half_width_pct.toFixed(2)}%, 1.28σ of 20d realized vol)
          </span>
        )}
        {a.band && (
          <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint"
            title="Band engine — switchable in Settings, each option labeled with its validated coverage">
            engine: {a.band.engine}
          </span>
        )}
        {cov && (
          <span className="flex items-center gap-1">
            <span className="tnum">
              Backtest receipt: band held{' '}
              <span className="font-semibold text-text">{cov.covered_pct}%</span> of{' '}
              {cov.days.toLocaleString()} days
              {cov.scope === 'basket_overall' && ' (basket-wide — ticker not in backtest)'}
            </span>
            <GlossaryTip entry={glossary.band_coverage} />
          </span>
        )}
        {opts?.available && opts.implied_move_pct != null && a.band && (
          <span className="flex items-center gap-1">
            <span className="tnum">
              Options market implies <span className="font-semibold text-text">±{opts.implied_move_pct}%</span>
              {' '}through {opts.implied_move_dte}d —{' '}
              {opts.implied_move_pct > a.band.half_width_pct * 1.6
                ? 'pricing an event well beyond recent vol'
                : opts.implied_move_pct > a.band.half_width_pct
                  ? 'a bit above recent realized vol'
                  : 'in line with recent realized vol'}
            </span>
            <GlossaryTip entry={glossary.implied_move} />
          </span>
        )}
      </div>
    </SectionShell>
  )
}
