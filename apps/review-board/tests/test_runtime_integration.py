"""Optional offline integration with the sibling samples-agent package.

Include ``agents`` on PYTHONPATH to exercise the four registered GraphAgents
through actual MCP/REST dispatch, with only model, authorization and stores faked.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fred_samples_agents.document_review")

import app as sample
from fred_samples_agents.document_review import DOCUMENT_REVIEW_AGENTS
from fred_samples_agents.document_review.graph_state import (
    AGENT_IDS,
    STAGE_IDS,
    ReviewInput,
    ReviewState,
)
from fred_sdk import GraphNodeResult
from fred_sdk.contracts.context import AgentInvocationResult
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_app import _client, _mcp_http_client
from test_review import BEARER, create

pytest_plugins = ["test_review"]
AGENTS = {agent.agent_id: agent for agent in DOCUMENT_REVIEW_AGENTS}


async def run_graph(agent_id: str, message: str, context: Any) -> ReviewState:
    agent = AGENTS[agent_id]
    workflow = agent.workflow
    state = ReviewState.model_validate(
        agent.build_initial_state(ReviewInput(message=message), context.binding)
    )
    handlers = agent.node_handlers()
    node = workflow.entry
    for _ in range(30):
        if node is None:
            return state
        result = handlers[node](state, context)
        result = await result if inspect.isawaitable(result) else result
        result = GraphNodeResult.model_validate(result)
        state = ReviewState.model_validate(
            {**state.model_dump(), **result.state_update}
        )
        node = (
            workflow.routes[node][result.route_key]
            if result.route_key is not None
            else workflow.edges.get(node)
        )
    raise AssertionError("The registered workflow did not finish")


class LiveMcpContext:
    def __init__(self, session: ClientSession, role: str = "summary", root: Any = None):
        self.session = session
        self.role = role
        self.root = root or self
        self.binding = SimpleNamespace(
            portable_context=SimpleNamespace(team_id="team-1")
        )
        self.model = object()
        self.model_inputs: list[tuple[str, Any]] = []
        self.invocations: list[str] = []

    def emit_status(self, *args: Any) -> None:
        pass

    async def invoke_runtime_tool(
        self, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self.session.call_tool(operation, arguments=payload)
        assert not response.isError, response.content
        return json.loads(response.content[0].text)

    async def invoke_structured_model(
        self, output_model: Any, messages: list[Any]
    ) -> Any:
        self.root.model_inputs.append((self.role, json.loads(messages[-1].content)))
        return output_model.model_validate(
            {
                "decision": f"{self.role}: saved real test decision",
                "rationale": "The source requires an owner and an approved schedule.",
                "evidence": [
                    {"location": "Project note", "excerpt": "requires an owner"}
                ],
                "findings": [
                    {
                        "title": "No named owner",
                        "severity": "high",
                        "detail": "Assign an accountable owner.",
                    }
                ]
                if self.role == "risk_reviewer"
                else [],
                "outcome": "needs_attention" if self.role == "summary" else None,
            }
        )

    async def invoke_agent(
        self, *, agent_id: str, message: str
    ) -> AgentInvocationResult:
        self.root.invocations.append(agent_id)
        role = next(role for role, value in AGENT_IDS.items() if value == agent_id)
        state = await run_graph(
            agent_id, message, LiveMcpContext(self.session, role, self.root)
        )
        return AgentInvocationResult(agent_id=agent_id, content=state.final_text)


@pytest.mark.asyncio(loop_scope="session")
async def test_four_registered_agents_dispatch_through_real_mcp_and_persist_to_rest(
    corpus: Any, configured_app: Any
) -> None:
    _, storage = configured_app
    async with _client() as client:
        task_id = await create(client)
    async with (
        _mcp_http_client(headers=BEARER) as http_client,
        streamable_http_client("http://sample/mcp", http_client=http_client) as (
            read,
            write,
            _,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        context = LiveMcpContext(session)
        final = await run_graph(AGENT_IDS["coordinator"], f"Review {task_id}", context)
    assert "Review saved" in final.final_text
    assert context.invocations == [AGENT_IDS[stage] for stage in STAGE_IDS[:-1]]
    assert [role for role, _ in context.model_inputs] == list(STAGE_IDS)
    for number, (_, model_input) in enumerate(context.model_inputs):
        assert model_input["document_content"] == corpus.text
        assert [
            prior["stage_id"] for prior in model_input["saved_previous_decisions"]
        ] == list(STAGE_IDS[:number])
    stored = storage.documents[sample.TASKS_INDEX][task_id]["review"]["runs"][-1]
    assert stored["status"] == "completed"
    assert all(stage["status"] == "completed" for stage in stored["stages"])
    async with _client() as client:
        task = (
            await client.get(f"/teams/team-1/tasks/{task_id}", headers=BEARER)
        ).json()
        stats = (await client.get("/teams/team-1/tasks", headers=BEARER)).json()[
            "statistics"
        ]
    assert task["progress_percent"] == 100
    assert task["review"]["runs"][-1] == stored
    assert stats["outcome_counts"] == {"needs_attention": 1}
    assert stats["severity_counts"] == {"high": 1}
