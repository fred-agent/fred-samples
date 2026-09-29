from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator
from typing import Annotated, Any

import app as sample
import httpx
import pytest
from fastapi import Depends, HTTPException
from fred_core import KeycloakUser, get_current_user_without_gcu, oauth2_scheme
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from opensearchpy import exceptions as os_exceptions


class FakeRebac:
    def __init__(self) -> None:
        self.application_checks: list[tuple[str, str, str]] = []
        self.capability_checks: list[tuple[str, str]] = []

    async def check_application_access(
        self,
        user: KeycloakUser,
        *,
        team_id: str,
        app_id: str,
    ) -> None:
        self.application_checks.append((user.uid, team_id, app_id))
        if team_id == "team-denied":
            raise HTTPException(status_code=403, detail="forbidden")

    async def check_team_capability(self, team_id: str, capability_id: str) -> None:
        self.capability_checks.append((team_id, capability_id))


class FakeOpenSearch:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, dict[str, Any]]] = {
            sample.TASKS_INDEX: {},
            sample.STATES_INDEX: {},
        }
        self.searches: list[tuple[str, dict[str, Any]]] = []
        self.versions: dict[tuple[str, str], int] = {}

    async def index(
        self,
        *,
        index: str,
        id: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        params = params or {}
        key = (index, id)
        if "if_seq_no" in params and params["if_seq_no"] != self.versions.get(key, 0):
            raise os_exceptions.ConflictError(409, "version conflict", {})
        self.documents[index][id] = copy.deepcopy(body)
        self.versions[key] = self.versions.get(key, 0) + 1
        return {"result": "updated"}

    async def get(self, *, index: str, id: str) -> dict[str, Any]:
        try:
            source = self.documents[index][id]
        except KeyError as error:
            raise os_exceptions.NotFoundError(404, "not found", {}) from error
        return {
            "_source": copy.deepcopy(source),
            "_seq_no": self.versions.get((index, id), 0),
            "_primary_term": 1,
        }

    async def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.searches.append((index, body))
        sources = list(self.documents[index].values())
        filters = body.get("query", {}).get("bool", {}).get("filter", [])
        for item in filters:
            field, value = next(iter(item["term"].items()))
            sources = [source for source in sources if source.get(field) == value]
        return {"hits": {"hits": [{"_source": source} for source in sources]}}


async def fake_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> KeycloakUser:
    if token != "user-token":
        raise HTTPException(
            status_code=401,
            detail="invalid bearer",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return KeycloakUser(uid="user-1", username="alice", roles=[])


@pytest.fixture
def configured_app() -> Iterator[tuple[FakeRebac, FakeOpenSearch]]:
    rebac = FakeRebac()
    search = FakeOpenSearch()
    sample.app.state.rebac_sdk = rebac
    sample.app.state.opensearch = search
    sample.app.dependency_overrides[get_current_user_without_gcu] = fake_current_user
    yield rebac, search
    sample.app.dependency_overrides.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample.app),
        base_url="http://sample",
    )


def _mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample.app),
        base_url="http://sample",
        headers=headers,
        timeout=timeout,
        auth=auth,
    )


@pytest.mark.asyncio
async def test_mcp_requires_bearer_even_when_service_key_is_sent(
    configured_app: tuple[FakeRebac, FakeOpenSearch],
) -> None:
    _ = configured_app
    async with _client() as client:
        response = await client.post(
            "/mcp",
            headers={
                "accept": "application/json, text/event-stream",
                "x-service-key": "not-an-authentication-mode",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
    assert response.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_bearer_mcp_write_is_visible_to_the_iframe_api(
    configured_app: tuple[FakeRebac, FakeOpenSearch],
) -> None:
    rebac, search = configured_app
    bearer = {"authorization": "Bearer user-token"}

    async with _client() as client:
        created = await client.post(
            "/teams/team-1/tasks",
            headers=bearer,
            json={"title": "Process the corpus"},
        )
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    async with (
        _mcp_http_client(headers=bearer) as mcp_client,
        streamable_http_client(
            "http://sample/mcp",
            http_client=mcp_client,
        ) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        _ = await session.initialize()
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "read_task_progress",
            "report_task_progress",
            "claim_document_review",
            "read_document_review_context",
            "start_document_review_stage",
            "save_document_review_stage",
            "interrupt_document_review",
            # Only the coordinator's own code calls this, but a runtime tool is
            # the sole way it can, so it is part of the surface the model sees.
            "heartbeat_document_review",
        }

        result = await session.call_tool(
            "report_task_progress",
            arguments={
                "team_id": "team-1",
                "task_id": task_id,
                "agent_label": "agent-01",
                "status": "running",
                "progress_percent": 40,
                "detail": "Indexed 40 of 100 documents",
            },
        )
        assert result.isError is False
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert "reported_by" not in json.loads(content.text)

    async with _client() as client:
        detail = await client.get(f"/teams/team-1/tasks/{task_id}", headers=bearer)
    assert detail.status_code == 200
    assert detail.json()["progress"] == [
        {
            "agent_label": "agent-01",
            "status": "running",
            "progress_percent": 40,
            "detail": "Indexed 40 of 100 documents",
            "updated_at": detail.json()["progress"][0]["updated_at"],
        }
    ]
    assert ("team-1", "mcp-review-board") in rebac.capability_checks
    assert len(search.documents[sample.STATES_INDEX]) == 1
    assert (
        next(iter(search.documents[sample.STATES_INDEX].values()))["reported_by"]
        == "user-1"
    )

    progress_query = next(
        body for index, body in search.searches if index == sample.STATES_INDEX
    )
    assert progress_query["query"]["bool"]["filter"] == [
        {"term": {"team_id": "team-1"}},
        {"term": {"task_id": task_id}},
    ]


@pytest.mark.asyncio
async def test_authorization_runs_before_storage(
    configured_app: tuple[FakeRebac, FakeOpenSearch],
) -> None:
    rebac, search = configured_app
    async with _client() as client:
        response = await client.get(
            "/teams/team-denied/tasks",
            headers={"authorization": "Bearer user-token"},
        )

    assert response.status_code == 403
    assert rebac.application_checks == [("user-1", "team-denied", "review-board")]
    assert search.searches == []


@pytest.mark.asyncio
async def test_ten_reporter_labels_keep_ten_independent_states(
    configured_app: tuple[FakeRebac, FakeOpenSearch],
) -> None:
    _, search = configured_app
    bearer = {"authorization": "Bearer user-token"}
    async with _client() as client:
        created = await client.post(
            "/teams/team-1/tasks",
            headers=bearer,
            json={"title": "Ten-agent run"},
        )
        task_id = created.json()["task_id"]

        responses = await asyncio.gather(
            *(
                client.put(
                    f"/teams/team-1/tasks/{task_id}/reporters/agent-{number:02d}",
                    headers=bearer,
                    json={
                        "status": "running",
                        "progress_percent": number * 10,
                        "detail": f"worker {number}",
                    },
                )
                for number in range(1, 11)
            )
        )

    assert [response.status_code for response in responses] == [200] * 10
    assert len(search.documents[sample.STATES_INDEX]) == 10
