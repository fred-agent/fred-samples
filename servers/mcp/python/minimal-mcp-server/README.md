# **Minimal MCP Server Example (Python)**

This folder contains a very simple ready-to-use MCP server example, meant to be used by an agent (for example in Fred). It exposes a Streamable HTTP transport at `/mcp` and declares a random tool.

Main entry points:

- Server code: `minimal_mcp_server/server_mcp.py`
- Local launch: `make run`

---

## MCP in 3 Minutes

The Model Context Protocol (MCP) standardizes how “capability servers” expose tools, resources, prompts, etc., to AI agents.

- Protocol: JSON-RPC 2.0 exchanges over a transport (here HTTP + SSE for event streaming).
- Streamable HTTP transport: a single endpoint (default `/mcp`) handles JSON-RPC POST requests and an SSE GET stream for events/notifications.
- Session: the client initializes a session (handshake), then invokes methods (e.g., execute a tool) and receives results/events.
- Tools: typed functions declared by the server that the client can call in a structured way.
- Clients: agents (Fred, LangChain, etc.) that speak MCP and know how to consume these endpoints.

In this example, we use the “FastMCP” API from the official `mcp` SDK, which simplifies writing tools using a `@server.tool()` decorator and directly exposes an ASGI app for Uvicorn.

---

## Provided Example

The example server is in `minimal_mcp_server/server_mcp.py` and does three things:

- Creates an MCP server with `FastMCP` and exposes the app through `app = server.streamable_http_app()` (endpoint `/mcp`).
- Declares a single educational tool:
  - `random_numbers(count, min_value, max_value)`: returns a list of `count` values between `min_value` and `max_value`.

File to inspect: `minimal_mcp_server/server_mcp.py`

---

## Requirements

- Python 3.12+ (a virtual environment will be created by the Makefile)
- `make`
- Port `9799` free on `127.0.0.1`

---

## Installation and Launch

The command below creates a venv, installs dependencies (`mcp==1.28.1`, `uvicorn`, `fastapi`) and starts the server:

```bash
make run
```

Manual equivalent:

```bash
python -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install "mcp==1.28.1" fastapi uvicorn
uvicorn minimal_mcp_server.server_mcp:app --host 127.0.0.1 --port 9799 --reload
```

Exposed endpoint: `http://127.0.0.1:9799/mcp`

Note: this is a “machine endpoint” (MCP). Opening it in a browser will not show a readable page; it is intended for MCP clients.

---

## Using with Fred

- This server is deliberately **absent** from `agents/config/mcp_catalog.yaml`: it is a template, and no sample agent depends on it.
- Typical steps:
  1. Start this server with `make run`.
  2. Add an entry to `agents/config/mcp_catalog.yaml` pointing at `http://localhost:9799/mcp`, modelled on the entries already there, and attach it to an agent.
  3. Chat with the agent: ask it to generate random numbers — `random_numbers` is the only tool this server declares.

Useful agent prompts:

- "Generate 8 numbers between 1 and 2021"

---

## Adding Your Own Tools

To create a new tool:

1. Open `minimal_mcp_server/server_mcp.py`.
2. Add a typed Python function and decorate it with `@server.tool()`.
3. Restart the server.

Example:

```python
@server.tool()
async def hello(name: str) -> dict[str, str]:
    return {"message": f"Hello {name}!"}
```

Tips:

- Type parameters/returns clearly; FastMCP uses that to generate the tool specification.
- Keep side effects (I/O, network) explicit and well-managed inside the tool.

---

## Troubleshooting

- “ImportError: No module named 'mcp.server.fastapi'”

  - Recent versions of the `mcp` SDK no longer export this module. The example uses `FastMCP` and `server.streamable_http_app()` (see `minimal_mcp_server/server_mcp.py`).
  - If you have an old venv, run: `make clean && make run`.

- “Address already in use”
  - Port `9799` is in use. Change it in the Makefile or pass `--port` to Uvicorn.

---

Happy hacking!
