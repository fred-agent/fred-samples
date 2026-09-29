"""Ordered runtime delegation and deterministic MCP persistence.

The application owns run leases, source authorization and transitions. Graph
state is only this turn's scratch space; every specialist reads the saved prior
decisions again before reasoning. No agent talks to OpenSearch directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fred_sdk import (
    GraphNodeContext,
    GraphNodeResult,
    StepResult,
    finalize_step,
    structured_model_step,
    typed_node,
)
from fred_sdk.graph.authoring.api import GraphStepHandler

from .graph_state import (
    AGENT_IDS,
    ReviewContext,
    ReviewDecision,
    ReviewRun,
    ReviewState,
    StageId,
    StageReceipt,
    WorkItem,
)
from .prompts import COMMON_INSTRUCTIONS, ROLE_INSTRUCTIONS

MODEL_TIMEOUT_SECONDS = 600
TOOL_TIMEOUT_SECONDS = 60
# Comfortably inside the application's lease, so a slow model call is
# never mistaken for an abandoned run.
HEARTBEAT_SECONDS = 30
logger = logging.getLogger(__name__)
_TASK_REFERENCE = re.compile(r"(?<![A-Za-z0-9_-])task-[0-9a-f]{32}(?![A-Za-z0-9_-])")
_RESUME_COMMAND = re.compile(r"^\s*(?:please\s+)?(resume|restart)\b", re.IGNORECASE)
_GUIDANCE = (
    "Choose a corpus document and create a task in Review Board, then copy its "
    "task reference here: Review task-<32 lowercase hex characters>. Use exactly "
    "one task reference. To continue interrupted work say Resume task-..., or "
    "say Restart task-... to create a new review after the previous run ends."
)
_FAILED = (
    "The document review stopped before completion. Check the task in Review "
    "Board. If it was interrupted, start a fresh Fred chat turn with Resume "
    "and its task reference; completed decisions remain saved. An expired "
    "bearer, revoked source access, cancellation, or a failed dependency must "
    "be resolved first. No later specialist has been started."
)

_STAGE_LABELS: dict[str, str] = {
    "analyst": "analyst",
    "risk_reviewer": "risk reviewer",
    "action_planner": "action planner",
    "summary": "summary",
}


def _run_progress(run: ReviewRun) -> str:
    """One line naming what each specialist has done so far."""
    done = [s for s in run.stages if s.status == "completed"]
    current = next((s for s in run.stages if s.status == "running"), None)
    parts = [f"{len(done)} of {len(run.stages)} stages completed"]
    if current is not None:
        parts.append(
            f"{_STAGE_LABELS.get(current.stage_id, current.stage_id)} in progress"
        )
    if done:
        parts.append(
            "saved: "
            + ", ".join(_STAGE_LABELS.get(s.stage_id, s.stage_id) for s in done)
        )
    return "; ".join(parts)


def _already_running(task_id: str, run: ReviewRun) -> str:
    """Explain an existing claim instead of letting the application reject it.

    The application refuses a second concurrent claim, which is correct but
    surfaces as a bare conflict. The caller has the run in hand here, so say
    what is actually happening and which command resolves it.
    """
    return (
        f"This task already has a review in progress ({_run_progress(run)}). "
        f"A run is only left open like this when a previous attempt was "
        f"interrupted. Say Resume task-{task_id} to continue it, keeping every "
        f"decision already saved, or Restart task-{task_id} to discard the "
        f"unfinished run and review the document from the beginning."
    )


async def _tool(
    context: GraphNodeContext, operation: str, **payload: object
) -> dict[str, Any]:
    team_id = context.binding.portable_context.team_id
    if not team_id:
        raise ValueError("Document review requires a collaborative team.")
    async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
        raw = await context.invoke_runtime_tool(
            operation, {"team_id": team_id, **payload}
        )
    if not isinstance(raw, dict) or raw.get("is_error") or raw.get("isError"):
        raise ValueError("The application operation did not return a valid result.")
    return raw


def _identity(state: ReviewState) -> dict[str, object]:
    work = WorkItem(task_id=state.task_id, run_id=state.run_id, claim_id=state.claim_id)
    return work.model_dump()


def _matching_run(state: ReviewState, raw: object) -> ReviewRun:
    run = ReviewRun.model_validate(raw)
    if run.run_id != state.run_id or run.claim_id != state.claim_id:
        raise ValueError("The application returned a different or superseded run.")
    return run


async def _read_context(state: ReviewState, context: GraphNodeContext) -> ReviewContext:
    raw = await _tool(context, "read_document_review_context", **_identity(state))
    data = ReviewContext.model_validate(raw)
    _matching_run(state, data.run)
    if data.task_id != state.task_id or data.source_hash != data.run.source_hash:
        raise ValueError("The document review context does not match this task.")
    if data.run.status not in {"running", "completed"}:
        raise ValueError("The document review is no longer running.")
    return data


def _receipt(state: ReviewState, stage_id: StageId, *, failed: bool = False) -> str:
    return StageReceipt(
        task_id=state.task_id,
        run_id=state.run_id,
        stage_id=stage_id,
        status="failed" if failed else "completed",
    ).model_dump_json()


async def _report_interruption(state: ReviewState, context: GraphNodeContext) -> None:
    """Hand the claim back so the review is resumable at once.

    Never persist an exception string: upstream responses can carry source
    excerpts or credentials. When this cannot be delivered the lease still
    frees the run, just later.
    """

    if not (state.task_id and state.run_id and state.claim_id):
        return
    try:
        await _tool(
            context,
            "interrupt_document_review",
            **_identity(state),
            error="Review interrupted; check authorization and dependencies before resuming.",
        )
    except Exception:  # noqa: BLE001 — arbitrary transport errors, no source/secret logging
        logger.warning("Could not persist review interruption; run lease will expire.")


async def _beat_while_working(state: ReviewState, context: GraphNodeContext) -> None:
    """Say the holder is alive for as long as a silent step runs."""

    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            await _tool(context, "heartbeat_document_review", **_identity(state))
        except Exception:  # noqa: BLE001 — a missed beat only shortens the lease
            logger.warning("Review heartbeat failed; the run lease may expire.")
            return


async def _failure(
    state: ReviewState, context: GraphNodeContext, stage_id: StageId | None
) -> StepResult:
    # Never persist an exception string: upstream responses can contain source
    # excerpts or credentials. A revoked/expired bearer may prevent this best
    # effort update; the app's lease then exposes the stale run for resume.
    await _report_interruption(state, context)
    return StepResult(
        state_update={
            "final_text": _receipt(state, stage_id, failed=True)
            if stage_id is not None
            else _FAILED,
            "review_context": None,
            "result": None,
        },
        route_key="done",
    )


def guarded(
    step: Callable[[ReviewState, GraphNodeContext], Awaitable[StepResult]],
    *,
    stage_id: StageId | None = None,
) -> GraphStepHandler:
    """Fail visibly without a runtime node-error abort skipping our app update."""

    @typed_node(ReviewState)
    async def safe_step(state: ReviewState, context: GraphNodeContext) -> StepResult:
        try:
            return await step(state, context)
        except asyncio.CancelledError:
            # A pod going away would otherwise hold the review for the whole
            # lease. Shielded so the report survives the cancellation itself.
            await asyncio.shield(_report_interruption(state, context))
            raise
        except Exception:  # noqa: BLE001 — sanitize model/transport failures at this boundary
            return await _failure(state, context, stage_id)

    return safe_step


async def prepare_review(state: ReviewState, context: GraphNodeContext) -> StepResult:
    references = set(_TASK_REFERENCE.findall(state.latest_user_text))
    if len(references) != 1:
        return StepResult(state_update={"final_text": _GUIDANCE}, route_key="done")
    task_id = references.pop()
    command = _RESUME_COMMAND.match(state.latest_user_text)
    mode = command.group(1).lower() if command else "start"
    task = await _tool(context, "read_task_progress", task_id=task_id)
    if task.get("task_id") != task_id:
        raise ValueError("The application returned a different task.")
    review = task.get("review")
    if not isinstance(review, dict):
        return StepResult(
            state_update={
                "final_text": "This task has no corpus document. " + _GUIDANCE
            },
            route_key="done",
        )
    runs = review.get("runs")
    resumed: ReviewRun | None = None
    if mode == "start" and isinstance(runs, list) and runs:
        latest = ReviewRun.model_validate(runs[-1])
        if latest.status == "running":
            return StepResult(
                state_update={"final_text": _already_running(task_id, latest)},
                route_key="done",
            )
        # A read projects an expired lease to `interrupted`, so this run is
        # already abandoned and continuing it needs no choice from the person.
        if latest.status == "interrupted":
            mode, resumed = "resume", latest
    payload: dict[str, object] = {
        "task_id": task_id,
        "claim_id": str(uuid4()),
        "mode": mode,
    }
    if mode in {"resume", "restart"}:
        if not isinstance(runs, list) or not runs:
            raise ValueError("There is no previous run to resume or restart.")
        payload["run_id"] = ReviewRun.model_validate(runs[-1]).run_id
    context.emit_status(
        "review",
        f"Resuming the interrupted review ({_run_progress(resumed)})."
        if resumed is not None
        else "Opening the selected document review.",
    )
    claimed = await _tool(context, "claim_document_review", **payload)
    run = ReviewRun.model_validate(claimed["review"]["runs"][-1])
    if claimed.get("task_id") != task_id or run.claim_id != payload["claim_id"]:
        raise ValueError("The application did not claim the requested task.")
    return StepResult(
        state_update={
            "task_id": task_id,
            "run_id": run.run_id,
            "claim_id": run.claim_id,
        },
        route_key="next",
    )


def delegate(stage_id: StageId) -> GraphStepHandler:
    async def step(state: ReviewState, context: GraphNodeContext) -> StepResult:
        data = await _read_context(state, context)
        saved = data.run.stage(stage_id)
        if saved.status == "completed" and saved.result is not None:
            context.emit_status(stage_id, "Reusing the completed, saved decision.")
            return StepResult(route_key="next")
        context.emit_status(stage_id, "Asking the specialist to review the document.")
        result = await context.invoke_agent(
            agent_id=AGENT_IDS[stage_id],
            message=json.dumps(_identity(state)),
        )
        # Do not use invoke_agent(output_schema=...) here: its structured-output
        # retry would rerun a child that may already have saved a decision.
        receipt = StageReceipt.model_validate_json(result.content)
        if result.is_error or (
            receipt.task_id,
            receipt.run_id,
            receipt.stage_id,
            receipt.status,
        ) != (state.task_id, state.run_id, stage_id, "completed"):
            raise ValueError("The specialist did not complete this stage.")
        verified = (await _read_context(state, context)).run.stage(stage_id)
        if verified.status != "completed" or verified.result is None:
            raise ValueError("The specialist decision was not saved.")
        return StepResult(route_key="next")

    return guarded(step)


def parse_work(stage_id: StageId) -> GraphStepHandler:
    async def step(state: ReviewState, context: GraphNodeContext) -> StepResult:
        del context
        work = WorkItem.model_validate_json(state.latest_user_text)
        return StepResult(state_update=work.model_dump(), route_key="next")

    return guarded(step, stage_id=stage_id)


def load_stage(stage_id: StageId, *, coordinator: bool = False) -> GraphStepHandler:
    async def step(state: ReviewState, context: GraphNodeContext) -> StepResult:
        data = await _read_context(state, context)
        saved = data.run.stage(stage_id)
        if saved.status == "completed" and saved.result is not None:
            return StepResult(
                state_update={
                    "result": saved.result,
                    "final_text": _summary_text(state, saved.result)
                    if coordinator
                    else _receipt(state, stage_id),
                },
                route_key="done",
            )
        started = await _tool(
            context,
            "start_document_review_stage",
            **_identity(state),
            stage_id=stage_id,
        )
        run = _matching_run(state, started)
        if run.stage(stage_id).status != "running":
            raise ValueError("The application did not start the specialist stage.")
        context.emit_status(stage_id, "Reviewing the document and saved decisions.")
        return StepResult(state_update={"review_context": data}, route_key="next")

    return guarded(step, stage_id=None if coordinator else stage_id)


def decide_stage(stage_id: StageId, *, coordinator: bool = False) -> GraphStepHandler:
    async def step(state: ReviewState, context: GraphNodeContext) -> StepResult:
        data = state.review_context
        if data is None:
            raise ValueError("The document context was not loaded.")
        previous = [
            {"stage_id": stage.stage_id, "result": stage.result.model_dump()}
            for stage in data.run.stages
            if stage.status == "completed" and stage.result is not None
        ]
        prompt = json.dumps(
            {
                "task_title": data.title,
                "document_name": data.document.document_name,
                "document_content": data.content,
                "saved_previous_decisions": previous,
            },
            ensure_ascii=False,
        )
        # A model call reports nothing until it returns, so without this the
        # lease would have to outlast the slowest one.
        beat = asyncio.create_task(_beat_while_working(state, context))
        try:
            async with asyncio.timeout(MODEL_TIMEOUT_SECONDS):
                decision = await structured_model_step(
                    context,
                    output_model=ReviewDecision,
                    system_prompt=COMMON_INSTRUCTIONS
                    + "\n"
                    + ROLE_INSTRUCTIONS[stage_id],
                    user_prompt=prompt,
                )
        finally:
            beat.cancel()
        if stage_id == "summary" and decision.outcome is None:
            raise ValueError("The coordinator must give an overall outcome.")
        return StepResult(state_update={"result": decision}, route_key="next")

    return guarded(step, stage_id=None if coordinator else stage_id)


def _summary_text(state: ReviewState, decision: ReviewDecision) -> str:
    lines = [
        f"Review saved for {state.task_id}.",
        f"Outcome: {(decision.outcome or 'not_recorded').replace('_', ' ')}.",
        decision.decision,
        decision.rationale,
    ]
    if decision.actions:
        lines.append("Recommended next actions:")
        lines.extend(f"- {action.title}" for action in decision.actions)
    if decision.limitations:
        lines.append("Limitations: " + "; ".join(decision.limitations))
    lines.append("Open Review Board for each saved specialist decision and statistics.")
    return "\n\n".join(lines)


def save_stage(stage_id: StageId, *, coordinator: bool = False) -> GraphStepHandler:
    async def step(state: ReviewState, context: GraphNodeContext) -> StepResult:
        if state.result is None:
            raise ValueError("The specialist produced no validated decision.")
        raw = await _tool(
            context,
            "save_document_review_stage",
            **_identity(state),
            stage_id=stage_id,
            result=state.result.model_dump(mode="json"),
        )
        run = _matching_run(state, raw)
        saved = run.stage(stage_id)
        if saved.status != "completed" or saved.result != state.result:
            raise ValueError("The application did not persist this exact decision.")
        if coordinator and run.status != "completed":
            raise ValueError("The application did not complete the review run.")
        return StepResult(
            state_update={
                "review_context": None,
                "final_text": _summary_text(state, state.result)
                if coordinator
                else _receipt(state, stage_id),
            },
            route_key="done",
        )

    return guarded(step, stage_id=None if coordinator else stage_id)


@typed_node(ReviewState)
async def finish(state: ReviewState, context: GraphNodeContext) -> GraphNodeResult:
    del context
    return finalize_step(final_text=state.final_text, fallback_text=_FAILED)
