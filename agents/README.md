# Sample agents — developer guide

Start with the [root quick start](../README.md#quick-start--one-assistant-no-platform-required)
for prerequisites, provider configuration and the two terminals (`make run`, `make cli`).
All commands below run from `agents/` after `make dev`, with the pod already running
where HTTP is involved. The published-package path also needs `UV_NO_SOURCES=1`
on subsequent Make commands; see the quick start.

## Read the code in this order

| File or folder | What to learn |
|---|---|
| [general_assistant.py](fred_samples_agents/general_assistant.py) | An agent definition and its instructions, without tools. |
| [registry.py](fred_samples_agents/registry.py) | Register instances under their actual `agent_id`. |
| [main.py](fred_samples_agents/main.py) | `create_agent_app` exposes the registry through the runtime. |
| [hello_graph/](fred_samples_agents/hello_graph/) | Pydantic state, typed async steps and explicit graph transitions. |
| [bank_transfer/](fred_samples_agents/bank_transfer/) | MCP operations and human confirmation before a simulated transfer. |
| [team_of_3_agents_sample/](fred_samples_agents/team_of_3_agents_sample/README_AGENT.md) | Route to one registered specialist. |
| [document_review/](fred_samples_agents/document_review/README.md) | A staged workflow using the Review Board application. |

To add an agent, create its definition in this package, instantiate it and add it
to `build_registry()` in `registry.py`. Update `tests/test_registry.py` for its
expected ID and type. Agent logic uses `fred-sdk`; application startup uses
`fred-runtime`. Application services are reached through MCP, not imports from
`apps/` or per-application Python capability packages.

## Call the local HTTP endpoint

This request targets the security-off development profile, not a deployed team instance:

```bash
curl -N http://127.0.0.1:8010/samples/agents/v1/agents/execute/stream \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "fred.samples.assistant",
    "input": "Explain what an API is.",
    "session_id": "developer-demo",
    "runtime_context": {"user_id": "developer"}
  }'
```

The endpoint returns SSE events. `-N` disables curl buffering. Reuse `session_id`
for another turn in the same conversation; use a new ID for an independent one.
`GET /agents` under the same base URL lists registered agents. Stop with Ctrl-C.

## Use the agent in Fred’s UI

The local CLI uses `agent_id`, the definition registered by the pod. A managed UI
conversation uses `agent_instance_id`, created when an authorized user enrolls a
template for a team. Registering Python code alone does not create that instance.

For that path, use the deployment’s security profile and expose this runtime in
its catalog and gateway. The checked-in `configuration_prod.yaml` illustrates a
security-on setup, but its URLs and identities must match your deployment. The
runtime checks the caller’s team access and resolves the instance’s definition
and tuning. Start with the standalone assistant before debugging this integration.

## Models and configuration

`config/.env` selects the YAML profile through `CONFIG_FILE` and supplies the
model credential. `config/models_catalog.yaml` selects chat and language model
profiles. Set both when changing provider: graph steps can use structured language
calls as well as chat. Never assume `OPENAI_API_KEY` implies an OpenAI endpoint;
check each profile’s `settings.base_url`.

To use OpenAI instead, keep the `openai` provider adapter, set the `chat` and
`language` profiles to model names available to your OpenAI account and set their
`settings.base_url` to `https://api.openai.com/v1`. Replace `OPENAI_API_KEY` with
your OpenAI key and restart the pod. Update both profiles rather than only the
chat profile, so graph classification and structured calls use the same provider.

MCP-backed agents declare server IDs, resolved through `config/mcp_catalog.yaml`.
The four mock servers are documented in [servers/README.md](../servers/README.md).
Document Review additionally needs the deployed Review Board application, its
MCP catalog entry and team grants; see its own guide.

## Verify your changes

```bash
make code-quality
make test
```

These are offline checks once dependencies are installed. They do not prove a
live model, MCP connection or UI deployment works. For a real smoke test, send a
message to the assistant, try both Hello Graph branches, then test the particular
external service your agent uses.
