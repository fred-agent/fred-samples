"""Four real Fred GraphAgents sharing one bearer-authenticated app MCP server."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from fred_sdk import GraphAgent, GraphWorkflow, MCPServerRef
from pydantic import BaseModel

from .graph_state import AGENT_IDS, ReviewInput, ReviewState, StageId
from .graph_steps import (
    decide_stage,
    delegate,
    finish,
    guarded,
    load_stage,
    parse_work,
    prepare_review,
    save_stage,
)

MCP_SERVER_ID = "mcp-review-board"


def _specialist_workflow(stage_id: StageId) -> GraphWorkflow:
    return GraphWorkflow(
        entry="parse",
        nodes={
            "parse": parse_work(stage_id),
            "load": load_stage(stage_id),
            "decide": decide_stage(stage_id),
            "save": save_stage(stage_id),
            "finish": finish,
        },
        routes={
            "parse": {"next": "load", "done": "finish"},
            "load": {"next": "decide", "done": "finish"},
            "decide": {"next": "save", "done": "finish"},
            "save": {"done": "finish"},
        },
    )


class _DocumentReviewGraph(GraphAgent):
    supports_capabilities: bool = True
    tags: tuple[str, ...] = ("sample", "document-review", "graph", "mcp")
    # Each child activates its own defaults. Managed-instance selections are
    # not inherited through local agent delegation.
    default_mcp_servers: tuple[MCPServerRef, ...] = (MCPServerRef(id=MCP_SERVER_ID),)
    input_schema = ReviewInput
    state_schema = ReviewState
    input_to_state: ClassVar[Mapping[str, str]] = {"message": "latest_user_text"}

    def build_completed_state(self, state: BaseModel) -> BaseModel:
        # App OpenSearch is the durable record. Do not duplicate source text or
        # full decisions in the runtime's completed-state checkpoint. The next
        # turn always starts fresh and retrieves its explicitly named task.
        return state.model_copy(update={"review_context": None, "result": None})


class DocumentReviewCoordinator(_DocumentReviewGraph):
    agent_id: str = AGENT_IDS["coordinator"]
    role: str = "Review Board Agent - Coordinator"
    description: str = (
        "Review a Review Board corpus task with three specialists in order, "
        "then save a final summary. Start with Review task-<id>; use Resume "
        "task-<id> after interruption. Requires the Review Board MCP service."
    )
    workflow = GraphWorkflow(
        entry="prepare",
        nodes={
            "prepare": guarded(prepare_review),
            "analyst": delegate("analyst"),
            "risk_reviewer": delegate("risk_reviewer"),
            "action_planner": delegate("action_planner"),
            "summary_load": load_stage("summary", coordinator=True),
            "summary_decide": decide_stage("summary", coordinator=True),
            "summary_save": save_stage("summary", coordinator=True),
            "finish": finish,
        },
        routes={
            "prepare": {"next": "analyst", "done": "finish"},
            "analyst": {"next": "risk_reviewer", "done": "finish"},
            "risk_reviewer": {"next": "action_planner", "done": "finish"},
            "action_planner": {"next": "summary_load", "done": "finish"},
            "summary_load": {"next": "summary_decide", "done": "finish"},
            "summary_decide": {"next": "summary_save", "done": "finish"},
            "summary_save": {"done": "finish"},
        },
    )


class DocumentAnalyst(_DocumentReviewGraph):
    agent_id: str = AGENT_IDS["analyst"]
    role: str = "Review Board Agent - Analyst"
    description: str = (
        "Identify document purpose, factual claims and supporting evidence."
    )
    workflow = _specialist_workflow("analyst")


class DocumentRiskReviewer(_DocumentReviewGraph):
    agent_id: str = AGENT_IDS["risk_reviewer"]
    role: str = "Review Board Agent - Risk Reviewer"
    description: str = "Review ambiguity, inconsistencies and risks against the source."
    workflow = _specialist_workflow("risk_reviewer")


class DocumentActionPlanner(_DocumentReviewGraph):
    agent_id: str = AGENT_IDS["action_planner"]
    role: str = "Review Board Agent - Action Planner"
    description: str = (
        "Recommend prioritized actions from the saved analyst and risk reviews."
    )
    workflow = _specialist_workflow("action_planner")


DOCUMENT_REVIEW_AGENTS: tuple[GraphAgent, ...] = (
    DocumentReviewCoordinator(),
    DocumentAnalyst(),
    DocumentRiskReviewer(),
    DocumentActionPlanner(),
)
