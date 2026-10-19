import { Button } from 'antd'
import { useState } from 'react'
import { useLocation } from 'react-router'
import { ChatView } from '~/components/chat/ChatView'
import { ConnectLocalAgentDrawer } from '~/components/team/ConnectLocalAgentDrawer'
import { useT } from '~/lib/i18n'
import { metaDict, pageTitle } from '~/lib/pageTitle'
import type { Route } from './+types/team'

export function meta({ matches }: Route.MetaArgs) {
  const t = metaDict(matches)
  return [{ title: pageTitle(t, t.team.title) }]
}

export default function TeamPage() {
  const { t } = useT()
  const location = useLocation()
  const [connectOpen, setConnectOpen] = useState(false)

  return (
    <>
      <ChatView
        key={location.key}
        project={null}
        sessionId={null}
        headerExtra={
          <Button
            data-testid="team-fleet"
            onClick={() => setConnectOpen(true)}
            className="!rounded-full !border-msa-line-1 !bg-msa-bg-1 !text-msa-text-1 shadow-none hover:!bg-msa-fill-2"
          >
            {t.team.fleetSettings}
          </Button>
        }
      />
      <ConnectLocalAgentDrawer
        open={connectOpen}
        onClose={() => setConnectOpen(false)}
      />
    </>
  )
}
