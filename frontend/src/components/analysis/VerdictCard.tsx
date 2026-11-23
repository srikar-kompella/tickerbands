// The Verdict card (docs/VERDICT_SPEC.md): the call, the two axes it is built
// from, how the rulebook turned them into the call, and what it means for
// you. Value: the measure now against its own five-year median / low / high
// (valuation_range). Risk: the three kinds of item — Hard, Soft, Watch-only.
// Compact by default; long texts truncate with the full text on hover or
// click. Every field is optional — an earlier-format note (before prompt v6)
// shows its old stance and whatever of this card still applies.
import { useState } from 'react'
import { fmtDate, fmtNum } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Disclosure, GlossaryTip, Notes, SectionShell } from '@/components/ui'
import {
  EarlierStance, RISK_WORD, TrustWord, VALUE_WORD, VerdictPill, howItWasSet,
  ownerParts, readVerdict, stanceOwned,
} from '@/components/verdict'
import type { Analysis, GlossaryEntry, RuleInput, Trust, ValuationRange } from '@/types'
import { isNewListing, newListingCapped } from './Panels'

const strs = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x.trim()) : []

/** "[ni_over_fcf] Net income …" → { key: 'ni_over_fcf', text: 'Net income …' } (spec §2). */
const RULE_PREFIX = /^\s*\[([A-Za-z0-9_]+)\]\s*/
export function splitRuleKey(s: string): { key: string | null; text: string } {
  const m = RULE_PREFIX.exec(s)
  return m ? { key: m[1], text: s.slice(m[0].length) || s } : { key: null, text: s }
}

// fallback labels when the payload sends a measure without its label
const MEASURE_LABEL: Record<string, string> = {
  p_fcf: 'Price to free cash flow', ev_ebit: 'EV / EBIT', pe: 'Price to earnings',
  p_sales: 'Price to sales', p_tbv: 'Price to tangible book', distribution_rate: 'Distribution rate',
}
const measureLabel = (vr: ValuationRange) =>
  vr.label ?? (vr.measure ? MEASURE_LABEL[vr.measure] : undefined) ?? 'Measure'

const GRADE_ORDER = { cheap: 0, fair: 1, expensive: 2 } as const
type Hint = keyof typeof GRADE_ORDER
/** The app's resulting grade: `grade`, else the less favorable of the two hints (spec §4). */
function appGrade(vr: ValuationRange): Hint | null {
  const asHint = (x: unknown): Hint | null => (typeof x === 'string' && x in GRADE_ORDER ? (x as Hint) : null)
  const g = asHint(vr.grade)
  if (g) return g
  const a = asHint(vr.grade_hint), b = asHint(vr.normalized_grade_hint)
  if (a && b) return GRADE_ORDER[a] >= GRADE_ORDER[b] ? a : b
  return a ?? b
}

// ─── value axis ─────────────────────────────────────────────────────────────

const NOTES_SHOWN = 2

/** "FY ended 2025-12-31: no FCF — left out" ×4 → one line naming the four years. */
function mergeYearNotes(notes: string[]): string[] {
  const out: string[] = []
  const byTail = new Map<string, { at: number; pre: string; years: string[] }>()
  const YEAR = /^(FY ended )?(\d{4}-\d{2}-\d{2}): (.+)$/
  for (const n of notes) {
    const m = YEAR.exec(n)
    if (!m) { out.push(n); continue }
    const key = (m[1] ?? '') + m[3]
    const hit = byTail.get(key)
    if (hit) hit.years.push(m[2])
    else { byTail.set(key, { at: out.length, pre: m[1] ?? '', years: [m[2]] }); out.push('') }
  }
  for (const [key, { at, pre, years }] of byTail) {
    out[at] = `${pre}${years.join(', ')}: ${key.slice(pre.length)}`
  }
  // the general notes ("only 0 year-end points") read first; per-year detail after
  const isYear = (n: string) => YEAR.test(n) || /^(FY ended )?\d{4}-\d{2}-\d{2}(, \d{4}-\d{2}-\d{2})+: /.test(n)
  return [...out.filter((n) => !isYear(n)), ...out.filter(isYear)]
}

const unitOf = (vr: ValuationRange) => (vr.measure === 'distribution_rate' ? '%' : '×')
const fmtM = (v: number | null | undefined, unit: string) =>
  // small multiples (P/TBV 1.18×) need the second decimal to tell now from the median
  v == null ? '—' : `${fmtNum(v, Math.abs(v) < 10 ? 2 : 1)}${unit}`

/** A small horizontal range: the five-year low–high band, the median tick, "now". */
function RangeBar({ vr }: { vr: ValuationRange }) {
  const { now, low, high, median } = vr
  if (now == null || low == null || high == null || high <= low) return null
  const unit = unitOf(vr)
  const lo = Math.min(low, now), hi = Math.max(high, now)
  const pad = (hi - lo) * 0.08 || 1
  const min = lo - pad, max = hi + pad
  const pos = (x: number) => `${((x - min) / (max - min)) * 100}%`
  const g = appGrade(vr)
  const tone = g === 'cheap' ? 'bg-up' : g === 'expensive' ? 'bg-warn' : 'bg-text'
  const label = `${measureLabel(vr)} now ${fmtM(now, unit)}; five-year range ${fmtM(low, unit)} to ${fmtM(high, unit)}`
    + (median != null ? `, median ${fmtM(median, unit)}` : '')
  const pts = (vr.points ?? []).filter((p) => p.value != null)
  // a left-out year (> 5× the others' median) would sit far off the bar
  const ticks = pts.filter((p) => (p.value as number) >= min && (p.value as number) <= max)
  return (
    <div role="img" aria-label={label}
      title={pts.length ? pts.map((p) => `FY end ${p.fy_end}: ${fmtM(p.value, unit)}`).join('\n') : label}
      className="relative mt-2 mb-1 h-4">
      <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-surface-3" />
      <div className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-accent/30"
        style={{ left: pos(low), width: `calc(${pos(high)} - ${pos(low)})` }} />
      {ticks.map((p, i) => (
        <div key={`${i}-${p.fy_end}`} className="absolute top-1/2 h-1.5 w-px -translate-y-1/2 bg-muted/50"
          style={{ left: pos(p.value as number) }} />
      ))}
      {median != null && (
        <div className="absolute top-0 h-4 w-0.5 -translate-x-1/2 rounded bg-muted"
          style={{ left: pos(median) }} />
      )}
      <div className={cn('absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface', tone)}
        style={{ left: pos(now) }} />
    </div>
  )
}

function ValueAxis({ vr, grade, basis, newListing, tip }: {
  vr: ValuationRange | null | undefined; grade: string; basis?: string | null; newListing: boolean
  tip?: GlossaryEntry
}) {
  const unit = vr ? unitOf(vr) : '×'
  const ok = !!vr && (vr.status ?? 'ok') === 'ok' && vr.now != null
  const warns = mergeYearNotes(strs(vr?.warnings))
  // years left out of median / low / high — named unless a warning already does
  const excluded = (vr?.excluded_points ?? []).filter((p) => p?.fy_end && !warns.some((w) => w.includes(p.fy_end)))
  // the backend usually says it already ("… N× its five-year average …") — once is enough
  const notes = [
    ...warns,
    ...(vr?.normalized && !warns.some((w) => /average|normali/i.test(w))
      ? ['Current profit or cash flow is more than double its five-year average — the app grades both the reported figure and the average'] : []),
    ...(excluded.length
      ? [`Left out of median / low / high (more than 5× the other years' median): ${excluded
          .map((p) => `FY ${p.fy_end}${p.value != null ? ` (${fmtM(p.value, unit)})` : ''}`).join(', ')}`] : []),
  ]
  // the app's grade (the less favorable of the two hints) and, when the two
  // differ, both hints: on the reported figure and on the five-year average
  const appG = vr ? appGrade(vr) : null
  const grade0 = appG ? VALUE_WORD[appG] : ''
  const hint = vr?.grade_hint ? VALUE_WORD[vr.grade_hint] : ''
  const nHint = vr?.normalized_grade_hint ? VALUE_WORD[vr.normalized_grade_hint] : ''
  const bothHints = !!hint && !!nHint && hint !== nHint
  const why = !vr
    ? (newListing ? 'New listing — graded against peers, not its own history.' : 'No valuation range in this snapshot.')
    : vr.status === 'withheld'
      ? `Withheld${vr.reason ? ` — ${vr.reason}` : notes.length ? '' : ' — the report does not trust the inputs'}.`
      : vr.status === 'no_data' || vr.now == null
        ? (vr.now != null
            ? `now ${fmtM(vr.now, unit)} — no five-year range to compare it with${vr.reason ? `: ${vr.reason}` : ''}.`
            : `No data${vr.reason ? ` — ${vr.reason}` : ''}.`)
        : ''
  return (
    <div className="min-w-0">
      <h4 className="text-[11px] uppercase tracking-wide text-faint">
        Value{grade && <span className="ml-1.5 normal-case tracking-normal text-text">· {grade}</span>}
        {tip && <GlossaryTip entry={tip} />}
      </h4>
      {ok ? (
        <>
          <RangeBar vr={vr!} />
          <p className="tnum text-xs text-muted">
            <span className="text-text">{measureLabel(vr!)}</span> now{' '}
            <span className="font-semibold text-text">{fmtM(vr!.now, unit)}</span>
            {vr!.median != null && <> · 5-yr median {fmtM(vr!.median, unit)}</>}
            {vr!.low != null && vr!.high != null && <> · range {fmtM(vr!.low, unit)}–{fmtM(vr!.high, unit)}</>}
          </p>
          {vr!.now_basis && <p className="text-[11px] text-faint">{vr!.now_basis}</p>}
          {vr!.measure === 'p_sales' && (
            <p className="text-[11px] text-faint">
              Measured on sales — profit or cash flow is negative, so earnings multiples do not apply.
            </p>
          )}
          {grade0 && (
            <p className="text-[11px] text-faint"
              title={"The app's arithmetic on these numbers; the research note sets the value grade"
                + (bothHints ? ' — with two hints the app takes the less favorable one' : '')}>
              App's grade: <span className="text-muted">{grade0}</span>
              {bothHints && (
                <> ({hint} on the reported figure · {nHint} on the
                  five-year average{vr!.now_normalized != null && <>, {fmtM(vr!.now_normalized, unit)}</>})</>
              )}
            </p>
          )}
        </>
      ) : (
        <>
          <p className="mt-1 text-xs text-faint">
            {vr && (vr.label || vr.measure) && <span className="text-muted">{measureLabel(vr)}{vr.now != null ? ' ' : ': '}</span>}{why}
          </p>
          {vr?.now != null && vr.now_basis && <p className="text-[11px] text-faint">{vr.now_basis}</p>}
          {vr && newListing && (
            <p className="text-[11px] text-faint">New listing — value is graded against peers, not its own history.</p>
          )}
        </>
      )}
      {basis && <p className="mt-1 text-xs leading-snug text-muted">{basis}</p>}
      <Notes notes={notes.slice(0, NOTES_SHOWN)} className="mt-1.5" />
      {notes.length > NOTES_SHOWN && (
        <Disclosure className="mt-1" label={`${notes.length - NOTES_SHOWN} more note${notes.length - NOTES_SHOWN === 1 ? '' : 's'} on the data`}>
          <Notes notes={notes.slice(NOTES_SHOWN)} className="mt-0" />
        </Disclosure>
      )}
    </div>
  )
}

// ─── risk axis ──────────────────────────────────────────────────────────────

const CHIP_TONE = {
  hard: 'border-down/30 bg-down/10 text-down',
  soft: 'border-warn/30 bg-warn/10 text-warn',
  watch: 'border-border-2 bg-surface-2 text-muted',
}

function RiskChip({ text, tone, onRule }: {
  text: string; tone: keyof typeof CHIP_TONE
  /** a soft signal's rule key: clicking also opens and highlights its rule check */
  onRule?: (key: string) => void
}) {
  const [open, setOpen] = useState(false)
  // soft signals start with their rule key in brackets — shown without it
  const { key, text: shown } = tone === 'soft' ? splitRuleKey(text) : { key: null, text }
  const ruleTip = key && onRule ? `\nRule: ${key} — click to see the app's check` : ''
  return (
    <button onClick={() => { setOpen((o) => !o); if (key && onRule) onRule(key) }} aria-expanded={open}
      title={(open ? 'Click to shorten' : shown) + ruleTip}
      data-rule={key ?? undefined}
      className={cn('max-w-full rounded-md border px-1.5 py-0.5 text-left text-[11px] leading-snug',
        CHIP_TONE[tone], open ? 'whitespace-normal' : 'truncate @3xl:max-w-[22rem]')}>
      {shown}
    </button>
  )
}

// ─── rule checks (the app's computations, spec §3/§6) ───────────────────────

const fmtVal = (v: unknown): string => {
  if (v == null) return '—'
  if (typeof v === 'number') return fmtNum(v, Math.abs(v) < 10 ? 2 : 1)
  if (typeof v === 'string') return v
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (Array.isArray(v)) return v.map(fmtVal).join(' · ')
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k} ${fmtVal(x)}`).join(' · ')
  }
  return String(v)
}

function RuleLine({ r, active, listed }: { r: RuleInput; active: boolean; listed: boolean }) {
  // not checked: the warnings say why ("no inventory line filed"); the basis is where from
  const why = strs(r.warnings)[0] || r.basis || 'no data'
  const marker = r.fires === true ? '⚠' : r.fires === false ? '✓' : '–'
  const tip = [r.threshold != null ? `Fires when: ${fmtVal(r.threshold)}` : '', r.basis, ...strs(r.warnings)]
    .filter(Boolean).join('\n') || undefined
  const shown = r.summary || (r.values != null ? fmtVal(r.values) : '')
  return (
    <li id={r.key ? `rule-${r.key}` : undefined} title={tip}
      className={cn('flex min-w-0 items-start gap-1.5 rounded px-1 py-0.5 text-[11px] leading-snug',
        r.fires === true ? 'bg-warn/10 text-warn' : 'text-faint',
        active && 'ring-1 ring-accent')}>
      <span aria-hidden className={cn('w-3 shrink-0 text-center', r.fires === false && 'text-muted')}>{marker}</span>
      <span className="min-w-0 break-words">
        <span className={r.fires === true ? 'font-medium' : 'text-muted'}>{r.label ?? r.key ?? 'Rule'}</span>
        {r.fires == null ? (
          <> — not checked: {why}</>
        ) : (
          <>
            {shown && <span className="tnum"> · {shown}</span>}
            {/* the label already states the rule; the exact threshold is printed
                only when there is no label (and is always on hover) */}
            {!r.label && r.threshold != null && r.threshold !== '' && <span> · fires if {fmtVal(r.threshold)}</span>}
            {listed && <span className="text-faint"> · listed in Soft</span>}
          </>
        )}
      </span>
    </li>
  )
}

function RuleChecks({ rules, open, setOpen, active, listedKeys, source }: {
  rules: RuleInput[]; open: boolean; setOpen: (o: boolean) => void
  active: string | null; listedKeys: Set<string>; source?: string
}) {
  if (!rules.length) {
    // the block came back empty (e.g. no filed quarters): say so, once
    return source ? <p className="mt-2 text-[11px] text-faint">Rule checks not computed — {source}</p> : null
  }
  const fires = rules.filter((r) => r.fires === true).length
  const unchecked = rules.filter((r) => r.fires == null).length
  return (
    <div className="mt-2">
      <button onClick={() => setOpen(!open)} aria-expanded={open}
        title={"The app's own checks of the default rulebook against filed statements — the research note's Soft list is checked against them"
          + (source ? `\n${source}` : '')}
        className={cn('text-[11px] underline decoration-dotted underline-offset-2 hover:text-text',
          fires ? 'text-warn' : 'text-faint')}>
        {open ? '▾' : '▸'} {fires} of {rules.length} rule check{rules.length === 1 ? '' : 's'} fire
        {unchecked > 0 && <span className="text-faint"> · {unchecked} not checked</span>}
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5">
          {rules.map((r, i) => (
            <RuleLine key={`${i}-${r.key ?? ''}`} r={r}
              active={!!r.key && r.key === active} listed={!!r.key && listedKeys.has(r.key)} />
          ))}
        </ul>
      )}
    </div>
  )
}

function RiskAxis({ grade, hard, soft, watch, g, rules, rulesSource }: {
  grade: string; hard: string[]; soft: string[]; watch: string[]; g: Record<string, GlossaryEntry>
  rules: RuleInput[]; rulesSource?: string
}) {
  const [rulesOpen, setRulesOpen] = useState(false)
  const [active, setActive] = useState<string | null>(null)
  const ruleKeys = new Set(rules.map((r) => r.key).filter((k): k is string => !!k))
  const listedKeys = new Set(soft.map((s) => splitRuleKey(s).key).filter((k): k is string => !!k))
  const onRule = (key: string) => {
    if (!ruleKeys.has(key)) return
    setRulesOpen(true)
    setActive(key)
    // after the list renders
    requestAnimationFrame(() =>
      document.getElementById(`rule-${key}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
  }
  const groups = [
    { key: 'hard' as const, label: 'Hard', tip: 'A confirmed finding or event — any one sets Avoid', items: hard, entry: g.hard_flags },
    { key: 'soft' as const, label: 'Soft', tip: 'A confirmed fact about the business — 2 drop the verdict one step, 3 set Avoid', items: soft, entry: g.soft_signals },
    { key: 'watch' as const, label: 'Watch-only', tip: 'Unconfirmed or still open — never changes the verdict', items: watch, entry: g.watch_items },
  ]
  const any = hard.length + soft.length + watch.length > 0
  return (
    <div className="min-w-0">
      <h4 className="text-[11px] uppercase tracking-wide text-faint">
        Risk{grade && <span className="ml-1.5 normal-case tracking-normal text-text">· {grade}</span>}
        {g.risk_grade && <GlossaryTip entry={g.risk_grade} />}
      </h4>
      {any ? (
        <div className="mt-1 space-y-1">
          {groups.filter((g) => g.items.length).map((g) => (
            <div key={g.key} className="flex min-w-0 items-start gap-1.5">
              <span className="w-16 shrink-0 pt-0.5 text-[11px] text-faint" title={g.tip}>
                {g.label} ({g.items.length}){g.entry && <GlossaryTip entry={g.entry} />}
              </span>
              <div className="flex min-w-0 flex-1 flex-wrap gap-1">
                {g.items.map((t, i) => (
                  <RiskChip key={`${i}-${t}`} text={t} tone={g.key}
                    onRule={g.key === 'soft' ? onRule : undefined} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-1 text-xs text-faint">No hard flags, soft signals or watch items recorded.</p>
      )}
      {(rules.length > 0 || !!rulesSource) && (
        <RuleChecks rules={rules} open={rulesOpen} setOpen={setRulesOpen} active={active} listedKeys={listedKeys}
          source={rulesSource} />
      )}
    </div>
  )
}

/** Rule checks beside an ungraded Risk column (earlier-format note, or no note yet). */
function StandaloneRules({ rules, source }: { rules: RuleInput[]; source?: string }) {
  const [open, setOpen] = useState(false)
  if (!rules.length && !source) return null
  return <RuleChecks rules={rules} open={open} setOpen={setOpen} active={null} listedKeys={new Set()} source={source} />
}

// ─── the card ───────────────────────────────────────────────────────────────

export function VerdictCard({ a, g, trust, trustReason, onAskClaude }: {
  a: Analysis; g: Record<string, GlossaryEntry>
  trust: Trust | null; trustReason?: string | null
  onAskClaude: () => void
}) {
  const cs = a.claude_stance
  const sf = a.report_view?.fields?.claude_stance
  const vr = a.valuation_range
  if (!cs && !vr) return null

  const v = readVerdict(cs ? { ...sf, ...cs } : null)
  const meta = cs?.research_meta
  const hard = strs(cs?.hard_flags), soft = strs(cs?.soft_signals), watch = strs(cs?.watch_items)
  // under a year (stocks, banks): the verdict cap; valuation against peers
  const capped = newListingCapped(a)
  const newListing = isNewListing(a) || capped
  const how = v.verdict ? howItWasSet(v, { hard: hard.length, soft: soft.length, watch: watch.length }, capped,
    cs?.expected_verdict) : ''
  const owner = ownerParts(v.verdict, stanceOwned(cs) ?? a.position?.owned ?? null, cs?.owner_line,
    cs?.owner_note ?? sf?.owner_note)
  // { rules: [...], as_of, filed, status } — or a bare list from an older shape
  const rulesRaw = a.rule_inputs ?? cs?.rule_inputs
  const rulesList = Array.isArray(rulesRaw) ? rulesRaw : rulesRaw?.rules
  const rules: RuleInput[] = Array.isArray(rulesList)
    ? rulesList.filter((r): r is RuleInput => !!r && typeof r === 'object') : []
  const rulesMeta = rulesRaw && !Array.isArray(rulesRaw) ? rulesRaw : null
  const rulesSource = rulesMeta
    ? [rulesMeta.as_of && `quarters to ${rulesMeta.as_of}`, rulesMeta.filed && `filed ${rulesMeta.filed}`,
       ...strs(rulesMeta.warnings)].filter(Boolean).join(' · ')
    : ''
  const up = strs(sf?.upgrade_if ?? meta?.upgrade_if)
  const down = strs(sf?.downgrade_if ?? meta?.downgrade_if)
  const reviewBy = sf?.review_by ?? meta?.review_by
  const confidence = sf?.confidence ?? meta?.stance_confidence
  const warnings = strs(sf?.warnings ?? cs?.warnings)
  const note = (v.verdict ? cs?.verdict_note : null) ?? cs?.stance_note

  // why an Unrated verdict is unrated
  const unratedWhy = v.verdict === 'unrated'
    ? (v.value === 'paused'
        ? 'Value paused — the valuation measure cannot be computed reliably (see Value)'
        : (sf?.research_status ?? meta?.research_status) === 'failed'
          ? 'The research session failed'
          : note || 'Too little clean data to apply the rulebook')
    : ''

  const summary = v.verdict ? v.label : cs ? `${cs.label ?? cs.stance.toUpperCase()} · earlier format` : undefined

  return (
    <SectionShell title="Verdict" glossary={g.verdict} summary={summary}
      right={cs && (
        <span className="flex items-center gap-1.5">
          <TrustWord trust={trust} reason={cs.status_reason ?? trustReason} />
          {trust && g.trust && <GlossaryTip entry={g.trust} />}
          <span className="tnum text-[11px] text-faint">{fmtDate(cs.created_ts.slice(0, 10))}</span>
        </span>
      )}>
      <div className="grid grid-cols-1 gap-x-6 gap-y-3 @3xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.2fr)]">
        {/* the call, how it was set, what it means for you */}
        <div className="min-w-0 space-y-1.5">
          {cs ? (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {v.verdict ? (
                <>
                  <VerdictPill verdict={v.verdict} className="text-sm" />
                  {v.axes && <span className="text-[13px] text-muted">({v.axes})</span>}
                </>
              ) : (
                <EarlierStance cs={cs} word={cs.label ?? cs.stance.toUpperCase()} />
              )}
            </div>
          ) : (
            <p className="text-xs text-faint">
              No research note yet —{' '}
              <button onClick={onAskClaude}
                className="underline decoration-dotted underline-offset-2 hover:text-accent">ask Claude</button>
              {' '}for a verdict.
            </p>
          )}
          {unratedWhy && (
            <p className="rounded-md border border-dashed border-border-2 px-2 py-1 text-xs text-muted">
              Unrated: {unratedWhy}
            </p>
          )}
          {note && !unratedWhy && (
            <p className="line-clamp-3 text-xs leading-snug text-muted" title={note}>{note}</p>
          )}
          {how && !unratedWhy && (
            <p className="text-[11px] text-faint"><span className="text-muted">How it was set:</span> {how}</p>
          )}
          {cs && !v.verdict && (
            <p className="text-[11px] text-faint">
              Written before the verdict format — the next research note will carry a verdict.
            </p>
          )}
          {(owner.line || owner.note) && (
            <p className="text-xs leading-snug">
              <span className="text-muted">For you:</span> <span className="text-text">{owner.line}</span>
              {owner.note && (
                <span className="text-muted" title="From your own rules — they never change the verdict">
                  {owner.line ? ' — ' : ''}{owner.note}
                </span>
              )}
            </p>
          )}
          {(reviewBy || confidence) && (
            <p className="text-[11px] text-faint">
              {reviewBy && <>Review by <span className="text-muted">{fmtDate(reviewBy)}</span></>}
              {reviewBy && confidence && ' · '}
              {confidence && <>confidence <span className="text-muted">{confidence}</span></>}
            </p>
          )}
          {(up.length > 0 || down.length > 0) && (
            <Disclosure label={`What would change it (${up.length} up · ${down.length} down)`}>
              <div className="space-y-1.5 pl-4 text-xs leading-snug">
                {up.length > 0 && (
                  <div>
                    <span className="text-faint">Upgrade if:</span>
                    <ul className="list-disc pl-4 text-muted">{up.map((t, i) => <li key={`${i}-${t}`}>{t}</li>)}</ul>
                  </div>
                )}
                {down.length > 0 && (
                  <div>
                    <span className="text-faint">Downgrade if:</span>
                    <ul className="list-disc pl-4 text-muted">{down.map((t, i) => <li key={`${i}-${t}`}>{t}</li>)}</ul>
                  </div>
                )}
              </div>
            </Disclosure>
          )}
        </div>

        {/* an earlier-format note with no valuation range has neither axis:
            one line says so instead of two empty columns */}
        {!v.verdict && !vr ? (
          <div className="text-xs text-faint @3xl:col-span-2">
            <p>
              No value or risk grades — earlier-format notes carry neither, and this
              snapshot has no valuation range.
            </p>
            <StandaloneRules rules={rules} source={rulesSource} />
          </div>
        ) : (
          <>
            <ValueAxis vr={vr} newListing={newListing}
              grade={v.value ? VALUE_WORD[v.value] : ''}
              basis={cs?.value_basis} tip={g.value_grade} />
            {/* the risk lists exist on v6 notes only */}
            {v.verdict ? (
              <RiskAxis grade={v.risk ? RISK_WORD[v.risk] : ''} hard={hard} soft={soft} watch={watch} g={g}
                rules={rules} rulesSource={rulesSource} />
            ) : cs || rules.length || rulesSource ? (
              <div className="min-w-0">
                <h4 className="text-[11px] uppercase tracking-wide text-faint">Risk</h4>
                <p className="mt-1 text-xs text-faint">
                  {cs ? 'Not graded — earlier-format notes carry no Hard / Soft / Watch-only lists.'
                    : 'Not graded — no research note yet.'}
                </p>
                <StandaloneRules rules={rules} source={rulesSource} />
              </div>
            ) : null}
          </>
        )}
      </div>
      {/* the app's rulebook checks (the header's ⚠ re-check), printed once */}
      <Notes notes={warnings} />
    </SectionShell>
  )
}
