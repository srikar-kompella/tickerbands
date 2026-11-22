// The section panels. Each: data in → compact honest display, wrapped in
// SectionShell (freshness badge + Q12 degradation) with glossary tooltips.
//
// Declutter rules (2026-11-22), shared by every panel:
//   · rows with no value (status no_data) sit behind "Show N unavailable"
//     (RowList); a WITHHELD figure stays in the list, its caveat in the notes
//   · a caveat is printed once per section; affected rows carry a ⚠ marker
//   · a section with nothing to say is not drawn as an empty card — the page
//     lists it, with the reason, in one "nothing to show" line (panelStatus)
import { ExternalLink, Wallet } from 'lucide-react'
import { useState } from 'react'
import { fmtCompact, fmtDate, fmtMarketCap, fmtMoney, fmtNum, fmtPct, plColor } from '@/lib/format'
import { cn } from '@/lib/utils'
import {
  Badge, FinePrint, GlossaryTip, Notes, RowList, SectionShell, unavailableText, WarnMark,
  type Note, type RowSpec,
} from '@/components/ui'
import type { Analysis, GlossaryEntry, ReportField, SecurityKind, VLMetric } from '@/types'

type G = Record<string, GlossaryEntry>

// ─── report fields, by group ───────────────────────────────────────────────────
//
// report_view.fields is the checked, single-price set the report prints. Each
// field names its panel in `group`; payloads written before that attribute
// existed fall back to this key → group table, so nothing disappears.

const FIELD_GROUP: Record<string, string> = {
  pe_ttm: 'fundamentals', eps_ttm: 'fundamentals', market_cap: 'fundamentals',
  ev: 'fundamentals', net_cash: 'fundamentals', fcf_ttm: 'fundamentals', p_fcf: 'fundamentals',
  revenue_growth: 'fundamentals', revenue_growth_q: 'fundamentals',
  gross_margin: 'fundamentals', operating_margin: 'fundamentals',
  net_margin: 'fundamentals', net_margin_q: 'fundamentals',
  div_yield_forward: 'fundamentals', div_yield_trailing: 'fundamentals',
  put_call: 'options', atm_iv: 'options', implied_move: 'options', implied_vs_band: 'options',
}

// reading order inside a panel; keys not listed keep the backend's order, after these
const FIELD_ORDER = [
  'pe_ttm', 'eps_ttm', 'market_cap', 'ev', 'net_cash', 'fcf_ttm', 'p_fcf',
  'revenue_growth', 'revenue_growth_q', 'gross_margin', 'operating_margin',
  'net_margin', 'net_margin_q', 'div_yield_forward', 'div_yield_trailing',
  'put_call', 'atm_iv', 'implied_move', 'implied_vs_band',
]

// field key → glossary entry, where the names differ
const GLOSSARY_KEY: Record<string, string> = {
  net_margin_q: 'net_margin', div_yield_forward: 'dividend_yield',
  div_yield_trailing: 'dividend_yield', put_call: 'options_positioning',
}

const COMPANY_GROUPS = ['fundamentals', 'fund', 'bank']

const fieldGroup = (f: ReportField): string => f.group ?? FIELD_GROUP[f.key] ?? ''

function groupFields(a: Analysis, groups: string[]): ReportField[] {
  const rank = (k: string) => {
    const i = FIELD_ORDER.indexOf(k)
    return i < 0 ? FIELD_ORDER.length : i
  }
  return Object.entries(a.report_view?.fields ?? {})
    .map(([k, f]) => ({ ...f, key: f.key ?? k }))
    .filter((f) => groups.includes(fieldGroup(f)))
    .sort((x, y) => rank(x.key) - rank(y.key))
}

// A WITHHELD figure is a finding, not a gap: the report computed it and chose
// not to print it ("EPS sources disagree >50%", "EV withheld — balance sheet
// predates the listing"). It stays in the list, with its caveat in the
// section's notes. Only no_data rows go behind "Show N unavailable".
function fieldRow(f: ReportField, g: G, over: Partial<RowSpec> = {}): RowSpec {
  const raw = String(f.display ?? '—')
  const withheld = f.status === 'withheld'
  const empty = raw.trim() === '—' || raw.trim() === ''
  const display = withheld && empty ? '— withheld' : raw
  const basis = [f.basis, typeof f.source === 'string' && f.source ? `Source: ${f.source}` : '']
    .filter(Boolean).join(' · ')
  return {
    id: f.key,
    label: f.label,
    value: display,
    missing: !withheld && (f.status === 'no_data' || empty),
    ...(withheld && empty ? { valueClass: 'font-normal text-faint' } : {}),
    warnings: f.warnings ?? [],
    // no glossary entry → the field's own basis and source are the tooltip
    tip: g[GLOSSARY_KEY[f.key] ?? f.key] ?? (basis ? { term: f.label, plain: basis, why: '' } : undefined),
    ...over,
  }
}

/** stock | fund | bank | new_listing — from the backend when it says, else the quote's asset type. */
export function securityKind(a: Analysis): SecurityKind | undefined {
  return a.security_kind ?? a.report_view?.security_kind ?? a.security?.kind
    ?? (/ETF|FUND|COLLECTIVE/i.test(a.quote?.asset_type ?? '') ? 'fund' : undefined)
}

// `security_kind` precedence is fund → bank → new_listing, so a fund listed
// last week reads "fund": whether it is new comes from is_new_listing /
// security.new_listing, never from the kind alone.
export const isFund = (a: Analysis): boolean => securityKind(a) === 'fund' || !!a.security?.is_fund
export const isBank = (a: Analysis): boolean =>
  !isFund(a) && (securityKind(a) === 'bank' || !!a.security?.is_bank)
export const isNewListing = (a: Analysis): boolean =>
  !!a.is_new_listing || securityKind(a) === 'new_listing' || !!a.security?.new_listing

// report fields whose display is a sentence for the section's notes, not a row
const NOTE_FIELDS = ['bank_note']

// ─── Fundamentals (stocks, banks) / Fund profile (funds) ───────────────────────

function fundamentalsRows(a: Analysis, g: G): RowSpec[] {
  // The same checked, single-price fields the report uses (market cap / P/E /
  // margins / yields recomputed at the report price, basis-labeled).
  const fields = groupFields(a, COMPANY_GROUPS).filter((f) => !NOTE_FIELDS.includes(f.key))
  if (fields.length) return fields.map((f) => fieldRow(f, g))
  // Older payloads without report_view: the vendor block, with P/E and yield
  // from the TTM-computed block (BUG A: vendor metrics alone rendered ECO at
  // 40x / 0.8% when the real trailing figures were ~6.4x / ~13%).
  const f = a.fundamentals
  const t = a.fundamentals_ttm
  if (!f?.available) return []
  const warn = (w?: string | null) => (w ? [w] : [])
  return [
    { label: 'P/E (TTM)', value: t?.pe_ttm?.value != null ? fmtNum(t.pe_ttm.value, 1) : '—', tip: g.pe_ttm, warnings: warn(t?.pe_ttm?.warning) },
    { label: 'EPS (TTM)', value: fmtMoney(f.eps_ttm), tip: g.eps_ttm },
    { label: 'Market cap', value: fmtMarketCap(f.market_cap_m), tip: g.market_cap },
    { label: 'Revenue growth', value: fmtPct(f.revenue_growth_ttm_pct, 1, true), tip: g.revenue_growth, valueClass: plColor(f.revenue_growth_ttm_pct) },
    { label: 'Gross margin', value: fmtPct(f.gross_margin_pct, 1), tip: g.gross_margin },
    { label: 'Operating margin', value: fmtPct(f.operating_margin_pct, 1), tip: g.operating_margin },
    { label: 'Net margin', value: fmtPct(f.net_margin_pct, 1), tip: g.net_margin },
    { label: 'Dividend yield (TTM)', value: t?.yield_ttm?.value != null ? fmtPct(t.yield_ttm.value, 2) : '—', tip: g.dividend_yield, warnings: warn(t?.yield_ttm?.warning) },
  ]
}

const hasValue = (rows: RowSpec[]) => rows.some((r) => !(r.missing ?? r.value.trim() === '—'))

export function FundamentalsPanel({ a, g }: { a: Analysis; g: G }) {
  const rows = fundamentalsRows(a, g)
  const fund = isFund(a)
  const fromReport = groupFields(a, COMPANY_GROUPS).length > 0
  // the bank note says which company rows are not shown, and why — a sentence, not a figure
  const sentences = NOTE_FIELDS.map((k) => a.report_view?.fields?.[k])
    .filter((f) => f && f.status === 'ok' && typeof f.display === 'string' && f.display.trim() && f.display !== '—')
    .map((f) => f!.display)
  const t = a.fundamentals_ttm
  return (
    <SectionShell title={fund ? 'Fund profile' : 'Fundamentals'} envelope={a.sections.fundamentals}
      glossary={fund ? g.asset_type : g.pe_ttm}>
      <RowList rows={rows} />
      <Notes tone="info" notes={sentences} />
      {fromReport && a.price_basis?.price_label && (
        <FinePrint>At the report price: {a.price_basis.price_label}</FinePrint>
      )}
      {!fromReport && t && (
        <FinePrint>
          P/E: {t.pe_ttm.source}{t.pe_ttm.as_of ? ` · as of ${t.pe_ttm.as_of}` : ''}
          <br />
          Yield: {t.yield_ttm.source}{t.yield_ttm.as_of ? ` · as of ${t.yield_ttm.as_of}` : ''}
        </FinePrint>
      )}
    </SectionShell>
  )
}

// ─── Value lens (statement-derived, multi-year) ────────────────────────────────

function valueLensRows(a: Analysis): RowSpec[] {
  const v = a.value_lens
  if (!v) return []
  const why = 'Statement-derived comparison lens — context for the research stance, not a signal.'
  const tip = (term: string, plain: string) => ({ term, plain, why })
  const or = (x: number | null, f: (n: number) => string) => (x != null ? f(x) : '—')
  // a figure the Fundamentals card already shows (checked, at the report price) is not repeated here
  const inFundamentals = (k: string) => {
    const f = a.report_view?.fields?.[k]
    return !!f && COMPANY_GROUPS.includes(fieldGroup({ ...f, key: f.key ?? k }))
  }
  // A bank's cash-flow / EV / ROIC rows are deliberately not computed (the
  // section's warning says why) — an empty one is not "unavailable", it is not
  // shown. A figure the backend did return still prints.
  const bank = isBank(a)
  const notForBank = (...ms: VLMetric[]) => bank && ms.every((m) => m?.value == null)
  const acqLabel = v.acquisitions_total.label || 'Cash paid for acquisitions'
  const rows: (RowSpec | false)[] = [
    !inFundamentals('fcf_ttm') && !notForBank(v.fcf_ttm) && { label: 'FCF (TTM)', value: or(v.fcf_ttm.value, (n) => `$${fmtNum(n, 0)}M`), tip: tip('FCF (TTM)', v.fcf_ttm.basis) },
    !inFundamentals('p_fcf') && !notForBank(v.p_fcf) && { label: 'P/FCF', value: or(v.p_fcf.value, (n) => `${fmtNum(n, 1)}x`), tip: tip('P/FCF', v.p_fcf.basis) },
    !notForBank(v.p_fcf_avg) && { label: 'P/FCF (multi-FY avg)', value: or(v.p_fcf_avg.value, (n) => `${fmtNum(n, 1)}x`), tip: tip('P/FCF avg', v.p_fcf_avg.basis) },
    { label: 'P/E (multi-FY avg)', value: or(v.pe_avg.value, (n) => `${fmtNum(n, 1)}x`), tip: tip('P/E avg', v.pe_avg.basis) },
    !notForBank(v.payout_of_fcf_pct) && { label: 'Dividends ÷ FCF', value: or(v.payout_of_fcf_pct.value, (n) => fmtPct(n, 0)), tip: tip('Payout of FCF', v.payout_of_fcf_pct.basis) },
    !inFundamentals('ev') && !notForBank(v.ev) && { label: 'Enterprise value', value: or(v.ev.value, (n) => `$${fmtNum(n, 0)}M${v.net_cash ? ' · net cash' : ''}`), tip: tip('EV', v.ev.basis) },
    {
      label: 'Revenue CAGR 3y / ~5y',
      value: `${or(v.rev_cagr_3y.value, (n) => fmtPct(n, 1))} / ${or(v.rev_cagr_5y.value, (n) => fmtPct(n, 1))}`,
      missing: v.rev_cagr_3y.value == null && v.rev_cagr_5y.value == null,
      tip: tip('Revenue CAGR', v.rev_cagr_5y.basis),
    },
    !notForBank(v.roic_ttm, v.roic_avg) && {
      label: 'ROIC~ TTM / multi-yr',
      value: `${or(v.roic_ttm.value, (n) => fmtPct(n, 1))} / ${or(v.roic_avg.value, (n) => fmtPct(n, 1))}`,
      missing: v.roic_ttm.value == null && v.roic_avg.value == null,
      tip: tip('ROIC (approx)', v.roic_ttm.basis),
    },
    // cash deals only — the backend excludes acquisitions paid in stock
    { label: acqLabel, value: or(v.acquisitions_total.value, (n) => `$${fmtNum(n, n < 10 ? 1 : 0)}M`), tip: tip(acqLabel, v.acquisitions_total.basis) },
  ]
  return rows.filter(Boolean) as RowSpec[]
}

export function ValueLensPanel({ a }: { a: Analysis; g: G }) {
  const v = a.value_lens
  if (!v) return null
  return (
    <SectionShell title="Value lens · statements">
      <RowList rows={valueLensRows(a)} />
      <Notes tone="info" notes={v.readings ?? []} />
      <Notes notes={v.warnings ?? []} />
      <FinePrint>
        Statements: {v.source}{v.as_of ? ` · ${v.as_of}` : ''}. Comparison lenses
        for research — they feed the Ask-Claude prompt and the reasoned stance,
        never a computed buy/sell. ROIC~ is an approximation.
      </FinePrint>
    </SectionShell>
  )
}

// ─── News sentiment + headlines ────────────────────────────────────────────────

const HEADLINES_SHOWN = 5

export function NewsPanel({ a, g }: { a: Analysis; g: G }) {
  const [allHeadlines, setAllHeadlines] = useState(false)
  const n = a.news_sentiment
  const cn2 = a.claude_news
  const bull = n?.available ? Math.round((n.bullish_pct ?? 0) * 100) : null
  const bear = n?.available ? Math.round((n.bearish_pct ?? 0) * 100) : null
  const lexicon = n?.method === 'headline-lexicon'
  // What is wrong with the research news check (written before a ≥2σ session,
  // cause not stated, research incomplete, from an older note…). Without
  // these, "Claude: neutral — no material news" read as a clean result the
  // day after a −14% session. Same list the report prints beside the row.
  const newsWarns: string[] =
    a.report_view?.fields?.claude_news?.warnings ?? a.research_flags?.news ?? []
  const flagged = newsWarns.length > 0
  // why the report does not score this sample (e.g. fewer than 10 relevant articles)
  const sentimentWarns = a.report_view?.fields?.news_sentiment?.warnings ?? []
  const heads = a.headlines ?? []
  const shown = allHeadlines ? heads : heads.slice(0, HEADLINES_SHOWN)
  const env = a.sections.news_sentiment
  const hasContent = !!n?.available || !!cn2 || flagged || heads.length > 0
  return (
    <SectionShell title="News" glossary={g.news_sentiment}
      envelope={env?.status === 'unavailable' && hasContent ? undefined : env}>
      {n?.available ? (
        <>
          <div className="mb-1 flex items-center justify-between gap-2 text-xs">
            <span className="font-medium text-up">{bull}% bullish</span>
            <span className="text-muted tnum" title={lexicon
              ? 'Scored from the free headlines with a transparent wordlist (Finnhub aggregate sentiment unavailable on this tier)'
              : 'Finnhub aggregated news sentiment'}>
              {n.articles_week} articles
              {n.buzz != null && ` · buzz ${fmtNum(n.buzz, 1)}×`}
              {' · '}{lexicon ? 'headline-scored' : 'finnhub'}
            </span>
            <span className="font-medium text-down">{bear}% bearish</span>
          </div>
          {/* sentiment split bar */}
          <div className="flex h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div className="bg-up" style={{ width: `${bull}%` }} />
            <div className="ml-auto bg-down" style={{ width: `${bear}%` }} />
          </div>
          <Notes notes={sentimentWarns} />
        </>
      ) : (
        <p className="py-1 text-[13px] text-faint">No news sentiment for this instrument.</p>
      )}

      {/* Claude's live news check (from discussion frontmatter, ask #3) */}
      {(cn2 || flagged) && (
        <div className="mt-3 border-t border-border/60 pt-2.5">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {cn2 ? (
              <Badge
                tone={flagged ? 'warn'
                  : cn2.news_view === 'bullish' ? 'up' : cn2.news_view === 'bearish' ? 'down' : 'neutral'}
                title={flagged
                  ? `Do not read this news check as a result:\n${newsWarns.join('\n')}`
                  : 'The research session\'s own news check — written by the session, not computed'}>
                {flagged && '⚠ '}Claude: {cn2.news_view}
              </Badge>
            ) : (
              <Badge tone="warn" title={newsWarns.join('\n')}>⚠ Claude: no news check shown</Badge>
            )}
            {cn2 && <span className="text-faint">{fmtDate(cn2.created_ts)}</span>}
          </div>
          {cn2?.news_note && <p className="mt-1.5 text-xs leading-relaxed text-muted">{cn2.news_note}</p>}
          <Notes notes={newsWarns} className="mt-2" />
        </div>
      )}

      {!!heads.length && (
        <div className="mt-3 border-t border-border/60 pt-2.5">
          <ul className="space-y-2">
            {shown.map((h, i) => {
              const tone = n?.per_headline?.[i]
              return (
                <li key={i} className="text-[13px] leading-snug">
                  <a href={h.url} target="_blank" rel="noreferrer" title={h.headline}
                    className="group flex items-start gap-1.5 text-text hover:text-accent">
                    <span className={cn('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                      tone == null || tone === 0 ? 'bg-surface-3' : tone > 0 ? 'bg-up' : 'bg-down')} />
                    <span className="line-clamp-2 min-w-0 flex-1">{h.headline}</span>
                    <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-faint group-hover:text-accent" />
                  </a>
                  <span className="ml-3 text-[11px] text-faint">{h.source} · {fmtDate(new Date(h.ts * 1000).toISOString())}</span>
                </li>
              )
            })}
          </ul>
          {heads.length > HEADLINES_SHOWN && (
            <button onClick={() => setAllHeadlines((s) => !s)} aria-expanded={allHeadlines}
              className="mt-2 text-xs text-faint hover:text-muted">
              {allHeadlines ? 'Show fewer headlines' : `Show ${heads.length - HEADLINES_SHOWN} more headlines`}
            </button>
          )}
        </div>
      )}
    </SectionShell>
  )
}

// ─── Social (StockTwits) ───────────────────────────────────────────────────────

export function SocialPanel({ a, g }: { a: Analysis; g: G }) {
  const s = a.social
  const thin = (s?.tagged ?? 0) < 5
  // the report's reason for not scoring the sample, shown once with the app's own thin-sample line
  const notes = [
    ...(thin ? [`Thin sample (${s?.tagged} tagged) — treat as anecdote, not signal.`] : []),
    ...(a.report_view?.fields?.social?.warnings ?? []),
  ]
  return (
    <SectionShell title="Social · StockTwits" envelope={a.sections.social} glossary={g.social_buzz}
      defaultCollapsed
      summary={s?.available ? (
        <>
          polarity {(s.polarity ?? 0) >= 0 ? '+' : ''}{fmtNum(s.polarity, 2)}
          {!!notes.length && <> · <WarnMark text={notes.join('\n')} /></>}
        </>
      ) : undefined}>
      {s?.available ? (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Badge tone={(s.polarity ?? 0) > 0.15 ? 'up' : (s.polarity ?? 0) < -0.15 ? 'down' : 'neutral'}>
              polarity {(s.polarity ?? 0) >= 0 ? '+' : ''}{fmtNum(s.polarity, 2)}
            </Badge>
            <span className="tnum text-xs text-muted">
              {s.bullish} bull / {s.bearish} bear of {s.total_msgs} recent msgs
            </span>
          </div>
          <Notes notes={notes} />
          {!!s.sample?.length && (
            <p className="mt-2.5 line-clamp-3 text-xs italic leading-relaxed text-faint" title={s.sample[0].body}>
              “{s.sample[0].body}”
            </p>
          )}
        </>
      ) : (
        <p className="py-1 text-[13px] text-faint">No StockTwits stream.</p>
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
              <div className="mb-1 flex justify-between text-xs text-muted">
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
          <FinePrint>
            Shifts in the mix matter; levels are mostly priced in. Price targets
            deliberately not shown (no free source; levels are stale anyway).
          </FinePrint>
        </div>
      ) : (
        <p className="py-1 text-[13px] text-faint">No analyst coverage (normal for ETFs).</p>
      )}
    </SectionShell>
  )
}

// ─── Options positioning ───────────────────────────────────────────────────────

export function OptionsPanel({ a, g }: { a: Analysis; g: G }) {
  const o = a.options
  if (!o?.available) return null // no listed options → listed under "nothing to show" (Q12)
  const pc = o.put_call_ratio
  const pcClass = pc == null ? 'text-faint' : pc < 0.7 ? 'text-up' : pc > 1.1 ? 'text-down' : undefined
  const pcLabel = a.price_basis?.price_kind === 'live' ? 'Put/Call (today)' : 'Put/Call (last session)'
  const volume: RowSpec = {
    label: 'Call / Put volume', value: `${fmtCompact(o.call_volume)} / ${fmtCompact(o.put_volume)}`,
  }
  const fields = groupFields(a, ['options'])
  const ivLabel = fields.find((f) => f.key === 'atm_iv')?.label
    ?? (o.atm_iv_note ? 'ATM implied vol (at last close)' : 'ATM implied vol')
  const rows: RowSpec[] = fields.length
    ? fields.flatMap((f) => (f.key === 'put_call'
      ? [fieldRow(f, g, { label: pcLabel, valueClass: pcClass }), volume]
      : [fieldRow(f, g)]))
    : [
      { label: pcLabel, value: pc == null ? '— (thin volume)' : fmtNum(pc, 2), valueClass: pcClass, tip: g.options_positioning, missing: false },
      volume,
      { label: ivLabel, value: o.atm_iv_pct != null ? `${fmtNum(o.atm_iv_pct, 1)}%` : '—', tip: g.atm_iv },
      { label: `Implied move (${o.implied_move_dte ?? '—'}d)`, value: o.implied_move_pct != null ? `±${o.implied_move_pct}%` : '—', tip: g.implied_move },
    ]
  if (fields.length && !fields.some((f) => f.key === 'put_call')) rows.unshift(volume)
  const notes: Note[] = o.atm_iv_note ? [{ text: o.atm_iv_note, rows: [ivLabel] }] : []
  return (
    <SectionShell title="Options positioning" envelope={a.sections.options} glossary={g.options_positioning}>
      <RowList rows={rows} notes={notes} />
    </SectionShell>
  )
}

// ─── Earnings (S1 — the band's bodyguard) ──────────────────────────────────────

export function EarningsPanel({ a, g }: { a: Analysis; g: G }) {
  const e = a.earnings
  const tag = a.report_view?.fields?.earnings_next?.tag
  return (
    <SectionShell title="Earnings" envelope={a.sections.earnings} glossary={g.earnings_countdown}>
      {e?.available ? (
        <>
          <div className="flex items-baseline justify-between text-[13px]">
            <span className="text-muted">Next report</span>
            <span className="tnum font-semibold">
              {fmtDate(e.next_date)}{e.days_until != null && ` · ${e.days_until}d`}
              {tag && <span className="ml-1.5 text-[11px] font-normal text-faint">{tag === 'confirmed' ? 'confirmed' : 'est.'}</span>}
            </span>
          </div>
          {e.days_until != null && e.days_until <= 5 && (
            <Notes notes={['Earnings window: the 80% range is calibrated for normal days and is unreliable through the print.']} />
          )}
          {/* roadmap #3: what THIS stock actually does on earnings days,
              vs what options are pricing for the next one */}
          {a.earnings_move && (a.earnings_move.quarters > 0 || a.earnings_move.implied_move_pct != null) && (
            <div className="mt-3">
              <div className="mb-1 flex items-center gap-1 text-[11px] uppercase tracking-wide text-faint">
                expected earnings move <GlossaryTip entry={g.earnings_move} />
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px]">
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
            <p className="mt-3 flex items-start gap-1 text-xs leading-relaxed text-muted">
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
            <div className="mt-3">
              <div className="mb-1 flex items-center gap-1 text-[11px] uppercase tracking-wide text-faint">
                surprise history <GlossaryTip entry={g.earnings_surprise} />
              </div>
              <div className="flex gap-1.5">
                {e.surprises.map((s2, i) => (
                  <div key={i}
                    className={cn('min-w-0 flex-1 rounded-md px-1.5 py-1 text-center',
                      (s2.surprise_pct ?? 0) >= 0 ? 'bg-up/10 text-up' : 'bg-down/10 text-down')}>
                    <div className="tnum text-xs font-semibold">{fmtPct(s2.surprise_pct, 1, true)}</div>
                    <div className="tnum text-[11px] opacity-70">{s2.period?.slice(0, 7)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="py-1 text-[13px] text-faint">No earnings data (normal for ETFs).</p>
      )}
    </SectionShell>
  )
}

// ─── Insiders (SEC EDGAR — open-market trades, cluster detection) ──────────────

export function InsidersPanel({ a, g }: { a: Analysis; g: G }) {
  const ins = a.insiders
  return (
    <SectionShell title="Insiders · SEC Form 4" envelope={a.sections.insiders} glossary={g.insiders_cluster}
      // routine trades start closed; a cluster buy is the one insider event worth opening for
      defaultCollapsed={!ins?.cluster_buy}
      summary={ins?.available
        ? `${ins.cluster_buy ? 'cluster buy · ' : ''}net ${ins.net_shares_90d > 0 ? '+' : ''}${fmtCompact(ins.net_shares_90d)} sh / 90d`
        : undefined}>
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
            <div className="divide-y divide-border/50">
              {ins.transactions.slice(0, 5).map((t, i) => (
                <div key={i} className="py-1.5 text-[13px]">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="min-w-0 truncate" title={`${t.owner} (${t.title})`}>
                      <span className={t.code === 'P' ? 'font-semibold text-up' : 'font-semibold text-down'}>
                        {t.code === 'P' ? 'BUY' : 'SELL'}
                      </span>{' '}
                      {t.owner}
                    </span>
                    <span className="tnum shrink-0 font-medium">{fmtMoney(t.value, 0)}</span>
                  </div>
                  <div className="flex justify-between gap-2 text-[11px] text-faint">
                    <span className="min-w-0 truncate">{t.title}</span>
                    <span className="tnum shrink-0">{fmtCompact(t.shares)} sh · {fmtDate(t.date)}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-1 text-[13px] text-faint">No open-market insider trades in recent filings.</p>
          )}
          <FinePrint>{ins.note}</FinePrint>
        </>
      ) : (
        <p className="py-1 text-[13px] text-faint">No Form 4 data (ETFs/funds don't file).</p>
      )}
    </SectionShell>
  )
}

// ─── Short interest (FINRA bi-monthly) ─────────────────────────────────────────

export function ShortInterestPanel({ a, g }: { a: Analysis; g: G }) {
  const si = a.short_interest
  return (
    <SectionShell title="Short interest · FINRA" envelope={a.sections.short_interest} glossary={g.short_interest}
      defaultCollapsed
      summary={si?.available
        ? `${fmtPct(si.pct_of_shares_out, 1)} of shares · ${fmtNum(si.days_to_cover, 1)}d to cover`
        : undefined}>
      {si?.available ? (
        <>
          <RowList rows={[
            { label: 'Short interest', value: fmtCompact(si.short_interest), tip: g.short_interest },
            { label: 'Δ vs prior period', value: fmtPct(si.change_pct, 1, true), valueClass: plColor(si.change_pct != null ? -si.change_pct : null) },
            { label: 'Days to cover', value: fmtNum(si.days_to_cover, 1), tip: g.days_to_cover, valueClass: (si.days_to_cover ?? 0) >= 5 ? 'text-warn font-semibold' : undefined },
            { label: '% of shares outstanding', value: fmtPct(si.pct_of_shares_out, 2) },
          ]} />
          <FinePrint>
            Settlement {fmtDate(si.settlement_date)} · reported bi-monthly, so this
            lags by days-to-weeks · % uses shares OUTSTANDING (free float isn't free data)
          </FinePrint>
        </>
      ) : (
        <p className="py-1 text-[13px] text-faint">No short interest data for this instrument.</p>
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
        <>
          <RowList rows={[
            { label: 'Shares', value: `${fmtNum(p.qty, 0)} @ ${fmtMoney(p.avg_cost)}` },
            { label: 'Market value', value: fmtMoney(p.market_value) },
            { label: 'Gain', value: `${fmtMoney(p.gain)} (${fmtPct(p.gain_pct, 1, true)})`, valueClass: plColor(p.gain) },
            { label: 'Portfolio weight', value: fmtPct(p.weight_pct, 1) },
          ]} />
          <FinePrint>
            <Wallet className="mr-1 inline h-3 w-3" />
            read-only from the Portfolio app · as of {fmtDate(p.as_of)}
          </FinePrint>
        </>
      ) : (
        <p className="py-1 text-[13px] text-faint">You don't hold this in the tracked Schwab account.</p>
      )}
    </SectionShell>
  )
}

// ─── Context stats (S5: beta/corr, 52w position, volume z) ─────────────────────

export function ContextPanel({ a, g }: { a: Analysis; g: G }) {
  const bc = a.bottom_context
  const sig = a.signals
  // the report's caveats on these rows ("beta not meaningful — correlation 0.01 …")
  const rvf = a.report_view?.fields ?? {}
  const warns = (k: string): string[] => rvf[k]?.warnings ?? []
  const rows: RowSpec[] = [
    { label: 'Beta vs SPY (60d)', value: fmtNum(a.beta?.beta, 2), tip: g.beta, warnings: warns('beta') },
    { label: 'Correlation to SPY', value: fmtNum(a.beta?.correlation, 2), tip: g.correlation, warnings: warns('correlation') },
    { label: 'Realized vol (20d, ann.)', value: fmtPct(sig?.rv_20d, 1), tip: g.rv_20d, warnings: warns('rv20') },
    {
      label: 'Volume z-score (prev. session)', value: fmtNum(sig?.volume_z, 2), tip: g.volume_z,
      valueClass: Math.abs(sig?.volume_z ?? 0) > 2 ? 'text-warn' : undefined,
    },
    {
      label: 'vs 20d / 50d avg', tip: g.sma20_dist, missing: !sig,
      value: `${fmtPct(sig?.sma20_dist, 1, true)} / ${fmtPct(sig?.sma50_dist, 1, true)}`,
    },
    ...(bc ? [
      { label: 'vs 200-day average', value: fmtPct(bc.vs_sma200_pct, 1, true) },
      {
        label: 'Drawdown from 52w high', value: fmtPct(bc.drawdown_pct, 1), tip: g.bottom_context,
        valueClass: bc.drawdown_pct < -25 ? 'text-warn' : undefined,
      },
      {
        label: 'Yearly range percentile',
        value: `${fmtNum(bc.range_percentile, 0)}%${bc.bottom_decile ? ' · bottom decile' : ''}`,
        valueClass: bc.bottom_decile ? 'text-accent font-semibold' : undefined,
      },
    ] : []),
  ]
  return (
    <SectionShell title="Context" envelope={a.sections.history} glossary={g.beta}>
      {a.week52 && (
        <div className="pb-2">
          <div className="mb-1.5 flex items-center justify-between text-[13px]">
            <span className="flex items-center gap-1 text-muted">52-week range <GlossaryTip entry={g.week52_position} /></span>
            <span className="tnum font-medium">{fmtNum(a.week52.position_pct, 0)}%</span>
          </div>
          <div className="relative h-1.5 rounded-full bg-surface-3">
            <div className="absolute top-1/2 h-3 w-1 -translate-y-1/2 rounded-full bg-accent"
              style={{ left: `calc(${Math.min(100, Math.max(0, a.week52.position_pct))}% - 2px)` }} />
          </div>
          <div className="mt-1 flex justify-between text-[11px] text-faint tnum">
            <span>{fmtMoney(a.week52.low)}</span><span>{fmtMoney(a.week52.high)}</span>
          </div>
        </div>
      )}
      <RowList rows={rows} notes={warns('week52').map((text) => ({ text, rows: ['52-week range'] }))} />
      {bc?.insider_buys_into_drawdown && (
        <p className="mt-2 text-xs leading-relaxed text-up">
          Insiders are cluster-buying INTO this drawdown — the closest thing
          to a validated bottom signal that exists (multi-month horizon).
        </p>
      )}
    </SectionShell>
  )
}

// ─── which panels have something to show ───────────────────────────────────────

export type PanelKey = 'fundamentals' | 'value' | 'context' | 'options' | 'earnings'
  | 'news' | 'recs' | 'insiders' | 'short' | 'social' | 'position'

const ENVELOPE: Partial<Record<PanelKey, string>> = {
  fundamentals: 'fundamentals', context: 'history', options: 'options',
  earnings: 'earnings', recs: 'recommendations', insiders: 'insiders',
  short: 'short_interest', social: 'social', position: 'position',
}

export interface PanelStatus {
  /** draw the card */
  show: boolean
  /** not drawn, and this is why — listed once at the foot of the page */
  reason?: string
}

/**
 * A section with nothing to say is not drawn as an empty card. `reason` is the
 * sentence the empty card used to carry, so nothing becomes unreachable.
 * A feature-flagged-off section (`disabled`) is neither drawn nor listed.
 */
export function panelStatus(a: Analysis, key: PanelKey): PanelStatus {
  const show: PanelStatus = { show: true }
  const gap = (reason: string): PanelStatus => ({ show: false, reason })
  const env = a.sections?.[ENVELOPE[key] ?? '']
  if (env?.status === 'disabled') return { show: false }
  if (env?.status === 'unavailable') return gap(unavailableText(env))
  switch (key) {
    case 'fundamentals':
      return hasValue(fundamentalsRows(a, {})) ? show
        : gap('No fundamentals published for this instrument (normal for ETFs/trusts).')
    case 'value': {
      const v = a.value_lens
      if (!v) return { show: false }
      return hasValue(valueLensRows(a)) || v.readings?.length || v.warnings?.length ? show
        : gap('No statement-derived figures for this instrument.')
    }
    case 'context':
      return show
    case 'options':
      return a.options?.available ? show : gap('No listed options for this instrument.')
    case 'earnings':
      return a.earnings?.available ? show : gap('No earnings data (normal for ETFs).')
    case 'news': {
      const flagged = (a.report_view?.fields?.claude_news?.warnings ?? a.research_flags?.news ?? []).length > 0
      if (a.news_sentiment?.available || a.claude_news || flagged || a.headlines?.length) return show
      const e = a.sections?.news_sentiment
      return gap(e?.status === 'unavailable' ? unavailableText(e) : 'No news sentiment or headlines for this instrument.')
    }
    case 'recs':
      return a.recommendations?.available && a.recommendations.months?.length ? show
        : gap('No analyst coverage (normal for ETFs).')
    case 'insiders': {
      const ins = a.insiders
      if (!ins?.available) return gap("No Form 4 data (ETFs/funds don't file).")
      if (ins.transactions.length || ins.cluster_buy) return show
      const base = 'No open-market insider trades in recent filings'
      const note = ins.note && !ins.note.toLowerCase().startsWith(base.toLowerCase()) ? ` ${ins.note}` : ''
      return gap(`${base} (net ${ins.net_shares_90d > 0 ? '+' : ''}${fmtCompact(ins.net_shares_90d)} sh / 90d).${note}`)
    }
    case 'short':
      return a.short_interest?.available ? show : gap('No short interest data for this instrument.')
    case 'social':
      return a.social?.available ? show : gap('No StockTwits stream.')
    case 'position':
      return a.position?.owned ? show : gap("You don't hold this in the tracked Schwab account.")
  }
}
