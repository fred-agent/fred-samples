# CLI Notes For Team Of 3 Sample

Run these commands from `agents/` after completing the root quick start.
The pod and CLI run in separate terminals. Model routing remains model-dependent.

## 1. Actually available CLI commands (installed version)
Verified console entry points for installed Fred packages:
- `fred-agents-cli`

The package declares `fred-runtime>=4.0.0`; `fred-agents-cli` is its chat client.
Check `.venv/bin/fred-agents-cli --help` for the installed version’s options.

## 2. How to access CLI help
From `agents/`:
```bash
.venv/bin/fred-agents-cli --help
```

## 3. How to run the sample using supported commands
Start pod (existing flow):
```bash
make run
```

Use CLI (existing flow):
```bash
make cli
```

Or direct one-shot:
```bash
.venv/bin/fred-agents-cli \
  --base-url http://127.0.0.1:8010/samples/agents/v1 \
  --agent fred.samples.team_of_3.router \
  "Convert 7 km to meters."
```

## 4. Run the 3 routing tests from CLI path

Test A (graph):
```bash
.venv/bin/fred-agents-cli \
  --base-url http://127.0.0.1:8010/samples/agents/v1 \
  --agent fred.samples.team_of_3.router \
  --verbose --stream \
  "Please approve this expense request for 120 EUR."
```
Expected thought conclusion: `Routing to Graph Approval Specialist.`

Test B (react_1):
```bash
.venv/bin/fred-agents-cli \
  --base-url http://127.0.0.1:8010/samples/agents/v1 \
  --agent fred.samples.team_of_3.router \
  --verbose --stream \
  "Convert 2.5 km to meters and add 120."
```
Expected thought conclusion: `Routing to Math Conversion Specialist.`

Test C (react_2):
```bash
.venv/bin/fred-agents-cli \
  --base-url http://127.0.0.1:8010/samples/agents/v1 \
  --agent fred.samples.team_of_3.router \
  --verbose --stream \
  "Rewrite this sentence in plain English: The rollout was postponed due to environmental contingencies."
```
Expected thought conclusion: `Routing to Writing Specialist.`

## 5. How to identify which child agent handled the request
The coordinator's own routing decision is a `thought`-channel event
(`phase="planning"`, `title="Choosing a specialist"`) with a `conclusion`
field naming the chosen member — visible in the chat UI's "Thought…" panel
and, from the CLI, in `--verbose --stream` output. Child replies carry no
routing marker. Inspect the events rather than expecting that marker in the final answer.

## 6. Capturing routing evidence/logs
Use chat client verbose/stream flags:
```bash
.venv/bin/fred-agents-cli \
  --base-url http://127.0.0.1:8010/samples/agents/v1 \
  --agent fred.samples.team_of_3.router \
  --verbose --stream \
  "Convert 2.5 km to meters and add 120."
```

`--verbose` prints intermediate runtime events; `--stream` renders SSE events live.
