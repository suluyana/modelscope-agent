import { useEffect, useRef, useState } from 'react'
import { Button, Input } from 'antd'
import { AssistantMessage } from '~/components/messages/AssistantMessage'
import { IconButton } from '~/components/common/IconButton'
import { useT } from '~/lib/i18n'
import type { AgentMessage } from '~/lib/agentProvider'
import {
  applyDispatchEvent,
  isDispatchTerminal,
} from '~/lib/dispatchStream'
import { teamApi, type TeamEvent } from '~/lib/teamApi'
import CloseIcon from '~/assets/icons/close.svg?react'

function emptyMessage(
  dispatchId: string,
  atName?: string,
): AgentMessage {
  return {
    role: 'assistant',
    content: '',
    atName,
    dispatchId,
    parts: [],
  }
}

function foldEvents(
  base: AgentMessage,
  events: TeamEvent[],
): { message: AgentMessage; terminal?: 'done' | 'error' | 'cancelled' } {
  let message = base
  let terminal: 'done' | 'error' | 'cancelled' | undefined
  for (const ev of events) {
    if (ev.type === 'team.attribution_mismatch') continue
    if (isDispatchTerminal(ev)) {
      terminal =
        ev.type === 'team.dispatch_cancelled'
          ? 'cancelled'
          : ev.type === 'team.dispatch_error'
            ? 'error'
            : 'done'
      continue
    }
    message = applyDispatchEvent(message, ev)
  }
  return { message, terminal }
}

export function DispatchTranscriptPanel({
  dispatchId,
  atName,
  projectId,
  senderUserId,
  referencedTaskId,
  railTitle,
  onClose,
  onFollowUpSent,
  embedded = false,
  onCancel,
  cancelPending,
}: {
  dispatchId: string
  atName?: string
  projectId?: string
  senderUserId?: string
  referencedTaskId?: string
  railTitle?: string
  onClose?: () => void
  onFollowUpSent?: () => void
  embedded?: boolean
  onCancel?: () => void
  cancelPending?: boolean
}) {
  const { t } = useT()
  const [message, setMessage] = useState<AgentMessage>(
    emptyMessage(dispatchId, atName),
  )
  const [status, setStatus] = useState<
    'running' | 'done' | 'error' | 'cancelled' | 'waiting_approval'
  >('running')
  const [error, setError] = useState<string | null>(null)
  const [mismatch, setMismatch] = useState(false)
  const [needsClarification, setNeedsClarification] = useState(false)
  const [followUp, setFollowUp] = useState('')
  const [sending, setSending] = useState(false)
  const [taskId, setTaskId] = useState(referencedTaskId || '')
  const [resolvedAt, setResolvedAt] = useState(atName || '')
  const [resolvedProject, setResolvedProject] = useState(projectId || '')
  const atNameRef = useRef(atName)
  atNameRef.current = atName

  useEffect(() => {
    if (atName) setResolvedAt(atName)
  }, [atName])
  useEffect(() => {
    if (projectId) setResolvedProject(projectId)
  }, [projectId])
  useEffect(() => {
    if (referencedTaskId) setTaskId(referencedTaskId)
  }, [referencedTaskId])

  useEffect(() => {
    let cancelled = false
    let es: EventSource | null = null
    let raf = 0
    const pending: TeamEvent[] = []
    setMessage(emptyMessage(dispatchId, atNameRef.current))
    setStatus('running')
    setError(null)
    setMismatch(false)
    setNeedsClarification(false)
    setFollowUp('')

    const seen = new Set<string>()
    const keyOf = (ev: TeamEvent) =>
      `${ev.created_at || ''}|${ev.type}|${ev.dispatch_id || ''}|${JSON.stringify(ev.payload || {}).slice(0, 80)}`

    const flush = () => {
      raf = 0
      if (!pending.length) return
      const batch = pending.splice(0)
      setMessage((prev) => {
        let next = prev
        for (const ev of batch) next = applyDispatchEvent(next, ev)
        return next
      })
    }

    const applyLive = (ev: TeamEvent) => {
      const key = keyOf(ev)
      if (seen.has(key)) return
      seen.add(key)
      if (ev.type === 'team.attribution_mismatch') {
        setMismatch(true)
        return
      }
      if (
        ev.type === 'team.dispatch_waiting_approval' ||
        ev.type === 'team.permission_requested'
      ) {
        setStatus('waiting_approval')
        return
      }
      if (ev.type === 'team.permission_resolved') {
        setStatus((prev) => (prev === 'waiting_approval' ? 'running' : prev))
        return
      }
      if (isDispatchTerminal(ev)) {
        flush()
        setStatus(
          ev.type === 'team.dispatch_cancelled'
            ? 'cancelled'
            : ev.type === 'team.dispatch_error'
              ? 'error'
              : 'done',
        )
        const summary = String((ev.payload || {}).summary || '')
        if (summary) {
          setNeedsClarification(
            /请问|哪个|could you|please provide|placeholder|未替换|\?/i.test(
              summary,
            ),
          )
        }
        return
      }
      pending.push(ev)
      if (!raf) raf = requestAnimationFrame(flush)
    }

    ;(async () => {
      try {
        const [detail, stored] = await Promise.all([
          teamApi.getDispatch(dispatchId),
          teamApi.listDispatchEvents(dispatchId),
        ])
        if (cancelled) return
        const name = detail.at_name || atNameRef.current
        if (name) setResolvedAt(name)
        if (detail.project_id) setResolvedProject(detail.project_id)
        if (detail.referenced_task_id) setTaskId(detail.referenced_task_id)
        if (detail.needs_clarification) setNeedsClarification(true)
        for (const ev of stored) seen.add(keyOf(ev))
        const folded = foldEvents(emptyMessage(dispatchId, name), stored)
        setMessage(folded.message)
        if (
          detail.status === 'done' ||
          detail.status === 'error' ||
          detail.status === 'cancelled' ||
          detail.status === 'waiting_approval'
        ) {
          setStatus(detail.status)
        } else if (folded.terminal) {
          setStatus(folded.terminal)
        } else if (detail.waiting_approval) {
          setStatus('waiting_approval')
        }
      } catch (err: unknown) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err))
      }
      if (cancelled) return
      es = teamApi.openDispatchStream(
        dispatchId,
        (ev) => {
          if (!cancelled) applyLive(ev)
        },
        undefined,
        0,
      )
    })()

    return () => {
      cancelled = true
      es?.close()
      if (raf) cancelAnimationFrame(raf)
    }
  }, [dispatchId])

  const streaming = status === 'running'
  const title =
    railTitle ||
    (resolvedAt
      ? `@${resolvedAt} ${t.team.privateStream}`
      : t.team.privateStream)

  const onReply = async () => {
    const text = followUp.trim()
    if (!text || !resolvedProject || !resolvedAt || !senderUserId) return
    setSending(true)
    setError(null)
    try {
      await teamApi.sendMessage({
        projectId: resolvedProject,
        content: `@${resolvedAt} ${text}`,
        senderUserId,
        sessionMode: 'attach',
        referencedTaskId: taskId || undefined,
        targetAtName: resolvedAt,
        wait: false,
      })
      setFollowUp('')
      onFollowUpSent?.()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="team-private-stream">
      <div className="flex items-center justify-between gap-2 border-b border-msa-line-1 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-msa-text-1">
            {title}
          </div>
          <div className="text-xs text-msa-text-3">
            {status === 'waiting_approval'
              ? t.team.waitingApproval
              : status === 'running'
              ? t.team.streamRunning
              : status === 'error' || status === 'cancelled'
                ? t.team.streamFailed
                : needsClarification
                  ? t.team.needsClarification
                  : t.team.streamDone}
          </div>
        </div>
        {onCancel && (status === 'running' || status === 'waiting_approval') ? (
          <Button
            danger
            size="small"
            data-testid="team-rail-cancel"
            loading={cancelPending}
            onClick={onCancel}
          >
            {t.team.cancelRail}
          </Button>
        ) : null}
        {onClose && !embedded ? (
          <IconButton
            icon={<CloseIcon className="h-4 w-4" />}
            variant="tonal"
            size="sm"
            onClick={onClose}
          />
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
        {mismatch ? (
          <p
            data-testid="team-attribution-mismatch"
            className="mb-3 text-sm text-msa-text-danger"
          >
            {t.team.attributionMismatch}
          </p>
        ) : null}
        {error ? (
          <p className="text-sm text-msa-text-danger">{error}</p>
        ) : (
          <AssistantMessage
            message={message}
            streaming={streaming}
            keepProcessOpen
          />
        )}
      </div>
      {resolvedAt && resolvedProject && senderUserId ? (
        <div className="border-t border-msa-line-1 px-4 py-3">
          <div className="mb-2 text-xs text-msa-text-3">
            {t.team.replyOnRailHint}
          </div>
          <div className="flex gap-2">
            <Input.TextArea
              data-testid="team-rail-reply"
              rows={2}
              value={followUp}
              onChange={(e) => setFollowUp(e.target.value)}
              placeholder={t.team.replyOnRailPlaceholder}
            />
            <Button
              type="primary"
              loading={sending}
              disabled={!followUp.trim()}
              onClick={() => void onReply()}
            >
              {t.team.replyOnRail}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
