import { BookOpen, Briefcase, Eye, LineChart, MessagesSquare, Settings2, Telescope } from 'lucide-react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

export const TABS = [
  { key: 'analysis', label: 'Analysis', icon: LineChart },
  { key: 'portfolio', label: 'Portfolio', icon: Briefcase },
  { key: 'watchlist', label: 'Watchlist', icon: Eye },
  { key: 'discussions', label: 'Discussions', icon: MessagesSquare },
  { key: 'settings', label: 'Settings', icon: Settings2 },
  { key: 'guide', label: 'Guide', icon: BookOpen },
] as const

export type TabKey = (typeof TABS)[number]['key']

export function Sidebar({ tab, setTab }: { tab: TabKey; setTab: (t: TabKey) => void }) {
  return (
    <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-surface max-md:w-14">
      <div className="flex items-center gap-2 px-4 py-5 max-md:justify-center max-md:px-0">
        <Telescope className="h-6 w-6 text-accent" />
        <div className="max-md:hidden">
          <div className="text-base font-bold leading-none">TickerLens</div>
          <div className="mt-1 text-[10px] text-faint">ranges, not predictions</div>
        </div>
      </div>
      <nav className="flex flex-col gap-1 px-2" role="tablist" aria-label="Main navigation">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              'relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors max-md:justify-center max-md:px-0',
              tab === key ? 'text-text' : 'text-muted hover:bg-surface-2 hover:text-text')}>
            {tab === key && (
              <motion.span
                layoutId="active-tab"
                className="absolute inset-0 rounded-lg bg-surface-3"
                transition={{ type: 'spring', duration: 0.4, bounce: 0.2 }}
              />
            )}
            <Icon className="relative z-10 h-4 w-4 shrink-0" />
            <span className="relative z-10 max-md:hidden">{label}</span>
          </button>
        ))}
      </nav>
      <div className="mt-auto px-4 py-4 text-[10px] leading-relaxed text-faint max-md:hidden">
        Research &amp; education only. Not investment advice. This tool does not
        predict direction — by design.
      </div>
    </aside>
  )
}
