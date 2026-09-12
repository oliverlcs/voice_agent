import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type ContextInfo, type UserSettings } from './api'
import { CloseIcon, FileIcon, GearIcon, LinkedInIcon, MemoryIcon, PlugIcon, VoiceIcon } from './icons'

export type SettingsTab = 'general' | 'voice' | 'connectors' | 'memory'

type Props = {
  tab: SettingsTab
  context: ContextInfo | null
  onTab: (t: SettingsTab) => void
  onClose: () => void
  refresh: () => void
  onError: (m: string) => void
}

const NAV: { id: SettingsTab; label: string; icon: ReactNode }[] = [
  { id: 'general', label: 'General', icon: <GearIcon /> },
  { id: 'voice', label: 'Voice', icon: <VoiceIcon /> },
  { id: 'connectors', label: 'Connectors', icon: <PlugIcon /> },
  { id: 'memory', label: 'Memory', icon: <MemoryIcon /> },
]

export function Settings({ tab, context, onTab, onClose, refresh, onError }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const fail = (e: unknown) => onError(e instanceof Error ? e.message : String(e))
  const save = async (patch: Partial<UserSettings>) => {
    try { await api.saveSettings(patch); refresh() } catch (e) { fail(e) }
  }

  return (
    <div className="settings-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <div className="settings" role="dialog" aria-modal="true" aria-label="Settings">
      <aside className="settings-nav">
        <button className="settings-close" aria-label="Close settings" onClick={onClose}><CloseIcon /></button>
        {NAV.map(n => (
          <button key={n.id} className={`navrow${tab === n.id ? ' active' : ''}`} onClick={() => onTab(n.id)}>
            {n.icon}<span>{n.label}</span>
          </button>
        ))}
      </aside>

      <section className="settings-body">
        {!context ? <div className="hint">Loading…</div>
          : tab === 'general' ? <General context={context} save={save} refresh={refresh} fail={fail} />
          : tab === 'voice' ? <Voice context={context} save={save} />
          : tab === 'connectors' ? <Connectors context={context} refresh={refresh} fail={fail} />
          : <Memory context={context} refresh={refresh} fail={fail} />}
      </section>
    </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
type SectionProps = { context: ContextInfo; save: (p: Partial<UserSettings>) => Promise<void> }

function General({ context, save, refresh, fail }: SectionProps & { refresh: () => void; fail: (e: unknown) => void }) {
  const [text, setText] = useState(context.settings.instructions)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => { setText(context.settings.instructions) }, [context.settings.instructions])
  const dirty = text !== context.settings.instructions

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    try { for (const f of Array.from(files)) await api.uploadFile(f); refresh() } catch (e) { fail(e) }
    finally { setBusy(false); if (fileInput.current) fileInput.current.value = '' }
  }

  return (
    <>
      <h2>General</h2>

      <div className="row col">
        <div>
          <div className="row-title">Instructions</div>
          <div className="row-help">The agent keeps these in mind in every conversation, spoken and in the background.</div>
        </div>
        <textarea value={text} rows={8} maxLength={8000} onChange={e => setText(e.target.value)}
          placeholder={'- Answer really concise.\n- I am looking for embedded systems roles in Munich.'} />
        <div className="row-actions">
          <button className="btn primary" disabled={!dirty} onClick={() => save({ instructions: text })}>Save</button>
          {dirty && <button className="btn" onClick={() => setText(context.settings.instructions)}>Discard</button>}
        </div>
      </div>

      <div className="row col">
        <div className="row-between">
          <div>
            <div className="row-title">Files</div>
            <div className="row-help">Standing files for all future conversations, for example your CV.</div>
          </div>
          <button className="btn" disabled={busy} onClick={() => fileInput.current?.click()}>{busy ? 'Uploading…' : 'Add file'}</button>
          <input ref={fileInput} type="file" multiple hidden onChange={e => onFiles(e.target.files)} />
        </div>
        {context.files.length === 0 ? <div className="hint">No files yet</div> : (
          <ul className="list">
            {context.files.map(f => (
              <li key={f.name}>
                <FileIcon /><span className="grow" title={f.has_text ? 'text extracted' : 'binary; readable via run_python'}>{f.name}</span>
                <span className="dim">{fmtSize(f.size)}</span>
                <button className="x" aria-label={`remove ${f.name}`} onClick={async () => { try { await api.removeFile(f.name); refresh() } catch (e) { fail(e) } }}>✕</button>
              </li>
            ))}
          </ul>
        )}
      </div>

    </>
  )
}

function Voice({ context, save }: SectionProps) {
  const s = context.settings
  return (
    <>
      <h2>Voice</h2>
      <div className="row">
        <div>
          <div className="row-title">Default voice</div>
        </div>
        <select value={s.voice} onChange={e => save({ voice: e.target.value })}>
          {context.voices.map(v => <option key={v} value={v}>{v[0].toUpperCase() + v.slice(1)}</option>)}
        </select>
      </div>
      <div className="row">
        <div>
          <div className="row-title">Language</div>
        </div>
        <select value={s.language} onChange={e => save({ language: e.target.value })}>
          <option value="auto">Auto-detect</option>
          {context.languages.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>
    </>
  )
}

function Connectors({ context, refresh, fail }: { context: ContextInfo; refresh: () => void; fail: (e: unknown) => void }) {
  const li = context.connectors.linkedin
  const [busy, setBusy] = useState(false)
  const [showInstructions, setShowInstructions] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const onFile = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    try { await api.importLinkedIn(files[0]); setShowInstructions(false); refresh() } catch (e) { fail(e) }
    finally { setBusy(false); if (input.current) input.current.value = '' }
  }
  return (
    <>
      <h2>Connectors</h2>
      <div className="row">
        <div className="conn">
          <LinkedInIcon />
          <div>
            <div className="row-title">LinkedIn</div>
            {li && <div className="row-help">
              {`Imported: ${li.name} (${li.source}, ${new Date(li.imported_at * 1000).toLocaleDateString()})`}
            </div>}
          </div>
        </div>
        <div className="row-actions">
          <button className={`btn${li ? '' : ' primary'}`} disabled={busy} aria-expanded={showInstructions}
            onClick={() => setShowInstructions(value => !value)}>Import LinkedIn Data</button>
          {li && <button className="btn" onClick={async () => { try { await api.disconnectLinkedIn(); refresh() } catch (e) { fail(e) } }}>Remove</button>}
        </div>
      </div>
      {showInstructions && (
        <div className="connector-guide">
          <h3>Import profile from LinkedIn</h3>
          <ol>
            <li>Sign in to LinkedIn on a desktop computer.</li>
            <li>Open <strong>Me → Settings &amp; Privacy → Data Privacy → Download your data</strong>. Select the data you want, then choose <strong>Request archive</strong>.</li>
            <li>Download the ZIP file from LinkedIn’s email, then import it here.</li>
          </ol>
          <div className="row-actions">
            <a className="btn" href="https://www.linkedin.com/mypreferences/d/download-my-data"
              target="_blank" rel="noreferrer">Open LinkedIn</a>
            <button className="btn primary" disabled={busy} onClick={() => input.current?.click()}>
              {busy ? 'Importing…' : 'Import data'}
            </button>
          </div>
          <input ref={input} type="file" accept=".pdf,.zip" hidden onChange={e => onFile(e.target.files)} />
        </div>
      )}
    </>
  )
}

function Memory({ context, refresh, fail }: { context: ContextInfo; refresh: () => void; fail: (e: unknown) => void }) {
  return (
    <>
      <h2>Memory</h2>
      <div className="row col">
        <div className="row-help">What the agent has remembered about you, newest first, with the date it was learned. Remove anything that is wrong or outdated.</div>
        {context.memories.length === 0 ? <div className="hint">Nothing remembered yet</div> : (
          <ul className="list">
            {context.memories.map(m => (
              <li key={m.id}>
                <span className="dim date">{m.date}</span>
                <span className="grow" title={`${m.kind}${m.tags.length ? ' · ' + m.tags.join(', ') : ''}`}>{m.text}</span>
                <button className="x" aria-label={`forget memory ${m.id}`} onClick={async () => { try { await api.removeMemory(m.id); refresh() } catch (e) { fail(e) } }}>✕</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

function fmtSize(n: number) {
  return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`
}
