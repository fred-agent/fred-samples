# CLI Notes For Team Of 3 Sample

This document only uses CLI commands verified in the installed package versions.

## 1. Actually available CLI commands (installed version)
Verified console entry points for installed Fred packages:
- `fred-agents-cli`

For this starter package, `fred-runtime==0.1.19` exposes `fred-agents-cli` as the runtime chat client.

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
  "Please approve this expense request for 120 EUR."
```
Expected marker: `[ROUTED:GRAPH]`

Test B (react_1):
```bash
.venv/bin/fred-agents-cli \
  --base-url http://127.0.0.1:8010/samples/agents/v1 \
  --agent fred.samples.team_of_3.router \
  "Convert 2.5 km to meters and add 120."
```
Expected marker: `[ROUTED:REACT_1]`

Test C (react_2):
```bash
.venv/bin/fred-agents-cli \
  --base-url http://127.0.0.1:8010/samples/agents/v1 \
  --agent fred.samples.team_of_3.router \
  "Rewrite this sentence in plain English: The rollout was postponed due to environmental contingencies."
```
Expected marker: `[ROUTED:REACT_2]`

## 5. How to identify which child agent handled the request
Each child emits a stable marker:
- graph child: `[ROUTED:GRAPH]`
- react child 1: `[ROUTED:REACT_1]`
- react child 2: `[ROUTED:REACT_2]`

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

## 7. If a “new CLI” is expected
In this starter package's installed `fred-runtime==0.1.19`, the supported runtime console script is `fred-agents-cli`.
If a future runtime release renames the CLI again, verify the available entry points in `.venv/bin/` or the package metadata before updating the starter docs.
