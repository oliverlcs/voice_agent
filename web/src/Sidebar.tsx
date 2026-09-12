import type { ChatSummary } from './api'
import { GearIcon } from './icons'

type Props = {
  chats: ChatSummary[]
  activeId: string | null
  open: boolean
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onSettings: () => void
  onClose: () => void
}

export function Sidebar({ chats, activeId, open, onNew, onSelect, onDelete, onSettings, onClose }: Props) {
  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={`sidebar${open ? ' open' : ''}`}>
        <div className="brand">Guess Who Doesn't Have A Job Agent</div>
        <button className="newchat" onClick={onNew}>✎&nbsp; New chat</button>
        <div className="section">Chats</div>
        <nav className="chatlist">
          {chats.length === 0 && <div className="hint">No chats yet</div>}
          {chats.map(c => (
            <div key={c.id} className={`chatrow${c.id === activeId ? ' active' : ''}`}>
              <button className="chatbtn" onClick={() => onSelect(c.id)} title={new Date(c.updated_at * 1000).toLocaleString()}>
                {c.title ?? 'New chat'}
              </button>
              <button className="x" aria-label="delete chat" onClick={() => onDelete(c.id)}>✕</button>
            </div>
          ))}
        </nav>
        <button className="settingsbtn" onClick={onSettings}><GearIcon /><span>Settings</span></button>
      </aside>
    </>
  )
}
