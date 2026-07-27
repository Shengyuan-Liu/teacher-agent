import uuid
from pathlib import Path

from httpx import AsyncClient

from app.core.config import settings
from app.rag.chunking import chunk_document, referenced_images
from app.services.storage import image_dir, save_image


class TestReferencedImages:
    def test_finds_markdown_image_targets(self):
        text = "Text\n\n![img-0.jpeg](img-0.jpeg)\n\nMore\n\n![](img-1.jpeg)"
        assert referenced_images(text) == ["img-0.jpeg", "img-1.jpeg"]

    def test_deduplicates_repeats(self):
        assert referenced_images("![a](x.jpg) ... ![b](x.jpg)") == ["x.jpg"]

    def test_ignores_plain_links(self):
        assert referenced_images("see [the notes](notes.pdf)") == []


def test_parent_chunk_records_its_figures():
    parents = chunk_document("## Norms\n\nThe ball:\n\n![img-3.jpeg](img-3.jpeg)\n\nas shown.")
    assert parents[0].image_ids == ["img-3.jpeg"]


def test_save_image_strips_directory_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    ws, src = uuid.uuid4(), uuid.uuid4()
    path = save_image(ws, src, "../../escaped.jpeg", b"data")
    assert path.parent == image_dir(ws, src)
    assert path.name == "escaped.jpeg"
    assert not (Path(tmp_path) / "escaped.jpeg").exists()


async def test_missing_image_is_404(auth_client: AsyncClient):
    ws = (await auth_client.post("/workspaces", json={"name": "Figures"})).json()
    res = await auth_client.get(f"/workspaces/{ws['id']}/images/{uuid.uuid4()}/img-0.jpeg")
    assert res.status_code == 404
    await auth_client.delete(f"/workspaces/{ws['id']}")


async def test_images_of_another_workspace_are_not_reachable(
    auth_client: AsyncClient, client: AsyncClient
):
    ws = (await auth_client.post("/workspaces", json={"name": "Private figures"})).json()

    other = f"eve-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": other, "password": "password123"})
    tokens = (
        await client.post("/auth/login", json={"email": other, "password": "password123"})
    ).json()

    res = await client.get(
        f"/workspaces/{ws['id']}/images/{uuid.uuid4()}/img-0.jpeg",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert res.status_code == 404
    await auth_client.delete(f"/workspaces/{ws['id']}")
