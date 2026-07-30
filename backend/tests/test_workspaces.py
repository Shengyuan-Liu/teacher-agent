from httpx import AsyncClient

from app.models import SourceStatus, WorkspaceStatus
from app.services.workspace_status import derive_workspace_status


def test_workspace_status_is_derived_from_all_sources():
    assert derive_workspace_status([]) is WorkspaceStatus.EMPTY
    assert derive_workspace_status([SourceStatus.READY]) is WorkspaceStatus.READY
    assert (
        derive_workspace_status([SourceStatus.PENDING, SourceStatus.READY])
        is WorkspaceStatus.INGESTING
    )
    assert (
        derive_workspace_status([SourceStatus.PARSING, SourceStatus.FAILED])
        is WorkspaceStatus.INGESTING
    )
    assert (
        derive_workspace_status([SourceStatus.READY, SourceStatus.FAILED])
        is WorkspaceStatus.PARTIAL
    )
    assert derive_workspace_status([SourceStatus.FAILED]) is WorkspaceStatus.PARTIAL


async def test_workspace_crud(auth_client: AsyncClient):
    res = await auth_client.post(
        "/workspaces", json={"name": "Operating Systems", "description": "OSTEP notes"}
    )
    assert res.status_code == 201
    ws = res.json()
    assert ws["status"] == "empty"

    res = await auth_client.get("/workspaces")
    assert ws["id"] in [w["id"] for w in res.json()]

    res = await auth_client.patch(f"/workspaces/{ws['id']}", json={"name": "OS"})
    assert res.json()["name"] == "OS"

    assert (await auth_client.delete(f"/workspaces/{ws['id']}")).status_code == 204
    assert (await auth_client.get(f"/workspaces/{ws['id']}")).status_code == 404


async def test_workspace_is_private_to_owner(auth_client: AsyncClient, client: AsyncClient):
    ws = (await auth_client.post("/workspaces", json={"name": "Private"})).json()

    import uuid

    other = f"eve-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": other, "password": "password123"})
    tokens = (
        await client.post("/auth/login", json={"email": other, "password": "password123"})
    ).json()

    res = await client.get(
        f"/workspaces/{ws['id']}",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert res.status_code == 404

    await auth_client.delete(f"/workspaces/{ws['id']}")
