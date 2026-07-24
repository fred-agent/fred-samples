from fred_sdk import GraphNodeContext, GraphNodeResult, StepResult, finalize_step, typed_node

from .graph_state import Team3GraphState


def _normalize(text: str) -> str:
    return text.lower().strip()


@typed_node(Team3GraphState)
async def classify_request_step(
    state: Team3GraphState,
    context: GraphNodeContext,
) -> StepResult:
    context.emit_status("classify", detail="deterministic keyword classifier")
    text = _normalize(state.latest_user_text)

    async with context.thinking("planning", title="Classifying the request") as thought:
        await thought.write(f"Message: {state.latest_user_text!r}")

        if any(token in text for token in ("approve", "approved", "allow", "accept")):
            await thought.conclude("Approval keyword found — decision: APPROVED.")
            return StepResult(
                state_update={
                    "decision": "approved",
                    "reason": "approval keyword found",
                },
                route_key="approved",
            )

        if any(token in text for token in ("reject", "rejected", "deny", "decline")):
            await thought.conclude("Rejection keyword found — decision: REJECTED.")
            return StepResult(
                state_update={
                    "decision": "rejected",
                    "reason": "rejection keyword found",
                },
                route_key="rejected",
            )

        await thought.conclude("No approval/rejection keyword — decision: NEEDS_REVIEW.")
        return StepResult(
            state_update={
                "decision": "needs_review",
                "reason": "no approval/rejection keyword found",
            },
            route_key="needs_review",
        )


@typed_node(Team3GraphState)
async def approved_step(state: Team3GraphState, context: GraphNodeContext) -> StepResult:
    context.emit_status("approved")
    return StepResult(
        state_update={
            "final_text": (
                f"Decision: APPROVED.\nReason: {state.reason}.\nThis path is deterministic."
            )
        }
    )


@typed_node(Team3GraphState)
async def rejected_step(state: Team3GraphState, context: GraphNodeContext) -> StepResult:
    context.emit_status("rejected")
    return StepResult(
        state_update={
            "final_text": (
                f"Decision: REJECTED.\nReason: {state.reason}.\nThis path is deterministic."
            )
        }
    )


@typed_node(Team3GraphState)
async def needs_review_step(
    state: Team3GraphState, context: GraphNodeContext
) -> StepResult:
    context.emit_status("needs_review")
    return StepResult(
        state_update={
            "final_text": (
                f"Decision: NEEDS_REVIEW.\n"
                f"Reason: {state.reason}.\n"
                "Include an explicit approve/reject keyword to choose a branch."
            )
        }
    )


@typed_node(Team3GraphState)
async def finalize_graph_step(
    state: Team3GraphState, context: GraphNodeContext
) -> GraphNodeResult:
    del context
    return finalize_step(
        final_text=state.final_text,
        fallback_text="No result available.",
    )
