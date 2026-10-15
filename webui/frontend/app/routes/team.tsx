import { Alert, Button, Card, Drawer, Input, Modal, Select, Space, Switch, Tag, Typography } from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import {
  buildConnectCommand,
  formatSessionWhen,
  pickDefaultLead,
  suggestAtName,
  taskTitle,
  teamApi,
  TEAM_OWNER_ID,
  loadTeamOwnerId,
  type DispatchEnvelope,
  type EndpointHealth,
  type MachineBridge,
  type PermissionAsk,
  type RuntimeCandidate,
  type TeamEndpoint,
  type TeamProject,
  type TeamTask
} from '~/lib/teamApi'
import { useT } from '~/lib/i18n'
import { TeamKanban } from '~/components/team/TeamKanban'
import { WorkerRails, type WorkerRail } from '~/components/team/WorkerRails'

const { Text, Title, Paragraph } = Typography

const STORAGE_KEY = 'msa.team.ui.v3'

type PendingAction =
  | null
  | 'refresh'
  | 'createProject'
  | 'updateLead'
  | 'register'
  | 'pair'
  | 'copy'
  | 'enable'
  | 'send'
  | 'cancel'
  | 'runBoard'
  | 'askLead'
  | 'approvePlan'
  | 'caps'

type PersistedUi = {
  userId?: string
  projectId?: string
  message?: string
  threadId?: string
  sessionMode?: 'auto' | 'attach' | 'fresh'
  newAtName?: string
}

function loadPersisted(): PersistedUi {
  if (typeof window === 'undefined') return {}
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}') as PersistedUi
  } catch {
    return {}
  }
}

/** Drop the old '@me ' composer default so a new project can talk to Lead. */
function initialComposerMessage(raw?: string): string {
  const text = (raw || '').trim()
  if (!text || text === '@me') return ''
  return raw || ''
}

function statusColor(
  state: string
): 'success' | 'warning' | 'error' | 'default' | 'processing' {
  switch (state) {
    case 'online':
    case 'busy':
      return 'success'
    case 'degraded':
    case 'reconnecting':
      return 'warning'
    case 'offline':
    case 'need_reauth':
      return 'error'
    default:
      return 'default'
  }
}

function PermissionAskCard({
  ask,
  onResolved,
}: {
  ask: PermissionAsk
  onResolved: () => void
}) {
  const { t } = useT()
  const [pattern, setPattern] = useState(
    (ask.suggestions && ask.suggestions[0]) || ask.tool_name || '',
  )
  const [busy, setBusy] = useState(false)
  const resolve = (
    action: 'allow_once' | 'allow_always' | 'deny',
    persist?: string,
  ) => {
    setBusy(true)
    void teamApi
      .resolvePermissionAsk(ask.request_id, action, ask.call_id, persist, ask.dispatch_id)
      .then(onResolved)
      .finally(() => setBusy(false))
  }
  return (
    <div className="rounded border border-msa-line-1 px-2 py-1.5">
      <div className="text-xs text-msa-text-1">
        @{ask.at_name || 'agent'} · {ask.tool_name || 'tool'}
      </div>
      <div className="mt-1 flex flex-col gap-1">
        <Button
          size="small"
          type="primary"
          disabled={busy}
          onClick={() => resolve('allow_once')}
        >
          {t.team.allowOnce}
        </Button>
        <div className="flex gap-1">
          <Input
            size="small"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            className="font-mono !text-[11px]"
          />
          <Button
            size="small"
            disabled={busy || !pattern.trim()}
            onClick={() => resolve('allow_always', pattern.trim())}
          >
            {t.team.allowAlways}
          </Button>
        </div>
        <Button
          size="small"
          danger
          disabled={busy}
          onClick={() => resolve('deny')}
        >
          {t.team.denyAsk}
        </Button>
      </div>
    </div>
  )
}

export default function TeamPage() {
  const { t } = useT()
  const location = useLocation()
  const persisted = useMemo(() => loadPersisted(), [])

  const [userId] = useState(
    persisted.userId && persisted.userId !== 'u1'
      ? persisted.userId
      : loadTeamOwnerId(),
  )
  const [projects, setProjects] = useState<TeamProject[]>([])
  const [projectId, setProjectId] = useState(persisted.projectId || '')
  const [endpoints, setEndpoints] = useState<TeamEndpoint[]>([])
  const [bridges, setBridges] = useState<MachineBridge[]>([])
  const [candidatesByBridge, setCandidatesByBridge] = useState<
    Record<string, RuntimeCandidate[]>
  >({})
  const [healthMap, setHealthMap] = useState<Record<string, EndpointHealth>>({})
  const [message, setMessage] = useState(initialComposerMessage(persisted.message))
  const [sessionMode, setSessionMode] = useState<'auto' | 'attach' | 'fresh'>(
    persisted.sessionMode || 'auto'
  )
  const [threadId, setThreadId] = useState(persisted.threadId || 'web-main')
  const [lastDispatches, setLastDispatches] = useState<DispatchEnvelope[]>([])
  const [lastOkHint, setLastOkHint] = useState('')
  const [boardTasks, setBoardTasks] = useState<TeamTask[]>([])
  const [liveDispatches, setLiveDispatches] = useState<WorkerRail[]>([])
  const [activeRailId, setActiveRailId] = useState('')
  const [fleetOpen, setFleetOpen] = useState(false)
  const [askLeadTaskId, setAskLeadTaskId] = useState<string | null>(null)
  const [approvePlanTaskId, setApprovePlanTaskId] = useState<string | null>(null)
  const [circuitNotice, setCircuitNotice] = useState('')
  const [permissionAsks, setPermissionAsks] = useState<PermissionAsk[]>([])
  const [maxParallel, setMaxParallel] = useState(5)
  const [artifactPreview, setArtifactPreview] = useState<{
    name: string
    text: string
  } | null>(null)
  const [pairCode, setPairCode] = useState('')
  const [connectCmd, setConnectCmd] = useState('')
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState<PendingAction>(null)
  const [newAtName, setNewAtName] = useState(persisted.newAtName || 'me')
  const [enableName, setEnableName] = useState<Record<string, string>>({})
  const [ready, setReady] = useState(false)
  const projectIdRef = useRef(projectId)
  const userIdRef = useRef(userId)
  const refreshGen = useRef(0)
  projectIdRef.current = projectId
  userIdRef.current = userId

  useEffect(() => {
    const payload: PersistedUi = {
      userId,
      projectId,
      message,
      threadId,
      sessionMode,
      newAtName
    }
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
      window.localStorage.setItem('msa.team.ownerId', userId || TEAM_OWNER_ID)
    } catch {
      /* ignore quota */
    }
  }, [userId, projectId, message, threadId, sessionMode, newAtName])

  const selectedProject = useMemo(
    () => projects.find((p) => p.project_id === projectId),
    [projects, projectId]
  )

  const sortedProjects = useMemo(() => {
    return [...projects].sort((a, b) => {
      const byTime = String(b.created_at || '').localeCompare(
        String(a.created_at || '')
      )
      if (byTime) return byTime
      return String(b.project_id).localeCompare(String(a.project_id))
    })
  }, [projects])

  const workerRails = useMemo<WorkerRail[]>(() => {
    const leadAt = (selectedProject?.default_lead_at || '')
      .replace(/^@/, '')
      .toLowerCase()
    const byDispatch = new Map<string, TeamTask>()
    for (const task of boardTasks) {
      if (task.last_dispatch_id) byDispatch.set(task.last_dispatch_id, task)
    }
    const map = new Map<string, WorkerRail>()
    const put = (row: WorkerRail) => {
      const prev = map.get(row.dispatchId)
      const task = byDispatch.get(row.dispatchId)
      const at = (
        row.atName ||
        prev?.atName ||
        task?.target_at_name ||
        ''
      )
        .replace(/^@/, '')
        .toLowerCase()
      map.set(row.dispatchId, {
        ...prev,
        ...row,
        atName: row.atName || prev?.atName || task?.target_at_name || undefined,
        taskId: row.taskId || prev?.taskId || task?.task_id,
        status: row.status || prev?.status,
        kind: task
          ? at && at === leadAt
            ? 'collect'
            : 'worker'
          : row.kind ||
            prev?.kind ||
            (at && at === leadAt ? 'lead' : 'worker'),
        title: task ? taskTitle(task.prompt, 16) : row.title || prev?.title,
      })
    }
    for (const row of liveDispatches) put(row)
    for (const d of lastDispatches) {
      put({
        dispatchId: d.dispatch_id,
        atName: d.target_at_name,
        status: 'running',
      })
    }
    for (const task of boardTasks) {
      if (!task.last_dispatch_id) continue
      put({
        dispatchId: task.last_dispatch_id,
        atName: task.target_at_name || undefined,
        taskId: task.task_id,
        status: task.status,
        title: taskTitle(task.prompt, 16),
      })
    }
    return [...map.values()]
  }, [
    liveDispatches,
    lastDispatches,
    boardTasks,
    selectedProject?.default_lead_at,
  ])

  useEffect(() => {
    if (!workerRails.length) {
      setActiveRailId('')
      return
    }
    if (workerRails.some((r) => r.dispatchId === activeRailId)) return
    const running = workerRails.find(
      (r) => r.status === 'in_progress' || r.status === 'running',
    )
    setActiveRailId((running || workerRails[workerRails.length - 1]).dispatchId)
  }, [workerRails, activeRailId])

  const refresh = useCallback(async () => {
    setError('')
    const gen = ++refreshGen.current
    try {
      const uid = userIdRef.current
      const [ps, eps, brs] = await Promise.all([
        teamApi.listProjects(),
        teamApi.listEndpoints(uid),
        teamApi.listBridges(uid)
      ])
      if (gen !== refreshGen.current) return
      setProjects(ps)
      setEndpoints(eps)
      setBridges(brs)
      // Keep the current selection even if this snapshot is stale (create-in-flight).
      // Never snap to ps[0] — that stole newly created empty projects.

      const candEntries = await Promise.all(
        brs.map(async (b) => {
          try {
            const cands = await teamApi.listCandidates(b.bridge_id)
            return [b.bridge_id, cands] as const
          } catch {
            return [b.bridge_id, []] as const
          }
        })
      )
      if (gen !== refreshGen.current) return
      setCandidatesByBridge(Object.fromEntries(candEntries))

      const healthEntries = await Promise.all(
        eps.map(async (ep) => {
          try {
            return [
              ep.endpoint_id,
              await teamApi.getEndpointHealth(ep.endpoint_id)
            ] as const
          } catch {
            return [
              ep.endpoint_id,
              {
                endpoint_id: ep.endpoint_id,
                state: ep.status || 'unknown'
              }
            ] as const
          }
        })
      )
      if (gen !== refreshGen.current) return
      setHealthMap(Object.fromEntries(healthEntries))
      const pid = projectIdRef.current
      if (pid) {
        try {
          const [tasks, asks] = await Promise.all([
            teamApi.listTasks(pid),
            teamApi.listPermissionAsks(pid),
          ])
          if (projectIdRef.current === pid) {
            setBoardTasks(tasks)
            setPermissionAsks(asks)
          }
        } catch {
          /* board is best-effort */
        }
      } else {
        setBoardTasks([])
        setPermissionAsks([])
      }
      try {
        const flags = await teamApi.getTeamFlags()
        if (gen === refreshGen.current) setMaxParallel(flags.maxParallel)
      } catch {
        /* flags are best-effort */
      }
      setReady(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setReady(true)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Always poll while on Team page so daemon connect / candidates show up
  // without requiring a manual Refresh (pair flow used to be the only trigger).
  useEffect(() => {
    const id = window.setInterval(() => {
      void refresh()
    }, 3000)
    return () => window.clearInterval(id)
  }, [refresh])

  // Surface a one-shot banner when a Bridge becomes online.
  const onlineBridgeIds = bridges
    .filter((b) => b.status === 'online')
    .map((b) => b.bridge_id)
    .sort()
    .join(',')
  useEffect(() => {
    if (!onlineBridgeIds) return
    const first = bridges.find((b) => b.status === 'online')
    if (!first) return
    setLastOkHint(
      `Bridge online: ${first.machine_label || first.bridge_id} · ` +
        `${(first.agents || []).map((a) => `@${a.at_name}`).join(' ') || 'no agents yet — enable a candidate'}`
    )
  }, [onlineBridgeIds]) // eslint-disable-line react-hooks/exhaustive-deps

  const sortedBridges = useMemo(() => {
    return [...bridges].sort((a, b) => {
      const ao = a.status === 'online' ? 0 : 1
      const bo = b.status === 'online' ? 0 : 1
      if (ao !== bo) return ao - bo
      return String(b.last_heartbeat || '').localeCompare(
        String(a.last_heartbeat || '')
      )
    })
  }, [bridges])

  const fleetColumns = useMemo(() => {
    const reauth: TeamEndpoint[] = []
    const agents: TeamEndpoint[] = []
    for (const ep of endpoints) {
      const h = healthMap[ep.endpoint_id]
      const state = h?.state || ep.status
      if (state === 'need_reauth') reauth.push(ep)
      else agents.push(ep)
    }
    return { bridges, agents, reauth }
  }, [bridges, endpoints, healthMap])

  useEffect(() => {
    setLiveDispatches([])
    setActiveRailId('')
    setLastDispatches([])
  }, [projectId])

  useEffect(() => {
    if (!projectId) return
    const es = teamApi.openEventStream(projectId, (ev) => {
      if (ev.type === 'team.circuit_open') {
        const reason =
          (typeof ev.payload?.reason === 'string' && ev.payload.reason) ||
          t.team.circuitOpen
        setCircuitNotice(
          `${t.team.circuitOpen}${ev.at_name ? ` (@${ev.at_name})` : ''} ${reason}`,
        )
      }
      if (ev.dispatch_id && ev.type === 'team.dispatch_start') {
        setLiveDispatches((prev) => {
          if (prev.some((r) => r.dispatchId === ev.dispatch_id)) return prev
          return [
            ...prev,
            {
              dispatchId: ev.dispatch_id as string,
              atName: ev.at_name || undefined,
              status: 'running',
            },
          ]
        })
      }
      if (
        ev.dispatch_id &&
        (ev.type === 'team.dispatch_waiting_approval' ||
          ev.type === 'team.permission_requested')
      ) {
        setLiveDispatches((prev) =>
          prev.map((r) =>
            r.dispatchId === ev.dispatch_id
              ? { ...r, status: 'waiting_approval', atName: ev.at_name || r.atName }
              : r,
          ),
        )
        void teamApi.listPermissionAsks(projectId).then(setPermissionAsks).catch(() => {})
        void teamApi.listTasks(projectId).then(setBoardTasks).catch(() => {})
      }
      if (ev.type === 'team.permission_resolved') {
        void teamApi.listPermissionAsks(projectId).then(setPermissionAsks).catch(() => {})
        void teamApi.listTasks(projectId).then(setBoardTasks).catch(() => {})
        setLiveDispatches((prev) =>
          prev.map((r) =>
            r.dispatchId === ev.dispatch_id
              ? { ...r, status: 'running' }
              : r,
          ),
        )
      }
      if (
        ev.dispatch_id &&
        (ev.type === 'team.dispatch_done' ||
          ev.type === 'team.dispatch_error' ||
          ev.type === 'team.dispatch_cancelled')
      ) {
        const nextStatus =
          ev.type === 'team.dispatch_done'
            ? 'done'
            : ev.type === 'team.dispatch_cancelled'
              ? 'cancelled'
              : 'error'
        setLiveDispatches((prev) =>
          prev.map((r) =>
            r.dispatchId === ev.dispatch_id
              ? { ...r, status: nextStatus, atName: ev.at_name || r.atName }
              : r,
          ),
        )
      }
      if (
        ev.type === 'task.update' ||
        ev.type === 'team.dispatch_done' ||
        ev.type === 'team.dispatch_error' ||
        ev.type === 'team.dispatch_start'
      ) {
        void teamApi.listTasks(projectId).then(setBoardTasks).catch(() => {
          /* ignore */
        })
      }
    })
    return () => es.close()
  }, [projectId])

  const run = async (action: PendingAction, fn: () => Promise<void>) => {
    setPending(action)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPending(null)
    }
  }

  const onSaveCaps = (endpointId: string, raw: string) =>
    void run('caps', async () => {
      const capabilities = raw
        .split(/[,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      await teamApi.patchEndpoint(endpointId, { capabilities })
      await refresh()
    })

  const onCreateProject = () =>
    void run('createProject', async () => {
      const eps =
        endpoints.length > 0
          ? endpoints
          : await teamApi.listEndpoints(userIdRef.current)
      const lead = pickDefaultLead(eps)
      const p = await teamApi.createProject({
        name: `team-${Date.now().toString(36).slice(-4)}`,
        workspace_path: '',
        ...(lead ? { default_lead_at: lead } : {})
      })
      projectIdRef.current = p.project_id
      setProjectId(p.project_id)
      setBoardTasks([])
      setProjects((prev) => {
        if (prev.some((x) => x.project_id === p.project_id)) return prev
        return [p, ...prev]
      })
      await refresh()
    })

  const onChangeLead = (atName: string) => {
    if (!selectedProject) return
    void run('updateLead', async () => {
      const p = await teamApi.createProject({
        project_id: selectedProject.project_id,
        name: selectedProject.name,
        workspace_path: selectedProject.workspace_path || '',
        default_lead_at: atName
      })
      setProjects((prev) =>
        prev.map((x) => (x.project_id === p.project_id ? { ...x, ...p } : x))
      )
    })
  }

  const onRegisterCloud = () =>
    void run('register', async () => {
      await teamApi.upsertEndpoint({
        at_name: newAtName.replace(/^@/, ''),
        owner_user_id: userIdRef.current,
        adapter_kind: 'cloud',
        status: 'online',
        machine_label: 'web-demo'
      })
      await refresh()
    })

  const onConnectMachine = () =>
    void run('pair', async () => {
      const tok = await teamApi.createBridgePairToken(userIdRef.current)
      const cmd = buildConnectCommand(tok.pair_code)
      setPairCode(tok.pair_code)
      setConnectCmd(cmd)
      setCopied(false)
    })

  const onCopyCommand = () =>
    void run('copy', async () => {
      if (!connectCmd) return
      await navigator.clipboard.writeText(connectCmd)
      setCopied(true)
    })

  const onEnableCandidate = (
    bridgeId: string,
    cand: RuntimeCandidate,
    atName: string
  ) =>
    void run('enable', async () => {
      if (sessionMode === 'attach' && cand.attachable === false) {
        setError(t.team.cannotAttach)
        return
      }
      const name = (atName || cand.label || 'agent').replace(/^@/, '')
      await teamApi.enableAgent({
        bridgeId,
        at_name: name,
        runtime: cand.runtime || 'claude_code',
        adapter_kind: cand.adapter_kind || 'acp',
        candidate_id: cand.candidate_id,
        status: 'offline'
      })
      await refresh()
    })

  const onEnableNamed = (bridgeId: string) =>
    void run('enable', async () => {
      const name = (enableName[bridgeId] || newAtName || 'me').replace(/^@/, '')
      await teamApi.enableAgent({
        bridgeId,
        at_name: name,
        runtime: 'claude_code',
        adapter_kind: 'acp',
        status: 'offline'
      })
      setEnableName((prev) => ({ ...prev, [bridgeId]: '' }))
      await refresh()
    })

  const onSend = () => {
    if (!projectIdRef.current) {
      setError(t.team.needsCard)
      return
    }
    if (!message.trim()) return
    const mentioned = [...message.matchAll(/@([A-Za-z0-9_\-]+)/g)].map(
      (m) => m[1].toLowerCase(),
    )
    const targets = mentioned.length
      ? endpoints.filter((ep) => mentioned.includes(ep.at_name.toLowerCase()))
      : []
    for (const ep of targets) {
      const state = healthMap[ep.endpoint_id]?.state || ep.status
      if (state === 'offline' || state === 'need_reauth') {
        setError(`${t.team.offlineTarget}: @${ep.at_name}`)
        return
      }
      const runtime = `${ep.runtime || ''} ${ep.adapter_kind || ''}`.toLowerCase()
      if (
        sessionMode === 'attach' &&
        (runtime.includes('cursor') || ep.at_name.toLowerCase().includes('cursor'))
      ) {
        setError(t.team.cannotAttach)
        return
      }
    }
    void run('send', async () => {
      const res = await teamApi.sendMessage({
        projectId: projectIdRef.current,
        content: message,
        senderUserId: userIdRef.current,
        threadId,
        sessionMode,
        wait: false
      })
      setLastDispatches(res.dispatches || [])
      setLiveDispatches((prev) => {
        const next = [...prev]
        for (const d of res.dispatches || []) {
          if (next.some((r) => r.dispatchId === d.dispatch_id)) continue
          next.push({
            dispatchId: d.dispatch_id,
            atName: d.target_at_name,
            status: 'running',
          })
        }
        return next
      })
      if (res.needs_card) setError(t.team.needsCard)
      if (!res.dispatches?.length && !res.needs_card) {
        setError(t.team.noDispatch)
        setLastOkHint('')
        return
      }
      const targets = (res.dispatches || [])
        .map((d) => `@${d.target_at_name}`)
        .join(', ')
      const receiptLine = (res.receipts || [])
        .map((r) => r.text)
        .filter(Boolean)
        .join(' · ')
      setLastOkHint(
        receiptLine || `${t.team.dispatched} ${targets}`
      )
      try {
        setBoardTasks(await teamApi.listTasks(projectIdRef.current))
      } catch {
        /* ignore */
      }
    })
  }

  const onAskLead = (taskId: string) => {
    if (!projectIdRef.current) return
    setAskLeadTaskId(taskId)
    void run('askLead', async () => {
      await teamApi.askLead(projectIdRef.current, taskId)
      setBoardTasks(await teamApi.listTasks(projectIdRef.current))
      setLastOkHint(t.team.askLeadHint)
    }).finally(() => setAskLeadTaskId(null))
  }

  const onApprovePlan = (taskId: string) => {
    if (!projectIdRef.current) return
    setApprovePlanTaskId(taskId)
    void run('approvePlan', async () => {
      await teamApi.approvePlan(projectIdRef.current, taskId)
      setBoardTasks(await teamApi.listTasks(projectIdRef.current))
    }).finally(() => setApprovePlanTaskId(null))
  }

  const onRunBoard = () => {
    if (!projectIdRef.current) {
      setError(t.team.needProject)
      return
    }
    void run('runBoard', async () => {
      const res = await teamApi.runCoordinator(projectIdRef.current)
      setBoardTasks(await teamApi.listTasks(projectIdRef.current))
      const n = res.dispatched?.length || 0
      setLastOkHint(
        n
          ? `${t.team.runBoard} × ${n}`
          : `${t.team.runBoard}: ${res.skipped?.map((s) => s.reason).join(', ') || '—'}`
      )
    })
  }

  const onCancelLast = () => {
    const id = lastDispatches[0]?.dispatch_id || activeRailId
    if (!id) return
    void run('cancel', async () => {
      await teamApi.cancelDispatch(id)
    })
  }

  const onCancelRail = (dispatchId: string) => {
    void run('cancel', async () => {
      await teamApi.cancelDispatch(dispatchId)
    })
  }

  const onTogglePlanGate = (on: boolean) => {
    if (!selectedProject) return
    void run('updateLead', async () => {
      const p = await teamApi.createProject({
        project_id: selectedProject.project_id,
        name: selectedProject.name,
        workspace_path: selectedProject.workspace_path || '',
        default_lead_at: selectedProject.default_lead_at || undefined,
        plan_gate: on,
      })
      setProjects((prev) =>
        prev.map((x) => (x.project_id === p.project_id ? { ...x, ...p } : x)),
      )
    })
  }

  const insertMention = (atName: string) => {
    const token = `@${atName}`
    setMessage((prev) =>
      prev.includes(token) ? prev : `${token} ${prev.replace(/^@\S+\s*/, '')}`
    )
  }

  const onTeamRoute = location.pathname === '/team'
  const unboundCandidates = (bridgeId: string) =>
    [...(candidatesByBridge[bridgeId] || [])]
      .filter((c) => !c.bound_endpoint_id)
      .sort((a, b) => {
        const as = a.runtime_session_id ? 0 : 1
        const bs = b.runtime_session_id ? 0 : 1
        if (as !== bs) return as - bs
        const aa = a.attachable ? 0 : 1
        const ba = b.attachable ? 0 : 1
        if (aa !== ba) return aa - ba
        return String(a.label || '').localeCompare(String(b.label || ''))
      })

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden rounded-[16px] bg-msa-bg-1 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Title level={3} className="!mb-1 !text-msa-text-1">
            {t.team.title}
          </Title>
          <Paragraph className="!mb-0 !text-msa-text-2">
            {t.team.subtitle}
          </Paragraph>
          <Text type="secondary" className="!text-xs">
            route: {location.pathname}
            {onTeamRoute ? ' · OK' : ''}
          </Text>
        </div>
        <Space>
          <Button
            data-testid="team-fleet"
            onClick={() => setFleetOpen(true)}
          >
            {t.team.fleetSettings}
          </Button>
          <Button
            onClick={() => void run('refresh', refresh)}
            loading={pending === 'refresh'}
          >
            {t.team.refresh}
          </Button>
        </Space>
      </div>

      {error ? (
        <Alert
          type="error"
          showIcon
          closable
          onClose={() => setError('')}
          message={error}
        />
      ) : null}

      {lastOkHint ? (
        <Alert
          type="success"
          showIcon
          closable
          onClose={() => setLastOkHint('')}
          message={lastOkHint}
        />
      ) : null}

      {circuitNotice ? (
        <Alert
          type="warning"
          showIcon
          closable
          data-testid="team-circuit-open"
          onClose={() => setCircuitNotice('')}
          message={circuitNotice}
        />
      ) : null}

      {ready && !projectId ? (
        <Alert type="info" showIcon message={t.team.hintStart} />
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
        <Drawer
          title={t.team.fleetSettings}
          open={fleetOpen}
          onClose={() => setFleetOpen(false)}
          width={420}
        >
          <div className="flex flex-col gap-3">
          <div
            className="grid grid-cols-3 gap-2"
            data-testid="team-fleet-health"
          >
            <div className="rounded border border-msa-line-1 p-2">
              <Text type="secondary" className="!text-[10px]">
                {t.team.fleetBridges}
              </Text>
              <div className="mt-1 flex flex-col gap-1">
                {fleetColumns.bridges.length === 0 ? (
                  <Text type="secondary" className="!text-[10px]">
                    —
                  </Text>
                ) : (
                  fleetColumns.bridges.map((b) => (
                    <Tag
                      key={b.bridge_id}
                      color={statusColor(b.status)}
                      className="!m-0 !text-[10px]"
                    >
                      {b.machine_label || b.bridge_id.slice(0, 8)} · {b.status}
                    </Tag>
                  ))
                )}
              </div>
            </div>
            <div className="rounded border border-msa-line-1 p-2">
              <Text type="secondary" className="!text-[10px]">
                {t.team.fleetAgents}
              </Text>
              <div className="mt-1 flex flex-col gap-1">
                {fleetColumns.agents.map((ep) => {
                  const state = healthMap[ep.endpoint_id]?.state || ep.status
                  return (
                    <Tag
                      key={ep.endpoint_id}
                      color={statusColor(state)}
                      className="!m-0 !text-[10px]"
                    >
                      @{ep.at_name} · {state}
                    </Tag>
                  )
                })}
              </div>
            </div>
            <div className="rounded border border-msa-line-1 p-2">
              <Text type="secondary" className="!text-[10px]">
                {t.team.fleetReauth}
              </Text>
              <div className="mt-1 flex flex-col gap-1">
                {fleetColumns.reauth.length === 0 ? (
                  <Text type="secondary" className="!text-[10px]">
                    —
                  </Text>
                ) : (
                  fleetColumns.reauth.map((ep) => (
                    <Tag
                      key={ep.endpoint_id}
                      color="error"
                      className="!m-0 !text-[10px]"
                    >
                      @{ep.at_name}
                    </Tag>
                  ))
                )}
              </div>
            </div>
          </div>
          <Text type="secondary" className="!text-[10px]">
            {t.team.parallelSlots}{' '}
            {boardTasks.filter(
              (task) =>
                task.status === 'in_progress' || task.waiting_approval,
            ).length}
            /{maxParallel}
          </Text>
          <Card size="small" title={t.team.identity}>
            <Space direction="vertical" className="w-full" size="middle">
              <Text className="!text-xs">{t.team.ownerThisWebui}</Text>
              <Text code>{userId || TEAM_OWNER_ID}</Text>
              <Text type="secondary" className="!text-xs">
                {t.team.leadHint}
              </Text>
            </Space>
          </Card>

          <Card size="small" title={t.team.connectMachine}>
            <Space direction="vertical" className="w-full" size="small">
              <Text type="secondary" className="!text-xs">
                {t.team.connectHint}
              </Text>
              <Button
                type="primary"
                block
                onClick={onConnectMachine}
                loading={pending === 'pair'}
              >
                {t.team.generateConnect}
              </Button>
              {pairCode ? (
                <Alert
                  type="info"
                  message={
                    <div className="space-y-2">
                      <div>
                        pair: <Text code>{pairCode}</Text>
                      </div>
                      <pre className="m-0 whitespace-pre-wrap break-all rounded bg-msa-fill-2 p-2 font-mono text-[11px]">
                        {connectCmd}
                      </pre>
                      <Button
                        size="small"
                        type="primary"
                        onClick={onCopyCommand}
                        loading={pending === 'copy'}
                      >
                        {copied ? t.team.copied : t.team.copyCommand}
                      </Button>
                      <div>
                        <Text type="secondary" className="!text-xs">
                          {t.team.afterConnect}
                        </Text>
                      </div>
                    </div>
                  }
                />
              ) : null}
            </Space>
          </Card>

          <Card size="small" title={t.team.bridges}>
            <Space direction="vertical" className="w-full" size="middle">
              {sortedBridges.length === 0 ? (
                <Text type="secondary">{t.team.noBridges}</Text>
              ) : null}
              {sortedBridges.map((b) => {
                const agents = b.agents || []
                const cands = unboundCandidates(b.bridge_id)
                return (
                  <div
                    key={b.bridge_id}
                    className="rounded-lg border border-msa-line-1 p-3"
                  >
                    <div className="mb-2 flex items-center gap-2">
                      <Text strong>
                        {b.machine_label || '本机'}
                      </Text>
                      <Tag color={statusColor(b.status)}>{b.status}</Tag>
                    </div>
                    <Text type="secondary" className="!text-xs">
                      {b.bridge_id.slice(0, 14)}
                      {b.machine_label ? '' : ' · 未设 machine_label'}
                      {b.last_heartbeat
                        ? ` · hb ${String(b.last_heartbeat).slice(11, 19)}`
                        : ''}
                    </Text>

                    <div className="mt-2 space-y-1">
                      <Text type="secondary" className="!text-xs">
                        {t.team.agents}
                      </Text>
                      {agents.length === 0 ? (
                        <Text type="secondary" className="!text-xs">
                          {t.team.noAgentsOnBridge}
                        </Text>
                      ) : null}
                      {agents.map((ep) => {
                        const h = healthMap[ep.endpoint_id]
                        const state = h?.state || ep.status
                        return (
                          <button
                            key={ep.endpoint_id}
                            type="button"
                            onClick={() => insertMention(ep.at_name)}
                            className="w-full rounded border border-msa-line-1 px-2 py-1.5 text-left hover:bg-msa-fill-2"
                          >
                            <div className="flex items-center gap-2">
                              <Text strong>@{ep.at_name}</Text>
                              <Tag color={statusColor(state)}>{state}</Tag>
                            </div>
                            <Text type="secondary" className="!text-xs">
                              {ep.runtime || ep.adapter_kind}
                            </Text>
                            <Input
                              size="small"
                              className="!mt-1"
                              defaultValue={(ep.capabilities || []).join(', ')}
                              placeholder={t.team.capabilitiesHint}
                              onClick={(e) => e.stopPropagation()}
                              onBlur={(e) =>
                                onSaveCaps(ep.endpoint_id, e.target.value)
                              }
                            />
                          </button>
                        )
                      })}
                    </div>

                    {cands.length > 0 ? (
                      <div className="mt-3 space-y-2">
                        <Text type="secondary" className="!text-xs">
                          {t.team.candidates}
                        </Text>
                        {cands.map((c) => {
                          const meta = (c.meta || {}) as Record<
                            string,
                            unknown
                          >
                          const preview =
                            (typeof meta.preview === 'string' && meta.preview) ||
                            ''
                          const mtime =
                            typeof meta.mtime === 'number' ? meta.mtime : null
                          const when = mtime
                            ? formatSessionWhen(mtime)
                            : ''
                          const cwdPath =
                            (typeof meta.cwd === 'string' && meta.cwd) ||
                            c.cwd ||
                            ''
                          const cwdName = cwdPath
                            ? cwdPath.replace(/\/+$/, '').split('/').pop() || ''
                            : ''
                          const sid = (c.runtime_session_id || '').slice(0, 8)
                          const suggested = suggestAtName(c)
                          const atValue =
                            enableName[c.candidate_id] ?? suggested
                          const isSession = Boolean(c.runtime_session_id)
                          const headline = isSession
                            ? `会话 · 建议 @${suggested}`
                            : c.label || c.runtime
                          const detailParts = [
                            preview ? `“${preview}”` : '',
                            cwdName ? `📁 ${cwdName}` : '',
                            when,
                            sid ? `id ${sid}` : ''
                          ].filter(Boolean)
                          return (
                          <div
                            key={c.candidate_id}
                            className="rounded border border-dashed border-msa-line-1 px-2 py-2"
                          >
                            <div className="mb-1 flex items-center gap-2">
                              <Text className="!text-xs !font-medium">
                                {headline}
                              </Text>
                              {c.attachable ? (
                                <Tag color="blue">attach</Tag>
                              ) : (
                                <Tag>发现</Tag>
                              )}
                            </div>
                            <Text type="secondary" className="!text-xs">
                              {detailParts.join(' · ') || c.label || c.runtime}
                            </Text>
                            <div className="mt-2 flex gap-1">
                              <Input
                                size="small"
                                addonBefore="@"
                                placeholder={suggested}
                                value={atValue}
                                onChange={(e) =>
                                  setEnableName((prev) => ({
                                    ...prev,
                                    [c.candidate_id]: e.target.value
                                  }))
                                }
                              />
                              <Button
                                size="small"
                                type="primary"
                                loading={pending === 'enable'}
                                onClick={() =>
                                  onEnableCandidate(
                                    b.bridge_id,
                                    c,
                                    enableName[c.candidate_id] || suggested
                                  )
                                }
                              >
                                {t.team.enable}
                              </Button>
                            </div>
                          </div>
                          )
                        })}
                      </div>
                    ) : null}

                    <div className="mt-3 flex gap-1">
                      <Input
                        size="small"
                        addonBefore="@"
                        placeholder="me"
                        value={enableName[b.bridge_id] ?? ''}
                        onChange={(e) =>
                          setEnableName((prev) => ({
                            ...prev,
                            [b.bridge_id]: e.target.value
                          }))
                        }
                      />
                      <Button
                        size="small"
                        loading={pending === 'enable'}
                        onClick={() => onEnableNamed(b.bridge_id)}
                      >
                        {t.team.addAgent}
                      </Button>
                    </div>
                  </div>
                )
              })}
            </Space>
          </Card>

          <Card size="small" title={t.team.cloudDemo}>
            <Space direction="vertical" className="w-full" size="small">
              <Text type="secondary" className="!text-xs">
                {t.team.cloudDemoHint}
              </Text>
              <Input
                addonBefore="@"
                value={newAtName}
                onChange={(e) => setNewAtName(e.target.value)}
              />
              <Button
                block
                onClick={onRegisterCloud}
                loading={pending === 'register'}
              >
                {t.team.registerCloud}
              </Button>
              {endpoints.filter((e) => e.adapter_kind === 'cloud').length >
              0 ? (
                <div className="space-y-1">
                  {endpoints
                    .filter((e) => e.adapter_kind === 'cloud')
                    .map((ep) => (
                      <button
                        key={ep.endpoint_id}
                        type="button"
                        onClick={() => insertMention(ep.at_name)}
                        className="w-full rounded border border-msa-line-1 px-2 py-1 text-left hover:bg-msa-fill-2"
                      >
                        <Text strong>@{ep.at_name}</Text>
                        <Tag className="!ml-2" color="default">
                          cloud
                        </Tag>
                        <Input
                          size="small"
                          className="!mt-1"
                          defaultValue={(ep.capabilities || []).join(', ')}
                          placeholder={t.team.capabilitiesHint}
                          onClick={(e) => e.stopPropagation()}
                          onBlur={(e) =>
                            onSaveCaps(ep.endpoint_id, e.target.value)
                          }
                        />
                      </button>
                    ))}
                </div>
              ) : null}
            </Space>
          </Card>
        </div>
        </Drawer>

        <div className="flex min-h-0 flex-col gap-2 overflow-hidden">
          <Card size="small" title={t.team.dispatch} className="shrink-0">
            <Space direction="vertical" className="w-full" size="small">
              <div className="flex flex-col gap-2 lg:flex-row">
                <Select
                  className="min-w-0 flex-1"
                  data-testid="team-project-select"
                  placeholder={t.team.searchProject}
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  value={projectId || undefined}
                  onChange={(id) => setProjectId(id || '')}
                  options={sortedProjects.map((p) => ({
                    value: p.project_id,
                    label: `${p.name} · @${p.default_lead_at || '—'} · ${p.project_id}`
                  }))}
                />
                <Button
                  data-testid="team-new-project"
                  onClick={onCreateProject}
                  loading={pending === 'createProject'}
                >
                  {t.team.newProject}
                </Button>
                <Select
                  className="lg:w-56"
                  data-testid="team-lead-select"
                  placeholder={t.team.lead}
                  showSearch
                  optionFilterProp="label"
                  disabled={!selectedProject}
                  loading={pending === 'updateLead'}
                  value={selectedProject?.default_lead_at || undefined}
                  onChange={onChangeLead}
                  options={endpoints.map((e) => ({
                    value: e.at_name,
                    label: `@${e.at_name} · ${e.adapter_kind} · ${e.status}`
                  }))}
                />
                <div className="flex items-center gap-2">
                  <Switch
                    data-testid="team-plan-gate"
                    checked={Boolean(selectedProject?.plan_gate)}
                    disabled={!selectedProject}
                    onChange={onTogglePlanGate}
                  />
                  <Text className="!text-xs">{t.team.planGate}</Text>
                </div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Select
                  className="sm:w-64"
                  value={sessionMode}
                  onChange={setSessionMode}
                  options={[
                    { value: 'auto', label: t.team.sessionAuto },
                    { value: 'attach', label: t.team.sessionAttach },
                    { value: 'fresh', label: t.team.sessionFresh }
                  ]}
                />
                <Input
                  className="flex-1"
                  addonBefore="thread"
                  value={threadId}
                  onChange={(e) => setThreadId(e.target.value)}
                />
              </div>
              <Input.TextArea
                rows={2}
                data-testid="team-composer"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder={t.team.placeholder}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault()
                    onSend()
                  }
                }}
              />
              <Space>
                <Button
                  type="primary"
                  data-testid="team-send"
                  onClick={onSend}
                  loading={pending === 'send'}
                  disabled={!projectId}
                >
                  {t.team.send}
                </Button>
                <Button
                  danger
                  onClick={onCancelLast}
                  loading={pending === 'cancel'}
                  disabled={!lastDispatches[0]}
                >
                  {t.team.cancelLast}
                </Button>
              </Space>
              {!projectId ? (
                <Text type="warning">{t.team.needProject}</Text>
              ) : null}
            </Space>
          </Card>

          <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-hidden lg:grid-cols-[minmax(280px,34%)_1fr]">
            <div className="flex min-h-0 flex-col gap-2 overflow-hidden">
              {permissionAsks.length > 0 ? (
                <Card
                  size="small"
                  title={t.team.permissionInbox}
                  data-testid="team-permission-inbox"
                >
                  <Space direction="vertical" className="w-full" size="small">
                    {permissionAsks.map((ask) => (
                      <PermissionAskCard
                        key={ask.request_id}
                        ask={ask}
                        onResolved={() => {
                          void teamApi
                            .listPermissionAsks(projectId)
                            .then(setPermissionAsks)
                          void teamApi.listTasks(projectId).then(setBoardTasks)
                        }}
                      />
                    ))}
                  </Space>
                </Card>
              ) : null}
              <TeamKanban
                tasks={boardTasks}
                activeRailId={activeRailId}
                onSelectDispatch={setActiveRailId}
                onRunBoard={onRunBoard}
                onAskLead={onAskLead}
                onApprovePlan={onApprovePlan}
                askLeadTaskId={askLeadTaskId}
                approvePlanTaskId={approvePlanTaskId}
                runPending={pending === 'runBoard'}
                disabled={!projectId}
                endpoints={endpoints}
                onPatchTask={(taskId, body) => {
                  if (!projectId) return
                  void teamApi
                    .patchTask(projectId, taskId, body)
                    .then(() => teamApi.listTasks(projectId))
                    .then(setBoardTasks)
                    .catch((e) =>
                      setError(e instanceof Error ? e.message : String(e)),
                    )
                }}
                onOpenFile={(path) => {
                  if (!projectId) return
                  void teamApi
                    .readLocalFile(projectId, path)
                    .then((file) => {
                      if (file.binary || !file.text) {
                        void navigator.clipboard?.writeText(file.path)
                        setLastOkHint(t.team.artifactBinary)
                        return
                      }
                      setArtifactPreview({
                        name: file.name,
                        text: file.text,
                      })
                    })
                    .catch((e) => {
                      void navigator.clipboard?.writeText(path)
                      setLastOkHint(
                        `${t.team.artifactCopied}: ${path}${
                          e instanceof Error ? ` (${e.message})` : ''
                        }`,
                      )
                    })
                }}
                onFocusInbox={() => {
                  document
                    .querySelector('[data-testid="team-permission-inbox"]')
                    ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
                }}
              />
            </div>

            {workerRails.length > 0 ? (
              <WorkerRails
                rails={workerRails}
                activeId={activeRailId}
                onSelect={setActiveRailId}
                projectId={projectId}
                senderUserId={userId}
                onCancelRail={onCancelRail}
                cancelPendingId={pending === 'cancel' ? activeRailId : null}
                onFollowUpSent={() => {
                  void teamApi.listTasks(projectId).then(setBoardTasks)
                }}
              />
            ) : (
              <Card size="small" className="min-h-0 flex-1">
                <Text type="secondary" className="!text-xs">
                  {t.team.noWorkerRails}
                </Text>
              </Card>
            )}
          </div>
        </div>
      </div>
      <Modal
        open={Boolean(artifactPreview)}
        title={artifactPreview?.name || t.team.artifactPreview}
        onCancel={() => setArtifactPreview(null)}
        footer={null}
        width={720}
      >
        <pre className="m-0 max-h-[60vh] overflow-auto whitespace-pre-wrap break-all font-mono text-xs">
          {artifactPreview?.text}
        </pre>
      </Modal>
    </div>
  )
}
