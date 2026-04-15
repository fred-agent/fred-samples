from fred_sdk import GraphAgent, GraphWorkflow

from .graph_state import Team3GraphInput, Team3GraphState
from .graph_steps import (
    approved_step,
    classify_request_step,
    finalize_graph_step,
    needs_review_step,
    rejected_step,
)

TEAM3_GRAPH_AGENT_ID = "fred.samples.team_of_3.graph_child"


class Team3GraphChildAgent(GraphAgent):
    agent_id: str = TEAM3_GRAPH_AGENT_ID
    role: str = "Deterministic approval classifier"
    description: str = (
        "Deterministic graph workflow that classifies approve/reject requests "
        "using explicit keyword rules."
    )
    tags: tuple[str, ...] = ("sample", "team", "graph", "deterministic", "routing")

    input_schema = Team3GraphInput
    state_schema = Team3GraphState
    input_to_state = {"message": "latest_user_text"}
    output_state_field = "final_text"

    workflow = GraphWorkflow(
        entry="classify_request",
        nodes={
            "classify_request": classify_request_step,
            "approved": approved_step,
            "rejected": rejected_step,
            "needs_review": needs_review_step,
            "finalize": finalize_graph_step,
        },
        edges={
            "approved": "finalize",
            "rejected": "finalize",
            "needs_review": "finalize",
        },
        routes={
            "classify_request": {
                "approved": "approved",
                "rejected": "rejected",
                "needs_review": "needs_review",
            }
        },
    )


TEAM3_GRAPH_AGENT = Team3GraphChildAgent()

