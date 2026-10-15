import { applyChunk, type AgentMessage, type AgentPart, type StepKind } from '~/lib/agentProvider'
import type { TeamEvent } from '~/lib/teamApi'

const TERMINAL = new Set([
  'team.dispatch_done',
  'team.dispatch_error',
  'team.dispatch_cancelled',
])

/** ACP / Bridge handshake lines — not model output. */
const STATUS_NOISE =
  /^(?:(?:codex|claude|cursor)_acp_connected|dry_run|attached|attach_skipped_platform_session_id|attach_fallback_fresh)$/i

function nestedPayload(p: Record<string, unknown>): Record<string, unknown> {
  const inner = p.payload
  return inner && typeof inner === 'object' && !Array.isArray(inner)
    ? (inner as Record<string, unknown>)
    : {}
}

function inferStepKind(name: string, acpKind?: unknown): StepKind {
  const n = name.toLowerCase()
  const k = String(acpKind || '').toLowerCase()
  if (k === 'read' || n.includes('read_file') || n.includes('file_read'))
    return 'file_read'
  if (
    k === 'edit' ||
    k === 'write' ||
    n.includes('edit') ||
    n.includes('write_file') ||
    n.includes('file_write')
  )
    return 'file_write'
  if (
    k === 'execute' ||
    n.includes('shell') ||
    n.includes('bash') ||
    n.includes('terminal')
  )
    return 'terminal'
  if (n.includes('search') || n.includes('web_search')) return 'search'
  return 'tool_call'
}

export function isDispatchTerminal(ev: TeamEvent): boolean {
  return TERMINAL.has(ev.type)
}

function withTurnClock(msg: AgentMessage, parts: AgentPart[]): AgentMessage {
  return {
    ...msg,
    parts,
    turnStartedAt: msg.turnStartedAt ?? Date.now(),
  }
}

function closeOpenThoughts(parts: AgentPart[]): AgentPart[] {
  return parts.map((p) =>
    p.kind === 'thought' && p.done === false
      ? {
          ...p,
          done: true,
          duration:
            p.duration ??
            (p.startedAt
              ? Math.max(1, Math.floor((Date.now() - p.startedAt) / 1000))
              : undefined),
        }
      : p,
  )
}

/**
 * Keep a single thinking block for the whole dispatch. Late status packets
 * must grow that block in place — never insert a new thought after text,
 * or the rail becomes one row per SSE frame.
 */
function mergeThought(
  msg: AgentMessage,
  text: string,
  asLine: boolean,
): AgentMessage {
  const parts = msg.parts ? [...msg.parts] : []
  const idx = parts.findIndex((p) => p.kind === 'thought')
  if (idx >= 0 && parts[idx].kind === 'thought') {
    const prev = parts[idx]
    if (text === prev.text) return msg
    // Growing prefix = snapshot of the same block; otherwise a token delta.
    const nextText =
      prev.text && text.startsWith(prev.text) && text.length > prev.text.length
        ? text
        : prev.text +
          (asLine && prev.text && !prev.text.endsWith('\n') ? '\n' : '') +
          text
    parts[idx] = { ...prev, text: nextText, done: false }
    return withTurnClock(msg, parts)
  }
  parts.unshift({
    kind: 'thought',
    text,
    startedAt: Date.now(),
    done: false,
  })
  return withTurnClock(msg, parts)
}

function mergeText(msg: AgentMessage, text: string): AgentMessage {
  const closed = {
    ...msg,
    parts: closeOpenThoughts(msg.parts ? [...msg.parts] : []),
  }
  const parts = closed.parts ? [...closed.parts] : []
  const last = parts[parts.length - 1]
  if (last && last.kind === 'text') {
    if (text === last.text) return closed
    const nextText =
      last.text && text.startsWith(last.text) && text.length > last.text.length
        ? text
        : last.text + text
    parts[parts.length - 1] = { kind: 'text', text: nextText }
    return {
      ...withTurnClock(closed, parts),
      content: nextText,
    }
  }
  parts.push({ kind: 'text', text })
  return {
    ...withTurnClock(closed, parts),
    content: (closed.content || '') + text,
  }
}

/** Fold one TeamEvent into an assistant message for the private-stream rail. */
export function applyDispatchEvent(
  base: AgentMessage,
  ev: TeamEvent,
): AgentMessage {
  if (ev.type === 'team.attribution_mismatch') return base
  const evDid = ev.dispatch_id
  if (evDid && base.dispatchId && evDid !== base.dispatchId) return base
  const evAt = (ev.at_name || '').replace(/^@/, '').toLowerCase()
  const baseAt = (base.atName || '').replace(/^@/, '').toLowerCase()
  if (evAt && baseAt && evAt !== baseAt) return base
  if (ev.type !== 'team.stream') return base
  const p = ev.payload || {}
  const inner = nestedPayload(p)
  const kind = String(p.type || '')
  if (kind === 'text') {
    const text = String(p.content || '')
    if (!text) return base
    return mergeText(base, text)
  }
  if (kind === 'status') {
    const text = String(p.content || '')
    if (!text || STATUS_NOISE.test(text)) return base
    const asLine = inner.kind !== 'thought'
    return mergeThought(base, text, asLine)
  }
  if (kind === 'tool_call' || kind === 'tool_result') {
    const name = String(p.name || inner.name || 'tool')
    const callId = String(p.call_id || inner.call_id || '')
    const status = String(
      p.status ||
        inner.status ||
        (kind === 'tool_result' ? 'done' : 'running'),
    )
    const result =
      p.result ?? (kind === 'tool_result' ? p.content : undefined)
    const closed = {
      ...base,
      parts: closeOpenThoughts(base.parts ? [...base.parts] : []),
    }
    return applyChunk(closed, {
      type: 'step',
      content: '',
      meta: {
        kind: inferStepKind(name, inner.kind || p.kind),
        tool: name,
        name,
        call_id: callId,
        status,
        arguments: p.arguments ?? inner.arguments,
        result,
        error: p.error ?? inner.error,
      },
    })
  }
  if (kind === 'error') {
    const msg = String(p.content || p.error || inner.error || 'error')
    return applyChunk(base, {
      type: 'error',
      content: '',
      meta: { message: msg, recoverable: false },
    })
  }
  return base
}
