# Copyright (c) ModelScope Contributors. All rights reserved.
"""Wait for async Team dispatch completion via event fanout."""
from __future__ import annotations

import asyncio
from typing import Any


async def wait_for_dispatches(
    state,
    dispatch_ids: list[str],
    *,
    timeout: float = 90.0,
) -> dict[str, Any]:
    """Block until each dispatch emits done/error/cancelled (or timeout).

    Returns ``{dispatch_id: {ok, texts, events, error?}}``.
    """
    wanted = {d for d in dispatch_ids if d}
    if not wanted:
        return {}

    results: dict[str, dict[str, Any]] = {
        d: {'ok': False, 'texts': [], 'events': []}
        for d in wanted
    }
    pending = set(wanted)
    queue: asyncio.Queue = asyncio.Queue(maxsize=512)

    async def _subscriber(event) -> None:
        did = getattr(event, 'dispatch_id', None)
        if did not in wanted:
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    state.event_subscribers.append(_subscriber)
    # Replay anything already buffered (dry-run can finish before we subscribe).
    for ev in state.recent_events(limit=256):
        did = getattr(ev, 'dispatch_id', None)
        if did in wanted:
            await queue.put(ev)

    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while pending:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            did = getattr(ev, 'dispatch_id', None)
            if did not in results:
                continue
            bucket = results[did]
            bucket['events'].append(ev)
            et = getattr(ev, 'type', '')
            payload = getattr(ev, 'payload', None) or {}
            if et == 'team.stream':
                # Only agent text — skip status/done noise (e.g. *_acp_connected).
                if str(payload.get('type') or '') != 'text':
                    continue
                text = payload.get('content') or ''
                if text:
                    bucket['texts'].append(str(text))
            elif et == 'team.dispatch_done':
                bucket['ok'] = True
                summary = payload.get('summary')
                if summary and not bucket['texts']:
                    bucket['texts'].append(str(summary))
                pending.discard(did)
            elif et in ('team.dispatch_error', 'team.dispatch_cancelled'):
                bucket['ok'] = False
                err = (
                    payload.get('summary')
                    or payload.get('error')
                    or payload.get('code')
                    or et
                )
                bucket['error'] = err
                if err and not bucket['texts']:
                    bucket['texts'].append(str(err))
                pending.discard(did)
    finally:
        if _subscriber in state.event_subscribers:
            state.event_subscribers.remove(_subscriber)

    for did in pending:
        results[did]['error'] = results[did].get('error') or 'timeout'
    return results
