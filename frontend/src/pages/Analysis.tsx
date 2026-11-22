// The Analysis page: ticker in at the top → key facts → sections.
//
// Layout (2026-11-22 declutter), sized by the CONTENT width (container
// queries), not the window, so it holds with the sidebar open or collapsed:
//   1. sticky header — price, move, 80% range, stance, data as-of
//   2. price chart + premium-seller table (2/3) beside the at-a-glance rail:
//      setup score + your position
//   3. the remaining sections, balanced into as many ~22rem columns as fit
//   4. one line naming the sections that have nothing to show, and why
import { Search, Telescope } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { TickerSearch } from '@/components/TickerSearch'
import { AskClaudeModal } from '@/components/discussions/AskClaudeModal'
import { DiscussionTimeline } from '@/components/discussions/DiscussionTimeline'
import {
  AnalystPanel, ContextPanel, EarningsPanel, FundamentalsPanel, InsidersPanel,
  NewsPanel, OptionsPanel, OwnershipPanel, ShortInterestPanel, SocialPanel,
  ValueLensPanel, isFund, isNewListing, panelStatus, type PanelKey,
} from '@/components/analysis/Panels'
import { fmtDate } from '@/lib/format'
import { PremiumLensCard } from '@/components/analysis/PremiumLensCard'
import { SetupScoreCard } from '@/components/analysis/SetupScoreCard'
import { SnapshotHeader } from '@/components/analysis/SnapshotHeader'
import { VolBandCard } from '@/components/analysis/VolBandCard'
import { Button, Card, Disclosure, EmptyState, SkeletonCard } from '@/components/ui'
import {
  useAnalysis, useDiscussions, useGlossary, useRefreshAnalysis,
  useWatchlist, useWatchlistMutations,
} from '@/hooks'
import type { Analysis as AnalysisData, GlossaryEntry } from '@/types'

type PanelProps = { a: AnalysisData; g: Record<string, GlossaryEntry> }

// Reading order. `companyOnly` sections do not apply to funds.
const PANELS: { key: PanelKey; title: string; companyOnly?: boolean
  Panel: (p: PanelProps) => ReactNode }[] = [
  { key: 'fundamentals', title: 'Fundamentals', Panel: FundamentalsPanel },
  { key: 'context', title: 'Context', Panel: ContextPanel },
  { key: 'options', title: 'Options positioning', Panel: OptionsPanel },
  { key: 'earnings', title: 'Earnings', companyOnly: true, Panel: EarningsPanel },
  { key: 'news', title: 'News', Panel: NewsPanel },
  { key: 'value', title: 'Value lens', companyOnly: true, Panel: ValueLensPanel },
  { key: 'recs', title: 'Analyst recs', companyOnly: true, Panel: AnalystPanel },
  { key: 'insiders', title: 'Insiders', companyOnly: true, Panel: InsidersPanel },
  { key: 'short', title: 'Short interest', Panel: ShortInterestPanel },
  { key: 'social', title: 'Social', Panel: SocialPanel },
]

type QuietItem = { title: string; reason: string }

function QuietList({ items, label }: { items: QuietItem[]; label: ReactNode }) {
  return (
    <Disclosure label={label}>
      <ul className="space-y-1 pl-4 text-xs leading-relaxed text-muted">
        {items.map((i) => (
          <li key={i.title}>
            <span className="font-medium text-text">{i.title}</span> — {i.reason}
          </li>
        ))}
      </ul>
    </Disclosure>
  )
}

/** The sections with nothing to show: one quiet line; the reasons on click. */
function QuietSections({ items, notApplicable }: {
  items: QuietItem[]; notApplicable: string[]
}) {
  if (!items.length && !notApplicable.length) return null
  return (
    <Card className="space-y-1.5 py-3">
      {!!items.length && (
        <QuietList items={items} label={
          <span>
            Nothing to show for {items.length} section{items.length > 1 ? 's' : ''}:{' '}
            <span className="text-muted">{items.map((i) => i.title).join(' · ')}</span>
          </span>
        } />
      )}
      {!!notApplicable.length && (
        <p className="pl-4 text-xs text-faint">
          Company-only sections, not applicable to a fund:{' '}
          <span className="text-muted">{notApplicable.join(' · ')}</span>
        </p>
      )}
    </Card>
  )
}

/**
 * A symbol that started trading days ago: one line at the top says why most
 * of the page is empty, and the empty sections fold into it instead of being
 * listed one by one at the foot.
 */
function NewListingNote({ a, items, notApplicable }: {
  a: AnalysisData; items: QuietItem[]; notApplicable: string[]
}) {
  const n = a.security?.sessions_listed
  const first = a.security?.first_trade_date
  const when = n != null
    ? `Listed ${n} session${n === 1 ? '' : 's'} ago${first ? ` (first trade ${fmtDate(first)})` : ''}`
    : `Newly listed${first ? ` (first trade ${fmtDate(first)})` : ''}`
  return (
    <div role="note" className="mt-3 space-y-1.5 rounded-xl border border-accent/30 bg-accent/[0.06] px-4 py-2.5">
      <p className="text-[13px] text-text">
        <span className="font-medium">{when}</span>
        <span className="text-muted"> — history too short for σ and beta; the 80% range,
        realized vol and beta start once enough sessions have traded.</span>
      </p>
      {!!items.length && (
        <QuietList items={items} label={
          <span>
            {items.length} section{items.length > 1 ? 's have' : ' has'} nothing yet:{' '}
            <span className="text-muted">{items.map((i) => i.title).join(' · ')}</span>
          </span>
        } />
      )}
      {!!notApplicable.length && (
        <p className="pl-4 text-xs text-faint">
          Company-only sections, not applicable to a fund:{' '}
          <span className="text-muted">{notApplicable.join(' · ')}</span>
        </p>
      )}
    </div>
  )
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

  // which sections have something to show; the rest are named once at the foot
  // A fund has no earnings, insiders, analyst coverage or statements: those
  // empty sections are not drawn and are named once as not applicable,
  // instead of as "missing". (If a fund does carry such data, it still shows.)
  const fund = !!a && isFund(a)
  const newListing = !!a && isNewListing(a)
  const statuses = a ? PANELS.map((p) => {
    const st = panelStatus(a, p.key)
    return fund && p.companyOnly && !st.show
      ? { ...p, show: false, reason: undefined, notApplicable: true }
      : { ...p, ...st, notApplicable: false }
  }) : []
  const position = a ? panelStatus(a, 'position') : { show: false }
  const quiet = [
    ...(position.reason ? [{ title: 'Your position', reason: position.reason }] : []),
    ...statuses.filter((p) => !p.show && p.reason)
      .map((p) => ({ title: p.title, reason: p.reason! })),
  ]
  const notApplicable = statuses.filter((p) => p.notApplicable).map((p) => p.title)

  return (
    <div className="@container flex min-h-full flex-col">
      {/* ticker / company-name search */}
      <div className="mb-3 flex items-center gap-2">
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

          {newListing && <NewListingNote a={a} items={quiet} notApplicable={notApplicable} />}

          {/* chart beside the at-a-glance rail; stacked when the content is narrow */}
          <div className="grid grid-cols-1 items-start gap-4 pt-4 @4xl:grid-cols-3">
            <div className="grid min-w-0 grid-cols-1 gap-4 @4xl:col-span-2">
              <VolBandCard a={a} glossary={glossary} />
              {/* full chart width: the nine-column table needs it */}
              <PremiumLensCard a={a} glossary={glossary} />
            </div>
            <div className="grid min-w-0 grid-cols-1 items-start gap-4 @2xl:grid-cols-2 @4xl:grid-cols-1 [&>*:only-child]:col-span-full">
              <SetupScoreCard a={a} glossary={glossary} />
              {position.show && <OwnershipPanel a={a} g={glossary} />}
            </div>
          </div>

          {/* The rest: as many ~22rem columns as the content width holds,
              balanced by the browser, each card as tall as its content. No
              per-card motion wrappers — layout animation inside columns is
              what left cards stranded off-screen. */}
          <div className="mt-4 columns-[22rem] gap-4 *:mb-4 *:break-inside-avoid">
            {statuses.filter((p) => p.show).map(({ key, Panel }) => (
              <div key={key}><Panel a={a} g={glossary} /></div>
            ))}
          </div>

          {!newListing && <QuietSections items={quiet} notApplicable={notApplicable} />}

          {/* this ticker's discussion history, inline */}
          <div className="mt-6">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
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
