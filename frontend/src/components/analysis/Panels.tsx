// The section panels. Each: data in → compact honest display, wrapped in
// SectionShell (freshness badge + Q12 degradation) with glossary tooltips.
import { ExternalLink, Wallet } from 'lucide-react'
import { fmtCompact, fmtDate, fmtMarketCap, fmtMoney, fmtNum, fmtPct, plColor } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, GlossaryTip, SectionShell } from '@/components/ui'
import type { Analysis, GlossaryEntry } from '@/types'

type G = Record<string, GlossaryEntry>

// ─── shared row helper ─────────────────────────────────────────────────────────

function Row({ label, value, tip, valueClass }: {
  label: string; value: string; tip?: GlossaryEntry; valueClass?: string
}) {
  return (
    <div className="flex items-center justify-between py-1 text-sm">
      <span className="flex items-center gap-1 text-muted">{label}{tip && <GlossaryTip entry={tip} />}</span>
      <span className={cn('tnum font-medium', valueClass)}>{value}</span>
    </div>
  )
}

// ─── Fundamentals ──────────────────────────────────────────────────────────────

export function FundamentalsPanel({ a, g }: { a: Analysis; g: G }) {
  const f = a.fundamentals
  if (a.sections.fundamentals?.status !== 'unavailable' && f && !f.available) {
    // Finnhub knows the symbol but has no metrics (many ETFs) — honest hide.
    return (
      <SectionShell title="Fundamentals" envelope={a.sections.fundamentals} glossary={g.asset_type}>
        <p className="py-2 text-sm text-faint">No fundamentals published for this instrument (normal for ETFs/trusts).</p>
      </SectionShell>
    )
  }
  // BUG A fix: P/E and yield come from the TTM-computed block (price ÷ TTM
  // EPS; SUM of trailing-12mo distributions ÷ price) with source + as-of and
  // divergence warnings — vendor metrics alone rendered ECO at 40x / 0.8%
  // when the real trailing figures were ~6.4x / ~13%.
  const t = a.fundamentals_ttm
  const warns = [t?.pe_ttm?.warning, t?.yield_ttm?.warning].filter(Boolean) as string[]
  return (
    <SectionShell title="Fundamentals" envelope={a.sections.fundamentals} glossary={g.pe_ttm}>
      {f && (
        <div className="divide-y divide-border/60">
          <Row label="P/E (TTM)"
            value={t?.pe_ttm?.value != null ? fmtNum(t.pe_ttm.value, 1) : '—'}
            tip={g.pe_ttm} />
          <Row label="EPS (TTM)" value={fmtMoney(f.eps_ttm)} tip={g.eps_ttm} />
          <Row label="Market cap" value={fmtMarketCap(f.market_cap_m)} tip={g.market_cap} />
          <Row label="Revenue growth" value={fmtPct(f.revenue_growth_ttm_pct, 1, true)}
            tip={g.revenue_growth} valueClass={plColor(f.revenue_growth_ttm_pct)} />
          <Row label="Gross margin" value={fmtPct(f.gross_margin_pct, 1)} tip={g.gross_margin} />
          <Row label="Operating margin" value={fmtPct(f.operating_margin_pct, 1)} tip={g.operating_margin} />
          <Row label="Net margin" value={fmtPct(f.net_margin_pct, 1)} tip={g.net_margin} />
          <Row label="Dividend yield (TTM)"
            value={t?.yield_ttm?.value != null ? fmtPct(t.yield_ttm.value, 2) : '—'}
            tip={g.dividend_yield} />
        </div>
      )}
      {t && (
        <p className="mt-2 text-[10px] leading-relaxed text-faint">
          P/E: {t.pe_ttm.source}{t.pe_ttm.as_of ? ` · as of ${t.pe_ttm.as_of}` : ''}
          <br />
          Yield: {t.yield_ttm.source}{t.yield_ttm.as_of ? ` · as of ${t.yield_ttm.as_of}` : ''}
        </p>
      )}
      {warns.map((w, i) => (
        <p key={i} className="mt-1.5 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-2.5 py-1.5 text-[11px] text-yellow-500">
          ⚠ {w}
        </p>
      ))}
    </SectionShell>
  )
}

// ─── News sentiment + headlines ────────────────────────────────────────────────

export function NewsPanel({ a, g }: { a: Analysis; g: G }) {
  const n = a.news_sentiment
  const cn2 = a.claude_news
  const bull = n?.available ? Math.round((n.bullish_pct ?? 0) * 100) : null
  const bear = n?.available ? Math.round((n.bearish_pct ?? 0) * 100) : null
  const lexicon = n?.method === 'headline-lexicon'
  return (
    <SectionShell title="News" envelope={a.sections.news_sentiment} glossary={g.news_sentiment}>
      {n?.available ? (
        <>
          <div className="mb-1 flex items-center justify-between gap-2 text-xs">
            <span className="font-medium text-up">{bull}% bullish</span>
            <span className="text-muted tnum">
              {n.articles_week} articles
              {n.buzz != null && ` · buzz ${fmtNum(n.buzz, 1)}×`}
            </span>
            <span className="font-medium text-down">{bear}% bearish</span>
          </div>
          {/* sentiment split bar */}
          <div className="flex h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div className="bg-up" style={{ width: `${bull}%` }} />
            <div className="ml-auto bg-down" style={{ width: `${bear}%` }} />
          </div>
          <div className="mt-1.5">
            <Badge className="text-[10px]" title={lexicon
              ? 'Scored from the free headlines with a transparent wordlist (Finnhub aggregate sentiment unavailable on this tier)'
              : 'Finnhub aggregated news sentiment'}>
              {lexicon ? 'headline-scored' : 'finnhub'}
            </Badge>
          </div>
        </>
      ) : (
        <p className="py-1 text-sm text-faint">No news sentiment for this instrument.</p>
      )}

      {/* Claude's live news check (from discussion frontmatter, ask #3) */}
      {cn2 && (
        <div className="mt-2.5 rounded-lg border border-accent/25 bg-accent/5 px-2.5 py-2">
          <div className="flex items-center gap-2 text-xs">
            <Badge tone={cn2.news_view === 'bullish' ? 'up' : cn2.news_view === 'bearish' ? 'down' : 'neutral'}>
              Claude: {cn2.news_view}
            </Badge>
            <span className="text-faint">{fmtDate(cn2.created_ts)}</span>
          </div>
          {cn2.news_note && <p className="mt-1 text-xs text-muted">{cn2.news_note}</p>}
        </div>
      )}

      {!!a.headlines?.length && (
        <ul className="mt-3 space-y-2 border-t border-border pt-3">
          {a.headlines.map((h, i) => {
            const tone = n?.per_headline?.[i]
            return (
              <li key={i} className="text-sm leading-snug">
                <a href={h.url} target="_blank" rel="noreferrer"
                  className="group flex items-start gap-1.5 text-text hover:text-accent">
                  {tone != null && tone !== 0 && (
                    <span className={tone > 0 ? 'mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-up' : 'mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-down'} />
                  )}
                  <span className="min-w-0 flex-1 truncate">{h.headline}</span>
                  <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-faint group-hover:text-accent" />
                </a>
                <span className="text-[11px] text-faint">{h.source} · {fmtDate(new Date(h.ts * 1000).toISOString())}</span>
              </li>
            )
          })}
        </ul>
      )}
    </SectionShell>
  )
}

// ─── Social (StockTwits) ───────────────────────────────────────────────────────

export function SocialPanel({ a, g }: { a: Analysis; g: G }) {
  const s = a.social
  return (
    <SectionShell title="Social · StockTwits" envelope={a.sections.social} glossary={g.social_buzz}>
      {s?.available ? (
        <>
          <div className="flex items-center gap-3">
            <Badge tone={(s.polarity ?? 0) > 0.15 ? 'up' : (s.polarity ?? 0) < -0.15 ? 'down' : 'neutral'}>
              polarity {(s.polarity ?? 0) >= 0 ? '+' : ''}{fmtNum(s.polarity, 2)}
            </Badge>
            <span className="tnum text-xs text-muted">
              {s.bullish} bull / {s.bearish} bear of {s.total_msgs} recent msgs
            </span>
          </div>
          {(s.tagged ?? 0) < 5 && (
            <p className="mt-2 text-[11px] text-warn">
              Thin sample ({s.tagged} tagged) — treat as anecdote, not signal.
            </p>
          )}
          {!!s.sample?.length && (
            <p className="mt-2 border-t border-border pt-2 text-xs italic text-faint">
              “{s.sample[0].body}”
            </p>
          )}
        </>
      ) : (
        <p className="py-1 text-sm text-faint">No StockTwits stream.</p>
      )}
    </SectionShell>
  )
}

// ─── Analyst recommendation trend (Q4: trends only, no targets) ────────────────

export function AnalystPanel({ a, g }: { a: Analysis; g: G }) {
  const months = a.recommendations?.available ? a.recommendations.months ?? [] : []
  const segs = [
    ['strong_buy', 'bg-up'], ['buy', 'bg-up/50'], ['hold', 'bg-warn/60'],
    ['sell', 'bg-down/50'], ['strong_sell', 'bg-down'],
  ] as const
  return (
    <SectionShell title="Analyst recs" envelope={a.sections.recommendations} glossary={g.analyst_trend}>
      {months.length ? (
        <div className="space-y-2.5">
          {months.slice(0, 2).map((m, i) => (
            <div key={m.period}>
              <div className="mb-1 flex justify-between text-[11px] text-muted">
                <span>{i === 0 ? 'This month' : 'Last month'} <span className="text-faint">({m.period})</span></span>
                <span className="tnum">{m.total} analysts</span>
              </div>
              <div className="flex h-2.5 overflow-hidden rounded-full bg-surface-3">
                {segs.map(([key, color]) => {
                  const v = m[key]
                  return v > 0 ? (
                    <div key={key} className={color}
                      style={{ width: `${(v / m.total) * 100}%` }}
                      title={`${key.replace('_', ' ')}: ${v}`} />
                  ) : null
                })}
              </div>
            </div>
          ))}
          <p className="text-[11px] text-faint">
            Shifts in the mix matter; levels are mostly priced in. Price targets
            deliberately not shown (no free source; levels are stale anyway).
          </p>
        </div>
      ) : (
        <p className="py-1 text-sm text-faint">No analyst coverage (normal for ETFs).</p>
      )}
    </SectionShell>
  )
}

// ─── Options positioning ───────────────────────────────────────────────────────

export function OptionsPanel({ a, g }: { a: Analysis; g: G }) {
  const o = a.options
  if (o && !o.available) return null // no listed options → section hides entirely (Q12)
  const pc = o?.put_call_ratio
  return (
    <SectionShell title="Options positioning" envelope={a.sections.options} glossary={g.options_positioning}>
      {o?.available && (
        <div className="divide-y divide-border/60">
          <Row label="Put/Call (today)" tip={g.options_positioning}
            value={pc == null ? '— (thin volume)' : fmtNum(pc, 2)}
            valueClass={pc == null ? 'text-faint' : pc < 0.7 ? 'text-up' : pc > 1.1 ? 'text-down' : undefined} />
          <Row label="Call / Put volume" value={`${fmtCompact(o.call_volume)} / ${fmtCompact(o.put_volume)}`} />
          <Row label="ATM implied vol" value={o.atm_iv_pct != null ? `${fmtNum(o.atm_iv_pct, 1)}%` : '—'} tip={g.atm_iv} />
          <Row label={`Implied move (${o.implied_move_dte ?? '—'}d)`}
            value={o.implied_move_pct != null ? `±${o.implied_move_pct}%` : '—'} tip={g.implied_move} />
        </div>
      )}
    </SectionShell>
  )
}

// ─── Earnings (S1 — the band's bodyguard) ──────────────────────────────────────

export function EarningsPanel({ a, g }: { a: Analysis; g: G }) {
  const e = a.earnings
  return (
    <SectionShell title="Earnings" envelope={a.sections.earnings} glossary={g.earnings_countdown}>
      {e?.available ? (
        <>
          <div className="flex items-baseline justify-between">
            <span className="text-sm text-muted">Next report</span>
            <span className="tnum text-sm font-semibold">
              {fmtDate(e.next_date)}{e.days_until != null && ` · ${e.days_until}d`}
            </span>
          </div>
          {e.days_until != null && e.days_until <= 5 && (
            <p className="mt-1.5 rounded-lg bg-warn/10 px-2.5 py-1.5 text-[11px] leading-snug text-warn">
              Earnings window: the 80% range is calibrated for normal days and
              is unreliable through the print.
            </p>
          )}
          {/* roadmap #3: what THIS stock actually does on earnings days,
              vs what options are pricing for the next one */}
          {a.earnings_move && (a.earnings_move.quarters > 0 || a.earnings_move.implied_move_pct != null) && (
            <div className="mt-2.5 border-t border-border pt-2">
              <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wide text-faint">
                expected earnings move <GlossaryTip entry={g.earnings_move} />
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="tnum">options: <b>{a.earnings_move.implied_move_pct != null ? `±${a.earnings_move.implied_move_pct}%` : '—'}</b></span>
                <span className="tnum text-muted">
                  history: ±{fmtNum(a.earnings_move.hist_median_abs_pct, 1)}% med
                  ({a.earnings_move.quarters}q, max ±{fmtNum(a.earnings_move.hist_max_abs_pct, 1)}%)
                </span>
                {a.earnings_move.implied_vs_hist != null && (
                  <Badge tone={a.earnings_move.implied_vs_hist > 1.3 ? 'warn'
                    : a.earnings_move.implied_vs_hist < 0.8 ? 'accent' : 'neutral'}>
                    {fmtNum(a.earnings_move.implied_vs_hist, 2)}× its own history
                  </Badge>
                )}
              </div>
            </div>
          )}
          {/* roadmap #7: PEAD context */}
          {a.pead?.active && (
            <p className="mt-2 flex items-start gap-1 rounded-lg bg-accent/5 px-2.5 py-1.5 text-[11px] leading-snug text-muted">
              <span>
                <b className={a.pead.direction === 'positive' ? 'text-up' : 'text-down'}>
                  {a.pead.direction === 'positive' ? 'Beat' : 'Missed'} by {Math.abs(a.pead.surprise_pct)}%
                </b>{' '}
                {a.pead.trading_days_since}d ago — historically prices drift in the
                surprise's direction ~{a.pead.window_days} trading days (PEAD).
                Documented tendency, not a promise.
              </span>
              <GlossaryTip entry={g.pead} />
            </p>
          )}
          {!!e.surprises.length && (
            <div className="mt-2.5 border-t border-border pt-2">
              <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wide text-faint">
                surprise history <GlossaryTip entry={g.earnings_surprise} />
              </div>
              <div className="flex gap-1.5">
                {e.surprises.map((s2, i) => (
                  <div key={i}
                    className={cn('flex-1 rounded-md px-1.5 py-1 text-center',
                      (s2.surprise_pct ?? 0) >= 0 ? 'bg-up/10 text-up' : 'bg-down/10 text-down')}>
                    <div className="tnum text-xs font-semibold">{fmtPct(s2.surprise_pct, 1, true)}</div>
                    <div className="text-[9px] opacity-70">{s2.period?.slice(0, 7)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="py-1 text-sm text-faint">No earnings data (normal for ETFs).</p>
      )}
    </SectionShell>
  )
}

// ─── Insiders (SEC EDGAR — open-market trades, cluster detection) ──────────────

export function InsidersPanel({ a, g }: { a: Analysis; g: G }) {
  const ins = a.insiders
  return (
    <SectionShell title="Insiders · SEC Form 4" envelope={a.sections.insiders} glossary={g.insiders_cluster}>
      {ins?.available ? (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {ins.cluster_buy && (
              <Badge tone="up" title={`Multiple insiders buying within 2 weeks: ${(ins.cluster_buyers ?? []).join(', ')}`}>
                CLUSTER BUY
              </Badge>
            )}
            <Badge tone={ins.net_shares_90d > 0 ? 'up' : ins.net_shares_90d < 0 ? 'down' : 'neutral'}>
              net {ins.net_shares_90d > 0 ? '+' : ''}{fmtCompact(ins.net_shares_90d)} sh / 90d
            </Badge>
          </div>
          {ins.transactions.length ? (
            <div className="divide-y divide-border/60">
              {ins.transactions.slice(0, 5).map((t, i) => (
                <div key={i} className="flex items-baseline justify-between gap-2 py-1.5 text-sm">
                  <span className="min-w-0 truncate">
                    <span className={t.code === 'P' ? 'font-semibold text-up' : 'font-semibold text-down'}>
                      {t.code === 'P' ? 'BUY' : 'SELL'}
                    </span>{' '}
                    {t.owner} <span className="text-faint">({t.title})</span>
                  </span>
                  <span className="tnum shrink-0 text-xs text-muted">
                    {fmtCompact(t.shares)} sh · {fmtMoney(t.value, 0)} · {fmtDate(t.date)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-1 text-sm text-faint">No open-market insider trades in recent filings.</p>
          )}
          <p className="mt-2 border-t border-border pt-2 text-[10px] leading-snug text-faint">{ins.note}</p>
        </>
      ) : (
        <p className="py-1 text-sm text-faint">No Form 4 data (ETFs/funds don't file).</p>
      )}
    </SectionShell>
  )
}

// ─── Short interest (FINRA bi-monthly) ─────────────────────────────────────────

export function ShortInterestPanel({ a, g }: { a: Analysis; g: G }) {
  const si = a.short_interest
  return (
    <SectionShell title="Short interest · FINRA" envelope={a.sections.short_interest} glossary={g.short_interest}>
      {si?.available ? (
        <div className="divide-y divide-border/60">
          <Row label="Short interest" value={fmtCompact(si.short_interest)} tip={g.short_interest} />
          <Row label="Δ vs prior period" value={fmtPct(si.change_pct, 1, true)}
            valueClass={plColor(si.change_pct != null ? -si.change_pct : null)} />
          <Row label="Days to cover" value={fmtNum(si.days_to_cover, 1)} tip={g.days_to_cover}
            valueClass={(si.days_to_cover ?? 0) >= 5 ? 'text-warn font-semibold' : undefined} />
          <Row label="% of shares outstanding" value={fmtPct(si.pct_of_shares_out, 2)} />
          <p className="pt-1.5 text-[10px] text-faint">
            Settlement {fmtDate(si.settlement_date)} · reported bi-monthly, so this
            lags by days-to-weeks · % uses shares OUTSTANDING (free float isn't free data)
          </p>
        </div>
      ) : (
        <p className="py-1 text-sm text-faint">No short interest data for this instrument.</p>
      )}
    </SectionShell>
  )
}

// ─── Ownership chip (Q3 — read-only from the Portfolio app's DB) ───────────────

export function OwnershipPanel({ a, g }: { a: Analysis; g: G }) {
  const p = a.position
  return (
    <SectionShell title="Your position" envelope={a.sections.position} glossary={g.ownership}>
      {p?.owned ? (
        <div className="divide-y divide-border/60">
          <Row label="Shares" value={`${fmtNum(p.qty, 0)} @ ${fmtMoney(p.avg_cost)}`} />
          <Row label="Market value" value={fmtMoney(p.market_value)} />
          <Row label="Gain" value={`${fmtMoney(p.gain)} (${fmtPct(p.gain_pct, 1, true)})`}
            valueClass={plColor(p.gain)} />
          <Row label="Portfolio weight" value={fmtPct(p.weight_pct, 1)} />
          <p className="pt-1.5 text-[10px] text-faint">
            <Wallet className="mr-1 inline h-3 w-3" />
            read-only from the Portfolio app · as of {fmtDate(p.as_of)}
          </p>
        </div>
      ) : (
        <p className="py-1 text-sm text-faint">You don't hold this in the tracked Schwab account.</p>
      )}
    </SectionShell>
  )
}

// ─── Context stats (S5: beta/corr, 52w position, volume z) ─────────────────────

export function ContextPanel({ a, g }: { a: Analysis; g: G }) {
  return (
    <SectionShell title="Context" envelope={a.sections.history} glossary={g.beta}>
      <div className="divide-y divide-border/60">
        <Row label="Beta vs SPY (60d)" value={fmtNum(a.beta?.beta, 2)} tip={g.beta} />
        <Row label="Correlation to SPY" value={fmtNum(a.beta?.correlation, 2)} tip={g.correlation} />
        {a.week52 && (
          <div className="py-2">
            <div className="mb-1 flex items-center justify-between text-sm">
              <span className="flex items-center gap-1 text-muted">52-week range <GlossaryTip entry={g.week52_position} /></span>
              <span className="tnum text-xs text-muted">{fmtNum(a.week52.position_pct, 0)}%</span>
            </div>
            <div className="relative h-1.5 rounded-full bg-surface-3">
              <div className="absolute top-1/2 h-3 w-1 -translate-y-1/2 rounded-full bg-accent"
                style={{ left: `calc(${Math.min(100, Math.max(0, a.week52.position_pct))}% - 2px)` }} />
            </div>
            <div className="mt-1 flex justify-between text-[10px] text-faint tnum">
              <span>{fmtMoney(a.week52.low)}</span><span>{fmtMoney(a.week52.high)}</span>
            </div>
          </div>
        )}
        <Row label="Volume z-score (prev. session)" value={fmtNum(a.signals?.volume_z, 2)}
          tip={g.volume_z}
          valueClass={Math.abs(a.signals?.volume_z ?? 0) > 2 ? 'text-warn' : undefined} />
        {a.bottom_context && (
          <>
            <Row label="Drawdown from 52w high" value={fmtPct(a.bottom_context.drawdown_pct, 1)}
              tip={g.bottom_context}
              valueClass={a.bottom_context.drawdown_pct < -25 ? 'text-warn' : undefined} />
            <Row label="Yearly range percentile"
              value={`${fmtNum(a.bottom_context.range_percentile, 0)}%${a.bottom_context.bottom_decile ? ' · bottom decile' : ''}`}
              valueClass={a.bottom_context.bottom_decile ? 'text-accent font-semibold' : undefined} />
            <Row label="vs 200-day average" value={fmtPct(a.bottom_context.vs_sma200_pct, 1, true)} />
            {a.bottom_context.insider_buys_into_drawdown && (
              <p className="py-1.5 text-[11px] text-up">
                Insiders are cluster-buying INTO this drawdown — the closest thing
                to a validated bottom signal that exists (multi-month horizon).
              </p>
            )}
          </>
        )}
        <Row label="Realized vol (20d, ann.)" value={fmtPct(a.signals?.rv_20d, 1)} tip={g.rv_20d} />
        <Row label="vs 20d / 50d avg" value={`${fmtPct(a.signals?.sma20_dist, 1, true)} / ${fmtPct(a.signals?.sma50_dist, 1, true)}`} tip={g.sma20_dist} />
      </div>
    </SectionShell>
  )
}
