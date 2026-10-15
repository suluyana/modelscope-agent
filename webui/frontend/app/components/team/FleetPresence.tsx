import { Tag } from 'antd'
import { useEffect, useState } from 'react'
import { useT } from '~/lib/i18n'
import {
  TEAM_OWNER_ID,
  teamApi,
  type TeamEndpoint,
} from '~/lib/teamApi'

const REACHABLE = new Set(['online', 'busy'])

/** Compact fleet lights for the main chat composer. */
export function FleetPresence() {
  const { t } = useT()
  const [endpoints, setEndpoints] = useState<TeamEndpoint[]>([])

  useEffect(() => {
    let alive = true
    const load = () => {
      void teamApi
        .listEndpoints(TEAM_OWNER_ID)
        .then((rows) => {
          if (alive) setEndpoints(rows)
        })
        .catch(() => {
          if (alive) setEndpoints([])
        })
    }
    load()
    const id = window.setInterval(load, 8000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  if (endpoints.length === 0) return null

  return (
    <div
      className="mb-2 flex flex-wrap items-center gap-1 px-1"
      data-testid="chat-fleet-presence"
    >
      {endpoints.map((ep) => {
        const state = ep.status || 'offline'
        const ok = REACHABLE.has(state)
        const reauth = state === 'need_reauth'
        return (
          <Tag
            key={ep.endpoint_id}
            color={ok ? 'success' : reauth ? 'error' : 'default'}
            className="!m-0 !text-[10px]"
            title={`${ep.machine_label || ''} ${state}`.trim()}
          >
            @{ep.at_name}
            {reauth ? ` · ${t.team.fleetReauth}` : ok ? '' : ` · ${state}`}
          </Tag>
        )
      })}
    </div>
  )
}
