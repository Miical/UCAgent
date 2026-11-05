"""Conversation-scoped MCP tools for enabled workflows and project operations."""
import asyncio
import json
import socket
from mcp.server.mcpserver import MCPServer
from mcp.types import Tool, CallToolResult
import uvicorn
from .workflow_runtime import WorkflowRuntime


class WorkflowMCP(MCPServer):
    """Publish the active host's real schemas alongside stable lifecycle tools."""

    def __init__(self, owner):
        self.owner = owner
        super().__init__('flowcanvas-workflows')

    async def list_tools(self):
        return await super().list_tools() + list(self.owner.host_tools.values())

    def _tool_input_schema(self, name):
        tool = self.owner.host_tools.get(name)
        return tool.input_schema if tool else super()._tool_input_schema(name)

    async def call_tool(self, name, arguments, context=None):
        if name not in self.owner.host_tools:
            return await super().call_tool(name, arguments, context)
        self.owner.call = asyncio.current_task()
        try:
            result = await self.owner.runtime.invoke(self.owner.active_card, name, arguments)
            return CallToolResult.model_validate(result['result'])
        finally:
            self.owner.call = None


class WorkflowBridge:
    """Own a loopback MCP endpoint whose tool closes over one trusted conversation identity."""

    def __init__(self, service, session):
        """Create the tool without accepting a model-supplied conversation or shell command."""
        self.service, self.session = service, session
        self.server = None
        self.task = None
        self.call = None
        self.url = None
        self.host_tools = {}
        self.active_card = None
        self.refresh_task = None
        self.mcp = WorkflowMCP(self)

        self.runtime = WorkflowRuntime(service, session)

        @self.mcp.tool()
        async def list_workflow_runs() -> dict:
            """List enabled workflows and inspect this conversation's cards, health and results."""
            from .catalog import workflow_menu
            result = await self.runtime.inspect()
            result['available_workflows'] = [
                {key: row[key] for key in ('id', 'name', 'description')}
                for row in workflow_menu() if row['can_start']]
            return result

        @self.mcp.tool()
        async def run_workflow(workflow_id: str = '', rtl_file: str = '', dut: str = 'RTL',
                               card_id: str = '') -> dict:
            """Start any enabled workflow without prior attachment or separate authorization, or resume this conversation's card. Returns card_id, instructions and tool argument schemas. Execute with the separately named workflow tools, never by calling this tool repeatedly. Inspect cards with list_workflow_runs."""
            self.call = asyncio.current_task()
            try:
                result = await self.runtime.run(workflow_id, rtl_file, dut, card_id)
                if result.get('tools'):
                    changed = json.dumps(session.get('workflow_tool_catalog', {}).get('tools', []), sort_keys=True) != json.dumps(result['tools'], sort_keys=True)
                    if changed:
                        service.catalog_reconnect.add(session['id'])
                    self.active_card = result['card']['id']
                    self.host_tools = {tool.name: tool for raw in result['tools']
                                       if (tool := Tool.model_validate(raw)).name not in {'run_workflow', 'list_workflow_runs', 'verification_targets', 'test_cases', 'test_case_runs', 'bug_records'}}
                    session['workflow_tool_catalog'] = {'card_id': self.active_card, 'tools': result['tools']}
                    service.changed(session)
                    service.save(session)
                    if self.refresh_task:
                        self.refresh_task.cancel()
                    self.refresh_task = asyncio.create_task(self.refresh_backend())
                    result['next'] = 'If these tools are not callable in this turn, end this turn briefly. FlowCanvas will reconnect and continue the same conversation automatically; do not use shell workarounds. Workflow tools are now published with their original argument schemas. The host refreshes the backend catalog automatically. Call tools such as CurrentTips directly with their original arguments; no card_id wrapper. Do not repeatedly call run_workflow to discover tools.'
                return result
            finally:
                self.call = None

        @self.mcp.tool()
        async def verification_targets(operation: str = 'read', expected_revision: int = 0,
                                       files: list[str] | None = None, kind: str = 'group',
                                       name: str = '', parent: str = '', target_id: str = '',
                                       binding_id: str = '', values: dict | None = None) -> dict:
            """Manage the bound project's verification hierarchy and source markers. Start with guide, then read. AI creation must begin_create before edits, update/bind the same ID and finish_create afterward. For a plan without implementation or bindings, finish_create with values.planned=true; this does not claim implementation or coverage. AI deletion must first read the current revision and immediately begin_delete for the user-selected ID BEFORE source inspection or scope analysis, then inspect scope/references, clean up and delete; descendants require values.cascade=true. resume_operation resumes paused work. Read/inspect/source are read-only. create/import/update/bind/accept require workspace or full access and current revision. guide returns the skill and authoritative schema."""
            from .targets import execute
            root = session.get('project_root')
            if not root:
                raise ValueError('当前对话未绑定项目，请在项目中创建新对话')
            if operation not in {'guide', 'read', 'inspect', 'source'} and session.get('permission_mode') not in {'workspace', 'full'}:
                raise ValueError('请选择工作区访问或完全访问模式')
            try:
                return await asyncio.to_thread(execute, root, dict(operation=operation,
                    expected_revision=expected_revision,files=files or [],kind=kind,name=name,
                    parent=parent,target_id=target_id,binding_id=binding_id,values=values or {}), owner=session['id'])
            except (ValueError, FileNotFoundError) as exc:
                return {'error': str(exc), 'operation': operation,
                        'next_action': 'Read current definitions and correct the request using guide; resume_operation does not repair bindings.'}

        @self.mcp.tool()
        async def test_cases(operation: str = 'read', expected_revision: int = 0,
                             files: list[str] | None = None, name: str = '',
                             target_id: str = '', binding_id: str = '', values: dict | None = None) -> dict:
            """Manage project test cases and source bindings. New authoring MUST create a placeholder before source edits, then update/bind that same ID and finish_create after valid binding. Deletion MUST first read the revision and immediately begin_delete for the user-selected ID BEFORE source inspection or scope analysis, then inspect scope/references, clean up and delete. resume_operation resumes a paused placeholder. Start with guide/read; configure the shared runner command/description before import; discover returns candidates only. Inspect stimulus and run entry; exclude environment-only base tests. import requires values.selected entries with file/start/fingerprint/reason/title/description. Generate a source-grounded description for every imported test. configure_coverage saves the optional project coverage extraction/merge configuration (executed after each run). configure/configure_coverage/import/create/update/delete/bind/accept require workspace or full access and current revision. Discovery never implies a passing test."""
            from .testcases import execute
            root = session.get('project_root')
            if not root:
                raise ValueError('当前对话未绑定项目，请在项目中创建新对话')
            if operation not in {'guide', 'read', 'discover', 'inspect', 'source'} and session.get('permission_mode') not in {'workspace', 'full'}:
                raise ValueError('请选择工作区访问或完全访问模式')
            return await asyncio.to_thread(execute, root, dict(operation=operation,
                expected_revision=expected_revision, files=files or [], name=name,
                target_id=target_id, binding_id=binding_id, values=values or {}), owner=session['id'])

        @self.mcp.tool()
        async def test_case_runs(operation: str = 'list', case_id: str = '', run_id: str = '',
                                 offset: int = 0, wait_seconds: int = 10) -> dict:
            """Run and monitor a case through the shared runner. start(case_id) returns run.id; read(run_id, offset) returns new text and next offset, waiting at most 10 seconds for output. Continue read until terminal status and no unread output, then analyze exit code, assertions, errors and coverage_status. Never restart just to poll. stop(run_id) interrupts this run. list/read are read-only; start/stop are available in workspace and full access modes."""
            root = session.get('project_root')
            if not root:
                raise ValueError('当前对话未绑定项目')
            if operation not in {'list', 'read', 'start', 'stop'}:
                raise ValueError('operation 必须为 list/read/start/stop')
            runner = service.test_runs
            if operation == 'list':
                return {'runs': runner.list(root)}
            if operation == 'start':
                run = await runner.start(root, case_id)
                project = next((row for row in service.projects.state['recent'] if row['path'] == root), None)
                session['ai_test_run'] = {'run': run, 'project': project}
                service.changed(session)
                service.save(session)
                return {'run': run, 'offset': 0}
            if operation == 'stop':
                return await runner.stop(root, run_id)
            if not 0 <= wait_seconds <= 10:
                raise ValueError('wait_seconds 必须在 0 到 10 秒之间')
            deadline = asyncio.get_running_loop().time() + wait_seconds
            while True:
                result = runner.output(root, run_id, offset)
                if result['text'] or result['run']['status'] not in {'starting', 'running'} or asyncio.get_running_loop().time() >= deadline:
                    return result
                await asyncio.sleep(.25)

        @self.mcp.tool()
        async def bug_records(operation: str = 'read', expected_revision: int = 0,
                              files: list[str] | None = None, name: str = '', target_id: str = '',
                              binding_id: str = '', values: dict | None = None) -> dict:
            """Manage project bugs. Start with guide/read. Never treat examples or hypotheses as confirmed defects. Writes require workspace or full access and current revision."""
            from .bugs import execute
            root=session.get('project_root')
            if not root: raise ValueError('当前对话未绑定项目')
            if operation not in {'guide','read','inspect','source'} and session.get('permission_mode') not in {'workspace', 'full'}:
                raise ValueError('请选择工作区访问或完全访问模式')
            return await asyncio.to_thread(execute,root,dict(operation=operation,expected_revision=expected_revision,
                files=files or [],name=name,target_id=target_id,binding_id=binding_id,values=values or {}))

        # Retain discovery across backend reconnects; invocation still checks host
        # liveness, enabled status and conversation ownership on every call.
        cached = session.get('workflow_tool_catalog', {})
        self.active_card = cached.get('card_id')
        self.host_tools = {tool.name: tool for raw in cached.get('tools', [])
                           if (tool := Tool.model_validate(raw)).name not in {'run_workflow', 'list_workflow_runs', 'verification_targets', 'test_cases', 'test_case_runs', 'bug_records'}}

    async def refresh_backend(self):
        """Reload after the lifecycle tool response has left the MCP connection."""
        await asyncio.sleep(.5)
        backend = self.service.session_backends.get(self.session['id'])
        if backend:
            try:
                await backend.reload_tools()
            except Exception as exc:
                self.session['error'] = '工作流工具刷新失败：' + str(exc)
                self.service.changed(self.session)


    async def start(self):
        """Bind an owned ephemeral socket and wait for the MCP transport to be ready."""
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}/mcp'
        app = self.mcp.streamable_http_app(stateless_http=True)
        self.server = uvicorn.Server(uvicorn.Config(app, log_level='error', timeout_graceful_shutdown=2))
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
        for _ in range(100):
            if self.server.started:
                return self.url
            if self.task.done():
                await self.task
                raise RuntimeError('Workflow tool service stopped before readiness')
            await asyncio.sleep(.05)
        await self.close()
        raise RuntimeError('Workflow tool service startup timed out')

    async def close(self):
        """Cancel owned work and close only this conversation's capability endpoint."""
        if self.call and self.call is not asyncio.current_task():
            self.call.cancel()
            await asyncio.gather(self.call, return_exceptions=True)
        if self.refresh_task:
            self.refresh_task.cancel()
            await asyncio.gather(self.refresh_task, return_exceptions=True)
        await self.runtime.close()
        if self.server:
            self.server.should_exit = True
        if self.task:
            await self.task
