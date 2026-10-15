export const TEAM_OWNER_ID = 'webui'

export function loadTeamOwnerId(): string {
  if (typeof window === 'undefined') return TEAM_OWNER_ID
  try {
    const raw = window.localStorage.getItem('msa.team.ownerId')
    if (!raw || raw === 'u1') return TEAM_OWNER_ID
    return raw
  } catch {
    return TEAM_OWNER_ID
  }
}

const TEAM_BASE = '/api/v1/team'

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail
    let msg = res.statusText
    if (typeof detail === 'string') msg = detail
    else if (detail && typeof detail === 'object' && 'message' in detail) {
      msg = String((detail as { message: string }).message)
    } else if (detail && typeof detail === 'object' && 'error' in detail) {
      msg = String((detail as { error: string }).error)
    }
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return body as T
}

export type TeamProject = {
  project_id: string
  name: string
  workspace_path?: string
  default_lead_at?: string | null
  plan_gate?: boolean
  created_at?: string
}

export type TeamEndpoint = {
  endpoint_id: string
  at_name: string
  owner_user_id: string
  adapter_kind: string
  status: string
  machine_label?: string
  bridge_id?: string | null
  runtime?: string
  last_heartbeat?: string | null
  capabilities?: string[]
}

const REACHABLE = new Set(['online', 'busy'])
const CLOUD_KINDS = new Set(['cloud', 'ms_agent'])
const PREFERRED_LEADS = ['ms_agent', 'planner']

/** Pick a Lead for a new Team project. Prefer online cloud / ms_agent; never default to @me. */
export function pickDefaultLead(endpoints: TeamEndpoint[]): string {
  const at = (e: TeamEndpoint) => e.at_name.replace(/^@/, '')
  const notMe = (e: TeamEndpoint) => at(e).toLowerCase() !== 'me'
  const reachable = endpoints.filter((e) => REACHABLE.has(e.status))
  const pool = (reachable.length ? reachable : endpoints).filter(notMe)
  const search = pool.length ? pool : endpoints.filter(notMe)
  for (const name of PREFERRED_LEADS) {
    const hit = search.find((e) => at(e) === name)
    if (hit) return at(hit)
  }
  const cloud = search.find((e) => CLOUD_KINDS.has(e.adapter_kind))
  if (cloud) return at(cloud)
  return search[0] ? at(search[0]) : ''
}

export type MachineBridge = {
  bridge_id: string
  owner_user_id: string
  machine_label?: string
  status: string
  current_instance_id?: string | null
  last_heartbeat?: string | null
  agents?: TeamEndpoint[]
}

export type RuntimeCandidate = {
  candidate_id: string
  bridge_id: string
  runtime: string
  adapter_kind?: string
  label?: string
  cwd?: string | null
  runtime_session_id?: string | null
  attachable?: boolean
  bound_endpoint_id?: string | null
  meta?: Record<string, unknown>
}

export type EndpointHealth = {
  endpoint_id: string
  state: string
  last_seen?: string | null
  reason?: string
  detail?: string
}

export type TeamEvent = {
  type: string
  project_id?: string | null
  dispatch_id?: string | null
  endpoint_id?: string | null
  at_name?: string | null
  payload?: Record<string, unknown>
  created_at?: string
}

export type PermissionAsk = {
  request_id: string
  call_id?: string
  tool_name?: string
  tool_args?: Record<string, unknown>
  at_name?: string
  dispatch_id?: string
  context?: string
  options?: Array<{ optionId?: string; name?: string; kind?: string }>
  suggestions?: string[]
}

export type TeamTask = {
  task_id: string
  project_id: string
  status: string
  prompt: string
  target_at_name?: string | null
  target_endpoint_id?: string | null
  blocked_by?: string[]
  last_dispatch_id?: string | null
  parent_task_id?: string | null
  claim_version?: number
  result_summary?: string | null
  result_text?: string | null
  result_source?: 'result_summary' | 'dispatch_log' | null
  result_truncated?: boolean
  output_artifacts?: string[]
  tags?: string[]
  plan_status?: string | null
  unclaimed?: boolean
  unclaimed_expired?: boolean
  waiting_approval?: boolean
  pending_permission_asks?: PermissionAsk[]
  created_at?: string
}

const TASK_DONE = new Set(['completed', 'failed', 'cancelled'])

/** True only while upstream blockers are unfinished — not merely "has edges". */
export function isCurrentlyBlocked(
  task: TeamTask,
  byId: Map<string, TeamTask>,
): boolean {
  if (TASK_DONE.has(task.status)) return false
  for (const bid of task.blocked_by || []) {
    const blocker = byId.get(bid)
    if (!blocker || blocker.status !== 'completed') return true
  }
  return false
}

export function taskTitle(prompt?: string | null, limit = 28): string {
  const line = (prompt || '').split('\n')[0].trim()
  if (!line) return ''
  if (line.length <= limit) return line
  return `${line.slice(0, limit).trimEnd()}…`
}

export type DispatchEnvelope = {
  dispatch_id: string
  session_mode?: string
  runtime_session_id?: string | null
  session_resolution?: string | null
  target_at_name?: string
  prompt?: string
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.replace(/^\[|\]$/g, '').toLowerCase()
  return (
    host === '' ||
    host === 'localhost' ||
    host === '0.0.0.0' ||
    host === '::' ||
    host === '::1' ||
    host === '127.0.0.1' ||
    host.startsWith('127.')
  )
}

function apiBaseForCli(): string {
  if (typeof window === 'undefined') return ''
  const { protocol, hostname, port } = window.location
  // Dev: Vite on 5173 proxies API to backend 8000 — CLI must hit backend directly.
  if (port === '5173' || port === '5174') {
    return `${protocol}//${hostname}:8000`
  }
  return `${protocol}//${hostname}${port ? `:${port}` : ''}`
}

export function suggestAtName(c: RuntimeCandidate): string {
  const meta = c.meta || {}
  const fromMeta = meta.suggested_at_name
  if (typeof fromMeta === 'string' && fromMeta.trim()) {
    return fromMeta.trim().replace(/^@/, '').slice(0, 32)
  }
  const preview = typeof meta.preview === 'string' ? meta.preview : ''
  const nick = preview.match(
    /(?:你叫|叫我|我叫|call me)\s*[@]?([A-Za-z][\w\-]{0,31}|[\u4e00-\u9fff]{1,16})/i
  )
  if (nick?.[1]) return nick[1].toLowerCase()
  if (c.runtime_session_id) {
    return `${(c.runtime || 'agent').replace(/_code$/, '')}_${c.runtime_session_id.slice(0, 4)}`
  }
  const runtime = (c.runtime || 'agent').replace(/_code$/, '')
  if (runtime === 'cursor_ide') return 'cursor_ide'
  if (runtime === 'claude') return 'claude'
  return runtime || 'agent'
}

/** Format ACP session mtime (unix seconds) for candidate list. */
export function formatSessionWhen(mtimeSec: number): string {
  const ms = mtimeSec > 1e12 ? mtimeSec : mtimeSec * 1000
  const delta = Math.max(0, Date.now() - ms)
  if (delta < 60_000) return 'just now'
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`
  if (delta < 14 * 86_400_000) return `${Math.floor(delta / 86_400_000)}d ago`
  return new Date(ms).toLocaleDateString()
}

export function buildConnectCommand(pairCode: string, apiBase?: string): string {
  // A page opened at localhost cannot be the daemon's target: another machine
  // has no route to this loopback. Prefer the API base the server advertised
  // (its LAN address). A non-loopback page host is already the address the
  // operator chose, so keep that.
  const fromPage = apiBaseForCli()
  const pageHost = typeof window === 'undefined' ? '' : window.location.hostname
  const base =
    !isLoopbackHost(pageHost) && fromPage
      ? fromPage
      : (apiBase || '').trim() || fromPage
  return (
    `python -m ms_agent.bridge.daemon --api-base ${base} ` +
    `--pair-code ${pairCode} ` +
    `--machine-label "$(hostname -s)" ` +
    `--no-auto-me`
  )
}

export const teamApi = {
  async listProjects(): Promise<TeamProject[]> {
    const data = await json<{ projects: TeamProject[] }>(
      await fetch(`${TEAM_BASE}/projects`)
    )
    return data.projects || []
  },

  async createProject(input: {
    project_id?: string
    name: string
    workspace_path?: string
    default_lead_at?: string
    plan_gate?: boolean
  }): Promise<TeamProject> {
    return json(
      await fetch(`${TEAM_BASE}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input)
      })
    )
  },

  async listEndpoints(ownerUserId?: string): Promise<TeamEndpoint[]> {
    const q = ownerUserId
      ? `?owner_user_id=${encodeURIComponent(ownerUserId)}`
      : ''
    const data = await json<{ endpoints: TeamEndpoint[] }>(
      await fetch(`${TEAM_BASE}/endpoints${q}`, {
        headers: ownerUserId ? { 'X-User-Id': ownerUserId } : {}
      })
    )
    return data.endpoints || []
  },

  async getTeamFlags(): Promise<{
    maxParallel: number
    coordinatorEnabled: boolean
  }> {
    const data = await json<{
      feature_flags?: {
        max_parallel_agents_per_project?: number
        coordinator_enabled?: boolean
      }
    }>(await fetch(`${TEAM_BASE}/endpoints`))
    const flags = data.feature_flags || {}
    return {
      maxParallel: Number(flags.max_parallel_agents_per_project || 5),
      coordinatorEnabled: flags.coordinator_enabled !== false,
    }
  },

  async listBridges(ownerUserId?: string): Promise<MachineBridge[]> {
    const q = ownerUserId
      ? `?owner_user_id=${encodeURIComponent(ownerUserId)}`
      : ''
    const data = await json<{ bridges: MachineBridge[] }>(
      await fetch(`${TEAM_BASE}/bridges${q}`)
    )
    return data.bridges || []
  },

  async getBridge(bridgeId: string): Promise<MachineBridge> {
    return json(await fetch(`${TEAM_BASE}/bridges/${bridgeId}`))
  },

  async listCandidates(bridgeId: string): Promise<RuntimeCandidate[]> {
    const data = await json<{ candidates: RuntimeCandidate[] }>(
      await fetch(`${TEAM_BASE}/bridges/${bridgeId}/candidates`)
    )
    return data.candidates || []
  },

  async enableAgent(input: {
    bridgeId: string
    at_name: string
    runtime?: string
    adapter_kind?: string
    candidate_id?: string
    status?: string
  }): Promise<TeamEndpoint> {
    return json(
      await fetch(`${TEAM_BASE}/bridges/${input.bridgeId}/agents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          at_name: input.at_name.replace(/^@/, ''),
          runtime: input.runtime || 'claude_code',
          adapter_kind: input.adapter_kind || 'acp',
          candidate_id: input.candidate_id,
          // Stay offline until Host Bridge heartbeats this agent for real.
          status: input.status || 'offline'
        })
      })
    )
  },

  async upsertEndpoint(body: {
    at_name: string
    owner_user_id: string
    adapter_kind?: string
    status?: string
    machine_label?: string
    bridge_id?: string | null
  }): Promise<TeamEndpoint> {
    return json(
      await fetch(`${TEAM_BASE}/endpoints`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          adapter_kind: 'cloud',
          status: 'online',
          ...body
        })
      })
    )
  },

  async patchEndpoint(
    endpointId: string,
    body: { capabilities: string[] },
  ): Promise<TeamEndpoint> {
    return json(
      await fetch(`${TEAM_BASE}/endpoints/${endpointId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    )
  },

  async getRoster(projectId: string): Promise<
    Array<{
      at_name: string
      tag: string
      completed: number
      failed: number
      wait_human: number
      accuracy: number | null
      n: number
    }>
  > {
    const data = await json<{ roster?: unknown[] }>(
      await fetch(`${TEAM_BASE}/projects/${projectId}/roster`),
    )
    return (data.roster || []) as Array<{
      at_name: string
      tag: string
      completed: number
      failed: number
      wait_human: number
      accuracy: number | null
      n: number
    }>
  },

  async getEndpointHealth(endpointId: string): Promise<EndpointHealth> {
    return json(await fetch(`${TEAM_BASE}/endpoints/${endpointId}/health`))
  },

  /** Host Bridge pair token (one machine). */
  async createBridgePairToken(ownerUserId: string): Promise<{
    pair_code: string
    expires_at: string
    api_base?: string
    local_command?: string
    remote_command?: string
  }> {
    return json(
      await fetch(`${TEAM_BASE}/bridges/pair-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner_user_id: ownerUserId, ttl_minutes: 60 })
      })
    )
  },

  async sendMessage(input: {
    projectId: string
    content: string
    senderUserId: string
    threadId?: string
    sessionMode?: 'auto' | 'attach' | 'fresh'
    wait?: boolean
    referencedTaskId?: string
    targetAtName?: string
  }): Promise<{
    project_id?: string
    needs_card?: boolean
    dispatches: DispatchEnvelope[]
    events: TeamEvent[]
    replies?: Array<{
      dispatch_id: string
      at_name?: string
      ok: boolean
      content: string
      error?: string
    }>
    receipts?: Array<{
      kind?: string
      text: string
      dispatch_id?: string
      at_name?: string
      session_mode?: string
    }>
  }> {
    const q = input.wait === false ? '' : '?wait=true&wait_timeout=240'
    return json(
      await fetch(`${TEAM_BASE}/projects/${input.projectId}/messages${q}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: input.content,
          sender_user_id: input.senderUserId,
          channel: 'web',
          thread_id: input.threadId || null,
          session_mode: input.sessionMode || 'auto',
          referenced_task_id: input.referencedTaskId || null,
          target_at_name: input.targetAtName || null
        })
      })
    )
  },

  async listTasks(projectId: string): Promise<TeamTask[]> {
    const data = await json<{ tasks: TeamTask[] }>(
      await fetch(`${TEAM_BASE}/projects/${projectId}/tasks`)
    )
    return data.tasks || []
  },

  async upsertTask(
    projectId: string,
    body: Partial<TeamTask> & { status: string; prompt: string }
  ): Promise<TeamTask> {
    return json(
      await fetch(`${TEAM_BASE}/projects/${projectId}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
    )
  },

  async runCoordinator(projectId: string): Promise<{
    dispatched: Array<{ task_id: string; dispatch_ids: string[] }>
    skipped: Array<{ task_id: string; reason: string }>
  }> {
    return json(
      await fetch(`${TEAM_BASE}/projects/${projectId}/coordinator/run`, {
        method: 'POST'
      })
    )
  },

  async askLead(projectId: string, taskId: string): Promise<unknown> {
    return json(
      await fetch(
        `${TEAM_BASE}/projects/${projectId}/tasks/${taskId}/ask-lead`,
        { method: 'POST' }
      )
    )
  },

  async approvePlan(projectId: string, taskId: string): Promise<TeamTask> {
    const data = await json<{ ok: boolean; task: TeamTask }>(
      await fetch(
        `${TEAM_BASE}/projects/${projectId}/tasks/${taskId}/approve-plan`,
        { method: 'POST' },
      ),
    )
    return data.task
  },

  async patchTask(
    projectId: string,
    taskId: string,
    body: {
      prompt?: string
      target_at_name?: string | null
      blocked_by?: string[]
    },
  ): Promise<TeamTask> {
    return json(
      await fetch(`${TEAM_BASE}/projects/${projectId}/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    )
  },

  async listPermissionAsks(projectId: string): Promise<PermissionAsk[]> {
    const data = await json<{ asks?: PermissionAsk[] }>(
      await fetch(`${TEAM_BASE}/projects/${projectId}/permission_asks`),
    )
    return data.asks || []
  },

  async resolvePermissionAsk(
    requestId: string,
    action: 'allow_once' | 'allow_always' | 'deny',
    callId?: string,
    pattern?: string,
    dispatchId?: string,
  ): Promise<unknown> {
    return json(
      await fetch(`${TEAM_BASE}/permission_asks/${encodeURIComponent(requestId)}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          call_id: callId || null,
          pattern: pattern || null,
          dispatch_id: dispatchId || null,
        }),
      }),
    )
  },

  async readLocalFile(
    projectId: string,
    path: string,
  ): Promise<{
    path: string
    name: string
    size: number
    truncated: boolean
    binary: boolean
    text: string | null
  }> {
    const q = new URLSearchParams({ path })
    return json(
      await fetch(
        `${TEAM_BASE}/projects/${projectId}/local-file?${q.toString()}`,
      ),
    )
  },

  async cancelDispatch(dispatchId: string): Promise<unknown> {
    return json(
      await fetch(`${TEAM_BASE}/dispatches/${dispatchId}/cancel`, {
        method: 'POST'
      })
    )
  },

  async getDispatch(dispatchId: string): Promise<{
    dispatch_id: string
    project_id?: string | null
    at_name?: string | null
    endpoint_id?: string | null
    runtime_session_id?: string | null
    session_mode?: string | null
    referenced_task_id?: string | null
    status: 'running' | 'done' | 'error' | 'cancelled' | 'waiting_approval' | string
    ok?: boolean | null
    error_code?: string | null
    prompt?: string | null
    event_count?: number
    final_text?: string | null
    needs_clarification?: boolean
    waiting_approval?: boolean
    pending_permission_asks?: PermissionAsk[]
  }> {
    return json(await fetch(`${TEAM_BASE}/dispatches/${dispatchId}`))
  },

  async listDispatchEvents(dispatchId: string): Promise<TeamEvent[]> {
    const data = await json<{ events: TeamEvent[] }>(
      await fetch(`${TEAM_BASE}/dispatches/${dispatchId}/events`)
    )
    return data.events || []
  },

  openDispatchStream(
    dispatchId: string,
    onEvent: (ev: TeamEvent) => void,
    onError?: (err: Event) => void,
    replay = 500,
  ): EventSource {
    const es = new EventSource(
      `${TEAM_BASE}/dispatches/${dispatchId}/stream?replay=${replay}`,
    )
    const types = [
      'team.session',
      'team.dispatch_start',
      'team.dispatch_done',
      'team.dispatch_error',
      'team.dispatch_cancelled',
      'team.circuit_open',
      'team.stream',
    ]
    const handler = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data) as TeamEvent)
      } catch {
        /* ignore */
      }
    }
    for (const t of types) es.addEventListener(t, handler as EventListener)
    es.onmessage = handler
    if (onError) es.onerror = onError
    return es
  },

  openEventStream(
    projectId: string,
    onEvent: (ev: TeamEvent) => void,
    onError?: (err: Event) => void
  ): EventSource {
    const es = new EventSource(
      `${TEAM_BASE}/projects/${projectId}/events?replay=30`
    )
    const types = [
      'team.session',
      'team.dispatch_start',
      'team.dispatch_done',
      'team.dispatch_error',
      'team.dispatch_cancelled',
      'team.stream',
      'team.circuit_open',
      'endpoint.status',
      'task.update',
    ]
    const handler = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data) as TeamEvent)
      } catch {
        /* ignore */
      }
    }
    for (const t of types) es.addEventListener(t, handler as EventListener)
    es.onmessage = handler
    if (onError) es.onerror = onError
    return es
  }
}
