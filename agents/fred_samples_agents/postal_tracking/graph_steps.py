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
Business steps for the postal tracking graph agent sample.

Why this module exists:
- keep the workflow steps small, typed, and easy to scan
- centralize postal + IoT MCP tool usage for the demo flow

How to use it:
- import these steps in graph_agent.py when wiring the GraphWorkflow

Design principles illustrated here:
- intent_router_step: single model call for classification + field extraction
- model_text_step: LLM-generated summaries instead of mechanical string templates
- choice_step: pauses execution for explicit user confirmation before mutation
- build_turn_state (in graph_agent.py): keeps tracking_id alive across turns so
  the conversation feels continuous, not stateless
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fred_sdk import (
    GraphNodeContext,
    GraphNodeResult,
    HumanChoiceOption,
    StepResult,
    choice_step,
    intent_router_step,
    model_text_step,
    typed_node,
)
from fred_sdk import (
    finalize_step as _finalize_step,
)
from pydantic import BaseModel, Field

from .graph_state import PostalTrackingState

# ── System prompts ──────────────────────────────────────────────────────────────

_INTENT_SYSTEM_PROMPT_BASE = """\
You are a routing assistant for a postal tracking workflow.

The agent can:
- track a parcel by tracking_id (example: PKG-ABCD1234)
- seed a demo parcel + IoT incident when the user asks for a demo
- show pickup points (relais) and a map when the user asks to see locations
- reroute a parcel to a pickup point when explicitly requested

Classify the user message:
- "track_request": the user wants tracking status, location, pickup points, or wants
  to act on a parcel (reroute, reschedule, show relais). Use this whenever the user
  refers to "the parcel", "it", "ce colis" etc. — even without an explicit tracking id,
  if a parcel is already in context (see active_tracking_id below).
- "seed_demo": the user asks to seed a demo parcel or says they want a demo
- "conversational": greetings, capability questions, or general questions with no
  reference to a specific parcel and no active parcel in context

Extract:
- tracking_id: parcel identifier if explicitly mentioned (e.g. PKG-ABCD1234), else null
- wants_map: true only if the user explicitly asks for a map, route, or location
- wants_pickup_points: true only if the user asks for pickup points, relais, lockers,
  or a reroute destination
- wants_reroute: true if the user asks to reroute, redirect, réacheminer, or deliver
  to a pickup point / relais
"""


def _build_intent_system_prompt(tracking_id: str | None) -> str:
    """
    Build a context-aware intent router prompt.

    Why this exists:
    - when a parcel is already active in the session, the router should treat
      references like "it", "ce colis", "réacheminer" as track_request, not
      conversational
    - injecting tracking_id here avoids a second model call to resolve the reference

    How to use it:
    - call with state.tracking_id before intent_router_step
    """
    if tracking_id:
        context_note = (
            f"\nIMPORTANT: active_tracking_id={tracking_id}. "
            "The user is already talking about this parcel. "
            "Any message that refers to it (reroute, show map, status, relais, "
            "'it', 'ce colis', 'le', etc.) must be classified as 'track_request'. "
            "Only use 'conversational' for questions that are clearly unrelated to "
            "this parcel (greetings, capability questions, off-topic)."
        )
    else:
        context_note = "\nactive_tracking_id=none (no parcel in context yet)."
    return _INTENT_SYSTEM_PROMPT_BASE + context_note


_TRACKING_SUMMARY_SYSTEM_PROMPT = """\
You are a friendly and helpful postal tracking assistant.

Based on the parcel tracking data provided, answer the user's question naturally.
Be warm and conversational — not mechanical or log-like.
Use bullet points only when listing multiple distinct items (e.g., pickup point options).

Guidelines:
- Mention the tracking id once, naturally, not as a label.
- If the parcel is delayed, acknowledge it with empathy before explaining the situation.
- If pickup points are available and the user asked for them, list them briefly.
- If a map is available (map_available=true), say so in one short sentence at the end.
- Do not invent information that is not in the data. Omit missing fields gracefully.
- Keep the response concise: 3–6 lines for a status update, slightly more if listing options.
"""

_REROUTE_SUMMARY_SYSTEM_PROMPT = """\
You are a helpful postal tracking assistant confirming a completed action.

Write a short, warm confirmation that the parcel was rerouted. Mention:
- the tracking id
- the chosen pickup point name and id
- that the customer has been notified

Keep it to 2–3 sentences. Be reassuring, not mechanical.
"""


def _build_conversational_system_prompt(tracking_id: str | None) -> str:
    """
    Build a context-aware system prompt for the conversational branch.

    Why this exists:
    - if the user is in the middle of a conversation about a specific parcel,
      the agent should be able to reference it naturally without extra tool calls
    - avoids coupling the lightweight conversational path to MCP tool calls

    How to use it:
    - call with state.tracking_id; it returns a richer prompt when a parcel is active
    """
    base = """\
You are a helpful postal tracking assistant.

You can help users:
- track a parcel by tracking id (e.g. PKG-ABC123)
- seed a demo parcel for testing
- show pickup points (relais) and a map for a tracked parcel
- reroute a parcel to a pickup point after confirmation

Keep the response short and actionable. If the user's question is vague, ask one \
clarifying question rather than guessing.
"""
    if tracking_id:
        base += (
            f"\nThe user is currently tracking parcel {tracking_id}. "
            "Reference it naturally when relevant. You already have it in context — "
            "do not ask the user to provide it again."
        )
    return base


# ── Intent model ────────────────────────────────────────────────────────────────


class PostalIntent(BaseModel):
    """
    Structured intent for routing postal tracking requests.

    Why this exists:
    - the entry node needs a typed, single-shot classification and extraction
    - it keeps routing decisions and payload extraction in one model call

    How to use it:
    - use with intent_router_step to choose the next node
    """

    intent: Literal["track_request", "seed_demo", "conversational"] = Field(
        description="Routing label for the entry node."
    )
    tracking_id: str | None = Field(
        default=None, description="Tracking id if provided in the message."
    )
    wants_map: bool = Field(
        default=False,
        description="True only if the user explicitly asked for a map, route, or location.",
    )
    wants_pickup_points: bool = Field(
        default=False,
        description="True only if the user explicitly asked for pickup points or relais.",
    )
    wants_reroute: bool = Field(
        default=False,
        description="True only if the user explicitly asked to reroute the parcel.",
    )


# ── GeoJSON helpers ─────────────────────────────────────────────────────────────


def _point_feature(
    *,
    lon: Any,
    lat: Any,
    name: str,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Build a GeoJSON point feature from raw coordinates.

    Why this exists:
    - map rendering needs strict GeoJSON even when tool outputs are loose
    - a single helper keeps validation logic in one place

    How to use it:
    - pass lon/lat values plus a display name and optional properties

    Example:
    ```python
    feature = _point_feature(lon=2.33, lat=48.86, name="Hub")
    ```
    """

    try:
        lon_f = float(lon)
        lat_f = float(lat)
    except (TypeError, ValueError):
        return None
    props = {"name": name}
    if properties:
        props.update(properties)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon_f, lat_f]},
        "properties": props,
    }


def _build_tracking_geojson(
    *,
    route_geometry: dict[str, Any] | None,
    route_markers: list[dict[str, Any]],
    pickup_points: list[dict[str, Any]],
    highlight_pickup_point_id: str | None,
    business_track: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Combine IoT route geometry and pickup points into a map-friendly GeoJSON.

    Why this exists:
    - the UI needs a single FeatureCollection to render a map
    - merging route, vehicle, and pickup point data keeps the sample cohesive

    How to use it:
    - call after collecting route geometry and pickup points
    - pass highlight_pickup_point_id to emphasize a selected relais

    Example:
    ```python
    geojson = _build_tracking_geojson(
        route_geometry=route_geometry,
        route_markers=markers,
        pickup_points=pickup_points,
        highlight_pickup_point_id="PP-PAR-001",
        business_track=track,
    )
    ```
    """

    features: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    polyline = (route_geometry or {}).get("polyline") if route_geometry else None
    if isinstance(polyline, list):
        coords: list[list[float]] = []
        for point in polyline:
            if not isinstance(point, dict):
                continue
            lat_raw = point.get("lat")
            lon_raw = point.get("lon")
            if lat_raw is None or lon_raw is None:
                continue
            try:
                lat_f = float(lat_raw)
                lon_f = float(lon_raw)
            except (TypeError, ValueError):
                continue
            coords.append([lon_f, lat_f])
        if len(coords) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "name": "Route",
                        "kind": "route",
                        "style": {
                            "color": "#1d4ed8",
                            "weight": 4,
                            "opacity": 0.85,
                        },
                    },
                }
            )

    for marker in route_markers:
        if not isinstance(marker, dict):
            continue
        marker_id = str(marker.get("id") or "")
        if marker_id:
            seen_ids.add(marker_id)
        kind = str(marker.get("kind") or "marker")
        style: dict[str, Any] = {"weight": 2, "opacity": 1.0, "fillOpacity": 0.9}
        radius = 6
        if kind == "hub":
            style.update({"color": "#c2410c", "fillColor": "#fb923c"})
            radius = 8
        elif kind == "vehicle":
            style.update({"color": "#1d4ed8", "fillColor": "#60a5fa"})
            radius = 7
        elif kind in {"pickup_locker", "pickup_point"}:
            style.update({"color": "#166534", "fillColor": "#4ade80"})
            radius = 7
        feature = _point_feature(
            lon=marker.get("lon"),
            lat=marker.get("lat"),
            name=str(marker.get("label") or marker_id or "Marker"),
            properties={
                "id": marker_id or None,
                "kind": kind,
                "status": marker.get("status"),
                "radius": radius,
                "style": style,
            },
        )
        if feature:
            features.append(feature)

    if business_track:
        current_location = business_track.get("current_location")
        if isinstance(current_location, dict):
            current_id = str(
                current_location.get("vehicle_id")
                or current_location.get("hub_id")
                or current_location.get("label")
                or ""
            )
            if current_id and current_id not in seen_ids:
                feature = _point_feature(
                    lon=current_location.get("lon"),
                    lat=current_location.get("lat"),
                    name=str(current_location.get("label") or "Parcel location"),
                    properties={
                        "id": current_id,
                        "kind": str(current_location.get("kind") or "business"),
                        "radius": 7,
                        "style": {
                            "color": "#7c3aed",
                            "fillColor": "#c4b5fd",
                            "weight": 2,
                            "fillOpacity": 0.85,
                        },
                    },
                )
                if feature:
                    features.append(feature)

    for point in pickup_points[:5]:
        if not isinstance(point, dict):
            continue
        pp_id = str(point.get("pickup_point_id") or "")
        is_selected = bool(pp_id) and pp_id == highlight_pickup_point_id
        base_color = "#166534"
        fill_color = "#86efac"
        if str(point.get("type")) == "locker":
            base_color = "#0f766e"
            fill_color = "#5eead4"
        if is_selected:
            base_color = "#b45309"
            fill_color = "#fbbf24"
        desc_bits = []
        if point.get("type"):
            desc_bits.append(f"type={point.get('type')}")
        if point.get("available_slots") is not None:
            desc_bits.append(f"slots={point.get('available_slots')}")
        if point.get("distance_hint_km") is not None:
            desc_bits.append(f"distance={point.get('distance_hint_km')} km")
        feature = _point_feature(
            lon=point.get("lon"),
            lat=point.get("lat"),
            name=str(point.get("name") or pp_id or "Pickup point"),
            properties={
                "id": pp_id or None,
                "kind": "pickup_point_candidate",
                "pickup_point_id": pp_id or None,
                "pickup_type": point.get("type"),
                "description": ", ".join(desc_bits) if desc_bits else None,
                "radius": 9 if is_selected else 7,
                "style": {
                    "color": base_color,
                    "fillColor": fill_color,
                    "weight": 3 if is_selected else 2,
                    "fillOpacity": 0.9,
                },
            },
        )
        if feature:
            features.append(feature)

    if not features:
        return None
    return {"type": "FeatureCollection", "features": features}


# ── Summary helpers ─────────────────────────────────────────────────────────────


def _build_tracking_context(
    *,
    tracking_id: str,
    business_track: dict[str, Any],
    iot_snapshot: dict[str, Any] | None,
    pickup_points: list[dict[str, Any]],
    map_available: bool,
) -> dict[str, Any]:
    """
    Build a lean JSON context dict for model_text_step tracking summaries.

    Why this exists:
    - passing the full raw MCP response to the model wastes tokens and adds noise
    - a curated subset produces cleaner, more focused responses

    How to use it:
    - call before model_text_step in load_tracking_step and execute_reroute_step
    """
    eta = business_track.get("eta") or {}
    current_location = business_track.get("current_location") or {}
    return {
        "tracking_id": tracking_id,
        "status": business_track.get("status"),
        "receiver": business_track.get("receiver"),
        "current_location_label": current_location.get("label"),
        "eta_promised": eta.get("promised_ts"),
        "eta_estimated": eta.get("estimated_ts"),
        "delay_minutes": eta.get("delay_minutes"),
        "iot_phase": iot_snapshot.get("phase") if iot_snapshot else None,
        "pickup_points": [
            {
                "id": p.get("pickup_point_id"),
                "name": p.get("name"),
                "city": p.get("city"),
                "type": p.get("type"),
                "available_slots": p.get("available_slots"),
            }
            for p in pickup_points[:3]
        ],
        "map_available": map_available,
    }


def _fallback_tracking_summary(
    *,
    tracking_id: str,
    business_track: dict[str, Any],
    pickup_points: list[dict[str, Any]],
    map_available: bool,
) -> str:
    """
    Deterministic fallback when model_text_step is unavailable.

    Why this exists:
    - the graph must stay runnable even without a language model (tests, offline demos)

    How to use it:
    - pass as fallback_text to model_text_step in load_tracking_step
    """
    status = business_track.get("status", "unknown")
    receiver = business_track.get("receiver", "")
    eta = business_track.get("eta") or {}
    delay = eta.get("delay_minutes")

    lines = [f"Parcel {tracking_id}"]
    if receiver:
        lines[0] += f" for {receiver}"
    lines.append(f"Status: {status}")
    if delay is not None:
        lines.append(f"Estimated delay: {delay} min")
    if pickup_points:
        ids = [
            str(p.get("pickup_point_id") or "")
            for p in pickup_points[:3]
            if p.get("pickup_point_id")
        ]
        if ids:
            lines.append(f"Nearby pickup points: {', '.join(ids)}")
    if map_available:
        lines.append("A map is shown below.")
    return "\n".join(lines)


# ── Steps ───────────────────────────────────────────────────────────────────────


@typed_node(PostalTrackingState)
async def analyze_intent_step(
    state: PostalTrackingState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Classify the user message and extract tracking intent in one model call.

    Why this exists:
    - routing between demo seeding, tracking, and conversation must happen first
    - extraction of tracking_id and map preferences avoids extra model hops

    How to use it:
    - keep as the entry node and route on "track_request", "seed_demo", or
      "conversational" in the workflow

    Design note:
    - the state already carries tracking_id from the previous turn (via build_turn_state)
    - when the model returns a new tracking_id it overrides the carried one
    - when the model returns null the carried one is preserved (not overwritten)
    """

    context.emit_status("analyze_intent", "Understanding your request.")
    system_prompt = _build_intent_system_prompt(state.tracking_id)

    async with context.thinking("planning", title="Classifying the request") as thought:
        await thought.write(f"User said: {state.latest_user_text!r}")
        if state.tracking_id:
            await thought.write(
                f"Active parcel already in context: {state.tracking_id}"
            )

        raw = await intent_router_step(
            context,
            route_model=PostalIntent,
            system_prompt=system_prompt,
            user_prompt=state.latest_user_text,
            fallback_output={
                "intent": "conversational",
                "tracking_id": None,
                "wants_map": False,
                "wants_pickup_points": False,
                "wants_reroute": False,
            },
            route_field="intent",
            state_update_builder=lambda d: {
                # Only overwrite tracking_id when the model found a new one
                **({"tracking_id": d.tracking_id} if d.tracking_id else {}),
                "wants_map": d.wants_map,
                "wants_pickup_points": d.wants_pickup_points,
                "wants_reroute": d.wants_reroute,
            },
        )

        route_key = raw.route_key
        update = dict(raw.state_update)

        # Heuristic fallbacks — catch keywords the model may have missed.
        # Note: wants_map and wants_pickup_points are intentionally independent.
        # "Où est mon colis ?" → map, not pickup points.
        # "Montre-moi les relais" → pickup points, not necessarily map.
        lowered = state.latest_user_text.casefold()
        if not update.get("wants_map") and any(
            w in lowered
            for w in ("map", "carte", "route", "trajet", "position", "localisation")
        ):
            update["wants_map"] = True

        if not update.get("wants_pickup_points") and any(
            w in lowered
            for w in ("relais", "pickup", "locker", "point de retrait", "point relais")
        ):
            update["wants_pickup_points"] = True

        if not update.get("wants_reroute") and any(
            w in lowered
            for w in (
                "réacheminer",
                "reacheminer",
                "réacheminement",
                "reroute",
                "rediriger",
                "point relais",
                "relais le plus proche",
                "livrer à un point",
            )
        ):
            update["wants_reroute"] = True
            update["wants_pickup_points"] = True

        # Guardrail: if the user is acting on the active parcel (reroute, pickup, map)
        # but the model routed to conversational, promote to track_request.
        # This handles messages like "est-il possible de le réacheminer ?" where the
        # model has no tracking id in the message to anchor on.
        action_flags = (
            update.get("wants_reroute")
            or update.get("wants_pickup_points")
            or update.get("wants_map")
        )
        if route_key == "conversational" and action_flags and state.tracking_id:
            await thought.write(
                "Message acts on the active parcel but the model said "
                "'conversational' — promoting to track_request."
            )
            route_key = "track_request"

        await thought.conclude(
            f"Classified as '{route_key}' "
            f"(map={update.get('wants_map')}, "
            f"pickup_points={update.get('wants_pickup_points')}, "
            f"reroute={update.get('wants_reroute')})."
        )

    return StepResult(state_update=update, route_key=route_key)


@typed_node(PostalTrackingState)
async def answer_conversationally_step(
    state: PostalTrackingState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Answer a conversational question about postal tracking.

    Why this exists:
    - the workflow should provide a clear fallback for non-tracking requests
    - when a parcel is already in context (from build_turn_state), the agent
      references it naturally without additional tool calls

    Design note:
    - no MCP tool calls here: the lightweight conversational branch should stay fast
    - tracking_id context is injected via the system prompt, not fetched at runtime
    - this is intentional: conversational questions ("what can you do?", "help") should
      not trigger tool calls; tracking questions are routed to load_tracking_step instead

    How to use it:
    - route the "conversational" branch here and edge to finalize
    """

    context.emit_status("answer", "Preparing response.")
    system_prompt = _build_conversational_system_prompt(state.tracking_id)
    response = await model_text_step(
        context,
        system_prompt=system_prompt,
        user_prompt=state.latest_user_text,
        fallback_text=(
            "I can track a parcel, show pickup points, or seed a demo parcel. "
            "Try: 'Track PKG-1234' or 'Seed a demo parcel'."
        ),
    )
    return StepResult(
        state_update={"final_text": response, "done_reason": "conversational"}
    )


@typed_node(PostalTrackingState)
async def seed_demo_step(
    state: PostalTrackingState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Seed a demo parcel and IoT incident to make tracking deterministic.

    Why this exists:
    - the sample should be runnable without pre-existing tracking ids
    - it ensures the IoT server has a scenario for map rendering

    How to use it:
    - place before load_tracking when the user asks for a demo parcel
    """

    context.emit_status("seed_demo", "Seeding demo parcel data.")
    raw = await context.invoke_runtime_tool(
        "seed_demo_parcel_exception_for_current_user",
        {},
    )
    result = raw if isinstance(raw, dict) else {}
    tracking_id = str(result.get("tracking_id") or "")

    if tracking_id:
        await context.invoke_runtime_tool(
            "seed_demo_tracking_incident",
            {"tracking_id": tracking_id, "scenario": "hub_congestion_delay"},
        )

    if not tracking_id:
        return StepResult(
            state_update={
                "final_text": "Unable to seed a demo parcel right now.",
                "done_reason": "seed_failed",
            },
            route_key="error",
        )

    return StepResult(
        state_update={
            "tracking_id": tracking_id,
            # final_text here is transient — load_tracking_step will overwrite it
            "final_text": f"Seeded demo parcel {tracking_id}. Fetching the latest status.",
        },
        route_key="ok",
    )


@typed_node(PostalTrackingState)
async def load_tracking_step(
    state: PostalTrackingState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Load business tracking data and optional IoT/map context, then summarize.

    Why this exists:
    - postal and IoT MCP calls provide the data needed for summaries and maps
    - it centralizes map preparation and pickup point lookup for the sample
    - it recovers a tracking id from active parcels when one is truly not available
      (first turn, no prior session context)

    How to use it:
    - route here after analyze_intent or seed_demo
    - branch to confirm_reroute when wants_reroute is true

    Design note on tracking id recovery:
    - thanks to build_turn_state, tracking_id is carried across turns so this
      recovery path is only hit on the very first message without an id
    - when multiple parcels exist, the agent asks the user to disambiguate
    """

    context.emit_status("track", "Loading parcel status.")

    tracking_id = state.tracking_id
    if not tracking_id:
        raw_list = await context.invoke_runtime_tool(
            "list_my_active_parcels",
            {"include_terminal": False, "limit": 5},
        )
        parcels_result = raw_list if isinstance(raw_list, dict) else {}
        parcels = parcels_result.get("parcels", []) if parcels_result.get("ok") else []
        if len(parcels) == 1 and isinstance(parcels[0], dict):
            tracking_id = str(parcels[0].get("tracking_id") or "")
        elif parcels:
            parcel_lines = [
                f"- {p.get('tracking_id', 'unknown')} ({p.get('status', 'unknown')})"
                for p in parcels[:3]
                if isinstance(p, dict)
            ]
            return StepResult(
                state_update={
                    "final_text": (
                        "I found several active parcels. Which one would you like "
                        "to track?\n" + "\n".join(parcel_lines)
                    ),
                    "done_reason": "multiple_active_parcels",
                },
                route_key="error",
            )
        else:
            return StepResult(
                state_update={
                    "final_text": (
                        "Please provide a tracking id (example: PKG-ABC123) or ask "
                        "to seed a demo parcel."
                    ),
                    "done_reason": "missing_tracking_id",
                },
                route_key="error",
            )

    raw = await context.invoke_runtime_tool(
        "track_package", {"tracking_id": tracking_id}
    )
    business_track = raw if isinstance(raw, dict) else {}
    if not business_track.get("ok"):
        error = business_track.get("error", "unknown error")
        return StepResult(
            state_update={
                "final_text": f"Could not load parcel {tracking_id}: {error}",
                "done_reason": "track_failed",
            },
            route_key="error",
        )

    wants_map = bool(state.wants_map)
    wants_pickup_points = bool(state.wants_pickup_points)
    wants_reroute = bool(state.wants_reroute)

    pickup_points: list[dict[str, Any]] = []
    delivery = (
        business_track.get("delivery") if isinstance(business_track, dict) else None
    )
    address = delivery.get("address") if isinstance(delivery, dict) else None
    if wants_pickup_points or wants_reroute or wants_map:
        city = (
            str(address.get("city") or "Paris")
            if isinstance(address, dict)
            else "Paris"
        )
        postal_code = (
            str(address.get("postal_code") or "75015")
            if isinstance(address, dict)
            else "75015"
        )
        raw_pickup = await context.invoke_runtime_tool(
            "get_pickup_points_nearby",
            {"city": city, "postal_code": postal_code, "limit": 3},
        )
        pickup_result = raw_pickup if isinstance(raw_pickup, dict) else {}
        pickup_points = (
            pickup_result.get("pickup_points", []) if pickup_result.get("ok") else []
        )

    route_geometry: dict[str, Any] | None = None
    route_markers: list[dict[str, Any]] = []
    iot_snapshot: dict[str, Any] | None = None
    if wants_map:
        raw_iot = await context.invoke_runtime_tool(
            "get_live_tracking_snapshot", {"tracking_id": tracking_id}
        )
        iot_snapshot = (
            raw_iot if isinstance(raw_iot, dict) and raw_iot.get("ok") else None
        )

        raw_route = await context.invoke_runtime_tool(
            "get_route_geometry", {"tracking_id": tracking_id}
        )
        route_result = raw_route if isinstance(raw_route, dict) else {}
        if route_result.get("ok"):
            route_geometry = route_result.get("route_geometry")
            route_markers = route_result.get("markers") or []

    ui_geojson = _build_tracking_geojson(
        route_geometry=route_geometry,
        route_markers=route_markers,
        pickup_points=pickup_points,
        highlight_pickup_point_id=state.chosen_pickup_point_id,
        business_track=business_track,
    )

    # Build a curated context dict for the model — leaner than raw MCP output.
    context_data = _build_tracking_context(
        tracking_id=tracking_id,
        business_track=business_track,
        iot_snapshot=iot_snapshot,
        pickup_points=pickup_points,
        map_available=ui_geojson is not None,
    )
    fallback = _fallback_tracking_summary(
        tracking_id=tracking_id,
        business_track=business_track,
        pickup_points=pickup_points,
        map_available=ui_geojson is not None,
    )
    summary = await model_text_step(
        context,
        system_prompt=_TRACKING_SUMMARY_SYSTEM_PROMPT,
        user_prompt=(
            f"User question: {state.latest_user_text}\n\n"
            f"Tracking data:\n{json.dumps(context_data, indent=2)}"
        ),
        fallback_text=fallback,
    )

    state_update = {
        "tracking_id": tracking_id,
        "business_track": business_track,
        "iot_snapshot": iot_snapshot,
        "route_geometry": route_geometry,
        "route_markers": route_markers,
        "pickup_points": pickup_points,
        "ui_geojson": ui_geojson,
        "final_text": summary,
    }

    async with context.thinking(
        "reflection", title="Checking reroute eligibility"
    ) as thought:
        await thought.write(
            f"wants_reroute={wants_reroute}, pickup points found={len(pickup_points)}."
        )
        if wants_reroute and pickup_points:
            route_key = "reroute"
            await thought.conclude(
                f"Reroute requested and {len(pickup_points)} pickup point(s) "
                "available — routing to confirm_reroute."
            )
        else:
            route_key = "ok"
            reason = (
                "no pickup points available near the delivery address"
                if wants_reroute
                else "no reroute requested"
            )
            await thought.conclude(
                f"Not rerouting ({reason}) — finalizing with the tracking summary."
            )

    return StepResult(state_update=state_update, route_key=route_key)


@typed_node(PostalTrackingState)
async def confirm_reroute_step(
    state: PostalTrackingState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Ask the user to choose a pickup point for rerouting.

    Why this exists:
    - rerouting should be explicit and user-confirmed
    - choice_step pauses the workflow for a clear human decision

    How to use it:
    - route here only when pickup points are available and reroute is requested
    """

    pickup_points = state.pickup_points or []
    if not pickup_points:
        return StepResult(
            state_update={
                "final_text": "No pickup points available to reroute this parcel.",
                "done_reason": "no_pickup_points",
            },
            route_key="cancelled",
        )

    choices = []
    summary_lines = []
    for point in pickup_points[:3]:
        pp_id = str(point.get("pickup_point_id") or "")
        if not pp_id:
            continue
        label = str(point.get("name") or pp_id)
        city = point.get("city") or ""
        postal_code = point.get("postal_code") or ""
        address = point.get("street") or ""
        slots = point.get("available_slots")
        summary_lines.append(
            f"- {pp_id}: {label}, {address}, {city} {postal_code} (slots: {slots})"
        )
        choices.append(HumanChoiceOption(id=pp_id, label=f"{label} ({pp_id})"))

    choices.append(HumanChoiceOption(id="cancel", label="Cancel reroute"))

    question = "Choose a pickup point for rerouting:\n\n" + "\n".join(summary_lines)

    choice_id = await choice_step(
        context,
        stage="postal_reroute",
        title="Select Pickup Point",
        question=question,
        choices=choices,
    )

    if choice_id == "cancel" or not choice_id:
        return StepResult(
            state_update={
                "final_text": "Reroute cancelled. No changes were made.",
                "done_reason": "reroute_cancelled",
            },
            route_key="cancelled",
        )

    return StepResult(
        state_update={"chosen_pickup_point_id": choice_id},
        route_key="selected",
    )


@typed_node(PostalTrackingState)
async def execute_reroute_step(
    state: PostalTrackingState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Execute the reroute to the chosen pickup point and notify the customer.

    Why this exists:
    - keeps the mutation step explicit and separate from user confirmation

    How to use it:
    - route here after confirm_reroute_step returns "selected"
    """

    tracking_id = state.tracking_id or ""
    pickup_point_id = state.chosen_pickup_point_id or ""
    if not tracking_id or not pickup_point_id:
        return StepResult(
            state_update={
                "final_text": "Missing tracking id or pickup point for reroute.",
                "done_reason": "reroute_missing_data",
            }
        )

    context.emit_status("reroute", "Rerouting parcel to pickup point.")
    raw = await context.invoke_runtime_tool(
        "reroute_package_to_pickup_point",
        {
            "tracking_id": tracking_id,
            "pickup_point_id": pickup_point_id,
            "reason": "customer_request",
        },
    )
    result = raw if isinstance(raw, dict) else {}
    if not result.get("ok"):
        error = result.get("error", "unknown error")
        return StepResult(
            state_update={
                "final_text": f"Could not reroute parcel {tracking_id}: {error}",
                "done_reason": "reroute_failed",
            }
        )

    await context.invoke_runtime_tool(
        "notify_customer",
        {
            "tracking_id": tracking_id,
            "channel": "sms",
            "message": (
                f"Your parcel {tracking_id} was rerouted to pickup point {pickup_point_id}."
            ),
        },
    )

    ui_geojson = _build_tracking_geojson(
        route_geometry=state.route_geometry,
        route_markers=state.route_markers,
        pickup_points=state.pickup_points,
        highlight_pickup_point_id=pickup_point_id,
        business_track=state.business_track,
    )

    # Find the chosen pickup point name for a more natural confirmation message.
    pickup_point_name = pickup_point_id
    for point in state.pickup_points or []:
        if str(point.get("pickup_point_id") or "") == pickup_point_id:
            pickup_point_name = str(point.get("name") or pickup_point_id)
            break

    reroute_context = {
        "tracking_id": tracking_id,
        "pickup_point_id": pickup_point_id,
        "pickup_point_name": pickup_point_name,
        "customer_notified_via": "SMS",
    }
    fallback = (
        f"Parcel {tracking_id} has been rerouted to {pickup_point_name} ({pickup_point_id}). "
        "The customer has been notified by SMS."
    )
    confirmation = await model_text_step(
        context,
        system_prompt=_REROUTE_SUMMARY_SYSTEM_PROMPT,
        user_prompt=json.dumps(reroute_context, indent=2),
        fallback_text=fallback,
    )

    return StepResult(
        state_update={
            "final_text": confirmation,
            "done_reason": "reroute_completed",
            "ui_geojson": ui_geojson,
        }
    )


@typed_node(PostalTrackingState)
async def finalize_step(
    state: PostalTrackingState,
    context: GraphNodeContext,
) -> GraphNodeResult:
    """
    Terminal step that ensures a response is always returned.

    Why this exists:
    - every branch should end with a consistent terminal node

    How to use it:
    - register as the "finalize" node in the workflow
    """

    return _finalize_step(
        final_text=state.final_text,
        fallback_text="Postal tracking workflow completed.",
        done_reason=state.done_reason,
    )
