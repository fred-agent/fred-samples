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
Business steps for the Hello Graph sample agent.

Read this file to understand the smallest useful v2 graph agent:
- classify_step: one structured model call decides which branch to take
  (intent_router_step)
- greet_step / answer_step: one more model call per branch produces the
  user-facing text (model_text_step)
- finalize_step: the standard terminal node

No MCP server and no human-in-the-loop gate — see bank_transfer or
postal_tracking for those, once this shape is familiar.
"""

from __future__ import annotations

from typing import Literal

from fred_sdk import (
    GraphNodeContext,
    GraphNodeResult,
    StepResult,
    intent_router_step,
    model_text_step,
    typed_node,
)
from fred_sdk import (
    finalize_step as _finalize_step,
)
from pydantic import BaseModel, Field

from .graph_state import HelloGraphState

# ── System prompts ─────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM_PROMPT = """\
Classify the user message as one of:
- "greeting": hellos, small talk, or asking what this agent can do
- "question": anything else the user wants an answer to
"""

_GREET_SYSTEM_PROMPT = """\
You are a friendly assistant saying hello for the first time.
Reply in one short, warm sentence and mention that you can answer questions.
"""

_ANSWER_SYSTEM_PROMPT = """\
You are a helpful, concise assistant.
Answer the user's question clearly in a few sentences.
"""


# ── Route model ───────────────────────────────────────────────────────────────


class MessageKind(BaseModel):
    """Structured classification produced by classify_step."""

    kind: Literal["greeting", "question"] = Field(
        description="'greeting' for hellos/small talk, 'question' for everything else."
    )


# ── Step: classify ─────────────────────────────────────────────────────────────


@typed_node(HelloGraphState)
async def classify_step(
    state: HelloGraphState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Classify the user message as a greeting or a question.

    Why this exists:
    - shows the smallest routing pattern in a graph agent: one structured model
      call decides which node runs next
    - wraps the decision in a `context.thinking()` block so the routing choice
      is visible in the frontend's "Thought" trace panel — `emit_status` alone
      is a fire-and-forget signal that is never persisted or shown in the UI

    How to use:
    - place as the entry node; declare "greeting" and "question" routes after it
    """
    context.emit_status("classify", "Reading your message.")
    async with context.thinking("planning", title="Classifying the message") as thought:
        await thought.write(f"User said: {state.latest_user_text!r}")
        result = await intent_router_step(
            context,
            route_model=MessageKind,
            system_prompt=_CLASSIFY_SYSTEM_PROMPT,
            user_prompt=state.latest_user_text,
            fallback_output={"kind": "question"},
            route_field="kind",
            state_update_builder=lambda d: {"kind": d.kind},
        )
        next_step = "greet" if result.route_key == "greeting" else "answer"
        await thought.conclude(
            f"Classified as '{result.route_key}' — routing to {next_step}."
        )
    return result


# ── Step: greet ─────────────────────────────────────────────────────────────────


@typed_node(HelloGraphState)
async def greet_step(
    state: HelloGraphState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Answer a greeting with one short, friendly line.

    How to use:
    - place on the "greeting" branch with a direct edge to "finalize"
    """
    context.emit_status("greet", "Saying hello.")
    response = await model_text_step(
        context,
        system_prompt=_GREET_SYSTEM_PROMPT,
        user_prompt=state.latest_user_text,
        fallback_text="Hello! Ask me anything.",
    )
    return StepResult(state_update={"final_text": response, "done_reason": "greeted"})


# ── Step: answer ─────────────────────────────────────────────────────────────────


@typed_node(HelloGraphState)
async def answer_step(
    state: HelloGraphState,
    context: GraphNodeContext,
) -> StepResult:
    """
    Answer the user's question with one model call.

    How to use:
    - place on the "question" branch with a direct edge to "finalize"
    """
    context.emit_status("answer", "Thinking.")
    response = await model_text_step(
        context,
        system_prompt=_ANSWER_SYSTEM_PROMPT,
        user_prompt=state.latest_user_text,
        fallback_text="I couldn't come up with an answer just now — try rephrasing?",
    )
    return StepResult(state_update={"final_text": response, "done_reason": "answered"})


# ── Step: finalize ────────────────────────────────────────────────────────────


@typed_node(HelloGraphState)
async def finalize_step(
    state: HelloGraphState,
    context: GraphNodeContext,
) -> GraphNodeResult:
    """
    Terminal step — keep existing final_text or set a generic fallback.

    How to use:
    - register as the "finalize" node; both branches route here
    """
    return _finalize_step(
        final_text=state.final_text
        or (
            f"An unexpected error occurred: {state.node_error}"
            if state.node_error
            else None
        ),
        fallback_text="Hi! Say hello or ask me a question.",
        done_reason=state.done_reason
        or ("infrastructure_error" if state.node_error else None),
    )
