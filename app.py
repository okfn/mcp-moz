"""A minimal Starlette app acting as an MCP client for the local MCP server.

The server (server.py) already runs as streamable-http at http://localhost:8000/mcp.
This app is a thin backend: it exposes a small JSON API over the MCP client and
serves index.html (the static front end, no CSS). The front end renders MCP Apps
UI resources: a tool carrying ``_meta.ui.resourceUri`` (the
``io.modelcontextprotocol/ui`` extension) gets its ``ui://`` HTML resource loaded
into a sandboxed iframe, and the tool result is delivered to it via ``postMessage``
as a ``notifications/call_tool_result`` JSON-RPC notification (the payload shape
the resource expects: ``event.data.params.structuredContent``).

Routes:

* GET  /            -> serves index.html
* GET  /api/tools   -> JSON list of the server's tools (name, description, resourceUri)
* POST /api/call    -> calls a tool; returns the CallToolResult params
                       (content, structuredContent, isError) plus the tool's
                       ``resourceUri`` (null when the tool has no UI resource)
* GET  /ui-resource -> proxies a ``ui://`` resource (read via resources/read) so the
                       iframe can load it by URL

Run with:  uv run uvicorn app:app --port 8080
"""

from __future__ import annotations

import json
from pathlib import Path

import uvicorn
from mcp import Client
from mcp.types import CallToolResult
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

MCP_SERVER_URL = "http://localhost:8000/mcp"
INDEX_HTML = Path(__file__).resolve().parent / "index.html"


async def index(request: Request) -> FileResponse:
    """Serve the static front end."""
    return FileResponse(INDEX_HTML)


async def tools(request: Request) -> JSONResponse:
    """List the server's tools as JSON."""
    try:
        async with Client(MCP_SERVER_URL) as client:
            result = await client.list_tools()
    except Exception as exc:  # noqa: BLE001 - simple test client
        return JSONResponse({"error": f"Could not list tools: {exc}"}, status_code=502)

    return JSONResponse({
        "tools": [
            {
                "name": tool.name,
                "description": tool.description or "",
                "resourceUri": (tool.meta or {}).get("ui", {}).get("resourceUri"),
            }
            for tool in result.tools
        ]
    })


async def call(request: Request) -> JSONResponse:
    """Call a tool; return the result params and the tool's UI resource, if any."""
    try:
        body = await request.json()
        tool_name = str(body.get("tool", ""))
        arguments = body.get("arguments") or {}
    except Exception as exc:  # noqa: BLE001 - malformed request body
        return JSONResponse({"error": f"Bad request body: {exc}"}, status_code=400)

    try:
        async with Client(MCP_SERVER_URL) as client:
            tool = next(
                (t for t in (await client.list_tools()).tools if t.name == tool_name), None
            )
            if tool is None:
                return JSONResponse({"error": f"Unknown tool: {tool_name}"}, status_code=404)
            resource_uri = (tool.meta or {}).get("ui", {}).get("resourceUri")
            result: CallToolResult = await client.call_tool(tool_name, arguments)
    except Exception as exc:  # noqa: BLE001 - simple test client
        return JSONResponse({"error": f"MCP call failed: {exc}"}, status_code=502)

    # Spec-shaped CallToolResult params for notifications/call_tool_result.
    params = {
        k: v
        for k, v in result.model_dump(by_alias=True, exclude_none=True, mode="json").items()
        if k in ("content", "structuredContent", "isError")
    }
    return JSONResponse({"resourceUri": resource_uri, "result": params})


async def ui_resource(request: Request) -> FileResponse | JSONResponse:
    """Proxy a ui:// resource from the MCP server so the iframe can load it."""
    uri = request.query_params.get("uri", "")
    if not uri.startswith("ui://"):
        return JSONResponse({"error": f"Not a ui:// resource: {uri}"}, status_code=400)

    try:
        async with Client(MCP_SERVER_URL) as client:
            result = await client.read_resource(uri)
    except Exception as exc:  # noqa: BLE001 - simple test client
        return JSONResponse({"error": f"Could not read resource {uri}: {exc}"}, status_code=502)

    html = next((c.text for c in result.contents if getattr(c, "text", None)), None)
    if html is None:
        return JSONResponse({"error": f"Resource {uri} has no text content"}, status_code=404)
    return HTMLResponse(html)


app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/api/tools", tools, methods=["GET"]),
        Route("/api/call", call, methods=["POST"]),
        Route("/ui-resource", ui_resource, methods=["GET"]),
    ]
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
