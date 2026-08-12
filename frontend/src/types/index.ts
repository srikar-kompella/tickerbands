export * from './analysis'

export interface WatchItem {
  symbol: string
  note: string
  tags: string[]
  added_ts: string
  last_analyzed_ts: string | null
}

export interface Discussion {
  id: number
  ticker: string
  file_path: string
  prompt: string | null
  response_markdown: string
  summary: string | null
  tags: string[]
  context_hash: string | null
  news_view: string | null
  news_note: string | null
  decision: string | null
  decision_price: number | null
  created_ts: string
  file_mtime: string
}

export interface ScreenerRow {
  symbol: string
  price: number | null
  day_pct: number | null
  band_half_pct: number | null
  implied_move_pct: number | null
  implied_vs_band: number | null
  days_to_earnings: number | null
  yesterday_z: number | null
  outside_band_yesterday: boolean
  last_score: number | null
  last_score_ts: string | null
  stance: string | null
  stance_ts: string | null
  note: string
  tags: string[]
}

export interface PortfolioIncome {
  holdings: { symbol: string; yield_pct: number | null; income_annual: number }[]
  projected_annual: number
  blended_yield_pct: number | null
  coverage_pct: number | null
}

export interface DecisionRow {
  id: number
  ticker: string
  decision: string
  decision_price: number | null
  current_price: number | null
  pct_since: number | null
  summary: string | null
  created_ts: string
}

export interface PortfolioRisk {
  available: boolean
  sigma_daily_pct?: number
  annualized_vol_pct?: number
  band_low?: number
  band_high?: number
  band_dollars?: number
  total_value?: number
  coverage_target?: number
  diversification_ratio?: number | null
  hhi?: number
  effective_positions?: number | null
  portfolio_beta?: number | null
  window_days?: number
  excluded?: string[]
}

export interface ScoreAuditBucket {
  band: string
  horizon_days: number
  n: number
  mean_fwd_pct: number
  hit_rate_up: number
}

export interface ScoreAudit {
  samples: { symbol: string; date: string; score: number; fwd5_pct: number | null; fwd20_pct: number | null }[]
  buckets: ScoreAuditBucket[]
  n_scored_days: number
  n_pending: number
  sufficient: boolean
  note: string
}

export interface BandValidation {
  note: string
  overall: Record<string, { coverage_pct: number; mean_halfwidth_pct: number; days: number }>
  per_ticker: Record<string, Record<string, { coverage_pct: number; days: number; mean_halfwidth_pct: number }>>
}

export interface LensRow {
  side: 'call' | 'put'
  strike: number
  expiry: string
  dte: number
  bid: number
  moneyness_pct: number
  yield_ann_pct: number
  implied_breach: number
  empirical_breach: number
  edge_pp: number
  tail_zone: boolean
  earnings_inside: boolean
  oi: number
  spread_pct: number
  iv_pct: number
}

export interface Lens {
  available: boolean
  reason?: string
  spot?: number
  calls: LensRow[]
  puts: LensRow[]
  skew_25d_pp: number | null
  caveats?: {
    dte_cap_calendar: number
    validation: Record<string, string>
    tail_note: string
    n_effective: Record<string, number>
  }
}

export interface VrpReport {
  samples: { day: string; spot: number; dte: number; implied_pct: number; realized_pct: number | null }[]
  n_resolved: number
  avg_ratio: number | null
  note?: string
}

export interface WhatIfView {
  risk: PortfolioRisk
  income: { projected_annual: number; blended_yield_pct: number | null }
}

export interface WhatIfResult {
  before: WhatIfView
  after: WhatIfView
  cash_before: number
  cash_after: number
}

export interface ActionItem {
  id: number
  symbol: string | null
  action: string
  rationale: string
  status: 'open' | 'done' | 'dismissed'
  priority: number
  created_ts: string
}

export interface PortfolioPosition {
  symbol: string
  description: string
  asset_type: string
  qty: number
  avg_cost: number
  market_value: number
  cost_basis: number
  gain: number
  gain_pct: number | null
  day_pl: number
  day_pl_pct: number
  weight_pct: number | null
}

export interface Portfolio {
  as_of: string
  total_value: number
  cash: number
  day_pl: number
  day_pl_pct: number
  positions: PortfolioPosition[]
  source: 'live' | 'cache' | 'stale'
  age_seconds: number
  history: { ts: string; total_value: number; cash: number | null }[]
}

export interface SearchResult {
  symbol: string
  description: string
  type: string
}

export interface GlossaryEntry {
  term: string
  plain: string
  why: string
}

export interface Glossary {
  entries: Record<string, GlossaryEntry>
  features: Record<string, boolean>
}

export interface Settings {
  weights: Record<string, number>
  defaults: Record<string, number>
  score_bands: { green: number; yellow: number }
  disclaimer: string
}

export interface Health {
  ok: boolean
  providers: Record<string, boolean>
  discussions: number
  mock_mode: boolean
}

export type StreamEvent =
  | { type: 'created'; ticker: string; id: number }
  | { type: 'deleted'; ticker: string; id: number }
  | { type: 'error'; file: string; detail: string }
  | { type: 'discovery'; added: number }
