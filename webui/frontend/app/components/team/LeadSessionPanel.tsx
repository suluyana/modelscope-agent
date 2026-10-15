import { useEffect, useState } from 'react'
import { AssistantMessage } from '~/components/messages/AssistantMessage'
import { UserBubble } from '~/components/messages/UserBubble'
import { IconButton } from '~/components/common/IconButton'
import { api } from '~/lib/api'
import { historyToAgentMessages, type HistoryMessage } from '~/lib/agentProvider'
import { useT } from '~/lib/i18n'
import CloseIcon from '~/assets/icons/close.svg?react'

/** Right-rail view of the Lead LLM session (what that model actually saw).
 * Distinct from {@link DispatchTranscriptPanel}, which replays a Worker
 * `team.stream`. */
export function LeadSessionPanel({
  sessionId,
  onClose,
}: {
  sessionId: string
  onClose: () => void
}) {
  const { t } = useT()
  const [messages, setMessages] = useState(
    () => [] as ReturnType<typeof historyToAgentMessages>,
  )
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setMessages([])
    setError(null)
    api
      .listSessionMessages(sessionId, { view: 'llm', silent: true })
      .then((rows) => {
        if (cancelled) return
        setMessages(historyToAgentMessages(rows as HistoryMessage[]))
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="team-lead-session">
      <div className="flex items-center justify-between gap-2 border-b border-msa-line-1 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-msa-text-1">
            {t.team.leadSession}
          </div>
          <div className="text-xs text-msa-text-3">{t.team.lead}</div>
        </div>
        <IconButton
          icon={<CloseIcon className="h-4 w-4" />}
          variant="tonal"
          size="sm"
          onClick={onClose}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
        {error ? (
          <p className="text-sm text-msa-text-danger">{error}</p>
        ) : (
          <div className="space-y-6">
            {messages.map((message, i) =>
              message.role === 'user' ? (
                <div key={i} className="flex justify-end">
                  <UserBubble message={message} />
                </div>
              ) : (
                <AssistantMessage key={i} message={message} streaming={false} />
              ),
            )}
          </div>
        )}
      </div>
    </div>
  )
}
