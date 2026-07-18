// App shell — routing + cross-cutting concerns only (thin by spec §6).
import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { Toaster } from 'sonner'
import { CommandPalette } from '@/components/layout/CommandPalette'
import { Sidebar, TABS, type TabKey } from '@/components/layout/Sidebar'
import { useDiscussionStream } from '@/hooks'
import Analysis from '@/pages/Analysis'
import Discussions from '@/pages/Discussions'
import Guide from '@/pages/Guide'
import Portfolio from '@/pages/Portfolio'
import Settings from '@/pages/Settings'
import Watchlist from '@/pages/Watchlist'

export default function App() {
  const [tab, setTab] = useState<TabKey>('analysis')
  const [symbol, setSymbol] = useState<string | null>(null)

  // SSE: discussions written by Claude CLI appear in the UI within a second.
  useDiscussionStream()

  const analyze = (s: string) => {
    setSymbol(s.toUpperCase())
    setTab('analysis')
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar tab={tab} setTab={setTab} />
      <main className="flex flex-1 flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* smooth page transitions between tabs (polish requirement) */}
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18 }}
              className="h-full">
              {tab === 'analysis' && <Analysis symbol={symbol} setSymbol={analyze} />}
              {tab === 'portfolio' && <Portfolio onAnalyze={analyze} />}
              {tab === 'watchlist' && <Watchlist onAnalyze={analyze} />}
              {tab === 'discussions' && <Discussions />}
              {tab === 'settings' && <Settings />}
              {tab === 'guide' && <Guide />}
            </motion.div>
          </AnimatePresence>
        </div>
        <footer className="border-t border-border px-6 py-1.5 text-[10px] text-faint">
          {TABS.find((t) => t.key === tab)?.label} · ⌘K to search ·
          research &amp; education only — no direction predictions, by design
        </footer>
      </main>
      <CommandPalette onTicker={analyze} onTab={setTab} />
      <Toaster theme="dark" position="bottom-right"
        toastOptions={{ style: { background: 'var(--color-surface-2)', border: '1px solid var(--color-border)', color: 'var(--color-text)' } }} />
    </div>
  )
}
