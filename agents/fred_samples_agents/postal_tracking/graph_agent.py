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
Graph definition for the postal tracking sample agent.

Purpose:
- demonstrate a v2 graph agent that uses two MCP servers (postal + IoT)
- show how to emit GeoPart maps for pickup point (relais) visualization
- include an optional HITL confirmation before rerouting a parcel
- illustrate build_turn_state for natural conversation continuity across turns

MCP servers required:
- mcp-postal-business-demo (port 9797): track_package, get_pickup_points_nearby,
  reroute_package_to_pickup_point, notify_customer, list_my_active_parcels,
  seed_demo_parcel_exception_for_current_user
  → cd ../servers/mcp/python/postal_business_mcp_server && make run
- mcp-iot-tracking-demo (port 9798): get_live_tracking_snapshot, get_route_geometry,
  seed_demo_tracking_incident
  → cd ../servers/mcp/python/iot_tracking_mcp_server && make run
"""

from __future__ import annotations

from fred_sdk import (
    GraphAgent,
    GraphExecutionOutput,
    GraphWorkflow,
    MCPServerRef,
)
from fred_sdk.contracts.context import BoundRuntimeContext, GeoPart
from pydantic import BaseModel

from .graph_state import PostalTrackingInput, PostalTrackingState
from .graph_steps import (
    analyze_intent_step,
    answer_conversationally_step,
    confirm_reroute_step,
    execute_reroute_step,
    finalize_step,
    load_tracking_step,
    seed_demo_step,
)

MCP_SERVER_POSTAL = "mcp-postal-business-demo"
MCP_SERVER_IOT = "mcp-iot-tracking-demo"


class PostalTrackingGraphAgent(GraphAgent):
    """
    Sample v2 graph agent for postal tracking and pickup point rerouting.

    Why this exists:
    - demonstrate a multi-step workflow that combines postal and IoT MCP servers
    - provide a ready-to-run sample that renders a GeoPart map for relais
    - show build_turn_state for natural conversation continuity

    How to use it:
    - start the postal and IoT demo MCP servers (see module docstring above)
    - select agent id "sample.postal_tracking.graph" in fred-agents-cli
    """

    agent_id: str = "fred.samples.postal_tracking.graph"
    role: str = "Postal Tracking Assistant"
    description: str = (
        "Sample graph agent that tracks parcels, shows pickup point locations, "
        "and optionally reroutes a parcel using the postal business and IoT MCP servers."
    )
    tags: tuple[str, ...] = (
        "postal",
        "tracking",
        "graph",
        "sample",
        "mcp",
        "iot",
        "map",
        "hitl",
    )

    default_mcp_servers: tuple[MCPServerRef, ...] = (
        MCPServerRef(id=MCP_SERVER_POSTAL),
        MCPServerRef(id=MCP_SERVER_IOT),
    )

    input_schema = PostalTrackingInput
    state_schema = PostalTrackingState
    input_to_state = {"message": "latest_user_text"}
    output_state_field = "final_text"

    workflow = GraphWorkflow(
        entry="analyze_intent",
        nodes={
            "analyze_intent": analyze_intent_step,
            "answer_conversationally": answer_conversationally_step,
            "seed_demo": seed_demo_step,
            "load_tracking": load_tracking_step,
            "confirm_reroute": confirm_reroute_step,
            "execute_reroute": execute_reroute_step,
            "finalize": finalize_step,
        },
        edges={
            "answer_conversationally": "finalize",
            "execute_reroute": "finalize",
        },
        routes={
            "analyze_intent": {
                "track_request": "load_tracking",
                "seed_demo": "seed_demo",
                "conversational": "answer_conversationally",
            },
            "seed_demo": {
                "ok": "load_tracking",
                "error": "finalize",
            },
            "load_tracking": {
                "ok": "finalize",
                "reroute": "confirm_reroute",
                "error": "finalize",
            },
            "confirm_reroute": {
                "selected": "execute_reroute",
                "cancelled": "finalize",
            },
        },
    )

    def build_turn_state(
        self,
        input_model: BaseModel,
        binding: BoundRuntimeContext,
        previous_state: BaseModel | None = None,
    ) -> BaseModel:
        """
        Carry the active parcel tracking_id across conversation turns.

        Why this exists:
        - without this, tracking_id resets to None on every new message
        - users expect "reroute it" or "show the map" to work without
          repeating the tracking id each turn
        """
        base = self.build_initial_state(input_model, binding)
        if not isinstance(previous_state, PostalTrackingState):
            return base
        base_state = PostalTrackingState.model_validate(base)
        if previous_state.tracking_id and not base_state.tracking_id:
            return base_state.model_copy(
                update={"tracking_id": previous_state.tracking_id}
            )
        return base_state

    def build_output(self, state: PostalTrackingState) -> GraphExecutionOutput:
        """Return the final output with optional GeoPart map rendering."""
        content = state.final_text or ""
        if state.ui_geojson:
            return GraphExecutionOutput(
                content=content,
                ui_parts=(
                    GeoPart(
                        geojson=state.ui_geojson,
                        popup_property="name",
                        fit_bounds=True,
                    ),
                ),
            )
        return GraphExecutionOutput(content=content)


POSTAL_TRACKING_AGENT = PostalTrackingGraphAgent()
