from httpx import AsyncClient

from app.core.config import settings


async def test_upload_rejects_unsupported_type(auth_client: AsyncClient):
    ws = (await auth_client.post("/workspaces", json={"name": "Uploads"})).json()

    res = await auth_client.post(
        f"/workspaces/{ws['id']}/sources/upload",
        files={"file": ("notes.docx", b"whatever", "application/octet-stream")},
    )
    assert res.status_code == 415

    await auth_client.delete(f"/workspaces/{ws['id']}")


async def test_upload_md_creates_pending_source(auth_client: AsyncClient):
    ws = (await auth_client.post("/workspaces", json={"name": "Uploads"})).json()

    res = await auth_client.post(
        f"/workspaces/{ws['id']}/sources/upload",
        files={"file": ("notes.md", b"# Hello\n\nSome content.", "text/markdown")},
    )
    assert res.status_code == 201
    source = res.json()
    assert source["type"] == "md"
    assert source["status"] == "pending"

    listed = (await auth_client.get(f"/workspaces/{ws['id']}/sources")).json()
    assert [s["id"] for s in listed] == [source["id"]]

    await auth_client.delete(f"/workspaces/{ws['id']}")


async def test_deleting_a_workspace_removes_its_files(auth_client: AsyncClient):
    from pathlib import Path

    ws = (await auth_client.post("/workspaces", json={"name": "Cleanup"})).json()
    await auth_client.post(
        f"/workspaces/{ws['id']}/sources/upload",
        files={"file": ("notes.md", b"# Hello\n\nSome content.", "text/markdown")},
    )
    listed = (await auth_client.get(f"/workspaces/{ws['id']}/sources")).json()
    stored = Path(settings.storage_dir) / ws["id"]
    assert list(stored.iterdir()), "upload did not reach disk"

    await auth_client.delete(f"/workspaces/{ws['id']}")
    assert not stored.exists(), "workspace files outlived the workspace"
    assert listed
