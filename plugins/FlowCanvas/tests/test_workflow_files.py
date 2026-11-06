"""Workflow file previews remain confined to registered task workspaces."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
import httpx2
from flowcanvas.projects import routes

class WorkflowFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_workspace_and_read_boundaries(self):
        """Allow task outputs while rejecting sibling state, escaped links and unknown tasks."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            managed=root/"workflow_runs"
            task=managed/"one"
            work=task/"workspace"
            (work/"summary").mkdir(parents=True)
            report=work/"summary/report.md"
            report.write_text("# Report")
            private=task/"request.json"
            private.write_text("private task metadata")
            (work/"escape").symlink_to(private)
            other=managed/"unknown/workspace/file.txt"
            other.parent.mkdir(parents=True)
            other.write_text("not registered")
            sessions={"owner":{"workflow_runs":[{"root":str(task),"workspace":str(work)}]}}
            app=FastAPI()
            app.include_router(routes(SimpleNamespace(state={"recent":[]},current=None),sessions,managed))
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url="http://test") as client:
                response=await client.get("/api/chat/projects/source",params={"path":str(report)})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()["content"],"# Report")
                for path in (private,work/"escape",other):
                    response=await client.get("/api/chat/projects/source",params={"path":str(path)})
                    self.assertEqual(response.status_code,403)
                sessions.clear()
                response=await client.get("/api/chat/projects/source",params={"path":str(report)})
                self.assertEqual(response.status_code,403)
