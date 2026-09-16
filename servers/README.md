# Sample MCP servers

Demo dependencies for the sample agents — not production services. Each one is self-contained,
serves deterministic mock data, and is started by hand from its own folder.

All five live under `servers/mcp/python/`:

| Folder | Port | Used by | What it simulates |
|---|---|---|---|
| `bank_core_mcp_server` | 9801 | `bank_transfer` | banking core — account details, prepare and commit transfers |
| `risk_guard_mcp_server` | 9802 | `bank_transfer` | risk engine — KYC compliance check and transfer risk scoring |
| `postal-service-mcp-server` | 9797 | `postal_tracking` | postal core — parcel tracking, pickup points, rerouting |
| `iot-tracking-mcp-server` | 9798 | `postal_tracking` | IoT service — live GPS snapshots, route geometry, hub events |
| `minimal-mcp-server` | 9799 | nothing — it is the template | one tool, `random_numbers` |

Each folder contains its own README and Makefile. To start one:

```bash
cd servers/mcp/python/<folder>
make run
```

The four servers an agent depends on are declared in
[`agents/config/mcp_catalog.yaml`](../agents/config/mcp_catalog.yaml), which is what the agents pod
reads. `minimal-mcp-server` is deliberately absent from it: nothing depends on the template, and a
catalog entry for a server nobody runs would only make the pod fail to connect. Add an entry there
yourself if you want to attach it to an agent.

A sample agent that needs an MCP server **will not work without it running** — start the servers
its row names before `make run` in `agents/`.

## No test suite, on purpose

These servers ship no tests, which is why the repository-wide `make test` and `make code-quality`
skip them (see the repository map in root `CLAUDE.md`). Their correctness is established by running
them and performing a real MCP handshake, not by a suite.

When you change a server's port or folder name, update `agents/config/mcp_catalog.yaml` and this
table in the same commit — a wrong path or port there is invisible until someone tries to start it.
