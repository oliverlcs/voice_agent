import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type ChatDetail, type ChatSummary, type ContextInfo } from './api'
import { PlusMenu } from './PlusMenu'
import { Settings, type SettingsTab } from './Settings'
import { Sidebar } from './Sidebar'
import { useLiveSession } from './useLiveSession'

const SETTINGS_TABS: SettingsTab[] = ['general', 'voice', 'connectors', 'memory']

export default function App() {
  const [context, setContext] = useState<ContextInfo | null>(null)
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [active, setActive] = useState<ChatDetail | null>(null)   // null = fresh "new chat" view
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<SettingsTab | null>(() => {
    // /?settings=connectors is where the LinkedIn callback lands
    const t = new URLSearchParams(window.location.search).get('settings')
    return SETTINGS_TABS.includes(t as SettingsTab) ? (t as SettingsTab) : null
  })
  const [uiError, setUiError] = useState<string | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  const refreshContext = useCallback(() => { api.context().then(setContext).catch(e => setUiError(e.message)) }, [])
  const refreshChats = useCallback(() => { api.chats().then(setChats).catch(e => setUiError(e.message)) }, [])
  const loadChat = useCallback(async (id: string) => {
    try { setActive(await api.chat(id)) } catch (e) { setUiError((e as Error).message) }
  }, [])
  const activeRef = useRef(active)
  activeRef.current = active

  const live = useLiveSession({
    onStarted: chatId => { refreshChats(); if (activeRef.current?.id !== chatId) loadChat(chatId) },
    onClosed: async chatId => {
      // Swap the live lines for the saved messages only once the server has them, so the
      // transcript never blanks out. The title arrives a few seconds later from the summarizer.
      const before = activeRef.current?.id === chatId ? activeRef.current.messages.length : 0
      for (let i = 0; i < 6; i++) {
        try {
          const chat = await api.chat(chatId)
          if (chat.messages.length > before || i === 5) {
            setActive(chat); live.clearLines(); break
          }
        } catch (e) { setUiError((e as Error).message); break }
        await new Promise(r => setTimeout(r, 1000))
      }
      refreshChats()
      setTimeout(refreshChats, 8000); setTimeout(() => loadChat(chatId), 8000)
    },
  })

  useEffect(() => { refreshContext(); refreshChats() }, [refreshContext, refreshChats])
  useEffect(() => {
    if (settingsTab && window.location.search) window.history.replaceState(null, '', '/')
  }, [settingsTab])
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }) }, [live.lines, active?.messages])

  const isLive = live.status === 'live' || live.status === 'connecting'

  const newChat = () => { if (isLive) live.stop(); live.clearLines(); setActive(null); setSidebarOpen(false) }
  const selectChat = (id: string) => { if (isLive) live.stop(); live.clearLines(); loadChat(id); setSidebarOpen(false) }
  const deleteChat = async (id: string) => {
    if (isLive && active?.id === id) live.stop()
    await api.deleteChat(id).catch(e => setUiError(e.message))
    if (active?.id === id) setActive(null)
    refreshChats()
  }
  /** The plus menu may add a file before any session exists: create the chat on demand. */
  const ensureChat = async () => {
    if (active) return active.id
    const c = await api.createChat()
    await loadChat(c.id)
    refreshChats()
    return c.id
  }
  const openSettings = (tab: SettingsTab = 'general') => { setSettingsTab(tab); setSidebarOpen(false); refreshContext() }
  const closeSettings = useCallback(() => setSettingsTab(null), [])

  const error = live.error ?? uiError
  const statusText =
    error ? error
    : live.status === 'connecting' ? 'Connecting…'
    : live.status === 'live' ? (live.thinking ? 'Thinking with the backend…' : 'Listening')
    : active ? 'Press the mic to continue this chat' : 'Press the mic to start'

  const stored = active?.messages ?? []
  const empty = stored.length === 0 && live.lines.length === 0

  return (
    <div className="layout">
      <Sidebar chats={chats} activeId={active?.id ?? null} open={sidebarOpen}
        onNew={newChat} onSelect={selectChat} onDelete={deleteChat} onSettings={() => openSettings()} onClose={() => setSidebarOpen(false)} />

      <main className="main">
        <header className="header">
          <button className="menubtn" aria-label="Open chats" onClick={() => setSidebarOpen(true)}>☰</button>
        </header>

        <div className="transcript" ref={scroller}>
          {empty && <div className="empty">What’s your next career move?</div>}
          {stored.map(m => (
            <div key={`s${m.id}`} className={`line ${m.role === 'assistant' ? 'agent' : m.role}`}>{m.text}</div>
          ))}
          {live.lines.map(l => <div key={`l${l.id}`} className={`line ${l.role}`}>{l.text}</div>)}
        </div>

        <div className="dock">
          <PlusMenu chatId={active?.id ?? null} files={active?.files ?? []} ensureChat={ensureChat}
            refresh={() => { if (active) loadChat(active.id) }} onError={setUiError} />
          <div className={`status${error ? ' error' : ''}`}>{statusText}</div>
          <button
            className={`round voice ${live.status}`}
            aria-label={isLive ? 'Stop voice mode' : 'Start voice mode'}
            onClick={isLive ? live.stop : () => live.start(active?.id ?? null)}
            disabled={live.status === 'connecting'}
          >
            {live.status === 'live' ? '■' : '🎤'}
          </button>
        </div>
      </main>

      {settingsTab && (
        <Settings tab={settingsTab} context={context} onTab={setSettingsTab} onClose={closeSettings}
          refresh={refreshContext} onError={setUiError} />
      )}
    </div>
  )
}
