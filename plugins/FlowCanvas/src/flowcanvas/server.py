"""Standalone web host for the FlowCanvas plugin, sharing UCAgent theme assets."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import os
from importlib.util import find_spec
from pathlib import Path
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from .chat import ChatService, routes
from .catalog import workflow_menu
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import __version__


def create_app(state_dir=None) -> FastAPI:
    """Create the workspace and lazily connect the selected chat backend."""
    package = find_spec("ucagent")
    if package is None or not package.origin:
        raise RuntimeError("Install UCAgent before starting FlowCanvas")
    shared = Path(package.origin).parent / "server/static/share"
    web = Path(__file__).parent / "web"
    service = ChatService(state_dir or os.environ.get('FLOWCANVAS_STATE_DIR',
        Path.home() / '.local/share/ucagent-flowcanvas'))

    from .test_runner import TestRuns, routes as test_run_routes
    test_runs = TestRuns()

    @asynccontextmanager
    async def lifespan(app):
        """Own background persistence and backend process lifetime."""
        service.flusher = asyncio.create_task(service.flush())
        yield
        await test_runs.close()
        await service.close()

    app = FastAPI(title="FlowCanvas", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        """Reject cross-site requests to local chat and avoid caching plugin assets."""
        origin = request.headers.get('origin')
        if request.url.path.startswith('/api/chat') and (
            (origin and origin != str(request.base_url).rstrip('/')) or
            request.headers.get('sec-fetch-site') == 'cross-site'
        ):
            return JSONResponse({'detail': 'Cross-origin access denied'}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith(('/assets/', '/api/chat', '/rtl-viewer/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/chat/workflows')
    def workflows():
        """Return explicitly curated menu items without starting any model or agent."""
        try:
            return {'workflows': workflow_menu()}
        except (ValueError, OSError):
            raise HTTPException(503, 'Workflow catalog could not be loaded') from None

    from .projects import routes as project_routes
    app.include_router(project_routes(service.projects))
    from .rtl_viewer import routes as viewer_routes
    app.include_router(viewer_routes(service))
    from .targets import routes as target_routes
    app.include_router(target_routes(service.projects))
    from .testcases import routes as testcase_routes
    app.include_router(testcase_routes(service.projects))
    app.include_router(test_run_routes(service.projects,test_runs))
    from .bugs import routes as bug_routes
    app.include_router(bug_routes(service.projects))
    bundle = web / 'rtl-viewer'
    if (bundle / 'index.html').is_file():
        app.mount('/rtl-viewer', StaticFiles(directory=bundle, html=True), name='rtl-viewer')
    app.include_router(routes(service))
    started = time.time()
    app.mount("/assets", StaticFiles(directory=web), name="flowcanvas-assets")

    @app.get("/")
    def index():
        """Display the product shell and empty workspace."""
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/static/share/{name}")
    def shared_asset(name: str):
        """Serve only the three public theme assets from the installed UCAgent."""
        if name not in {"ucagent-shell.css", "ucagent-shell.js", "ucagent-favicon.svg"}:
            raise HTTPException(404)
        return FileResponse(shared / name)

    @app.get("/api/ui-meta")
    def metadata():
        """Identify the running application to the shared shell."""
        return {"product": "FlowCanvas", "version": __version__, "started_at": started}

    return app


def main():
    """Run the local web interface on an explicit port."""
    parser = argparse.ArgumentParser(description="FlowCanvas web workspace")
    parser.add_argument("--port", type=int, default=8810)
    options = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=options.port, timeout_graceful_shutdown=3)
