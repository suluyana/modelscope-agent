# Copyright (c) ModelScope Contributors. All rights reserved.
"""Artifact store REST API."""
from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ms_agent.team.models import Artifact, new_id
from team.state import get_team_state

router = APIRouter(prefix='/artifacts', tags=['team-artifacts'])


class UploadRequest(BaseModel):
    project_id: str
    filename: str = 'blob'
    content_base64: str = ''
    created_by_dispatch_id: Optional[str] = None


@router.post('')
def upload_artifact(body: UploadRequest):
    state = get_team_state()
    data = base64.b64decode(body.content_base64) if body.content_base64 else b''
    art = Artifact(
        artifact_id=new_id('art_'),
        project_id=body.project_id,
        sha256='',
        size=0,
        storage_url='',
        filename=body.filename,
        created_by_dispatch_id=body.created_by_dispatch_id,
    )
    saved = state.artifacts.put(art, data=data)
    return saved.to_dict()


@router.get('/{artifact_id}')
def get_artifact(artifact_id: str, include_content_base64: bool = False):
    state = get_team_state()
    art = state.artifacts.get(artifact_id)
    if art is None:
        raise HTTPException(404, detail={'error': 'ARTIFACT_NOT_FOUND'})
    out = art.to_dict()
    if include_content_base64:
        blob = state.artifacts.get_bytes(artifact_id) or b''
        out['content_base64'] = base64.b64encode(blob).decode('ascii')
    return out
