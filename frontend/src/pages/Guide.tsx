// How the app works — the in-app manual (ask #5), plus the full glossary
// (moved here from Settings, where nobody would look for it).
import { BookOpen } from 'lucide-react'
import { Card, CardTitle, SkeletonCard } from '@/components/ui'
import { useGlossary } from '@/hooks'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <div className="space-y-2 text-sm leading-relaxed text-muted">{children}</div>
    </Card>
  )
}

export default function Guide() {
  const { data: glossary, isLoading } = useGlossary()

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2 text-accent">
        <BookOpen className="h-5 w-5" />
        <h2 className="text-lg font-semibold text-text">How TickerLens works</h2>
      </div>

      <Section title="What this is — and deliberately isn't">
        <p>
          TickerLens is a research tool: for any US ticker it assembles price,
          a calibrated <span className="text-text">80% expected range for tomorrow</span>,
          fundamentals, options positioning, news and social sentiment, analyst
          recommendation shifts, and your own position — in plain English.
        </p>
        <p>
          It will <span className="text-text">never predict direction</span>. This project's
          parent experiment tested exactly that — two model classes, four time
          horizons, twenty per-ticker models, ~9,600 predictions per configuration —
          and everything landed at coin-flip. The one thing that survived with
          evidence is the range math, so that's what the app is built around.
        </p>
      </Section>

      <Section title="Reading the range (the one number with a receipt)">
        <p>
          The band takes how much the stock actually moved over the last 20
          sessions and marks the span tomorrow's close should land in about 4
          days out of 5. Replayed over 9,620 historical days, this exact
          construction held <span className="text-text">80.5%</span> of the time
          (77.5–83% per ticker) — that receipt is shown under every chart.
        </p>
        <p>
          <span className="text-text">Band engines:</span> Settings offers three
          ways to build the range — flat 20-day (default), EWMA (weights recent
          days more), and conformal (empirical quantile, no distribution
          assumed). Each shows its coverage from a 7,309-day replay. Flat-20d
          won that test; the others exist because the evidence is displayed,
          not because they're better. The chart footer shows which engine built
          the band you're looking at.
        </p>
        <p>
          The band's known weakness: it's calibrated for normal days, and
          earnings days aren't normal. That's why the earnings panel now
          quantifies the exception instead of just warning about it — see below.
        </p>
      </Section>

      <Section title="Earnings: expected move & post-earnings drift">
        <p>
          Before a report, the earnings panel shows what the options market is
          charging for the reaction (<span className="text-text">implied move</span>)
          next to what this stock actually did on its last ~8 earnings days —
          and the ratio between them. Options pricing 2× the stock's own
          history means an unusual event is expected; well under 1× means
          complacency. Neither says which direction.
        </p>
        <p>
          After a big surprise (≥5% beat or miss), a
          <span className="text-text"> PEAD</span> chip appears: research going back
          decades shows prices tend to keep drifting in the surprise's
          direction for roughly 60 trading days. A documented historical
          tendency, shown as context — not a promise.
        </p>
      </Section>

      <Section title="Insiders & short interest (official sources)">
        <p>
          <span className="text-text">Insiders</span> come straight from SEC EDGAR
          Form 4 filings — filtered to real open-market buys and sells (stock
          awards, option exercises, and tax sales are noise and get dropped).
          A <span className="text-text">CLUSTER BUY</span> badge means two or more
          different insiders bought within ~two weeks — the pattern research
          says actually carries information, on a multi-month horizon.
        </p>
        <p>
          <span className="text-text">Short interest</span> is FINRA's official
          bi-monthly report: total shares short, change vs the prior period,
          and days-to-cover (how many normal days shorts would need to exit).
          The percentage uses shares outstanding — true float isn't freely
          available, so we label it honestly instead of pretending.
        </p>
      </Section>

      <Section title="The Setup Score (transparent, tunable, humble)">
        <p>
          A 0–100 rollup of observable signals: momentum vs the 20/50-day
          averages, news tone, options put/call tilt, month-over-month analyst
          shifts, and social chatter. 50 is neutral. Volatility regime scales
          conviction (calm tape ⇒ slightly more, turbulent ⇒ less) — it never
          votes on direction. Click the score to see exactly which signal
          contributed how many points; missing data redistributes weight
          instead of silently reading as bearish. Weights are yours to tune in
          Settings. The permanent disclaimer under the score is not decoration —
          it's the experiment's verdict.
        </p>
      </Section>

      <Section title="News sentiment — the fallback chain">
        <p>
          The app tries Finnhub's aggregated sentiment first; if that endpoint
          isn't available on the free tier, it scores the free headlines itself
          with a transparent wordlist (labeled <span className="text-text">headline-scored</span> —
          crude but inspectable, and it feeds the score the same way). Third
          layer: when you run a Claude discussion, the prompt asks Claude to do
          a live news check and file a <span className="text-text">news_view</span> — that
          shows up in the News panel with its date.
        </p>
      </Section>

      <Section title="Claude stance vs the Setup Score — who says what">
        <p>
          Two different objects, deliberately kept apart. The
          <span className="text-text"> Setup Score</span> is computed by the app from
          observable signals — it describes alignment and never says buy or
          sell, because these signals demonstrably can't call direction. The
          <span className="text-text"> Claude stance</span> (header chip: BUY / SELL /
          HOLD / TRIM / WATCH with a horizon) is <em>surfaced, not computed</em>:
          it's the reasoned call from your latest discussion session, dated,
          with its one-line justification on hover and the full reasoning one
          click away. It flips to <span className="text-warn">stale</span> when the
          data has moved since the call, and Claude is explicitly allowed to
          decline — an honest "watch" beats a forced verdict. Alongside your
          own decision journal, you get three tracks to compare over time:
          what the signals said, what Claude concluded, and what you did.
        </p>
      </Section>

      <Section title="Claude discussions — the file loop">
        <p>
          "Ask Claude" builds a prompt containing the current analysis snapshot
          <span className="text-text"> plus your last five discussion summaries</span> for
          continuity. Paste it into <code className="rounded bg-surface-3 px-1 py-0.5 text-accent">claude</code> in
          a terminal; Claude writes a markdown file into <code className="rounded bg-surface-3 px-1 py-0.5">discussions/</code>;
          the app ingests it automatically and the timeline updates within
          seconds. The files are the source of truth — edit one and the app
          updates; delete one and the entry disappears. Lost your terminal
          session? The prompt is regenerable anytime from the button — nothing
          is lost.
        </p>
      </Section>

      <Section title="Premium seller's lens (options)">
        <p>
          On optionable stocks, the lens lists covered-call and cash-secured-put
          candidates inside a validated 1–5 week window. Each row shows the
          premium and annualized yield, the <span className="text-text">market's</span>{' '}
          breach probability (the option's delta), and{' '}
          <span className="text-text">history's</span> breach probability — this stock's
          own two years of moves at that horizon, an estimator validated
          point-in-time to ±6.8pp at 5 days, ±6.1pp at 10, ±9.5pp at 21
          (45-day horizons failed validation and are simply not offered).
          Positive <span className="text-text">edge</span> means the market pays more
          than the historical risk costs — the variance risk premium, per strike.
        </p>
        <p>
          The warnings are the product: an <span className="text-warn">E</span> badge
          means earnings lands inside the window (history-based probabilities
          don't apply through a print — the NFLX −8.2% night was "expected size,
          unwanted direction"), and a <span className="text-text">tail</span> badge marks
          probabilities under ~15%, where measurement says the true risk runs
          up to ~5–9pp worse than shown. Selling premium is being PAID to bear
          risk — the opposite of predicting direction, and it still cuts.
          Illiquid strikes (low open interest, wide spreads) are filtered out
          entirely. The VRP counter accrues as you use the app: implied moves
          captured at analysis time vs what later happened.
        </p>
      </Section>

      <Section title="Portfolio tab">
        <p>
          A live, read-only view of your Schwab account (shared weekly-re-auth
          token). Click any holding to jump straight into its analysis — its
          discussion history is already there, because discussions are keyed by
          ticker. Each live sync also records a value snapshot, so the history
          chart grows with use. This tool never places or modifies orders.
        </p>
        <p>
          The <span className="text-text">risk card</span> applies the same validated
          band math to your whole account: tomorrow's 80% dollar range,
          annualized volatility, the diversification ratio (2.0× = your
          diversification is halving risk; 1.0× = it's doing nothing),
          effective positions, and portfolio beta. Real dividend yields feed
          the income columns, and the action plan tracks your trim/buy/sell
          checklist.
        </p>
        <p>
          The <span className="text-text">rebalance sandbox</span> previews hypothetical
          trades before you make them: enter ± dollar amounts per symbol and
          see the whole book's before/after — range, vol, diversification,
          beta, income, blended yield, cash (overspend-guarded). Nothing
          executes; you still trade in the Schwab app.
        </p>
      </Section>

      <Section title="Watchlist screener & morning sheet">
        <p>
          The watchlist is a screener: tickers paint instantly, prices arrive
          in one batched call, and the heavy columns stream in with a progress
          bar. Amber rows broke their 80% band yesterday; Impl/Band above ~1.3×
          means options are pricing an event; the score column shows the last
          computed value (open the ticker to refresh it).
        </p>
        <p>
          The <span className="text-text">☀ Morning sheet</span> button turns all of it
          into one printable page — unusual moves, earnings within a week, the
          full table, and your portfolio's day — for the open-one-thing-with-
          coffee routine.
        </p>
      </Section>

      <Section title="Keeping the app honest (Settings)">
        <p>
          Two instruments live in Settings. The <span className="text-text">band
          engine picker</span> shows each engine's measured backtest coverage —
          you choose with the evidence in front of you. The
          <span className="text-text"> score honesty audit</span> accumulates your Setup
          Scores and, once ~30 scored days exist, shows whether high scores
          actually preceded better 5- and 20-day returns than low ones. If the
          buckets don't order themselves, that's a finding about the score —
          the same discipline that killed the direction-prediction experiment
          this app grew out of, pointed inward.
        </p>
      </Section>

      <Section title="Freshness, staleness, and failures">
        <p>
          Every data source caches on its own clock (quotes 30s, options 5m,
          news 1h, analyst 12h…). Cards show their age; a source that fails
          serves its last good data with a <span className="text-warn">stale</span> badge
          rather than going blank, and a section with nothing to show says so
          instead of breaking the page. The Refresh button forces everything
          past its clock.
        </p>
      </Section>

      <Card>
        <CardTitle>Glossary — every term in the app, in plain English</CardTitle>
        {isLoading && <SkeletonCard lines={4} />}
        <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-2">
          {glossary && Object.entries(glossary.entries).map(([k, e]) => (
            <div key={k}>
              <p className="text-sm font-medium">{e.term}</p>
              <p className="text-xs leading-relaxed text-muted">{e.plain}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-faint">{e.why}</p>
            </div>
          ))}
        </div>
      </Card>

      <p className="pb-2 text-[11px] text-faint">
        Research &amp; education only — not investment advice. Direction is not
        predictable from these signals; that's the finding this app is built on.
      </p>
    </div>
  )
}
