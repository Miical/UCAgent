"""Conversation-scoped MCP capability for launching only user-authorized workflows."""
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
            """Inspect this conversation's cards, MCP health, stages and accepted results."""
            return await self.runtime.inspect()

        @self.mcp.tool()
        async def run_workflow(workflow_id: str = '', rtl_file: str = '', dut: str = 'RTL',
                               card_id: str = '') -> dict:
            """Only start, resume or restart this conversation's workflow MCP. Returns card_id, instructions and tool argument schemas. Execute with the separately named workflow tools, never by calling this tool repeatedly. Inspect cards with list_workflow_runs."""
            self.call = asyncio.current_task()
            try:
                result = await self.runtime.run(workflow_id, rtl_file, dut, card_id)
                if result.get('tools'):
                    changed = json.dumps(session.get('workflow_tool_catalog', {}).get('tools', []), sort_keys=True) != json.dumps(result['tools'], sort_keys=True)
                    if changed:
                        service.catalog_reconnect.add(session['id'])
                    self.active_card = result['card']['id']
                    self.host_tools = {tool.name: tool for raw in result['tools']
                                       if (tool := Tool.model_validate(raw)).name not in {'run_workflow', 'list_workflow_runs', 'verification_targets'}}
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
            """Manage the bound project's verification hierarchy and source markers. Start with guide, then read. Read/inspect/source are read-only. create/import/update/bind/accept require full access and current revision. guide returns the skill and authoritative schema."""
            from .targets import execute
            root = session.get('project_root')
            if not root:
                raise ValueError('当前对话未绑定项目，请在项目中创建新对话')
            if operation not in {'guide', 'read', 'inspect', 'source'} and session.get('permission_mode') != 'full':
                raise ValueError('修改验证目标和源码标识需要完全访问模式')
            return await asyncio.to_thread(execute, root, dict(operation=operation,
                expected_revision=expected_revision,files=files or [],kind=kind,name=name,
                parent=parent,target_id=target_id,binding_id=binding_id,values=values or {}))

        # Retain discovery across backend reconnects; invocation still checks host
        # liveness and current authorization on every call.
        cached = session.get('workflow_tool_catalog', {})
        self.active_card = cached.get('card_id')
        self.host_tools = {tool.name: tool for raw in cached.get('tools', [])
                           if (tool := Tool.model_validate(raw)).name not in {'run_workflow', 'list_workflow_runs', 'verification_targets'}}

    async def refresh_backend(self):
        """Reload after the lifecycle tool response has left the MCP connection."""
        await asyncio.sleep(.5)
        backend = self.service.session_backends.get(self.session['id'])
        if backend:
            try:
                await backend.request('config/mcpServer/reload', None)
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
