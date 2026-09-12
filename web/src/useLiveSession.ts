/**
 * Browser side of a GPT-Live WebRTC session, bound to one chat.
 *
 * The browser sends mic audio and receives model audio directly from OpenAI over WebRTC.
 * Our server creates the session (holding the API key), replays the chat's history into it,
 * and runs tools on a sideband. Events arrive on the "oai-events" data channel.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

export type Status = 'idle' | 'connecting' | 'live' | 'error'
export type Line = { id: number; role: 'user' | 'agent' | 'tool'; text: string }

type LiveEvent = { type: string; [k: string]: unknown }
type Options = { onStarted?: (chatId: string) => void; onClosed?: (chatId: string) => void }

export function useLiveSession(opts: Options = {}) {
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [thinking, setThinking] = useState(false)

  const pc = useRef<RTCPeerConnection | null>(null)
  const dc = useRef<RTCDataChannel | null>(null)
  const mic = useRef<MediaStream | null>(null)
  const audioEl = useRef<HTMLAudioElement | null>(null)
  const sessionId = useRef<string | null>(null)
  const chatId = useRef<string | null>(null)
  const nextId = useRef(1)
  const optsRef = useRef(opts)
  optsRef.current = opts

  // Full-duplex: user and agent transcripts interleave in time. Track the open bubble per
  // speaker and keep appending to it until that speaker pauses for more than TURN_GAP_MS,
  // measured on the session timeline (start_ms/end_ms of the fragments).
  const openLine = useRef<Partial<Record<Line['role'], { id: number; endMs: number }>>>({})
  const TURN_GAP_MS = 1200

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

  const teardown = useCallback(() => {
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
    setStatus('idle')
  }, [])

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
          const item = inner.item as { type: string; name?: string; arguments?: string; action?: { query?: string } }
          if (item.type === 'function_call') append('tool', `${item.name}(${(item.arguments ?? '').slice(0, 160)})`)
          else if (item.type === 'web_search_call') append('tool', `web_search(${item.action?.query ?? ''})`)
        } else if (inner.type === 'response.completed' || inner.type === 'response.failed' || inner.type === 'response.incomplete') {
          setThinking(false)
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
  }, [append, teardown])

  const start = useCallback(async (forChatId: string | null) => {
    setError(null)
    setLines([])
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
  }, [handleEvent, teardown])

  const stop = useCallback(() => {
    try { dc.current?.send(JSON.stringify({ type: 'session.close' })) } catch { /* channel may be closed */ }
    teardown()
  }, [teardown])

  const clearLines = useCallback(() => { setLines([]); openLine.current = {} }, [])

  useEffect(() => () => teardown(), [teardown])

  return { status, error, lines, thinking, start, stop, clearLines }
}
