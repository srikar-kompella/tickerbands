// Search bar with name→ticker lookup ("apple" → AAPL): debounced dropdown,
// full keyboard nav (↑↓ Enter Esc), and an add-to-watchlist button on the
// right of every result row.
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowRight, Eye, Loader2, Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui'
import { useSymbolSearch, useWatchlist, useWatchlistMutations } from '@/hooks'
import { cn } from '@/lib/utils'

export function TickerSearch({ onAnalyze }: { onAnalyze: (symbol: string) => void }) {
  const [input, setInput] = useState('')
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)

  const { data: results, isFetching } = useSymbolSearch(input)
  const { data: watchlist } = useWatchlist()
  const { add } = useWatchlistMutations()

  const typed = input.trim().toUpperCase()
  const typedIsTicker = /^[A-Z.]{1,10}$/.test(typed)
  const rows = results ?? []

  // close on outside click
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [])

  useEffect(() => setHighlight(0), [input, rows.length])

  const pick = (symbol: string) => {
    onAnalyze(symbol)
    setInput('')
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setOpen(false); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setHighlight((h) => Math.min(h + 1, rows.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setHighlight((h) => Math.max(h - 1, 0)) }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (open && rows[highlight]) pick(rows[highlight].symbol)
      else if (typedIsTicker) pick(typed)
    }
  }

  return (
    <div ref={boxRef} className="relative max-w-md flex-1">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
          {isFetching && (
            <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-faint" />
          )}
          <input
            value={input}
            onChange={(e) => { setInput(e.target.value); setOpen(true) }}
            onFocus={() => input.trim().length >= 2 && setOpen(true)}
            onKeyDown={onKeyDown}
            placeholder="Ticker or company name… (⌘K anywhere)"
            aria-label="Search ticker or company name"
            role="combobox"
            aria-expanded={open && rows.length > 0}
            className="w-full rounded-xl border border-border bg-surface py-2 pl-9 pr-9 text-sm outline-none transition-colors placeholder:text-faint focus:border-accent/60"
          />
        </div>
        <Button variant="accent" onClick={() => typedIsTicker && pick(typed)} disabled={!typedIsTicker}>
          Analyze <ArrowRight className="h-4 w-4" />
        </Button>
      </div>

      <AnimatePresence>
        {open && rows.length > 0 && (
          <motion.ul
            role="listbox"
            className="absolute z-40 mt-1.5 w-full overflow-hidden rounded-xl border border-border-2 bg-surface-2 shadow-2xl"
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.12 }}>
            {rows.map((r, i) => {
              const watched = !!watchlist?.some((w) => w.symbol === r.symbol)
              return (
                <li key={r.symbol}
                  role="option"
                  aria-selected={i === highlight}
                  onMouseEnter={() => setHighlight(i)}
                  className={cn('flex cursor-pointer items-center gap-3 px-3 py-2',
                    i === highlight && 'bg-accent/10')}>
                  <button onClick={() => pick(r.symbol)} className="flex min-w-0 flex-1 items-baseline gap-2 text-left">
                    <span className="font-bold">{r.symbol}</span>
                    <span className="truncate text-sm text-muted">{r.description}</span>
                    <span className="ml-auto shrink-0 text-[10px] uppercase text-faint">{r.type}</span>
                  </button>
                  {/* the requested right-side add-to-watchlist button */}
                  <button
                    title={watched ? 'Already on watchlist' : `Add ${r.symbol} to watchlist`}
                    disabled={watched}
                    onClick={(e) => { e.stopPropagation(); add.mutate({ symbol: r.symbol }) }}
                    className={cn('shrink-0 rounded-md border px-1.5 py-1 text-xs',
                      watched
                        ? 'cursor-default border-border text-faint'
                        : 'border-accent/40 text-accent hover:bg-accent/15')}>
                    <Eye className="h-3.5 w-3.5" />
                  </button>
                </li>
              )
            })}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  )
}
