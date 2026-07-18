// ⌘K palette (cmdk): jump to any ticker or tab from anywhere — including
// live name→ticker search ("apple" → AAPL).
import { Command } from 'cmdk'
import { AnimatePresence, motion } from 'framer-motion'
import { BookOpen, Briefcase, Building2, Eye, LineChart, MessagesSquare, Search, Settings2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSymbolSearch, useWatchlist } from '@/hooks'
import type { TabKey } from './Sidebar'

export function CommandPalette({ onTicker, onTab }: {
  onTicker: (symbol: string) => void
  onTab: (tab: TabKey) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const { data: watchlist } = useWatchlist()
  const { data: searchHits } = useSymbolSearch(open ? query : '')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const go = (fn: () => void) => {
    fn()
    setOpen(false)
    setQuery('')
  }

  // Free-form ticker entry: whatever's typed, offer "Analyze XYZ"
  const typed = query.trim().toUpperCase()
  const validTicker = /^[A-Z.]{1,10}$/.test(typed)

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-[15vh] backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onClick={() => setOpen(false)}>
          <motion.div
            className="w-full max-w-lg overflow-hidden rounded-2xl border border-border-2 bg-surface shadow-2xl"
            initial={{ opacity: 0, scale: 0.97, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -8 }}
            transition={{ duration: 0.15 }}
            onClick={(e) => e.stopPropagation()}>
            <Command label="Command palette" shouldFilter={true}>
              <div className="flex items-center gap-2 border-b border-border px-4">
                <Search className="h-4 w-4 text-muted" />
                <Command.Input
                  autoFocus
                  value={query}
                  onValueChange={setQuery}
                  placeholder="Type a ticker or command…"
                  className="w-full bg-transparent py-3.5 text-sm outline-none placeholder:text-faint"
                />
                <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] text-faint">esc</kbd>
              </div>
              <Command.List className="max-h-72 overflow-y-auto p-2">
                <Command.Empty className="px-3 py-6 text-center text-sm text-faint">
                  Nothing matches.
                </Command.Empty>

                {validTicker && (
                  <Command.Group heading="Analyze" className="text-[10px] uppercase tracking-wide text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                    <Command.Item
                      value={`analyze-${typed}`}
                      onSelect={() => go(() => onTicker(typed))}
                      className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-text data-[selected=true]:bg-accent/15 data-[selected=true]:text-accent">
                      <LineChart className="h-4 w-4" />
                      Analyze <span className="font-bold">{typed}</span>
                    </Command.Item>
                  </Command.Group>
                )}

                {!!searchHits?.length && (
                  <Command.Group heading="Companies" className="text-[10px] uppercase tracking-wide text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                    {searchHits.map((h) => (
                      <Command.Item
                        key={`hit-${h.symbol}`}
                        value={`company-${h.symbol} ${h.description} ${query}`}
                        onSelect={() => go(() => onTicker(h.symbol))}
                        className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-text data-[selected=true]:bg-accent/15 data-[selected=true]:text-accent">
                        <Building2 className="h-4 w-4" />
                        <span className="font-semibold">{h.symbol}</span>
                        <span className="truncate text-xs text-faint">{h.description}</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}

                {!!watchlist?.length && (
                  <Command.Group heading="Watchlist" className="text-[10px] uppercase tracking-wide text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                    {watchlist.map((w) => (
                      <Command.Item
                        key={w.symbol}
                        value={`watch-${w.symbol} ${w.note}`}
                        onSelect={() => go(() => onTicker(w.symbol))}
                        className="flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm text-text data-[selected=true]:bg-accent/15 data-[selected=true]:text-accent">
                        <span className="flex items-center gap-2">
                          <Eye className="h-4 w-4" /> {w.symbol}
                        </span>
                        <span className="max-w-48 truncate text-xs text-faint">{w.note}</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}

                <Command.Group heading="Go to" className="text-[10px] uppercase tracking-wide text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                  {([
                    ['analysis', 'Analysis', LineChart],
                    ['portfolio', 'Portfolio', Briefcase],
                    ['watchlist', 'Watchlist', Eye],
                    ['discussions', 'Discussions', MessagesSquare],
                    ['settings', 'Settings', Settings2],
                    ['guide', 'Guide', BookOpen],
                  ] as const).map(([key, label, Icon]) => (
                    <Command.Item
                      key={key}
                      value={`goto-${label}`}
                      onSelect={() => go(() => onTab(key))}
                      className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-text data-[selected=true]:bg-accent/15 data-[selected=true]:text-accent">
                      <Icon className="h-4 w-4" /> {label}
                    </Command.Item>
                  ))}
                </Command.Group>
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
