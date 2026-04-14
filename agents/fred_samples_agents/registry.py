# Copyright Thales 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from fred_sdk.contracts.models import GraphAgentDefinition, ReActAgentDefinition

from fred_samples_agents.bank_transfer.graph_agent import BANK_TRANSFER_AGENT
from fred_samples_agents.postal_tracking.graph_agent import POSTAL_TRACKING_AGENT
from fred_samples_agents.general_assistant import GENERAL_ASSISTANT_AGENT
from fred_samples_agents.team_of_3_agents_sample import (
    TEAM3_GRAPH_AGENT,
    TEAM3_REACT_AGENT_ONE,
    TEAM3_REACT_AGENT_TWO,
    TEAM3_ROUTER_TEAM,
)

def build_registry() -> dict[str, ReActAgentDefinition | GraphAgentDefinition]:
    return {
        GENERAL_ASSISTANT_AGENT.agent_id: GENERAL_ASSISTANT_AGENT,
        BANK_TRANSFER_AGENT.agent_id: BANK_TRANSFER_AGENT,
        POSTAL_TRACKING_AGENT.agent_id: POSTAL_TRACKING_AGENT,
        TEAM3_GRAPH_AGENT.agent_id: TEAM3_GRAPH_AGENT,
        TEAM3_REACT_AGENT_ONE.agent_id: TEAM3_REACT_AGENT_ONE,
        TEAM3_REACT_AGENT_TWO.agent_id: TEAM3_REACT_AGENT_TWO,
        TEAM3_ROUTER_TEAM.agent_id: TEAM3_ROUTER_TEAM,
    }


REGISTRY: dict[str, ReActAgentDefinition | GraphAgentDefinition] = build_registry()
