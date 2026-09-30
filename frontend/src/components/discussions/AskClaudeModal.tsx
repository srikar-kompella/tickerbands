// The locked file-watcher workflow, step 1: show the copyable prompt. There is
// deliberately NO paste-back textbox — Claude CLI writes the markdown file,
// the watcher ingests it, SSE updates the timeline. Zero manual save.
import { Check, Copy, TerminalSquare } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Button, Modal, Skeleton } from '@/components/ui'

export function AskClaudeModal({ symbol, open, onClose }: {
  symbol: string; open: boolean; onClose: () => void
}) {
  const [prompt, setPrompt] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!open) return
    setPrompt(null)
    setCopied(false)
    api.prompt(symbol)
      .then((r) => setPrompt(r.prompt))
      .catch((e: Error) => {
        toast.error(`Couldn't build prompt: ${e.message}`)
        onClose()
      })
  }, [open, symbol, onClose])

  const copy = async () => {
    if (!prompt) return
    await navigator.clipboard.writeText(prompt)
    setCopied(true)
    toast.success('Prompt copied — paste it into `claude` in your terminal')
    setTimeout(() => setCopied(false), 2500)
  }

  return (
    <Modal open={open} onClose={onClose} title={`Ask Claude about ${symbol}`} wide>
      <ol className="mb-4 space-y-1 text-sm text-muted">
        <li><span className="font-semibold text-text">1.</span> Copy the prompt below (it embeds the current analysis snapshot).</li>
        <li><span className="font-semibold text-text">2.</span> Paste it into <code className="rounded bg-surface-3 px-1.5 py-0.5 text-accent">claude</code> in your terminal.</li>
        <li><span className="font-semibold text-text">3.</span> Claude writes the discussion file; the timeline here updates by itself within seconds.</li>
      </ol>

      {prompt === null ? (
        <div className="space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-4 w-4/6" />
        </div>
      ) : (
        <pre className="max-h-80 overflow-auto rounded-xl border border-border bg-bg p-4 text-xs leading-relaxed text-muted">
          {prompt}
        </pre>
      )}

      <div className="mt-4 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs text-faint">
          <TerminalSquare className="h-3.5 w-3.5" />
          File-watcher ingest — no paste-back needed
        </span>
        <Button variant="accent" onClick={copy} disabled={!prompt}>
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? 'Copied' : 'Copy prompt'}
        </Button>
      </div>
    </Modal>
  )
}
