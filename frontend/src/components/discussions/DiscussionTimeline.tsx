// Discussion timeline + expandable cards. New items animate in when the SSE
// event lands. Bodies render as real formatted markdown (react-markdown is
// XSS-safe by default) — the raw file stays the source of truth on disk.
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, FileText, MessagesSquare } from 'lucide-react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { fmtDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge, Card, EmptyState } from '@/components/ui'
import {
  EARLIER_TIP, TRUST_WORD, VerdictPill, discussionTrust, discussionVerdictSource, readVerdict, trustMeaning,
} from '@/components/verdict'
import type { Discussion, Trust } from '@/types'

// Can the note still be relied on? Same judgment the research prompt and the
// report give each note (backend research_quality.prior_note_status), shown
// with the trust labels of docs/VERDICT_SPEC.md §9: Current / Out of date /
// Don't use; a replaced note is folded away (toggle at the foot).
const TRUST_TAIL: Record<Trust, string> = {
  current: '',
  out_of_date: 'it cannot reflect that — re-run research',
  dont_use: 'do not build on this note',
  replaced: 'do not build on this note',
}

function statusLine(d: Discussion): string | null {
  const t = discussionTrust(d)
  if (!t || t === 'current') return null
  return `${TRUST_WORD[t]} — ${d.status_reason ?? 'no reason recorded'}; ${TRUST_TAIL[t]}`
}

function StatusChip({ d }: { d: Discussion }) {
  const asOf = d.status_as_of ? `Judged against the daily bars through ${d.status_as_of}.` : ''
  const t = discussionTrust(d)
  if (!t) {
    // not judged (no cached price history) — say so rather than imply Current
    return d.status_reason
      ? <Badge className="text-[11px]" title={d.status_reason}>unchecked</Badge>
      : null
  }
  if (t === 'current') {
    return (
      <Badge className="text-[11px]"
        title={`Current — ${trustMeaning(t)}: the research was complete and no ≥2σ session was left unexplained or has closed since. ${asOf}`}>
        Current
      </Badge>
    )
  }
  return (
    <Badge tone={t === 'out_of_date' ? 'warn' : t === 'replaced' ? 'neutral' : 'down'} className="text-[11px]"
      title={`${statusLine(d)}. ${asOf}`}>
      {t !== 'replaced' && '⚠ '}{TRUST_WORD[t]}
    </Badge>
  )
}

function DiscussionCard({ d, currentHash }: { d: Discussion; currentHash?: string }) {
  const [open, setOpen] = useState(false)
  const stale = !!currentHash && !!d.context_hash && d.context_hash !== currentHash
  const trust = discussionTrust(d)
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}>
      <Card className="cursor-pointer px-4 py-3 transition-colors hover:border-border-2" >
        <button onClick={() => setOpen((o) => !o)} className="w-full text-left" aria-expanded={open}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="accent">{d.ticker}</Badge>
                <span className="text-xs text-faint tnum">{fmtDateTime(d.created_ts)}</span>
                <StatusChip d={d} />
                {(() => {
                  // top-level verdict fields, else the parsed frontmatter
                  const v = readVerdict(discussionVerdictSource(d))
                  if (v.verdict) {
                    return (
                      <span className="inline-flex items-center gap-1">
                        <VerdictPill verdict={v.verdict} className="text-[11px]" title={v.label} />
                        {v.axes && <span className="text-[11px] text-muted">{v.axes}</span>}
                      </span>
                    )
                  }
                  return <span className="text-[11px] text-faint" title={EARLIER_TIP}>earlier format</span>
                })()}
                {d.tags.map((t, i) => <Badge key={`${i}-${t}`} className="text-[11px]">{t}</Badge>)}
                {stale && (
                  <Badge tone="warn" className="text-[11px]"
                    title="The analysis snapshot has changed since this discussion">
                    data moved since
                  </Badge>
                )}
                {new Date(d.file_mtime).getTime() - new Date(d.created_ts).getTime() > 5 * 60_000 && (
                  <Badge className="text-[11px]"
                    title={`File edited after creation — last touched ${fmtDateTime(d.file_mtime)}`}>
                    edited
                  </Badge>
                )}
              </div>
              {/* collapsed: two clipped lines; expanded: full summary wraps */}
              <p className={cn('mt-1.5 max-w-[110ch] text-[13px] font-medium leading-snug',
                !open && 'line-clamp-2')}>
                {d.summary || 'Untitled discussion'}
              </p>
              {/* Collapsed: the note-specific REASON on one truncated line (why
                  this note is INVALID/STALE is not visible from the badge alone,
                  and hover does not exist on touch). The fixed tail ("do not
                  build on this note") is the same for every note, so the full
                  sentence is printed only when the card opens. */}
              {statusLine(d) && (
                <p className={cn('mt-1 text-[11px] leading-snug',
                  !open && 'truncate',
                  trust === 'out_of_date' ? 'text-warn' : trust === 'replaced' ? 'text-muted' : 'text-down')}
                  title={open ? undefined : statusLine(d) ?? undefined}>
                  ⚠ {open ? statusLine(d) : `${TRUST_WORD[trust!]} — ${d.status_reason ?? 'no reason recorded'}`}
                </p>
              )}
            </div>
            <ChevronDown className={cn('mt-1 h-4 w-4 shrink-0 text-muted transition-transform', open && 'rotate-180')} />
          </div>
        </button>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="overflow-hidden">
              <div className="md-body mt-3 border-t border-border pt-3">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {d.response_markdown}
                </ReactMarkdown>
              </div>
              <p className="mt-2 flex items-center gap-1 text-[11px] text-faint">
                <FileText className="h-3 w-3" /> {d.file_path}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
  )
}

export function DiscussionTimeline({ discussions, currentHash, emptyAction }: {
  discussions: Discussion[]
  currentHash?: string
  emptyAction?: React.ReactNode
}) {
  const [showReplaced, setShowReplaced] = useState(false)
  const replaced = discussions.filter((d) => discussionTrust(d) === 'replaced')
  const shown = showReplaced ? discussions : discussions.filter((d) => discussionTrust(d) !== 'replaced')
  if (!discussions.length) {
    return (
      <EmptyState
        icon={<MessagesSquare className="h-7 w-7" />}
        title="No discussions yet"
        body="Hit “Ask Claude” on any ticker, paste the prompt into the claude CLI, and the conversation lands here automatically."
        action={emptyAction}
      />
    )
  }
  return (
    <div className="space-y-2">
      <AnimatePresence>
        {shown.map((d) => (
          <DiscussionCard key={d.id} d={d} currentHash={currentHash} />
        ))}
      </AnimatePresence>
      {/* replaced notes stay reachable — folded, not dropped */}
      {!!replaced.length && (
        <button onClick={() => setShowReplaced((s) => !s)} aria-expanded={showReplaced}
          className="flex items-center gap-1 pt-1 text-xs text-faint hover:text-muted"
          title="Notes a later note replaced (listed in its supersedes) — kept for the record; do not build on them">
          <ChevronDown className={cn('h-3 w-3 transition-transform', showReplaced && 'rotate-180')} />
          {showReplaced ? 'Hide' : 'Show'} {replaced.length} replaced note{replaced.length === 1 ? '' : 's'}
        </button>
      )}
    </div>
  )
}
