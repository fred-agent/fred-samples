"""
minimal_mcp_server/server_mcp.py
---------------------------------
Minimal MCP server using the current `mcp` Python SDK.

This exposes the Streamable HTTP transport at `/mcp` and is compatible with
modern MCP clients.

Run:
  uvicorn minimal_mcp_server.server_mcp:app --host 127.0.0.1 --port 9799 --reload
  or: make run

Tools implemented:
  - random_numbers(count, min_value, max_value)
"""

from __future__ import annotations

from typing import Dict, Any
import random

try:
    # Use FastMCP (ergonomic server with @tool decorator) and build a Starlette app
    from mcp.server import FastMCP
except Exception as e:  # pragma: no cover - helpful error at import time
    raise ImportError(
        "The 'mcp' package is required for minimal_mcp_server.server_mcp.\n"
        "Install it via: pip install \"mcp==1.28.1\"\n"
        f"Import error: {e}"
    )


# Create a FastMCP server (provides @tool and compatible transports)
server = FastMCP(name="minimal-mcp")


@server.tool()
async def random_numbers(
    count: int,
    min_value: int = 0,
    max_value: int = 100
) -> Dict[str, Any]:
    """
    Generate a list of random integers.

    Parameters:
      - count: number of random numbers to generate
      - min_value: minimum integer value (inclusive)
      - max_value: maximum integer value (inclusive)

    Returns:
      { "numbers": [ ... ] }
    """
    if count <= 0:
        return {"error": "count must be > 0"}

    if min_value > max_value:
        return {"error": "min_value must be <= max_value"}

    nums = [random.randint(min_value, max_value) for _ in range(count)]
    return {"numbers": nums}



# Expose the Streamable HTTP transport under /mcp
# This returns a Starlette app that uvicorn can serve directly
app = server.streamable_http_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("minimal_mcp_server.server_mcp:app", host="127.0.0.1", port=9799, reload=False)
