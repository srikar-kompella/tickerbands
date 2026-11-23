// The verdict (docs/VERDICT_SPEC.md): one ownership-independent call per
// security — `Stretched (Expensive · Watch)` — written by the research
// session from a fixed rulebook and checked by the app. This module holds the
// words, colours, the trust label and the small shared pieces (pill, trust
// word, owner line) used by the watchlist, the analysis header and the
// Verdict card. Every payload field is optional: an older backend or a note
// written before prompt v6 falls back to the old stance display.
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { ClaudeStance, Discussion, Trust, ValueGrade, Verdict, RiskGrade } from '@/types'

export const VERDICT_WORD: Record<Verdict, string> = {
  attractive: 'Attractive', fair: 'Fair', stretched: 'Stretched', avoid: 'Avoid', unrated: 'Unrated',
}
export const VALUE_WORD: Record<ValueGrade, string> = {
  cheap: 'Cheap', fair: 'Fair', expensive: 'Expensive', paused: 'Value paused',
}
export const RISK_WORD: Record<RiskGrade, string> = {
  clean: 'Clean', watch: 'Watch', red_flag: 'Red flag',
}
export const TRUST_WORD: Record<Trust, string> = {
  current: 'Current', out_of_date: 'Out of date', dont_use: "Don't use", replaced: 'Replaced',
}
const TRUST_MEANING: Record<Trust, string> = {
  current: 'fine to rely on',
  out_of_date: 'something happened since it was written — re-run research',
  dont_use: 'the research failed or missed a large move',
  replaced: 'a later note replaced it',
}

// best → worst; also the sort order on the watchlist
const STEPS: Verdict[] = ['attractive', 'fair', 'stretched', 'avoid']
export const VERDICT_RANK: Record<Verdict, number> = {
  attractive: 0, fair: 1, stretched: 2, avoid: 3, unrated: 4,
}
// spec §7: the legacy stance a v6 note maps to — used in reverse to rank
// earlier-format notes beside v6 ones
const STANCE_AS_VERDICT: Record<string, Verdict> = {
  buy: 'attractive', hold: 'fair', trim: 'stretched', sell: 'avoid', watch: 'unrated',
}

const asVerdict = (v: unknown): Verdict | null =>
  typeof v === 'string' && v in VERDICT_WORD ? (v as Verdict) : null
const asValue = (v: unknown): ValueGrade | null =>
  typeof v === 'string' && v in VALUE_WORD ? (v as ValueGrade) : null
const asRisk = (v: unknown): RiskGrade | null =>
  typeof v === 'string' && v in RISK_WORD ? (v as RiskGrade) : null
export const asTrust = (v: unknown): Trust | null =>
  typeof v === 'string' && v in TRUST_WORD ? (v as Trust) : null

/** Internal note status → trust label (spec §9), for payloads without `trust`. */
export function trustFromStatus(status?: string | null): Trust | null {
  switch ((status ?? '').toUpperCase()) {
    case 'OK': return 'current'
    case 'STALE': return 'out_of_date'
    case 'INVALID': return 'dont_use'
    case 'SUPERSEDED': return 'replaced'
    default: return null
  }
}

export const discussionTrust = (d: Pick<Discussion, 'trust' | 'status'>): Trust | null =>
  asTrust(d.trust) ?? trustFromStatus(d.status)

/**
 * A discussion row's verdict fields: top level (newer backend), else the
 * parsed frontmatter in `research_meta`. A row with no verdict, or one whose
 * prompt_version is below 6, is an earlier-format note.
 */
export function discussionVerdictSource(d: Discussion): VerdictSource {
  const m = (d.research_meta ?? {}) as Record<string, unknown>
  const s = (k: string): string | null => {
    const top = (d as unknown as Record<string, unknown>)[k]
    if (typeof top === 'string' && top) return top
    return typeof m[k] === 'string' && m[k] ? (m[k] as string) : null
  }
  const pv = typeof d.prompt_version === 'number' ? d.prompt_version
    : typeof m.prompt_version === 'number' ? (m.prompt_version as number) : null
  const verdict = s('verdict')
  const format = s('format') ?? (!verdict || (pv != null && pv < 6) ? 'earlier' : 'v6')
  return {
    verdict, value_grade: s('value_grade'), risk_grade: s('risk_grade'),
    verdict_label: s('verdict_label'), format, stance: s('stance'),
  }
}

/** Anything that carries the verdict fields — a stance, a screener row. */
interface VerdictSource {
  verdict?: string | null
  value_grade?: string | null
  risk_grade?: string | null
  verdict_label?: string | null
  format?: string | null
  stance?: string | null
}

export interface VerdictView {
  /** null = no v6 verdict: show the old stance (earlier format) */
  verdict: Verdict | null
  value: ValueGrade | null
  risk: RiskGrade | null
  earlier: boolean
  /** "(Expensive · Watch)" words, without the brackets */
  axes: string
  label: string
}

export function readVerdict(s: VerdictSource | null | undefined): VerdictView {
  const verdict = asVerdict(s?.verdict)
  const value = asValue(s?.value_grade)
  const risk = asRisk(s?.risk_grade)
  const earlier = !verdict || s?.format === 'earlier'
  const axes = [value && VALUE_WORD[value], risk && RISK_WORD[risk]].filter(Boolean).join(' · ')
  const label = s?.verdict_label
    || (verdict ? `${VERDICT_WORD[verdict]}${axes ? ` (${axes})` : ''}` : '')
  return { verdict: earlier ? null : verdict, value, risk, earlier, axes, label }
}

/** Sort rank: v6 verdict, else the earlier note's stance mapped (spec §7), else last. */
export function verdictSortRank(s: VerdictSource | null | undefined): number {
  const v = readVerdict(s)
  if (v.verdict) return VERDICT_RANK[v.verdict]
  const legacy = s?.stance ? STANCE_AS_VERDICT[s.stance] : undefined
  // an earlier-format note sorts just after the v6 notes with the same step
  return legacy ? VERDICT_RANK[legacy] + 0.5 : 9
}

// ─── what it means for the user (spec §5) ───────────────────────────────────

const IF_OWNED: Record<Verdict, string> = {
  attractive: 'Room to add', fair: 'Hold', stretched: 'Consider trimming',
  avoid: 'Consider exiting', unrated: 'Do not act on it',
}
const IF_NOT: Record<Verdict, string> = {
  attractive: 'Candidate to buy', fair: 'Wait', stretched: 'Pass',
  avoid: 'Pass', unrated: 'Do not act on it',
}

/** The backend's owner_line when sent; else derived from the table in spec §5. */
export function ownerLine(verdict: Verdict | null, owned: boolean | null | undefined,
  backend?: string | null): string {
  if (backend) return backend
  if (!verdict) return ''
  if (owned === true) return `You own it: ${IF_OWNED[verdict]}`
  if (owned === false) return `Not owned: ${IF_NOT[verdict]}`
  return IF_OWNED[verdict] === IF_NOT[verdict]
    ? IF_OWNED[verdict]
    : `If you own it: ${IF_OWNED[verdict]} · if not: ${IF_NOT[verdict]}`
}

/**
 * The derived owner line and the user's own-rules note, each once (spec §5).
 * An older backend appended the note to `owner_line` ("… — <note>"); a newer
 * one sends them as separate fields. Either way the note is printed once,
 * after the line.
 */
export function ownerParts(verdict: Verdict | null, owned: boolean | null | undefined,
  backendLine?: string | null, backendNote?: string | null): { line: string; note: string } {
  const note = (backendNote ?? '').trim()
  let line = ownerLine(verdict, owned, backendLine).trim()
  if (note && line.endsWith(note)) {
    line = line.slice(0, line.length - note.length).replace(/\s*[—–:;-]\s*$/, '').trim()
  }
  return { line, note }
}

/** "line — note" as one plain string (tooltips). */
export function ownerText(p: { line: string; note: string }): string {
  return [p.line, p.note].filter(Boolean).join(' — ')
}

export function stanceOwned(cs: ClaudeStance | null | undefined): boolean | null {
  if (!cs) return null
  if (typeof cs.account_owned === 'boolean') return cs.account_owned
  const o = cs.research_meta?.owned
  return o === 'yes' ? true : o === 'no' ? false : null
}

// ─── how it was set (spec §4, the arithmetic, for the one-line explainer) ───

const BASELINE: Partial<Record<ValueGrade, Verdict>> = {
  cheap: 'attractive', fair: 'fair', expensive: 'stretched',
}

export function howItWasSet(v: VerdictView, counts: { hard: number; soft: number; watch: number },
  /** stock or bank listed under a year (spec §3 cap) */
  newListing: boolean, expected?: string | null): string {
  if (!v.value) return ''
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
  if (v.value === 'paused') return 'Value paused → Unrated'
  let verdict = BASELINE[v.value]!
  const parts = [`${VALUE_WORD[v.value]} → ${VERDICT_WORD[verdict]}`]
  if (counts.hard) {
    verdict = 'avoid'
    parts.push(`${plural(counts.hard, 'hard flag')} → Avoid`)
  } else if (counts.soft >= 3) {
    verdict = 'avoid'
    parts.push(`${plural(counts.soft, 'soft signal')} → Avoid`)
  } else if (counts.soft === 2) {
    verdict = STEPS[Math.min(STEPS.indexOf(verdict) + 1, STEPS.length - 1)]
    parts.push(`2 soft signals: one step down → ${VERDICT_WORD[verdict]}`)
  } else if (counts.soft === 1) {
    parts.push('1 soft signal: no change')
  } else if (counts.watch) {
    parts.push(`${plural(counts.watch, 'watch item')}: no change`)
  } else {
    parts.push('no risk items')
  }
  if (newListing && verdict === 'attractive') {
    verdict = 'fair'
    parts.push('listed under a year: capped at Fair')
  }
  let line = parts.join('; ')
  // the backend's arithmetic wins where it differs from this explainer's
  const exp = asVerdict(expected)
  if (exp && exp !== verdict) {
    verdict = exp
    line += ` → expected ${VERDICT_WORD[exp]}`
  }
  if (v.verdict && v.verdict !== 'unrated' && v.verdict !== verdict) {
    line += ` — the note says ${VERDICT_WORD[v.verdict]} (see the checks)`
  }
  return line
}

// ─── pieces ─────────────────────────────────────────────────────────────────

const PILL: Record<Verdict, string> = {
  attractive: 'bg-up/10 text-up border-up/30',
  fair: 'bg-surface-2 text-text border-border-2',
  stretched: 'bg-warn/10 text-warn border-warn/30',
  avoid: 'bg-down/10 text-down border-down/30',
  unrated: 'bg-transparent text-muted border-border-2 border-dashed',
}

export function VerdictPill({ verdict, className, title }: {
  verdict: Verdict; className?: string; title?: string
}) {
  return (
    <span title={title}
      className={cn('inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs font-semibold',
        PILL[verdict], className)}>
      {VERDICT_WORD[verdict]}
    </span>
  )
}

/** The old stance word for a note written before v6, with the format hint. */
export const EARLIER_TIP =
  'Written before the verdict format (prompt v6): shown with its old stance until a new note replaces it'

export function EarlierStance({ cs, word, className, hint = true }: {
  cs?: { stance?: string | null } | null; word: ReactNode; className?: string
  /** print the "earlier format" hint beside the word (off where the caller places it) */
  hint?: boolean
}) {
  const s = cs?.stance
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className={cn('inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium',
        s === 'buy' ? 'border-up/30 bg-up/10 text-up'
          : s === 'sell' || s === 'trim' ? 'border-down/30 bg-down/10 text-down'
            : 'border-accent/30 bg-accent/10 text-accent')}>
        {word}
      </span>
      {hint && <span className="text-[11px] text-faint" title={EARLIER_TIP}>earlier format</span>}
    </span>
  )
}

const TRUST_CLASS: Record<Trust, string> = {
  current: 'text-muted',
  out_of_date: 'text-warn',
  dont_use: 'text-down',
  replaced: 'text-faint',
}

/** The trust label as a quiet coloured word; the reason on hover. */
export function TrustWord({ trust, reason, className }: {
  trust: Trust | null | undefined; reason?: string | null; className?: string
}): ReactNode {
  if (!trust) return null
  const tip = `${TRUST_WORD[trust]} — ${TRUST_MEANING[trust]}${reason ? `.\n${reason}` : ''}`
  return (
    <span title={tip} className={cn('text-[11px] font-medium', TRUST_CLASS[trust], className)}>
      {trust !== 'current' && '⚠ '}{TRUST_WORD[trust]}
    </span>
  )
}

export const trustMeaning = (t: Trust) => TRUST_MEANING[t]
