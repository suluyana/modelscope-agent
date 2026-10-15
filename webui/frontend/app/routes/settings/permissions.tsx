import { useEffect, useState } from 'react'
import { Button, Input, Radio, Select, Table, Tag, Typography } from 'antd'
import { api } from '~/lib/api'
import { useT } from '~/lib/i18n'
import { metaDict, pageTitle } from '~/lib/pageTitle'
import type { Project } from '~/lib/types'
import type { Route } from './+types/permissions'

const { Text, Paragraph } = Typography

export function meta({ matches }: Route.MetaArgs) {
  const t = metaDict(matches)
  return [{ title: pageTitle(t, t.settings.navPermissions, t.settings.title) }]
}

type RuleRow = {
  pattern: string
  scope: string
  source: string
  kind: string
}

type AskRow = {
  request_id: string
  tool_name: string
  state: string
  session_id?: string
  call_id?: string
}

export default function PermissionSettings() {
  const { t } = useT()
  const [rules, setRules] = useState<RuleRow[]>([])
  const [asks, setAsks] = useState<AskRow[]>([])
  const [pattern, setPattern] = useState('')
  const [scope, setScope] = useState<'project' | 'global'>('project')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState('')
  const [toolName, setToolName] = useState('file_system---write_file')
  const [argsJson, setArgsJson] = useState('{"path": "src/app.py"}')
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = async (pid: string) => {
    const [r, a] = await Promise.all([
      api.listPermissionRules(pid || undefined),
      api.listPermissionAsks({
        project_id: pid || undefined,
        state: 'pending',
      }),
    ])
    setRules(r.rules || [])
    setAsks(a.asks || [])
  }

  useEffect(() => {
    void api
      .listProjects()
      .then((ps) => {
        setProjects(ps)
        const def = ps.find((p) => p.is_default) ?? ps[0]
        if (def) setProjectId(def.id)
      })
      .catch(() => {
        /* toast */
      })
  }, [])

  useEffect(() => {
    void refresh(projectId).catch(() => {
      /* toast */
    })
  }, [projectId])

  const onAdd = async () => {
    if (!pattern.trim()) return
    if (scope === 'project' && !projectId) {
      setError('project_id required for project-scoped rules')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.addPermissionRule({
        pattern: pattern.trim(),
        scope,
        project_id: projectId || undefined,
      })
      setPattern('')
      await refresh(projectId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const onPreview = async () => {
    let args: Record<string, unknown> = {}
    try {
      args = JSON.parse(argsJson || '{}') as Record<string, unknown>
    } catch {
      setPreview('invalid JSON')
      return
    }
    const res = await api.previewPermission({
      tool_name: toolName,
      tool_args: args,
      project_id: projectId || undefined,
    })
    setPreview(
      res.matches
        ? res.rules.map((x) => `${x.kind}: ${x.pattern}`).join('\n')
        : '(no match)',
    )
  }

  return (
    <div className="space-y-8" data-testid="settings-permissions">
      <section>
        <div className="mb-1 text-base font-semibold text-msa-text-1">
          {t.settings.permRules}
        </div>
        <Paragraph type="secondary" className="!text-xs">
          {t.settings.permRulesHint}
        </Paragraph>
        <div className="mb-3 flex flex-wrap gap-2">
          <Select
            className="min-w-[160px]"
            data-testid="settings-perm-project"
            value={projectId || undefined}
            onChange={setProjectId}
            options={projects.map((p) => ({
              value: p.id,
              label: p.is_default ? `${p.name} (default)` : p.name,
            }))}
          />
          <Input
            className="min-w-[240px] flex-1"
            data-testid="settings-perm-pattern"
            placeholder="file_system---write_file"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
          />
          <Select
            value={scope}
            onChange={setScope}
            options={[
              { value: 'project', label: t.settings.permScopeProject },
              { value: 'global', label: t.settings.permScopeGlobal },
            ]}
          />
          <Button
            type="primary"
            loading={busy}
            data-testid="settings-perm-add"
            onClick={() => void onAdd()}
          >
            {t.settings.permAdd}
          </Button>
        </div>
        {error ? (
          <Text type="danger" data-testid="settings-perm-error">
            {error}
          </Text>
        ) : null}
        <Table
          size="small"
          pagination={false}
          rowKey="pattern"
          dataSource={rules}
          columns={[
            { title: t.settings.permPattern, dataIndex: 'pattern' },
            {
              title: 'kind',
              dataIndex: 'kind',
              width: 90,
              render: (k: string) => <Tag>{k}</Tag>,
            },
            { title: t.settings.permScope, dataIndex: 'scope', width: 100 },
            {
              title: '',
              width: 80,
              render: (_: unknown, row: RuleRow) => (
                <Button
                  size="small"
                  danger
                  onClick={() =>
                    void api
                      .deletePermissionRule({
                        pattern: row.pattern,
                        project_id: projectId || undefined,
                      })
                      .then(() => refresh(projectId))
                  }
                >
                  {t.settings.permDeny}
                </Button>
              ),
            },
          ]}
        />
      </section>

      <section>
        <div className="mb-1 text-base font-semibold text-msa-text-1">
          {t.settings.permPreview}
        </div>
        <Paragraph type="secondary" className="!text-xs">
          {t.settings.permPreviewHint}
        </Paragraph>
        <div className="flex flex-col gap-2">
          <Input
            value={toolName}
            onChange={(e) => setToolName(e.target.value)}
          />
          <Input.TextArea
            rows={3}
            value={argsJson}
            onChange={(e) => setArgsJson(e.target.value)}
          />
          <Button
            data-testid="settings-perm-preview"
            onClick={() => void onPreview()}
          >
            {t.settings.permPreviewRun}
          </Button>
          {preview ? (
            <pre className="rounded bg-msa-fill-2 p-2 text-xs">{preview}</pre>
          ) : null}
        </div>
      </section>

      <section>
        <div className="mb-1 text-base font-semibold text-msa-text-1">
          {t.settings.permInbox}
        </div>
        {asks.length === 0 ? (
          <Text type="secondary">{t.settings.permInboxEmpty}</Text>
        ) : (
          <div className="space-y-2">
            {asks.map((ask) => (
              <div
                key={ask.request_id}
                className="flex flex-wrap items-center gap-2 rounded border border-msa-line-1 p-2"
              >
                <Text code>{ask.tool_name}</Text>
                <Tag>{ask.state}</Tag>
                <Button
                  size="small"
                  type="primary"
                  onClick={() =>
                    void api
                      .resolvePermissionAsk(ask.request_id, {
                        action: 'allow_once',
                        session_id: ask.session_id,
                      })
                      .then(() => refresh(projectId))
                  }
                >
                  {t.settings.permAllowOnce}
                </Button>
                <Button
                  size="small"
                  danger
                  onClick={() =>
                    void api
                      .resolvePermissionAsk(ask.request_id, {
                        action: 'deny',
                        session_id: ask.session_id,
                      })
                      .then(() => refresh(projectId))
                  }
                >
                  {t.settings.permDeny}
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="mb-1 text-base font-semibold text-msa-text-1">
          {t.settings.permAuto}
        </div>
        <Paragraph type="secondary" className="!text-xs">
          {t.settings.permAutoHint}
        </Paragraph>
        <div className="mb-2">
          <Text>{t.settings.permApprover}: </Text>
          <Tag color="blue">{t.settings.permApproverBuiltin}</Tag>
        </div>
        <Radio.Group value="delegate">
          <Radio value="delegate">{t.home.permDelegate}</Radio>
        </Radio.Group>
        <Paragraph type="secondary" className="!mt-2 !text-xs">
          {t.settings.permLinkComposer}
        </Paragraph>
      </section>
    </div>
  )
}
