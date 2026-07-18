// The Analysis page: ticker in at the top → every section below.
import { motion } from 'framer-motion'
import { Search, Telescope } from 'lucide-react'
import { useState } from 'react'
import { TickerSearch } from '@/components/TickerSearch'
import { AskClaudeModal } from '@/components/discussions/AskClaudeModal'
import { DiscussionTimeline } from '@/components/discussions/DiscussionTimeline'
import {
  AnalystPanel, ContextPanel, EarningsPanel, FundamentalsPanel, InsidersPanel,
  NewsPanel, OptionsPanel, OwnershipPanel, ShortInterestPanel, SocialPanel,
} from '@/components/analysis/Panels'
import { PremiumLensCard } from '@/components/analysis/PremiumLensCard'
import { SetupScoreCard } from '@/components/analysis/SetupScoreCard'
import { SnapshotHeader } from '@/components/analysis/SnapshotHeader'
import { VolBandCard } from '@/components/analysis/VolBandCard'
import { Button, EmptyState, SkeletonCard } from '@/components/ui'
import {
  useAnalysis, useDiscussions, useGlossary, useRefreshAnalysis,
  useWatchlist, useWatchlistMutations,
} from '@/hooks'

const stagger = {
  hidden: { opacity: 0, y: 12 },
  show: (i: number) => ({
    opacity: 1, y: 0,
    transition: { delay: i * 0.05, type: 'spring' as const, duration: 0.45, bounce: 0.15 },
  }),
}

export default function Analysis({ symbol, setSymbol }: {
  symbol: string | null
  setSymbol: (s: string) => void
}) {
  const [askOpen, setAskOpen] = useState(false)
  const { data: a, isLoading, error } = useAnalysis(symbol)
  const refresh = useRefreshAnalysis(symbol)
  const { data: glossaryData } = useGlossary()
  const { data: watchlist } = useWatchlist()
  const { add, remove } = useWatchlistMutations()
  const { data: discussions } = useDiscussions(symbol ?? undefined)

  const glossary = glossaryData?.entries ?? {}
  const watched = !!watchlist?.some((w) => w.symbol === symbol)

  return (
    <div className="flex h-full flex-col">
      {/* ticker / company-name search */}
      <div className="mb-4 flex items-center gap-2">
        <TickerSearch onAnalyze={setSymbol} />
      </div>

      {!symbol && (
        <EmptyState
          icon={<Telescope className="h-7 w-7" />}
          title="Pick a ticker to research"
          body="Price, a calibrated 80% range for tomorrow, fundamentals, positioning, sentiment — plain English, no direction predictions."
        />
      )}

      {symbol && isLoading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} lines={i % 2 ? 4 : 3} />)}
        </div>
      )}

      {symbol && !!error && !isLoading && (
        <EmptyState
          icon={<Search className="h-7 w-7" />}
          title={`Couldn't load ${symbol}`}
          body={(error as Error).message}
          action={<Button onClick={() => refresh.mutate()}>Try again</Button>}
        />
      )}

      {a && (
        <>
          <SnapshotHeader
            a={a}
            glossary={glossary}
            onRefresh={() => refresh.mutate()}
            refreshing={refresh.isPending}
            onAskClaude={() => setAskOpen(true)}
            watched={watched}
            onToggleWatch={() =>
              watched ? remove.mutate(a.symbol) : add.mutate({ symbol: a.symbol })}
          />

          {/* hero chart full-width, then masonry columns: variable-height
              cards pack tightly and REFLOW when any card collapses/expands
              (ask #4 — break-inside-avoid keeps cards whole) */}
          <div className="pt-4">
            <motion.div custom={0} variants={stagger} initial="hidden" animate="show" className="mb-4">
              <VolBandCard a={a} glossary={glossary} />
            </motion.div>
            <motion.div custom={1} variants={stagger} initial="hidden" animate="show" className="mb-4">
              <PremiumLensCard a={a} glossary={glossary} />
            </motion.div>
            <div className="columns-1 gap-4 md:columns-2 xl:columns-3">
              {[
                <SetupScoreCard key="score" a={a} glossary={glossary} />,
                <ContextPanel key="ctx" a={a} g={glossary} />,
                <EarningsPanel key="earn" a={a} g={glossary} />,
                <OwnershipPanel key="own" a={a} g={glossary} />,
                <OptionsPanel key="opts" a={a} g={glossary} />,
                <NewsPanel key="news" a={a} g={glossary} />,
                <InsidersPanel key="insiders" a={a} g={glossary} />,
                <FundamentalsPanel key="fund" a={a} g={glossary} />,
                <ShortInterestPanel key="si" a={a} g={glossary} />,
                <SocialPanel key="social" a={a} g={glossary} />,
                <AnalystPanel key="recs" a={a} g={glossary} />,
              ].map((panel, i) => (
                <motion.div key={panel.key} custom={i + 1} variants={stagger}
                  initial="hidden" animate="show"
                  className="mb-4 break-inside-avoid">
                  {panel}
                </motion.div>
              ))}
            </div>
          </div>

          {/* this ticker's discussion history, inline */}
          <div className="mt-6">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
              Claude discussions · {a.symbol}
            </h3>
            <DiscussionTimeline
              discussions={discussions ?? []}
              currentHash={a.context_hash}
              emptyAction={
                <Button variant="accent" onClick={() => setAskOpen(true)}>Ask Claude</Button>
              }
            />
          </div>

          <AskClaudeModal symbol={a.symbol} open={askOpen} onClose={() => setAskOpen(false)} />
        </>
      )}
    </div>
  )
}
