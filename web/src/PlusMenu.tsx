import { useEffect, useRef, useState } from 'react'
import { api, type FileInfo } from './api'
import { FileIcon } from './icons'

type Props = {
  chatId: string | null
  files: FileInfo[]
  ensureChat: () => Promise<string>     // creates the chat when the user uploads before speaking
  refresh: () => void
  onError: (m: string) => void
}

/** Files for the current chat only. Standing files for every chat live in Settings > General. */
export function PlusMenu({ chatId, files, ensureChat, refresh, onError }: Props) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const wrap = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const onFiles = async (list: FileList | null) => {
    if (!list?.length) return
    setBusy(true)
    try {
      const id = chatId ?? await ensureChat()
      for (const f of Array.from(list)) await api.uploadChatFile(id, f)
      refresh()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  return (
    <div ref={wrap}>
      <button className="round" aria-label="Add a file to this chat" onClick={() => setOpen(o => !o)} title="Add a file to this chat">
        {open ? '×' : '+'}
      </button>
      <input ref={fileInput} type="file" multiple hidden onChange={e => onFiles(e.target.files)} />
      {open && (
        <div className="menu" role="menu">
          <button onClick={() => fileInput.current?.click()} disabled={busy}>
            <span>📎</span> {busy ? 'Uploading…' : 'Upload file for this chat'}
          </button>
          {files.length > 0 && (
            <>
              <div className="section">In this chat</div>
              {files.map(u => (
                <div className="item" key={u.name}>
                  <FileIcon /><span title={u.has_text ? 'text extracted' : 'binary; readable via run_python'}>{u.name}</span>
                  <button className="x" aria-label={`remove ${u.name}`} disabled={!chatId}
                    onClick={async () => { if (!chatId) return; await api.removeChatFile(chatId, u.name).catch(e => onError(e.message)); refresh() }}>✕</button>
                </div>
              ))}
            </>
          )}
          <div className="hint">Files for every chat: Settings › General</div>
        </div>
      )}
    </div>
  )
}
