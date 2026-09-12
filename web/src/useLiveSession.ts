/**
 * Browser side of a GPT-Live WebRTC session, bound to one chat.
 *
 * The browser sends mic audio and receives model audio directly from OpenAI over WebRTC.
 * Our server creates the session (holding the API key), replays the chat's history into it,
 * and runs tools on a sideband. Events arrive on the "oai-events" data channel.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { ToolView } from './ToolCard'

export type Status = 'idle' | 'connecting' | 'live' | 'error'
export type Line = { id: number; role: 'user' | 'agent' | 'tool'; text: string; tool?: ToolView }

type LiveEvent = { type: string; [k: string]: unknown }

const PENDING_TITLES: Record<string, string> = {
  web_search: 'Searching the web', fetch_url: 'Reading a web page', run_python: 'Running a calculation',
  get_current_time: 'Checking the time', remember: 'Saving to memory', recall: 'Looking in memory',
  forget: 'Forgetting a memory', search_chats: 'Searching past chats', read_chat: 'Reading a past chat',
}
const pendingView = (name: string, args: string, callId: string | null): ToolView =>
  ({ name, title: PENDING_TITLES[name] ?? name.replace(/_/g, ' '), detail: '', result: null, error: null, args, output: null, call_id: callId })
type Options = { onStarted?: (chatId: string) => void; onClosed?: (chatId: string) => void }

export function useLiveSession(opts: Options = {}) {
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [thinking, setThinking] = useState(false)
  const [muted, setMuted] = useState(false)
  const [inputLevel, setInputLevel] = useState(0)

  const pc = useRef<RTCPeerConnection | null>(null)
  const dc = useRef<RTCDataChannel | null>(null)
  const mic = useRef<MediaStream | null>(null)
  const audioEl = useRef<HTMLAudioElement | null>(null)
  const sessionId = useRef<string | null>(null)
  const chatId = useRef<string | null>(null)
  const nextId = useRef(1)
  const meterFrame = useRef<number | null>(null)
  const meterContext = useRef<AudioContext | null>(null)
  const smoothedLevel = useRef(0)
  const mutedRef = useRef(false)
  const optsRef = useRef(opts)
  optsRef.current = opts

  // Full-duplex: user and agent transcripts interleave in time. Track the open bubble per
  // speaker and keep appending to it until that speaker pauses for more than TURN_GAP_MS,
  // measured on the session timeline (start_ms/end_ms of the fragments).
  const openLine = useRef<Partial<Record<Line['role'], { id: number; endMs: number }>>>({})
  const TURN_GAP_MS = 1200

  const stopMeter = useCallback(() => {
    if (meterFrame.current !== null) cancelAnimationFrame(meterFrame.current)
    meterFrame.current = null
    meterContext.current?.close().catch(() => {})
    meterContext.current = null
    smoothedLevel.current = 0
    setInputLevel(0)
  }, [])

  const startMeter = useCallback((stream: MediaStream) => {
    stopMeter()
    const audioContext = new AudioContext()
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.65
    audioContext.createMediaStreamSource(stream).connect(analyser)
    meterContext.current = audioContext
    const samples = new Uint8Array(analyser.fftSize)
    let lastPaint = 0
    const measure = (time: number) => {
      if (mutedRef.current) { meterFrame.current = requestAnimationFrame(measure); return }
      analyser.getByteTimeDomainData(samples)
      let sum = 0
      for (const sample of samples) {
        const centered = (sample - 128) / 128
        sum += centered * centered
      }
      const rms = Math.sqrt(sum / samples.length)
      const target = Math.min(1, Math.max(0, (rms - 0.012) / 0.16))
      smoothedLevel.current = smoothedLevel.current * 0.72 + target * 0.28
      if (time - lastPaint > 40) {
        setInputLevel(Number(smoothedLevel.current.toFixed(3)))
        lastPaint = time
      }
      meterFrame.current = requestAnimationFrame(measure)
    }
    meterFrame.current = requestAnimationFrame(measure)
  }, [stopMeter])

  const append = useCallback((role: Line['role'], text: string, startMs?: number, endMs?: number) => {
    const open = openLine.current[role]
    const at = startMs ?? Date.now()
    const end = endMs ?? Date.now()
    const reuse = role !== 'tool' && open && at - open.endMs < TURN_GAP_MS
    if (reuse) {
      openLine.current[role] = { id: open.id, endMs: Math.max(open.endMs, end) }
      setLines(prev => prev.map(l => (l.id === open.id ? { ...l, text: l.text + text } : l)))
    } else {
      const id = nextId.current++
      openLine.current[role] = { id, endMs: end }
      setLines(prev => [...prev, { id, role, text }])
    }
  }, [])

  const addTool = useCallback((view: ToolView) => {
    const id = nextId.current++
    setLines(prev => [...prev, { id, role: 'tool', text: `${view.name}(${view.args})`, tool: view }])
  }, [])

  /** Replace the pending cards with the server's finished ones (title, outcome, raw result). */
  const syncTools = useCallback(() => {
    const sid = sessionId.current
    if (!sid) return
    api.sessionTools(sid).then(({ tools }) => {
      const byId = new Map(tools.filter(t => t.call_id).map(t => [t.call_id!, t]))
      setLines(prev => prev.map(l => (l.tool?.call_id && byId.has(l.tool.call_id) ? { ...l, tool: byId.get(l.tool.call_id)! } : l)))
    }).catch(() => {})
  }, [])

  const teardown = useCallback(() => {
    stopMeter()
    dc.current?.close()
    pc.current?.close()
    mic.current?.getTracks().forEach(t => t.stop())
    dc.current = null
    pc.current = null
    mic.current = null
    const closedChat = chatId.current
    if (sessionId.current) {
      const sid = sessionId.current
      sessionId.current = null
      api.closeSession(sid).catch(() => {}).finally(() => { if (closedChat) optsRef.current.onClosed?.(closedChat) })
    }
    setThinking(false)
    mutedRef.current = false
    setMuted(false)
    setStatus('idle')
  }, [stopMeter])

  const handleEvent = useCallback((ev: LiveEvent) => {
    switch (ev.type) {
      case 'session.started':
        setStatus('live')
        break
      case 'session.input_transcript.delta':
        append('user', ev.delta as string, ev.start_ms as number, ev.end_ms as number)
        break
      case 'session.output_transcript.delta':
        append('agent', ev.delta as string, ev.start_ms as number, ev.end_ms as number)
        break
      case 'session.delegation.created':
        setThinking(true)
        break
      case 'response.event': {
        const inner = ev.event as LiveEvent
        if (inner.type === 'response.output_item.done') {
          const item = inner.item as { type: string; id?: string; call_id?: string; name?: string; arguments?: string; action?: unknown }
          if (item.type === 'function_call') addTool(pendingView(item.name ?? 'tool', item.arguments ?? '{}', item.call_id ?? null))
          else if (item.type === 'web_search_call') addTool(pendingView('web_search', JSON.stringify(item.action ?? {}), item.id ?? null))
        } else if (inner.type === 'response.completed' || inner.type === 'response.failed' || inner.type === 'response.incomplete') {
          setThinking(false)
          syncTools()
        }
        break
      }
      case 'error': {
        const e = ev.error as { message: string }
        setError(e.message)
        break
      }
      case 'session.closed':
        teardown()
        break
    }
  }, [append, addTool, syncTools, teardown])

  const start = useCallback(async (forChatId: string | null) => {
    setError(null)
    setLines([])
    mutedRef.current = false
    setMuted(false)
    openLine.current = {}
    setStatus('connecting')
    try {
      const peer = new RTCPeerConnection()
      pc.current = peer

      if (!audioEl.current) {
        audioEl.current = document.createElement('audio')
        audioEl.current.autoplay = true
        document.body.appendChild(audioEl.current)
      }
      peer.ontrack = e => { audioEl.current!.srcObject = e.streams[0] }

      const ms = await navigator.mediaDevices.getUserMedia({ audio: true })
      mic.current = ms
      startMeter(ms)
      peer.addTrack(ms.getTracks()[0], ms)

      const channel = peer.createDataChannel('oai-events')
      dc.current = channel
      channel.onmessage = e => {
        try { handleEvent(JSON.parse(e.data)) } catch (err) { console.warn('bad event', err) }
      }
      channel.onclose = () => { if (pc.current) teardown() }
      peer.onconnectionstatechange = () => {
        if (peer.connectionState === 'failed' || peer.connectionState === 'disconnected') {
          setError('connection lost')
          teardown()
        }
      }

      const offer = await peer.createOffer()
      await peer.setLocalDescription(offer)
      const { session_id, chat_id, sdp } = await api.createSession(offer.sdp!, forChatId)
      sessionId.current = session_id
      chatId.current = chat_id
      optsRef.current.onStarted?.(chat_id)
      await peer.setRemoteDescription({ type: 'answer', sdp })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      teardown()
      setStatus('error')
    }
  }, [handleEvent, startMeter, teardown])

  const stop = useCallback(() => {
    try { dc.current?.send(JSON.stringify({ type: 'session.close' })) } catch { /* channel may be closed */ }
    teardown()
  }, [teardown])

  /** Mute = stop sending mic audio. The track keeps running (silence is sent), so the
   *  WebRTC connection and the server-side turn detection stay untouched. */
  const applyMute = useCallback((next: boolean) => {
    mutedRef.current = next
    mic.current?.getAudioTracks().forEach(track => { track.enabled = !next })
    if (next) { smoothedLevel.current = 0; setInputLevel(0) }
    setMuted(next)
  }, [])

  const toggleMute = useCallback(() => {
    if (!mic.current) return
    applyMute(!mutedRef.current)
  }, [applyMute])

  const clearLines = useCallback(() => { setLines([]); openLine.current = {} }, [])

  useEffect(() => () => teardown(), [teardown])

  return { status, error, lines, thinking, muted, inputLevel, start, stop, toggleMute, clearLines }
}
