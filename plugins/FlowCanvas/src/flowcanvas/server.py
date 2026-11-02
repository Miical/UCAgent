"""Standalone web host for the FlowCanvas plugin, sharing UCAgent theme assets."""
import argparse
from importlib.util import find_spec
from pathlib import Path
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import __version__


def create_app() -> FastAPI:
    """Create an empty workspace without starting agents or loading projects."""
    package = find_spec("ucagent")
    if package is None or not package.origin:
        raise RuntimeError("Install UCAgent before starting FlowCanvas")
    shared = Path(package.origin).parent / "server/static/share"
    web = Path(__file__).parent / "web"
    app = FastAPI(title="FlowCanvas", docs_url=None, redoc_url=None, openapi_url=None)
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
    uvicorn.run(create_app(), host="127.0.0.1", port=options.port)
