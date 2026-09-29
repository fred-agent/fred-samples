"""Offline contract tests through the public GraphAgent workflow handlers."""

from __future__ import annotations

import asyncio
import inspect
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fred_runtime.app import AgentPodConfig, create_agent_app
from fred_sdk import GraphAgent, GraphNodeContext, GraphNodeResult
from fred_sdk.contracts.context import AgentInvocationResult, BoundRuntimeContext
from fred_sdk.contracts.models import MCPServerConfiguration
from fred_sdk.graph.authoring.api import GraphStepHandler
from pydantic import BaseModel, ValidationError

from fred_samples_agents.document_review import DOCUMENT_REVIEW_AGENTS
from fred_samples_agents.document_review.graph_agent import MCP_SERVER_ID
from fred_samples_agents.document_review.graph_state import (
    AGENT_IDS,
    STAGE_IDS,
    Evidence,
    ReviewContext,
    ReviewDecision,
    ReviewInput,
    ReviewState,
    StageReceipt,
)
from fred_samples_agents.registry import REGISTRY

TASK_ID = "task-" + "a" * 32
AGENTS = {agent.agent_id: agent for agent in DOCUMENT_REVIEW_AGENTS}


def decision(stage: str) -> dict[str, Any]:
    return ReviewDecision(
        decision=f"{stage}: evidence-based decision",
        rationale="The document states the delivery date.",
        evidence=[Evidence(location="Delivery", excerpt="Delivery is due Friday.")],
        findings=[],
        actions=[],
        limitations=[],
        outcome="needs_attention" if stage == "summary" else None,
    ).model_dump(mode="json")


class FakeBackend:
    def __init__(self) -> None:
        self.task: dict[str, Any] = {
            "task_id": TASK_ID,
            "title": "Review the delivery plan",
            "review": {"runs": []},
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.saved: list[str] = []
        self.unavailable_operation: str | None = None
        self.source_revoked = False
        self.cancel_after: str | None = None
        # Expire the caller's bearer once this stage has been saved. The
        # runtime cannot refresh a forwarded user token, so every later call
        # fails with 401 exactly as it does against a real deployment.
        self.expire_token_after: str | None = None
        self._token_expired = False
        self.wrong_claim = False

    @property
    def run(self) -> dict[str, Any]:
        return self.task["review"]["runs"][-1]

    def call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, deepcopy(payload)))
        assert payload["team_id"] == "team-a"
        assert payload["task_id"] == TASK_ID
        assert not {"access_token", "refresh_token", "authorization"} & payload.keys()
        if operation == self.unavailable_operation:
            raise RuntimeError("sensitive-source-and-token-must-not-be-displayed")
        if self._token_expired:
            raise RuntimeError(
                "401 Unauthorized (expired token): Cannot refresh user access "
                "token: refresh_token missing from runtime context."
            )
        if operation == "read_task_progress":
            return deepcopy(self.task)
        if operation == "claim_document_review":
            mode = payload["mode"]
            if mode == "resume":
                assert payload["run_id"] == self.run["run_id"]
                if self.run["status"] not in {"failed", "interrupted"}:
                    raise ValueError("Run is not resumable")
                self.run.update(claim_id=payload["claim_id"], status="running")
                for stage in self.run["stages"]:
                    if stage["status"] != "completed":
                        stage["status"] = "queued"
            else:
                if self.task["review"]["runs"] and mode != "restart":
                    raise ValueError("Explicit restart is required")
                self.task["review"]["runs"].append(
                    {
                        "run_id": f"run-{len(self.task['review']['runs']) + 1}",
                        "claim_id": payload["claim_id"],
                        "source_hash": "source-hash",
                        "status": "running",
                        "stages": [
                            {"stage_id": stage, "status": "queued", "result": None}
                            for stage in STAGE_IDS
                        ],
                    }
                )
            return deepcopy(self.task)
        assert payload["run_id"] == self.run["run_id"]
        assert payload["claim_id"] == self.run["claim_id"]
        if operation == "interrupt_document_review":
            if self.run["status"] == "running":
                self.run["status"] = "failed"
            return deepcopy(self.run)
        if self.source_revoked:
            raise PermissionError("Source access was revoked")
        if self.run["status"] not in {"running", "completed"}:
            raise ValueError("Run is cancelled or stopped")
        if operation == "read_document_review_context":
            run = deepcopy(self.run)
            if self.wrong_claim:
                run["claim_id"] = "someone-elses-claim"
            return {
                "task_id": TASK_ID,
                "title": self.task["title"],
                "document": {
                    "tag_id": "folder-a",
                    "document_uid": "document-a",
                    "document_name": "delivery.md",
                },
                "source_hash": "source-hash",
                "content": "# Delivery\nDelivery is due Friday.",
                "run": run,
            }
        stage_id = payload["stage_id"]
        stage = next(s for s in self.run["stages"] if s["stage_id"] == stage_id)
        if operation == "start_document_review_stage":
            assert all(
                s["status"] == "completed"
                for s in self.run["stages"][: STAGE_IDS.index(stage_id)]
            )
            stage["status"] = "running"
            return deepcopy(self.run)
        if operation == "save_document_review_stage":
            assert stage["status"] == "running"
            stage.update(status="completed", result=deepcopy(payload["result"]))
            self.saved.append(stage_id)
            if stage_id == "summary":
                self.run["status"] = "completed"
            response = deepcopy(self.run)
            if stage_id == self.cancel_after:
                self.run["status"] = "cancelled"
            if stage_id == self.expire_token_after:
                self._token_expired = True
            return response
        raise AssertionError(operation)


class FakeContext:
    def __init__(
        self,
        backend: FakeBackend,
        *,
        role: str = "summary",
        root: FakeContext | None = None,
    ) -> None:
        self.backend = backend
        self.role = role
        self.root = root or self
        self.binding = SimpleNamespace(
            portable_context=SimpleNamespace(team_id="team-a")
        )
        self.model: object | None = object()
        self.fail_model_role: str | None = None
        self.invalid_model_role: str | None = None
        self.forge_child_receipt = False
        self.model_payloads: list[tuple[str, dict[str, Any]]] = []
        self.invocations: list[str] = []

    def emit_status(self, status: str, detail: str | None = None) -> None:
        pass

    async def invoke_runtime_tool(
        self, operation: str, payload: dict[str, Any]
    ) -> object:
        return self.backend.call(operation, payload)

    async def invoke_structured_model(
        self, output_model: type[BaseModel], messages: list[Any]
    ) -> BaseModel:
        assert output_model is ReviewDecision
        self.root.model_payloads.append((self.role, json.loads(messages[-1].content)))
        if self.role == self.root.fail_model_role:
            raise RuntimeError("sensitive-source-and-token-must-not-be-displayed")
        raw = decision(self.role)
        if self.role == self.root.invalid_model_role:
            raw["decision"] = ""
        return output_model.model_validate(raw)

    async def invoke_agent(
        self, *, agent_id: str, message: str
    ) -> AgentInvocationResult:
        # This signature intentionally refuses output_schema: a failed JSON
        # response must never trigger a whole side-effecting child retry.
        self.invocations.append(agent_id)
        if self.forge_child_receipt:
            work = json.loads(message)
            return AgentInvocationResult(
                agent_id=agent_id,
                content=StageReceipt(
                    task_id=work["task_id"],
                    run_id=work["run_id"],
                    stage_id="analyst",
                    status="completed",
                ).model_dump_json(),
            )
        role = next(role for role, ref in AGENT_IDS.items() if ref == agent_id)
        child_context = FakeContext(self.backend, role=role, root=self)
        child_context.model = self.model
        state = await run_graph(AGENTS[agent_id], message, child_context)
        return AgentInvocationResult(agent_id=agent_id, content=state.final_text)


async def run_graph(
    agent: GraphAgent, message: str, context: FakeContext
) -> ReviewState:
    """Execute declared public nodes/routes, without importing runtime internals."""
    workflow = agent.workflow
    assert workflow is not None
    state = ReviewState.model_validate(
        agent.build_initial_state(
            ReviewInput(message=message), cast(BoundRuntimeContext, context.binding)
        )
    )
    handlers = agent.node_handlers()
    node: str | None = workflow.entry
    for _ in range(30):
        if node is None:
            return state
        handler = cast(GraphStepHandler, handlers[node])
        pending = handler(state, cast(GraphNodeContext, context))
        result = await pending if inspect.isawaitable(pending) else pending
        validated = GraphNodeResult.model_validate(result)
        state = ReviewState.model_validate(
            {**state.model_dump(), **validated.state_update}
        )
        if validated.route_key is not None:
            node = workflow.routes[node][validated.route_key]
        else:
            node = workflow.edges.get(node)
    raise AssertionError("Graph did not terminate")


def run_coordinator(context: FakeContext, command: str | None = None) -> ReviewState:
    return asyncio.run(
        run_graph(
            AGENTS[AGENT_IDS["coordinator"]], command or f"Review {TASK_ID}", context
        )
    )


def test_registered_real_graph_agents_and_each_declares_mcp() -> None:
    assert set(AGENTS) == set(AGENT_IDS.values())
    for agent in DOCUMENT_REVIEW_AGENTS:
        assert REGISTRY[agent.agent_id] is agent
        assert isinstance(agent, GraphAgent)
        assert [ref.id for ref in agent.default_mcp_servers] == [MCP_SERVER_ID]
        assert agent.build_graph().entry_node


def test_review_templates_advertise_selectable_mcp_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "fred_runtime.capabilities.registry._installed_entry_points", lambda **_: ()
    )
    server = MCPServerConfiguration.model_validate(
        {"id": MCP_SERVER_ID, "name": "Review tools"}
    )
    config = AgentPodConfig.model_validate(
        {
            "app": {"runtime_id": "sample-test", "base_url": "/test"},
            "security": {
                "m2m": {
                    "enabled": False,
                    "realm_url": "http://localhost/realms/test",
                    "client_id": "test",
                },
                "user": {
                    "enabled": False,
                    "realm_url": "http://localhost/realms/test",
                    "client_id": "test",
                },
            },
        }
    )

    class Catalog:
        def __init__(self) -> None:
            self.servers = [server]

        def get_server(self, id: str) -> MCPServerConfiguration | None:
            return server if id == server.id else None

    config.set_mcp_configuration(Catalog())
    monkeypatch.setattr(
        "fred_runtime.app.agent_app.get_runtime_context",
        lambda: SimpleNamespace(
            config=SimpleNamespace(mcp_configuration=config.get_mcp_configuration())
        ),
    )
    app = create_agent_app(registry=AGENTS, config=config)

    async def fetch_templates() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://sample"
        ) as client:
            response = await client.get("/test/agents/templates")
            response.raise_for_status()
            return response.json()

    templates = asyncio.run(fetch_templates())
    assert {item["template_agent_id"] for item in templates} == set(AGENTS)
    for template in templates:
        assert template["supports_capabilities"] is True
        assert MCP_SERVER_ID in template["default_capability_ids"]
        assert MCP_SERVER_ID in {
            entry["id"] for entry in template["available_capabilities"]
        }


def test_coordinator_runs_real_specialists_in_order_and_persists_summary() -> None:
    backend = FakeBackend()
    context = FakeContext(backend)
    state = run_coordinator(context)
    assert context.invocations == [AGENT_IDS[stage] for stage in STAGE_IDS[:-1]]
    assert backend.saved == list(STAGE_IDS)
    assert backend.run["status"] == "completed"
    assert "needs attention" in state.final_text
    assert "Review saved" in state.final_text
    assert state.review_context is None
    for index, (role, payload) in enumerate(context.model_payloads):
        assert role == STAGE_IDS[index]
        assert payload["document_content"] == "# Delivery\nDelivery is due Friday."
        assert [s["stage_id"] for s in payload["saved_previous_decisions"]] == list(
            STAGE_IDS[:index]
        )
    read_operations = [
        op for op, _ in backend.calls if op == "read_document_review_context"
    ]
    assert len(read_operations) >= 7


@pytest.mark.parametrize(
    "command",
    [
        "Review delivery.md",
        f"Review {TASK_ID} and task-{'b' * 32}",
        f"Review {TASK_ID}bad",
    ],
)
def test_missing_ambiguous_or_invalid_task_never_calls_backend(command: str) -> None:
    backend = FakeBackend()
    state = run_coordinator(FakeContext(backend), command)
    assert "Use exactly one task reference" in state.final_text
    assert backend.calls == []


@pytest.mark.parametrize("failure", ["model", "invalid-model", "missing-model", "tool"])
def test_failures_never_produce_success_or_start_later_specialists(
    failure: str,
) -> None:
    backend = FakeBackend()
    context = FakeContext(backend)
    if failure == "model":
        context.fail_model_role = "analyst"
    elif failure == "invalid-model":
        context.invalid_model_role = "analyst"
    elif failure == "missing-model":
        context.model = None
    else:
        backend.unavailable_operation = "start_document_review_stage"
    state = run_coordinator(context)
    assert backend.saved == []
    assert backend.run["status"] == "failed"
    assert len(context.invocations) == 1
    assert "stopped before completion" in state.final_text
    assert "sensitive-source" not in state.final_text
    for operation, payload in backend.calls:
        if operation == "interrupt_document_review":
            assert "sensitive-source" not in payload["error"]


def test_resume_rotates_claim_and_reuses_persisted_complete_stages() -> None:
    backend = FakeBackend()
    first_context = FakeContext(backend)
    first_context.fail_model_role = "risk_reviewer"
    run_coordinator(first_context)
    first_claim = backend.run["claim_id"]
    assert backend.saved == ["analyst"]
    context = FakeContext(backend)
    state = run_coordinator(context, f"Resume {TASK_ID}")
    assert backend.run["claim_id"] != first_claim
    assert context.invocations == [
        AGENT_IDS["risk_reviewer"],
        AGENT_IDS["action_planner"],
    ]
    assert backend.saved == list(STAGE_IDS)
    assert "Review saved" in state.final_text


def test_restart_creates_distinct_run_but_plain_start_does_not_repeat_work() -> None:
    backend = FakeBackend()
    run_coordinator(FakeContext(backend))
    state = run_coordinator(FakeContext(backend))
    assert "stopped before completion" in state.final_text
    assert backend.saved == list(STAGE_IDS)
    run_coordinator(FakeContext(backend), f"Restart {TASK_ID}")
    assert len(backend.task["review"]["runs"]) == 2
    assert backend.saved == list(STAGE_IDS) * 2


def test_cancel_after_first_stage_prevents_remaining_work() -> None:
    backend = FakeBackend()
    backend.cancel_after = "analyst"
    context = FakeContext(backend)
    state = run_coordinator(context)
    assert backend.run["status"] == "cancelled"
    assert backend.saved == ["analyst"]
    assert len(context.invocations) == 1
    assert "stopped before completion" in state.final_text


def test_child_acknowledgement_without_saved_result_is_not_trusted() -> None:
    backend = FakeBackend()
    context = FakeContext(backend)
    context.forge_child_receipt = True
    state = run_coordinator(context)
    assert "Review saved" not in state.final_text
    assert backend.run["status"] == "failed"
    assert len(context.invocations) == 1


@pytest.mark.parametrize("failure", ["revoked-source", "wrong-claim", "no-team"])
def test_invalid_authorization_or_run_context_fails_before_model(failure: str) -> None:
    backend = FakeBackend()
    context = FakeContext(backend)
    if failure == "revoked-source":
        backend.source_revoked = True
    elif failure == "wrong-claim":
        backend.wrong_claim = True
    else:
        context.binding.portable_context.team_id = None
    state = run_coordinator(context)
    assert "Review saved" not in state.final_text
    assert not context.model_payloads
    assert not context.invocations


def test_child_completed_stage_is_idempotent_without_model_or_save() -> None:
    backend = FakeBackend()
    context = FakeContext(backend)
    run_coordinator(context)
    calls_before = len(backend.calls)
    child = FakeContext(backend, role="analyst")
    child.model = None
    state = asyncio.run(
        run_graph(
            AGENTS[AGENT_IDS["analyst"]],
            json.dumps(
                {
                    "task_id": TASK_ID,
                    "run_id": backend.run["run_id"],
                    "claim_id": backend.run["claim_id"],
                }
            ),
            child,
        )
    )
    assert StageReceipt.model_validate_json(state.final_text).status == "completed"
    assert [op for op, _ in backend.calls[calls_before:]] == [
        "read_document_review_context"
    ]
    assert not child.model_payloads


def test_checkpoint_redacts_source_and_a_new_turn_does_not_reuse_task_identity() -> (
    None
):
    backend = FakeBackend()
    context = FakeContext(backend)
    state = run_coordinator(context)
    agent = AGENTS[AGENT_IDS["coordinator"]]
    state.review_context = ReviewContext.model_validate(
        backend.call(
            "read_document_review_context",
            {
                "team_id": "team-a",
                "task_id": TASK_ID,
                "run_id": backend.run["run_id"],
                "claim_id": backend.run["claim_id"],
            },
        )
    )
    completed = ReviewState.model_validate(agent.build_completed_state(state))
    assert completed.review_context is None
    assert completed.result is None
    fresh = ReviewState.model_validate(
        agent.build_turn_state(
            ReviewInput(message="Review a different document"),
            cast(BoundRuntimeContext, context.binding),
            previous_state=completed,
        )
    )
    assert fresh.task_id == ""


def test_empty_or_oversized_model_decisions_are_rejected() -> None:
    raw = decision("analyst")
    raw["decision"] = ""
    with pytest.raises(ValidationError):
        ReviewDecision.model_validate(raw)
    raw["decision"] = "x" * 1001
    with pytest.raises(ValidationError):
        ReviewDecision.model_validate(raw)


def test_decision_normalization_matches_api() -> None:
    raw = decision("analyst")
    raw.update(decision="  Source decision  ", limitations=["  Missing appendix  "])
    raw["evidence"] = [{"location": "  Section 2  ", "excerpt": "  Source text  "}]
    result = ReviewDecision.model_validate(raw)
    assert result.decision == "Source decision"
    assert result.limitations == ["Missing appendix"]
    assert result.evidence == [Evidence(location="Section 2", excerpt="Source text")]


def test_model_timeout_is_interrupted_without_false_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fred_samples_agents.document_review import graph_steps

    async def slow_model(
        self: FakeContext, output_model: type[BaseModel], messages: list[Any]
    ) -> BaseModel:
        await asyncio.sleep(1)
        return output_model.model_validate(decision(self.role))

    monkeypatch.setattr(graph_steps, "MODEL_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(FakeContext, "invoke_structured_model", slow_model)
    backend = FakeBackend()
    state = run_coordinator(FakeContext(backend))
    assert backend.saved == []
    assert backend.run["status"] == "failed"
    assert "stopped before completion" in state.final_text


def test_model_transport_timeout_does_not_preempt_review_timeout() -> None:
    from fred_samples_agents.document_review import graph_steps

    catalog = (Path(__file__).parents[1] / "config" / "models_catalog.yaml").read_text()
    read_timeout = next(
        float(line.split(":", 1)[1])
        for line in catalog.splitlines()
        if line.strip().startswith("read:")
    )
    assert read_timeout > graph_steps.MODEL_TIMEOUT_SECONDS


def test_start_on_an_open_run_reports_progress_instead_of_a_generic_failure() -> None:
    """An interrupted run is explained, not surfaced as an unexplained stop.

    The application refuses a second concurrent claim. Reporting that as a
    generic failure hides both the cause and the fix, so the coordinator names
    what the previous run completed and which command resolves it.
    """
    backend = FakeBackend()
    backend.task["review"]["runs"] = [
        {
            "run_id": "run-" + "a" * 32,
            "claim_id": "claim-" + "b" * 32,
            "source_hash": "hash",
            "status": "running",
            "stages": [
                {"stage_id": "analyst", "status": "completed", "result": None},
                {"stage_id": "risk_reviewer", "status": "running", "result": None},
                {"stage_id": "action_planner", "status": "queued", "result": None},
                {"stage_id": "summary", "status": "queued", "result": None},
            ],
        }
    ]
    state = run_coordinator(FakeContext(backend))

    assert "already has a review in progress" in state.final_text
    assert "1 of 4 stages completed" in state.final_text
    assert "risk reviewer in progress" in state.final_text
    assert "Resume task-" in state.final_text and "Restart task-" in state.final_text
    assert "stopped before completion" not in state.final_text
    # No claim is attempted, so the application never has to reject one.
    assert [operation for operation, _ in backend.calls] == ["read_task_progress"]


def test_an_interrupted_run_resumes_without_asking_for_a_second_turn() -> None:
    """An expired lease is not a decision to hand back to the person.

    A read projects a stale run to `interrupted`, so the previous attempt is
    already abandoned and the document is unchanged. Continuing it keeps every
    saved decision, which is the only useful outcome, so the coordinator does
    it rather than ending the turn with an instruction to type Resume.
    """
    backend = FakeBackend()
    first = FakeContext(backend)
    first.fail_model_role = "risk_reviewer"
    run_coordinator(first)
    first_claim = backend.run["claim_id"]
    assert backend.saved == ["analyst"]

    # The lease expires; every later read projects the run as interrupted.
    backend.run["status"] = "interrupted"

    context = FakeContext(backend)
    state = run_coordinator(context)  # a plain start, with no Resume command

    # Resumed in place: same run, fresh claim, completed work never repeated.
    assert len(backend.task["review"]["runs"]) == 1
    assert backend.run["claim_id"] != first_claim
    assert context.invocations == [
        AGENT_IDS["risk_reviewer"],
        AGENT_IDS["action_planner"],
    ]
    assert backend.saved == list(STAGE_IDS)
    assert "Review saved" in state.final_text
    assert "stopped before completion" not in state.final_text


def test_expired_bearer_mid_review_keeps_saved_work_and_starts_no_specialist() -> None:
    """A token that expires part-way must not lose work or run ahead.

    The runtime forwards the caller's bearer and cannot refresh it, so a long
    review can outlive its own token. What matters is that the stages already
    saved survive, no later specialist is started on a dead token, and the
    token itself never reaches the user-visible text.
    """
    backend = FakeBackend()
    backend.expire_token_after = "analyst"
    state = run_coordinator(FakeContext(backend))

    # Work completed before the expiry is durable.
    assert backend.saved == ["analyst"]
    # Nothing ran afterwards on an unusable token.
    assert "risk_reviewer" not in backend.saved
    assert backend.run["stages"][1]["status"] != "completed"
    # The user is told to resume, and no credential leaks into the answer.
    assert "Resume" in state.final_text
    assert "refresh_token" not in state.final_text
    assert "401" not in state.final_text
