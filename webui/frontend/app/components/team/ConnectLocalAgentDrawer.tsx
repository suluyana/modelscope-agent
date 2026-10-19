import { Alert, Button, Card, Drawer, Input, Space, Tag, Typography } from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  buildConnectCommand,
  formatSessionWhen,
  loadTeamOwnerId,
  suggestAtName,
  teamApi,
  TEAM_OWNER_ID,
  type EndpointHealth,
  type MachineBridge,
  type RuntimeCandidate,
  type TeamEndpoint,
} from '~/lib/teamApi'
import { useT } from '~/lib/i18n'

const { Text } = Typography

type PendingAction = null | 'refresh' | 'register' | 'pair' | 'copy' | 'enable' | 'caps'

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

/** Host Bridge pairing, local agents, and the cloud demo that used to live
 * in the team page's fleet drawer. */
export function ConnectLocalAgentDrawer({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const { t } = useT()
  const [userId] = useState(() => {
    const stored = loadTeamOwnerId()
    return stored && stored !== 'u1' ? stored : TEAM_OWNER_ID
  })
  const [endpoints, setEndpoints] = useState<TeamEndpoint[]>([])
  const [bridges, setBridges] = useState<MachineBridge[]>([])
  const [candidatesByBridge, setCandidatesByBridge] = useState<
    Record<string, RuntimeCandidate[]>
  >({})
  const [healthMap, setHealthMap] = useState<Record<string, EndpointHealth>>({})
  const [pairCode, setPairCode] = useState('')
  const [localCmd, setLocalCmd] = useState('')
  const [remoteCmd, setRemoteCmd] = useState('')
  const [copied, setCopied] = useState<'local' | 'remote' | ''>('')
  const [error, setError] = useState('')
  const [hint, setHint] = useState('')
  const [pending, setPending] = useState<PendingAction>(null)
  const [newAtName, setNewAtName] = useState('me')
  const [enableName, setEnableName] = useState<Record<string, string>>({})
  const userIdRef = useRef(userId)
  const refreshGen = useRef(0)
  userIdRef.current = userId

  useEffect(() => {
    try {
      window.localStorage.setItem('msa.team.ownerId', userId || TEAM_OWNER_ID)
    } catch {
      /* ignore quota */
    }
  }, [userId])

  const refresh = useCallback(async () => {
    setError('')
    const gen = ++refreshGen.current
    try {
      const uid = userIdRef.current
      const [eps, brs] = await Promise.all([
        teamApi.listEndpoints(uid),
        teamApi.listBridges(uid),
      ])
      if (gen !== refreshGen.current) return
      setEndpoints(eps)
      setBridges(brs)

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
              await teamApi.getEndpointHealth(ep.endpoint_id),
            ] as const
          } catch {
            return [
              ep.endpoint_id,
              {
                endpoint_id: ep.endpoint_id,
                state: ep.status || 'unknown',
              },
            ] as const
          }
        })
      )
      if (gen !== refreshGen.current) return
      setHealthMap(Object.fromEntries(healthEntries))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const id = window.setInterval(() => {
      void refresh()
    }, 3000)
    return () => window.clearInterval(id)
  }, [refresh])

  const onlineBridgeIds = bridges
    .filter((b) => b.status === 'online')
    .map((b) => b.bridge_id)
    .sort()
    .join(',')
  useEffect(() => {
    if (!onlineBridgeIds) return
    const first = bridges.find((b) => b.status === 'online')
    if (!first) return
    setHint(
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

  const onRegisterCloud = () =>
    void run('register', async () => {
      await teamApi.upsertEndpoint({
        at_name: newAtName.replace(/^@/, ''),
        owner_user_id: userIdRef.current,
        adapter_kind: 'cloud',
        status: 'online',
        machine_label: 'web-demo',
      })
      await refresh()
    })

  const onConnectMachine = () =>
    void run('pair', async () => {
      const tok = await teamApi.createBridgePairToken(userIdRef.current)
      setPairCode(tok.pair_code)
      setLocalCmd(tok.local_command || '')
      setRemoteCmd(
        tok.remote_command || buildConnectCommand(tok.pair_code, tok.api_base)
      )
      setCopied('')
    })

  const onCopyCommand = (kind: 'local' | 'remote', command: string) =>
    void run('copy', async () => {
      if (!command) return
      await navigator.clipboard.writeText(command)
      setCopied(kind)
    })

  const onEnableCandidate = (
    bridgeId: string,
    cand: RuntimeCandidate,
    atName: string
  ) =>
    void run('enable', async () => {
      const name = (atName || cand.label || 'agent').replace(/^@/, '')
      await teamApi.enableAgent({
        bridgeId,
        at_name: name,
        runtime: cand.runtime || 'claude_code',
        adapter_kind: cand.adapter_kind || 'acp',
        candidate_id: cand.candidate_id,
        status: 'offline',
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
        status: 'offline',
      })
      setEnableName((prev) => ({ ...prev, [bridgeId]: '' }))
      await refresh()
    })

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
    <Drawer
      title={t.team.fleetSettings}
      open={open}
      onClose={onClose}
      width={420}
      extra={
        <Button
          size="small"
          onClick={() => void run('refresh', refresh)}
          loading={pending === 'refresh'}
        >
          {t.team.refresh}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        {error ? (
          <Alert
            type="error"
            showIcon
            closable
            onClose={() => setError('')}
            message={error}
          />
        ) : null}
        {hint ? (
          <Alert
            type="success"
            showIcon
            closable
            onClose={() => setHint('')}
            message={hint}
          />
        ) : null}

        <div className="grid grid-cols-3 gap-2" data-testid="team-fleet-health">
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

        <Card size="small" title={t.team.identity}>
          <Space direction="vertical" className="w-full" size="middle">
            <Text className="!text-xs">{t.team.ownerThisWebui}</Text>
            <Text code>{userId || TEAM_OWNER_ID}</Text>
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
                  <div className="space-y-3">
                    <div>
                      pair: <Text code>{pairCode}</Text>
                    </div>
                    {localCmd ? (
                      <div className="space-y-1">
                        <Text strong className="!text-xs">
                          {t.team.localDebug}
                        </Text>
                        <div>
                          <Text type="secondary" className="!text-xs">
                            {t.team.localDebugHint}
                          </Text>
                        </div>
                        <pre className="m-0 whitespace-pre-wrap break-all rounded bg-msa-fill-2 p-2 font-mono text-[11px]">
                          {localCmd}
                        </pre>
                        <Button
                          size="small"
                          type="primary"
                          onClick={() => onCopyCommand('local', localCmd)}
                          loading={pending === 'copy'}
                        >
                          {copied === 'local' ? t.team.copied : t.team.copyCommand}
                        </Button>
                      </div>
                    ) : null}
                    {remoteCmd ? (
                      <div className="space-y-1">
                        <Text strong className="!text-xs">
                          {t.team.remoteConnect}
                        </Text>
                        <div>
                          <Text type="secondary" className="!text-xs">
                            {t.team.remoteConnectHint}
                          </Text>
                        </div>
                        <pre className="m-0 whitespace-pre-wrap break-all rounded bg-msa-fill-2 p-2 font-mono text-[11px]">
                          {remoteCmd}
                        </pre>
                        <Button
                          size="small"
                          onClick={() => onCopyCommand('remote', remoteCmd)}
                          loading={pending === 'copy'}
                        >
                          {copied === 'remote'
                            ? t.team.copied
                            : t.team.copyCommand}
                        </Button>
                      </div>
                    ) : null}
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
                    <Text strong>{b.machine_label || '本机'}</Text>
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
                        <div
                          key={ep.endpoint_id}
                          className="rounded border border-msa-line-1 px-2 py-1.5"
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
                            onBlur={(e) =>
                              onSaveCaps(ep.endpoint_id, e.target.value)
                            }
                          />
                        </div>
                      )
                    })}
                  </div>

                  {cands.length > 0 ? (
                    <div className="mt-3 space-y-2">
                      <Text type="secondary" className="!text-xs">
                        {t.team.candidates}
                      </Text>
                      {cands.map((c) => {
                        const meta = (c.meta || {}) as Record<string, unknown>
                        const preview =
                          (typeof meta.preview === 'string' && meta.preview) ||
                          ''
                        const mtime =
                          typeof meta.mtime === 'number' ? meta.mtime : null
                        const when = mtime ? formatSessionWhen(mtime) : ''
                        const cwdPath =
                          (typeof meta.cwd === 'string' && meta.cwd) ||
                          c.cwd ||
                          ''
                        const cwdName = cwdPath
                          ? cwdPath.replace(/\/+$/, '').split('/').pop() || ''
                          : ''
                        const sid = (c.runtime_session_id || '').slice(0, 8)
                        const suggested = suggestAtName(c)
                        const atValue = enableName[c.candidate_id] ?? suggested
                        const isSession = Boolean(c.runtime_session_id)
                        const headline = isSession
                          ? `会话 · 建议 @${suggested}`
                          : c.label || c.runtime
                        const detailParts = [
                          preview ? `“${preview}”` : '',
                          cwdName ? `📁 ${cwdName}` : '',
                          when,
                          sid ? `id ${sid}` : '',
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
                                    [c.candidate_id]: e.target.value,
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
                          [b.bridge_id]: e.target.value,
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
            {endpoints.filter((e) => e.adapter_kind === 'cloud').length > 0 ? (
              <div className="space-y-1">
                {endpoints
                  .filter((e) => e.adapter_kind === 'cloud')
                  .map((ep) => (
                    <div
                      key={ep.endpoint_id}
                      className="rounded border border-msa-line-1 px-2 py-1"
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
                        onBlur={(e) =>
                          onSaveCaps(ep.endpoint_id, e.target.value)
                        }
                      />
                    </div>
                  ))}
              </div>
            ) : null}
          </Space>
        </Card>
      </div>
    </Drawer>
  )
}
