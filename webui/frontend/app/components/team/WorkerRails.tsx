import { Typography } from 'antd'
import { DispatchTranscriptPanel } from '~/components/team/DispatchTranscriptPanel'
import { useT } from '~/lib/i18n'

const { Text } = Typography

export type WorkerRail = {
  dispatchId: string
  atName?: string
  taskId?: string
  status?: string
  kind?: 'lead' | 'worker' | 'collect'
  title?: string
}

function railTone(status?: string): string {
  if (status === 'waiting_approval') return 'bg-amber-50'
  if (status === 'in_progress' || status === 'running') return 'bg-msa-fill-2'
  if (status === 'wait_human') return 'bg-amber-50'
  if (status === 'failed' || status === 'error' || status === 'cancelled')
    return 'bg-red-50'
  return 'bg-transparent'
}

export function WorkerRails({
  rails,
  activeId,
  onSelect,
  projectId,
  senderUserId,
  onFollowUpSent,
  onCancelRail,
  cancelPendingId,
}: {
  rails: WorkerRail[]
  activeId: string
  onSelect: (dispatchId: string) => void
  projectId?: string
  senderUserId?: string
  onFollowUpSent?: () => void
  onCancelRail?: (dispatchId: string) => void
  cancelPendingId?: string | null
}) {
  const { t } = useT()
  const active = rails.find((r) => r.dispatchId === activeId) || rails[0]

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-msa-line-1 bg-msa-bg-1"
      data-testid="team-worker-rails"
    >
      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-msa-line-1 px-2 py-1">
        <Text type="secondary" className="!mr-1 !shrink-0 !text-[11px]">
          {t.team.workerRails}
        </Text>
        {rails.map((r) => {
          const selected = active?.dispatchId === r.dispatchId
          return (
            <button
              key={r.dispatchId}
              type="button"
              data-testid={`team-rail-tab-${r.dispatchId}`}
              onClick={() => onSelect(r.dispatchId)}
              className={`shrink-0 rounded px-2 py-1 text-xs ${railTone(r.status)} ${
                selected
                  ? 'border border-msa-line-1 font-medium text-msa-text-1'
                  : 'text-msa-text-3 hover:bg-msa-fill-2'
              }`}
            >
              {r.kind === 'lead'
                ? t.team.lead
                : r.kind === 'collect'
                  ? `${t.team.collect} · @${r.atName || 'agent'}`
                  : `@${r.atName || 'agent'}`}
              {r.kind !== 'lead' && r.kind !== 'collect' && r.title
                ? ` · ${r.title}`
                : r.kind === 'collect' && r.title
                  ? ` · ${r.title}`
                  : ''}
              {r.status === 'waiting_approval'
                ? ' · !'
                : r.status === 'in_progress' || r.status === 'running'
                ? ' · …'
                : r.status === 'wait_human'
                  ? ' · ?'
                  : r.status === 'completed' || r.status === 'done'
                    ? ' · ✓'
                    : r.status === 'failed' || r.status === 'error'
                      ? ' · !'
                      : ''}
            </button>
          )
        })}
      </div>
      <div className="min-h-0 flex-1">
        {active ? (
          <DispatchTranscriptPanel
            key={active.dispatchId}
            dispatchId={active.dispatchId}
            atName={active.atName}
            projectId={projectId}
            senderUserId={senderUserId}
            referencedTaskId={active.taskId}
            railTitle={
              active.kind === 'lead'
                ? t.team.lead
                : active.kind === 'collect'
                  ? `${t.team.collect} · @${active.atName || 'agent'}${
                      active.title ? ` · ${active.title}` : ''
                    }`
                  : active.title
                    ? `@${active.atName || 'agent'} · ${active.title}`
                    : undefined
            }
            embedded
            onFollowUpSent={onFollowUpSent}
            onCancel={
              onCancelRail &&
              (active.status === 'in_progress' ||
                active.status === 'running' ||
                active.status === 'wait_human')
                ? () => onCancelRail(active.dispatchId)
                : undefined
            }
            cancelPending={cancelPendingId === active.dispatchId}
          />
        ) : null}
      </div>
    </div>
  )
}
