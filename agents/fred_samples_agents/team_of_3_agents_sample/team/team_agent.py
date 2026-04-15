from fred_sdk import AgentSpec, TeamAgent

from fred_samples_agents.team_of_3_agents_sample.graph_agent import TEAM3_GRAPH_AGENT_ID
from fred_samples_agents.team_of_3_agents_sample.react_agent_one import (
    TEAM3_REACT_AGENT_ONE_ID,
)
from fred_samples_agents.team_of_3_agents_sample.react_agent_two import (
    TEAM3_REACT_AGENT_TWO_ID,
)


class TeamOf3RouterSample(TeamAgent):
    agent_id: str = "fred.samples.team_of_3.router"
    role: str = "Route each user request to one of three specialists"
    description: str = (
        "Sample TeamAgent in route mode with exactly three children "
        "(one graph child, two ReAct children)."
    )
    tags: tuple[str, ...] = ("sample", "team", "routing", "route_mode")
    mode = "route"
    coordinator_instructions = (
        "You are a strict dispatcher. Pick exactly one specialist based on user intent. "
        "Choose the graph specialist for approval/rejection-style requests, "
        "the math specialist for arithmetic or unit conversions, "
        "and the writing specialist for rewrite/summarize/glossary tasks."
    )
    members = (
        AgentSpec(
            name="Graph Approval Specialist",
            role="Deterministic approval/rejection workflow",
            agent_ref=TEAM3_GRAPH_AGENT_ID,
        ),
        AgentSpec(
            name="Math Conversion Specialist",
            role="Arithmetic and metric unit conversion",
            agent_ref=TEAM3_REACT_AGENT_ONE_ID,
        ),
        AgentSpec(
            name="Writing Specialist",
            role="Rewrite, summarize, and glossary explanation",
            agent_ref=TEAM3_REACT_AGENT_TWO_ID,
        ),
    )


TEAM3_ROUTER_TEAM = TeamOf3RouterSample()

