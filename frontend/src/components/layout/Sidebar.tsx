import { BookOpen, Briefcase, Compass, Eye, LineChart, MessagesSquare, Settings2, Telescope } from 'lucide-react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

export const TABS = [
  { key: 'analysis', label: 'Analysis', icon: LineChart },
  { key: 'portfolio', label: 'Portfolio', icon: Briefcase },
  { key: 'discover', label: 'Discover', icon: Compass },
  { key: 'watchlist', label: 'Watchlist', icon: Eye },
  { key: 'discussions', label: 'Discussions', icon: MessagesSquare },
  { key: 'settings', label: 'Settings', icon: Settings2 },
  { key: 'guide', label: 'Guide', icon: BookOpen },
] as const

export type TabKey = (typeof TABS)[number]['key']

export function Sidebar({ tab, setTab, tabs = TABS, userName }: {
  tab: TabKey; setTab: (t: TabKey) => void
  tabs?: ReadonlyArray<(typeof TABS)[number]>
  userName?: string
}) {
  // Below 1024px the rail collapses to icons (labels become tooltips) so the
  // content keeps a usable width on a small laptop or a split screen.
  return (
    <aside className="flex w-48 shrink-0 flex-col border-r border-border bg-surface max-lg:w-14">
      <div className="flex items-center gap-2 px-4 pb-4 pt-5 max-lg:justify-center max-lg:px-0">
        <Telescope className="h-6 w-6 shrink-0 text-accent" />
        <div className="min-w-0 max-lg:hidden">
          <div className="text-base font-bold leading-none">TickerBands</div>
          <div className="mt-1 truncate text-[11px] text-faint">
            {userName ? `${userName}’s Portfolio` : 'ranges, not predictions'}
          </div>
        </div>
      </div>
      <nav className="flex flex-col gap-1 px-2" role="tablist" aria-label="Main navigation">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            aria-label={label}
            title={label}
            onClick={() => setTab(key)}
            className={cn(
              'relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors max-lg:justify-center max-lg:px-0',
              tab === key ? 'text-text' : 'text-muted hover:bg-surface-2 hover:text-text')}>
            {tab === key && (
              <motion.span
                layoutId="active-tab"
                className="absolute inset-0 rounded-lg bg-surface-3"
                transition={{ type: 'spring', duration: 0.4, bounce: 0.2 }}
              />
            )}
            <Icon className="relative z-10 h-4 w-4 shrink-0" />
            <span className="relative z-10 max-lg:hidden">{label}</span>
          </button>
        ))}
      </nav>
      <div className="mt-auto px-4 py-4 text-[11px] leading-relaxed text-faint max-lg:hidden">
        Research &amp; education only. Not investment advice. This tool does not
        predict direction — by design.
      </div>
    </aside>
  )
}
