// Lean UI primitives (shadcn-flavored, hand-rolled to stay small).
// Everything themable rides on the tokens in index.css.
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, HelpCircle, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { fmtAge } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { GlossaryEntry, SectionEnvelope } from '@/types'

// ─── Card ──────────────────────────────────────────────────────────────────────

export function Card({ className, children, hover = false }: {
  className?: string; children: ReactNode; hover?: boolean
}) {
  return (
    <div className={cn(
      'min-w-0 rounded-xl border border-border bg-surface p-4',
      hover && 'card-hover', className)}>
      {children}
    </div>
  )
}

export function CardTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{children}</h3>
      {right}
    </div>
  )
}

// ─── Stat (small label over a figure — headers and summary strips) ────────────

export function Stat({ label, tip, children, className, title }: {
  label: ReactNode; tip?: GlossaryEntry; children: ReactNode; className?: string; title?: string
}) {
  return (
    <div className={cn('min-w-0', className)} title={title}>
      <div className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-faint">
        {label}{tip && <GlossaryTip entry={tip} />}
      </div>
      <div className="mt-0.5 flex min-h-6 flex-wrap items-center gap-1.5 text-sm">{children}</div>
    </div>
  )
}

// ─── Rows, notes, disclosures (the declutter kit) ─────────────────────────────
//
// One caveat often applies to several rows ("statements unavailable" on three
// margins). The rule everywhere: the text is printed ONCE per section by
// <Notes>, each affected row carries a small ⚠ marker (hover shows the text),
// and rows with no value sit behind a "Show N unavailable" toggle together
// with the caveats that only concern them. Nothing is dropped.

export interface RowSpec {
  /** stable id for React lists (a report-field key); never rendered */
  id?: string
  label: string
  value: string
  tip?: GlossaryEntry
  valueClass?: string
  /** caveats on this row — marked here, printed once by Notes */
  warnings?: string[]
  /** no value to show; defaults to value === '—' */
  missing?: boolean
}

export interface Note { text: string; rows?: string[] }

const rowMissing = (r: RowSpec) => r.missing ?? r.value.trim() === '—'

export function WarnMark({ text }: { text: string }) {
  return (
    <span title={text} aria-label={`Caveat: ${text}`} role="img"
      className="cursor-help text-[11px] leading-none text-warn">⚠</span>
  )
}

// a value this long (a list of distributions, a cash-minus-debt breakdown)
// reads badly squeezed into the right-hand column — it gets its own line
const LONG_VALUE = 44

export function Row({ label, value, tip, valueClass, warnings }: RowSpec) {
  const long = value.length > LONG_VALUE
  return (
    <div className={cn('py-1.5 text-[13px] leading-5',
      !long && 'flex items-baseline justify-between gap-4')}>
      <span className="flex min-w-0 items-center gap-1 text-muted">
        <span className="min-w-0">{label}</span>
        {tip && <GlossaryTip entry={tip} />}
        {!!warnings?.length && <WarnMark text={warnings.join('\n')} />}
      </span>
      <span className={cn('tnum font-medium',
        long ? 'mt-0.5 block text-pretty' : 'max-w-[62%] shrink-0 text-right', valueClass)}>{value}</span>
    </div>
  )
}

/** Identical caveats collapse to one line that names every row it applies to. */
export function mergeNotes(notes: (Note | string)[]): Note[] {
  const out: Note[] = []
  for (const raw of notes) {
    const n = typeof raw === 'string' ? { text: raw } : raw
    const text = n.text.trim()
    if (!text) continue
    const hit = out.find((o) => o.text === text)
    if (!hit) out.push({ text, rows: [...(n.rows ?? [])] })
    else for (const r of n.rows ?? []) if (!hit.rows!.includes(r)) hit.rows!.push(r)
  }
  return out
}

export function Notes({ notes, tone = 'warn', className }: {
  notes: (Note | string)[]; tone?: 'warn' | 'info'; className?: string
}) {
  const merged = mergeNotes(notes)
  if (!merged.length) return null
  return (
    <ul className={cn('mt-2.5 space-y-1 rounded-lg px-2.5 py-2 text-xs leading-snug',
      tone === 'warn' ? 'bg-warn/[0.07] text-warn' : 'bg-accent/[0.07] text-accent', className)}>
      {merged.map((n) => (
        <li key={n.text} className="flex gap-1.5">
          <span aria-hidden className="shrink-0">{tone === 'warn' ? '⚠' : '•'}</span>
          <span className="min-w-0">
            {!!n.rows?.length && <span className="font-semibold">{n.rows.join(', ')}: </span>}
            {n.text}
          </span>
        </li>
      ))}
    </ul>
  )
}

const rowNotes = (rows: RowSpec[]): Note[] =>
  rows.flatMap((r) => (r.warnings ?? []).map((text) => ({ text, rows: [r.label] })))

export function RowList({ rows, notes = [] }: { rows: RowSpec[]; notes?: (Note | string)[] }) {
  const [showMissing, setShowMissing] = useState(false)
  const have = rows.filter((r) => !rowMissing(r))
  const missing = rows.filter(rowMissing)
  return (
    <div>
      {!!have.length && (
        <div className="divide-y divide-border/50">
          {have.map((r) => <Row key={r.id ?? r.label} {...r} />)}
        </div>
      )}
      <Notes notes={[...rowNotes(have), ...notes]} />
      {!!missing.length && (
        <>
          <button onClick={() => setShowMissing((s) => !s)} aria-expanded={showMissing}
            className="mt-2 flex items-center gap-1 text-xs text-faint hover:text-muted">
            <ChevronRight className={cn('h-3 w-3 transition-transform', showMissing && 'rotate-90')} />
            {showMissing ? 'Hide' : 'Show'} {missing.length} unavailable
          </button>
          {showMissing && (
            <div className="mt-1">
              <div className="divide-y divide-border/50 opacity-75">
                {missing.map((r) => <Row key={r.id ?? r.label} {...r} valueClass="text-faint" />)}
              </div>
              <Notes notes={rowNotes(missing)} />
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** Small text toggle for footnotes and "how to read this" copy. */
export function Disclosure({ label, children, defaultOpen = false, className }: {
  label: ReactNode; children: ReactNode; defaultOpen?: boolean; className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={className}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex items-center gap-1 text-left text-xs text-faint hover:text-muted">
        <ChevronRight className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-90')} />
        {label}
      </button>
      {open && <div className="mt-1.5">{children}</div>}
    </div>
  )
}

/** Fine print under a section: one size, one colour, readable line length. */
export function FinePrint({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn('mt-2.5 text-[11px] leading-relaxed text-faint', className)}>{children}</p>
}

// ─── Badge / Button ────────────────────────────────────────────────────────────

export function Badge({ children, tone = 'neutral', className, title }: {
  children: ReactNode
  tone?: 'neutral' | 'up' | 'down' | 'warn' | 'accent'
  className?: string
  title?: string
}) {
  const tones = {
    neutral: 'bg-surface-2 text-muted border-border',
    up: 'bg-up/10 text-up border-up/30',
    down: 'bg-down/10 text-down border-down/30',
    warn: 'bg-warn/10 text-warn border-warn/30',
    accent: 'bg-accent/10 text-accent border-accent/30',
  }
  return (
    <span title={title} className={cn('inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium', tones[tone], className)}>
      {children}
    </span>
  )
}

export function Button({
  children, onClick, variant = 'default', disabled, className, title, ariaLabel,
  id, ariaHasPopup, ariaExpanded, ariaControls,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'accent' | 'ghost' | 'danger'
  disabled?: boolean
  className?: string
  title?: string
  ariaLabel?: string
  // menu-button wiring (aria-haspopup / aria-expanded / aria-controls)
  id?: string
  ariaHasPopup?: 'menu' | 'dialog' | 'listbox' | true
  ariaExpanded?: boolean
  ariaControls?: string
}) {
  const variants = {
    default: 'border border-border bg-surface hover:bg-surface-2 text-text',
    accent: 'border border-accent/40 bg-accent/15 hover:bg-accent/25 text-accent',
    ghost: 'text-muted hover:text-text hover:bg-surface-2',
    danger: 'border border-down/30 bg-down/10 hover:bg-down/20 text-down',
  }
  return (
    <button
      id={id}
      title={title}
      aria-label={ariaLabel}
      aria-haspopup={ariaHasPopup}
      aria-expanded={ariaExpanded}
      aria-controls={ariaControls}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium',
        'transition-colors disabled:opacity-50 disabled:pointer-events-none',
        variants[variant], className)}>
      {children}
    </button>
  )
}

// ─── Skeleton (per-section loading — no bare spinners, per the polish bar) ────

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton', className)} />
}

export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <Card>
      <Skeleton className="mb-3 h-4 w-28" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={cn('mb-2 h-4', i % 2 ? 'w-3/4' : 'w-full')} />
      ))}
    </Card>
  )
}

// ─── Modal (framer-motion powered, Escape-closable, focus-friendly) ───────────

export function Modal({ open, onClose, title, children, wide = false }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onClick={onClose}>
          <motion.div
            role="dialog" aria-modal="true" aria-label={title}
            className={cn('max-h-[85vh] w-full overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-2xl',
              wide ? 'max-w-3xl' : 'max-w-lg')}
            initial={{ opacity: 0, scale: 0.96, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 12 }}
            transition={{ type: 'spring', duration: 0.3, bounce: 0.15 }}
            onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold">{title}</h2>
              <Button variant="ghost" onClick={onClose} title="Close (Esc)">
                <X className="h-4 w-4" />
              </Button>
            </div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

// ─── Glossary tooltip (ⓘ — plain-English everywhere, Q9) ──────────────────────

const TIP_WIDTH = 288 // px (w-72)
const TIP_PAD = 8

export function GlossaryTip({ entry }: { entry?: GlossaryEntry }) {
  // Rendered in a PORTAL with fixed positioning: an in-flow absolute tooltip
  // gets clipped by any overflow ancestor (score-card expansion, scroll
  // containers, narrow panels). Portal + viewport clamping fixes every widget
  // at once. Closes on scroll/resize (stale coordinates must never linger).
  const [pos, setPos] = useState<{ top: number; left: number; below: boolean } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  const openTip = () => {
    const r = btnRef.current?.getBoundingClientRect()
    if (!r) return
    const left = Math.min(
      Math.max(r.left + r.width / 2 - TIP_WIDTH / 2, TIP_PAD),
      window.innerWidth - TIP_WIDTH - TIP_PAD,
    )
    const below = r.top < 180 // not enough headroom → flip under the trigger
    setPos({ top: below ? r.bottom + TIP_PAD : r.top - TIP_PAD, left, below })
  }
  const close = () => setPos(null)

  useEffect(() => {
    if (!pos) return
    const onMove = () => setPos(null)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [pos])

  if (!entry) return null
  return (
    <>
      <button
        ref={btnRef}
        aria-label={`What is ${entry.term}?`}
        onMouseEnter={openTip} onMouseLeave={close}
        onFocus={openTip} onBlur={close}
        className="inline-flex text-faint hover:text-muted">
        <HelpCircle className="h-3.5 w-3.5" />
      </button>
      {createPortal(
        <AnimatePresence>
          {pos && (
            <motion.div
              role="tooltip"
              className="pointer-events-none fixed z-[60] rounded-lg border border-border-2 bg-surface-3 p-3 text-xs shadow-xl"
              style={{
                top: pos.top,
                left: pos.left,
                width: TIP_WIDTH,
                transform: pos.below ? undefined : 'translateY(-100%)',
              }}
              initial={{ opacity: 0, y: pos.below ? -4 : 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: pos.below ? -4 : 4 }}
              transition={{ duration: 0.15 }}>
              <span className="mb-1 block font-semibold text-text">{entry.term}</span>
              <span className="block text-muted">{entry.plain}</span>
              <span className="mt-1.5 block text-faint">{entry.why}</span>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  )
}

// ─── SectionShell — the Q12 degradation contract, rendered ─────────────────────

/** Why a section has nothing to show, in words ("no data" is not "broken"). */
export function unavailableText(envelope?: SectionEnvelope): string {
  const reason = envelope?.error_reason?.replace(/_/g, ' ')
  const detail = envelope?.error_detail
  if (envelope?.error_reason === 'no_data') return detail ? `No data — ${detail}.` : 'No data.'
  return `Temporarily unavailable${reason || detail
    ? ` (${[reason, detail].filter((x) => x && x !== 'unavailable').join(': ') || 'unavailable'})` : ''}.`
}

export function SectionShell({ title, envelope, glossary, children, className, collapsible = true,
  defaultCollapsed = false, right, summary }: {
  title: string
  envelope?: SectionEnvelope
  glossary?: GlossaryEntry
  children: ReactNode
  className?: string
  collapsible?: boolean
  /** low-priority sections start closed; the user's own choice still wins */
  defaultCollapsed?: boolean
  /** short summary shown in the title bar (still visible when collapsed) */
  right?: ReactNode
  /** the headline figure, shown in the title bar only while collapsed */
  summary?: ReactNode
}) {
  // Collapsed state persists per section (localStorage) — collapsing a card
  // you don't care about should stick across tickers and restarts.
  const storageKey = `tl-collapsed:${title}`
  const [collapsed, setCollapsed] = useState(() => {
    if (!collapsible) return false
    const saved = localStorage.getItem(storageKey)
    return saved == null ? defaultCollapsed : saved === '1'
  })
  const toggle = () => {
    setCollapsed((c) => {
      localStorage.setItem(storageKey, c ? '0' : '1')
      return !c
    })
  }

  if (envelope?.status === 'disabled') return null // feature-flagged off

  // No layout animation on the card itself: inside a scrolling, multi-column
  // page framer's projection left cards parked at stale offsets (blank gaps,
  // cards drawn off-screen). Only the body's height animates.
  return (
    <Card className={className}>
      <div className="flex items-center justify-between gap-2">
        <button
          onClick={collapsible ? toggle : undefined}
          aria-expanded={!collapsed}
          className={cn('flex min-w-0 flex-1 items-center gap-1.5 text-left',
            collapsible ? 'cursor-pointer' : 'cursor-default')}>
          {collapsible && (
            <ChevronRight className={cn(
              'h-3.5 w-3.5 shrink-0 text-faint transition-transform', !collapsed && 'rotate-90')} />
          )}
          <h3 className="truncate text-xs font-semibold tracking-wide text-muted uppercase">{title}</h3>
        </button>
        <span className="flex shrink-0 items-center gap-1.5">
          {collapsed && summary != null && (
            <span className="tnum text-[11px] text-muted">{summary}</span>
          )}
          {right}
          {envelope?.status === 'ok' && envelope.age_seconds != null && (
            <span className="text-[11px] text-faint tnum">{fmtAge(envelope.age_seconds)}</span>
          )}
          {envelope?.status === 'stale' && (
            <Badge tone="warn" className="text-[11px]">
              stale · {fmtAge(envelope.age_seconds)}
            </Badge>
          )}
          {glossary && <GlossaryTip entry={glossary} />}
        </span>
      </div>
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden">
            <div className="pt-2.5">
              {envelope?.status === 'unavailable' ? (
                <p className="py-1 text-[13px] text-faint">{unavailableText(envelope)}</p>
              ) : children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  )
}

// ─── EmptyState (illustrated, with CTA — never a blank page) ───────────────────

export function EmptyState({ icon, title, body, action }: {
  icon: ReactNode; title: string; body: string; action?: ReactNode
}) {
  return (
    <motion.div
      className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border py-16 text-center"
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      <div className="rounded-2xl bg-surface-2 p-4 text-accent">{icon}</div>
      <div>
        <p className="font-semibold">{title}</p>
        <p className="mx-auto mt-1 max-w-sm text-sm text-muted">{body}</p>
      </div>
      {action}
    </motion.div>
  )
}
