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
"""
Offline smoke tests for the pod registry: every declared agent boots, every
graph agent's workflow assembles without error. None of these touch a
model, an MCP server, or the network — that's what --disable-socket
enforces.
"""

from fred_sdk.contracts.models import GraphAgentDefinition

from fred_samples_agents.registry import REGISTRY

EXPECTED_AGENT_IDS = {
    "fred.samples.assistant",
    "fred.samples.hello_graph",
    "fred.samples.bank_transfer.graph",
    "fred.samples.postal_tracking.graph",
}


def test_registry_keys_match_agent_ids():
    for key, agent in REGISTRY.items():
        assert agent.agent_id == key


def test_expected_sample_agents_are_registered():
    assert EXPECTED_AGENT_IDS <= set(REGISTRY.keys())


def test_every_graph_agent_builds_a_valid_workflow():
    graph_agents = {
        key: agent
        for key, agent in REGISTRY.items()
        if isinstance(agent, GraphAgentDefinition)
    }
    assert graph_agents, "expected at least one GraphAgentDefinition in the registry"

    for key, agent in graph_agents.items():
        graph = agent.build_graph()
        node_ids = {node.node_id for node in graph.nodes}
        assert graph.entry_node in node_ids, (
            f"{key}: entry node {graph.entry_node!r} is not among its own nodes"
        )
