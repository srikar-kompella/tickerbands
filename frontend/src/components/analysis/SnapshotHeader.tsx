// Sticky ticker header: animated price (Robinhood-style NumberFlow), day
// change, the 80% range headline, today's-move z-score, earnings countdown.
// Stays pinned while the user scrolls the section grid.
import NumberFlow from '@number-flow/react'
import { CalendarClock, Eye, EyeOff, FileDown, MessageSquarePlus, Moon, RefreshCw } from 'lucide-react'
import { fmtMoney, fmtPct, plColor } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, Button, Stat } from '@/components/ui'
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

  // One price basis, same as the report (2026-11-21 audit): the big number is
  // the live regular-session price when the market is open, otherwise the
  // official close of the last session. An extended-hours trade is a labeled
  // second line (toggle shows/hides it) — never the headline number. (The
  // old header showed a stale pre-market print, $144.12 +14.81%, as NVDA's
  // price on a Saturday.)
  const pb = a.price_basis
  const rvf = a.report_view?.fields ?? {}
  const ah = pb?.after_hours ?? null
  const showAH = ahOn && !!ah
  const price = pb?.price ?? q?.regular_last ?? q?.last ?? 0
  const chg = pb ? pb.day_move_pct : (q?.regular_change_pct ?? q?.net_change_pct ?? null)
  const sessionWord = pb?.move_word && pb.move_word !== 'today' ? `${pb.move_word} session` : 'today'
  const earnTag = rvf.earnings_next?.tag

  // Research stance tooltip: the note, its date, then what would change it
  // (prompt v5: upgrade/downgrade conditions, review date, confidence) — the
  // same lines the report prints under the stance.
  const sf = rvf.claude_stance
  const stanceTip = a.claude_stance ? [
    a.claude_stance.stance_note ?? '',
    a.claude_stance.created_ts.slice(0, 10)
      + (a.claude_stance.drift_material ? ' — price has moved beyond the expected range since this call' : ''),
    sf?.upgrade_if?.length ? `Upgrade if: ${sf.upgrade_if.join('; ')}` : '',
    sf?.downgrade_if?.length ? `Downgrade if: ${sf.downgrade_if.join('; ')}` : '',
    [sf?.review_by ? `review by ${sf.review_by}` : '', sf?.confidence ? `confidence ${sf.confidence}` : '']
      .filter(Boolean).join(' · '),
  ].filter(Boolean).join('\n') : ''

  // A stale quote wearing a live face is the worst lie this header can tell —
  // surface it where the eye lands, not in a corner chip.
  const staleEnv = ['quote', 'history']
    .map((k) => a.sections?.[k])
    .find((e) => e && ['stale', 'unavailable'].includes(e.status))
  const staleAgeH = staleEnv?.age_seconds ? (staleEnv.age_seconds / 3600).toFixed(1) : null

  // What the page's figures are as of: the price line the report prints, with
  // the statement and news dates one hover away.
  const dao = a.report_view?.data_as_of
  const asOfTip = [
    dao?.statements ? `Statements: ${dao.statements}` : '',
    dao?.news ? `News: ${dao.news}` : '',
  ].filter(Boolean).join('\n')

  // Two fixed rows instead of one wrapping row: identity + price + actions,
  // then the key facts. Stays under ~100px tall so the pinned header never
  // eats the page. No layout animation — a sticky bar must not move.
  return (
    <div className="sticky top-0 z-30 -mx-6 border-b border-border bg-bg/90 px-6 py-2.5 backdrop-blur-md"
      title={q?.name ? `${a.symbol} — ${q.name}` : undefined}>
      {staleEnv && (
        <div className="mb-2 flex flex-wrap items-center gap-x-2 rounded-lg bg-warn/10 px-3 py-1.5 text-xs text-warn">
          <span className="font-semibold">PRICES ARE STALE</span>
          <span>
            provider failing ({staleEnv.error_reason ?? 'error'})
            {staleAgeH ? ` — showing data from ${staleAgeH}h ago` : ''}
            {staleEnv.error_reason === 'auth_expired' &&
              ' · fix: backend/.venv/bin/python backend/schwab_reauth.py'}
          </span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2.5">
          <h2 className="text-xl font-bold tracking-tight">{a.symbol}</h2>
          <span className="tnum text-xl font-bold">
            <NumberFlow
              value={price}
              format={{ style: 'currency', currency: 'USD' }}
              transformTiming={{ duration: 500, easing: 'ease-out' }}
            />
          </span>
          <span className={cn('tnum text-sm font-semibold', plColor(chg))}>
            {fmtPct(chg, 2, true)}
          </span>
          <span className="text-xs text-faint">{sessionWord}</span>
          {a.ex_div?.today && (
            a.ex_div.amount != null ? (
              <Badge tone="warn" className="self-center text-[11px]"
                title="The distribution comes out of the price on the ex-date. Day move, σ and the expected-range band are computed against the dividend-adjusted prior close — without this, every ex-date looks like a fake sell-off.">
                ex-div ${a.ex_div.amount} · div-adjusted
              </Badge>
            ) : (
              <Badge tone="warn" className="self-center text-[11px]"
                title="Today is an ex-dividend date but the amount isn't published in the data feeds yet, so moves are NOT adjusted — a red day-move here is partly or wholly the distribution, not a sell-off.">
                ex-div today · amount pending — move not adjusted
              </Badge>
            )
          )}
          {/* the name shows at every width — truncated, full text on hover; when
              the row is tight it wraps under the symbol instead of disappearing */}
          {q?.name && (
            <span className="min-w-0 max-w-full truncate text-xs text-faint @4xl:max-w-64" title={q.name}>{q.name}</span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
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
            className="px-2.5"
            title={watched ? 'Watching — click to remove from watchlist' : 'Add to watchlist'}
            ariaLabel={watched ? 'Remove from watchlist' : 'Add to watchlist'}>
            {watched ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            <span className="hidden @6xl:inline">{watched ? 'Watching ✓' : 'Watch'}</span>
          </Button>
          <Button variant="accent" onClick={onAskClaude}>
            <MessageSquarePlus className="h-4 w-4" /> Ask Claude
          </Button>
          <Button
            className="px-2.5" ariaLabel="Export report"
            onClick={() => window.open(`/api/analysis/${a.symbol}/report`, '_blank')}
            title="Export the full research snapshot — analysis + Claude discussions — as a printable PDF">
            <FileDown className="h-4 w-4" />
            <span className="hidden @6xl:inline">Export</span>
          </Button>
          <Button onClick={onRefresh} disabled={refreshing} className="px-2.5"
            title="Force-refresh all sections" ariaLabel="Refresh all sections">
            <RefreshCw className={cn('h-4 w-4', refreshing && 'animate-spin')} />
            <span className="hidden @6xl:inline">{refreshing ? 'Refreshing…' : 'Refresh'}</span>
          </Button>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-start gap-x-6 gap-y-2">
        {a.band && (
          <Stat tip={glossary.expected_range}
            label={`80% range · ${pb?.price_kind === 'live' ? "today's session" : 'next session'}`}>
            <span className="tnum font-medium">
              {fmtMoney(a.band.low)} – {fmtMoney(a.band.high)}
              <span className="ml-1.5 text-xs font-normal text-muted">±{a.band.half_width_pct.toFixed(2)}%</span>
            </span>
          </Stat>
        )}

        {a.move && (
          <Stat tip={glossary.move_z}
            label={pb?.move_word && pb.move_word !== 'today' ? `move · ${pb.move_word}` : "today's move"}>
            <Badge tone={a.move.outside_band ? 'warn' : 'neutral'} className="tnum">
              {a.move.z >= 0 ? '+' : ''}{a.move.z.toFixed(2)}σ
              {a.move.outside_band && ' · outside band'}
            </Badge>
          </Stat>
        )}

        {a.earnings?.available && a.earnings.days_until != null && (
          <Stat label="earnings" tip={glossary.earnings_countdown}>
            <Badge tone={a.earnings.days_until <= 5 ? 'warn' : 'neutral'}>
              <CalendarClock className="h-3 w-3" />
              {a.earnings.days_until === 0 ? 'today' : `in ${a.earnings.days_until}d`}
              {earnTag && <span className="ml-1 text-[11px] opacity-75">{earnTag === 'confirmed' ? 'confirmed' : 'est.'}</span>}
            </Badge>
          </Stat>
        )}

        {/* Research stance: surfaced from the latest Claude discussion — the
            app never computes buy/sell/hold itself (signals can't call
            direction; a reasoned session judgment is a different animal). */}
        <Stat label="claude stance" tip={glossary.research_stance}>
          {a.claude_stance ? (
            <span className="inline-flex flex-wrap items-center gap-1.5"
              title={stanceTip}>
              <Badge tone={
                a.claude_stance.stance === 'buy' ? 'up'
                  : a.claude_stance.stance === 'sell' || a.claude_stance.stance === 'trim' ? 'down'
                    : 'accent'}>
                {/* sell on a name the user does not own means avoid (prompt v5);
                    the backend decides — the note's `owned`, else the account */}
                {a.claude_stance.label ?? a.claude_stance.stance.toUpperCase()}
                {a.claude_stance.stance_horizon && ` · ${a.claude_stance.stance_horizon}`}
              </Badge>
              {(rvf.claude_stance?.warnings?.length ?? 0) > 0 && (
                <Badge tone="warn" className="text-[11px]"
                  title={rvf.claude_stance!.warnings.join('\n')}>
                  ⚠ re-check
                </Badge>
              )}
              <span className="text-[11px] text-faint tnum">
                {a.claude_stance.created_ts.slice(5, 10)}
                {a.claude_stance.drift_material && a.claude_stance.drift_pct != null &&
                  ` · price ${a.claude_stance.drift_pct > 0 ? '+' : ''}${a.claude_stance.drift_pct}% since`}
              </span>
              {a.claude_stance.stance === 'buy' && a.bottom_context && (
                <Badge tone={a.bottom_context.bottom_decile ? 'accent' : 'warn'}
                  className="text-[11px] tnum"
                  title={`Where this buy sits: ${a.bottom_context.drawdown_pct}% off the 52w high, ${a.bottom_context.above_52w_low_pct}% above the low, cheaper than ${100 - a.bottom_context.range_percentile}% of the last year's closes. ${a.bottom_context.insider_buys_into_drawdown ? 'Insiders are buying this drawdown. ' : ''}${a.bottom_context.note}`}>
                  {a.bottom_context.bottom_decile ? 'near yearly lows'
                    : a.bottom_context.range_percentile >= 70 ? 'buying strength'
                      : 'mid-range'}
                </Badge>
              )}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5">
              <button onClick={onAskClaude}
                className="text-xs text-faint underline decoration-dotted underline-offset-2 hover:text-accent"
                title="No stance yet — run a Claude discussion and its reasoned call will appear here">
                none yet — ask Claude
              </button>
              {/* e.g. the newest note's stance could not be read */}
              {(rvf.claude_stance?.warnings?.length ?? 0) > 0 && (
                <Badge tone="warn" className="text-[11px]"
                  title={rvf.claude_stance!.warnings.join('\n')}>
                  ⚠ re-check
                </Badge>
              )}
            </span>
          )}
        </Stat>

        {/* data as-of: the one price basis, then the extended-hours trade as
            a labeled second line — never the headline number */}
        {(pb?.price_label || (showAH && ah)) && (
          <div title={asOfTip || undefined}
            className="flex min-w-0 basis-full flex-wrap items-center gap-x-4 text-[11px] leading-5 @4xl:ml-auto @4xl:block @4xl:basis-auto @4xl:text-right">
            {pb?.price_label && <div className="text-faint">{pb.price_label}</div>}
            {showAH && ah && (
              <div className="tnum flex items-center gap-1 text-muted @4xl:justify-end"
                title="Extended-hours trade — shown for information; nothing on this page is computed from it">
                <Moon className="h-2.5 w-2.5" /> {fmtMoney(ah.price)} ({fmtPct(ah.change_pct, 2, true)}) · {ah.label}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
