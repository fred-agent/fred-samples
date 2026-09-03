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
Graph definition for the Hello Graph sample agent.

Purpose:
- the minimal v2 graph agent: no MCP server, no human-in-the-loop gate, no
  external dependency of any kind beyond a configured chat model
- read this first, before bank_transfer or postal_tracking, to see the shape
  of a graph agent with nothing else in the way

Workflow overview:
    classify
     ├─ greeting  ──► greet ──► finalize
     └─ question  ──► answer ──► finalize

No MCP servers required — just a configured model.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from fred_sdk import GraphAgent, GraphWorkflow

from .graph_state import HelloGraphInput, HelloGraphState
from .graph_steps import (
    answer_step,
    classify_step,
    finalize_step,
    greet_step,
)


class HelloGraphAgent(GraphAgent):
    """
    Sample v2 graph agent with the smallest possible shape: classify, then
    answer on one of two branches.

    Use this agent as a reference when building any workflow agent, before
    adding MCP tools or HITL gates. Once this shape is clear, look at
    bank_transfer (MCP + two HITL gates) or postal_tracking (MCP + map +
    one HITL gate) for a fuller example.

    Change graph_agent.py when the workflow shape changes.
    Change graph_state.py when the data model changes.
    Change graph_steps.py when step behaviour changes.
    """

    agent_id: str = "fred.samples.hello_graph"
    role: str = "Hello Graph"
    description: str = (
        "Minimal sample graph agent: classifies a message as a greeting or a "
        "question, then answers on the matching branch. No MCP server, no HITL."
    )
    tags: tuple[str, ...] = ("sample", "graph", "hello_world", "minimal", "v2")

    input_schema = HelloGraphInput
    state_schema = HelloGraphState
    input_to_state: ClassVar[Mapping[str, str]] = {"message": "latest_user_text"}
    output_state_field = "final_text"

    workflow = GraphWorkflow(
        entry="classify",
        nodes={
            "classify": classify_step,
            "greet": greet_step,
            "answer": answer_step,
            "finalize": finalize_step,
        },
        edges={
            "greet": "finalize",
            "answer": "finalize",
        },
        routes={
            "classify": {
                "greeting": "greet",
                "question": "answer",
            },
        },
    )


HELLO_GRAPH_AGENT = HelloGraphAgent()
