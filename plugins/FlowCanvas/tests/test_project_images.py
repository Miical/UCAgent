"""Image previews share project boundaries and sandbox SVG content."""
from types import SimpleNamespace
import pytest
import httpx2
from fastapi import FastAPI
from flowcanvas.projects import routes


@pytest.mark.asyncio
async def test_project_svg_preview_is_bounded_and_sandboxed(tmp_path):
    """Serve project SVGs but reject escaped paths and invalid XML."""
    root=tmp_path/"project"
    root.mkdir()
    svg=root/"timing.svg"
    content='<svg xmlns="http://www.w3.org/2000/svg"><style>text{fill:red}</style><text>clock</text></svg>'
    svg.write_text(content)
    outside=tmp_path/"outside.svg"
    outside.write_text(content)
    (root/"escape.svg").symlink_to(outside)
    (root/"bad.svg").write_text("<html>not svg</html>")
    app=FastAPI()
    app.include_router(routes(SimpleNamespace(state={"recent":[{"path":str(root)}]},current={"path":str(root)})))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get("/api/chat/projects/source",params={"path":str(svg),"media":"true"})
        assert response.status_code==200
        assert response.text==content
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert "sandbox" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"]=="nosniff"
        response=await client.get("/api/chat/projects/source",params={"path":str(root/"escape.svg"),"media":"true"})
        assert response.status_code==403
        response=await client.get("/api/chat/projects/source",params={"path":str(root/"bad.svg"),"media":"true"})
        assert response.status_code==415
        response=await client.get("/api/chat/projects/source",params={"path":str(svg)})
        assert response.json()["content"]==content
