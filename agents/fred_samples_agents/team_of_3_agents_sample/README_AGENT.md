# Team Of 3 Agents Sample

## 1. Purpose
This sample demonstrates the Fred SDK team concept using exactly 3 child agents:
- 1 graph agent (`fred.samples.team_of_3.graph_child`)
- 2 ReAct agents (`fred.samples.team_of_3.react_math`, `fred.samples.team_of_3.react_writer`)

The team entrypoint is:
- `fred.samples.team_of_3.router`

It is implemented with the official SDK primitive `TeamAgent` in `mode="route"`.

## 2. Folder structure
```text
team_of_3_agents_sample/
├── __init__.py
├── graph_agent/
│   ├── __init__.py
│   ├── graph_agent.py
│   ├── graph_state.py
│   └── graph_steps.py
├── react_agent_one/
│   ├── __init__.py
│   └── react_agent.py
├── react_agent_two/
│   ├── __init__.py
│   └── react_agent.py
├── team/
│   ├── __init__.py
│   └── team_agent.py
├── README_AGENT.md
└── README_CLI.md
```

## 3. How the team is wired
- Team class: `TeamOf3RouterSample(TeamAgent)` in `team/team_agent.py`
- Team mode: `mode = "route"`
- Members: 3 `AgentSpec(...)` entries, each with `agent_ref` to a registered child agent id

Routing target mapping:
- `Graph Approval Specialist` -> `fred.samples.team_of_3.graph_child`
- `Math Conversion Specialist` -> `fred.samples.team_of_3.react_math`
- `Writing Specialist` -> `fred.samples.team_of_3.react_writer`

## 4. How to run with existing Makefile flow
From `agents/`:
```bash
make run
```

In another terminal, from `agents/`:
```bash
make chat
```

Inside `fred-agents-cli`, select:
- `/agent fred.samples.team_of_3.router`

## 5. Exactly 3 routing tests

### Test 1 (should route to graph child)
- Prompt:
  `Please approve this expense request for 120 EUR.`
- Expected child:
  `fred.samples.team_of_3.graph_child`
- Expected marker in response:
  `[ROUTED:GRAPH]`

### Test 2 (should route to ReAct agent 1)
- Prompt:
  `Convert 2.5 km to meters and add 120.`
- Expected child:
  `fred.samples.team_of_3.react_math`
- Expected marker in response:
  `[ROUTED:REACT_1]`

### Test 3 (should route to ReAct agent 2)
- Prompt:
  `Rewrite this sentence in plain English: The rollout was postponed due to environmental contingencies.`
- Expected child:
  `fred.samples.team_of_3.react_writer`
- Expected marker in response:
  `[ROUTED:REACT_2]`

## 6. Routing analysis (verified from installed packages)

### Where routing logic is implemented
- Team primitive:
  - `fred_sdk.graph.authoring.team_api.TeamAgent`
- Route mode workflow builder:
  - `fred_sdk.graph.authoring.team_api._build_route_workflow`
- Route coordinator decision node:
  - `fred_sdk.graph.authoring.team_api._make_route_coordinator_step`
- Structured routing call:
  - `fred_sdk.graph.authoring.team_api._make_route_coordinator_step` calls
    `fred_sdk.graph.authoring.api.structured_model_step(..., operation="team_route_coordinator", ...)`
- Agent delegation execution:
  - `fred_sdk.graph.authoring.team_api._make_agent_invoke_step` calls `context.invoke_agent(...)`
  - runtime implementation: `fred_sdk.graph.graph_runtime._GraphNodeExecutionContext.invoke_agent`
  - pod-local invoker: `fred_runtime.app.agent_app.LocalRegistryAgentInvoker`

### Is routing deterministic, LLM-based, or hybrid?
Hybrid.

Why:
- LLM-based selection:
  - `_make_route_coordinator_step` uses `structured_model_step(...)` to ask a model for `next_member`.
- Deterministic guardrails/fallback:
  - `_make_route_coordinator_step` defines fallback to the first member when no model is bound (`fallback_output` path).
  - `_make_route_coordinator_step` also clamps unknown member names back to the first declared member.
  - `_build_route_workflow` topology is fixed and deterministic: `coordinator -> one selected member -> finalize`.

### Conditions that trigger each mode
- LLM selection path:
  - a chat model is bound and returns a valid structured `next_member` value.
- Deterministic fallback path:
  - no bound chat model in context (`structured_model_step` uses `fallback_output`),
  - or coordinator output contains an unknown member name (explicit guard in `_make_route_coordinator_step`).

### Model/config requirements for routing
- `TeamAgent(mode="route")` needs a bound chat model for normal LLM-based dispatch.
- In this sample, model binding comes from the runtime configuration (`config/models_catalog.yaml`) plus environment (`OPENAI_API_KEY` for OpenAI provider).
- Child invocation requires agents to be present in pod registry (`LocalRegistryAgentInvoker` lookup by `agent_id`).

## 7. Optional ambiguous test (edge behavior)
- Prompt:
  `Can you approve this and also summarize it in two bullets?`
- This is intentionally mixed-intent; routing choice may vary by model behavior.

## 8. Prerequisites/config
- `agents/.venv` dependencies installed (`make dev` or `uv sync --extra dev`)
- runtime config present at `agents/config/configuration.yaml`
- valid model credentials if you want LLM routing behavior (typically `OPENAI_API_KEY` in `agents/config/.env`)
