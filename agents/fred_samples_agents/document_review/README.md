# Document review agents

Four registered Fred runtime agents demonstrate one ordered, application-owned
document review:

```text
coordinator → document analyst → risk reviewer → action planner → coordinator summary
                       bearer-authenticated MCP at every stage
                                     ↓
                            Review Board backend
                            /                    \
                    Knowledge Flow           OpenSearch
```

The coordinator is `fred.samples.document_review.coordinator`. The specialists
are `fred.samples.document_review.analyst`, `.risk_reviewer` and `.action_planner`.
They are registered in the same samples runtime pod, not four independent pods.
`GraphNodeContext.invoke_agent` runs each real specialist in order. The current
SDK's sequential `TeamAgent` uses inline model calls; it is not used here.

## Try it

1. Open Review Board for a collaborative team, select a corpus file and create
   a task. Copy the opaque task reference.
2. In Fred chat, select a managed instance of Document Review Coordinator.
3. Send `Review task-<32 lowercase hex characters>` using the exact copied ID.
4. Watch Review Board: each specialist's stored decision, evidence, findings,
   actions and elapsed time appear as the backend persists them in OpenSearch.
5. After interrupted work, send `Resume task-...` in a fresh Fred turn. Completed
   stages are reused. For a new review after a terminal run, send `Restart task-...`.

A filename alone is intentionally insufficient: duplicate names must not select
another corpus document. The child agents accept only the coordinator's small
JSON work reference; direct natural-language chat with a specialist does not
start an independent review.

## Deployment requirements

Register the samples runtime with Fred and enroll the coordinator template for
the team. All four agents declare `mcp-review-board` in `default_mcp_servers`;
each child uses its own defaults, not a copy of the parent's managed tuning.
Grant the app (`app__review-board`, mapped to `app:review-board`), the MCP
server and the required agent/model capabilities. Select the MCP capability on
the coordinator instance. The MCP catalog uses `auth_mode: delegated`: an
interactive turn forwards the person's bearer, a run acting for people sends the
workload's bearer plus the grant, and the app backend URL must be reachable
from this pod.

All four review templates support capability selection and advertise their
MCP dependency. For an existing coordinator instance, reopen its settings,
select the review capability, and save before starting a fresh chat turn.

There is no custom Python capability or shared agent key. Fred privately forwards
the current user's bearer and supplies the current team. The app validates that
bearer and checks app/MCP authorization; Knowledge Flow checks the user's document
access. Both the dashboard and agents read the backend's persisted decisions.
The human UID is authenticated; specialist labels are workflow attribution, not
cryptographically verified agent identities.

## Lifecycle and failures

This is an interactive workflow, not a background scheduler. Each model call is
bounded to 600 seconds and each MCP call to 60 seconds. Fred does not renew the
user's bearer mid-turn; if it expires, a fresh user turn must resume. Completed
decisions survive in the app. Failure reporting is best effort when
authorization or the backend itself is unavailable; an expired run lease allows
explicit resume later.

Serve the reviews from an instruct model that honours the response format, as
the review-board runtime manifest does. For a thinking model, set
`reasoning: false` in the Ollama model profile's `settings`: thinking-mode
grammar initialization can fail in the local provider, and with thinking
disabled schema enforcement may be ignored. The prompt therefore specifies the
JSON fields explicitly, and the runtime still validates every decision against
the unchanged typed model before saving.
Invalid output interrupts the review; it is never treated as a saved decision.

The backend owns stage order, optimistic concurrency, run leases, cancellation,
source revision checks and duplicate-write protection. Agents recheck it between
stages. Browser disconnection does not guarantee immediate cancellation of a model
call; cancellation in the app fences later writes. Source content is untrusted
input, never instructions. No full corpus content or bearer is written into
completed graph checkpoints, and no model failure is replaced by a fabricated
success.

Only concise decisions, evidence and limitations are saved—not private reasoning.
The coordinator verifies the saved stage before invoking the next expert. A
completed flow can still conclude `needs_attention` or `blocked`.

## Offline tests

From `agents/`, run `make test` and `make code-quality`. The document-review tests
use the public workflow handlers with fake runtime/model/MCP services: no model,
OpenSearch or other network dependency is required. Live deployment additionally
needs the app, Knowledge Flow, model provider and Fred security services.
