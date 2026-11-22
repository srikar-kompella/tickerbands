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
import type { Discussion } from '@/types'

// Can the note still be relied on? Same judgment the research prompt and the
// report give each note (backend research_quality.prior_note_status).
const STATUS_TAIL: Record<string, string> = {
  STALE: 'it cannot reflect that move',
  INVALID: 'do not build on this note',
  SUPERSEDED: 'do not build on this note',
}

function statusLine(d: Discussion): string | null {
  if (!d.status || d.status === 'OK') return null
  return `${d.status} — ${d.status_reason ?? 'no reason recorded'}; ${STATUS_TAIL[d.status]}`
}

function StatusChip({ d }: { d: Discussion }) {
  const asOf = d.status_as_of ? `Judged against the daily bars through ${d.status_as_of}.` : ''
  if (!d.status) {
    // not judged (no cached price history) — say so rather than imply OK
    return d.status_reason
      ? <Badge className="text-[11px]" title={d.status_reason}>unchecked</Badge>
      : null
  }
  if (d.status === 'OK') {
    return (
      <Badge className="text-[11px]"
        title={`No check failed: the research was complete and no ≥2σ session was left unexplained or has closed since. ${asOf}`}>
        OK
      </Badge>
    )
  }
  return (
    <Badge tone={d.status === 'STALE' ? 'warn' : 'down'} className="text-[11px]"
      title={`${statusLine(d)}. ${asOf}`}>
      ⚠ {d.status}
    </Badge>
  )
}

function DiscussionCard({ d, currentHash }: { d: Discussion; currentHash?: string }) {
  const [open, setOpen] = useState(false)
  const stale = !!currentHash && !!d.context_hash && d.context_hash !== currentHash
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
                {d.tags.map((t) => <Badge key={t} className="text-[11px]">{t}</Badge>)}
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
                  d.status === 'STALE' ? 'text-warn' : 'text-down')}
                  title={open ? undefined : statusLine(d) ?? undefined}>
                  ⚠ {open ? statusLine(d) : `${d.status} — ${d.status_reason ?? 'no reason recorded'}`}
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
        {discussions.map((d) => (
          <DiscussionCard key={d.id} d={d} currentHash={currentHash} />
        ))}
      </AnimatePresence>
    </div>
  )
}
