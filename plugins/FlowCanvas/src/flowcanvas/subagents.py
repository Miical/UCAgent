"""Read native child threads without resuming or executing agents."""
import asyncio
from .backends.codex import normalize


async def thread_items(backend, thread):
    """Read all pages in chronological order, including in-progress items."""
    items, cursor = [], None
    while True:
        page = await backend.request('thread/items/list', {
            'threadId': thread, 'limit': 200, 'sortDirection': 'asc', 'cursor': cursor})
        items.extend(page['data'])
        cursor = page.get('nextCursor')
        if not cursor:
            return items


def children(entries):
    """Discover only children recorded by native spawn or child-activity items."""
    result = {}
    for entry in entries:
        item = entry['item']
        if item['type'] == 'collabAgentToolCall':
            if item.get('tool') == 'spawnAgent':
                for identity in item.get('receiverThreadIds', []):
                    result.setdefault(identity, {'id': identity, 'name': '子 Agent', 'task': item.get('prompt') or ''})
            for identity, state in item.get('agentsStates', {}).items():
                if identity in result:
                    result[identity]['status'] = state.get('status', 'unknown')
        elif item['type'] == 'subAgentActivity':
            identity = item['agentThreadId']
            row = result.setdefault(identity, {'id': identity, 'task': ''})
            row.update(name=item.get('agentPath') or '子 Agent', model=item.get('model'))
            row['status'] = {'started': 'running', 'completed': 'completed', 'interrupted': 'interrupted'}.get(item['kind'], row.get('status', 'unknown'))
    return list(result.values())


def messages(entries):
    """Project public user messages, summaries, replies and tool records for display."""
    result = []
    for entry in entries:
        item = entry['item']
        if item['type'] == 'userMessage':
            result.append({'id': item['id'], 'kind': 'user', 'text': '\n'.join(
                block.get('text', '') for block in item.get('content', []) if block.get('type') == 'text')})
        else:
            event = normalize('item/completed', {'item': item, 'turnId': entry['turnId']})
            if event:
                result.append(event['item'])
    return result


async def read_agents(service, session, selected=None, include_messages=True):
    """Resolve child membership from this parent's native history before reading a child."""
    if not session.get('thread'):
        return {'agents': [], 'selected': None, 'items': []}
    backend = service.session_backends.get(session['id']) or service.backend
    await backend.connect()
    agents = children(await thread_items(backend, session['thread']))
    limit = asyncio.Semaphore(4)
    histories = {}
    seen = {session['thread'], *(row['id'] for row in agents)}
    frontier = list(agents)

    async def descendants(row):
        """Follow only native spawn edges; nested agents belong to the same root task."""
        async with limit:
            try:
                entries = await thread_items(backend, row['id'])
                histories[row['id']] = entries
                return children(entries)
            except RuntimeError as exc:
                row['error'] = str(exc)
                return []
    while frontier:
        batches = await asyncio.gather(*(descendants(row) for row in frontier))
        frontier = []
        for batch in batches:
            for row in batch:
                if row['id'] not in seen:
                    seen.add(row['id'])
                    agents.append(row)
                    frontier.append(row)

    async def metadata(row):
        """Keep unavailable children visible without failing the complete list."""
        async with limit:
            try:
                thread = (await backend.request('thread/read', {'threadId': row['id'], 'includeTurns': False}))['thread']
                row['name'] = thread.get('agentNickname') or thread.get('name') or row.get('name', '子 Agent')
                row['role'] = thread.get('agentRole') or ''
                native = (thread.get('status') or {}).get('type')
                if native == 'active':
                    row['status'] = 'running'
                elif native == 'systemError':
                    row['status'] = 'error'
                else:
                    # Loading state is not execution state: unloaded histories still
                    # retain the terminal outcome of their most recent turn.
                    page = await backend.request('thread/turns/list', {
                        'threadId': row['id'], 'limit': 1,
                        'sortDirection': 'desc', 'itemsView': 'summary'})
                    turns = page.get('data', [])
                    status = turns[0].get('status') if turns else None
                    terminal = {'completed': 'completed', 'interrupted': 'interrupted', 'failed': 'error'}
                    if row.get('status') == 'shutdown':
                        pass
                    elif status in terminal:
                        row['status'] = terminal[status]
                    elif native == 'notLoaded':
                        row['status'] = 'notLoaded'
                    else:
                        row['status'] = 'idle' if native == 'idle' else 'unknown'
            except RuntimeError as exc:
                row['error'] = str(exc)
    await asyncio.gather(*(metadata(row) for row in agents))
    identity = selected if any(row['id'] == selected for row in agents) else (agents[0]['id'] if agents else None)
    result = {'agents': agents, 'selected': identity, 'items': []}
    if identity and include_messages:
        try:
            result['items'] = messages(histories[identity]) if identity in histories else messages(await thread_items(backend, identity))
        except RuntimeError as exc:
            result['error'] = str(exc)
    return result


def execution_active(session):
    """Treat live children as part of the authorized task, without reopening a stopped task."""
    if session.get('delegation_stopped'):
        return False
    return session.get('status') in {'starting', 'running'} or bool(
        session.get('children_running') or session.get('children_pending'))


async def refresh_activity(service, session):
    """Reconcile child liveness independently of whether the sidebar is open."""
    epoch = session.get('child_activity_epoch', 0)
    try:
        result = await read_agents(service, session, include_messages=False)
        if session.get('child_activity_epoch', 0) != epoch:
            return
        session['children_running'] = sum(row.get('status') in {'running', 'pendingInit'} for row in result['agents'])
        if any(row.get('error') for row in result['agents']):
            # Unavailable status must not revoke a potentially live child's grant.
            session['children_pending'] = True
        else:
            session['children_pending'] = False
        session.pop('children_error', None)
    except (RuntimeError, KeyError, ValueError) as exc:
        if session.get('child_activity_epoch', 0) != epoch:
            return
        session['children_error'] = str(exc)
        backend = service.session_backends.get(session['id'])
        if not backend or not backend.process or backend.process.returncode is not None:
            session.update(children_running=0, children_pending=False)
    finally:
        if session.get('child_activity_epoch', 0) == epoch:
            service.changed(session)
            service.save(session)


async def interrupt_children(service, session):
    """Interrupt the actual active child turns when the user stops the owning task."""
    backend = service.session_backends.get(session['id'])
    if not backend or not session.get('thread'):
        return
    agents = (await read_agents(service, session, include_messages=False))['agents']
    for row in agents:
        page = await backend.request('thread/turns/list', {
            'threadId': row['id'], 'limit': 1, 'sortDirection': 'desc', 'itemsView': 'summary'})
        for turn in page.get('data', []):
            if turn.get('status') == 'inProgress':
                await backend.interrupt(row['id'], turn['id'])
