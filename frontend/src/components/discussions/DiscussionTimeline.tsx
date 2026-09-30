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

function DiscussionCard({ d, currentHash }: { d: Discussion; currentHash?: string }) {
  const [open, setOpen] = useState(false)
  const stale = !!currentHash && !!d.context_hash && d.context_hash !== currentHash
  return (
    <motion.div layout
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ type: 'spring', duration: 0.4, bounce: 0.15 }}>
      <Card hover className="cursor-pointer" >
        <button onClick={() => setOpen((o) => !o)} className="w-full text-left" aria-expanded={open}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="accent">{d.ticker}</Badge>
                <span className="text-xs text-faint tnum">{fmtDateTime(d.created_ts)}</span>
                {d.tags.map((t) => <Badge key={t} className="text-[10px]">{t}</Badge>)}
                {stale && (
                  <Badge tone="warn" className="text-[10px]"
                    title="The analysis snapshot has changed since this discussion">
                    data moved since
                  </Badge>
                )}
                {new Date(d.file_mtime).getTime() - new Date(d.created_ts).getTime() > 5 * 60_000 && (
                  <Badge className="text-[10px]"
                    title={`File edited after creation — last touched ${fmtDateTime(d.file_mtime)}`}>
                    edited
                  </Badge>
                )}
              </div>
              {/* collapsed: one clipped line; expanded: full summary wraps */}
              <p className={cn('mt-1.5 text-sm font-medium',
                open ? 'whitespace-normal' : 'truncate')}>
                {d.summary || 'Untitled discussion'}
              </p>
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
              <p className="mt-2 flex items-center gap-1 text-[10px] text-faint">
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
    <div className="space-y-3">
      <AnimatePresence>
        {discussions.map((d) => (
          <DiscussionCard key={d.id} d={d} currentHash={currentHash} />
        ))}
      </AnimatePresence>
    </div>
  )
}
