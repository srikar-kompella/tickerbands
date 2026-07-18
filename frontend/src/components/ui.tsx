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
      'rounded-xl border border-border bg-surface p-4',
      hover && 'card-hover', className)}>
      {children}
    </div>
  )
}

export function CardTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h3 className="text-sm font-semibold tracking-wide text-muted uppercase">{children}</h3>
      {right}
    </div>
  )
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

export function Button({ children, onClick, variant = 'default', disabled, className, title }: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'accent' | 'ghost' | 'danger'
  disabled?: boolean
  className?: string
  title?: string
}) {
  const variants = {
    default: 'border border-border bg-surface hover:bg-surface-2 text-text',
    accent: 'border border-accent/40 bg-accent/15 hover:bg-accent/25 text-accent',
    ghost: 'text-muted hover:text-text hover:bg-surface-2',
    danger: 'border border-down/30 bg-down/10 hover:bg-down/20 text-down',
  }
  return (
    <button
      title={title}
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

export function SectionShell({ title, envelope, glossary, children, className, collapsible = true }: {
  title: string
  envelope?: SectionEnvelope
  glossary?: GlossaryEntry
  children: ReactNode
  className?: string
  collapsible?: boolean
}) {
  // Collapsed state persists per section (localStorage) — collapsing a card
  // you don't care about should stick across tickers and restarts. The
  // masonry column layout reflows around it automatically (ask #4).
  const storageKey = `tl-collapsed:${title}`
  const [collapsed, setCollapsed] = useState(
    () => collapsible && localStorage.getItem(storageKey) === '1')
  const toggle = () => {
    setCollapsed((c) => {
      localStorage.setItem(storageKey, c ? '0' : '1')
      return !c
    })
  }

  if (envelope?.status === 'disabled') return null // feature-flagged off

  return (
    <motion.div layout transition={{ type: 'spring', duration: 0.4, bounce: 0.12 }}>
      <Card className={className} hover>
        <div className="flex items-center justify-between gap-2">
          <button
            onClick={collapsible ? toggle : undefined}
            aria-expanded={!collapsed}
            className={cn('flex flex-1 items-center gap-1.5 text-left',
              collapsible && 'cursor-pointer')}>
            {collapsible && (
              <ChevronRight className={cn(
                'h-3.5 w-3.5 text-faint transition-transform', !collapsed && 'rotate-90')} />
            )}
            <h3 className="text-sm font-semibold tracking-wide text-muted uppercase">{title}</h3>
          </button>
          <span className="flex items-center gap-1.5">
            {envelope?.status === 'ok' && envelope.age_seconds != null && (
              <span className="text-[10px] text-faint tnum">{fmtAge(envelope.age_seconds)}</span>
            )}
            {envelope?.status === 'stale' && (
              <Badge tone="warn" className="text-[10px]">
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
              transition={{ duration: 0.22 }}
              className="overflow-hidden">
              <div className="pt-3">
                {envelope?.status === 'unavailable' ? (
                  <p className="py-2 text-sm text-faint">
                    Temporarily unavailable
                    {envelope.error_reason ? ` (${envelope.error_reason.replace('_', ' ')})` : ''}.
                  </p>
                ) : children}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
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
