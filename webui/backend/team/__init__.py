# Copyright (c) ModelScope Contributors. All rights reserved.
"""Agent Team HTTP/WS routers."""
from fastapi import APIRouter

from team.api_artifacts import router as artifacts_router
from team.api_bridges import router as bridges_router
from team.api_dispatch import router as dispatch_router
from team.api_events import router as events_router
from team.api_registry import router as registry_router
from team.channel_dingtalk import router as channels_router
from team.ws_bridge_hub import router as bridge_ws_router

router = APIRouter(prefix='/api/v1/team')
router.include_router(bridges_router)
router.include_router(registry_router)
router.include_router(dispatch_router)
router.include_router(events_router)
router.include_router(artifacts_router)
router.include_router(channels_router)
router.include_router(bridge_ws_router)
