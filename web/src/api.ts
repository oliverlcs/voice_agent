export type FileInfo = { name: string; size: number; has_text: boolean }
export type LinkedInProfile = { name: string; source: string; imported_at: number; files: string[] } | null
export type MemoryInfo = { id: number; kind: string; text: string; tags: string[]; created_at: number }
export type UserSettings = { instructions: string; voice: string; language: string }
export type ContextInfo = {
  settings: UserSettings
  voices: string[]
  languages: string[]
  files: FileInfo[]
  memories: MemoryInfo[]
  connectors: { linkedin: LinkedInProfile }
  models: { live: string; backend: string }
}
export type ChatSummary = { id: string; title: string | null; created_at: number; updated_at: number }
export type ChatMessage = { id: number; role: 'user' | 'assistant' | 'tool'; text: string; ts: number }
export type ChatDetail = ChatSummary & { messages: ChatMessage[]; files: FileInfo[]; live_session: string | null }

async function check<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail ?? msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}
const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
})
const multipart = (url: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return fetch(url, { method: 'POST', body: fd }).then(r => check<FileInfo>(r))
}

export const api = {
  context: () => fetch('/api/context').then(r => check<ContextInfo>(r)),
  saveSettings: (patch: Partial<UserSettings>) => fetch('/api/settings', json('PUT', patch)).then(r => check<UserSettings>(r)),

  // Standing files: part of every conversation (Settings > General)
  uploadFile: (file: File) => multipart('/api/files', file),
  removeFile: (name: string) => fetch(`/api/files/${encodeURIComponent(name)}`, { method: 'DELETE' }).then(r => check(r)),
  // Files for one chat only (plus menu)
  uploadChatFile: (chatId: string, file: File) => multipart(`/api/chats/${chatId}/files`, file),
  removeChatFile: (chatId: string, name: string) =>
    fetch(`/api/chats/${chatId}/files/${encodeURIComponent(name)}`, { method: 'DELETE' }).then(r => check(r)),

  removeMemory: (id: number) => fetch(`/api/memories/${id}`, { method: 'DELETE' }).then(r => check(r)),
  importLinkedIn: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch('/api/linkedin/import', { method: 'POST', body: fd }).then(r => check<{ name: string; files: number }>(r))
  },
  disconnectLinkedIn: () => fetch('/api/linkedin', { method: 'DELETE' }).then(r => check(r)),

  chats: () => fetch('/api/chats').then(r => check<{ chats: ChatSummary[] }>(r)).then(d => d.chats),
  createChat: () => fetch('/api/chats', { method: 'POST' }).then(r => check<ChatSummary>(r)),
  chat: (id: string) => fetch(`/api/chats/${id}`).then(r => check<ChatDetail>(r)),
  deleteChat: (id: string) => fetch(`/api/chats/${id}`, { method: 'DELETE' }).then(r => check(r)),
  renameChat: (id: string, title: string) => fetch(`/api/chats/${id}`, json('PATCH', { title })).then(r => check(r)),

  createSession: (sdp: string, chatId: string | null) =>
    fetch('/api/session', json('POST', { sdp, chat_id: chatId })).then(r => check<{ session_id: string; chat_id: string; sdp: string }>(r)),
  closeSession: (id: string) => fetch(`/api/session/${id}/close`, { method: 'POST' }).then(r => check(r)),
}
