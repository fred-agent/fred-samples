"""Team-of-3 sample package: 1 graph child + 2 ReAct children + route team."""

from fred_samples_agents.team_of_3_agents_sample.graph_agent import (
    TEAM3_GRAPH_AGENT,
    TEAM3_GRAPH_AGENT_ID,
)
from fred_samples_agents.team_of_3_agents_sample.react_agent_one import (
    TEAM3_REACT_AGENT_ONE,
    TEAM3_REACT_AGENT_ONE_ID,
)
from fred_samples_agents.team_of_3_agents_sample.react_agent_two import (
    TEAM3_REACT_AGENT_TWO,
    TEAM3_REACT_AGENT_TWO_ID,
)
from fred_samples_agents.team_of_3_agents_sample.team import TEAM3_ROUTER_TEAM

__all__ = [
    "TEAM3_GRAPH_AGENT",
    "TEAM3_GRAPH_AGENT_ID",
    "TEAM3_REACT_AGENT_ONE",
    "TEAM3_REACT_AGENT_ONE_ID",
    "TEAM3_REACT_AGENT_TWO",
    "TEAM3_REACT_AGENT_TWO_ID",
    "TEAM3_ROUTER_TEAM",
]

