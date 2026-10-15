import { Button, Card, Input, Select, Tag, Typography } from 'antd'
import { useMemo } from 'react'
import { useT } from '~/lib/i18n'
import {
  isCurrentlyBlocked,
  taskTitle,
  type TeamEndpoint,
  type TeamTask,
} from '~/lib/teamApi'

const { Text } = Typography

const COLS: Array<[string, 'colUnclaimed' | 'colTodo' | 'colDoing' | 'colWaiting' | 'colDone' | 'colFail', (task: TeamTask) => boolean]> = [
  ['claim', 'colUnclaimed', (task) => task.status === 'pending' && !task.target_at_name],
  [
    'todo',
    'colTodo',
    (task) =>
      task.status === 'wait_human' ||
      (task.status === 'pending' && Boolean(task.target_at_name)),
  ],
  ['doing', 'colDoing', (task) => task.status === 'in_progress' && !task.waiting_approval],
  [
    'waiting',
    'colWaiting',
    (task) =>
      Boolean(task.waiting_approval) || task.status === 'waiting_approval',
  ],
  ['done', 'colDone', (task) => task.status === 'completed'],
  ['fail', 'colFail', (task) => task.status === 'failed' || task.status === 'cancelled'],
]

export function TeamKanban({
  tasks,
  activeRailId,
  onSelectDispatch,
  onRunBoard,
  onAskLead,
  onApprovePlan,
  askLeadTaskId,
  approvePlanTaskId,
  runPending,
  disabled,
  endpoints,
  onPatchTask,
  onOpenFile,
  onFocusInbox,
}: {
  tasks: TeamTask[]
  activeRailId: string
  onSelectDispatch: (dispatchId: string) => void
  onRunBoard: () => void
  onAskLead?: (taskId: string) => void
  onApprovePlan?: (taskId: string) => void
  askLeadTaskId?: string | null
  approvePlanTaskId?: string | null
  runPending?: boolean
  disabled?: boolean
  endpoints?: TeamEndpoint[]
  onPatchTask?: (
    taskId: string,
    body: { prompt?: string; target_at_name?: string | null; blocked_by?: string[] },
  ) => void
  onOpenFile?: (path: string) => void
  onFocusInbox?: () => void
}) {
  const { t } = useT()
  const byId = useMemo(
    () => new Map(tasks.map((task) => [task.task_id, task])),
    [tasks],
  )

  return (
    <Card
      size="small"
      title={
        <div className="flex items-center justify-between gap-2">
          <span>{t.team.kanban}</span>
          <Button
            size="small"
            type="primary"
            data-testid="team-run-board"
            onClick={onRunBoard}
            loading={runPending}
            disabled={disabled}
          >
            {t.team.runBoard}
          </Button>
        </div>
      }
      className="flex min-h-0 flex-1 flex-col overflow-hidden [&_.ant-card-body]:min-h-0 [&_.ant-card-body]:flex-1 [&_.ant-card-body]:overflow-auto"
    >
      {tasks.length === 0 ? (
        <Text type="secondary" className="!text-xs">
          {t.team.noTasks}
        </Text>
      ) : (
        <div
          className="flex h-full flex-col gap-3"
          data-testid="team-kanban"
        >
          {COLS.map(([key, labelKey, match]) => {
            const col = tasks.filter(match)
            if (col.length === 0) return null
            return (
              <div key={key} className="min-w-0">
                <Text strong className="!text-xs">
                  {t.team[labelKey]} ({col.length})
                </Text>
                <div className="mt-1 flex flex-col gap-1">
                  {col.map((task) => {
                    const blocked = isCurrentlyBlocked(task, byId)
                    const title = taskTitle(task.prompt, 48)
                    const selected =
                      Boolean(task.last_dispatch_id) &&
                      task.last_dispatch_id === activeRailId
                    const preview = (task.result_text || '').trim()
                    const artifacts = task.output_artifacts || []
                    const clickable = Boolean(task.last_dispatch_id) ||
                      Boolean(task.waiting_approval)
                    return (
                      <div
                        key={task.task_id}
                        role="button"
                        tabIndex={0}
                        data-testid={`team-task-${task.task_id}`}
                        className={`rounded border px-2 py-1.5 text-left hover:bg-msa-fill-2 ${
                          clickable ? 'cursor-pointer' : 'cursor-default'
                        } ${
                          selected
                            ? 'border-msa-text-3 bg-msa-fill-2'
                            : 'border-msa-line-1 bg-msa-bg-1'
                        }`}
                        onClick={() => {
                          if (task.waiting_approval) onFocusInbox?.()
                          if (task.last_dispatch_id) {
                            onSelectDispatch(task.last_dispatch_id)
                          }
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            if (task.waiting_approval) onFocusInbox?.()
                            if (task.last_dispatch_id) {
                              onSelectDispatch(task.last_dispatch_id)
                            }
                          }
                        }}
                      >
                        <div className="text-xs text-msa-text-1">{title}</div>
                        {!task.last_dispatch_id &&
                        task.status === 'pending' ? (
                          <div className="mt-0.5 text-[10px] text-msa-text-3">
                            {t.team.noDispatchYet}
                          </div>
                        ) : null}
                        {task.status === 'wait_human' &&
                        task.result_summary ? (
                          <div className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-[10px] text-amber-800">
                            {task.result_summary}
                          </div>
                        ) : null}
                        {task.status === 'wait_human' ? (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {task.last_dispatch_id ? (
                              <Button
                                size="small"
                                className="!h-6 !text-[10px]"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  onSelectDispatch(task.last_dispatch_id!)
                                }}
                              >
                                {t.team.replyOnRail}
                              </Button>
                            ) : null}
                            {onAskLead ? (
                              <Button
                                size="small"
                                className="!h-6 !text-[10px]"
                                data-testid={`team-ask-lead-${task.task_id}`}
                                loading={askLeadTaskId === task.task_id}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  onAskLead(task.task_id)
                                }}
                              >
                                {t.team.askLead}
                              </Button>
                            ) : null}
                            {onApprovePlan &&
                            (task.plan_status === 'ready' ||
                              task.plan_status === 'required') ? (
                              <Button
                                size="small"
                                type="primary"
                                className="!h-6 !text-[10px]"
                                data-testid={`team-approve-plan-${task.task_id}`}
                                loading={approvePlanTaskId === task.task_id}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  onApprovePlan(task.task_id)
                                }}
                              >
                                {t.team.approvePlan}
                              </Button>
                            ) : null}
                          </div>
                        ) : null}
                        {task.status === 'completed' && preview ? (
                          <div
                            data-testid={`team-task-result-${task.task_id}`}
                            className="mt-1 line-clamp-4 whitespace-pre-wrap text-[11px] leading-snug text-msa-text-2"
                          >
                            <span className="mr-1 text-[10px] text-msa-text-3">
                              {t.team.resultOriginal}
                            </span>
                            {preview}
                          </div>
                        ) : null}
                        <div className="mt-0.5 flex flex-wrap items-center gap-1">
                          {task.target_at_name ? (
                            <Tag className="!m-0 !text-[10px]">
                              @{task.target_at_name}
                            </Tag>
                          ) : (
                            <Tag className="!m-0 !text-[10px]">
                              {t.team.unassigned}
                            </Tag>
                          )}
                          {task.unclaimed_expired ? (
                            <Tag color="orange" className="!m-0 !text-[10px]">
                              {t.team.unclaimedExpired}
                            </Tag>
                          ) : null}
                          {blocked ? (
                            <Tag
                              color="red"
                              className="!m-0 !text-[10px]"
                              data-testid={`team-task-blocked-${task.task_id}`}
                            >
                              {t.team.blocked}
                            </Tag>
                          ) : null}
                          {task.status === 'wait_human' &&
                          task.result_summary ? (
                            <Tag color="gold" className="!m-0 !text-[10px]">
                              {t.team.needsClarification}
                            </Tag>
                          ) : null}
                          {task.waiting_approval ? (
                            <Tag color="gold" className="!m-0 !text-[10px]">
                              {t.team.waitingApproval}
                            </Tag>
                          ) : null}
                          {task.plan_status === 'ready' ? (
                            <Tag color="gold" className="!m-0 !text-[10px]">
                              {t.team.planReady}
                            </Tag>
                          ) : null}
                        </div>
                        {onPatchTask &&
                        (task.status === 'pending' ||
                          task.status === 'wait_human') ? (
                          <div
                            className="mt-1 flex flex-col gap-1"
                            onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => e.stopPropagation()}
                          >
                            <Select
                              size="small"
                              className="w-full"
                              allowClear
                              placeholder={t.team.assign}
                              value={task.target_at_name || undefined}
                              options={(endpoints || []).map((ep) => ({
                                value: ep.at_name,
                                label: `@${ep.at_name}`,
                              }))}
                              onChange={(value) =>
                                onPatchTask(task.task_id, {
                                  target_at_name: value || '',
                                })
                              }
                            />
                            <Input
                              size="small"
                              placeholder={t.team.deps}
                              defaultValue={(task.blocked_by || []).join(',')}
                              onPressEnter={(e) => {
                                const raw = (e.target as HTMLInputElement).value
                                onPatchTask(task.task_id, {
                                  blocked_by: raw
                                    .split(/[,，\s]+/)
                                    .map((s) => s.trim())
                                    .filter(Boolean),
                                })
                              }}
                            />
                          </div>
                        ) : null}
                        {artifacts.length > 0 ? (
                          <div
                            className="mt-1 flex flex-col gap-0.5"
                            data-testid={`team-task-artifacts-${task.task_id}`}
                          >
                            <span className="text-[10px] text-msa-text-3">
                              {t.team.artifacts}
                            </span>
                            {artifacts.map((raw) => {
                              const isUrl = /^https?:\/\//i.test(raw)
                              const label = isUrl
                                ? raw.replace(/^https?:\/\//i, '').slice(0, 48)
                                : (raw.split(/[\\/]/).pop() || raw).slice(0, 48)
                              return isUrl ? (
                                <a
                                  key={raw}
                                  href={raw}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="truncate text-[11px] text-msa-text-brand1"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  {label}
                                </a>
                              ) : (
                                <span
                                  key={raw}
                                  role="button"
                                  tabIndex={0}
                                  className="truncate font-mono text-[11px] text-msa-text-2 underline underline-offset-2"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    onOpenFile?.(raw)
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                      e.preventDefault()
                                      e.stopPropagation()
                                      onOpenFile?.(raw)
                                    }
                                  }}
                                >
                                  {label}
                                </span>
                              )
                            })}
                          </div>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
