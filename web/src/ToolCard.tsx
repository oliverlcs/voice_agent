import { useState } from 'react'

/** What the server renders for the person; args/output are the raw exchange with the model. */
export type ToolView = {
  name: string
  title: string
  detail: string
  result: string | null
  error: string | null
  args: string
  output: string | null
  call_id?: string | null
}

/** Stored tool rows are JSON since the tool-view change; older rows are plain "name(args)". */
export function parseToolView(text: string): ToolView {
  try {
    const v = JSON.parse(text)
    if (v && typeof v === 'object' && 'title' in v) return v as ToolView
  } catch { /* legacy row */ }
  const m = /^(\w+)\((.*)\)$/s.exec(text)
  const name = m?.[1] ?? 'tool'
  const args = m?.[2] ?? ''
  let detail = ''
  try { const a = JSON.parse(args); detail = String(a.query ?? a.url ?? a.text ?? a.timezone ?? (a.code ? String(a.code).split('\n')[0] : '') ?? '') } catch { detail = args }
  return { name, title: LEGACY_TITLES[name] ?? name.replace(/_/g, ' '), detail: detail.slice(0, 80), result: 'Done', error: null, args, output: null }
}

const LEGACY_TITLES: Record<string, string> = {
  web_search: 'Searched the web', fetch_url: 'Read a web page', run_python: 'Ran a calculation',
  get_current_time: 'Checked the time', remember: 'Saved to memory', recall: 'Looked in memory',
  forget: 'Forgot a memory', search_chats: 'Searched past chats', read_chat: 'Read a past chat',
}

const pretty = (s: string | null) => {
  if (!s) return ''
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { return s }
}

export function ToolCard({ view }: { view: ToolView }) {
  const [open, setOpen] = useState(false)
  const pending = view.result === null && view.error === null
  const status = view.error ? 'error' : pending ? 'pending' : 'done'
  return (
    <div className={`tool ${status}`}>
      <button className="tool-head" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className="tool-dot" aria-hidden />
        <span className="tool-text">
          <span className="tool-title">{view.title}{view.detail && <span className="tool-detail"> · {view.detail}</span>}</span>
          <span className="tool-result">{view.error ? `Failed: ${view.error}` : pending ? 'Working…' : view.result}</span>
        </span>
        <span className="tool-caret" aria-hidden>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="tool-raw">
          <div className="tool-raw-label">What the assistant sent · {view.name}</div>
          <pre>{pretty(view.args) || '{}'}</pre>
          <div className="tool-raw-label">What it got back</div>
          <pre>{view.output === null ? (view.name === 'web_search' ? 'Handled by OpenAI, results go straight to the assistant.' : '…') : pretty(view.output) || '(empty)'}</pre>
        </div>
      )}
    </div>
  )
}
