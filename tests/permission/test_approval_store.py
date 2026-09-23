import json
import os

import pytest

from ms_agent.permission.approval import (
    ApprovalConflictError,
    ApprovalRequest,
    FileApprovalStore,
    MemoryApprovalStore,
)


@pytest.mark.parametrize('store_factory', [
    lambda tmp_path: MemoryApprovalStore(),
    lambda tmp_path: FileApprovalStore(tmp_path / 'approvals.json'),
])
def test_store_cas_state_machine_and_one_time_token(tmp_path, store_factory):
    store = store_factory(tmp_path)
    request = ApprovalRequest.create(
        tool_name='web---fetch',
        tool_args={'url': 'https://example.com/a'},
        call_id='call-1',
    )
    store.create(request)

    saved = store.get(request.id)
    assert saved is not None
    assert saved.fingerprint == request.fingerprint
    assert saved.version == 1

    approved = store.transition(
        request.id, 'approved', expected_version=1,
        token=request.token, fingerprint=request.fingerprint)
    assert approved.version == 2
    with pytest.raises(ApprovalConflictError):
        store.transition(request.id, 'resume_queued', expected_version=1)
    with pytest.raises(ApprovalConflictError):
        store.transition(
            request.id, 'resume_queued', expected_version=2,
            token=request.token)

    queued = store.transition(
        request.id, 'resume_queued', expected_version=2)
    resumed = store.transition(
        request.id,
        'resumed',
        expected_version=queued.version,
        token=queued.continuation_token,
        fingerprint=request.fingerprint,
    )
    assert resumed.state == 'resumed'
    assert resumed.continuation_used


def test_invalid_transition_is_rejected():
    store = MemoryApprovalStore()
    request = ApprovalRequest.create('tool', {})
    store.create(request)
    with pytest.raises(ValueError):
        store.transition(request.id, 'resumed', expected_version=1)


def test_file_store_strips_plaintext_token(tmp_path):
    path = tmp_path / 'approvals.json'
    first = FileApprovalStore(path)
    request = ApprovalRequest.create('tool', {'unicode': '例子'})
    first.create(request)

    second = FileApprovalStore(path)
    assert second.get(request.id).fingerprint == first.get(request.id).fingerprint
    assert second.list()[0].tool_args == {'unicode': '例子'}
    assert path.stat().st_mode & 0o777 == 0o600
    assert request.token not in path.read_text(encoding='utf-8')


def test_legacy_plaintext_approval_file_is_rewritten_on_open(tmp_path):
    path = tmp_path / 'approvals.json'
    request = ApprovalRequest.create('tool', {'authorization': 'secret'})
    path.write_text(
        json.dumps({
            'version': 1,
            'requests': [{
                'id': request.id,
                'tool_name': request.tool_name,
                'tool_args': request.tool_args,
                'call_id': request.call_id,
                'context': request.context,
                'suggestions': list(request.suggestions),
                'state': 'pending',
                'version': 1,
                'fingerprint': request.fingerprint,
                'token': request.token,
                'token_used': False,
                'created_at': request.created_at,
                'updated_at': request.updated_at,
            }],
        }),
        encoding='utf-8',
    )
    os.chmod(path, 0o644)

    store = FileApprovalStore(path)
    saved = store.get(request.id)
    assert saved is not None
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text(encoding='utf-8')
    assert request.token not in text
    approved = store.transition(
        request.id,
        'approved',
        expected_version=saved.version,
        token=request.token,
        fingerprint=request.fingerprint,
    )
    assert approved.state == 'approved'
