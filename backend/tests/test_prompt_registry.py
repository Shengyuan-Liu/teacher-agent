import uuid

import pytest
from httpx import AsyncClient

from app.prompts.registry import (
    PromptRegistryError,
    current_prompt_trace,
    get_builtin_prompt,
    render_prompt,
    reset_prompt_pins,
    set_prompt_pins,
    template_variables,
)
from app.services import usage


def test_template_contract_parses_named_fields_and_escaped_json():
    assert template_variables('Context: {context}\n{{"answer":"..."}}') == ("context",)
    with pytest.raises(PromptRegistryError, match="top-level"):
        template_variables("Hello {user.name}")
    with pytest.raises(PromptRegistryError, match="invalid braces"):
        template_variables("Hello {name")


@pytest.mark.asyncio
async def test_builtin_render_validates_exact_variables_and_records_trace():
    usage.start()
    rendered = await render_prompt(
        "router.classify",
        {"context": "(none)", "question": "Explain Poisson"},
        step="router",
    )

    assert "Explain Poisson" in rendered.text
    assert rendered.prompt.source == "builtin"
    assert current_prompt_trace().for_step("router")["content_hash"] == rendered.prompt.content_hash
    with pytest.raises(PromptRegistryError, match="extra="):
        await render_prompt(
            "router.classify",
            {"context": "(none)", "question": "x", "unexpected": "y"},
        )


@pytest.mark.asyncio
async def test_prompt_api_creates_activates_rolls_back_and_pins_versions(
    auth_client: AsyncClient,
):
    workspace = (await auth_client.post("/workspaces", json={"name": "Prompts"})).json()
    workspace_id = workspace["id"]
    key = "router.classify"
    builtin = get_builtin_prompt(key)

    listed = await auth_client.get(f"/workspaces/{workspace_id}/prompts")
    assert listed.status_code == 200
    assert {item["key"] for item in listed.json()} >= {
        "router.classify",
        "answer.multi_source",
        "planner.draft",
    }

    invalid = await auth_client.post(
        f"/workspaces/{workspace_id}/prompts/{key}/versions",
        json={"template": "No variables"},
    )
    assert invalid.status_code == 422

    v2_template = f"{builtin.template}\n\nWorkspace behavior: be conservative."
    created = await auth_client.post(
        f"/workspaces/{workspace_id}/prompts/{key}/versions",
        json={"template": v2_template, "notes": "Conservative routing experiment"},
    )
    assert created.status_code == 201
    v2 = next(item for item in created.json()["versions"] if item["version"] == 2)
    assert v2["status"] == "draft"

    duplicate = await auth_client.post(
        f"/workspaces/{workspace_id}/prompts/{key}/versions",
        json={"template": v2_template},
    )
    assert duplicate.status_code == 409

    activated = await auth_client.post(
        f"/workspaces/{workspace_id}/prompts/{key}/versions/2/activate"
    )
    assert activated.status_code == 200
    assert activated.json()["active_source"] == "workspace"
    assert activated.json()["active_version"] == 2

    rendered_v2 = await render_prompt(
        key,
        {"context": "(none)", "question": "hello"},
        workspace_id=uuid.UUID(workspace_id),
    )
    assert rendered_v2.prompt.version == 2
    manifest = [rendered_v2.prompt.metadata()]

    v3_template = f"{builtin.template}\n\nWorkspace behavior: ask for clarification early."
    await auth_client.post(
        f"/workspaces/{workspace_id}/prompts/{key}/versions",
        json={"template": v3_template},
    )
    activated_v3 = await auth_client.post(
        f"/workspaces/{workspace_id}/prompts/{key}/versions/3/activate"
    )
    assert activated_v3.json()["active_version"] == 3

    pin = set_prompt_pins(manifest)
    try:
        replayed = await render_prompt(
            key,
            {"context": "(none)", "question": "hello"},
            workspace_id=uuid.UUID(workspace_id),
        )
        assert replayed.prompt.version == 2
        assert replayed.prompt.content_hash == rendered_v2.prompt.content_hash
    finally:
        reset_prompt_pins(pin)

    reset = await auth_client.post(f"/workspaces/{workspace_id}/prompts/{key}/reset-to-builtin")
    assert reset.status_code == 200
    assert reset.json()["active_source"] == "builtin"
    fallback = await render_prompt(
        key,
        {"context": "(none)", "question": "hello"},
        workspace_id=uuid.UUID(workspace_id),
    )
    assert fallback.prompt.source == "builtin"
