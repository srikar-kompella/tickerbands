// First-run setup wizard (Phase C): identity → providers → keys → test → save.
// Local-first: everything written to ~/.tickerlens on THIS machine only.
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Telescope, XCircle } from 'lucide-react'
import { toast } from 'sonner'
import { Badge, Button, Card, CardTitle, Skeleton } from '@/components/ui'
import { cn } from '@/lib/utils'

interface SetupOptions {
  capabilities: Record<string, { providers: string[]; current: string }>
  provider_info: Record<string, { label: string; keys: string[]; note: string }>
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? `${r.status}`)
  return r.json()
}

const CAP_LABELS: Record<string, string> = {
  quotes: 'Quotes', daily_history: 'Price history', option_chain: 'Options chain',
  account: 'Brokerage account (read-only)', fundamentals: 'Fundamentals',
  news_sentiment: 'News sentiment', news_headlines: 'News headlines',
  analyst_recs: 'Analyst recommendations', earnings: 'Earnings',
  social: 'Social sentiment', insiders: 'Insider filings',
  short_interest: 'Short interest', symbol_search: 'Symbol search',
}

export default function Setup({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const { data: opts } = useQuery({
    queryKey: ['setup-options'],
    queryFn: () => j<SetupOptions>('/api/setup/options'),
  })
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [caps, setCaps] = useState<Record<string, string>>({})
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [tests, setTests] = useState<Record<string, boolean | null>>({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (opts && !Object.keys(caps).length) {
      setCaps(Object.fromEntries(
        Object.entries(opts.capabilities).map(([c, v]) => [c, v.current])))
    }
  }, [opts])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!opts) return <div className="mx-auto max-w-2xl p-8"><Skeleton className="h-64 w-full" /></div>

  const chosenProviders = [...new Set(Object.values(caps))].filter((p) => p !== 'none')
  const neededKeys = [...new Set(chosenProviders.flatMap(
    (p) => opts.provider_info[p]?.keys ?? []))]

  const runTest = async (p: string) => {
    setTests((t) => ({ ...t, [p]: null }))
    try {
      const r = await j<{ ok: boolean }>(`/api/setup/test/${p}`, { method: 'POST' })
      setTests((t) => ({ ...t, [p]: r.ok }))
    } catch { setTests((t) => ({ ...t, [p]: false })) }
  }

  const save = async () => {
    setSaving(true)
    try {
      await j('/api/setup/save', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, contact_email: email, capabilities: caps, secrets }),
      })
      toast.success('Configuration saved to ~/.tickerlens — welcome aboard')
      qc.invalidateQueries()
      onDone()
    } catch (e) {
      toast.error((e as Error).message)
    } finally { setSaving(false) }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6">
      <div className="flex items-center gap-3 pt-4">
        <Telescope className="h-8 w-8 text-accent" />
        <div>
          <h1 className="text-xl font-bold">Welcome to TickerLens</h1>
          <p className="text-sm text-muted">
            Local-first setup — everything below is written to <code className="rounded bg-surface-3 px-1">~/.tickerlens</code> on
            this machine only. Ranges, not predictions.
          </p>
        </div>
      </div>

      <Card>
        <CardTitle>1 · You</CardTitle>
        <div className="space-y-2">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name (used in research prompts)"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent/60" />
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Contact email (required by SEC EDGAR fair-use policy)"
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent/60" />
        </div>
      </Card>

      <Card>
        <CardTitle>2 · Data providers per capability</CardTitle>
        <p className="mb-3 text-xs text-muted">
          Pick what you have credentials for — anything set to <b>none</b> simply
          hides its features. Zero-key start: yfinance for quotes/history,
          the no-key sources for the rest.
        </p>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {Object.entries(opts.capabilities).map(([cap, v]) => (
            <label key={cap} className="flex items-center justify-between gap-2 rounded-lg border border-border px-2.5 py-1.5 text-sm">
              <span className="text-muted">{CAP_LABELS[cap] ?? cap}</span>
              <select value={caps[cap] ?? v.current}
                onChange={(e) => setCaps({ ...caps, [cap]: e.target.value })}
                className="rounded-md border border-border bg-bg px-1.5 py-1 text-xs outline-none focus:border-accent/60">
                {v.providers.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
          ))}
        </div>
      </Card>

      <Card>
        <CardTitle>3 · Credentials</CardTitle>
        {neededKeys.length === 0 && (
          <p className="text-sm text-faint">Your selections need no API keys. Nice.</p>
        )}
        <div className="space-y-2">
          {neededKeys.map((k) => (
            <input key={k} value={secrets[k] ?? ''} type="password"
              onChange={(e) => setSecrets({ ...secrets, [k]: e.target.value })}
              placeholder={k}
              className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-sm outline-none focus:border-accent/60" />
          ))}
        </div>
        {chosenProviders.map((p) => opts.provider_info[p]?.note && (
          <p key={p} className="mt-2 text-[11px] text-faint">
            <b className="text-muted">{p}:</b> {opts.provider_info[p].note}
          </p>
        ))}
      </Card>

      <Card>
        <CardTitle>4 · Test & save</CardTitle>
        <div className="mb-3 flex flex-wrap gap-2">
          {chosenProviders.map((p) => (
            <button key={p} onClick={() => runTest(p)}
              className={cn('flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs',
                tests[p] === true ? 'border-up/40 text-up'
                  : tests[p] === false ? 'border-down/40 text-down'
                    : 'border-border text-muted hover:bg-surface-2')}>
              {tests[p] === true ? <CheckCircle2 className="h-3.5 w-3.5" />
                : tests[p] === false ? <XCircle className="h-3.5 w-3.5" /> : null}
              test {p}
            </button>
          ))}
          <Badge className="text-[10px]">note: key-based tests pass only after Save (keys load then)</Badge>
        </div>
        <Button variant="accent" onClick={save}
          disabled={saving || !name.trim() || !email.includes('@')}>
          {saving ? 'Saving…' : 'Save & start researching'}
        </Button>
        <p className="mt-3 border-t border-border pt-2 text-[11px] text-faint">
          Research &amp; education only — not investment advice. This tool never
          predicts direction and never places orders. Config lives in
          ~/.tickerlens/config.toml; secrets in ~/.tickerlens/.env (chmod 600).
        </p>
      </Card>
    </div>
  )
}
