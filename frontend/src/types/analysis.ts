// Mirrors backend/analysis/composer.py's payload exactly. If a shape changes
// there, change it here — test_analyze_full_payload_shape pins the contract.

export type SectionStatus = 'ok' | 'stale' | 'unavailable' | 'disabled'

export interface SectionEnvelope {
  status: SectionStatus
  fetched_ts?: number
  age_seconds?: number
  error_reason?: string
  error_detail?: string
}

export interface Quote {
  symbol: string
  name: string
  last: number
  regular_last: number | null
  regular_change_pct: number | null
  ah_price: number | null
  ah_change_pct: number | null
  is_extended: boolean
  open: number
  close_prev: number
  high_today: number
  low_today: number
  week52_high: number | null
  week52_low: number | null
  volume: number
  net_change_pct: number
  quote_time_ms: number
  asset_type: string
}

export interface Signals {
  prev_close: number
  return_1d: number
  return_5d: number
  return_20d: number
  rv_20d: number
  rv_60d: number | null
  sma20_dist: number
  sma50_dist: number
  volume_z: number
}

export interface Band {
  daily_sigma_pct: number
  half_width_pct: number
  low: number
  high: number
  prev_close: number
  coverage_target: number
  engine: 'flat20' | 'ewma' | 'conformal'
}

export interface EarningsMove {
  implied_move_pct: number | null
  hist_median_abs_pct: number | null
  hist_mean_abs_pct: number | null
  hist_max_abs_pct: number | null
  quarters: number
  implied_vs_hist: number | null
  recent: { date: string; move_pct: number; timing_ambiguous: boolean }[]
}

export interface Pead {
  active: boolean
  report_date: string
  trading_days_since: number
  window_days: number
  surprise_pct: number
  direction: 'positive' | 'negative'
}

export interface InsiderTx {
  owner: string
  title: string
  code: 'P' | 'S'
  date: string
  shares: number
  price: number
  value: number
}

export interface Insiders {
  available: boolean
  transactions: InsiderTx[]
  cluster_buy: boolean
  cluster_buyers?: string[]
  net_shares_90d: number
  note: string
}

export interface ShortInterest {
  available: boolean
  settlement_date?: string | null
  short_interest?: number | null
  prev_short_interest?: number | null
  change_pct?: number | null
  days_to_cover?: number | null
  pct_of_shares_out?: number | null
}

export interface Move {
  move_pct: number
  z: number
  abs_percentile: number
  outside_band: boolean
}

export interface ScoreComponent {
  component: string
  value: number | null
  weight_nominal: number
  weight_used_pct: number
  points: number
  available: boolean
}

export interface SetupScore {
  score: number
  lean: 'BULLISH' | 'NEUTRAL' | 'BEARISH'
  vol_factor: number
  components: ScoreComponent[]
  components_available: number
  components_total: number
  disclaimer: string
}

export interface OptionsSummary {
  available: boolean
  put_call_ratio?: number | null
  call_volume?: number
  put_volume?: number
  atm_iv_pct?: number | null
  implied_move_pct?: number | null
  implied_move_dte?: number | null
  as_of?: string
}

export interface NewsSentiment {
  available: boolean
  bullish_pct?: number
  bearish_pct?: number
  buzz?: number | null
  articles_week?: number
  news_score?: number
  sector_score?: number
  method?: 'finnhub' | 'headline-lexicon'
  per_headline?: number[] // 1 / 0 / -1, aligned with headlines[]
}

export interface ClaudeNews {
  news_view: 'bullish' | 'neutral' | 'bearish'
  news_note: string | null
  created_ts: string
}

export interface VLMetric {
  value: number | null
  basis: string
  approx: boolean
}

export interface TTMMetric {
  value: number | null
  source: string
  as_of: string | null
  warning: string | null
}

export interface ClaudeStance {
  id: number
  stance: 'buy' | 'sell' | 'hold' | 'trim' | 'watch'
  stance_horizon: string | null
  stance_note: string | null
  context_hash: string | null
  created_ts: string
  drift_pct?: number | null
  drift_material?: boolean
}

export interface Headline {
  headline: string
  source: string
  url: string
  ts: number
  summary: string
}

export interface Fundamentals {
  available: boolean
  pe_ttm?: number | null
  eps_ttm?: number | null
  market_cap_m?: number | null
  revenue_growth_ttm_pct?: number | null
  gross_margin_pct?: number | null
  operating_margin_pct?: number | null
  net_margin_pct?: number | null
  dividend_yield_pct?: number | null
  beta_reported?: number | null
}

export interface RecMonth {
  period: string
  strong_buy: number
  buy: number
  hold: number
  sell: number
  strong_sell: number
  total: number
}

export interface Recommendations {
  available: boolean
  months?: RecMonth[]
}

export interface EarningsSurprise {
  period: string
  estimate: number | null
  actual: number | null
  surprise_pct: number | null
}

export interface Earnings {
  available: boolean
  next_date: string | null
  days_until: number | null
  surprises: EarningsSurprise[]
}

export interface Social {
  available: boolean
  bullish?: number
  bearish?: number
  tagged?: number
  total_msgs?: number
  polarity?: number
  sample?: { body: string; sentiment: string | null; created_at: string }[]
}

export interface Position {
  owned: boolean
  qty?: number
  avg_cost?: number
  market_value?: number
  cost_basis?: number
  gain?: number
  gain_pct?: number
  weight_pct?: number
  as_of?: string | null
}

export interface BottomContext {
  drawdown_pct: number
  above_52w_low_pct: number
  range_percentile: number
  vs_sma200_pct: number
  bottom_decile: boolean
  insider_buys_into_drawdown: boolean
  note: string
}

export interface BandCoverage {
  scope: 'this_ticker' | 'basket_overall'
  covered_pct: number
  days: number
  overall?: { covered_pct: number; days: number }
  note?: string
}

export interface Analysis {
  symbol: string
  generated_ts: string
  quote: Quote | null
  signals: Signals | null
  band: Band | null
  move: Move | null
  beta: { beta: number | null; correlation: number | null; window: number } | null
  week52: { low: number; high: number; position_pct: number } | null
  band_coverage: BandCoverage | null
  bottom_context: BottomContext | null
  setup_score: SetupScore | null
  score_history: { ts: string; score: number; lean: string }[]
  chart: { date: string; close: number }[]
  sections: Record<string, SectionEnvelope>
  options: OptionsSummary | null
  news_sentiment: NewsSentiment | null
  claude_news: ClaudeNews | null
  fundamentals_ttm: {
    pe_ttm: TTMMetric
    yield_ttm: TTMMetric
  } | null
  value_lens: {
    as_of: string | null
    source: string
    fcf_ttm: VLMetric; p_fcf: VLMetric; p_fcf_avg: VLMetric; pe_avg: VLMetric
    payout_of_fcf_pct: VLMetric; ev: VLMetric; rev_cagr_3y: VLMetric
    rev_cagr_5y: VLMetric; roic_ttm: VLMetric; roic_avg: VLMetric
    acquisitions_total: VLMetric
    ni_vs_fcf_flag: boolean; net_cash: boolean
    readings: string[]; warnings: string[]
  } | null
  ex_div: { today: boolean; amount: number | null; pending: boolean } | null
  claude_stance: ClaudeStance | null
  headlines: Headline[] | null
  fundamentals: Fundamentals | null
  recommendations: Recommendations | null
  earnings: Earnings | null
  earnings_move: EarningsMove | null
  pead: Pead | null
  insiders: Insiders | null
  short_interest: ShortInterest | null
  social: Social | null
  position: Position | null
  context_hash: string
}
